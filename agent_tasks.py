"""Agent tasks execution module."""

import ast
import os
import re
import time
import json
import subprocess
from i18n import K
from lang import t
import agent_skills
import agent_git
import agent_files
import agent_issues
import agent_phase_checks
import agent_autoresearch
import config
from typing import Any, Generator
from llm_wrapper import LMStudioWrapper


def _use_native_tools(agent: Any | None = None) -> bool:
    """Check if native function calling should be used.
    
    Returns False when the model is in NATIVE_TOOLS_BLACKLIST
    (models that don't support OpenAI function calling well),
    even if config.NATIVE_TOOLS is True.
    """
    if not config.NATIVE_TOOLS:
        return False
    if agent is None:
        return True
    model = getattr(getattr(agent, 'llm', None), 'model', '')
    if not model:
        return True
    return LMStudioWrapper._supports_native_tools(model)


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

EXECUTION_TIMEOUT = config.EXECUTION_TIMEOUT

_WRITE_TOOLS = frozenset({"write_file", "edit_file", "write_file_section", "convert_pdf_html5"})


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


def _save_llm_prompt_file(agent: Any, task_name: str, iteration: int, messages: list[dict]) -> str:
    """Save full LLM prompt (all messages) to a file for later inspection."""
    session_id = getattr(agent, '_session_id', 'unknown')
    session_dir = os.path.join("logs", "llm_prompts", session_id)
    os.makedirs(session_dir, exist_ok=True)
    safe_name = task_name.replace(" ", "_").replace("/", "_").replace("\\", "_")[:30]
    filename = f"{safe_name}_iter{iteration}.json"
    filepath = os.path.join(session_dir, filename)
    try:
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(messages, f, ensure_ascii=False, indent=2, default=str)
    except Exception:
        pass
    return filepath


def _save_maintenance_prompt_dump(agent: Any, task_name: str, iteration: int, messages: list[dict], tool_defs: list[dict], pending_tc: list | None = None) -> str:
    session_id = getattr(agent, '_session_id', 'unknown')
    session_dir = os.path.join("logs", "maintenance_prompt_dump", session_id)
    os.makedirs(session_dir, exist_ok=True)
    safe_name = task_name.replace(" ", "_").replace("/", "_").replace("\\", "_")[:30]
    filepath = os.path.join(session_dir, f"{safe_name}_iter{iteration}.json")
    dump = {
        "session_id": session_id,
        "template": agent.active_template,
        "phase": task_name,
        "iteration": iteration,
        "is_greenfield": _is_greenfield(),
        "active_tools": list(agent.tool_registry.active_tools or []),
        "native_tools_enabled": config.NATIVE_TOOLS,
        "tool_choices": [tc.get("function", {}).get("name", "?") for tc in pending_tc] if pending_tc else [],
        "messages": messages[:2],
        "tool_definitions": tool_defs,
    }
    try:
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(dump, f, ensure_ascii=False, indent=2, default=str)
    except Exception:
        pass
    return filepath


def _save_llm_log_file(agent: Any, task_name: str, iteration: int, content: str) -> str:
    """Save LLM response to a file for later inspection."""
    session_id = getattr(agent, '_session_id', 'unknown')
    session_dir = os.path.join("logs", "llm_responses", session_id)
    os.makedirs(session_dir, exist_ok=True)
    safe_name = task_name.replace(" ", "_").replace("/", "_").replace("\\", "_")[:30]
    filename = f"{safe_name}_iter{iteration}.txt"
    filepath = os.path.join(session_dir, filename)
    try:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
    except Exception:
        pass
    return filepath


def _resolve_refactor_plan_path(agent, plan_file="refactor_plan.md"):
    """Resolve refactor plan path against AGENT_WORKDIR.
    
    Tries workdir-relative first, then falls back to agent._refactor_plan_path.
    Returns the correct absolute path or empty string if not found.
    """
    wd_resolve = os.environ.get('AGENT_WORKDIR', '')
    if wd_resolve:
        refac_plan_path = os.path.join(wd_resolve, plan_file)
    else:
        refac_plan_path = plan_file
    if not os.path.exists(refac_plan_path):
        refac_plan_path = getattr(agent, '_refactor_plan_path', '')
        if not refac_plan_path and plan_file:
            refac_plan_path = plan_file
        if refac_plan_path and not os.path.isabs(refac_plan_path) and wd_resolve:
            refac_plan_path = os.path.join(wd_resolve, refac_plan_path)
    return refac_plan_path if (refac_plan_path and os.path.exists(refac_plan_path)) else ""


def _check_import_placement(filepath: str) -> str | None:
    """Check if any import statements are inside functions/classes.
    Returns a warning message if found, None otherwise."""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            code = f.read()
    except Exception:
        return None
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return None
    bad = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            for child in ast.walk(node):
                if child is node:
                    continue
                if isinstance(child, (ast.Import, ast.ImportFrom)):
                    names = ", ".join(a.name for a in child.names)
                    kind = type(node).__name__
                    bad.append(f"'{names}' inside {kind} '{node.name}' at line {child.lineno}")
    if bad:
        return "\u26a0\ufe0f Import inde i funktion/klasse: " + "; ".join(bad) + ". Flyt importen til toppen af filen."
    return None


PHASE_ALIASES = {
    "analyse": "analyse", "analysis": "analyse",
    "test": "test",
    "implementering": "implementering", "implementation": "implementering",
    "verifikation": "verifikation", "verification": "verifikation", "green": "verifikation",
    "opdatering": "opdatering", "update": "opdatering",
    "ekstraher": "ekstraher", "extract": "ekstraher",
    "plan": "plan",
    "opdatér": "opdatér",
    "læs": "analyse", "read": "analyse",
    "afklar": "analyse", "clarify": "analyse",
    "afklar & opdater": "analyse", "clarify & update": "analyse",
    "verificer": "analyse", "verify": "analyse",
    "fix": "fix",
    "luk": "luk", "close": "luk",
    "luk issue": "luk", "close issue": "luk",
}


def _normalize_phase(name: str) -> str:
    """normalize phase.
    
    Args:
        name:"""
    lower = name.lower().split("(")[0].strip()
    lower = re.sub(r'^[\d.]+[\)\s]*', '', lower).strip()
    return PHASE_ALIASES.get(lower, lower)


def _get_phase_task_tools(agent: Any, task_name: str) -> set[str] | None:
    """Return the set of REQUIRED_ACTION_TOOLS that the phase's task tools list.
    Returns None when no template or no phase-specific list is found, so
    callers fall back to the default 'all action tools' behaviour.
    """
    template = getattr(agent, "active_template", "") or ""
    if not template or not task_name:
        return None
    phase = _normalize_phase(task_name).lower()
    template_tools = agent_skills.TEMPLATE_TASK_TOOLS.get(template, {})
    if phase in template_tools:
        return {t for t in REQUIRED_ACTION_TOOLS if t in template_tools[phase]}
    for key, tools in template_tools.items():
        if key.lower() in phase or phase in key.lower():
            return {t for t in REQUIRED_ACTION_TOOLS if t in tools}
    return None


def _refactor_actually_moved_code(agent: Any) -> bool:
    """Return True if a real refactor was performed (ALL modules moved + api_server reduced).

    For the refactor template, a passing test suite is NOT proof that the
    refactor was done — tests pass against the unchanged api_server.py if no
    code was actually moved. This helper verifies ALL three conditions:

      1. ``refactor_plan.md`` exists and lists at least one module
      2. EVERY module listed in the plan exists on disk AND has substantive
         code (functions or classes with >= 20 lines)
      3. ``api_server.py`` has been reduced to under 1000 lines (the original
         file was 1521 lines, so any real refactor must shrink it)

    Returns True unconditionally when the active template is not ``refactor``
    (e.g. bugfix, kodeanalyse) so existing behaviour is preserved.
    """
    template = getattr(agent, "active_template", "") or ""
    if template != "refactor":
        return True
    _wd = agent_files._resolve_workdir()
    plan_path = os.path.join(_wd, "refactor_plan.md")
    if not os.path.exists(plan_path):
        return False
    try:
        import agent_phase_checks as _apc
        modules = _apc._parse_refactor_plan_modules(plan_path)
    except Exception:
        return False
    if not modules:
        return False
    for mod in modules:
        if not mod or "/" in mod or "\\" in mod:
            continue
        path = os.path.join(_wd, mod)
        if not _apc._has_real_code(path, min_lines=20):
            return False
    # Check that the source file (from plan header) has been reduced
    # Use plan header: "# Refactor Plan for <file.py>"
    _target_file = None
    try:
        with open(plan_path, "r", encoding="utf-8") as _f:
            _first = _f.readline(200)
        _m = re.search(r'for\s+([a-zA-Z_][\w.]+\.py)', _first, re.IGNORECASE)
        if _m:
            _target_file = _m.group(1)
    except (OSError, UnicodeDecodeError):
        pass
    if _target_file:
        _target_path = os.path.join(_wd, _target_file)
        if os.path.exists(_target_path):
            try:
                with open(_target_path, encoding="utf-8") as f:
                    _line_count = sum(1 for _ in f)
                # Original was ~1000+, reduced version should be well under 500
                if _line_count >= 500:
                    return False
            except OSError:
                return False
    else:
        # Fallback: check api_server.py (legacy refactors)
        _api_path = os.path.join(_wd, "api_server.py")
        if os.path.exists(_api_path):
            try:
                with open(_api_path, encoding="utf-8") as f:
                    _line_count = sum(1 for _ in f)
                if _line_count >= 1000:
                    return False
            except OSError:
                return False
    return True


def _build_refactor_phase_context(agent: Any, source_file: str = "api_server.py") -> str:
    """Build a structured symbol-status block for refactor phases.
    Reads refactor_plan.md + AST of source/target modules so the LLM
    sees EXACTLY which symbols need extraction/cleanup.
    """
    plan_path = _resolve_refactor_plan_path(agent, "refactor_plan.md")
    if not plan_path or not os.path.exists(plan_path):
        return ""

    modules = agent_phase_checks._parse_refactor_plan_modules(plan_path)
    if not modules:
        return ""

    _wd2 = agent_files._resolve_workdir()

    try:
        with open(plan_path, encoding="utf-8") as f:
            plan_text = f.read()
    except Exception:
        plan_text = ""

    src_result = agent_files.list_symbols(source_file)
    source_syms: dict[str, str] = {}
    if src_result.get("success"):
        source_syms = {s["name"]: s.get("type", "?")
                       for s in src_result["symbols"]
                       if s.get("type") in ("function", "class", "async_function")}

    from symbol_checks import _parse_plan_symbol_mapping
    per_module = _parse_plan_symbol_mapping(plan_text)

    parts: list[str] = []
    parts.append(f"\n\n## STATUS: Symboler i {source_file} vs plan")

    for mod_name in sorted(modules):
        mod_path = os.path.join(_wd2, mod_name)
        planned_syms = per_module.get(mod_name, [])
        if not planned_syms:
            continue

        target_syms: set[str] = set()
        if os.path.exists(mod_path):
            tgt_result = agent_files.list_symbols(mod_path)
            if tgt_result.get("success"):
                target_syms = {s["name"] for s in tgt_result["symbols"]
                               if s.get("type") in ("function", "class", "async_function")}

        in_source = sorted(s for s in planned_syms if s in source_syms)
        in_target = sorted(s for s in planned_syms if s in target_syms)
        missing = sorted(s for s in planned_syms if s not in source_syms and s not in target_syms)

        parts.append(f"\n### {mod_name}")
        if in_source:
            parts.append(f"  I {source_file} (skal flyttes til {mod_name}): {', '.join(in_source)}")
        if in_target:
            parts.append(f"  ALLEREDE i {mod_name}: {', '.join(in_target)}")
        if missing:
            parts.append(f"  MANGLER (skal oprettes i {mod_name}): {', '.join(missing)}")

    all_planned = set()
    for syms in per_module.values():
        all_planned.update(syms)
    unplanned = sorted(s for s in source_syms if s not in all_planned)
    if unplanned:
        parts.append(f"\n### Ikke i planen (beholdes i {source_file})")
        parts.append(f"  {', '.join(unplanned)}")

    return "\n".join(parts)


def _get_max_tool_calls(task_name: str) -> int:
    """get max tool calls.
    
    Args:
        task_name:"""
    phase = _normalize_phase(task_name).lower()
    if any(k in phase for k in ["analyse", "formål", "læs", "afklar"]):
        return config.MAX_TOOL_CALLS_ANALYSE
    if any(k in phase for k in ["implementering", "fix", "test", "ekstraher", "opdatér",
                                  "kravanalyse", "arkitekturdesign", "kodeimplementering"]):
        return config.MAX_TOOL_CALLS_FIX
    if any(k in phase for k in ["luk", "close", "opdatering", "verifikation"]):
        return config.MAX_TOOL_CALLS_CLOSE
    return config.MAX_TOOL_CALLS_ANALYSE


