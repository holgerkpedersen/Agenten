import re
from i18n import K
from lang import t
from typing import Any, Generator
from phase_manager import _normalize_phase, PHASE_ALIASES, CLOSE_PHASE_ALIASES, _get_phase_auto_complete_msg, _generate_phase_todos
import config
import os
import agent_git
import agent_phase_checks
from tool_handler import _use_native_tools, set_task_tools, _get_phase_task_tools, _handle_tool_call, _check_required_tools, REQUIRED_ACTION_TOOLS, _ensure_done_tool
from prompt_builder import _build_initial_messages, _build_chunk_hint, _build_phase_reason, _cont_hint, _add_user_msg, _msg_content_len, _truncate_messages, _build_truncation_summary, _extract_last_assistant_text
import subprocess
import agent_issues
from refactor_helpers import _resolve_refactor_plan_path, _check_import_placement, _refactor_actually_moved_code, _build_refactor_phase_context, _save_full_context_for_refactor, _count_symbols_in_file, _get_symbol_names_in_file, _build_module_progress_msg, _detect_module_deps, _resolve_source_file, _get_modified_core_files, _execute_autoresearch_issue, _check_refactor_progress, _all_planned_modules_exist, AUTO_RESOLVE_PATTERNS
import agent_files

def _validate_done_output(agent: Any, result_text: str | dict, task_name: str, task_node: Any = None) -> str | None:
    """validate done output.
    
    Args:
        agent:
        result_text:
        task_name:"""
    if isinstance(result_text, dict):
        result_text = result_text.get("result", str(result_text))
    # For refactor Ekstraher/Opdatér phases, don't require long done() text —
    # the files on disk ARE the validation. Only require 20 chars minimum.
    _phase_v = _normalize_phase(task_name).lower()
    _template_v = getattr(agent, 'active_template', '') or ''
    _min_len = 20 if (_template_v == "refactor" and _phase_v in ("ekstraher", "opdatér")) else 50
    if not isinstance(result_text, str) or len(result_text.strip()) < _min_len:
        return t(K.VALIDATION_DONE_TOO_SHORT, agent.lang).format(len(result_text) if result_text else 0)
    
    # Check success criteria if provided
    if task_node and task_node.success_criteria:
        # Basic check: result should mention the task or be substantial when criteria exist
        if len(result_text.strip()) < 100:  # Increased minimum when criteria exist
            # More lenient: if we at least mention the task name, it's OK
            if task_node.name.lower() not in result_text.lower():
                return t(K.VALIDATION_DONE_TOO_SHORT, agent.lang).format(len(result_text) if result_text else 0) + " (for lidt detaljer når succeskriterier er defineret)"
    
    phase = _normalize_phase(task_name).lower()
    template = getattr(agent, 'active_template', '') or ''
    bugfix_templates = {"bugfix", "refactor", "testgenerering", "issue_handler"}
    if template not in bugfix_templates:
        return None
    # Refactor Analyse/Plan are code analysis, not bug triage — no issue-id/bug keywords needed
    if template == "refactor" and any(k in phase for k in ["analyse", "plan"]):
        return None
    rt = result_text.lower()
    if any(k in phase for k in ["analyse", "læs", "afklar"]):
        has_issue_id = bool(re.search(r'(BUG|SEC|TST|ARC|PRF|MNT|REFAC)-\d+', result_text))
        has_keyword = any(w in rt for w in ["bug", "confirmed", "already fixed", "location", "fejl", "bekræftet"])
        if not (has_issue_id and has_keyword):
            return t(K.VALIDATION_DONE_MISSING_KEYWORDS, agent.lang).format(
                phase, "issue-id + bug/location status")
    elif any(k in phase for k in ["implementering", "fix", "test", "ekstraher", "opdatér"]):
        has_keyword = any(w in rt for w in ["changed", "fixed", "edited", "implemented",
                                             "rettede", "implementerede", "skrevet", "fil"])
        if not has_keyword:
            return t(K.VALIDATION_DONE_MISSING_KEYWORDS, agent.lang).format(
                phase, "what was changed/implemented")
    elif any(k in phase for k in ["luk", "close", "opdatering", "verifikation"]):
        has_keyword = any(w in rt for w in ["resolved", "resolution", "lukket", "afsluttet",
                                             "tests pass", "består"])
        if not has_keyword:
            return t(K.VALIDATION_DONE_MISSING_KEYWORDS, agent.lang).format(
                phase, "resolution_note + test status")
    return None



