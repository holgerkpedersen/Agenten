import config
from typing import Any, Generator
from llm_wrapper import LMStudioWrapper
import os
from agent_config import log, EXECUTION_TIMEOUT, _WRITE_TOOLS, PHASE_ALIASES, REQUIRED_ACTION_TOOLS, CLOSE_PHASE_ALIASES, ISSUE_ID_PATTERN, AUTO_RESOLVE_PATTERNS, FRAMEWORK_PY, _TODO_TOOL_MAP
from lang import t
import json
from i18n import K
import agent_files
import agent_phase_checks
from agent_refactor_helpers import _resolve_refactor_plan_path, _check_import_placement, _refactor_actually_moved_code, _build_refactor_phase_context, _save_full_context_for_refactor, _count_symbols_in_file, _get_symbol_names_in_file, _validate_ekstraher_symbols, _build_module_progress_msg, _detect_module_deps, _resolve_source_file, _check_refactor_progress, _all_planned_modules_exist
import subprocess
import agent_issues
import re
import agent_autoresearch
import time
import agent_git
from agent_done_validation import _validate_done_output, _count_fix_attempts, _ensure_done_tool, _validate_done_completion, _check_done_pr_requirements
from agent_llm_logging import _save_llm_prompt_file, _save_maintenance_prompt_dump, _save_llm_log_file
from agent_message_builder import _build_chunk_hint, _build_phase_reason, _build_initial_messages, _msg_content_len, _truncate_messages, _build_truncation_summary, _cont_hint, _add_user_msg
from agent_rubric import _validate_rubrics, _evaluate_rubric_check
from agent_task_phase import _get_max_iterations, _get_max_tool_calls, _normalize_phase, _set_phase_model, set_task_tools
from agent_todo import _auto_populate_llm_todos, _auto_todo_update, _match_tool_to_todos, _reconcile_llm_todos, _reconcile_todos_with_disk

from agent_utils import _is_greenfield, _use_native_tools, _normalize_phase, _inject_todo_tools


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



def _track_produced_file(agent: Any, tool_result: dict) -> None:
    """Extract file path from a successful write/edit tool result and track it."""
    tool = tool_result.get("tool", "")
    if tool not in _WRITE_TOOLS:
        return
    result = tool_result.get("result", {})
    if not isinstance(result, dict) or not result.get("success"):
        return
    path = result.get("result") or tool_result.get("args", {}).get("path", "")
    if not path and isinstance(result, dict):
        path = result.get("path", "")
    if path:
        if not os.path.isabs(path):
            workdir = os.environ.get("AGENT_WORKDIR", "")
            base = os.path.abspath(workdir) if workdir else os.path.abspath(".")
            path = os.path.normpath(os.path.join(base, path))
        agent._produced_files.add(os.path.abspath(path))







def _get_phase_auto_complete_msg(task_node: Any, tool_name: str, tool_result: dict | Any, agent: Any, called_tools: dict | None = None, full_response: str = "") -> str | None:
    """Return auto-complete message if the phase goal was just met, else None.
    Checks phase-specific success conditions after each tool call.

    Two layers of auto-complete:
      1. Tool-result-based (run_tests passed, update_issue_status succeeded)
      2. Deterministic phase-check (file_exists, files_from_plan, etc.) —
         runs after any tool call when the template defines a check for
         this phase

    Args:
        called_tools: dict of ``"{tool_name}{args_repr}"`` → count. Forwarded
            to ``check_phase_done`` so ``tool_called`` and ``tests_pass``
            checks can see the full call history.
        full_response: accumulated LLM streaming text. Forwarded to
            ``check_phase_done`` for ``min_text_length``.
    """
    task_name = getattr(task_node, "name", "") or ""
    phase = _normalize_phase(task_name).lower()

    # Bloker auto-complete for Analyse og Plan hvis LLM'en ikke har
    # oprettet sin egen opgaveplan endnu (kræver plan_phase kaldt).
    if phase in ("analyse", "plan") and not getattr(agent, '_llm_has_planned', False):
        return None

    if tool_name == "run_tests" and not agent._tests_failed:
        if "test" in phase:
            # For refactor: tests passing IS proof of work — modules on disk + imports = done
            if getattr(agent, 'active_template', '') == 'refactor':
                agent.issue_resolved = True
                agent._needs_resolve_persist = True
                return t(K.LOG_RED_TEST_PASSED, agent.lang)
            if _refactor_actually_moved_code(agent):
                agent.issue_resolved = True
                agent._needs_resolve_persist = True
                return t(K.LOG_RED_TEST_PASSED, agent.lang)
            return (
                t(K.LOG_RED_TEST_PASSED, agent.lang)
                + "\n\n"
                + t(K.TEST_BUT_NO_REFACTOR, agent.lang)
            )
        if any(k in phase for k in ["implementering", "fix", "verifikation",
                                     "opdatering", "luk", "close", "green"]):
            return t(K.LOG_PHASE_COMPLETE, agent.lang)

    if tool_name == "update_issue_status":
        if isinstance(tool_result, dict) and tool_result.get("success"):
            if any(k in phase for k in ["opdatering", "update", "luk", "close"]):
                return t(K.LOG_PHASE_COMPLETE, agent.lang)
            # Bug already fixed — auto-complete implementering/fix phases
            if any(k in phase for k in ["implementering", "fix", "verifikation", "green"]):
                return t(K.LOG_PHASE_COMPLETE, agent.lang)

    # Phase output verification — prevent auto-complete when no output was produced
    if tool_name in ("write_file",):
        if "plan" in phase:
            plan_path = getattr(agent, '_refactor_plan_path', '') or os.path.join(agent_files._resolve_workdir(), "refactor_plan.md")
            if not os.path.exists(plan_path) or os.path.getsize(plan_path) == 0:
                agent._log("DEBUG", "Plan output verification", f"{plan_path} mangler eller er tom — afslutter IKKE auto-complete")
                return None
        if "ekstraher" in phase:
            # Check if write_file was actually called with a module name (not refactor_plan.md)
            wrote_module = False
            for tool_key in (called_tools or {}):
                if tool_key.startswith("write_file"):
                    try:
                        args_str = tool_key[len("write_file"):]
                        args = json.loads(args_str) if args_str else {}
                        fname = args.get("filepath", "") or args.get("file_path", "") or ""
                        if fname and fname != "refactor_plan.md":
                            wrote_module = True
                            break
                    except (json.JSONDecodeError, ValueError):
                        wrote_module = True
                        break
            if not wrote_module:
                agent._log("DEBUG", "Ekstraher output verification", "write_file ikke kaldt med et modulnavn — afslutter IKKE auto-complete")
                return None

    # Deterministic phase check (template-defined file existence criteria).
    # Only run after a successful productive tool call — no point
    # auto-completing when the tool itself failed (e.g. update_issue_status
    # with a non-existent issue ID, or edit_file via edit_file2 where the
    # symbol wasn't found but still returned success=True).
    tool_failed = isinstance(tool_result, dict) and not tool_result.get("success")
    # edit_file via edit_file2 pipeline can return success=True even when
    # extraction failed (extract_error). Check for real changes.
    if not tool_failed and tool_name == "edit_file":
        has_changes = isinstance(tool_result, dict) and tool_result.get("lines_changed", 0) > 0
        if not has_changes:
            tool_failed = True
    if not tool_failed:
        PRODUCTIVE_TOOLS = {"write_file", "edit_file", "run_tests", "update_issue_status", "batch_extract_symbols", "extract_symbol", "verify_refactor"}
        if tool_name in PRODUCTIVE_TOOLS:
            try:
                passed, reason = agent_phase_checks.check_phase_done(
                    agent, task_node, called_tools=called_tools,
                    tool_name=tool_name, full_response=full_response,
                )
                if passed:
                    return t(K.PHASE_AUTO_ADVANCED, agent.lang).format(reason=reason)
            except Exception as _e:
                agent._log("DEBUG", f"phase check error: {_e}", "")

    return None



def _extract_last_assistant_text(messages: list[dict]) -> str:
    """Extract the last substantive assistant text from messages, skipping tool-only responses."""
    if not messages:
        return ""
    for m in reversed(messages):
        if m["role"] != "assistant":
            continue
        content = m.get("content")
        if not content:
            continue
        text = content if isinstance(content, str) else ""
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    text = part.get("text", "")
                    break
        text = text.strip()
        if len(text) > 50:
            return text
    return ""



def _get_modified_core_files(agent: Any) -> set[str]:
    """Return set of core framework file basenames modified during this task."""
    modified: set[str] = set()
    for entry in getattr(agent, '_tool_log', []):
        tool = entry.get("tool", "")
        if tool not in ("write_file", "edit_file", "delete_file", "extract_symbol", "remove_symbol"):
            continue
        if not entry.get("success", False):
            continue
        args = entry.get("args", {})
        if not args:
            continue
        filepath = args.get("filepath") or args.get("path") or args.get("source") or ""
        basename = os.path.basename(filepath)
        if basename in FRAMEWORK_PY:
            modified.add(basename)
    return modified



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



def _execute_autoresearch_issue(agent: Any, issue_id: str) -> Generator[dict, None, bool]:
    """Execute a CORE issue inline via selvforbedring template.

    Called from _finalize_task_stream when a phase fails and auto-research
    creates a CORE issue. Builds a task tree (Analyser → Diagnosticér → Ret
    → Verificér → Commit) and executes each phase via solve_task_stream,
    yielding events through the same SSE stream so the user sees live progress.

    Depth-limited: tracks _autoresearch_depth on agent to prevent infinite
    recursion if a CORE issue's Ret phase fails and creates another CORE issue.

    Returns:
        True if ALL phases completed successfully, False if any phase failed.
    """
    from task_tree import TaskTree, TaskNode

    # Depth limit — max 2 nested auto-research sessions
    depth = getattr(agent, '_autoresearch_depth', 0)
    if depth >= 2:
        agent._log("AUTOR", f"Auto-research: max dybde ({depth}) nået for {issue_id}",
                   "Stopper for at undgå uendelig rekursion")
        if agent.agent_log:
            yield {"type": "log", "log": agent.agent_log[-1]}
        yield {"type": "autoresearch", "action": "error", "issue_id": issue_id,
               "error": "Max dybde nået — undgår uendelig rekursion"}
        return False

    # Load the issue
    try:
        data = agent_issues._load_issues()
        issue = next((i for i in data.get("issues", []) if i.get("id") == issue_id), None)
    except Exception:
        issue = None
    if not issue:
        return False

    prompt = (
        f"{issue.get('id', issue_id)}: {issue.get('title', '')}\n\n"
        f"{issue.get('description', '')}\n\n"
        f"Location: {issue.get('location', '—')}\n"
        f"Impact: {issue.get('impact', '—')}\n\n"
        f"{issue.get('proposed_fix', '')}"
    )

    yield {"type": "autoresearch", "action": "start", "issue_id": issue_id, "title": issue.get("title", "")}

    # Save original state
    orig_template = agent.active_template
    orig_prompt = getattr(agent, "original_prompt", "")
    orig_tree = getattr(agent, "task_tree", None)
    orig_file_chunks = dict(getattr(agent, "file_chunks", {}))
    orig_file_context = getattr(agent, "file_context", None)
    orig_full_prompt = getattr(agent, "full_prompt_with_context", "")

    # Configure for selvforbedring
    agent.active_template = "selvforbedring"
    agent.original_prompt = prompt
    agent._autoresearch_depth = depth + 1

    # Auto-load files from issue location
    try:
        from agent_file_context import _auto_load_issue_files, _auto_load_location_file
        _auto_load_issue_files(agent, prompt, "selvforbedring", None)
        _auto_load_location_file(agent, prompt)
    except ImportError:
        pass

    # Build task tree using title-case phase names (matches SECTION_INSTRUCTIONS
    # and TEMPLATE_PHASE_CHECKS keys).
    phase_names = list(agent_phase_checks.TEMPLATE_PHASE_CHECKS.get("selvforbedring", {}).keys())
    if not phase_names:
        phase_names = ["Analyser", "Diagnosticér", "Ret", "Verificér", "Commit"]
    tree = TaskTree(prompt)
    for phase_name in phase_names:
        tree.root.add_child(TaskNode(phase_name))
    agent.task_tree = tree

    # Execute each phase
    all_done = True
    for child in list(tree.root.children):
        child.status = "pending"
        try:
            for event in agent.solve_task_stream(child, prompt):
                yield event
                if event.get("type") == "done":
                    if child.status == "failed":
                        all_done = False
                        agent._log("AUTOR", f"Auto-research: {child.name} fejlede", issue_id)
                        yield {"type": "autoresearch", "action": "phase_failed", "issue_id": issue_id, "phase": child.name}
                        yield {"type": "log", "log": agent.agent_log[-1]}
                    else:
                        yield {"type": "autoresearch", "action": "phase_done", "issue_id": issue_id, "phase": child.name}
                    break
        except Exception as exc:
            agent._log("AUTOR", f"Auto-research: exception i {child.name}", str(exc)[:300])
            yield {"type": "log", "log": agent.agent_log[-1]}
            child.status = "failed"
            all_done = False
            break
        if not all_done:
            for remaining in list(tree.root.children)[tree.root.children.index(child) + 1:]:
                remaining.status = "skipped"
            break

    # Restore original state
    agent.active_template = orig_template
    agent.original_prompt = orig_prompt
    agent.task_tree = orig_tree
    agent.file_chunks = orig_file_chunks
    agent.file_context = orig_file_context
    agent.full_prompt_with_context = orig_full_prompt
    agent._autoresearch_depth = depth

    success = all_done and all(c.status == "done" for c in tree.root.children if c.status not in ("skipped",))
    yield {"type": "autoresearch", "action": "complete", "issue_id": issue_id, "success": success}

    if success:
        agent._log("AUTOR", f"Auto-research: {issue_id} gennemført",
                   "Alle faser i selvforbedring bestod")
        yield {"type": "log", "log": agent.agent_log[-1]}
    return success