def _get_max_iterations(agent: Any, task_name: str) -> int:
    """Get the max LLM conversation turns for this phase.

    Looks up per-template override in TEMPLATE_PHASE_ITERATION_LIMITS, then
    falls back to MAX_TASK_ITERATIONS (or MAX_PR_TASK_ITERATIONS for PR
    workflows) from config.
    """
    template = getattr(agent, "active_template", "") or ""
    if template and task_name:
        template_limits = agent_skills.TEMPLATE_PHASE_ITERATION_LIMITS.get(template, {})
        # Case-insensitive phase match
        task_lower = (task_name or "").lower()
        for key, limit in template_limits.items():
            if key.lower() == task_lower:
                return limit
    if agent_git.is_pr_workflow(task_name):
        return config.MAX_PR_TASK_ITERATIONS
    return config.MAX_TASK_ITERATIONS


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


def _is_greenfield() -> bool:
    """Return True if target workdir has NO .py files (pure greenfield project)."""
    workdir = os.environ.get('AGENT_WORKDIR') or os.getcwd()
    try:
        for f in os.listdir(workdir):
            if f.endswith(".py") and os.path.isfile(os.path.join(workdir, f)):
                return False
    except OSError:
        pass
    return True


def set_task_tools(agent: Any, task_name: str) -> None:
    """set task tools.
    
    Args:
        agent:
        task_name:"""
    _TODO_TOOLS = {"plan_phase", "create_todo", "update_todo", "delete_todo", "list_todos"}

    def _inject_todo_tools(tools: list[str]) -> list[str]:
        """Add universal todo tools to a tool list if missing."""
        for t in _TODO_TOOLS:
            if t not in tools:
                tools.append(t)
        return tools

    if not agent.active_template or agent.active_template not in agent_skills.TEMPLATE_TASK_TOOLS:
        _ensure_done_tool(agent)
        return
    template_tools = agent_skills.TEMPLATE_TASK_TOOLS[agent.active_template]
    phase = _normalize_phase(task_name)
    # For refactor Ekstraher: list_symbols er unødvendigt — symboler og
    # modulopdelinger auto-injectes i prompten af _build_initial_messages.
    # Fjern det efter tool-assignment så LLM'en ikke spilder iterationer.
    _refactor_ekstraher = agent.active_template == "refactor" and "ekstraher" in phase
    # Sorter edit_file før run_tests for alle test/verifikation faser
    _edit_before_tests = any(k in phase for k in ("test", "verifikation", "green"))
    if phase in template_tools:
        tools = list(template_tools[phase])
        if _refactor_ekstraher:
            tools = [t for t in tools if t != "list_symbols"]
        if _edit_before_tests:
            tools.sort(key=lambda t: t != "edit_file")  # edit_file før run_tests
        # programmering/kodeimplementering: adapt tool order to project context
        if agent.active_template == "programmering" and "kodeimplementering" in phase:
            is_greenfield = _is_greenfield()
            if is_greenfield:
                tools.sort(key=lambda t: t != "write_file")  # write_file first
        tools = _inject_todo_tools(tools)
        if _refactor_ekstraher:
            # Fjern plan_phase/create_todo så LLM ikke laver egne
            # position-baserede grupperinger — auto-populated todos
            # fra _auto_populate_llm_todos har konkrete batch-kald.
            tools = [t for t in tools if t not in ("plan_phase", "create_todo")]
        agent.tool_registry.set_active_tools(tools)
        agent._log("TOOL", f"Aktive tools for '{task_name[:40]}'", ', '.join(agent.tool_registry.active_tools or tools))
        _ensure_done_tool(agent)
        return
    for keyword, tools_kv in template_tools.items():
        if keyword in phase.lower():
            tools = list(tools_kv)
            if _refactor_ekstraher:
                tools = [t for t in tools if t != "list_symbols"]
            if _edit_before_tests:
                tools.sort(key=lambda t: t != "edit_file")
            if agent.active_template == "programmering" and "kodeimplementering" in phase:
                is_greenfield = _is_greenfield()
                if is_greenfield:
                    tools.sort(key=lambda t: t != "write_file")  # write_file first
            tools = _inject_todo_tools(tools)
            if _refactor_ekstraher:
                tools = [t for t in tools if t not in ("plan_phase", "create_todo")]
            agent.tool_registry.set_active_tools(tools)
            agent._log("TOOL", f"Aktive tools for '{task_name[:40]}'", ', '.join(tools))
            _ensure_done_tool(agent)
            return
    # Fallback: use generic template tools if no phase-specific match
    allowed = agent_skills.TEMPLATE_TOOLS.get(agent.active_template)
    if allowed is not None:
        tools = _inject_todo_tools(list(allowed))
        if _refactor_ekstraher:
            tools = [t for t in tools if t not in ("plan_phase", "create_todo")]
        agent.tool_registry.set_active_tools(tools)
    _ensure_done_tool(agent)


def solve_task(agent: Any, task_node: Any, original_prompt: str) -> str:
    """solve task.
    
    Args:
        agent:
        task_node:
        original_prompt:"""
    agent._log("INFO", f"Starting task: {task_node.name}")
    full_response = ""
    for event in solve_task_stream(agent, task_node, original_prompt):
        if event["type"] == "done":
            full_response = event["result"]
    return full_response or "Task failed"


def _build_chunk_hint(agent: Any) -> str:
    """build chunk hint.
    
    Args:
        agent:"""
    available_keys = list(agent.file_chunks.keys())
    hint = ""
    if available_keys:
        parts = []
        for key in available_keys:
            total = len(agent.file_chunks[key])
            display = key.replace("file_", "", 1)
            parts.append(f"\n  {display} ({total} chunk{'s' if total > 1 else ''})")
        base_dir = os.environ.get("AGENT_WORKDIR", "") or os.path.abspath('.')
        hint = f"\n\n## TILG\u00c6NGELIGE FILER (projektmappe: {base_dir})"
        hint += "".join(parts)
        hint += "\n\n  Brug list_symbols(filepath='fil.py') for at se ALLE symboler (funktioner, klasser, variabler) i en Python-fil — g\u00f8r det F\u00d8R locate/read_location n\u00e5r du ikke kender symbolnavnene."
        hint += "\n  Brug locate(name='funktionsnavn') for at finde en funktion p\u00e5 tv\u00e6rs af ALLE .py-filer (filepath er valgfri)."
        hint += "\n  locate returnerer ogs\u00e5 en 'also_in_file'-liste over andre symboler i filen — brug locate til hver enkelt."
        hint += "\n  Brug read_location(filepath='fil.py', name='funktionsnavn') for at l\u00e6se KUN en bestemt funktion/metode/klasse — IKKE hele filen."
        hint += "\n  Brug IKKE read_chunk til .py-filer — read_location er altid at foretr\u00e6kke og returnerer kun det relevante kode."
        hint += "\n  Read_chunk m\u00e5 KUN bruges til IKKE-PYTHON filer (JSON, HTML, TXT, osv.)."
    delegation_lines = []
    for key, chunks in agent.file_chunks.items():
        content = chunks[0] if chunks else ''
        if not content:
            continue
        for func_name, target_mod in agent_files.detect_delegations(content):
            target_key = f'file_{target_mod}.py'
            if target_key in agent.file_chunks:
                delegation_lines.append(f'  - {key.replace("file_", "", 1)}:{func_name} \u2192 rediger i stedet {target_mod}.py:{func_name}')
            else:
                delegation_lines.append(f'  - {key.replace("file_", "", 1)}:{func_name} \u2192 {target_mod}.py (ikke indl\u00e6st)')
    if delegation_lines:
        hint += '\n\n## DELEGERINGER\nNogle funktioner i de indl\u00e6ste filer er stubs, der kun videresender til en anden fil.\n' + '\n'.join(delegation_lines) + '\n'
    return hint


def _build_phase_reason(template: str, phase_name: str, original_prompt: str) -> str:
    """Build an 'I'm working on X for Y. They need Z. With that:' reason block.

    Gives the LLM context about WHY this phase exists, not just WHAT to do.
    The pattern helps the LLM understand the purpose and act more intelligently.
    """
    phase = _normalize_phase(phase_name).lower()
    file_match = re.search(r"([a-zA-Z_][\w.]+\.py)", original_prompt or "")
    target_file = file_match.group(1) if file_match else "koden"

    reasons = {
        ("refactor", "analyse"): (
            f"Jeg arbejder p\u00e5 at opdele {target_file} i mindre moduler. "
            f"Brugeren har brug for at forst\u00e5 symbolstrukturen og afh\u00e6ngighederne "
            f"f\u00f8r modulopdeling."
        ),
        ("refactor", "plan"): (
            f"Jeg har nu overblik over {target_file}. "
            f"Brugeren har brug for en konkret plan for hvilke moduler der skal oprettes."
        ),
        ("refactor", "ekstraher"): (
            f"Planen er klar. "
            f"Brugeren har brug for at symboler flyttes fra {target_file} til nye modulfiler."
        ),
        ("refactor", "opdat\u00e9r"): (
            f"Modulerne er oprettet. "
            f"Brugeren har brug for at {target_file} opryddes "
            f"\u2014 fjern flyttede symboler og tilf\u00f8j imports."
        ),
        ("refactor", "test"): (
            f"Refaktoreringen er udf\u00f8rt. "
            f"Brugeren har brug for at verificere at alle tests stadig best\u00e5r."
        ),
        ("bugfix", "analyse"): (
            f"Jeg unders\u00f8ger en bugrapport. "
            f"Brugeren har brug for at forst\u00e5 hvor fejlen opst\u00e5r."
        ),
        ("bugfix", "test"): (
            f"Jeg har forst\u00e5et fejlen. "
            f"Brugeren har brug for en test der reproducerer den."
        ),
        ("bugfix", "implementering"): (
            f"Testen bekr\u00e6fter fejlen. "
            f"Brugeren har brug for en minimal rettelse."
        ),
        ("bugfix", "verifikation"): (
            f"Rettelsen er anvendt. "
            f"Brugeren har brug for at bekr\u00e6fte at alle tests best\u00e5r."
        ),
        ("bugfix", "opdatering"): (
            f"Alt virker. "
            f"Brugeren har brug for at issuet markeres som l\u00f8st."
        ),
        ("selvforbedring", "analyser"): (
            f"Jeg unders\u00f8ger hvorfor en fase fejlede. "
            f"Brugeren har brug for at forst\u00e5 fejlkonteksten."
        ),
        ("selvforbedring", "diagnostic\u00e9r"): (
            f"Jeg har overblikket. "
            f"Brugeren har brug for at identificere rod\u00e5rsagen."
        ),
        ("selvforbedring", "ret"): (
            f"Rod\u00e5rsagen er kendt. "
            f"Brugeren har brug for at koden rettes."
        ),
        ("selvforbedring", "verific\u00e9r"): (
            f"Rettelsen er anvendt. "
            f"Brugeren har brug for at tests k\u00f8rer og issuet lukkes."
        ),
        ("selvforbedring", "commit"): (
            f"Alt er verificeret. "
            f"Brugeren har brug for at \u00e6ndringerne committes."
        ),
        ("issue_handler", "l\u00e6s"): (
            f"Jeg har f\u00e5et et issue. "
            f"Brugeren har brug for at forst\u00e5 hvad der skal laves."
        ),
        ("issue_handler", "afklar"): (
            f"Jeg har l\u00e6st issuet. "
            f"Brugeren har brug for at afklare pr\u00e6cis hvad der skal \u00e6ndres."
        ),
        ("issue_handler", "fix"): (
            f"Jeg ved hvad der skal laves. "
            f"Brugeren har brug for at koden rettes og testes."
        ),
        ("issue_handler", "luk"): (
            f"Fikset er implementeret. "
            f"Brugeren har brug for at issuet markeres som l\u00f8st."
        ),
    }

    key = (template or "").lower(), phase
    if key in reasons:
        return f"## Baggrund\n{reasons[key]}\n"
    return ""