def _count_fix_attempts(agent: Any, called_tools: dict[str, int]) -> str | None:
    """count fix attempts.
    
    Args:
        agent:
        called_tools:"""
    edit_count = 0
    for k in called_tools:
        if k.startswith("edit_file"):
            edit_count += 1
    if edit_count > config.MAX_FIX_ATTEMPTS:
        return t(K.VALIDATION_FIX_ATTEMPTS_EXHAUSTED, agent.lang).format(
            config.MAX_FIX_ATTEMPTS, edit_count)
    return None



def _validate_done_completion(
    agent: Any, messages: list[dict], called_tools: dict[str, int],
    task_node: Any, original_prompt: str, result_text: str = ""
) -> tuple[str | None, list[tuple]]:
    """F\u00e6lles valideringsk\u00e6de for b\u00e5de <<<DONE>>> (text-mode) og done() (native)."""
    yields_to_emit: list[tuple] = []

    if agent._write_failed:
        # Fix 2: Don't block DONE — just warn. The LLM may have fixed the issue
        # or the failure may be transient. Let _check_required_tools handle enforcement.
        pass

    if agent._tests_failed and "test" not in _normalize_phase(task_node.name).lower():
        return (
            f"{t(K.SYS_ERROR_PREFIX, agent.lang)}: DU KAN IKKE afslutte med "
            f"<<<DONE>>>/done() når tests fejler. Ret koden med edit_file "
            f"og kør run_tests() igen indtil ALLE tests består.",
            yields_to_emit
        )

    # Block done() in Analyse/Plan if LLM hasn't created its own plan
    _phase_check = _normalize_phase(task_node.name).lower()
    if _phase_check in ("analyse", "plan") and not getattr(agent, '_llm_has_planned', False):
        return (
            f"{t(K.SYS_ERROR_PREFIX, agent.lang)}: Du kan ikke afslutte {task_node.name} "
            f"før du har oprettet din egen opgaveplan. Kald **plan_phase(fasenavn, mål)** "
            f"eller **create_todo** for at lave en detaljeret plan med konkrete trin.",
            yields_to_emit
        )

    # Block done() in Analyse if refactor_analyse.md hasn't been written yet
    if _phase_check == "analyse":
        if getattr(agent, 'active_template', '') == 'refactor':
            _analyse_path = "refactor_analyse.md"
            _wd = os.environ.get('AGENT_WORKDIR', '')
            if _wd:
                _analyse_path = os.path.join(_wd, _analyse_path)
            if not os.path.exists(_analyse_path):
                return (
                    f"{t(K.SYS_ERROR_PREFIX, agent.lang)}: Du kan ikke afslutte Analyse "
                    f"før du har gemt din analyse. Skriv til `refactor_analyse.md` med "
                    f"**write_file(path='refactor_analyse.md', content='...')** som sidste handling.",
                    yields_to_emit
                )

    if not _check_done_pr_requirements(agent, messages, called_tools, original_prompt, task_node.name):
        if agent_git.is_pr_workflow(task_node.name):
            yields_to_emit.append(("checkpoint", {
                "message": t(K.CP_PR_FAILED, agent.lang), "tool": "done"
            }))
        return (
            f"{t(K.SYS_ERROR_PREFIX, agent.lang)}: PR-kravene er ikke opfyldt. "
            f"Fuldf\u00f8r alle p\u00e5kr\u00e6vede trin f\u00f8rst.",
            yields_to_emit
        )

    passed, failed = _validate_rubrics(agent, called_tools)
    if failed:
        try:
            from skill_tracker import tracker  # noqa: PLC0415
            skill_name = "__none__"
            for s in agent._active_skills:
                if not s.get("base"):
                    skill_name = s["name"]
                    break
            tracker.record(
                skill_name=skill_name,
                task_summary=f"rubric_failure: {task_node.name}",
                success=False,
                template=agent.active_template or "",
                detail="; ".join(r.get("desc", "?")[:120] for r in failed),
            )
        except Exception:
            pass
    if failed and not agent._rubric_retried:
        agent._rubric_retried = True
        feedback = t(K.RUBRIC_FAILED, agent.lang)
        for r in failed:
            feedback += "\n" + t(K.RUBRIC_FAILED_DETAIL, agent.lang).format(desc=r["desc"])
        return (feedback, yields_to_emit)

    missing_msg = _check_required_tools(agent, called_tools, task_node.name)
    if missing_msg:
        return (f"{t(K.SYS_ERROR_PREFIX, agent.lang)}: {missing_msg}", yields_to_emit)

    # If the deterministic phase check passes, allow done() regardless of
    # output format/shortness — the files on disk are the real proof of work.
    _done_check_passed = False
    try:
        _dcp, _dcr = agent_phase_checks.check_phase_done(
            agent, task_node, called_tools=called_tools,
            tool_name="done", full_response=result_text,
        )
        if _dcp:
            _done_check_passed = True
    except Exception:
        pass
    if _done_check_passed:
        return (None, yields_to_emit)

    validation_err = _validate_done_output(agent, result_text, task_node.name, task_node)
    if validation_err:
        return (f"{t(K.SYS_ERROR_PREFIX, agent.lang)}: {validation_err}", yields_to_emit)

    fix_err = _count_fix_attempts(agent, called_tools)
    if fix_err:
        return (f"{t(K.SYS_ERROR_PREFIX, agent.lang)}: {fix_err}", yields_to_emit)

    return (None, yields_to_emit)