def _finalize_task_stream(agent: Any, task_node: Any, full_response: str, text_fallback: str, called_tools: dict, _report_logs: int = 0, original_prompt: str = "", messages: list[dict] | None = None) -> Generator[dict, None, None]:
    """finalize task stream.
    
    Args:
        agent:
        task_node:
        full_response:
        text_fallback:
        called_tools:
        _report_logs:
        original_prompt:
        messages:
    
    Yields:
        ..."""
    from agent_tool_handler import _check_required_tools, _extract_issue_id

    if not full_response or "ERROR" in full_response:
        if called_tools:
            called_names = {k.split("{")[0] for k in called_tools}
            action_tools = called_names & {"write_file", "edit_file", "update_issue_status", "github_create_pr", "git_commit", "run_tests", "extract_symbol", "batch_extract_symbols", "remove_symbol", "add_import"}
            if action_tools:
                full_response = t(K.LOG_AUTO_DONE, agent.lang).format(count=len(called_tools))
            else:
                read_tools = called_names & {"read_issue", "read_chunk", "list_chunks", "list_files", "locate"}
                if read_tools:
                    full_response = t(K.LOG_READ_ONLY, agent.lang).format(tools=", ".join(sorted(read_tools)))
                else:
                    full_response = t(K.LOG_AUTO_DONE, agent.lang).format(count=len(called_tools))
            task_node.status = "done"
        elif text_fallback and "ERROR" not in text_fallback:
            full_response = text_fallback
            task_node.status = "done"
        else:
            assistant_text = _extract_last_assistant_text(messages or [])
            if assistant_text:
                full_response = assistant_text
                task_node.status = "done"
            else:
                full_response = t(K.LOG_TASK_FAILED, agent.lang)
                task_node.status = "failed"
    else:
        task_node.status = "done"

    # Analyse & Plan phase output for refactor: require output files
    if task_node.status == "done" and getattr(agent, "active_template", "") == "refactor":
        phase = _normalize_phase(task_node.name).lower()
        _wd = os.environ.get("AGENT_WORKDIR", "")
        if phase == "analyse":
            _analyse_path = os.path.join(_wd, "refactor_analyse.md") if _wd else "refactor_analyse.md"
            if not os.path.exists(_analyse_path):
                task_node.status = "failed"
                full_response = (
                    f"{t(K.SYS_ERROR_PREFIX, agent.lang)}: Analyse-fasen afsluttede "
                    f"UDEN at skrive `refactor_analyse.md`. Kald write_file() for at "
                    f"gemme din analyse før du afslutter."
                )
        if phase == "plan":
            _plan_path = os.path.join(_wd, "refactor_plan.md") if _wd else "refactor_plan.md"
            if not os.path.exists(_plan_path):
                task_node.status = "failed"
                full_response = (
                    f"{t(K.SYS_ERROR_PREFIX, agent.lang)}: Plan-fasen afsluttede "
                    f"UDEN at skrive `refactor_plan.md`. Kald write_file() for at "
                    f"gemme din plan før du afslutter."
                )

    # For refactor Ekstraher: verify that symbols were actually removed from source,
    # not just that extract_symbol/batch_extract_symbols was called once.
    template = getattr(agent, "active_template", "")
    if task_node.status in ("done",) and template == "refactor":
        phase = _normalize_phase(task_node.name).lower()
        if "ekstraher" in phase:
            try:
                src_file = getattr(agent, '_source_file', '') or 'api_server.py'
                abs_src = os.path.abspath(src_file) if not os.path.isabs(src_file) else src_file
                sym_result = agent_files.list_symbols(abs_src)
                if sym_result.get("success"):
                    remaining = len(sym_result.get("symbols", []))
                    if remaining >= 50:
                        _path = "ekstraher_remaining"
                        task_node.status = "failed"
                        full_response = t(K.LOG_EXTRACT_INCOMPLETE, agent.lang).format(
                            remaining=remaining
                        )
            except Exception:
                pass

        # Per-module symbol validation: check that ALL planned symbols exist
        # in their target modules, not just that the source was emptied.
        # NOTE: Only run for ekstraher phases — during Analyse/Plan modules
        # don't exist yet, so validation would falsely fail.
        if task_node.status in ("done",):
            phase = _normalize_phase(task_node.name).lower()
            if "ekstraher" in phase:
                try:
                    validation_msg = _validate_ekstraher_symbols(agent)
                    if validation_msg:
                        _path = "ekstraher_validation"
                        task_node.status = "failed"
                        full_response = validation_msg
                except Exception:
                    pass

    # For refactor Analyse: verify that refactor_analyse.md was actually written.
    # Prevents phases from being marked "done" when the model ran out of iterations
    # before calling write_file.
    if task_node.status in ("done",) and template == "refactor":
        _phase_analyse = _normalize_phase(task_node.name).lower()
        if "analyse" in _phase_analyse:
            _a_path = "refactor_analyse.md"
            _a_wd = os.environ.get('AGENT_WORKDIR', '')
            if _a_wd:
                _a_path = os.path.join(_a_wd, _a_path)
            if not os.path.exists(_a_path):
                task_node.status = "failed"
                full_response = (
                    "Analyse kan ikke afsluttes: refactor_analyse.md blev ikke skrevet. "
                    "Filen skal gemmes med write_file(path='refactor_analyse.md', content='...') "
                    "som sidste handling før <<<DONE>>>."
                )

    # Auto-resolve for close phases: if the phase completed and update_issue_status
    # hasn't been called yet, call it automatically to avoid false negatives from
    # _check_required_tools (which requires update_issue_status for close phases).
    if task_node.status in ("done", "running"):
        phase = _normalize_phase(task_node.name).lower()
        if phase in CLOSE_PHASE_ALIASES:
            called_names = {k.split("{")[0] for k in (called_tools or {})}
            if "update_issue_status" not in called_names:
                orig_id = _extract_issue_id(original_prompt or "")
                if orig_id:
                    try:
                        agent_issues.update_issue_status(
                            agent, orig_id, "resolved",
                            "Auto-resolved: fase gennemført via auto-advance."
                        )
                        called_tools["update_issue_status{}"] = 1
                        agent._log("INFO", f"Auto-resolved {orig_id}", "update_issue_status kaldt automatisk")
                    except Exception:
                        pass

    # Hvis auto-advance allerede har bestemt fasen er faerdig (f.eks.
    # run_tests bestod i Test-fasen), overskriv IKKE med _check_required_tools.
    # edit_file er irrelevant nar testen bestar — filerne pa disk er beviset.
    if full_response and "ERROR" not in full_response and len(full_response) > 10:
        missing_msg = None
    else:
        missing_msg = _check_required_tools(agent, called_tools, task_node.name)
    agent._log("DEBUG", f"_finalize: status={task_node.status}, missing_msg={'None' if missing_msg is None else missing_msg[:60]}", "")
    if missing_msg:
        task_node.status = "failed"
        full_response = missing_msg
    elif task_node.status == "done":
        called_names = {k.split("{")[0] for k in called_tools}
        if "run_tests" in called_names and "update_issue_status" not in called_names:
            available = set(agent.tool_registry.active_tools or [])
            if "update_issue_status" in available and not agent._tests_failed:
                hint = t(K.LOG_TESTS_PASSED_NO_RESOLVE, agent.lang)
                full_response = full_response.rstrip() + "\n\n" + hint
                agent._log("INFO", hint, "")

        phase = _normalize_phase(task_node.name).lower()
        if phase in ("analyse", "l\u00e6s", "afklar") and not getattr(agent, 'issue_resolved', False):
            text_sources = [(full_response or ""), (original_prompt or "")]
            assistant_texts = []
            if messages:
                assistant_texts = [
                    m.get("content", "") for m in messages
                    if m["role"] == "assistant" and isinstance(m.get("content"), str) and len(m["content"]) > 50
                ]
                text_sources.extend(assistant_texts)
            combined = " ".join(text_sources)
            text_lower = combined.lower()
            if any(re.search(p, text_lower) for p in AUTO_RESOLVE_PATTERNS):
                source_text = assistant_texts[-1] if messages and assistant_texts else (full_response or "")
                issue_id = _extract_issue_id(combined)
                if issue_id:
                    agent._log("INFO", f"Auto-resolving {issue_id} \u2014 bug already fixed per analysis", source_text[:200])
                    agent_issues.update_issue_status(agent, issue_id, "resolved",
                        t(K.SYS_AUTO_RESOLVED, agent.lang, source=source_text[:200]))
                    agent._log("INFO", f"Auto-resolved {issue_id}", "Remaining phases will be skipped")

        if getattr(agent, '_needs_resolve_persist', False):
            issue_id = _extract_issue_id(original_prompt or "")
            if issue_id:
                agent_issues.update_issue_status(agent, issue_id, "resolved",
                    f"Auto-resolved: Test phase confirmed bug is already fixed. {full_response[:200]}")
                agent._log("INFO", f"Persisted resolved status for {issue_id}", "")
                agent._needs_resolve_persist = False

    task_node.result = full_response
    if task_node.status == "done":
        bad_patterns = ["angiv venligst", "hvilken fil", "hvilket filnavn", "which file", "what file",
                      "venligst angiv", "specificer fil", "give me the file", "jeg har brug for filen", "send mig filen"]
        is_short = len(full_response) < 100
        asks_for_files = any(p in full_response.lower() for p in bad_patterns)
        if (is_short or asks_for_files) and not called_tools:
            agent._log("WARNING", "Mist\u00e6nkeligt kort resultat", f"{len(full_response)} tegn, asks_for_files={asks_for_files}")
            full_response = full_response + "\n\n\u26a0\ufe0f  ADVARSEL: Dette resultat ser ufuldst\u00e6ndigt ud. Overvej at k\u00f8re opgaven igen med en tydeligere prompt."

    if task_node.status == "done" and _get_modified_core_files(agent):
        _verify_self_modification(agent)
        if hasattr(agent, '_tests_failed') and agent._tests_failed:
            task_node.status = "failed"
            full_response = t(K.LOG_TASK_FAILED, agent.lang) + " \u2014 tests fejlede efter redigering af egen kode (auto-rollback udf\u00f8rt)."

    # Auto-fix cross-module imports for refactor Ekstraher — runs regardless
    # of whether the phase passed or failed, so module-level imports get fixed
    # even when the LLM runs out of iterations before adding them manually.
    if getattr(agent, 'active_template', '') == 'refactor':
        phase_name = _normalize_phase(task_node.name).lower()
        if "ekstraher" in phase_name:
            from file_checks import fix_cross_module_imports
            fix_cross_module_imports(agent)

    agent.action_history.append(task_node.name.split()[0] if task_node.name else "unknown")
    agent._record_outcome(task_node)
    template = getattr(agent, 'active_template', '') or 'fri'
    tool_sequence = [k.split("{")[0] for k in called_tools]
    if tool_sequence:
        agent._seq.record_task(template, task_node.name, tool_sequence, success=(task_node.status == "done"))
        if len(called_tools) % 10 < 2:
            agent._seq.save()
    if task_node.status == "failed":
        agent._log("INFO", t(K.LOG_TASK_FAILED, agent.lang), task_node.name)
        # Only auto-research when NOT already inside a sub-session —
        # nested CORE issues from sub-session failures are counterproductive.
        issue_id = None
        _d = getattr(agent, '_autoresearch_depth', 0)
        if not (isinstance(_d, int) and _d > 0):
            issue_id = agent_autoresearch.trigger_if_needed(
                agent, task_node, called_tools, full_response, messages)
        if issue_id:
            # Execute the CORE issue inline via selvforbedring sub-session
            yield {"type": "autoresearch", "action": "created", "issue_id": issue_id}
            autorepair_ok = yield from _execute_autoresearch_issue(agent, issue_id)

            # Update CORE issue status
            try:
                core_status = "resolved" if autorepair_ok else "escalated"
                agent_issues.update_issue_status(
                    agent, issue_id, core_status,
                    f"Auto-research {'gennemførte' if autorepair_ok else 'kunne ikke gennemføre'} alle faser."
                )
            except Exception:
                pass

            if autorepair_ok:
                # Run full test suite to verify no regression
                yield {"type": "autoresearch", "action": "verifying", "issue_id": issue_id}
                agent._log("AUTOR", f"Auto-research: kører tests for at verificere {issue_id}", "")
                yield {"type": "log", "log": agent.agent_log[-1]}
                tests_ok = _run_full_test_suite(agent)
                if tests_ok:
                    agent._log("AUTOR", f"Auto-research: tests bestået for {issue_id}", "Ingen regression")
                else:
                    agent._log("AUTOR", f"Auto-research: tests fejlede efter {issue_id}", "Manuel gennemgang påkrævet")
                yield {"type": "log", "log": agent.agent_log[-1]}

                # Mark the original phase as done
                task_node.status = "done"
                full_response = f"Auto-research rettede problemet via {issue_id}"
                if not tests_ok:
                    full_response += " (tests fejlede — se log)"
                # Also try to update the original issue to resolved
                orig_id = _extract_issue_id(original_prompt or "")
                if orig_id and orig_id != issue_id:
                    try:
                        agent_issues.update_issue_status(
                            agent, orig_id, "resolved",
                            f"Automatisk rettet via {issue_id}: selvforbedring gennemførte alle faser.")
                    except Exception:
                        pass
    else:
        agent._log("INFO", t(K.LOG_TASK_DONE, agent.lang), task_node.name)
    agent._evolve_if_needed()
    for entry in agent.agent_log[_report_logs:]:
        yield {"type": "log", "log": entry}
    yield {"type": "done", "result": full_response}