def _build_initial_messages(agent: Any, task_node: Any, original_prompt: str, chunk_hint: str) -> tuple[list[dict], str, bool]:
    """build initial messages.
    
    Args:
        agent:
        task_node:
        original_prompt:
        chunk_hint:"""
    clean_prompt = getattr(agent, 'prompt', original_prompt)
    file_ctx = getattr(agent, '_file_context_str', '')

    # Maintenance mode: use vedligeholdelse section instruction when .py files exist
    phase_name = task_node.name or ""
    maintenance_key = phase_name + " (vedligeholdelse)"
    is_maintenance = (
        agent.active_template == "programmering"
        and "kodeimplementering" in _normalize_phase(phase_name)
        and not _is_greenfield()
    )
    if is_maintenance:
        section_instr = agent_skills.SECTION_INSTRUCTIONS.get(agent.active_template, {}).get(maintenance_key, "")
    else:
        section_instr = agent_skills.SECTION_INSTRUCTIONS.get(agent.active_template, {}).get(agent.lang + "_" + phase_name.lower(), "") or \
                        agent_skills.SECTION_INSTRUCTIONS.get(agent.active_template, {}).get(phase_name.lower(), "") or \
                        agent_skills.SECTION_INSTRUCTIONS.get(agent.active_template, {}).get(phase_name, "") or \
                        agent_skills.SECTION_INSTRUCTIONS.get(agent.active_template, {}).get(_normalize_phase(phase_name), "")
    # Replace {source_file} placeholder in section instructions with the actual
    # target file from the prompt (e.g. refac_test.py).
    if section_instr and "{source_file}" in section_instr:
        _file_match = re.search(r"([a-zA-Z_][\w.]+\.py)", original_prompt or "")
        if _file_match:
            section_instr = section_instr.replace("{source_file}", _file_match.group(1))
    criteria_block = ""
    header = t(K.CRITERIA_HEADER, agent.lang)
    if task_node.success_criteria:
        items = "\n".join(f"- {c}" for c in task_node.success_criteria)
        criteria_block = f"\n\n## {header}\n{items}\n"
    elif section_instr and agent.active_template:
        lines = [l.strip() for l in section_instr.split("\n") if l.strip() and not l.startswith("Afslut")]
        criteria_text = "\n".join(f"- {l}" for l in lines[:6])
        if len(lines) > 6:
            criteria_text += "\n- ..."
        criteria_block = f"\n\n## {header}\n{criteria_text}\n"

    # Include results from previous sibling phases
    sibling_block = ""
    if task_node.parent and hasattr(task_node.parent, 'children'):
        siblings = task_node.parent.children
        my_idx = -1
        for i, sib in enumerate(siblings):
            if sib == task_node:
                my_idx = i
                break
        if my_idx > 0:
            prev_results = []
            for sib in siblings[:my_idx]:
                if sib.status == "done" and sib.result and len(sib.result.strip()) > 50:
                    prev_results.append(f"### {sib.name}\n{sib.result.strip()}")
            if prev_results:
                sibling_block = "\n\n## Resultater fra tidligere faser\n" + "\n\n".join(prev_results)

    plan_block = ""

    # For refactor template's Plan phase, auto-load refactor_analyse.md
    # so the LLM doesn't waste iterations re-reading symbol/functions.
    if (agent.active_template == "refactor" and
        task_node.name.lower() == "plan" and
        not any("refactor_analyse" in str(c) for c in agent.file_chunks.values())):
        _analyse_path = "refactor_analyse.md"
        _wd = os.environ.get('AGENT_WORKDIR', '')
        if _wd:
            _analyse_path = os.path.join(_wd, _analyse_path)
        if os.path.exists(_analyse_path):
            try:
                with open(_analyse_path, encoding="utf-8") as _af:
                    _analyse_content = _af.read()
                plan_block = "\n\n📄 **Analyse fra forrige fase (auto-indlæst):**\n```\n" + _analyse_content[:3000] + "\n```\n\nBrug denne analyse som grundlag — du behøver IKKE læse symboler eller funktioner igen."
                agent._log("DEBUG", f"Auto-loaded {_analyse_path} ({len(_analyse_content)} chars) for Plan", "")
            except Exception as _e:
                agent._log("DEBUG", f"Failed to auto-load analyse: {_e}", "")

    # For refactor template's Ekstraher and Opdatér phases, auto-load
    # refactor_plan.md + symbol-status so the LLM knows EXACTLY which
    # symbols need extraction/cleanup — no list_symbols needed.
    if not plan_block and (agent.active_template == "refactor" and
                           task_node.name.lower() in ("ekstraher", "opdatér") and
                           not any("plan.md" in str(c) for c in agent.file_chunks.values())):
        plan_path = getattr(agent, '_refactor_plan_path', '') or "refactor_plan.md"
        if os.path.exists(plan_path):
            try:
                with open(plan_path, encoding="utf-8") as _pf:
                    _plan_content = _pf.read()
                plan_block = "\n\n" + t(K.REFACTOR_PLAN_LOADED, agent.lang).format(
                    plan_content=_plan_content[:3000]
                )
                plan_block += _build_refactor_phase_context(agent)
                agent._log("DEBUG", f"Auto-loaded {plan_path} ({len(_plan_content)} chars) + symbol status for {task_node.name}", "")
            except Exception as _e:
                agent._log("DEBUG", f"Failed to auto-load refactor context: {_e}", "")

    # For refactor Ekstraher phase: auto-suggest module groups from dependency graph.
    # Only when the plan doesn't already have detailed per-module symbol lists.
    _group_block = ""
    if agent.active_template == "refactor" and task_node.name.lower() == "ekstraher":
        _plan_path = getattr(agent, '_refactor_plan_path', '') or "refactor_plan.md"
        _plan_has_details = False
        if os.path.exists(_plan_path):
            from symbol_checks import _parse_plan_symbol_mapping as _spm
            try:
                with open(_plan_path, encoding="utf-8") as _pf:
                    _plan_has_details = bool(_spm(_pf.read()))
            except Exception:
                pass
        if not _plan_has_details:
            try:
                from refactoring_engine import RefactoringEngine
                # Determine source file from prompt
                _src_match = re.search(r"([a-zA-Z_][\w.]+\.py)", original_prompt or "")
                _source_file = _src_match.group(1) if _src_match else "api_server.py"
                agent._source_file = _source_file
                _engine = RefactoringEngine()
                _gr = _engine.suggest_module_groups(source=_source_file, max_group_size=8)
                if _gr.get("success") and _gr.get("groups"):
                    # Filter groups to only include symbols that actually exist in the file
                    # (suggest_module_groups may include already-extracted symbols from imports)
                    import agent_files as _af
                    _existing = set()
                    _ls = _af.list_symbols(filepath=_source_file)
                    if _ls.get("success"):
                        _existing = {s["name"] for s in _ls.get("symbols", [])}
                    if _existing:
                        for _g in _gr["groups"]:
                            _g["symbols"] = [s for s in _g.get("symbols", []) if s in _existing]
                        _gr["groups"] = [_g for _g in _gr["groups"] if _g.get("symbols")]

                    _lines = ["\n## Foresl\u00e5ede modulopdelinger (fra afh\u00e6ngighedsgraf)"]
                    for i, g in enumerate(_gr["groups"], 1):
                        _syms = g.get("symbols", [])
                        if isinstance(_syms, (list, tuple)):
                            _sym_list = ", ".join(str(s) for s in _syms[:12])
                            if len(_syms) > 12:
                                _sym_list += f" ... (+{len(_syms)-12})"
                            _lines.append(f"\n  Gruppe {i} ({len(_syms)} symboler): {_sym_list}")
                    _group_block = "\n".join(_lines)
                    agent._log("DEBUG", f"Auto-generated {len(_gr['groups'])} module groups for {_source_file}", "")
            except Exception as _e:
                agent._log("DEBUG", f"Could not suggest module groups: {_e}", "")

    # For refactor phases: inject full list_symbols output so model never needs to call it
    _symbols_block = ""
    if agent.active_template == "refactor" and task_node.name.lower() in ("ekstraher", "opdatér"):
        try:
            import agent_files as _af
            _src_match = re.search(r"([a-zA-Z_][\w.]+\.py)", original_prompt or "")
            _source_file = _src_match.group(1) if _src_match else "api_server.py"
            agent._source_file = _source_file
            _ls = _af.list_symbols(filepath=_source_file)
            if _ls.get("success") and _ls.get("symbols"):
                _lines = [f"\n## Symboler i {_source_file} (auto-loaded)"]
                for _sym in _ls["symbols"]:
                    _name = _sym.get("name", "?")
                    _type = _sym.get("type", "?")
                    _sig = _sym.get("signature", "")
                    _line = _sym.get("line", "")
                    if _sig:
                        _lines.append(f"  {_type} {_sig}")
                    elif _line:
                        _lines.append(f"  {_type} {_name} (linje {_line})")
                    else:
                        _lines.append(f"  {_type} {_name}")
                    for _m in (_sym.get("methods") or []):
                        _m_sig = _m.get("signature", "")
                        if _m_sig:
                            _lines.append(f"    {_m_sig}")
                        else:
                            _lines.append(f"    def {_m.get('name','')} (linje {_m.get('line','')})")
                _symbols_block = "\n".join(_lines)
                agent._log("DEBUG", f"Auto-loaded {len(_ls['symbols'])} symbols from {_source_file} into prompt", "")
        except Exception as _e:
            agent._log("DEBUG", f"Could not inject symbols block: {_e}", "")

    # For programming template's later phases, auto-load docs from earlier phases.
    PROGRAMMING_DOCS = [
        ("docs/kravanalyse.md", "Kravanalyse"),
        ("docs/arkitektur.md", "Arkitekturdesign"),
        ("docs/implementeringsplan.md", "Implementeringsplan"),
        ("docs/sikkerhedsanalyse.md", "Sikkerhedsanalyse"),
    ]
    PHASE_ORDER = ["kravanalyse", "arkitekturdesign", "implementeringsplan", "sikkerhedsanalyse", "kodeimplementering"]
    if agent.active_template == "programmering" and not plan_block:
        workdir = os.environ.get('AGENT_WORKDIR') or os.getcwd()
        current_idx = -1
        task_lower = task_node.name.lower() if task_node.name else ""
        for i, p in enumerate(PHASE_ORDER):
            if p in task_lower:
                current_idx = i
                break
        if current_idx > 0:
            loaded_blocks = []
            for doc_path, doc_phase in PROGRAMMING_DOCS[:current_idx]:
                full_path = os.path.join(workdir, doc_path)
                if os.path.exists(full_path):
                    try:
                        with open(full_path, encoding="utf-8") as _df:
                            _content = _df.read()
                        loaded_blocks.append(
                            f"### {doc_phase} — {doc_path}\n\n{_content[:4000]}"
                        )
                    except Exception as _e:
                        agent._log("DEBUG", f"Failed to load {doc_path}: {_e}", "")
            if loaded_blocks:
                plan_block = "\n\n## Dokumenter fra tidligere faser (ALLEREDE INDLÆST — behøver IKKE read_chunk)\n" + "\n\n".join(loaded_blocks)
                agent._log("DEBUG", f"Auto-loaded {len(loaded_blocks)} previous phase docs for {task_node.name}", "")

    # Phase anchor: tell the LLM which phase it's currently in and forbid
    # cross-phase reasoning (the LLM otherwise tries to re-do Plan/Extract/etc.
    # because it sees the workflow description in the section instruction).
    phase_block = "\n\n" + t(K.PHASE_CURRENT, agent.lang).format(phase_name=task_node.name) + \
                  t(K.PHASE_ONLY, agent.lang).format(phase_name=task_node.name)

    # Build reason block — context about WHY this phase exists
    reason_block = _build_phase_reason(getattr(agent, 'active_template', ''), task_node.name, original_prompt)
    if not reason_block:
        reason_block = ""

    # For refactor Ekstraher: trust instruction — LLM already has plan + symbols
    # Triggers when EITHER refactor_plan.md is loaded (plan_block) OR
    # auto-generated module groups are present (_group_block).
    _trust_block = ""
    if (agent.active_template == "refactor"
        and task_node.name.lower() == "ekstraher"
        and (plan_block or _group_block)
        and _symbols_block):
        _trust_block = (
            "\n\n\U0001f512 DU HAR ALLEREDE ALLE DATA I PROMPTEN OVENFOR."
            "\nSymbol-listen OG modulopdelingerne er allerede indl\u00e6st."
            "\nDu beh\u00f8ver IKKE at kalde list_symbols \u2014 du har ALLE symboler i prompten."
            "\nG\u00e5 DIREKTE til batch_extract_symbols med symbol-grupperne nedenfor."
            "\nKald IKKE list_symbols f\u00f8r du har pr\u00f8vet batch_extract_symbols."
        )

    # Når trust-block er aktiv: fjern list_symbols fra active tools så LLM'en
    # slet ikke kan kalde det — den er nødt til at bruge batch_extract_symbols.
    if _trust_block:
        _active = getattr(agent, 'tool_registry', None)
        if _active and _active.active_tools:
            _active.active_tools = [t for t in _active.active_tools if t != "list_symbols"]
            agent._log("DEBUG", "Fjernede list_symbols fra active tools (trust-block aktiv)", "")

    if section_instr:
        task_prompt = f"{reason_block}{section_instr}{criteria_block}{sibling_block}{plan_block}{_group_block}{_symbols_block}{_trust_block}{phase_block}\n\nKontekst / Context: {clean_prompt}{chunk_hint}"
    else:
        task_prompt = f"{reason_block}{task_node.name}{criteria_block}{sibling_block}{plan_block}{_group_block}{_symbols_block}{_trust_block}{phase_block}\n\nKontekst / Context: {clean_prompt}{chunk_hint}"

    # Append phase todos as a numbered checklist — LLM must follow the order
    todos = getattr(agent, '_phase_todos', None)
    if todos:
        todo_lines = []
        for i, todo in enumerate(todos, 1):
            status = " [✓]" if todo.get("done") else ""
            todo_lines.append(f"  {i}. {todo.get('text', '')}{status}")
        if todo_lines:
            task_prompt += f"\n\n## {t(K.TODO_AGENT_HEADER, agent.lang)}\n" + "\n".join(todo_lines) + \
                           f"\n\n{t(K.TODO_ORDER_INSTRUCTION, agent.lang)}"

    # Append LLM's own todos (personal checklist)
    llm_todos = getattr(agent, '_llm_todos', None)
    if llm_todos:
        llm_todo_lines = []
        for todo in llm_todos:
            status = " [✓]" if todo.get("done") else ""
            tid = todo.get("id", "")
            llm_todo_lines.append(f"  [{status}] `{tid}` {todo.get('text', '')}")
        if llm_todo_lines:
            task_prompt += f"\n\n## {t(K.TODO_LLM_HEADER, agent.lang)}\n" + "\n".join(llm_todo_lines) + \
                           f"\n\nBrug **update_todo(todo_id='lt_xxx', done=true)** for at markere fremdrift."

    agent._refresh_skills()
    agent._match_skills(clean_prompt)
    skills_block = agent._format_skills_for_prompt()
    if skills_block:
        task_prompt = skills_block + task_prompt
        agent._log("SKILL", "Skills injectet i prompt", skills_block[:200])

    system_prompt = agent.tool_registry.build_system_prompt(task_prompt)
    agent._log("DEBUG", f"file_chunks keys: {list(agent.file_chunks.keys())}", "")
    agent._log("DEBUG", f"clean_prompt length: {len(clean_prompt)}", f"starts with: {clean_prompt[:100]}")
    agent._log("DEBUG", f"system_prompt length: {len(system_prompt)}", f"contains file content: {'###' in system_prompt}")

    # Build user guidance — the "call plan_phase" instruction goes FIRST
    tools_list = ', '.join([k for k in agent.tool_registry.tools if agent.tool_registry.active_tools is None or k in agent.tool_registry.active_tools])
    lang_instr = t(K.ANSWER_IN, agent.lang)
    user_guidance = f"{lang_instr}. "
    # Prominent instruction to build a plan (BEFORE tool guidance)
    if "plan_phase" in getattr(agent.tool_registry, 'active_tools', []) or getattr(agent.tool_registry, 'active_tools', None) is None:
        user_guidance += t(K.TODO_PLAN_START, agent.lang) + " "
    if chunk_hint:
        user_guidance += chunk_hint.strip() + " "
    if tools_list:
        if _use_native_tools(agent):
            user_guidance += t(K.TOOL_CONTINUATION_NATIVE, agent.lang).format(tools_list=tools_list)
        else:
            user_guidance += t(K.TOOL_CONTINUATION, agent.lang).format(tools_list=tools_list, TOOL_MARKER=agent.tool_registry.TOOL_MARKER, DONE_MARKER=agent.tool_registry.DONE_MARKER)
    else:
        user_guidance += t(K.DONE_CONTINUATION, agent.lang).format(DONE_MARKER=agent.tool_registry.DONE_MARKER)
    if not chunk_hint and tools_list:
        has_any_write = any(t in ('write_file', 'edit_file', 'delete_file', 'extract_symbol', 'remove_symbol', 'add_import', 'add_method', 'add_function') for t in agent.tool_registry.active_tools or [])
        if not has_any_write and not agent.images and not agent.file_chunks:
            user_guidance += "\n\nOBS: Ingen filer er indl\u00e6st. Du KAN svare direkte uden at kalde v\u00e6rkt\u00f8jer f\u00f8rst. Sp\u00f8rg IKKE efter filnavne \u2014 brug din egen viden til at besvare opgaven."
    WRITE_TOOLS = {'write_file', 'edit_file', 'delete_file', 'add_method', 'add_function', 'extract_symbol', 'remove_symbol', 'add_import'}
    has_write = any(t in WRITE_TOOLS for t in (agent.tool_registry.active_tools or []))
    if has_write:
        user_guidance += t(K.WRITE_REQUIRED, agent.lang)
        active_write = [t for t in WRITE_TOOLS if t in (agent.tool_registry.active_tools or [])]
        user_guidance += f" Tilg\u00e6ngelige skrivev\u00e6rkt\u00f8jer: {', '.join(active_write)}."
    wta_tip = agent._seq.generate_tool_tip(agent.active_template or "fri", task_node.name) if hasattr(agent, '_seq') else ""
    if wta_tip:
        user_guidance += "\n\n" + wta_tip

    # Tool-specific hints — filtered by active tools to avoid confusing the LLM
    active_tool_set = set(agent.tool_registry.active_tools or [])
    tool_hints = {
        "list_symbols": "\n  Brug list_symbols(filepath='fil.py') for at se ALLE symboler i en Python-fil — gør det FØR locate/read_location når du ikke kender symbolnavnene.",
        "read_chunk": "\n  Read_chunk må KUN bruges til IKKE-PYTHON filer (JSON, HTML, TXT, osv.). For .py-filer, brug read_location i stedet.",
        "locate": "\n  Brug locate(name='funktionsnavn') for at finde en PYTHON funktion/klasse/variabel på tværs af ALLE .py-filer. name er et Python symbol (def/class/variable), IKKE et værktøjsnavn (tool). locate returnerer også 'also_in_file'.",
        "read_location": "\n  Brug read_location(filepath='fil.py', name='funktionsnavn') for at læse KUN en bestemt funktion/metode/klasse — IKKE hele filen.",
        "write_file": "\n  Brug write_file(path='ny_fil.py', content='...') for at oprette NYE filer der IKKE findes i forvejen. Brug ALDRIG write_file til at erstatte eksisterende filer — brug edit_file i stedet.",
        "edit_file": "\n  Brug edit_file(path='fil.py', old_text='tekst der skal erstattes', new_text='ny tekst') for at redigere EKSISTERENDE filer. Læs filen FØRST med read_chunk, kopier den præcise tekst som old_text. For at TILFØJE en linje: sæt old_text = hele filens indhold, og new_text = det gamle indhold + den nye linje. ERSTAT ALDRIG hele indholdet med kun den nye tekst.",
        "add_method": "\n  Brug add_method(filepath='fil.py', class_name='MinKlasse', method_code='def ny_metode(self):\\n    pass') for at TILFØJE en ny metode til en eksisterende klasse. Du skal KUN angive den nye metodekode — IKKE hele klassen. Dette undgår escaping-problemer med edit_file.",
        "add_function": "\n  Brug add_function(filepath='fil.py', function_code='def ny_funktion():\\n    pass') for at TILFØJE en ny module-level funktion. Valgfrit: after_symbol='anden_funk' indsætter efter givet symbol.",
        "delete_file": "\n  Brug delete_file(filepath='overflødig_fil.py') for at SLETTE en hel fil der ikke længere er nødvendig. Bekræft ALTID at filen ikke bruges af anden kode før sletning.",
        "run_tests": "\n  Brug run_tests() for at køre tests og verificere at din kode virker.",
        "update_issue_status": "\n  Brug update_issue_status(issue_id='...', status='resolved') når et issue er løst.",
    }
    filtered_hints = [h for tool_name, h in tool_hints.items() if tool_name in active_tool_set]
    if filtered_hints:
        user_guidance += "\n\n## VÆRKTØJSGUIDE" + "".join(filtered_hints)

    messages = [{"role": "system", "content": system_prompt}]
    if file_ctx:
        messages.append({"role": "system", "content": f"## Filindhold (f\u00f8rste iteration)\n\n{file_ctx}"})
        # Append structured entity map if available
        _em = getattr(agent, '_entity_map', None)
        if _em:
            try:
                import agent_entity_map
                _em_text = agent_entity_map.format_entity_map_prompt(_em)
                if _em_text:
                    messages.append({"role": "system", "content": _em_text})
            except Exception:
                pass
        extraction_guidance = t(K.EXTRACT_CONTEXT_FIRST, agent.lang)
        user_guidance = extraction_guidance + "\n\n" + user_guidance
    messages.append({"role": "user", "content": user_guidance})
    agent._log("LLM", "System prompt", f"{len(system_prompt)} chars \u2014 {system_prompt[:300]}...")
    agent._log("LLM", "User guidance", user_guidance)
    return messages, tools_list, bool(file_ctx)