def _check_done_pr_requirements(agent: Any, messages: list[dict], called_tools: dict, original_prompt: str, task_name: str) -> bool:
    """check done pr requirements.
    
    Args:
        agent:
        messages:
        called_tools:
        original_prompt:
        task_name:"""
    if not agent_git.is_pr_workflow(task_name):
        return True
    if not called_tools:
        _add_user_msg(messages, f"{t(K.SYS_ERROR_PREFIX, agent.lang)}: Du kaldte <<<DONE>>> uden at bruge nogen v\u00e6rkt\u00f8jer. Brug v\u00e6rkt\u00f8jerne f\u00f8rst.")
        return False
    called_names = {t.split("{")[0] for t in agent._checkpoint_tools}
    if "github_create_pr" not in called_names:
        _add_user_msg(messages, f"!!! CHECKPOINT - {t(K.CP_PR_FAILED, agent.lang)}")
        agent._log("INFO", "CHECKPOINT", t(K.CP_PR_FAILED, agent.lang))
        return False
    missing_commit = agent_git.PR_COMMIT_TOOLS - called_names
    if missing_commit:
        _add_user_msg(messages, f"!!! CHECKPOINT - {t(K.CP_NO_COMMIT, agent.lang)}")
        agent._log("INFO", "CHECKPOINT", t(K.CP_NO_COMMIT, agent.lang))
        return False
    if "git_push" not in called_names:
        _add_user_msg(messages, f"!!! CHECKPOINT - {t(K.CP_NO_PUSH, agent.lang)}")
        agent._log("INFO", "CHECKPOINT", t(K.CP_NO_PUSH, agent.lang))
        return False
    return True