def _generate_phase_todos(template: str, phase_name: str, prompt: str = "", agent: Any | None = None) -> list[dict]:
    """Generate a todo checklist for a phase based on template and phase name."""
    phase = _normalize_phase(phase_name).lower()
    todos = []

    if template == "bugfix":
        if phase == "analyse":
            todos.extend([
                {"id": "bf_a1", "text": "L\u00e6s issue med read_issue()", "done": False},
                {"id": "bf_a2", "text": "Find relevant kode med locate()", "done": False},
                {"id": "bf_a3", "text": "Sammenlign koden med buggens p\u00e5stand", "done": False},
                {"id": "bf_a4", "text": "Afg\u00f8r om fejlen findes eller er rettet", "done": False},
            ])
        elif phase == "test" or "test" in phase:
            todos.extend([
                {"id": "bf_t1", "text": "Opret testfil i tests/temp/ med write_file", "done": False},
                {"id": "bf_t2", "text": "K\u00f8r specifik test - den SKAL fejle (r\u00f8d fase)", "done": False},
            ])
        elif phase == "implementering":
            todos.extend([
                {"id": "bf_i1", "text": "Ret kildekoden med edit_file/add_method/add_function", "done": False},
                {"id": "bf_i2", "text": "Undg\u00e5 write_file - filen findes allerede", "done": False},
                {"id": "bf_i3", "text": "Brug add_method til nye metoder i klasse", "done": False},
            ])
        elif phase == "verifikation":
            todos.extend([
                {"id": "bf_v1", "text": "K\u00f8r specifik test - den SKAL best\u00e5 (gr\u00f8n fase)", "done": False},
                {"id": "bf_v2", "text": "K\u00f8r HELE testsuiten for at tjekke regression", "done": False},
            ])
        elif phase == "opdatering":
            todos.append({"id": "bf_o1", "text": "Opdater issue status til 'resolved'", "done": False})

    elif template == "refactor":
        import os as _os
        import re as _re
        # Use session-scoped plan path when available
        if agent and getattr(agent, '_refactor_plan_path', ''):
            _plan_path = agent._refactor_plan_path
        else:
            _plan_path = 'refactor_plan.md'
        plan_content = ''
        plan_fresh = True
        if _os.path.exists(_plan_path):
            try:
                with open(_plan_path, 'r', encoding='utf-8') as _f:
                    plan_content = _f.read()
                if prompt:
                    _prompt_target = _re.search(r'(?:REFAC|ARC|BUG)[-\s]*\d+.*?([a-zA-Z_][\w.]+\.py)', prompt)
                    _plan_target = _re.search(r'([a-zA-Z_][\w.]+\.py)', plan_content[:300])
                    if _prompt_target and _plan_target and _prompt_target.group(1) != _plan_target.group(1):
                        plan_fresh = False
            except (OSError, UnicodeDecodeError):
                pass

        # Extract module names from plan (only if plan is fresh)
        # Matcher både backtick-format (`config.py`) og heading-format (## Modul: config.py)
        _mods = set(_re.findall(r'`([a-zA-Z_][\w.]+\.py)`', plan_content))
        _mods |= set(_re.findall(r'(?:^|\n)#{1,6}\s+(?:Modul:\s*)?([a-zA-Z_][\w]*\.py)', plan_content, _re.MULTILINE | _re.IGNORECASE))
        plan_modules = sorted(_mods) if (plan_content and plan_fresh) else []
        existing_modules = [m for m in plan_modules if _os.path.exists(m)]

        if phase == "analyse":
            todos.extend([
                {"id": "rf_a1", "text": "List alle symboler med list_symbols()", "done": False},
                {"id": "rf_a2", "text": "Læs de vigtigste metoder med read_location()", "done": False},
                {"id": "rf_a3", "text": "Analyser afhængigheder med analyze_dependencies()", "done": False},
                {"id": "rf_a4", "text": "Identificer SOLID-overtrædelser", "done": False},
                {"id": "rf_a5", "text": "Kortlæg ansvarsområder for modulopdeling", "done": False},
                {"id": "rf_a6", "text": "Gem analyse i refactor_analyse.md med write_file()", "done": False},
            ])
            if plan_modules:
                todos.append({
                    "id": "rf_a_modules",
                    "text": "Planen nævner {} moduler: {}".format(len(plan_modules), ', '.join(plan_modules)),
                    "done": False
                })

        elif phase == "plan":
            todos.extend([
                {"id": "rf_p0", "text": "Læs refactor_analyse.md for at få analyse-resultater", "done": False},
                {"id": "rf_p1", "text": "Beslut modulopdeling", "done": False},
                {"id": "rf_p2", "text": f"Skriv {_plan_path} med write_file()", "done": False},
                {"id": "rf_p3", "text": "Inkluder alle moduler og symboler i planen", "done": False},
            ])
            if existing_modules:
                todos.append({
                    "id": "rf_p_existing",
                    "text": "Findes allerede: {}".format(', '.join(existing_modules)),
                    "done": True
                })

        elif phase == "ekstraher":
            # Parse module→symbols mapping from plan
            _mod_symbols: dict[str, list[str]] = {}
            if plan_content:
                # Pattern 1: "### modul.py\nsymbol1, symbol2, ..." or "## Modul: config.py\n- symbol1\n- symbol2"
                for _m in _re.finditer(
                    r'#{1,4}\s+(?:Modul(?:e)?:\s*)?`?([a-zA-Z_][\w./-]+\.py)`?\s*\n(.*?)(?=\n#{1,4}\s+|$)',
                    plan_content, _re.MULTILINE | _re.DOTALL | _re.IGNORECASE
                ):
                    _mod = _m.group(1).strip('`')
                    _body = _m.group(2).strip()
                    _syms = _re.findall(r'`?([A-Za-z_]\w*)`?', _body)
                    _mod_symbols[_mod] = [s for s in _syms if s not in ('py', 'txt', 'md') and not s.startswith(('.', '/'))]
                # Pattern 2: inline "modul.py → sym1, sym2"
                if not _mod_symbols:
                    for _m in _re.finditer(
                        r'`([a-zA-Z_][\w./-]+\.py)`[^`]*?([A-Z][a-zA-Z_][\w,\s]*)',
                        plan_content
                    ):
                        _mod = _m.group(1)
                        _syms = [s.strip() for s in _m.group(2).split(',') if s.strip()]
                        _mod_symbols[_mod] = _syms

            # Bestem kildefil fra prompt eller plan
            _src_match = _re.search(r"([a-zA-Z_][\w.]+\.py)", prompt or "")
            _ekstraher_src = _src_match.group(1) if _src_match else "api_server.py"

            todos.extend([
                {"id": "rf_e1", "text": "Følg refactor_plan.md nøjagtigt — opfyld ALLE moduler deri", "done": False},
                {"id": "rf_e2", "text": "Brug batch_extract_symbols() til at flytte symboler fra {} til hvert modul".format(_ekstraher_src), "done": False},
                {"id": "rf_e3", "text": "Rækkefølge: batch_extract_symbols → verify_refactor → næste modul", "done": False},
                {"id": "rf_e4", "text": "Verificer syntaks med verify_refactor() efter hver batch", "done": False},
            ])
            # Brug plan_modules eller _mod_symbols keys (fra sektions-headers) som modul-liste
            _all_plan_mods = plan_modules or sorted(_mod_symbols.keys())
            _existing_mods = [m for m in _all_plan_mods if _os.path.exists(m)]
            if _all_plan_mods:
                total = len(_all_plan_mods)
                done_count = len(_existing_mods)
                todos.append({
                    "id": "rf_e_progress",
                    "text": "Fremskridt: {}/{} moduler oprettet ({})".format(done_count, total, ', '.join(_all_plan_mods)),
                    "done": False,
                })
            # Ta l faktiske symboler i eksisterende moduler for status
            if _existing_mods:
                for _mod in _existing_mods:
                    _planned = _mod_symbols.get(_mod, [])
                    _actual = _count_symbols_in_file(_mod)
                    if _planned:
                        _done_count = min(_actual, len(_planned))
                        _status = "{}/{} symbols i filen".format(_done_count, len(_planned))
                    elif _actual > 0:
                        _status = "{} symbols i filen".format(_actual)
                    else:
                        _status = "f\u00e6rdig"
                    todos.append({
                        "id": "rf_e_done_" + _mod.replace('.py', '').replace('.', '_'),
                        "text": "\u2705 {} f\u00e6rdig \u2014 {}".format(_mod, _status),
                        "done": True,
                    })
            to_create = [m for m in _all_plan_mods if m not in _existing_mods]
            for idx, _mod in enumerate(to_create, 1):
                _syms = _mod_symbols.get(_mod, [])
                _count_info = " ({} symbols)".format(len(_syms)) if _syms else ""
                # Afha ngighedsdetektion
                _deps = _detect_module_deps(_mod, _all_plan_mods)
                _dep_info = " \u2014 afh\u00e6nger af: " + ", ".join(_deps) if _deps else ""
                todos.append({
                    "id": "rf_e_create_" + _mod.replace('.py', '').replace('.', '_'),
                    "text": "[{}] Flyt til {}{}{}".format(idx, _mod, _count_info, _dep_info),
                    "done": False,
                })

        elif phase in ("opdater", "opdatering", "opdat\u00e9r"):
            # Determine target file from prompt + plan — fallback to agent_core.py
            _target_file = 'agent_core.py'
            _file_match = _re.search(r"([a-zA-Z_][\w.]+\.py)", prompt)
            if _file_match:
                _target_file = _file_match.group(1)
            if plan_content:
                _plan_match = _re.search(r'([a-zA-Z_][\w.]+\.py)', plan_content[:300])
                if _plan_match and _plan_match.group(1) != _target_file:
                    _target_file = _plan_match.group(1)

            core_path = _os.path.join(_os.environ.get('AGENT_WORKDIR', ''), _target_file) if _os.environ.get('AGENT_WORKDIR') else _target_file
            core_symbols = []
            if _os.path.exists(core_path):
                try:
                    with open(core_path, 'r', encoding='utf-8') as _f:
                        core_content = _f.read()
                    core_nodes = _re.findall(r'^def (\w+)|^class (\w+)', core_content, _re.MULTILINE)
                    core_symbols = sorted(set(n[0] or n[1] for n in core_nodes))
                except (OSError, UnicodeDecodeError):
                    pass

            todos.append({"id": "rf_u1", "text": "List symboler i {} med list_symbols()".format(_target_file), "done": False})

            # Find symbols mentioned in plan that are still in the target file
            # Format: "- `symbol_name` (linje N) -> `target_module.py`"
            if plan_content and core_symbols:
                symbol_map = {}  # symbol -> target_module
                for m in _re.finditer(r'- `(\w+)`[^`]+`([\w.]+\.py)`', plan_content):
                    sym = m.group(1)
                    target = m.group(2)
                    symbol_map[sym] = target
                # Also match explicit symbol_name references
                for m in _re.finditer(r"symbol_name='(\w+)'", plan_content):
                    sym = m.group(1)
                    if sym not in symbol_map:
                        symbol_map[sym] = '?'
                still_in_core = [s for s in symbol_map if s in core_symbols]
                for sym in still_in_core:
                    target_mod = symbol_map.get(sym, '?')
                    todos.append({
                        "id": "rf_u_remove_" + sym,
                        "text": "Fjern `{}` fra {} (i {})".format(sym, _target_file, target_mod),
                        "done": False
                    })

            if existing_modules:
                _target_base = _os.path.splitext(_target_file)[0]
                for mod in existing_modules:
                    mod_name = _os.path.splitext(mod)[0]
                    if mod_name != _target_base:
                        todos.append({
                            "id": "rf_u_import_" + mod_name,
                            "text": "Tilf\u00f8j import fra {} i {}".format(mod, _target_file),
                            "done": False
                        })

            todos.append({"id": "rf_u_verify", "text": "Verificer syntaks med verify_refactor()", "done": False})
            todos.append({"id": "rf_u_tests", "text": "K\u00f8r tests for at bekr\u00e6fte ingen regression", "done": False})
            todos.append({"id": "rf_u_status", "text": "Mark\u00e9r REFAC som resolved med update_issue_status()", "done": False})

        elif phase == "test":
            todos.extend([
                {"id": "rf_t1", "text": "K\u00f8r alle tests for at bekr\u00e6fte ingen regression", "done": False},
                {"id": "rf_t2", "text": "Opdater issue status til 'resolved'", "done": False},
            ])

    elif template == "kodeanalyse":
        if phase == "analyse" or "form\u00e5l" in phase:
            todos.extend([
                {"id": "ka_a1", "text": "List symboler med list_symbols()", "done": False},
                {"id": "ka_a2", "text": "L\u00e6s vigtige funktioner med read_location()", "done": False},
                {"id": "ka_a3", "text": "Analyser afh\u00e6ngigheder", "done": False},
                {"id": "ka_a4", "text": "Skriv analyserapport med write_file()", "done": False},
            ])
        elif "import" in phase:
            todos.extend([
                {"id": "ka_i1", "text": "Gennemg\u00e5 imports med read_location()", "done": False},
                {"id": "ka_i2", "text": "Not\u00e9r ubrugte og cirkul\u00e6re imports", "done": False},
                {"id": "ka_i3", "text": "Skriv import-rapport med write_file()", "done": False},
            ])
        elif "arkitektur" in phase:
            todos.extend([
                {"id": "ka_k1", "text": "Analys\u00e9r klasse- og funktionsstruktur", "done": False},
                {"id": "ka_k2", "text": "Vurd\u00e9r design patterns og SOLID", "done": False},
                {"id": "ka_k3", "text": "Skriv arkitektur-rapport med write_file()", "done": False},
            ])
        elif "kvalitet" in phase or "kodekvalitet" in phase:
            todos.extend([
                {"id": "ka_q1", "text": "Vurd\u00e9r l\u00e6sbarhed, navngivning, type hints", "done": False},
                {"id": "ka_q2", "text": "Tjek test coverage og kompleksitet", "done": False},
                {"id": "ka_q3", "text": "Skriv kodekvalitets-rapport med write_file()", "done": False},
            ])
        elif "sikkerhed" in phase:
            todos.extend([
                {"id": "ka_s1", "text": "Analys\u00e9r inputvalidering og autentifikation", "done": False},
                {"id": "ka_s2", "text": "Tjek for OWASP-top-10 s\u00e5rbarheder", "done": False},
                {"id": "ka_s3", "text": "Skriv sikkerheds-rapport med write_file()", "done": False},
            ])

    elif template == "programmering":
        if phase == "analyse" or "krav" in phase:
            todos.extend([
                {"id": "pr_a1", "text": "Analys\u00e9r krav og behov", "done": False},
                {"id": "pr_a2", "text": "Skriv kravanalyse i docs/kravanalyse.md", "done": False},
            ])
        elif "arkitektur" in phase:
            todos.extend([
                {"id": "pr_d1", "text": "Design arkitektur med komponenter og gr\u00e6nseflader", "done": False},
                {"id": "pr_d2", "text": "Skriv arkitektur i docs/arkitektur.md", "done": False},
            ])
        elif "implementeringsplan" in phase:
            todos.extend([
                {"id": "pr_p1", "text": "Lav implementeringsplan med moduler og r\u00e6kkef\u00f8lge", "done": False},
                {"id": "pr_p2", "text": "Skriv plan i docs/implementeringsplan.md", "done": False},
            ])
        elif "sikkerhed" in phase:
            todos.extend([
                {"id": "pr_s1", "text": "Analys\u00e9r sikkerhedsaspekter", "done": False},
                {"id": "pr_s2", "text": "Skriv sikkerhedsanalyse i docs/sikkerhedsanalyse.md", "done": False},
            ])
        elif "refinement" in phase or "uddyb" in phase:
            todos.extend([
                {"id": "pr_r1", "text": "Udfyld detaljer og pr\u00e6cis\u00e9r specifikationer", "done": False},
            ])
        elif "kodeimplementering" in phase or "implementer" in phase:
            todos.extend([
                {"id": "pr_i1", "text": "Implement\u00e9r koden med write_file()", "done": False},
                {"id": "pr_i2", "text": "K\u00f8r tests med run_tests()", "done": False},
            ])

    elif template == "issue_handler":
        if phase in ("l\u00e6s", "read", "analyse"):
            todos.extend([
                {"id": "ih_a1", "text": "L\u00e6s issue med read_issue()", "done": False},
                {"id": "ih_a2", "text": "Find relevant kode med locate()", "done": False},
            ])
        elif phase in ("afklar", "clarify"):
            todos.extend([
                {"id": "ih_c1", "text": "Forst\u00e5 problemet og afg\u00f8r l\u00f8sning", "done": False},
            ])
        elif phase in ("fix", "implementering"):
            todos.extend([
                {"id": "ih_f1", "text": "Ret koden med edit_file()", "done": False},
                {"id": "ih_f2", "text": "K\u00f8r tests med run_tests()", "done": False},
            ])
        elif phase in ("luk", "close"):
            todos.extend([
                {"id": "ih_l1", "text": "Opdater issue status til 'resolved'", "done": False},
            ])

    elif template == "selvforbedring":
        if phase == "analyse" or "analyser" in phase:
            todos.extend([
                {"id": "sf_a1", "text": "L\u00e6s CORE-issue med read_issue()", "done": False},
                {"id": "sf_a2", "text": "Find relevant kode med locate()", "done": False},
            ])
        elif "diagnostic" in phase:
            todos.extend([
                {"id": "sf_d1", "text": "K\u00f8r tests med run_tests()", "done": False},
                {"id": "sf_d2", "text": "Identific\u00e9r rod\u00e5rsag", "done": False},
            ])
        elif phase in ("ret", "fix"):
            todos.extend([
                {"id": "sf_r1", "text": "Ret koden med edit_file()", "done": False},
                {"id": "sf_r2", "text": "K\u00f8r tests for at bekr\u00e6fte fix", "done": False},
            ])
        elif "verific" in phase or "test" in phase:
            todos.extend([
                {"id": "sf_v1", "text": "K\u00f8r HELE testsuiten", "done": False},
                {"id": "sf_v2", "text": "Opdater CORE-issue status til 'resolved'", "done": False},
            ])
        elif "commit" in phase:
            todos.extend([
                {"id": "sf_c1", "text": "Commit \u00e6ndringer med git_commit()", "done": False},
            ])

    elif template == "testgenerering":
        if phase == "analyse":
            todos.extend([
                {"id": "tg_a1", "text": "Analys\u00e9r koden og find testbare enheder", "done": False},
            ])
        elif "test" in phase:
            todos.extend([
                {"id": "tg_t1", "text": "Opret testfil i tests/temp/ med write_file()", "done": False},
                {"id": "tg_t2", "text": "K\u00f8r test - den SKAL fejle f\u00f8rst (r\u00f8d)", "done": False},
            ])
        elif "implementer" in phase:
            todos.extend([
                {"id": "tg_i1", "text": "Implement\u00e9r koden der g\u00f8r testen gr\u00f8n", "done": False},
                {"id": "tg_i2", "text": "K\u00f8r specifik test - skal best\u00e5", "done": False},
            ])
        elif "verifikation" in phase or "green" in phase:
            todos.extend([
                {"id": "tg_v1", "text": "K\u00f8r HELE testsuiten for regression", "done": False},
            ])

    elif template == "agenten":
        if "branch" in phase:
            todos.extend([
                {"id": "ag_b1", "text": "Opret og skift til ny branch", "done": False},
            ])
        elif "commit" in phase:
            todos.extend([
                {"id": "ag_c1", "text": "Commit \u00e6ndringer", "done": False},
            ])
        elif "push" in phase:
            todos.extend([
                {"id": "ag_p1", "text": "Push til remote", "done": False},
            ])
        elif "pull" in phase or "pr" in phase or "request" in phase:
            todos.extend([
                {"id": "ag_pr1", "text": "Opret Pull Request", "done": False},
            ])

    if not todos:
        todos.append({"id": "todo_generic", "text": f"Gennemf\u00f8r fasen: {phase_name}", "done": False})

    # Add verification todo — auto-marked by reconcile when check_phase_done passes
    todos.append({
        "id": "verify_criteria",
        "text": "Verific\u00e9r at fasens succeskriterier er opfyldt",
        "done": False,
    })

    return todos