def _msg_content_len(m: dict) -> int:
    """msg content len.
    
    Args:
        m:"""
    c = m.get("content", "")
    if isinstance(c, str):
        return len(c)
    if isinstance(c, list):
        return sum(p.get("text", "").__len__() if isinstance(p, dict) and p.get("type") == "text" else 0 for p in c)
    return 0


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


def _truncate_messages(messages: list[dict], max_chars: int, agent: Any | None = None) -> list[dict]:
    """truncate messages.

    When truncating for a refactor template, saves the full conversation to a temp file
    and injects a compact progress summary so the LLM retains awareness of work done.

    Args:
        messages:
        max_chars:
        agent:"""
    total = sum(_msg_content_len(m) for m in messages)
    if total <= max_chars or len(messages) <= 3:
        return messages
    mid = "\n[... tidligere kontekst afkortet ...]"
    system = [m for m in messages if m["role"] == "system"]
    non_system = [m for m in messages if m["role"] != "system"]

    # For refactor template: save full context and build progress summary
    is_refactor = agent and getattr(agent, 'active_template', '') == 'refactor'
    if is_refactor and agent:
        _save_full_context_for_refactor(agent, messages)
        summary = _build_truncation_summary(messages, agent)
        mid += "\n\n" + summary

    keep_pairs = 6 if is_refactor else 4
    tail = non_system[-keep_pairs:] if len(non_system) > keep_pairs else non_system
    # Ensure tail doesn't start with a bare "tool" message (LM Studio template
    # error: "Message has tool role, but no preceding assistant with tool_calls")
    if tail and tail[0].get("role") == "tool":
        # Walk back to include the preceding assistant message so the pair is complete
        cut_point = len(non_system) - keep_pairs
        if cut_point > 0:
            for i in range(cut_point - 1, -1, -1):
                if non_system[i].get("role") == "assistant":
                    tail = non_system[i:]
                    break
    insert = [{"role": "user", "content": mid}]
    return system + insert + tail


def _save_full_context_for_refactor(agent: Any, messages: list[dict]) -> None:
    """Save the full conversation history to a temp file for context recovery.

    This allows the LLM (or diagnostics) to retrieve what happened in earlier iterations
    when the active message window has been truncated.
    """
    session_id = getattr(agent, '_session_id', 'unknown')
    log_dir = os.path.join(os.getcwd(), "logs", "llm_responses", str(session_id))
    try:
        os.makedirs(log_dir, exist_ok=True)
    except OSError:
        return
    path = os.path.join(log_dir, "full_context.json")
    try:
        serializable = []
        for m in messages:
            role = m["role"]
            content = m.get("content")
            tool_calls = m.get("tool_calls")
            # Skip empty assistant blocks (content=None, no tool_calls)
            if role == "assistant" and not content and not tool_calls:
                continue
            entry = {"role": role}
            if content:
                if isinstance(content, str):
                    entry["content"] = content
                elif isinstance(content, list):
                    entry["content"] = [p if not isinstance(p, dict) or p.get("type") != "image_url" else {"type": "image_url", "image_url": {"url": "[IMAGE]"}} for p in content]
            if tool_calls:
                entry["tool_calls"] = tool_calls
            serializable.append(entry)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(serializable, f, ensure_ascii=False)
    except (OSError, TypeError):
        pass


def _count_symbols_in_file(filepath: str) -> int:
    """Count top-level symbols in a Python file."""
    try:
        import agent_files as _af
        result = _af.list_symbols(filepath)
        if isinstance(result, dict) and result.get("success"):
            return result.get("count", 0)
    except Exception:
        pass
    return 0


def _get_symbol_names_in_file(filepath: str) -> set[str]:
    """Get set of top-level symbol names in a Python file."""
    try:
        import agent_files as _af
        result = _af.list_symbols(filepath)
        if isinstance(result, dict) and result.get("success"):
            return {s["name"] for s in result.get("symbols", [])}
    except Exception:
        pass
    return set()