def _old_text_was_in_prior_result(old_text: str, messages: list[dict], min_match_ratio: float = 0.8) -> bool:
    """Check if old_text appears (or substantially appears) in a prior tool result.

    This prevents the LLM from fabricating old_text from memory — it must have
    actually read the file content first.

    Returns True if old_text (or a large substring of it) was found in a prior
    tool result message.
    """
    if not old_text or len(old_text.strip()) < 10:
        return True  # too short to meaningfully check
    # Collect all prior tool result content
    prior_content = []
    for msg in messages:
        if msg.get("role") == "tool":
            content = msg.get("content", "")
            if isinstance(content, str):
                prior_content.append(content)
    if not prior_content:
        return False
    combined = "\n".join(prior_content)
    # Exact match
    if old_text in combined:
        return True
    # Substring match: check if the longest contiguous part of old_text is in results
    lines = old_text.split("\n")
    if len(lines) >= 3:
        # Check sliding windows of increasing size
        for window_size in range(len(lines), max(2, len(lines) // 2), -1):
            for start in range(0, len(lines) - window_size + 1):
                chunk = "\n".join(lines[start:start + window_size])
                if chunk in combined:
                    return True
    # Fallback: check if at least 80% of non-empty lines appear
    non_empty = [l for l in lines if l.strip()]
    if non_empty:
        found = sum(1 for l in non_empty if l.strip() in combined)
        if found >= len(non_empty) * min_match_ratio:
            return True
    return False



def _validate_rubrics(agent: Any, called_tools: dict[str, int]) -> tuple[list, list]:
    """validate rubrics.
    
    Args:
        agent:
        called_tools:"""
    skill = agent._active_skills[0] if agent._active_skills else None
    if not skill:
        return [], []
    skill_rubrics = skill.get("rubrics", [])
    if not skill_rubrics:
        return [], []
    called = {k.split("{")[0] for k in called_tools}
    passed, failed = [], []
    for rubric in skill_rubrics:
        check = rubric.get("check", "")
        ok = _evaluate_rubric_check(check, called)
        if ok:
            passed.append(rubric)
        else:
            failed.append(rubric)
    return passed, failed



def _evaluate_rubric_check(check_str: str, called_tools: set[str]) -> bool:
    """evaluate rubric check.
    
    Args:
        check_str:
        called_tools:"""
    if not check_str:
        return True
    for part in check_str.split(" or "):
        cond = part.strip()
        if cond.startswith("tool_used:"):
            target = cond[len("tool_used:"):].strip()
            if target in called_tools:
                return True
    return False



def _verify_self_modification(agent: Any) -> None:
    """After a task that modified core files, run tests and rollback if they fail.

    Only triggers when at least one core framework file was successfully
    modified during the task. If tests fail, each modified file is reverted
    via ``git checkout`` and the failure is recorded in CoreAnalytics.
    """
    modified = _get_modified_core_files(agent)
    if not modified:
        return

    agent._log("INFO", "Self-modification detected \u2014 running verification",
               ", ".join(sorted(modified)))

    result = agent_issues.run_pytest()
    passed = result.get("success", False) and result.get("exit_code", -1) == 0

    test_summary = _parse_test_summary(result) or ""
    if not passed:
        summary = test_summary or f"exit code {result.get('exit_code', '?')}"

        # Refactor template: skip rollback — extraction is intentional
        if getattr(agent, 'active_template', '') == "refactor":
            agent._log("WARNING",
                       f"Verification FAILED \u2014 REFACTOR template, SKIPPING rollback for {len(modified)} file(s)",
                       summary[:300])
        else:
            agent._log("WARNING", f"Verification FAILED \u2014 rolling back {len(modified)} file(s)",
                       summary[:300])

            for basename in sorted(modified):
                try:
                    subprocess.run(
                        ["git", "checkout", "--", basename],
                        capture_output=True, text=True, timeout=30,
                        cwd=os.path.dirname(os.path.abspath(__file__)),
                    )
                    agent._log("INFO", f"  Rolled back {basename}", "")
                except Exception as exc:
                    agent._log("ERROR", f"  Rollback failed for {basename}", str(exc))

        if hasattr(agent, '_core'):
            for basename in sorted(modified):
                agent._core.record_test_outcome(
                    test_file=f"self_mod:{basename}",
                    passed=passed,
                    summary=summary if not passed else "Verification passed",
                )
            if not passed:
                agent._core.save()

    if not passed:
        if hasattr(agent, '_core'):
            hotspots = agent._core.get_hotspots(min_failures=3)
            for basename in sorted(modified):
                matching = [h for h in hotspots if h["file"] == basename]
                if matching and matching[0]["tool_failures"] >= 3:
                    try:
                        agent_issues.create_issue(
                            agent,
                            title=f"{basename} har fejlet ved selvtests 3+ gange",
                            type="self",
                            severity="high",
                            description=(
                                f"{basename} har fejlet ved automatisk "
                                f"test-verifikation {matching[0]['tool_failures']} gange "
                                f"efter redigering af egen kode.\n\n"
                                f"Sidste test-output: {summary[:300]}"
                            ),
                            location=basename,
                        )
                        agent._log("INFO", f"Auto-created CORE issue for {basename}",
                                   f"{matching[0]['tool_failures']} failures")
                    except Exception as exc:
                        agent._log("ERROR", "Failed to create CORE issue", str(exc))
    else:
        agent._log("INFO", "Verification passed \u2014 all tests OK",
                   test_summary[:200] if test_summary else "")



def _run_full_test_suite(agent: Any) -> bool:
    """Run the full pytest suite to verify no regression."""
    try:
        import subprocess
        result = subprocess.run(
            ["python", "-m", "pytest", "tests/", "-q"],
            capture_output=True, text=True, timeout=120, cwd=agent_files._resolve_workdir()
        )
        if result.returncode == 0:
            return True
        agent._log("WARNING", f"Auto-research: tests fejlede ({result.returncode} failures)",
                   result.stdout[-500:] + result.stderr[-500:])
        return False
    except subprocess.TimeoutExpired:
        agent._log("WARNING", "Auto-research: tests timed out after 120s", "")
        return False
    except FileNotFoundError:
        return False
    except Exception as exc:
        agent._log("WARNING", f"Auto-research: test exception", str(exc)[:200])
        return False



def _parse_test_summary(result: dict) -> str:
    """Parse test output for summary line."""
    ud = result.get("stdout", "") or ""
    if not ud:
        return ""
    last_short = ""
    for line in ud.splitlines():
        if "==" in line and ("passed" in line or "failed" in line or "error" in line):
            if "short test summary" not in line.lower():
                last_short = line.strip().lstrip("=").rstrip("=").strip()
    if last_short:
        return last_short
    return ""



def _extract_issue_id(text: str) -> str | None:
    """extract issue id.
    
    Args:
        text:"""
    m = ISSUE_ID_PATTERN.search(text)
    return m.group(0).upper() if m else None



ISSUE_ID_PATTERN = re.compile(r'(BUG|SEC|ARC|MNT|PRF|TST|REFAC|STAB)-\d+', re.IGNORECASE)