def solve_task_stream(agent: Any, task_node: Any, original_prompt: str, saved_messages: list[dict] | None = None) -> Generator[dict, None, None]:
    """solve task stream.
    
    Args:
        agent:
        task_node:
        original_prompt:
        saved_messages: Optional saved conversation to resume from pause.
    
    Yields:
        ..."""
    from agent_tool_handler import _check_required_tools, _handle_tool_call, _old_text_was_in_prior_result

    task_node.status = "running"
    agent._phase_todos = _generate_phase_todos(getattr(agent, 'active_template', '') or '', task_node.name, getattr(agent, 'original_prompt', ''), agent)
    # Ryd gamle todos før nye fase-specifikke todos tilføjes
    yield {"type": "todo_clear"}
    for todo in agent._phase_todos:
        yield {"type": "todo_add", "todo": todo}
    # Auto-populate LLM todos from refactor_plan.md
    for evt in _auto_populate_llm_todos(agent, task_node):
        yield evt
    agent._task_start_time = time.time()
    agent.current_phase = _normalize_phase(task_node.name)
    agent._log("INFO", t(K.LOG_TASK_START, agent.lang), f"{task_node.name} (model: {agent.llm.model})")
    _set_phase_model(agent, task_node.name)
    set_task_tools(agent, task_node.name)
    agent._checkpoint_tools = set()
    agent._checkpoint_branch = ""
    agent._rubric_retried = False

    # Quick phase-completion check: hvis deterministisk check allerede
    # passerer (filer findes, tests består osv.), skip LLM helt.
    # Dette gør at faser markeret "failed" pga. tidligere bugs kan
    # auto-complete når den underliggende betingelse er opfyldt.
    if agent.active_template:
        try:
            _done_passed, _done_reason = agent_phase_checks.check_phase_done(
                agent, task_node, called_tools={},
                tool_name="", full_response="",
            )
            if _done_passed:
                agent._log("INFO", f"✅ Fase allerede opfyldt", _done_reason)
                # Mark verify_criteria as done
                yield {"type": "todo_update", "id": "verify_criteria", "done": True}
                task_node.status = "done"
                yield {"type": "complete", "message": _done_reason[:200]}
                return
            else:
                agent._log("DEBUG", f"Fast phase-check: fase IKKE opfyldt", _done_reason[:200] if _done_reason else "ingen grund")
        except Exception as _exc:
            import traceback
            agent._log("DEBUG", f"Fast phase-check exception: {_exc}", traceback.format_exc()[-300:])

    # Prerequisite check: Plan requires refactor_analyse.md from Analyse
    if agent.active_template == "refactor" and _normalize_phase(task_node.name) == "plan":
        _analyse_path = "refactor_analyse.md"
        _wd_check = os.environ.get('AGENT_WORKDIR', '')
        if _wd_check:
            _analyse_path_abs = os.path.join(_wd_check, _analyse_path)
        else:
            _analyse_path_abs = _analyse_path
        if not os.path.exists(_analyse_path_abs):
            _msg = (f"Analyse-fasen har ikke produceret `refactor_analyse.md`. "
                    f"Plan kan ikke køre uden analysen. Genstart refactor-processen.")
            agent._log("ERROR", "Plan prerequisite missing", _msg)
            task_node.status = "failed"
            yield {"type": "complete", "message": _msg[:200]}
            return
        # Auto-load refactor_analyse.md into file_chunks so Plan can read it
        _chunk_key = "file_refactor_analyse.md"
        if _chunk_key not in agent.file_chunks:
            try:
                with open(_analyse_path_abs, 'r', encoding='utf-8') as _f:
                    _content = _f.read()
                agent.file_chunks[_chunk_key] = agent_files.chunk_text(_content)
                agent._log("INFO", "Auto-loaded refactor_analyse.md", f"{len(_content)} chars, {len(agent.file_chunks[_chunk_key])} chunks")
            except Exception as _exc:
                agent._log("WARNING", f"Could not auto-load refactor_analyse.md: {_exc}", "")

    # Prerequisite check: Ekstraher requires refactor_plan.md from Plan
    if agent.active_template == "refactor" and _normalize_phase(task_node.name) == "ekstraher":
        _wd_check = os.environ.get('AGENT_WORKDIR', '')
        if _wd_check:
            _plan_path = os.path.join(_wd_check, "refactor_plan.md")
        else:
            _plan_path = "refactor_plan.md"
        _alt = getattr(agent, '_refactor_plan_path', '')
        _check_ok = os.path.exists(_plan_path)
        if not _check_ok:
            # Fallback: check _refactor_plan_path (may point to session-scoped dir)
            if _alt and os.path.isabs(_alt):
                _check_ok = os.path.exists(_alt)
            elif _alt and _wd_check:
                _check_ok = os.path.exists(os.path.join(_wd_check, _alt))
        if not _check_ok:
            _msg = (f"Plan-fasen har ikke produceret `refactor_plan.md`. "
                    f"Ekstraher kan ikke køre uden planen. Genstart refactor-processen.")
            agent._log("ERROR", "Ekstraher prerequisite missing", _msg)
            task_node.status = "failed"
            yield {"type": "complete", "message": _msg[:200]}
            return
        # Auto-load refactor_plan.md into file_chunks so Ekstraher can read it
        _chunk_key = "file_refactor_plan.md"
        if _chunk_key not in agent.file_chunks:
            _plan_abs = _alt if (_alt and os.path.isabs(_alt)) else (_plan_path if os.path.isabs(_plan_path) else os.path.join(_wd_check or '.', _plan_path))
            try:
                with open(_plan_abs, 'r', encoding='utf-8') as _f:
                    _content = _f.read()
                agent.file_chunks[_chunk_key] = agent_files.chunk_text(_content)
                agent._log("INFO", "Auto-loaded refactor_plan.md", f"{len(_content)} chars, {len(agent.file_chunks[_chunk_key])} chunks")
            except Exception as _exc:
                agent._log("WARNING", f"Could not auto-load refactor_plan.md: {_exc}", "")

    # Prerequisite check: Opdatér requires Ekstraher to have run (at least one module created)
    if agent.active_template == "refactor" and _normalize_phase(task_node.name) == "opdatér":
        _wd_check = os.environ.get('AGENT_WORKDIR', '')
        _refactor_plan_path = getattr(agent, '_refactor_plan_path', '')
        _plan_path = _refactor_plan_path or "refactor_plan.md"
        if _wd_check and not os.path.isabs(_plan_path):
            _plan_path = os.path.join(_wd_check, _plan_path)
        if os.path.exists(_plan_path):
            try:
                with open(_plan_path, 'r', encoding='utf-8') as f:
                    _plan_content = f.read()
                _modules = re.findall(r'##\s+Module:\s+(\S+\.py)', _plan_content)
                if _modules:
                    _any_exists = False
                    for _mod in _modules:
                        _mod_path = os.path.join(_wd_check or '.', _mod) if _wd_check else _mod
                        if os.path.exists(_mod_path):
                            _any_exists = True
                            break
                    if not _any_exists:
                        _msg = (f"Ekstraher har ikke oprettet nogen modulfiler endnu. "
                                f"Opdatér kan ikke køre uden moduler. Kør Ekstraher først.")
                        agent._log("ERROR", "Opdatér prerequisite missing", _msg)
                        task_node.status = "failed"
                        yield {"type": "complete", "message": _msg[:200]}
                        return
            except Exception:
                pass

    # If resuming from pause, use saved messages directly
    if saved_messages:
        messages = list(saved_messages)
        tools_list = ', '.join([k for k in agent.tool_registry.tools if agent.tool_registry.active_tools is None or k in agent.tool_registry.active_tools])
        has_file_ctx = False
    else:
        chunk_hint = _build_chunk_hint(agent)
        messages, tools_list, has_file_ctx = _build_initial_messages(agent, task_node, original_prompt, chunk_hint)

    _report_logs = len(agent.agent_log)

    full_response = ""
    text_fallback = ""
    max_iterations = _get_max_iterations(agent, task_node.name)
    called_tools = {}
    consecutive_errors = 0
    consecutive_dedups = 0
    consecutive_reads = 0
    consecutive_failures = 0
    consecutive_same_tool = 0
    consecutive_text_only = 0
    last_tool_name = ""
    last_name_arg = ""
    READ_ONLY_TOOLS = {"read_location", "read_chunk", "list_chunks", "list_files", "list_symbols", "locate", "read_issue"}
    WRITE_TOOLS = {"write_file", "edit_file", "delete_file", "extract_symbol", "batch_extract_symbols", "remove_symbol", "add_import", "add_method", "add_function"}
    agent._write_failed = False
    agent._tests_failed = False
    agent._located_files = set()
    agent._current_task_iteration = 0
    agent._non_productive_reminder_sent = False
    agent._tool_log = []
    agent._produced_files = set()
    agent._recently_deleted_files = set()
    agent._read_block_hits = 0
    agent._list_symbols_cache = {}
    _task_deadline = time.time() + EXECUTION_TIMEOUT

    for i in range(max_iterations):
        agent._current_task_iteration = i + 1

        # Flush pending SSE events from tool calls (e.g. todo CRUD)
        _pending = getattr(agent, '_pending_sse_events', None)
        if _pending:
            for evt in _pending:
                yield evt
            agent._pending_sse_events = []

        if agent.stop_requested:
            if getattr(agent, '_pause_requested', False):
                agent._paused_messages = list(messages)
                agent._paused_task = task_node
                agent._paused_original_prompt = original_prompt
                agent._pause_requested = False
                yield {"type": "paused"}
            break

        if time.time() > _task_deadline:
            agent._log("WARNING", "Task timeout", f"Exceeded {EXECUTION_TIMEOUT//60}-min limit")
            yield {"type": "timeout", "message": f"Task exceeded {EXECUTION_TIMEOUT//60}-minute limit"}
            break

        if agent.pending_reply:
            messages.append({"role": "user", "content": agent.pending_reply})
            agent._log("USER", "Bruger svarer", agent.pending_reply[:100])
            agent.pending_reply = None

        for entry in agent.agent_log[_report_logs:]:
            yield {"type": "log", "log": entry}
        _report_logs = len(agent.agent_log)

        response = ""
        tool_defs = agent.tool_registry.get_openai_tools_for_active() if _use_native_tools(agent) else []
        tools_param = tool_defs if tool_defs else None
        try:
            msg_count = len(messages)
            total_chars = sum(_msg_content_len(m) for m in messages)
            last_user = ""
            for m in reversed(messages):
                if m["role"] == "user" and isinstance(m.get("content"), str):
                    last_user = m["content"].strip()[:300].replace("\n", " ").replace("\r", "")
                    break
            why = f"[{task_node.name} #{i+1}] {tools_list[:120] if tools_list else 'no tools'}"
            if i == 0:
                sys_prompt = messages[0].get("content", "") if messages else ""
                instr = sys_prompt.strip()[:400].replace("\n", " ").replace("\r", "")
                agent._log("INFO", f"📤 {'→'.join(m['role'][0] for m in messages[:6])}{'+' if msg_count > 6 else ''} ({total_chars}c) {why}",
                           f"System: {instr}")
            else:
                agent._log("INFO", f"📤 {'→'.join(m['role'][0] for m in messages[:6])}{'+' if msg_count > 6 else ''} ({total_chars}c) {why}",
                           f"User: {last_user}")
            for entry in agent.agent_log[_report_logs:]:
                yield {"type": "log", "log": entry}
            _report_logs = len(agent.agent_log)
            # Inject budget info so the LLM knows remaining iterations
            remaining = max_iterations - i
            if remaining > 0:
                budget_msg = f"\n\n\u23f3 Budget: iteration {i + 1}/{max_iterations} \u2014 {remaining} tilbage."
                if remaining <= 2:
                    budget_msg += " (\u26a0\ufe0f F\u00e5 iterationer tilbage \u2014 priorit\u00e9r handlinger)"
                elif remaining / max_iterations <= 0.3:
                    budget_msg += " (\u26a0\ufe0f Knaphed)"
                # For refactor phases: inject progress and suggested order
                if getattr(agent, 'active_template', '') == 'refactor':
                    progress = _check_refactor_progress(agent, original_prompt)
                    if progress:
                        budget_msg += f"\n\n{progress}"
                    phase_lower = _normalize_phase(task_node.name).lower()
                    if phase_lower in ("opdatering", "opdat\u00e9r", "opdater"):
                        budget_msg += "\n\nR\u00e6kkef\u00f8lge: 1) list_symbols 2) remove_symbol 3) add_import 4) verify_refactor 5) run_tests 6) update_issue_status"
                    elif phase_lower == "ekstraher":
                        budget_msg += "\n\nRækkefølge: 1) list_symbols 2) extract_symbol (gentag) 3) add_function/add_method kun hvis nødvendigt 4) verify_refactor"
                # If LLM hasn't called plan_phase yet, nudge it (from iteration 0)
                # Skip for Analyse/Plan in refactor template — these phases have a single
                # file deliverable (refactor_analyse.md / refactor_plan.md) and plan_phase
                # distracts the LLM from calling write_file
                _phase_lower = _normalize_phase(task_node.name).lower() if hasattr(task_node, 'name') else ''
                _skip_plan_nudge = (
                    getattr(agent, 'active_template', '') == 'refactor'
                    and _phase_lower in ('analyse', 'plan')
                )
                if not getattr(agent, '_llm_has_planned', False) and not _skip_plan_nudge:
                    _has_auto_template = bool(getattr(agent, '_llm_todos', None))
                    if _has_auto_template:
                        # Has auto-populated template todos — nudge to detail them
                        if i == 0:
                            budget_msg += "\n\n🧠 Du har en skabelon til planen. Gør den mere detaljeret med **create_todo** — tilføj symbolnavne og tool-kald."
                        elif i >= 2:
                            budget_msg += "\n\n💡 Gør din plan mere detaljeret med **create_todo** — tilføj symbolnavne, parametre og rækkefølge."
                    else:
                        if i == 0:
                            budget_msg += "\n\n🧠 " + t(K.TODO_PLAN_NOT_CALLED, agent.lang)
                        elif i >= 1:
                            msg = t(K.TODO_PLAN_NOT_CALLED, agent.lang)
                            messages.append({"role": "system", "content": msg})
                            budget_msg += "\n\n⛔ " + msg
                # Also nudge to update_todo if LLM isn't marking progress
                elif i >= 1 and not _skip_plan_nudge:
                    _any_updated = any(t.get("done") for t in (getattr(agent, '_llm_todos') or []))
                    if not _any_updated:
                        _has_auto = bool(getattr(agent, '_llm_todos', None))
                        if _has_auto:
                            budget_msg += "\n\n💡 Du har en skabelon til planen. Gør den mere detaljeret med **create_todo** — tilføj symbolnavne og tool-kald. Brug **update_todo** for at markere fremdrift."
                        else:
                            budget_msg += "\n\n💡 Har du en plan? Brug **list_todos** for at se din status. Mangler du en plan, kald **plan_phase(fasenavn, mål)**."

                # For Analyse: remind to write refactor_analyse.md if not done yet
                if getattr(agent, 'active_template', '') == 'refactor' and _normalize_phase(task_node.name).lower() == "analyse":
                    _apath = "refactor_analyse.md"
                    _awd = os.environ.get('AGENT_WORKDIR', '')
                    if _awd:
                        _apath = os.path.join(_awd, _apath)
                    if not os.path.exists(_apath) and i >= 1:
                        _wmsg = "⛔ Du SKAL kalde write_file(path='refactor_analyse.md', content='...') nu. Det er din eneste opgave i denne fase. Kald IKKE plan_phase, list_symbols eller read_location igen."
                        messages.append({"role": "system", "content": _wmsg})
                        budget_msg += "\n\n" + _wmsg
                # For Plan: remind to write refactor_plan.md if not done yet
                if getattr(agent, 'active_template', '') == 'refactor' and _normalize_phase(task_node.name).lower() == "plan":
                    _ppath = "refactor_plan.md"
                    _pwd = os.environ.get('AGENT_WORKDIR', '')
                    if _pwd:
                        _ppath = os.path.join(_pwd, _ppath)
                    if not os.path.exists(_ppath) and i >= 1:
                        _wmsg = "⛔ Du SKAL kalde write_file(path='refactor_plan.md', content='...') nu. Det er din eneste opgave i denne fase. Kald IKKE plan_phase, list_symbols eller read_location igen."
                        messages.append({"role": "system", "content": _wmsg})
                        budget_msg += "\n\n" + _wmsg
                messages.append({"role": "user", "content": budget_msg})
                yield {"type": "budget", "iteration": i + 1, "max": max_iterations, "remaining": remaining}
            _save_llm_prompt_file(agent, task_node.name, i, messages)
            for chunk in agent.llm.generate_stream(messages=messages, temperature=0.3, max_tokens=agent.max_tokens, images=agent.images, tools=tools_param):
                response += chunk
                yield {"type": "chunk", "chunk": chunk}
        except GeneratorExit:
            agent._log("INFO", "Client disconnected", "GeneratorExit")
            raise

        if agent.stop_requested:
            if getattr(agent, '_pause_requested', False):
                agent._paused_messages = list(messages)
                agent._paused_task = task_node
                agent._paused_original_prompt = original_prompt
                agent._pause_requested = False
                yield {"type": "paused"}
            break

        if response.startswith("[ERROR:") or response.startswith("ERROR:"):
            yield {"type": "error", "message": response}
            break

        pending_tc = getattr(agent.llm, '_pending_tool_calls', [])
        if pending_tc and agent.active_template == "programmering" and "kodeimplementering" in _normalize_phase(task_node.name) and not _is_greenfield():
            _save_maintenance_prompt_dump(agent, task_node.name, i, messages, tools_param if tools_param else [], pending_tc)
        if pending_tc and hasattr(agent, '_wta'):
            template = getattr(agent, 'active_template', 'fri') or 'fri'
            phase = getattr(task_node, 'name', '?')
            pending_tc = agent._wta.rank_tool_calls(template, phase, pending_tc)
        pending_reasoning = getattr(agent.llm, '_pending_reasoning', None)
        if pending_tc:
            tool_call_msg = {"role": "assistant", "content": None, "tool_calls": list(pending_tc)}
            if pending_reasoning:
                tool_call_msg["reasoning_content"] = pending_reasoning
            tc_names = [tc["function"]["name"] for tc in pending_tc]
            reasoning = (pending_reasoning or "")[:400].replace("\n", " ").replace("\r", "")
            # Sørg for mellemrum efter ] i LLM's reasoning-tekst
            reasoning = re.sub(r'\]([^\s])', r'] \1', reasoning)
            detail = f"tools: {tc_names}"
            if reasoning:
                detail = f"{reasoning} | {detail}"
            agent._log("LLM", f"🤖 #{i+1} native-tool", detail)
            messages.append(tool_call_msg)
            for tc in pending_tc:
                args_val = tc["function"]["arguments"]
                if isinstance(args_val, str):
                    try:
                        args_val = json.loads(args_val)
                    except (json.JSONDecodeError, ValueError):
                        args_val = {}
                tool_name = tc["function"]["name"]

                # For read_location/locate: different name argument = research, not loop
                if tool_name in ("read_location", "locate") and isinstance(args_val, dict):
                    current_name = str(args_val.get("name", ""))
                    if current_name and current_name != last_name_arg:
                        consecutive_same_tool = 0
                        consecutive_reads = 0
                        last_name_arg = current_name

                if tool_name == last_tool_name:
                    consecutive_same_tool += 1
                    # Skip same-tool-loop escape for greenfield write_file calls
                    is_greenfield_write = (
                        agent.active_template == "programmering"
                        and "kodeimplementering" in (task_node.name or "").lower()
                        and tool_name == "write_file"
                    )
                    if consecutive_same_tool >= 5 and not is_greenfield_write:
                        _active = agent.tool_registry.active_tools or []
                        alt_tools = [t for t in _active
                                     if t not in ("read_issue", "locate", "read_location", "list_symbols")][:5]
                        tip = f" Pr\u00f8v: {', '.join(alt_tools)}." if alt_tools else ""
                        _add_user_msg(messages,
                            f"[SYSTEM: Du har kaldt '{tool_name}' {consecutive_same_tool} gange i tr\u00e6k.{tip}"
                            f" Brug <<<DONE>>> eller skift v\u00e6rkt\u00f8j.]")
                        agent._log("SYSTEM", "Same-tool-loop escape",
                                   f"{consecutive_same_tool}x {tool_name} in a row{tip}")
                        consecutive_same_tool = 0
                else:
                    consecutive_same_tool = 0
                    last_tool_name = tool_name

                # run_tests: skip dedup tracking (side-effect tool, called multiple times legitimately)
                if tool_name in ("run_tests",):
                    pass
                else:
                    tool_key = tool_name + str(args_val)
                    dup_count = called_tools.get(tool_key, 0)
                    called_tools[tool_key] = dup_count + 1
                if tool_name not in ("run_tests", "verify_refactor") and dup_count >= 1:
                    consecutive_dedups += 1
                    # For batch_extract_symbols/extract_symbol: show module progress
                    # so the LLM knows which modules are done and what to do next
                    if tool_name in ("batch_extract_symbols", "extract_symbol"):
                        _progress = _build_module_progress_msg(agent)
                        if _progress:
                            dup_err = (
                                f"{t(K.SYS_ERROR_PREFIX, agent.lang)}: "
                                f"Du har allerede dette resultat for {args_val.get('target', '?')}.\n\n"
                                f"📊 Fremgang:\n{_progress}\n\n"
                                f"Gå videre til næste modul med batch_extract_symbols."
                            )
                        else:
                            dup_err = f"{t(K.SYS_ERROR_PREFIX, agent.lang)}: {t(K.SYS_DUP_RESULT, agent.lang)}"
                    else:
                        dup_err = f"{t(K.SYS_ERROR_PREFIX, agent.lang)}: {t(K.SYS_DUP_RESULT, agent.lang)}"
                    _add_user_msg(messages, dup_err)
                    messages.append({"role": "user", "content": dup_err})
                    yield {"type": "tool_call", "tool": tool_name, "args": args_val}
                    yield {"type": "tool_result", "tool": tool_name, "args": args_val, "result": {"success": False, "error": "Duplicate call blocked"}}
                    if tool_name in READ_ONLY_TOOLS and getattr(agent, '_read_escape_sent', False):
                        agent._read_block_hits += 1
                    if consecutive_dedups >= 3:
                        _active = agent.tool_registry.active_tools or []
                        write_tools = [t for t in ("write_file", "edit_file", "delete_file", "extract_symbol", "batch_extract_symbols", "remove_symbol", "add_import", "add_method", "add_function")
                                       if t in _active]
                        # For refactor Ekstraher: if all planned modules exist, remove batch_extract_symbols
                        # (forcing LLM to use write_file for modules that don't exist yet)
                        _progress = _build_module_progress_msg(agent)
                        if _progress and getattr(agent, 'active_template', '') == 'refactor':
                            # Count how many planned modules exist on disk
                            planned = getattr(agent, '_planned_symbols_per_target', None)
                            if planned:
                                exists_count = sum(1 for mod in planned if os.path.exists(mod))
                                total_count = len(planned)
                                if exists_count < total_count:
                                    # Some modules don't exist yet — remove batch extract tools,
                                    # LLM must use write_file first to create the target file
                                    write_tools = [t for t in write_tools
                                                   if t not in ("extract_symbol", "batch_extract_symbols")]
                                    if "write_file" not in write_tools:
                                        write_tools.append("write_file")
                        if write_tools:
                            # Prune active_tools to WRITE_TOOLS only — LLM cannot read anymore
                            agent.tool_registry.active_tools = write_tools
                            tools_param = agent.tool_registry.get_openai_tools_for_active() if _use_native_tools(agent) else []
                            # For batch_extract_symbols: show concrete next step
                            if agent.active_template == "refactor" and "ekstraher" in str(task_node.name).lower():
                                planned = getattr(agent, '_planned_symbols_per_target', None)
                                missing_modules = []
                                if planned:
                                    for mod in planned:
                                        if not os.path.exists(mod):
                                            missing_modules.append(os.path.basename(mod))
                                if missing_modules:
                                    mod_list = ', '.join(missing_modules[:3])
                                    extra = f" mere" if len(missing_modules) > 3 else ""
                                    reminder = (
                                        f"[SYSTEM: Du er i en løkke. Opret FØRST de manglende modulfiler "
                                        f"med write_file: {mod_list}{extra}. "
                                        f"Bagefter kan du bruge batch_extract_symbols til at flytte symboler.]"
                                    )
                                elif _progress:
                                    reminder = (
                                        f"[SYSTEM: Du er i en løkke med identiske resultater. "
                                        f"STOP med at kalde batch_extract_symbols for det samme modul.\n\n"
                                        f"📊 Fremgang:\n{_progress}\n\n"
                                        f"Gå videre til næste modul nu.]"
                                    )
                                else:
                                    reminder = (
                                        f"[SYSTEM: Du er i en løkke med identiske resultater. "
                                        f"KUN skriveværktøjer er tilgængelige nu: "
                                        f"{', '.join(write_tools)}. "
                                        f"Respond ONLY with a tool call.]"
                                    )
                            else:
                                reminder = (
                                    f"[SYSTEM: Du er i en løkke med identiske resultater. "
                                    f"KUN skriveværktøjer er tilgængelige nu: "
                                    f"{', '.join(write_tools)}. "
                                    f"Respond ONLY with a tool call.]"
                                )
                            messages.append({"role": "system", "content": reminder})
                            agent._log("SYSTEM", "Dedup-loop escape", f"pruned to {write_tools}, progress={bool(_progress)}")
                            consecutive_dedups = 0
                    continue
                consecutive_dedups = 0
                if tool_name in READ_ONLY_TOOLS:
                    if getattr(agent, '_read_escape_sent', False):
                        agent._read_block_hits += 1
                        _active = agent.tool_registry.active_tools or []
                        write_tools = [t for t in ("write_file", "edit_file", "delete_file", "extract_symbol", "batch_extract_symbols", "remove_symbol", "add_import", "add_method", "add_function")
                                       if t in _active]
                        result_str = f"[SYSTEM: L\u00e6sning er blokeret. DU SKAL skrive kode. Brug {'/'.join(write_tools)}]"
                        result = {"success": True, "result": "Skipped \u2014 reads blocked"}
                        agent._log("TOOL", t(K.LOG_TOOL_CALLING, agent.lang).format(tool=tool_name), str(args_val))
                        agent._log("TOOL", t(K.LOG_TOOL_RESULT, agent.lang).format(tool=tool_name), result_str)
                        messages.append({"role": "user", "content": result_str})
                        yield {"type": "tool_call", "tool": tool_name, "args": args_val}
                        yield {"type": "tool_result", "tool": tool_name, "args": args_val, "result": result}
                        continue
                    consecutive_reads += 1
                    if consecutive_reads >= 3:
                        _active = agent.tool_registry.active_tools or []
                        write_tools = [t for t in ("write_file", "edit_file", "delete_file", "extract_symbol", "batch_extract_symbols", "remove_symbol", "add_import", "add_method", "add_function")
                                       if t in _active]
                        if write_tools:
                            _add_user_msg(messages, (
                                f"[SYSTEM: Du har lavet {consecutive_reads} l\u00e6sekald i tr\u00e6k uden at skrive noget. "
                                f"STOP med at l\u00e6se. BRUG et v\u00e6rkt\u00f8j der SKRIVER: "
                                f"{', '.join(write_tools)}. "
                                f"Forklar HVORFOR du bliver ved med at l\u00e6se \u2014 hvad mangler du?]"
                            ))
                            agent._log("SYSTEM", "Read-loop escape", f"{consecutive_reads} consecutive reads — force write")
                            consecutive_reads = 0
                            agent._read_escape_sent = True
                            result_str = f"[SYSTEM: L\u00e6sekald blokeret. Brug {', '.join(write_tools)}.]"
                            result = {"success": True, "result": "Skipped — force write"}
                            agent._log("TOOL", t(K.LOG_TOOL_CALLING, agent.lang).format(tool=tool_name), str(args_val))
                            agent._log("TOOL", t(K.LOG_TOOL_RESULT, agent.lang).format(tool=tool_name), result_str)
                            messages.append({"role": "user", "content": result_str})
                            yield {"type": "tool_call", "tool": tool_name, "args": args_val}
                            yield {"type": "tool_result", "tool": tool_name, "args": args_val, "result": result}
                            continue
                if tool_name in WRITE_TOOLS:
                    consecutive_reads = 0
                    agent._read_block_hits = 0
                    agent._read_escape_sent = False
                if tool_name == "write_file" and args_val.get("path"):
                    import os as _os
                    write_path = _os.path.abspath(args_val["path"])
                    if write_path in getattr(agent, '_recently_deleted_files', set()) and str(args_val.get("overwrite", "")).lower() != "force":
                        result_str = f"{t(K.SYS_ERROR_PREFIX, agent.lang)}: BLOCKERET — filen '{_os.path.basename(write_path)}' blev for nylig slettet. Brug edit_file for at genskabe den, eller overwrite=\"force\" for at tvinge oprettelsen."
                        result = {"success": False, "error": "Recently deleted file"}
                        agent._log("TOOL", t(K.LOG_TOOL_CALLING, agent.lang).format(tool=tool_name), str(args_val))
                        agent._log("TOOL", t(K.LOG_TOOL_RESULT, agent.lang).format(tool=tool_name), result_str)
                        messages.append({"role": "user", "content": result_str})
                        yield {"type": "tool_call", "tool": tool_name, "args": args_val}
                        yield {"type": "tool_result", "tool": tool_name, "args": args_val, "result": result}
                        continue
                if tool_name in ("write_file", "edit_file") and getattr(agent, 'issue_resolved', False) and getattr(agent, 'active_template', '') != 'refactor':
                    result_str = f"{t(K.SYS_ERROR_PREFIX, agent.lang)}: BLOCKERET — issuet er allerede markeret som resolved. Redig\u00e9r IKKE filer. Brug <<<DONE>>> for at afslutte, eller gen\u00e5bn issuet f\u00f8rst."
                    result = {"success": False, "error": "Issue already resolved"}
                    agent._log("TOOL", t(K.LOG_TOOL_CALLING, agent.lang).format(tool=tool_name), str(args_val))
                    agent._log("TOOL", t(K.LOG_TOOL_RESULT, agent.lang).format(tool=tool_name), result_str)
                    messages.append({"role": "user", "content": result_str})
                    yield {"type": "tool_call", "tool": tool_name, "args": args_val}
                    yield {"type": "tool_result", "tool": tool_name, "args": args_val, "result": result}
                    continue
                # Fix 1: Validate old_text was in a prior tool result
                if tool_name == "edit_file" and args_val.get("old_text") and not args_val.get("symbol"):
                    old_text = args_val["old_text"]
                    if not _old_text_was_in_prior_result(old_text, messages):
                        # Auto-read the file to give the LLM the actual content
                        filepath = args_val.get("path", "")
                        if filepath and os.path.exists(filepath):
                            try:
                                with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
                                    content = f.read()
                                auto_msg = f"[Auto-read {filepath}]\n{content}"
                                messages.append({"role": "user", "content": auto_msg})
                            except (OSError, IOError):
                                pass
                        # Re-check after auto-read
                        if not _old_text_was_in_prior_result(old_text, messages):
                            # Include actual file content so LLM can see what's there
                            _actual_content = ""
                            try:
                                _fp = args_val.get("path", "")
                                if _fp and os.path.exists(_fp):
                                    with open(_fp, 'r', encoding='utf-8', errors='replace') as _f:
                                        _actual_content = _f.read()
                            except (OSError, IOError):
                                pass
                            if _actual_content:
                                result_str = (
                                    f"{t(K.SYS_ERROR_PREFIX, agent.lang)}: old_text blev ikke fundet i "
                                    f"filen. Her er filens nuværende indhold:\n"
                                    f"--- START AF FIL ---\n{_actual_content}\n--- SLUT AF FIL ---\n"
                                    f"Brug teksten ovenfor som old_text. Kopier den præcise tekst "
                                    f"du vil erstatte, og sæt den som old_text. Prøv igen."
                                )
                            else:
                                result_str = (
f"{t(K.SYS_ERROR_PREFIX, agent.lang)}: {t(K.SYS_EDIT_OLDTEXT_NOREAD, agent.lang)}"
                                )
                            result = {"success": False, "error": "old_text not in prior read results"}
                            agent._log("TOOL", t(K.LOG_TOOL_CALLING, agent.lang).format(tool=tool_name), str(args_val))
                            agent._log("TOOL", t(K.LOG_TOOL_RESULT, agent.lang).format(tool=tool_name), result_str)
                            messages.append({"role": "user", "content": result_str})
                            yield {"type": "tool_call", "tool": tool_name, "args": args_val}
                            yield {"type": "tool_result", "tool": tool_name, "args": args_val, "result": result}
                            continue
                agent._log("TOOL", t(K.LOG_TOOL_CALLING, agent.lang).format(tool=tool_name), str(args_val))
                # Cache list_symbols results per file
                if tool_name == "list_symbols" and isinstance(args_val, dict):
                    _ls_file = args_val.get("filepath", "")
                    if _ls_file and _ls_file in agent._list_symbols_cache:
                        result = agent._list_symbols_cache[_ls_file]
                        result_str = json.dumps(result, ensure_ascii=False)
                        agent._log("TOOL", t(K.LOG_TOOL_RESULT, agent.lang).format(tool=tool_name), f"(cached) {result_str[:200]}")
                        messages.append({"role": "tool", "tool_call_id": tc.get("id", ""), "content": result_str})
                        yield {"type": "tool_call", "tool": tool_name, "args": args_val}
                        yield {"type": "tool_result", "tool": tool_name, "args": args_val, "result": result}
                        continue
                # For refactor Test: blokér run_tests hvis edit_file ikke er kaldt først
                # (forhindrer run_tests-loop når tests fejler pga. brudte imports)
                _is_refactor_test = (
                    getattr(agent, 'active_template', '') == 'refactor'
                    and getattr(task_node, 'name', '').lower() in ('test',)
                )
                if _is_refactor_test and tool_name == "run_tests":
                    tests_ran = any("run_tests" in str(t) for t in called_tools)
                    _edit_called = any("edit_file" in str(t) for t in called_tools)
                    if tests_ran and not _edit_called:
                        result_str = "[SYSTEM: Tests kørte og fejlede Brug edit_file til at rette import-stierne, før du kører tests igen. Desuden ligger originalfilen i git, så du kan finde eventuelt manglende symboler der]"
                        #OLD: result_str = "[SYSTEM: Ret import-fejlene med edit_file FØRST, før du kører tests]"
                        result = {"success": False, "error": "edit_file required first"}
                        agent._log("TOOL", t(K.LOG_TOOL_CALLING, agent.lang).format(tool=tool_name), str(args_val))
                        agent._log("TOOL", t(K.LOG_TOOL_RESULT, agent.lang).format(tool=tool_name), result_str)
                        messages.append({"role": "user", "content": result_str})
                        yield {"type": "tool_call", "tool": tool_name, "args": args_val}
                        yield {"type": "tool_result", "tool": tool_name, "args": args_val, "result": result}
                        continue
                # Plan-deviation check: block batch_extract_symbols if symbols go to wrong module
                # Block docs/ writes during Ekstraher — they waste iterations on status reports
                if tool_name == "write_file" and isinstance(args_val, dict):
                    _wf_path = args_val.get("path", "")
                    _phase_lower = _normalize_phase(task_node.name).lower() if hasattr(task_node, 'name') else ''
                    if getattr(agent, 'active_template', '') == 'refactor' and _phase_lower == "ekstraher":
                        _wf_norm = _wf_path.replace("\\", "/").lower()
                        if _wf_norm.startswith("docs/") or "/docs/" in _wf_norm or (_wf_norm.endswith(".md") and not _wf_norm.endswith("refactor_plan.md")):
                            _block_msg = (
                                "[SYSTEM: Skriv IKKE dokumentation eller statusrapporter i Ekstraher-fasen. "
                                "Brug batch_extract_symbols til at oprette manglende .py moduler. "
                                "Tjek 'Mangler' listen i din budget-besked.]"
                            )
                            messages.append({"role": "user", "content": _block_msg})
                            yield {"type": "tool_call", "tool": tool_name, "args": args_val}
                            yield {"type": "tool_result", "tool": tool_name, "args": args_val,
                                   "result": {"success": False, "error": "docs writes blocked in Ekstraher"}}
                            agent._log("SYSTEM", "docs write blocked in Ekstraher", _wf_path)
                            continue
                if tool_name == "batch_extract_symbols" and isinstance(args_val, dict):
                    _planned = getattr(agent, '_planned_symbols_per_target', None)
                    if _planned:
                        _target = os.path.basename(args_val.get("target", ""))
                        _symbols_raw = args_val.get("symbols", "")
                        _called_syms = set(s.strip() for s in _symbols_raw.split(",") if s.strip())
                        _wrong = []
                        for _sym in _called_syms:
                            for _mod, _plan_syms in _planned.items():
                                if _sym in _plan_syms and os.path.basename(_mod) != _target:
                                    _wrong.append((_sym, os.path.basename(_mod)))
                                    break
                        if _wrong:
                            _wrong_str = ", ".join(f"{s} → {m}" for s, m in _wrong[:5])
                            _deviation_msg = (
                                f"[SYSTEM: Plan-deviation blokeret: {_wrong_str}. "
                                f"Disse symboler hører til et andet modul ifølge planen. "
                                f"Ret dit batch_extract_symbols kald til at bruge det korrekte target-modul.]"
                            )
                            messages.append({"role": "user", "content": _deviation_msg})
                            yield {"type": "tool_call", "tool": tool_name, "args": args_val}
                            yield {"type": "tool_result", "tool": tool_name, "args": args_val,
                                   "result": {"success": False, "error": "Plan deviation blocked"}}
                            agent._log("SYSTEM", "Plan-deviation blocked", f"{_wrong_str} → {_target}")
                            continue
                result = agent.tool_registry.execute(tool_name, args_val)
                if tool_name == "list_symbols" and isinstance(args_val, dict) and isinstance(result, dict) and result.get("success"):
                    agent._list_symbols_cache[args_val["filepath"]] = result
                if tool_name in ("extract_symbol", "batch_extract_symbols") and isinstance(args_val, dict):
                    _src = args_val.get("source", "")
                    if _src in agent._list_symbols_cache:
                        del agent._list_symbols_cache[_src]
                result_str = json.dumps(result, ensure_ascii=False)
                # Flush pending SSE events immediately (e.g. llm_todo_add from plan_phase)
                _pending_flush = getattr(agent, '_pending_sse_events', None)
                if _pending_flush:
                    for evt in _pending_flush:
                        yield evt
                    agent._pending_sse_events = []
                agent._record_tool_call(
                    phase=getattr(task_node, 'name', '?'),
                    tool=tool_name,
                    args=args_val,
                    success=result.get('success', False) if isinstance(result, dict) else True,
                    error=result.get('error', '') if isinstance(result, dict) else '',
                )
                if tool_name in WRITE_TOOLS:
                    if tool_name == "delete_file" and isinstance(result, dict) and result.get("success"):
                        deleted_path = result.get("file", "")
                        if deleted_path:
                            agent._recently_deleted_files.add(deleted_path)
                    if tool_name in ("extract_symbol", "remove_symbol", "add_import"):
                        if isinstance(result, dict) and not result.get("success"):
                            called_tools.pop(tool_key, None)
                        else:
                            agent._current_task_iteration = 0
                            agent._non_productive_reminder_sent = False
                            consecutive_dedups = 0
                    else:
                        agent._current_task_iteration = 0
                        agent._non_productive_reminder_sent = False
                        consecutive_dedups = 0
                agent._log("TOOL", t(K.LOG_TOOL_RESULT, agent.lang).format(tool=tool_name), result_str)
                if isinstance(result, dict) and not result.get("success"):
                    consecutive_failures += 1
                    if consecutive_failures >= 3:
                        _active = agent.tool_registry.active_tools or []
                        alt_tools = [t for t in ("list_symbols", "list_chunks", "read_chunk", "write_file", "edit_file")
                                     if t in _active and t != tool_name]
                        tip = ""
                        if alt_tools:
                            tip = f" Pr\u00f8v i stedet: {', '.join(alt_tools)}."
                        msg = f"[SYSTEM: {consecutive_failures} v\u00e6rkt\u00f8jskald i tr\u00e6k fejlede.{tip}]"
                        _add_user_msg(messages, msg)
                        agent._log("SYSTEM", "Fail-loop escape", f"{consecutive_failures} consecutive failures — {tool_name} fails")
                        consecutive_failures = 0
                else:
                    consecutive_failures = 0
                if tool_name in ("write_file", "edit_file") and isinstance(result, dict) and result.get("success") is False:
                    agent._write_failed = True
                    tool_label = "write_file" if tool_name == "write_file" else "edit_file"
                    result_str += f"\n\n⚠️ {t(K.SYS_ERROR_PREFIX, agent.lang)}: {tool_label} mislykkedes. Læs filen igen og kopier teksten nøjagtigt som old_text."
                if tool_name in ("write_file", "edit_file") and isinstance(result, dict) and result.get("success"):
                    fpath = args_val.get("path", "")
                    if fpath and fpath.lower().endswith('.py'):
                        warning = _check_import_placement(fpath)
                        if warning:
                            result_str += f"\n\n{warning}"
                if tool_name == "run_tests":
                    inner = result.get("result", {}) if isinstance(result, dict) else {}
                    if isinstance(inner, dict) and inner.get("success") is False:
                        agent._tests_failed = True
                    else:
                        agent._tests_failed = False
                    test_summary = _parse_test_summary(inner)
                    exit_code = inner.get("exit_code")
                    if hasattr(agent, '_core'):
                        agent._core.record_test_outcome(
                            test_file=args_val.get("test_path", "") or "run_tests",
                            passed=exit_code == 0,
                            summary=test_summary,
                        )
                    if test_summary:
                        if exit_code == 0:
                            agent._log("INFO", f"✅ Tests PASSED: {test_summary}",
                                       json.dumps({"exit_code": exit_code, "summary": test_summary}, ensure_ascii=False))
                        else:
                            agent._log("ERROR", f"❌ Tests FAILED: {test_summary}",
                                       json.dumps({"exit_code": exit_code, "summary": test_summary}, ensure_ascii=False))
                    elif exit_code == 0:
                        agent._log("INFO", "✅ Tests passed",
                                   json.dumps({"exit_code": 0}))
                    elif exit_code:
                        agent._log("ERROR", f"❌ Tests failed (exit code: {exit_code})",
                                   json.dumps({"exit_code": exit_code}))
                if tool_name == "locate":
                    if isinstance(result, dict) and result.get("success"):
                        agent._located_files.add(os.path.abspath(result.get("file", "")))
                if tool_name == "read_chunk":
                    file_key = args_val.get("file_key", "")
                    if file_key.startswith("file_"):
                        file_path = os.path.abspath(file_key[5:])
                        if file_path in agent._located_files:
                            result_str += "\n\n\u2705 OBS: Du har allerede l\u00e6st funktion(er) i denne fil med locate. Brug locate(filepath='...', name='andet_navn') i stedet for read_chunk \u2014 det er hurtigere."
                if tool_name == "done":
                    error_msg, yields_to_emit = _validate_done_completion(
                        agent, messages, called_tools, task_node, original_prompt,
                        result.get("result", "")
                    )
                    for y_type, y_data in yields_to_emit:
                        yield {"type": y_type, **y_data}
                    agent._log("TOOL", t(K.LOG_TOOL_RESULT, agent.lang).format(tool=tool_name), result_str)
                    yield {"type": "tool_call", "tool": tool_name, "args": args_val}
                    yield {"type": "tool_result", "tool": tool_name, "args": args_val, "result": result}
                    if error_msg:
                        _add_user_msg(messages, error_msg)
                        messages = _truncate_messages(messages, agent.max_conversation_chars, agent)
                        continue
                    full_response = result.get("result", t(K.LOG_TASK_DONE, agent.lang))
                    _pending_flush = getattr(agent, '_pending_sse_events', None)
                    if _pending_flush:
                        for evt in _pending_flush:
                            yield evt
                        agent._pending_sse_events = []
                    break
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id", ""),
                    "content": result_str
                })
                for entry in agent.agent_log[_report_logs:]:
                    yield {"type": "log", "log": entry}
                _report_logs = len(agent.agent_log)
                yield {"type": "tool_call", "tool": tool_name, "args": args_val}
                yield {"type": "tool_result", "tool": tool_name, "args": args_val, "result": result}
                # Auto-check matching todos (both auto and LLM-driven)
                for tid in _auto_todo_update(tool_name, args_val, agent):
                    yield {"type": "todo_update", "id": tid, "done": True}
                _llm = getattr(agent, '_llm_todos', None)
                if _llm:
                    for tids in _match_tool_to_todos(tool_name, args_val, agent, _llm):
                        yield {"type": "llm_todo_update", "id": tids, "done": True, "text": None}
                # Inject plan deviation warnings BEFORE auto-advance (så LLM ser dem)
                _plan_warnings = getattr(agent, '_plan_warnings', None)
                if _plan_warnings:
                    for _pw in _plan_warnings:
                        _missing_str = ', '.join(_pw["missing"])
                        _target = _pw["target"]
                        warning_msg = (
                            f"[SYSTEM: \u26a0\ufe0f UFULDST\u00c6NDIGT MODUL \u2014 du var planlagt at ekstrahere "
                            f"{_pw['planned']} symboler til {_target}, men kaldte kun "
                            f"{_pw['called']}. Mangler: {_missing_str}. "
                            f"\u26d4 G\u00c5 IKKE videre til n\u00e6ste modul. "
                            f"Kald batch_extract_symbols IGEN med de manglende symboler: "
                            f"batch_extract_symbols(source='refac_test.py', symbols='{_missing_str}', target='{_target}'). "
                            f"\u26d4 Oprethold IKKE en ny plan \u2014 f\u00e6rdigg\u00f8r dette modul f\u00f8rst.]"
                        )
                        messages.append({"role": "user", "content": warning_msg})
                    agent._plan_warnings = []
                # Auto-advance check (EFTER todo matching så todos opdateres før break)
                msg = _get_phase_auto_complete_msg(task_node, tool_name, result, agent, called_tools=called_tools, full_response=full_response)
                if msg:
                    agent._log("INFO", msg, "")
                    full_response = msg
                    _pending_flush = getattr(agent, '_pending_sse_events', None)
                    if _pending_flush:
                        for evt in _pending_flush:
                            yield evt
                        agent._pending_sse_events = []
                    break

                # Inject compact progress summary after batch_extract_symbols / extract_symbol
                if tool_name == "batch_extract_symbols" and result.get("success"):
                    inner = result.get("result", {})
                    if isinstance(inner, dict) and inner.get("succeeded", 0):
                        target = os.path.basename(inner.get("target", "?"))
                        source = os.path.basename(inner.get("source", "?"))
                        succeeded = inner.get("succeeded", 0)
                        failed = inner.get("failed", 0)
                        total = inner.get("total", 0)
                        symbols_in_batch = [r.get("symbol", "") for r in inner.get("results", []) if r.get("success")]
                        log.info("batch_extract_symbols RESULT (native): %s → %s (%d/%d succeeded, %d failed): %s",
                                 source, target, succeeded, total, failed, ', '.join(symbols_in_batch))
                        progress_msg = f"[SYSTEM: ✅ {succeeded} symboler flyttet til {target}: {', '.join(symbols_in_batch)}]"
                        messages.append({"role": "user", "content": progress_msg})

                        # Check if this call had a plan deviation (warning already injected above)
                        _has_missing = getattr(agent, '_batch_had_deviation', False)

                        # Opdater todo-tekst med symbol-fremskridt
                        _todo_text = "{} færdig".format(target)
                        _actual = _count_symbols_in_file(inner.get("target", ""))
                        # Find matching per-modul todo og opdater tekst
                        for _t in (getattr(agent, '_phase_todos') or []):
                            _tid = _t.get("id", "")
                            _tt = _t.get("text", "")
                            if target in _tt and _tid.startswith("rf_e_create_"):
                                import re as _re3
                                _pm = _re3.search(r'(\d+)\s*symbols', _tt)
                                if _pm:
                                    _todo_text = "{} færdig — {}/{} symbols".format(target, _actual, _pm.group(1))
                                # Only mark as done if no plan deviation
                                if not _has_missing:
                                    yield {"type": "todo_update", "id": _tid, "done": True, "text": _todo_text}
                                break

                if tool_name == "extract_symbol" and result.get("success"):
                    inner = result.get("result", {})
                    if isinstance(inner, dict) and inner.get("success"):
                        sym = inner.get("symbol", "?")
                        tgt = os.path.basename(inner.get("target", "?"))
                        messages.append({"role": "user", "content": f"[SYSTEM: ✅ {sym} flyttet til {tgt}]"})

                # Disk-based reconciliation (both auto and LLM todos)
                for tid in _reconcile_todos_with_disk(agent):
                    if tid:
                        yield {"type": "todo_update", "id": tid, "done": True}
                for lid in _reconcile_llm_todos(agent):
                    if lid:
                        yield {"type": "llm_todo_update", "id": lid, "done": True, "text": None}

                messages = _truncate_messages(messages, agent.max_conversation_chars, agent)
                # Count only real-work tools toward the limit, not planning tools
                _planning_tools = {"plan_phase", "create_todo", "update_todo", "delete_todo", "list_todos"}
                total_calls = sum(v for k, v in called_tools.items()
                                  if k.split("{")[0] not in _planning_tools)
                if total_calls >= _get_max_tool_calls(task_node.name):
                    full_response = t(K.LOG_AUTO_DONE, agent.lang).format(count=total_calls)
                    break
            if full_response:
                break
            if getattr(agent, '_read_block_hits', 0) >= 2:
                full_response = t(K.LOG_STUCK_AUTO_ADVANCE, agent.lang).format(
                    phase=task_node.name, reads=agent._read_block_hits)
                break
            continue

        pending_reasoning = getattr(agent.llm, '_pending_reasoning', None)
        assistant_msg = {"role": "assistant", "content": response}
        if pending_reasoning and not pending_tc:
            assistant_msg["reasoning_content"] = pending_reasoning
        messages.append(assistant_msg)

        if i == 0 and has_file_ctx:
            messages = [m for m in messages if not (isinstance(m.get("content"), str) and m["content"].startswith("## Filindhold"))]
            has_file_ctx = False

        parsed = agent.tool_registry.parse_response(response)
        ptype = parsed.get("type", "?")
        text = response.strip()
        log_file = _save_llm_log_file(agent, task_node.name, i+1, response)
        if ptype == "tool":
            tool_call = f"{parsed.get('tool','?')}({json.dumps(parsed.get('args',{}), ensure_ascii=False)[:200]})"
            text_before = text.split("<<<TOOL>>>")[0].strip()
            detail = text_before.replace("\n", " ").replace("\r", "") if text_before else ""
            agent._log("LLM", f"🤖 #{i+1} tool: {tool_call}", detail, log_file=log_file)
        elif ptype == "done":
            result = parsed.get("result", "").replace("\n", " ").replace("\r", "")
            pre_done = text.split("<<<DONE>>>")[0].strip().replace("\n", " ").replace("\r", "")
            detail = f"{result}"
            if pre_done:
                detail = f"{pre_done} | {detail}"
            agent._log("LLM", f"🤖 #{i+1} done", detail, log_file=log_file)
        else:
            agent._log("LLM", f"🤖 #{i+1} {ptype}", text.replace("\n", " ").replace("\r", ""), log_file=log_file)

        if parsed["type"] == "tool":
            tool_result = _handle_tool_call(agent, parsed, messages, called_tools, tools_list, task_node, original_prompt)
            if tool_result is None:
                messages = _truncate_messages(messages, agent.max_conversation_chars, agent)
                continue
            for entry in agent.agent_log[_report_logs:]:
                yield {"type": "log", "log": entry}
            _report_logs = len(agent.agent_log)
            yield {"type": "tool_call", "tool": tool_result["tool"], "args": tool_result["args"]}
            yield {"type": "tool_result", "tool": tool_result["tool"], "result": tool_result["result"]}
            # Flush pending SSE events (e.g. llm_todo_add from plan_phase)
            _pending_flush = getattr(agent, '_pending_sse_events', None)
            if _pending_flush:
                for evt in _pending_flush:
                    yield evt
                agent._pending_sse_events = []
            for tid in _auto_todo_update(tool_result["tool"], tool_result["args"], agent):
                yield {"type": "todo_update", "id": tid, "done": True}
            _llm = getattr(agent, '_llm_todos', None)
            if _llm:
                for tids in _match_tool_to_todos(tool_result["tool"], tool_result["args"], agent, _llm):
                    yield {"type": "llm_todo_update", "id": tids, "done": True, "text": None}
            # State-based reconciliation: checkmark todos if work is already done on disk
            for tid in _reconcile_todos_with_disk(agent):
                if tid:
                    yield {"type": "todo_update", "id": tid, "done": True}
            # Disk-based reconciliation for LLM todos (file existence, symbol counts)
            for lid in _reconcile_llm_todos(agent):
                if lid:
                    yield {"type": "llm_todo_update", "id": lid, "done": True, "text": None}
            _track_produced_file(agent, tool_result)
            if agent._produced_files:
                yield {"type": "output_files", "files": sorted(agent._produced_files)}
            if tool_result.get("checkpoint_msg"):
                yield {"type": "checkpoint", "message": tool_result["checkpoint_msg"], "tool": parsed["tool"]}
            msg = _get_phase_auto_complete_msg(task_node, tool_result.get("tool"), tool_result.get("result"), agent, called_tools=called_tools, full_response=full_response)
            if msg:
                agent._log("INFO", msg, "")
                full_response = msg
                _pending_flush = getattr(agent, '_pending_sse_events', None)
                if _pending_flush:
                    for evt in _pending_flush:
                        yield evt
                    agent._pending_sse_events = []
                break
            messages = _truncate_messages(messages, agent.max_conversation_chars, agent)
            total_calls = sum(called_tools.values())
            if total_calls >= _get_max_tool_calls(task_node.name):
                full_response = t(K.LOG_AUTO_DONE, agent.lang).format(count=total_calls)
                _pending_flush = getattr(agent, '_pending_sse_events', None)
                if _pending_flush:
                    for evt in _pending_flush:
                        yield evt
                    agent._pending_sse_events = []
                break
            if getattr(agent, '_read_block_hits', 0) >= 2:
                full_response = t(K.LOG_STUCK_AUTO_ADVANCE, agent.lang).format(
                    phase=task_node.name, reads=agent._read_block_hits)
                _pending_flush = getattr(agent, '_pending_sse_events', None)
                if _pending_flush:
                    for evt in _pending_flush:
                        yield evt
                    agent._pending_sse_events = []
                break
            continue

        if parsed["type"] == "done":
            # Fix 2: Don't block DONE when edit_file failed — let _check_required_tools handle it
            if agent._tests_failed and "test" not in _normalize_phase(task_node.name).lower():
                _add_user_msg(messages, f"{t(K.SYS_ERROR_PREFIX, agent.lang)}: DU KAN IKKE afslutte med <<<DONE>>> når tests fejler. Ret koden med edit_file og kør run_tests() igen indtil ALLE tests består.")
                messages = _truncate_messages(messages, agent.max_conversation_chars, agent)
                continue
            # Block <<<DONE>>> in Analyse if refactor_analyse.md hasn't been written
            _dphase = _normalize_phase(task_node.name).lower()
            if _dphase == "analyse" and getattr(agent, 'active_template', '') == 'refactor':
                _dpath = "refactor_analyse.md"
                _dwd = os.environ.get('AGENT_WORKDIR', '')
                if _dwd:
                    _dpath = os.path.join(_dwd, _dpath)
                if not os.path.exists(_dpath):
                    _add_user_msg(messages, f"{t(K.SYS_ERROR_PREFIX, agent.lang)}: Du kan ikke afslutte Analyse før du har gemt din analyse. Skriv til **`refactor_analyse.md`** med write_file() først.")
                    messages = _truncate_messages(messages, agent.max_conversation_chars, agent)
                    continue
            if not _check_done_pr_requirements(agent, messages, called_tools, original_prompt, task_node.name):
                messages = _truncate_messages(messages, agent.max_conversation_chars, agent)
                if agent_git.is_pr_workflow(task_node.name):
                    yield {"type": "checkpoint", "message": t(K.CP_PR_FAILED, agent.lang), "tool": "done"}
                continue
            passed, failed = _validate_rubrics(agent, called_tools)
            if failed:
                try:
                    from skill_tracker import tracker
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
                _add_user_msg(messages, feedback)
                messages = _truncate_messages(messages, agent.max_conversation_chars, agent)
                continue
            missing_msg = _check_required_tools(agent, called_tools, task_node.name)
            if missing_msg:
                _add_user_msg(messages, f"{t(K.SYS_ERROR_PREFIX, agent.lang)}: {missing_msg}")
                messages = _truncate_messages(messages, agent.max_conversation_chars, agent)
                continue
            validation_err = _validate_done_output(agent, parsed.get("result", ""), task_node.name, task_node)
            if validation_err:
                _add_user_msg(messages, f"{t(K.SYS_ERROR_PREFIX, agent.lang)}: {validation_err}")
                messages = _truncate_messages(messages, agent.max_conversation_chars, agent)
                continue
            fix_err = _count_fix_attempts(agent, called_tools)
            if fix_err:
                _add_user_msg(messages, f"{t(K.SYS_ERROR_PREFIX, agent.lang)}: {fix_err}")
                messages = _truncate_messages(messages, agent.max_conversation_chars, agent)
                continue
            full_response = parsed["result"]
            done_idx = response.find(agent.tool_registry.DONE_MARKER)
            if done_idx > 0:
                pre_done = response[:done_idx].strip()
                if len(pre_done.strip()) > max(50, len(full_response) * 2):
                    full_response = pre_done
            break

        if parsed["type"] == "error":
            consecutive_errors += 1
            if consecutive_errors >= 3:
                if _use_native_tools(agent):
                    yield {"type": "error", "message": f"3 consecutive format errors — stopping. Use the available tools to complete the task."}
                else:
                    yield {"type": "error", "message": f"3 consecutive format errors — stopping. Use format: {agent.tool_registry.TOOL_MARKER}{{\"tool\":\"...\",\"args\":{{...}}}}{agent.tool_registry.END_MARKER} or {agent.tool_registry.DONE_MARKER}{{...}}{agent.tool_registry.END_MARKER}"}
                break
            if consecutive_errors == 1:
                if _use_native_tools(agent):
                    hint = f"{t(K.SYS_USE_AVAILABLE_TOOLS, agent.lang)}: {', '.join(list(called_tools.keys())[:3]) if called_tools else ', '.join(agent.tool_registry.active_tools[:3])}"
                else:
                    hint = f"Write your response in the correct format: {agent.tool_registry.TOOL_MARKER}{{\"tool\":\"{list(called_tools.keys())[0] if called_tools else 'write_file'}\",\"args\":{{...}}}}{agent.tool_registry.END_MARKER}"
                _add_user_msg(messages, f"{t(K.SYS_ERROR_PREFIX, agent.lang)}: {parsed['message']}. {hint}")
            else:
                _add_user_msg(messages, f"{t(K.SYS_ERROR_PREFIX, agent.lang)}: {parsed['message']}. Only use <<<TOOL>>> or <<<DONE>>> — no English text before or after.")
            messages = _truncate_messages(messages, agent.max_conversation_chars, agent)
            continue

        if i == 0 and not called_tools:
            all_files_loaded = all(len(v) <= 1 for v in agent.file_chunks.values()) if agent.file_chunks else True
            has_write_tools = any(t in ('write_file', 'edit_file') for t in (agent.tool_registry.active_tools or []))
            if all_files_loaded and not has_write_tools and parsed["type"] in ("text", "done"):
                text_fallback = response.strip() if parsed["type"] == "text" else parsed.get("result", response.strip())
                if text_fallback and "ERROR" not in text_fallback and not text_fallback.startswith("<<<") and len(text_fallback) > 100:
                    full_response = text_fallback
                    break
            if parsed["type"] == "text":
                if not _use_native_tools(agent):
                    tool_for_msg = agent.tool_registry.active_tools[0] if agent.tool_registry.active_tools else t(K.SYS_FALLBACK_TOOL, agent.lang)
                    _add_user_msg(messages, f"{t(K.SYS_ERROR_PREFIX, agent.lang)}: {t(K.FIRST_TOOL_REQUIRED, agent.lang).format(tool=tool_for_msg)}")
                messages = _truncate_messages(messages, agent.max_conversation_chars, agent)
                continue

        clean = response.strip() if "ERROR" not in response else ""
        if clean:
            text_fallback = clean
        _add_user_msg(messages, t(K.TOOL_NO_RESULT, agent.lang))
        messages = _truncate_messages(messages, agent.max_conversation_chars, agent)
        full_response = response
        # Break if 3+ consecutive text-only responses (no tool calls in this iteration)
        if parsed["type"] == "text":
            consecutive_text_only += 1
        else:
            consecutive_text_only = 0
        if i >= 3 and consecutive_text_only >= 3:
            agent._log("SYSTEM", "Text-only loop escape", f"{consecutive_text_only} consecutive text-only responses")
            break

    yield from _finalize_task_stream(agent, task_node, full_response, text_fallback, called_tools, _report_logs, original_prompt, messages)