def _detect_module_deps(module_path: str, all_modules: list[str]) -> list[str]:
    """Detect which planned modules a module depends on via imports."""
    if not os.path.exists(module_path):
        return []
    try:
        with open(module_path, 'r', encoding='utf-8') as f:
            content = f.read()
    except (OSError, IOError):
        return []
    deps = []
    for mod in all_modules:
        mod_name = os.path.splitext(os.path.basename(mod))[0]
        if mod != module_path and mod_name in content:
            deps.append(mod)
    return deps


def _resolve_source_file(agent: Any, prompt: str = "") -> str:
    """Resolve the source file being refactored from prompt or agent state.

    Priority:
    1. agent._source_file (set by _build_initial_messages)
    2. 'Location: filename.py' in prompt
    3. First 'filename.py' in prompt  
    4. Fallback to 'api_server.py'
    """
    src = getattr(agent, '_source_file', '') or ''
    if src:
        return src
    import re as _re
    m = _re.search(r'Location:\s*([a-zA-Z_][\w./-]+\.py)', prompt)
    if m:
        return m.group(1)
    m = _re.search(r'([a-zA-Z_][\w./-]+\.py)', prompt)
    if m:
        return m.group(1)
    return "api_server.py"


def _build_truncation_summary(messages: list[dict], agent: Any) -> str:
    """Build a compact summary of work done from the full message history.

    Extracts tool calls and their outcomes so the LLM knows what has been accomplished
    even after truncation removes earlier messages.
    """
    lines = []
    tools_summary: dict[str, list] = {}
    symbols_moved: list[str] = []
    modules_created: set[str] = set()

    for m in messages:
        role = m.get("role", "")
        content = m.get("content", "")
        if not isinstance(content, str):
            continue

        # Extract tool calls from assistant messages with tool_calls
        if role == "assistant" and "tool_calls" in content.lower():
            pass  # handled via tool results below

        # Extract tool results
        if role == "tool":
            try:
                parsed = json.loads(content)
                if isinstance(parsed, dict):
                    result_data = parsed.get("result", parsed)
                    if isinstance(result_data, dict):
                        inner = result_data.get("result", result_data)
                        # batch_extract_symbols results
                        if isinstance(inner, dict) and "total" in inner:
                            target = inner.get("target", "?")
                            succeeded = inner.get("succeeded", 0)
                            symbols_in_batch = []
                            for r in inner.get("results", []):
                                sym = r.get("symbol", "")
                                if sym:
                                    symbols_moved.append(sym)
                                    symbols_in_batch.append(sym)
                            modules_created.add(os.path.basename(target))
                            tools_summary.setdefault("batch_extract_symbols", []).append(
                                f"✅ {succeeded} symbols → {os.path.basename(target)}"
                            )
                        # extract_symbol results
                        elif isinstance(inner, dict) and "symbol" in inner:
                            sym = inner.get("symbol", "")
                            target = inner.get("target", "?")
                            if sym and inner.get("success"):
                                symbols_moved.append(sym)
                                modules_created.add(os.path.basename(target))
            except (json.JSONDecodeError, TypeError, AttributeError):
                pass

    # Count remaining symbols in source file
    remaining_count = ""
    try:
        import agent_files as _af
        _src = _resolve_source_file(agent, getattr(agent, 'original_prompt', ''))
        result = _af.list_symbols(_src)
        if isinstance(result, dict) and result.get("success"):
            symbols = result.get("symbols", [])
            count = len(symbols) if isinstance(symbols, list) else 0
            remaining_count = f"{_src}: {count} symbols tilbage"
    except Exception:
        pass

    # Build summary lines — always include remaining count
    if symbols_moved:
        unique_symbols = list(dict.fromkeys(symbols_moved))
        lines.append(f"Fremgang: {len(unique_symbols)} symboler flyttet til {len(modules_created)} modul(er): {', '.join(sorted(modules_created))}")
    if remaining_count:
        lines.append(remaining_count)

    if tools_summary:
        for tool, entries in tools_summary.items():
            recent = entries[-3:]  # last 3 batches
            lines.append(f"Seneste {tool} kald: {' | '.join(recent)}")

    return "\n".join(lines) if lines else ""


def _cont_hint(agent: Any, tools_list: str) -> str:
    """cont hint.
    
    Args:
        agent:
        tools_list:"""
    if _use_native_tools(agent):
        return t(K.TOOL_CONTINUATION_NATIVE, agent.lang).format(tools_list=tools_list)
    return t(K.TOOL_CONTINUATION, agent.lang).format(tools_list=tools_list, TOOL_MARKER=agent.tool_registry.TOOL_MARKER, DONE_MARKER=agent.tool_registry.DONE_MARKER)


def _add_user_msg(messages: list[dict], content: str) -> None:
    """add user msg.
    
    Args:
        messages:
        content:"""
    messages.append({"role": "user", "content": content})


