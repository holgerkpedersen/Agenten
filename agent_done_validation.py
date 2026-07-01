import re
from i18n import K
from lang import t
from typing import Any, Generator
import config
import os
import agent_git
import agent_phase_checks
from agent_message_builder import _add_user_msg
from agent_rubric import _validate_rubrics
from agent_utils import _use_native_tools, _normalize_phase

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
    if any(k in phase for k in ["analyse", "læs", "afklar", "analysis", "análisis", "分析"]):
        has_issue_id = bool(re.search(r'(BUG|SEC|TST|ARC|PRF|MNT|REFAC)-\d+', result_text))
        has_keyword = any(w in rt for w in ["bug", "confirmed", "already fixed", "location",
                                             "fejl", "bekræftet",
                                             "error", "verified", "confirmed", "found",
                                             "encontrado", "confirmado", "ubicación",
                                             "错误", "确认", "已修复", "位置", "找到"])
        if not (has_issue_id and has_keyword):
            return t(K.VALIDATION_DONE_MISSING_KEYWORDS, agent.lang).format(
                phase, "issue-id + bug/location status")
    elif any(k in phase for k in ["implementering", "fix", "test", "ekstraher", "opdatér",
                                   "implementation", "extract"]):
        has_keyword = any(w in rt for w in ["changed", "fixed", "edited", "implemented",
                                             "rettede", "implementerede", "skrevet", "fil",
                                             "modificado", "corregido", "implementado",
                                             "escrito", "archivo",
                                             "修改", "修复", "实现", "编写", "文件"])
        if not has_keyword:
            return t(K.VALIDATION_DONE_MISSING_KEYWORDS, agent.lang).format(
                phase, "what was changed/implemented")
    elif any(k in phase for k in ["luk", "close", "opdatering", "verifikation",
                                   "completion", "update", "verification",
                                   "cerrar", "完成", "更新", "验证"]):
        has_keyword = any(w in rt for w in ["resolved", "resolution", "lukket", "afsluttet",
                                             "tests pass", "består",
                                             "cerrado", "finalizado", "pruebas pasan",
                                             "已解决", "已完成", "测试通过"])
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



def _ensure_done_tool(agent: Any) -> None:
    """Tilføj 'done' til active_tools hvis NATIVE_TOOLS er slået til."""
    if not _use_native_tools(agent):
        return
    # active_tools=None means ALL tools — done is already registered, so nothing to do
    if agent.tool_registry.active_tools is None:
        return
    if "done" not in agent.tool_registry.active_tools:
        agent.tool_registry.set_active_tools(agent.tool_registry.active_tools + ["done"])



def _validate_done_completion(
    agent: Any, messages: list[dict], called_tools: dict[str, int],
    task_node: Any, original_prompt: str, result_text: str = ""
) -> tuple[str | None, list[tuple]]:
    """F\u00e6lles valideringsk\u00e6de for b\u00e5de <<<DONE>>> (text-mode) og done() (native)."""
    yields_to_emit: list[tuple] = []
    from agent_tool_handler import _check_required_tools

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
    # Skip for refactor template — these phases deliver a .md file, not a plan
    _phase_check = _normalize_phase(task_node.name).lower()
    _skip_plan_required = (
        getattr(agent, 'active_template', '') == 'refactor'
        and _phase_check in ('analyse', 'plan')
    )
    if _phase_check in ("analyse", "plan") and not getattr(agent, '_llm_has_planned', False) and not _skip_plan_required:
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