def _handle_tool_call(agent: Any, parsed: dict, messages: list[dict], called_tools: dict[str, int], tools_list: str, task_node: Any, original_prompt: str) -> dict | None:
    """handle tool call.
    
    Args:
        agent:
        parsed:
        messages:
        called_tools:
        tools_list:
        task_node:
        original_prompt:"""
    tool_key = parsed['tool'] + str(parsed.get('args', {}))
    dup_count = called_tools.get(tool_key, 0)
    called_tools[tool_key] = dup_count + 1
    if dup_count >= 1:
        _add_user_msg(messages, f"{t(K.SYS_ERROR_PREFIX, agent.lang)}: {t(K.SYS_DUP_RESULT, agent.lang)}")
        return None
    if parsed["tool"] in ("write_file", "edit_file") and getattr(agent, 'issue_resolved', False) and getattr(agent, 'active_template', '') != 'refactor':
        _add_user_msg(messages, f"{t(K.SYS_ERROR_PREFIX, agent.lang)}: {t(K.SYS_ISSUE_RESOLVED, agent.lang)}")
        return None

    # In test phases, force write_file as the first tool call — block reads before write
    if "test" in _normalize_phase(task_node.name):
        write_file_called = any(k.startswith("write_file{") for k in called_tools if k.split("{")[0] != parsed["tool"])
        if not write_file_called and parsed["tool"] != "write_file":
            _add_user_msg(messages, f"{t(K.SYS_ERROR_PREFIX, agent.lang)}: {t(K.WRITE_FILE_FIRST, agent.lang)}")
            return None

    agent._log("TOOL", t(K.LOG_TOOL_CALLING, agent.lang).format(tool=parsed['tool']), str(parsed.get("args", {})))
    # Cache list_symbols results per file
    if parsed["tool"] == "list_symbols" and isinstance(parsed.get("args"), dict):
        _ls_file = parsed["args"].get("filepath", "")
        if _ls_file and _ls_file in getattr(agent, '_list_symbols_cache', {}):
            _cached = agent._list_symbols_cache[_ls_file]
            agent._log("TOOL", t(K.LOG_TOOL_RESULT, agent.lang).format(tool=parsed['tool']), f"(cached) cached result")
            return {"tool": parsed["tool"], "args": parsed.get("args", {}), "result": _cached, **({"checkpoint_msg": ""} if False else {})}
    result = agent.tool_registry.execute(parsed["tool"], parsed["args"])
    if parsed["tool"] == "list_symbols" and isinstance(result, dict) and result.get("success"):
        _f = (parsed.get("args") or {}).get("filepath", "")
        if _f:
            agent._list_symbols_cache[_f] = result
    if parsed["tool"] in ("extract_symbol", "batch_extract_symbols") and isinstance(parsed.get("args"), dict):
        _src = parsed["args"].get("source", "")
        if _src in getattr(agent, '_list_symbols_cache', {}):
            del agent._list_symbols_cache[_src]
    result_str = json.dumps(result, ensure_ascii=False)
    agent._log("TOOL", t(K.LOG_TOOL_RESULT, agent.lang).format(tool=parsed['tool']), result_str)
    agent._record_tool_call(
        phase=getattr(task_node, 'name', '?'),
        tool=parsed['tool'],
        args=parsed.get('args', {}),
        success=result.get('success', False) if isinstance(result, dict) else True,
        error=result.get('error', '') if isinstance(result, dict) else '',
    )

    if parsed["tool"] in ("write_file", "edit_file", "extract_symbol"):
        if parsed["tool"] == "extract_symbol":
            if isinstance(result, dict) and not result.get("success"):
                called_tools.pop(parsed['tool'] + str(parsed.get('args', {})), None)
            else:
                agent._current_task_iteration = 0
                agent._non_productive_reminder_sent = False
                # Inject compact progress for extract_symbol
                inner = result.get("result", {})
                if isinstance(inner, dict) and inner.get("success"):
                    sym = inner.get("symbol", "?")
                    tgt = os.path.basename(inner.get("target", "?"))
                    _add_user_msg(messages, f"[SYSTEM: ✅ {sym} flyttet til {tgt}]")
        else:
            agent._current_task_iteration = 0
            agent._non_productive_reminder_sent = False
            if isinstance(result, dict) and result.get("success") is False:
                agent._write_failed = True
            unread_hints = agent._hints_available - agent._hints_requested
            if unread_hints:
                ids = ", ".join(sorted(unread_hints)[:3])
                _add_user_msg(messages, f"\u26a0\ufe0f {t(K.ANTI_LEAKAGE_WARNING, agent.lang)} ({ids})")
    if parsed["tool"] == "run_tests":
        inner = result.get("result", {}) if isinstance(result, dict) else {}
        if isinstance(inner, dict) and inner.get("success") is False:
            agent._tests_failed = True
        else:
            agent._tests_failed = False
        test_summary = _parse_test_summary(inner)
        exit_code = inner.get("exit_code")
        if hasattr(agent, '_core'):
            agent._core.record_test_outcome(
                test_file=parsed.get("args", {}).get("test_path", "") or "run_tests",
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

    if parsed["tool"] == "locate":
        if isinstance(result, dict) and result.get("success"):
            agent._located_files.add(os.path.abspath(result.get("file", "")))

    if parsed["tool"] == "read_chunk":
        file_key = parsed.get("args", {}).get("file_key", "")
        if file_key.startswith("file_"):
            file_path = os.path.abspath(file_key[5:])
            if file_path in agent._located_files:
                result_str += "\n\n📌 OBS: Du har allerede læst funktion(er) i denne fil med locate. Brug locate(filepath='...', name='andet_navn') i stedet for read_chunk — det er hurtigere."

    if parsed["tool"] == "read_issue":
        if isinstance(result, dict) and result.get("success"):
            issue_data = result.get("issue", {})
            iid = issue_data.get("id", "")
            if iid:
                if issue_data.get("_hints_available"):
                    agent._hints_available.add(iid)
                if issue_data.get("_hints_read"):
                    agent._hints_requested.add(iid)

    # Inject compact progress summary after batch_extract_symbols
    if parsed["tool"] == "batch_extract_symbols" and result.get("success"):
        inner = result.get("result", {})
        if isinstance(inner, dict) and inner.get("succeeded", 0):
            target = os.path.basename(inner.get("target", "?"))
            succeeded = inner.get("succeeded", 0)
            symbols_in_batch = [r.get("symbol", "") for r in inner.get("results", []) if r.get("success")]
            progress_msg = f"[SYSTEM: ✅ {succeeded} symboler flyttet til {target}: {', '.join(symbols_in_batch)}]"
            _add_user_msg(messages, progress_msg)

            # Check for missing dependencies after batch_extract_symbols
            missing_deps = inner.get("missing_dependencies", []) if isinstance(inner, dict) else []
            if missing_deps:
                deps_str = ', '.join(missing_deps)
                source_name = os.path.basename(inner.get("source", "?"))
                warning = (
                    f"[SYSTEM: ⚠️ ADVARSEL — {target} refererer symboler der kun "
                    f"findes i {source_name}: {deps_str}. Ekstraher disse symboler "
                    f"først eller tilføj imports for at undgå NameError.]"
                )
                _add_user_msg(messages, warning)

    checkpoint_msg = agent_git.verify_pr_step(agent, parsed["tool"], result, task_node.name, original_prompt)
    if checkpoint_msg:
        _add_user_msg(messages, f"!!! CHECKPOINT - {checkpoint_msg}")
        agent._log("INFO", "CHECKPOINT", checkpoint_msg)
        return {"type": "checkpoint", "tool": parsed["tool"], "args": parsed.get("args", {}), "result": result, "checkpoint_msg": checkpoint_msg}
    else:
        agent._checkpoint_tools.add(parsed["tool"] + str(parsed.get("args", {})))
        msg = f"{t(K.TOOL_RESULT_PREFIX, agent.lang).format(tool=parsed['tool'])}\n{result_str}\n\n{_cont_hint(agent, tools_list)}"
        if agent._write_failed:
            msg += f"\n\n⚠️ {t(K.SYS_ERROR_PREFIX, agent.lang)}: edit_file mislykkedes. Læs filen igen og kopier teksten nøjagtigt som old_text."
        _add_user_msg(messages, msg)
        return {"type": "tool_result", "tool": parsed["tool"], "args": parsed.get("args", {}), "result": result}


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


REQUIRED_ACTION_TOOLS = {"edit_file", "write_file", "delete_file", "extract_symbol", "remove_symbol", "add_import", "add_method", "add_function", "update_issue_status"}


CLOSE_PHASE_ALIASES = {"opdatering", "opdatér", "luk", "close"}


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


def _check_required_tools(agent: Any, called_tools: dict, task_name: str = "") -> str | None:
    """check required tools.

    Args:
        agent:
        called_tools:
        task_name:"""
    template = getattr(agent, "active_template", "")
    if template == "refactor" and task_name:
        refactor_writing_phases = ("plan", "ekstraher", "opdat")
        # Check BOTH current called_tools AND historical tool_log (covers retries)
        _all_writes = set()
        for k in (called_tools or {}):
            _all_writes.add(k.split("{")[0])
        for e in (getattr(agent, "_tool_log", None) or []):
            tn = e.get("tool", "")
            if tn:
                _all_writes.add(tn)
        has_written = any(t in _all_writes for t in
            ("write_file", "edit_file", "delete_file", "extract_symbol", "batch_extract_symbols", "remove_symbol", "add_import"))
        if any(k in _normalize_phase(task_name).lower() for k in refactor_writing_phases) and not has_written:
            iteration = getattr(agent, "_current_task_iteration", 0)
            if iteration >= 3 and not getattr(agent, "_non_productive_reminder_sent", False):
                agent._non_productive_reminder_sent = True
                return t(K.SYS_REQUIRED_TOOLS_REFACTOR, agent.lang).format(count=iteration)
        # For refactor Ekstraher: even when tools were called, verify actual symbol removal
        if template == "refactor" and has_written and "ekstraher" in _normalize_phase(task_name).lower():
            try:
                src_file = getattr(agent, '_source_file', '') or 'api_server.py'
                _wd_src = os.environ.get('AGENT_WORKDIR', '')
                if _wd_src and not os.path.isabs(src_file):
                    abs_src = os.path.join(_wd_src, src_file)
                else:
                    abs_src = src_file if os.path.isabs(src_file) else os.path.abspath(src_file)
                sym_result = agent_files.list_symbols(abs_src)
                if sym_result.get("success"):
                    remaining = len(sym_result.get("symbols", []))
                    if remaining >= 50:
                        return t(K.LOG_EXTRACT_INCOMPLETE, agent.lang).format(remaining=remaining)
            except Exception:
                pass
        programming_writing_phases = ("arkitekturdesign", "implementeringsplan", "kodeimplementering")
        has_written = any(k in (called_tools or {}) for k in called_tools if k.startswith("write_file") or k.startswith("edit_file"))
        if any(k in _normalize_phase(task_name).lower() for k in programming_writing_phases) and not has_written:
            iteration = getattr(agent, "_current_task_iteration", 0)
            if iteration >= 5 and not getattr(agent, "_non_productive_reminder_sent", False):
                agent._non_productive_reminder_sent = True
                return t(K.SYS_REQUIRED_TOOLS_PROGRAMMING, agent.lang).format(count=iteration)
    available = set(agent.tool_registry.active_tools or [])
    # Phase-specific required tools: only require action tools that the
    # phase's TEMPLATE_TASK_TOOLS actually lists. This prevents phases
    # from requiring tools they don't use (e.g., refactor Opdatér should
    # not require extract_symbol or write_file if remove_symbol+add_import suffice).
    phase_tools = _get_phase_task_tools(agent, task_name)
    if phase_tools is not None:
        required = phase_tools
    else:
        required = available & REQUIRED_ACTION_TOOLS
    if not required:
        return None
    phase = _normalize_phase(task_name).lower() if task_name else ""
    if "update_issue_status" in required:
        if phase not in CLOSE_PHASE_ALIASES:
            required.discard("update_issue_status")
    # In verification phases, edit_file/write_file are optional — they're only
    # allowed for quick test-fix cycles, not required.
    if phase in ("verifikation", "green"):
        required -= {"edit_file", "write_file"}
    if not required:
        return None
    called_names = set()
    for k in called_tools:
        name = k.split("{")[0]
        called_names.add(name)
    # If update_issue_status was called, the phase is complete regardless
    # of which write tools were or weren't called — return None immediately.
    if "update_issue_status" in called_names:
        return None
    # If the run_tests auto-complete already marked the issue resolved
    # (Test phase with passing tests), edit_file/write_file are no longer needed.
    if getattr(agent, "issue_resolved", False) and getattr(agent, 'active_template', '') != 'refactor':
        required -= {"edit_file", "write_file"}
    # For refactor Test phase: if run_tests was called and passed, no editing needed
    if template == "refactor" and "run_tests" in called_names and not getattr(agent, '_tests_failed', False):
        required -= {"edit_file", "verify_refactor"}
    uncalled = required - called_names
    import logging as _llog
    _llog.getLogger(__name__).debug(
        f"_check_req: template={template} phase={_normalize_phase(task_name).lower() if task_name else '?'} "
        f"called_names={sorted(called_names)} required={sorted(required)} uncalled={sorted(uncalled)} "
        f"issue_resolved={getattr(agent,'issue_resolved',False)} tests_failed={getattr(agent,'_tests_failed',None)}"
    )
    # write_file and extract_symbol are alternatives — calling either satisfies the requirement
    if "write_file" in uncalled and "extract_symbol" in called_names:
        uncalled.discard("write_file")
    if "extract_symbol" in uncalled and "write_file" in called_names:
        uncalled.discard("extract_symbol")
    # write_file and edit_file are alternatives — you either create new files or edit existing ones
    if "write_file" in uncalled and "edit_file" in called_names:
        uncalled.discard("write_file")
    if "edit_file" in uncalled and "write_file" in called_names:
        uncalled.discard("edit_file")
    # For selvforbedring/ret: create_issue is an acceptable alternative to edit_file
    # — the LLM documents the needed fix instead of applying it directly.
    if template == "selvforbedring" and "ret" in phase:
        if "edit_file" in uncalled and "create_issue" in called_names:
            uncalled.discard("edit_file")
    # remove_symbol + add_import together are the AST-based refactoring
    # approach — they achieve the same goal as edit_file/write_file/
    # delete_file/add_method/add_function.
    if "remove_symbol" in called_names and "add_import" in called_names:
        uncalled -= {"edit_file", "write_file", "delete_file", "add_method", "add_function"}
    # write_file/edit_file are supersets of add_method/add_function —
    # if the LLM wrote or edited the whole file, per-method tools aren't needed.
    if "write_file" in called_names or "edit_file" in called_names:
        uncalled -= {"add_method", "add_function"}
    # extract_symbol/batch_extract_symbols are supersets of add_method/add_function
    # — extracting a symbol to a new file achieves the same goal as adding a method.
    if "extract_symbol" in called_names or "batch_extract_symbols" in called_names:
        uncalled -= {"add_method", "add_function"}
    # write_file(overwrite="force") on an existing file is an alternative to
    # remove_symbol — the entire file was rewritten including all changes.
    if "remove_symbol" in uncalled:
        for k in called_tools:
            if k.startswith("write_file") and '"force"' in k.split("{", 1)[-1]:
                uncalled.discard("remove_symbol")
                break
    # tool_log success check: tools where ALL attempts failed due to LLM error
    # do NOT count as satisfied — only system blocks (hash, path safety) are excused.
    if agent._tool_log and not uncalled:
        for req_tool in required:
            if req_tool in called_names:
                attempts = [e for e in agent._tool_log if e.get("tool") == req_tool and e.get("success") is False]
                all_attempts = [e for e in agent._tool_log if e.get("tool") == req_tool]
                if all_attempts and len(attempts) == len(all_attempts):
                    # All attempts failed — check if system-blocked or LLM error
                    system_blocked = any(
                        "HARD BLOCK" in str(e.get("error", ""))
                        or "Adgang n\u00e6gtet" in str(e.get("error", ""))
                        for e in all_attempts
                    )
                    if system_blocked:
                        continue  # system prevented — still counts as satisfied
                    # LLM error — add back to uncalled (must try differently)
                    uncalled.add(req_tool)
    if uncalled:
        return t(K.LOG_REQUIRED_TOOLS_MISSING, agent.lang).format(tools=", ".join(sorted(uncalled)))
    return None


ISSUE_ID_PATTERN = re.compile(r'(BUG|SEC|ARC|MNT|PRF|TST|REFAC|STAB)-\d+', re.IGNORECASE)


def _extract_issue_id(text: str) -> str | None:
    """extract issue id.
    
    Args:
        text:"""
    m = ISSUE_ID_PATTERN.search(text)
    return m.group(0).upper() if m else None


AUTO_RESOLVE_PATTERNS = [
    r'allerede (?:løst|rettet|fikset|fixet)',
    r'(?:fejlen|buggen|problemet) (?:findes ikke|eksisterer ikke|er væk)',
    r'koden er allerede (?:korrekt|rettet|fikset)',
    r'allerede (?:implementeret|udført)',
    r'already (?:fixed|resolved|solved|correct)',
    r'(?:bug|issue|problem) no longer (?:exists|reproducible|applicable)',
    r'(?:no change|nothing to fix)',
    r'intet at (?:rette|fikse|gøre)',
]


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


def _set_phase_model(agent: Any, task_name: str) -> None:
    """Switch model per template/phase if configured in TEMPLATE_PHASE_MODEL_MAP.

    Read-only analysis phases use a cheap/fast model; write-heavy
    phases use a model known for reliable code generation.

    Does nothing if the current template or phase has no entry in the map.
    """
    template = getattr(agent, 'active_template', '') or ''
    if not template:
        return
    phase_lower = _normalize_phase(task_name).lower()
    phase_map = agent_skills.TEMPLATE_PHASE_MODEL_MAP.get(template, {})
    if not phase_map:
        return
    for key, model_name in phase_map.items():
        if key in phase_lower or phase_lower in key:
            if not model_name:
                return  # empty string = keep current execution model
            current = getattr(agent.llm, 'model', '')
            if current == model_name:
                return
            # Check om modellen er tilgaengelig — log advarsel men skift alligevel
            # (modellen kan vaere paa et andet URL end den nuvarende backend)
            try:
                available = agent.llm.list_models()
                if available and model_name not in available:
                    agent._log("MODEL", f"Warning: {model_name} not found in current backend — switching anyway",
                               f"available: {', '.join(available[:5])}...")
            except Exception:
                pass
            except Exception:
                pass  # hvis vi ikke kan liste modeller, forsøg skift alligevel
            agent.llm.set_model(model_name)
            agent._log("MODEL", f"Switched to {model_name} for {task_name}",
                       f"{current} \u2192 {model_name}")
            return


FRAMEWORK_PY = {"api_server.py", "agent_core.py", "agent_tasks.py", "agent_skills.py", "agent_files.py", "agent_issues.py", "agent_tree.py", "agent_git.py", "agent_phase_checks.py", "agent_wta.py", "core_analytics.py", "agent_logs.py", "tools.py", "i18n.py", "lang.py", "config.py", "task_tree.py", "llm_wrapper.py", "model_manager.py", "session_manager.py", "flow_builder.py", "skill_evolution.py", "skill_loader.py", "skill_tracker.py", "refactoring_engine.py", "github_wrapper.py"}


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
                        task_node.status = "failed"
                        full_response = t(K.LOG_EXTRACT_INCOMPLETE, agent.lang).format(
                            remaining=remaining
                        )
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


def _check_refactor_progress(agent: Any | None = None, prompt: str = "") -> str:
    """Check refactor progress from plan file OR directly from source file.

    Returns a status string like:
      'Already created (5/8): mod_a.py, mod_b.py, ...
        Remaining (3/8): mod_c.py, mod_d.py, mod_e.py'
    Falls back to counting symbols in source file when no plan exists.
    """
    import os as _os
    import re as _re
    parts = []
    plan_content = ""

    _src = _resolve_source_file(agent, prompt) if agent else "api_server.py"

    # Try plan-based progress first
    plan_path = _os.path.join(_os.environ.get('AGENT_WORKDIR', ''), 'refactor_plan.md') if _os.environ.get('AGENT_WORKDIR') else 'refactor_plan.md'
    if _os.path.exists(plan_path):
        try:
            with open(plan_path, 'r', encoding='utf-8') as f:
                plan_content = f.read()
        except (OSError, UnicodeDecodeError):
            plan_content = ""
        modules = _re.findall(r'`([a-zA-Z_][\w.]+\.py)`', plan_content)
        if modules:
            modules = sorted(set(modules))
            existing = [m for m in modules if _os.path.exists(m)]
            remaining = [m for m in modules if m not in existing]
            total = len(modules)
            if existing:
                parts.append("Allerede oprettet ({}/{}): {}".format(len(existing), total, ', '.join(existing)))
            if remaining:
                parts.append("Mangler ({}/{}): {}".format(len(remaining), total, ', '.join(remaining)))

    # Always count symbols in source file (fallback when no plan exists)
    try:
        import agent_files as _af
        result = _af.list_symbols(_src)
        if isinstance(result, dict) and result.get("success"):
            symbols = result.get("symbols", [])
            count = len(symbols) if isinstance(symbols, list) else 0
            parts.append("{}: {} symbols tilbage (mål: ≤50)".format(_src, count))
    except Exception:
        pass

    # For Opdatér phase: show symbols still in source vs already removed
    if _os.path.exists(plan_path):
        try:
            with open(plan_path, 'r', encoding='utf-8') as f:
                plan_content = f.read()
        except (OSError, UnicodeDecodeError):
            plan_content = ""
    if _os.path.exists(_src) and plan_content:
        try:
            with open(_src, 'r', encoding='utf-8') as f:
                src_content = f.read()
            src_nodes = _re.findall(r'^def (\w+)|^class (\w+)', src_content, _re.MULTILINE)
            src_symbols = sorted(set(n[0] or n[1] for n in src_nodes))
            plan_symbols = _re.findall(r'`(\w+)`[^`]*flyttes|`(\w+)`[^`]*rykkes|symbol_name=\'(\w+)\'', plan_content)
            planned = sorted(set(s for t in plan_symbols for s in t if s))
            if planned and src_symbols:
                still_in_src = [s for s in planned if s in src_symbols]
                already_removed = [s for s in planned if s not in src_symbols]
                if still_in_src:
                    parts.append("Skal fjernes fra {} ({}): {}".format(_src, len(still_in_src), ', '.join(still_in_src)))
                if already_removed:
                    parts.append("Allerede fjernet fra {} ({}): {}".format(_src, len(already_removed), ', '.join(already_removed)))
        except (OSError, UnicodeDecodeError):
            pass

    return '\n'.join(parts)


def _all_planned_modules_exist(args: dict) -> bool:
    """Check if ALL modules listed in refactor_plan.md exist on disk.

    Used as arg_check for batch_extract_symbols → rf_e1 mapping so
    'Følg refactor_plan.md nøjagtigt — opfyld ALLE moduler deri'
    is only marked done when every planned module file exists.
    """
    import os as _os
    _wd = _os.environ.get('AGENT_WORKDIR', '') or _os.getcwd()
    plan_path = _os.path.join(_wd, "refactor_plan.md")
    if not _os.path.exists(plan_path):
        return False
    try:
        from file_checks import _parse_refactor_plan_modules
        mods = _parse_refactor_plan_modules(plan_path)
        return bool(mods) and all(_os.path.exists(m) for m in mods)
    except Exception:
        return False


# Tool-to-todo mapping: (tool_name, arg_check_func_or_none) -> todo_id
_TODO_TOOL_MAP: list[tuple[str, Any | None, str]] = [
    ("read_issue", None, "bf_a1"),
    ("locate", None, "bf_a2"),
    ("list_symbols", None, "rf_a1"),
    ("read_location", None, "bf_a3"),
    ("analyze_dependencies", None, "rf_a3"),
    ("write_file", lambda a: "refactor_plan" in str(a.get("path", "")), "rf_p2"),
    ("write_file", lambda a: "refactor_analyse" in str(a.get("path", "")), "rf_a6"),
    ("write_file", lambda a: "tests/temp" in str(a.get("path", "")), "bf_t1"),
    ("write_file", lambda a: "docs/" in str(a.get("path", "")) and a.get("path","").endswith(".md"), "ka_a4"),
    ("write_file", lambda a: "docs/" in str(a.get("path", "")), "pr_a2"),
    ("extract_symbol", None, "rf_e1"),
    # rf_e1 ("Følg refactor_plan.md nøjagtigt — opfyld ALLE moduler deri")
    # ma kun markeres done naar ALLE planlagte moduler eksisterer.
    ("batch_extract_symbols", lambda a: _all_planned_modules_exist(a), "rf_e1"),
    ("batch_extract_symbols", None, "rf_e2"),
    ("batch_extract_symbols", None, "rf_e3"),
    ("extract_symbol", None, "rf_e2"),
    ("extract_symbol", None, "rf_e3"),
    ("add_method", None, "bf_i3"),
    ("add_function", None, None),
    ("verify_refactor", None, "rf_e4"),
    ("run_tests", None, "bf_t2"),
    ("run_tests", None, "rf_u_tests"),
    ("run_tests", None, "rf_t1"),
    ("run_tests", None, "sf_d1"),
    ("run_tests", None, "sf_v1"),
    ("run_tests", None, "tg_v1"),
    ("run_tests", None, "pr_i2"),
    ("run_tests", None, "ih_f2"),
    ("update_issue_status", None, "bf_o1"),
    ("update_issue_status", None, "rf_u_status"),
    ("update_issue_status", None, "rf_t2"),
    ("update_issue_status", None, "sf_v2"),
    ("update_issue_status", None, "ih_l1"),
    ("verify_refactor", None, "rf_u_verify"),
    ("list_symbols", None, "ka_a1"),
    ("edit_file", None, "ih_f1"),
    ("edit_file", None, "sf_r1"),
    ("read_issue", None, "ih_a1"),
    ("read_issue", None, "sf_a1"),
    ("git_create_branch", None, "ag_b1"),
    ("git_commit", None, "ag_c1"),
    ("git_commit", None, "sf_c1"),
    ("git_push", None, "ag_p1"),
    ("github_create_pr", None, "ag_pr1"),
]


def _match_tool_to_todos(tool_name: str, args_val: dict, agent: Any, todo_list: list[dict] | None) -> list[str]:
    """Match a tool call against todo text and return matching todo IDs."""
    # Reset cross-call flag — only set True if THIS call has a deviation
    agent._batch_had_deviation = False
    if not todo_list:
        return []
    ids = []

    # Static mapping: simple tool->todo matches
    for tname, arg_check, todo_id in _TODO_TOOL_MAP:
        if tool_name == tname and todo_id:
            if any(t.get("id") == todo_id for t in todo_list):
                if arg_check is None or arg_check(args_val):
                    ids.append(todo_id)

    # Dynamic mapping: match tool calls against todo text patterns
    for todo in todo_list:
        tid = todo.get("id", "")
        ttext = todo.get("text", "")

        if tool_name == "remove_symbol":
            sym = args_val.get("symbol_name", "")
            if sym and sym in ttext and "Fjern" in ttext:
                ids.append(tid)

        if tool_name == "add_import":
            mod = args_val.get("module", "")
            if mod and mod in ttext and "import" in ttext:
                ids.append(tid)

        if tool_name == "write_file":
            path = args_val.get("path", "")
            fname = os.path.basename(path) if path else ""
            if fname and fname in ttext and "Opret" in ttext:
                ids.append(tid)

        if tool_name in ("extract_symbol", "batch_extract_symbols"):
            target = args_val.get("target", "")
            if target and (target in ttext or os.path.basename(target) in ttext):
                # Check planned symbols vs called symbols
                _planned = getattr(agent, '_planned_symbols_per_target', {})
                if _planned and target in _planned and tool_name == "batch_extract_symbols":
                    _planned_syms = set(_planned[target])
                    _called = args_val.get("symbols", "")
                    if isinstance(_called, str):
                        _called_syms = set(s.strip() for s in _called.replace("'", "").replace('"', '').split(',') if s.strip())
                    elif isinstance(_called, (list, tuple)):
                        _called_syms = set(str(s).strip() for s in _called if str(s).strip())
                    else:
                        _called_syms = set()
                    _missing = _planned_syms - _called_syms
                    if _missing:
                        # Don't mark the todo as done — LLM hasn't covered all planned symbols
                        agent._log("ADVARSEL",
                            f"Planafvigelse for {target}: mangler {len(_missing)} plannede symboler",
                            f"Kaldte: {sorted(_called_syms)}\nMangler: {sorted(_missing)}")
                        # Store warning for handle_tool_call to inject
                        if not hasattr(agent, '_plan_warnings'):
                            agent._plan_warnings = []
                        agent._plan_warnings.append({
                            "target": target,
                            "missing": sorted(_missing),
                            "called": sorted(_called_syms),
                            "planned": sorted(_planned_syms),
                            "deviation": True,
                        })
                        # Also set persistent flag so batch_extract_symbols section can detect it
                        agent._batch_had_deviation = True
                    else:
                        ids.append(tid)
                else:
                    ids.append(tid)

        # list_symbols -> matches "List alle symboler" or "list_symbols"
        if tool_name == "list_symbols" and ("list_symbols" in ttext.lower() or "list alle symboler" in ttext.lower()):
            ids.append(tid)

        # read_location -> matches "læs" or "read_location"
        if tool_name == "read_location" and ("read_location" in ttext.lower() or "læs" in ttext.lower() or "metoder" in ttext.lower()):
            ids.append(tid)

        # run_tests -> matches "kør test" or "run_tests"
        if tool_name == "run_tests" and ("run_tests" in ttext.lower() or "test" in ttext.lower()):
            ids.append(tid)

        # analyze_dependencies -> matches "analyser afhængigheder"
        if tool_name == "analyze_dependencies" and ("afhængighed" in ttext.lower() or "dependencies" in ttext.lower()):
            ids.append(tid)

    return ids


def _auto_todo_update(tool_name: str, args_val: dict, agent: Any) -> list[str]:
    """Check if a tool call matches any active todo and returns todo_ids to mark done."""
    if not hasattr(agent, '_phase_todos'):
        return []
    ids = []
    ids = _match_tool_to_todos(tool_name, args_val, agent, getattr(agent, '_phase_todos', None))

    # Sequential order validation: only allow checkmarking the FIRST
    # unfinished todo that has a tool mapping. Soft todos (analysis/
    # planning tasks without tool matches) don't block — they're
    # implicitly completed when the next hard todo is triggered.
    # Non-blocking tools (verify_refactor, add_import) are always
    # allowed — they're incremental/verification tools where
    # out-of-order calling is harmless.
    NON_BLOCKING_TOOLS = {"verify_refactor", "add_import", "run_tests", "read_issue", "analyze_dependencies", "extract_symbol", "batch_extract_symbols", "list_symbols", "edit_file"}
    if ids:
        valid_ids = []
        for checked_id in ids:
            checked_idx = -1
            first_hard_unfinished = -1
            for i, t in enumerate(agent._phase_todos):
                if t.get("id") == checked_id:
                    checked_idx = i
                if not t.get("done") and first_hard_unfinished == -1:
                    # Check if this todo has any tool mapping
                    tid = t.get("id", "")
                    has_mapping = any(m[2] == tid for m in _TODO_TOOL_MAP)
                    # Also check dynamic patterns
                    ttext = t.get("text", "")
                    has_dynamic = any(kw in ttext for kw in ["Opret", "Fjern", "import", "extract"])
                    if has_mapping or has_dynamic:
                        first_hard_unfinished = i
            if (checked_idx == first_hard_unfinished or checked_idx <= first_hard_unfinished or
                tool_name in NON_BLOCKING_TOOLS):
                valid_ids.append(checked_id)
            else:
                checked_text = next((t.get("text","") for t in agent._phase_todos if t.get("id") == checked_id), checked_id)
                first_text = next((t.get("text","") for t in agent._phase_todos if t.get("id") == agent._phase_todos[first_hard_unfinished].get("id")), "")
                agent._log("TODO", f"Rækkefølge: '{checked_text[:60]}...' venter på '{first_text[:60]}...'", "")
        ids = valid_ids

    return ids


def _reconcile_llm_todos(agent: Any) -> list[str]:
    """Check actual disk state against LLM todos and return newly satisfiable llm todo IDs.

    For refactor Ekstraher: checks if target module files exist with content.
    """
    import re as _re
    import os as _os
    llm_todos = getattr(agent, '_llm_todos', None)
    if not llm_todos:
        return []
    ids = []
    for todo in llm_todos:
        if todo.get("done"):
            continue
        text = todo.get("text", "")
        tid = todo.get("id", "")
        if not text or not tid:
            continue

        # lt_total: mark done when all other module todos are done
        if "lt_total" in tid:
            _other_undone = [t for t in llm_todos if t.get("id") != tid and not t.get("done")]
            if not _other_undone:
                ids.append(tid)
            continue

        # Check if file mentioned in text exists
        m = _re.search(r'([a-zA-Z_][\w./-]+\.py)', text)
        if m:
            fpath = m.group(1)
            if _os.path.exists(fpath) and _os.path.getsize(fpath) > 0:
                _actual = _count_symbols_in_file(fpath)
                ids.append(tid)
                # Also update text with symbol count
                todo["text"] = f"Flyt symboler til {fpath} med batch_extract_symbols ({_actual} symbols)"
    return ids


def _reconcile_todos_with_disk(agent: Any) -> list[str]:
    """Check actual file state against todos and return newly satisfiable todo IDs.

    For each template, checks if work is already done on disk:
    - Opdatér: imports already exist in target file → checkmark import todos
    - kodeanalyse: docs/*.md already exist → checkmark write reports
    - programmering: docs/*.md already exist → checkmark write plans
    - bugfix: tests/temp/test_*.py already exists → checkmark test creation
    """
    import re as _re
    import os as _os
    todos = getattr(agent, '_phase_todos', None)
    if not todos:
        return []
    template = getattr(agent, 'active_template', '')
    phase = getattr(agent, 'current_phase', '')
    norm_phase = _normalize_phase(phase).lower()

    ids = []
    for todo in todos:
        if todo.get("done"):
            continue
        text = todo.get("text", "")
        tid = todo.get("id", "")
        if not text:
            continue

        # Check docs/*.md existence (kodeanalyse, programmering)
        if template in ("kodeanalyse", "programmering") and "skriv" in text.lower():
            m = _re.search(r'docs/[\w./-]+\.md', text)
            if m:
                doc_path = m.group(0)
                if _os.path.exists(doc_path):
                    ids.append(tid)

        # Check tests/temp/ files (bugfix, testgenerering)
        if template in ("bugfix", "testgenerering") and ("test" in text.lower() or "tests/temp" in text):
            m = _re.search(r'tests/temp/[\w./-]+\.py', text)
            if m:
                test_path = m.group(0)
                if _os.path.exists(test_path):
                    ids.append(tid)

        # Check import existence (refactor Opdatér)
        if template == "refactor" and norm_phase in ("opdater", "opdatering", "opdat\u00e9r"):
            m = _re.match(r'Tilf\u00f8j import fra ([\w./-]+\.py) i ([\w./-]+\.py)', text)
            if m:
                mod_file = m.group(1)
                target_file = m.group(2)
                target_path = target_file if _os.path.exists(target_file) else _os.path.join(agent_files._resolve_workdir(), target_file)
                if _os.path.exists(target_path):
                    try:
                        with open(target_path, 'r', encoding='utf-8', errors='replace') as f:
                            content = f.read()
                        mod_name = _os.path.splitext(mod_file)[0]
                        if f"from {mod_name}" in content or f"import {mod_name}" in content:
                            ids.append(tid)
                    except (OSError, UnicodeDecodeError):
                        pass

        # Check issue status (any template with update_issue_status todo)
        if "resolved" in text.lower() or "issue status" in text.lower():
            # Already checked by tool call — no disk check needed
            pass

    return ids


def _auto_populate_llm_todos(agent: Any, task_node: Any) -> list[dict]:
    """Auto-generate LLM-driven todos from phase context.

    For ALL templates and phases, creates a pre-populated checklist
    mirroring the Agent's auto-todos (``_phase_todos``) so the LLM
    has a ready-made plan.  The LLM can then call ``update_todo`` to
    mark progress and ``create_todo`` to add custom steps.

    For refactor-template phases that have a ``refactor_plan.md``,
    per-module tasks are created instead for finer granularity.

    Returns a list of event dicts (llm_todo_clear, llm_todo_add) that
    the caller can yield.
    """
    events: list[dict] = []
    import os as _os
    import re as _re

    template = getattr(agent, 'active_template', '') or ''
    phase = _normalize_phase(task_node.name).lower()

    # Bevar eksisterende LLM-todos hvis vi er i samme fase (retry).
    # Nulstil KUN ved fase-skift, så LLM's plan overlever iterationer.
    _prev_phase = getattr(agent, '_llm_todo_phase', '')
    if _prev_phase == phase and getattr(agent, '_llm_todos', None):
        if getattr(agent, '_llm_todos', None):
            # Retry — bevar planen, genudsender bare eksisterende todos
            agent._llm_has_planned = True
            import logging
            logging.getLogger(__name__).info("LLM-todos bevaret ved retry (%s, %d items)", _prev_phase, len(agent._llm_todos))
            for todo in agent._llm_todos:
                events.append({"type": "llm_todo_add", "id": todo.get("id",""), "text": todo.get("text",""), "parent_id": None})
            return events
        else:
            import logging
            logging.getLogger(__name__).info("LLM-todos TØMT ved retry (%s) — genopretter", _prev_phase)

    agent._llm_todos = []
    agent._llm_has_planned = False
    agent._llm_todo_phase = phase
    events.append({"type": "llm_todo_clear"})

    template = getattr(agent, 'active_template', '') or ''
    phase = _normalize_phase(task_node.name).lower()

    # ── Refactor-template: file-based todos from refactor_plan.md ──
    if template == 'refactor' and phase in ('plan', 'ekstraher', 'opdatering', 'opdatér', 'opdater'):
        plan_path = _resolve_refactor_plan_path(agent, 'refactor_plan.md')
        if not _os.path.isabs(plan_path):
            _wd = _os.environ.get('AGENT_WORKDIR', '')
            if _wd:
                plan_path = _os.path.normpath(_os.path.join(_wd, plan_path))
        try:
            from file_checks import _parse_refactor_plan_modules, _extract_modules_from_plan
            _all_mods = _parse_refactor_plan_modules(plan_path)
        except Exception:
            _all_mods = []
        if _all_mods:
            # Filter out the source file (the file being refactored)
            _src_match = _re.search(r"([a-zA-Z_][\w.]+\.py)", getattr(agent, 'original_prompt', '') or '')
            _src = _src_match.group(1) if _src_match else ''
            _tgt_mods = [m for m in _all_mods if _src not in m] if _src else _all_mods
            if not _tgt_mods:
                _tgt_mods = _all_mods
            # Hent symbol-mapping fra planen én gang per fase (ikke per modul)
            _ekstraher_sym_map = {}
            if phase == "ekstraher":
                try:
                    with open(plan_path, encoding="utf-8") as _pf:
                        import symbol_checks as _sc2
                        _ekstraher_sym_map = _sc2._parse_plan_symbol_mapping(_pf.read())
                except Exception:
                    pass
            for mod in _tgt_mods:
                _exists = _os.path.exists(mod)
                _done = _exists
                _todo_id = "lt_" + _re.sub(r'[^a-zA-Z0-9]', '', mod.replace('.py', ''))[:12]
                if phase == "ekstraher":
                    _actual = _count_symbols_in_file(mod) if _exists else 0
                    _gen_text = ""
                    _planned_syms = _ekstraher_sym_map.get(mod, [])
                    if _planned_syms:
                        _src_f = _src or "source_file"
                        # Chunk symbols into batches of max 15 symbols or ~500 chars.
                        # Each chunk contains COMPLETE symbols — never cut mid-symbol.
                        _chunks: list[list[str]] = []
                        _current: list[str] = []
                        _current_chars = 0
                        for _sym in _planned_syms:
                            _sym_len = len(_sym) + 2  # ", " separator
                            _would_exceed_count = len(_current) >= 15
                            _would_exceed_chars = _current and _current_chars + _sym_len > 500
                            if _would_exceed_count or _would_exceed_chars:
                                _chunks.append(_current)
                                _current = []
                                _current_chars = 0
                            _current.append(_sym)
                            _current_chars += _sym_len
                        if _current:
                            _chunks.append(_current)
                        _batch_parts = []
                        for _chunk in _chunks:
                            _sym_str = ", ".join(_chunk)
                            _batch_parts.append(f"batch_extract_symbols(source='{_src_f}', symbols='{_sym_str}', target='{mod}')")
                        _gen_text = " -> ".join(_batch_parts)
                        # Tjek om ALLE planlagte symboler findes i modulet
                        # (ikke bare om filen eksisterer — kan være stale fra tidl. session)
                        if _exists:
                            _actual_names = _get_symbol_names_in_file(mod)
                            _done = all(s in _actual_names for s in _planned_syms)
                    _text = f"[{_tgt_mods.index(mod)+1}/{len(_tgt_mods)}] {_gen_text or 'Flyt symboler til ' + mod + ' med batch_extract_symbols'}"
                    if _exists and _actual > 0:
                        _text += f" ({_actual} symbols)"
                elif phase == "plan":
                    _text = f"Planlæg indhold af {mod}"
                else:
                    _text = f"Opdatér referencer i {mod}"
                agent._llm_todos.append({"id": _todo_id, "text": _text, "done": _done, "parent_id": None, "phase": phase})
                events.append({"type": "llm_todo_add", "id": _todo_id, "text": _text, "parent_id": None})
            _n = len(_tgt_mods)
            _total_text = f"Verificér at alle {_n} moduler er korrekt oprettet" if _n != 1 else "Verificér at modulet er korrekt oprettet"
            _total_done = False
            if phase == "ekstraher" and _ekstraher_sym_map:
                _total_done = all(
                    _os.path.exists(m) and all(s in _get_symbol_names_in_file(m) for s in _ekstraher_sym_map.get(m, []))
                    for m in _tgt_mods
                )
            else:
                _total_done = all(_os.path.exists(m) for m in _tgt_mods)
            agent._llm_todos.append({"id": "lt_total", "text": _total_text, "done": _total_done, "parent_id": None, "phase": phase})
            events.append({"type": "llm_todo_add", "id": "lt_total", "text": _total_text, "parent_id": None})
            # Ekstraher: auto-populated todos har konkrete batch_extract_symbols
            # kald fra planen — LLM behøver IKKE kalde plan_phase/create_todo.
            if phase == "ekstraher" and _ekstraher_sym_map:
                agent._llm_has_planned = True
                # Store plan mapping so deviation detection can compare
                # batch_extract_symbols calls against planned symbols per module
                agent._planned_symbols_per_target = dict(_ekstraher_sym_map)
            return events

    # ── For other phases: leave LLM's plan empty — LLM creates its own ──
    # _llm_has_planned is left False so the budget nudge tells the LLM
    # to call plan_phase/create_todo to build its own plan.
    agent._llm_todos = []
    agent._llm_has_planned = False
    return events


def solve_task_stream(agent: Any, task_node: Any, original_prompt: str, saved_messages: list[dict] | None = None) -> Generator[dict, None, None]:
    """solve task stream.
    
    Args:
        agent:
        task_node:
        original_prompt:
        saved_messages: Optional saved conversation to resume from pause.
    
    Yields:
        ..."""
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

    # Prerequisite check: Ekstraher requires refactor_plan.md from Plan
    if agent.active_template == "refactor" and _normalize_phase(task_node.name) == "ekstraher":
        _wd_check = os.environ.get('AGENT_WORKDIR', '')
        if _wd_check:
            _plan_path = os.path.join(_wd_check, "refactor_plan.md")
        else:
            _plan_path = "refactor_plan.md"
        _check_ok = os.path.exists(_plan_path)
        if not _check_ok:
            # Fallback: check _refactor_plan_path (may point to session-scoped dir)
            _alt = getattr(agent, '_refactor_plan_path', '')
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
                if not getattr(agent, '_llm_has_planned', False):
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
                elif i >= 1:
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
                        budget_msg += "\n\n📝 **SIDSTE HANDLING: Skriv analysen til `refactor_analyse.md` med write_file()** — før du kan afslutte."
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

                # run_tests/list_symbols: skip dedup tracking
                # run_tests is a side-effect tool, list_symbols manages its own cache
                if tool_name in ("run_tests", "list_symbols"):
                    pass
                else:
                    tool_key = tool_name + str(args_val)
                    dup_count = called_tools.get(tool_key, 0)
                    called_tools[tool_key] = dup_count + 1
                if tool_name not in ("run_tests", "list_symbols") and dup_count >= 1:
                    consecutive_dedups += 1
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
                        if write_tools:
                            # Prune active_tools to WRITE_TOOLS only — LLM cannot read anymore
                            agent.tool_registry.active_tools = write_tools
                            tools_param = agent.tool_registry.get_openai_tools_for_active() if _use_native_tools(agent) else []
                            reminder = (
                                f"[SYSTEM: Du er i en l\u00f8kke med identiske resultater. "
                                f"KUN skrivev\u00e6rkt\u00f8jer er tilg\u00e6ngelige nu: "
                                f"{', '.join(write_tools)}. "
                                f"Respond ONLY with a tool call.]"
                            )
                            messages.append({"role": "system", "content": reminder})
                            agent._log("SYSTEM", "Dedup-loop escape", f"pruned to {write_tools}")
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
                        succeeded = inner.get("succeeded", 0)
                        symbols_in_batch = [r.get("symbol", "") for r in inner.get("results", []) if r.get("success")]
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
        if i >= 3 and not called_tools:
            break

    yield from _finalize_task_stream(agent, task_node, full_response, text_fallback, called_tools, _report_logs, original_prompt, messages)
