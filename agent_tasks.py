"""Agent tasks execution module."""

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
import config
from typing import Any, Generator


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
    plan_path = os.path.join(os.getcwd(), "refactor_plan.md")
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
        path = os.path.join(os.getcwd(), mod)
        if not _apc._has_real_code(path, min_lines=20):
            return False
    api_path = os.path.join(os.getcwd(), "api_server.py")
    if os.path.exists(api_path):
        try:
            with open(api_path, encoding="utf-8") as f:
                line_count = sum(1 for _ in f)
            if line_count >= 1000:
                return False
        except OSError:
            return False
    return True


def _build_refactor_phase_context(agent: Any, source_file: str = "api_server.py") -> str:
    """Build a structured symbol-status block for refactor phases.
    Reads refactor_plan.md + AST of source/target modules so the LLM
    sees EXACTLY which symbols need extraction/cleanup.
    """
    plan_path = os.path.join(os.getcwd(), "refactor_plan.md")
    if not os.path.exists(plan_path):
        return ""

    modules = agent_phase_checks._parse_refactor_plan_modules(plan_path)
    if not modules:
        return ""

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

    per_module: dict[str, list[str]] = {}
    current_mod = None
    for line in plan_text.splitlines():
        m = re.match(r'^##\s*Module:\s*(\S+)', line)
        if m:
            current_mod = m.group(1)
            continue
        if current_mod and line.strip().startswith('- '):
            sym = line.strip()[2:].strip()
            if sym and not sym.startswith('#'):
                per_module.setdefault(current_mod, []).append(sym)

    parts: list[str] = []
    parts.append("\n\n## STATUS: Symboler i api_server.py vs plan")

    for mod_name in sorted(modules):
        mod_path = os.path.join(os.getcwd(), mod_name)
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
            parts.append(f"  I api_server.py (skal flyttes til {mod_name}): {', '.join(in_source)}")
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
    if not isinstance(result_text, str) or len(result_text.strip()) < 50:
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
    """Tilf\u00f8j 'done' til active_tools hvis NATIVE_TOOLS er sl\u00e5et til."""
    if not config.NATIVE_TOOLS:
        return
    current = list(agent.tool_registry.active_tools) if agent.tool_registry.active_tools is not None else []
    if "done" not in current:
        agent.tool_registry.set_active_tools(current + ["done"])


def _validate_done_completion(
    agent: Any, messages: list[dict], called_tools: dict[str, int],
    task_node: Any, original_prompt: str, result_text: str = ""
) -> tuple[str | None, list[tuple]]:
    """F\u00e6lles valideringsk\u00e6de for b\u00e5de <<<DONE>>> (text-mode) og done() (native)."""
    yields_to_emit: list[tuple] = []

    if agent._write_failed:
        return (
            f"{t(K.SYS_ERROR_PREFIX, agent.lang)}: DU KAN IKKE afslutte med "
            f"<<<DONE>>>/done() n\u00e5r edit_file har fejlet. "
            f"Ret din anmodning og kald edit_file igen.",
            yields_to_emit
        )

    if agent._tests_failed and "test" not in _normalize_phase(task_node.name).lower():
        return (
            f"{t(K.SYS_ERROR_PREFIX, agent.lang)}: DU KAN IKKE afslutte med "
            f"<<<DONE>>>/done() n\u00e5r tests fejler. Ret koden med edit_file "
            f"og k\u00f8r run_tests() igen indtil ALLE tests best\u00e5r.",
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

    validation_err = _validate_done_output(agent, result_text, task_node.name, task_node)
    if validation_err:
        return (f"{t(K.SYS_ERROR_PREFIX, agent.lang)}: {validation_err}", yields_to_emit)

    fix_err = _count_fix_attempts(agent, called_tools)
    if fix_err:
        return (f"{t(K.SYS_ERROR_PREFIX, agent.lang)}: {fix_err}", yields_to_emit)

    return (None, yields_to_emit)


def set_task_tools(agent: Any, task_name: str) -> None:
    """set task tools.
    
    Args:
        agent:
        task_name:"""
    if not agent.active_template or agent.active_template not in agent_skills.TEMPLATE_TASK_TOOLS:
        _ensure_done_tool(agent)
        return
    template_tools = agent_skills.TEMPLATE_TASK_TOOLS[agent.active_template]
    phase = _normalize_phase(task_name)
    if phase in template_tools:
        agent.tool_registry.set_active_tools(template_tools[phase])
        agent._log("TOOL", f"Aktive tools for '{task_name[:40]}'", ', '.join(template_tools[phase]))
        _ensure_done_tool(agent)
        return
    for keyword, tools in template_tools.items():
        if keyword in phase.lower():
            agent.tool_registry.set_active_tools(tools)
            agent._log("TOOL", f"Aktive tools for '{task_name[:40]}'", ', '.join(tools))
            _ensure_done_tool(agent)
            return
    allowed = agent_skills.TEMPLATE_TOOLS.get(agent.active_template)
    if allowed is not None:
        agent.tool_registry.set_active_tools(allowed)
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
        base_dir = os.path.abspath('.')
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


def _build_initial_messages(agent: Any, task_node: Any, original_prompt: str, chunk_hint: str) -> tuple[list[dict], str, bool]:
    """build initial messages.
    
    Args:
        agent:
        task_node:
        original_prompt:
        chunk_hint:"""
    clean_prompt = getattr(agent, 'prompt', original_prompt)
    file_ctx = getattr(agent, '_file_context_str', '')

    section_instr = agent_skills.SECTION_INSTRUCTIONS.get(agent.active_template, {}).get(agent.lang + "_" + task_node.name.lower(), "") or \
                    agent_skills.SECTION_INSTRUCTIONS.get(agent.active_template, {}).get(task_node.name.lower(), "") or \
                    agent_skills.SECTION_INSTRUCTIONS.get(agent.active_template, {}).get(task_node.name, "") or \
                    agent_skills.SECTION_INSTRUCTIONS.get(agent.active_template, {}).get(_normalize_phase(task_node.name), "")
    criteria_block = ""
    header = t(K.CRITERIA_HEADER, agent.lang)
    if task_node.success_criteria:
        items = "\n".join(f"- {c}" for c in task_node.success_criteria)
        criteria_block = f"\n\n## {header}\n{items}\n"
    elif section_instr and agent.active_template:
        criteria_block = f"\n\n## {header}\n- {section_instr.split(chr(10))[0][:200]}\n"

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
                    prev_results.append(f"### {sib.name}\n{sib.result[:2000].strip()}")
            if prev_results:
                sibling_block = "\n\n## Resultater fra tidligere faser\n" + "\n\n".join(prev_results)

    # For refactor template's Ekstraher and Opdatér phases, auto-load
    # refactor_plan.md + symbol-status so the LLM knows EXACTLY which
    # symbols need extraction/cleanup — no list_symbols needed.
    plan_block = ""
    if (agent.active_template == "refactor" and
        task_node.name.lower() in ("ekstraher", "opdatér") and
        not any("refactor_plan.md" in str(c) for c in agent.file_chunks.values())):
        plan_path = "refactor_plan.md"
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

    # For programming template's later phases, auto-load docs from earlier phases.
    PROGRAMMING_DOCS = [
        ("docs/kravanalyse.md", "Kravanalyse"),
        ("docs/arkitektur.md", "Arkitekturdesign"),
        ("docs/implementeringsplan.md", "Implementeringsplan"),
        ("docs/sikkerhedsanalyse.md", "Sikkerhedsanalyse"),
    ]
    PHASE_ORDER = ["kravanalyse", "arkitekturdesign", "implementeringsplan", "sikkerhedsanalyse", "kodeimplementering"]
    if agent.active_template == "programmering" and not plan_block:
        current_idx = -1
        task_lower = task_node.name.lower() if task_node.name else ""
        for i, p in enumerate(PHASE_ORDER):
            if p in task_lower:
                current_idx = i
                break
        if current_idx > 0:
            loaded_blocks = []
            for doc_path, doc_phase in PROGRAMMING_DOCS[:current_idx]:
                full_path = os.path.join(os.getcwd(), doc_path)
                if os.path.exists(full_path):
                    try:
                        with open(full_path, encoding="utf-8") as _df:
                            _content = _df.read()
                        loaded_blocks.append(
                            f"### {doc_phase} — {doc_path}\n\n```\n{_content[:2000]}\n```"
                        )
                    except Exception as _e:
                        agent._log("DEBUG", f"Failed to load {doc_path}: {_e}", "")
            if loaded_blocks:
                plan_block = "\n\n## Dokumenter fra tidligere faser\n" + "\n\n".join(loaded_blocks)
                agent._log("DEBUG", f"Auto-loaded {len(loaded_blocks)} previous phase docs for {task_node.name}", "")

    # Phase anchor: tell the LLM which phase it's currently in and forbid
    # cross-phase reasoning (the LLM otherwise tries to re-do Plan/Extract/etc.
    # because it sees the workflow description in the section instruction).
    phase_block = "\n\n" + t(K.PHASE_CURRENT, agent.lang).format(phase_name=task_node.name) + \
                  t(K.PHASE_ONLY, agent.lang).format(phase_name=task_node.name)

    if section_instr:
        task_prompt = f"{section_instr}{criteria_block}{sibling_block}{plan_block}{phase_block}\n\nKontekst / Context: {clean_prompt}{chunk_hint}"
    else:
        task_prompt = f"{task_node.name}{criteria_block}{sibling_block}{plan_block}{phase_block}\n\nKontekst / Context: {clean_prompt}{chunk_hint}"

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

    tools_list = ', '.join([k for k in agent.tool_registry.tools if agent.tool_registry.active_tools is None or k in agent.tool_registry.active_tools])
    lang_instr = t(K.ANSWER_IN, agent.lang)
    user_guidance = f"{lang_instr}. "
    if chunk_hint:
        user_guidance += chunk_hint.strip() + " "
    if tools_list:
        if config.NATIVE_TOOLS:
            user_guidance += t(K.TOOL_CONTINUATION_NATIVE, agent.lang).format(tools_list=tools_list)
        else:
            user_guidance += t(K.TOOL_CONTINUATION, agent.lang).format(tools_list=tools_list, TOOL_MARKER=agent.tool_registry.TOOL_MARKER, DONE_MARKER=agent.tool_registry.DONE_MARKER)
    else:
        user_guidance += t(K.DONE_CONTINUATION, agent.lang).format(DONE_MARKER=agent.tool_registry.DONE_MARKER)
    if not chunk_hint and tools_list:
        read_only = all(t not in ('write_file',) for t in agent.tool_registry.active_tools or [])
        if read_only and not agent.images and not agent.file_chunks:
            user_guidance += "\n\nOBS: Ingen filer er indl\u00e6st. Du KAN svare direkte uden at kalde v\u00e6rkt\u00f8jer f\u00f8rst. Sp\u00f8rg IKKE efter filnavne \u2014 brug din egen viden til at besvare opgaven."
    has_write = any(t in ('write_file', 'edit_file') for t in (agent.tool_registry.active_tools or []))
    if has_write:
        user_guidance += t(K.WRITE_REQUIRED, agent.lang)
    wta_tip = agent._seq.generate_tool_tip(agent.active_template or "fri", task_node.name) if hasattr(agent, '_seq') else ""
    if wta_tip:
        user_guidance += "\n\n" + wta_tip

    # Tool-specific hints — filtered by active tools to avoid confusing the LLM
    active_tool_set = set(agent.tool_registry.active_tools or [])
    tool_hints = {
        "list_symbols": "\n  Brug list_symbols(filepath='fil.py') for at se ALLE symboler i en Python-fil — gør det FØR locate/read_location når du ikke kender symbolnavnene.",
        "read_chunk": "\n  Read_chunk må KUN bruges til IKKE-PYTHON filer (JSON, HTML, TXT, osv.). For .py-filer, brug read_location i stedet.",
        "locate": "\n  Brug locate(name='funktionsnavn') for at finde en funktion på tværs af ALLE .py-filer (filepath er valgfri). locate returnerer også 'also_in_file'.",
        "read_location": "\n  Brug read_location(filepath='fil.py', name='funktionsnavn') for at læse KUN en bestemt funktion/metode/klasse — IKKE hele filen.",
        "write_file": "\n  Brug write_file(path='ny_fil.py', content='...') for at oprette NYE filer (brug overwrite=true til at erstatte eksisterende). Skriv HELE modulet på ÉN gang.",
        "edit_file": "\n  Brug edit_file(path='fil.py', old_text='...', new_text='...') for at redigere EKSISTERENDE kode.",
        "run_tests": "\n  Brug run_tests() for at køre tests og verificere at din kode virker.",
        "update_issue_status": "\n  Brug update_issue_status(issue_id='...', status='resolved') når et issue er løst.",
    }
    filtered_hints = [h for tool_name, h in tool_hints.items() if tool_name in active_tool_set]
    if filtered_hints:
        user_guidance += "\n\n## VÆRKTØJSGUIDE" + "".join(filtered_hints)

    messages = [{"role": "system", "content": system_prompt}]
    if file_ctx:
        messages.append({"role": "system", "content": f"## Filindhold (f\u00f8rste iteration)\n\n{file_ctx}"})
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


def _truncate_messages(messages: list[dict], max_chars: int) -> list[dict]:
    """truncate messages.
    
    Args:
        messages:
        max_chars:"""
    total = sum(_msg_content_len(m) for m in messages)
    if total <= max_chars or len(messages) <= 3:
        return messages
    mid = "\n[... tidligere kontekst afkortet ...]"
    system = [m for m in messages if m["role"] == "system"]
    non_system = [m for m in messages if m["role"] != "system"]
    keep_pairs = 4
    tail = non_system[-keep_pairs:] if len(non_system) > keep_pairs else non_system
    insert = [{"role": "user", "content": mid}]
    return system + insert + tail


def _cont_hint(agent: Any, tools_list: str) -> str:
    """cont hint.
    
    Args:
        agent:
        tools_list:"""
    if config.NATIVE_TOOLS:
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
        _add_user_msg(messages, f"{t(K.SYS_ERROR_PREFIX, agent.lang)}: Du har allerede dette resultat. Gå videre eller brug <<<DONE>>>.")
        return None
    if parsed["tool"] in ("write_file", "edit_file") and getattr(agent, 'issue_resolved', False) and getattr(agent, 'active_template', '') != 'refactor':
        _add_user_msg(messages, f"{t(K.SYS_ERROR_PREFIX, agent.lang)}: BLOCKERET — issuet er allerede markeret som resolved. Redigér IKKE filer. Brug <<<DONE>>> for at afslutte, eller genåbn issuet med update_issue_status('<id>', 'open') først.")
        return None

    # In test phases, force write_file as the first tool call — block reads before write
    if "test" in _normalize_phase(task_node.name):
        write_file_called = any(k.startswith("write_file{") for k in called_tools if k.split("{")[0] != parsed["tool"])
        if not write_file_called and parsed["tool"] != "write_file":
            _add_user_msg(messages, f"{t(K.SYS_ERROR_PREFIX, agent.lang)}: {t(K.WRITE_FILE_FIRST, agent.lang)}")
            return None

    agent._log("TOOL", t(K.LOG_TOOL_CALLING, agent.lang).format(tool=parsed['tool']), str(parsed.get("args", {})))
    result = agent.tool_registry.execute(parsed["tool"], parsed["args"])
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

    checkpoint_msg = agent_git.verify_pr_step(agent, parsed["tool"], result, task_node.name, original_prompt)
    if checkpoint_msg:
        _add_user_msg(messages, f"!!! CHECKPOINT - {checkpoint_msg}")
        agent._log("INFO", "CHECKPOINT", checkpoint_msg)
        return {"type": "checkpoint", "tool": parsed["tool"], "args": parsed.get("args", {}), "result": result, "checkpoint_msg": checkpoint_msg}
    else:
        agent._checkpoint_tools.add(parsed["tool"] + str(parsed.get("args", {})))
        msg = f"{t(K.TOOL_RESULT_PREFIX, agent.lang).format(tool=parsed['tool'])}\n{result_str}\n\n{_cont_hint(agent, tools_list)}"
        if agent._write_failed:
            msg += f"\n\n\u26a0\ufe0f {t(K.SYS_ERROR_PREFIX, agent.lang)}: edit_file MISLYKKEDES. DU M\u00c5 IKKE bruge <<<DONE>>> f\u00f8r edit_file lykkes. Ret din anmodning og pr\u00f8v igen."
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


REQUIRED_ACTION_TOOLS = {"edit_file", "write_file", "extract_symbol", "update_issue_status"}


CLOSE_PHASE_ALIASES = {"opdatering", "opdatér", "luk", "close"}

def _check_required_tools(agent: Any, called_tools: dict, task_name: str = "") -> str | None:
    """check required tools.

    Args:
        agent:
        called_tools:
        task_name:"""
    template = getattr(agent, "active_template", "")
    if template == "refactor" and task_name:
        refactor_writing_phases = ("plan", "ekstraher", "opdat")
        has_written = any(k in (called_tools or {}) for k in (
            k for k in called_tools
            if k.startswith("write_file") or k.startswith("edit_file") or k.startswith("extract_symbol") or k.startswith("remove_symbol") or k.startswith("add_import")
        ))
        if any(k in _normalize_phase(task_name).lower() for k in refactor_writing_phases) and not has_written:
            iteration = getattr(agent, "_current_task_iteration", 0)
            if iteration >= 3 and not getattr(agent, "_non_productive_reminder_sent", False):
                agent._non_productive_reminder_sent = True
                return ("FEJL: Du har ikke kaldt write_file, edit_file, extract_symbol, remove_symbol eller add_import i "
                        f"{iteration} iterationer. Refactor kræver at du SKRIVER kode. "
                        "Brug write_file for nye moduler eller edit_file for at opdatere api_server.py.")
    elif template == "programming" and task_name:
        programming_writing_phases = ("arkitekturdesign", "implementeringsplan", "kodeimplementering")
        has_written = any(k in (called_tools or {}) for k in called_tools if k.startswith("write_file") or k.startswith("edit_file"))
        if any(k in _normalize_phase(task_name).lower() for k in programming_writing_phases) and not has_written:
            iteration = getattr(agent, "_current_task_iteration", 0)
            if iteration >= 5 and not getattr(agent, "_non_productive_reminder_sent", False):
                agent._non_productive_reminder_sent = True
                return ("FEJL: Du har ikke kaldt write_file eller edit_file i "
                        f"{iteration} iterationer. Programming kræver at du SKRIVER kode og design. "
                        "Brug write_file til at oprette filer (arkitektur, plan, kode). Stop med at læse og begynd at skrive.")
    available = set(agent.tool_registry.active_tools or [])
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
    # If update_issue_status was called, the bug is being resolved —
    # edit_file/write_file are no longer needed.
    if "update_issue_status" in called_names:
        required -= {"edit_file", "write_file"}
    # If the run_tests auto-complete already marked the issue resolved
    # (Test phase with passing tests), edit_file/write_file are no longer needed.
    if getattr(agent, "issue_resolved", False) and getattr(agent, 'active_template', '') != 'refactor':
        required -= {"edit_file", "write_file"}
    uncalled = required - called_names
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
    # tool_log success check: tools som blev kaldt men ALLE forsøg fejlede tæller ikke
    if agent._tool_log and not uncalled:
        for req_tool in required:
            if req_tool in called_names:
                attempts = [e for e in agent._tool_log if e.get("tool") == req_tool and e.get("success") is False]
                all_attempts = [e for e in agent._tool_log if e.get("tool") == req_tool]
                if all_attempts and len(attempts) == len(all_attempts):
                    if req_tool == "write_file" and "edit_file" in called_names:
                        continue
                    if req_tool == "edit_file" and "write_file" in called_names:
                        continue
                    if req_tool == "write_file" and "extract_symbol" in called_names:
                        continue
                    if req_tool == "extract_symbol" and "write_file" in called_names:
                        continue
                    uncalled.add(req_tool)
    if uncalled:
        return t(K.LOG_REQUIRED_TOOLS_MISSING, agent.lang).format(tools=", ".join(sorted(uncalled)))
    return None


ISSUE_ID_PATTERN = re.compile(r'(BUG|SEC|ARC|MNT|PRF|TST|REFAC)-\d+', re.IGNORECASE)


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

    if tool_name == "run_tests" and not agent._tests_failed:
        if "test" in phase:
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
            plan_path = os.path.join(os.getcwd(), "refactor_plan.md")
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
    # Only run after a productive tool call to avoid infinite loops on read-only.
    PRODUCTIVE_TOOLS = {"write_file", "edit_file", "run_tests", "update_issue_status"}
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
            if current != model_name:
                agent.llm.set_model(model_name)
                agent._log("MODEL", f"Switched to {model_name} for {task_name}",
                           f"{current} \u2192 {model_name}")
            return


FRAMEWORK_PY = {"api_server.py", "agent_core.py", "agent_tasks.py", "agent_skills.py", "agent_files.py", "agent_issues.py", "agent_tree.py", "agent_git.py", "agent_phase_checks.py", "agent_wta.py", "core_analytics.py", "agent_logs.py", "tools.py", "i18n.py", "lang.py", "config.py", "task_tree.py", "llm_wrapper.py", "model_manager.py", "session_manager.py", "flow_builder.py", "skill_evolution.py", "skill_loader.py", "skill_tracker.py", "refactoring_engine.py", "edit_file2.py", "github_wrapper.py"}


def _get_modified_core_files(agent: Any) -> set[str]:
    """Return set of core framework file basenames modified during this task."""
    modified: set[str] = set()
    for entry in getattr(agent, '_tool_log', []):
        tool = entry.get("tool", "")
        if tool not in ("write_file", "edit_file", "extract_symbol", "remove_symbol"):
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
            action_tools = called_names & {"write_file", "edit_file", "update_issue_status", "github_create_pr", "git_commit", "run_tests"}
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

    missing_msg = _check_required_tools(agent, called_tools, task_node.name)
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
                        f"Auto-resolved: Analyse konkluderede at fejlen allerede er løst. {source_text[:200]}")
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
    else:
        agent._log("INFO", t(K.LOG_TASK_DONE, agent.lang), task_node.name)
    agent._evolve_if_needed()
    for entry in agent.agent_log[_report_logs:]:
        yield {"type": "log", "log": entry}
    yield {"type": "done", "result": full_response}


def solve_task_stream(agent: Any, task_node: Any, original_prompt: str) -> Generator[dict, None, None]:
    """solve task stream.
    
    Args:
        agent:
        task_node:
        original_prompt:
    
    Yields:
        ..."""
    task_node.status = "running"
    agent._task_start_time = time.time()
    agent.current_phase = _normalize_phase(task_node.name)
    agent._log("INFO", t(K.LOG_TASK_START, agent.lang), f"{task_node.name} (model: {agent.llm.model})")
    _set_phase_model(agent, task_node.name)
    set_task_tools(agent, task_node.name)
    agent._checkpoint_tools = set()
    agent._checkpoint_branch = ""
    agent._rubric_retried = False

    # Greenfield check: programmering template must NOT execute if .py files exist
    if agent.active_template == "programmering":
        cwd = os.getcwd()
        FRAMEWORK_PY = {"api_server.py", "agent_core.py", "agent_tasks.py", "agent_skills.py", "agent_files.py", "agent_issues.py", "agent_tree.py", "agent_git.py", "agent_phase_checks.py", "agent_wta.py", "core_analytics.py", "agent_logs.py", "tools.py", "i18n.py", "lang.py", "config.py", "task_tree.py", "llm_wrapper.py", "model_manager.py", "session_manager.py", "flow_builder.py", "skill_evolution.py", "skill_loader.py", "skill_tracker.py", "refactoring_engine.py", "edit_file2.py", "github_wrapper.py"}
        existing_py = [
            f for f in os.listdir(cwd)
            if f.endswith(".py")
            and f not in FRAMEWORK_PY
            and os.path.isfile(os.path.join(cwd, f))
        ]
        if existing_py:
            msg = (
                f"Programmeringsskabelonen er kun til greenfield-projekter. Workdir "
                f"indeholder allerede .py-filer ({', '.join(existing_py[:5])}). "
                f"Brug i stedet en bugfix-, refactor- eller kodeanalyse-skabelon."
            )
            agent._log("ERROR", msg, "")
            task_node.status = "failed"
            task_node.result = msg
            yield {"type": "done", "result": msg}
            return

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
    READ_ONLY_TOOLS = {"read_location", "read_chunk", "list_chunks", "list_files", "list_symbols", "locate", "read_issue"}
    agent._write_failed = False
    agent._tests_failed = False
    agent._located_files = set()
    agent._current_task_iteration = 0
    agent._non_productive_reminder_sent = False
    agent._tool_log = []
    _task_deadline = time.time() + EXECUTION_TIMEOUT

    for i in range(max_iterations):
        agent._current_task_iteration = i + 1
        if agent.stop_requested:
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
        tool_defs = agent.tool_registry.get_openai_tools_for_active() if config.NATIVE_TOOLS else []
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
            for chunk in agent.llm.generate_stream(messages=messages, temperature=0.3, max_tokens=agent.max_tokens, images=agent.images, tools=tools_param):
                if agent.stop_requested:
                    break
                response += chunk
                yield {"type": "chunk", "chunk": chunk}
        except GeneratorExit:
            agent._log("INFO", "Client disconnected", "GeneratorExit")
            raise

        if agent.stop_requested:
            break

        if response.startswith("[ERROR:") or response.startswith("ERROR:"):
            yield {"type": "error", "message": response}
            break

        pending_tc = getattr(agent.llm, '_pending_tool_calls', [])
        if pending_tc and hasattr(agent, '_wta'):
            template = getattr(agent, 'active_template', 'fri') or 'fri'
            phase = getattr(task_node, 'name', '?')
            original = [tc.get("function", {}).get("name", "?") for tc in pending_tc]
            pending_tc = agent._wta.rank_tool_calls(template, phase, pending_tc)
            reordered = [tc.get("function", {}).get("name", "?") for tc in pending_tc]
            if original != reordered:
                agent._log("WTA", f"Reordered: {', '.join(original or ['?'])} \u2192 {', '.join(reordered or ['?'])}", f"tpl={template}, phase={phase}")
        pending_reasoning = getattr(agent.llm, '_pending_reasoning', None)
        if pending_tc:
            tool_call_msg = {"role": "assistant", "content": None, "tool_calls": list(pending_tc)}
            if pending_reasoning:
                tool_call_msg["reasoning_content"] = pending_reasoning
            tc_names = [tc["function"]["name"] for tc in pending_tc]
            reasoning = (pending_reasoning or "")[:400].replace("\n", " ").replace("\r", "")
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
                tool_key = tool_name + str(args_val)
                dup_count = called_tools.get(tool_key, 0)
                called_tools[tool_key] = dup_count + 1
                if dup_count >= 1:
                    consecutive_dedups += 1
                    _add_user_msg(messages, f"{t(K.SYS_ERROR_PREFIX, agent.lang)}: Du har allerede dette resultat. G\u00e5 videre eller brug <<<DONE>>>.")
                    if consecutive_dedups >= 3:
                        write_tools = [t for t in ("write_file", "edit_file")
                                       if t in agent.tool_registry.active_tools]
                        if write_tools:
                            reminder = (
                                f"[SYSTEM: Du er i en l\u00f8kke med identiske resultater. "
                                f"STOP med at l\u00e6se. BRUG et v\u00e6rkt\u00f8j der SKRIVER: "
                                f"{', '.join(write_tools)}. Hvis du er i tvivl, brug write_file med et NYT filnavn.]"
                            )
                            messages.append({"role": "system", "content": reminder})
                            agent._log("SYSTEM", "Dedup-loop escape", reminder[:120])
                            consecutive_dedups = 0
                    continue
                consecutive_dedups = 0
                if tool_name in READ_ONLY_TOOLS:
                    if getattr(agent, '_read_escape_sent', False):
                        result_str = f"[SYSTEM: L\u00e6sekald BLOKERET. Du fik besked om at skrive. Brug write_file NU.]"
                        result = {"success": True, "result": "Skipped — write forced"}
                        agent._log("TOOL", t(K.LOG_TOOL_CALLING, agent.lang).format(tool=tool_name), str(args_val))
                        agent._log("TOOL", t(K.LOG_TOOL_RESULT, agent.lang).format(tool=tool_name), result_str)
                        messages.append({"role": "tool", "tool_call_id": tc.get("id", ""), "content": result_str})
                        yield {"type": "tool_call", "tool": tool_name, "args": args_val}
                        yield {"type": "tool_result", "tool": tool_name, "args": args_val, "result": result}
                        continue
                    consecutive_reads += 1
                    if consecutive_reads >= 5:
                        write_tools = [t for t in ("write_file", "edit_file", "extract_symbol", "remove_symbol", "add_import")
                                       if t in agent.tool_registry.active_tools]
                        if write_tools:
                            _add_user_msg(messages, (
                                f"[SYSTEM: Du har lavet {consecutive_reads} l\u00e6sekald i tr\u00e6k uden at skrive noget. "
                                f"STOP med at l\u00e6se. BRUG et v\u00e6rkt\u00f8j der SKRIVER: "
                                f"{', '.join(write_tools)}. L\u00e6s h\u00f8jst \u00e9n ting mere, skriv s\u00e5.]"
                            ))
                            agent._log("SYSTEM", "Read-loop escape", f"{consecutive_reads} consecutive reads — force write")
                            consecutive_reads = 0
                            agent._read_escape_sent = True
                            result_str = f"[SYSTEM: L\u00e6sekald blokeret. Brug {', '.join(write_tools)}.]"
                            result = {"success": True, "result": "Skipped — force write"}
                            agent._log("TOOL", t(K.LOG_TOOL_CALLING, agent.lang).format(tool=tool_name), str(args_val))
                            agent._log("TOOL", t(K.LOG_TOOL_RESULT, agent.lang).format(tool=tool_name), result_str)
                            messages.append({"role": "tool", "tool_call_id": tc.get("id", ""), "content": result_str})
                            yield {"type": "tool_call", "tool": tool_name, "args": args_val}
                            yield {"type": "tool_result", "tool": tool_name, "args": args_val, "result": result}
                            continue
                elif tool_name in ("write_file", "edit_file", "extract_symbol", "remove_symbol", "add_import"):
                    consecutive_reads = 0
                    agent._read_escape_sent = False
                if tool_name in ("write_file", "edit_file") and getattr(agent, 'issue_resolved', False) and getattr(agent, 'active_template', '') != 'refactor':
                    result_str = f"{t(K.SYS_ERROR_PREFIX, agent.lang)}: BLOCKERET — issuet er allerede markeret som resolved. Redig\u00e9r IKKE filer. Brug <<<DONE>>> for at afslutte, eller gen\u00e5bn issuet f\u00f8rst."
                    result = {"success": False, "error": "Issue already resolved"}
                    agent._log("TOOL", t(K.LOG_TOOL_CALLING, agent.lang).format(tool=tool_name), str(args_val))
                    agent._log("TOOL", t(K.LOG_TOOL_RESULT, agent.lang).format(tool=tool_name), result_str)
                    messages.append({"role": "tool", "tool_call_id": tc.get("id", ""), "content": result_str})
                    yield {"type": "tool_call", "tool": tool_name, "args": args_val}
                    yield {"type": "tool_result", "tool": tool_name, "args": args_val, "result": result}
                    continue
                agent._log("TOOL", t(K.LOG_TOOL_CALLING, agent.lang).format(tool=tool_name), str(args_val))
                result = agent.tool_registry.execute(tool_name, args_val)
                result_str = json.dumps(result, ensure_ascii=False)
                agent._record_tool_call(
                    phase=getattr(task_node, 'name', '?'),
                    tool=tool_name,
                    args=args_val,
                    success=result.get('success', False) if isinstance(result, dict) else True,
                    error=result.get('error', '') if isinstance(result, dict) else '',
                )
                if tool_name in ("write_file", "edit_file", "extract_symbol", "remove_symbol", "add_import"):
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
                if tool_name in ("write_file", "edit_file") and isinstance(result, dict) and result.get("success") is False:
                    agent._write_failed = True
                    result_str += f"\n\n\u26a0\ufe0f {t(K.SYS_ERROR_PREFIX, agent.lang)}: edit_file MISLYKKEDES. DU M\u00c5 IKKE bruge <<<DONE>>> f\u00f8r edit_file lykkes. Ret din anmodning og pr\u00f8v igen."
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
                        messages = _truncate_messages(messages, agent.max_conversation_chars)
                        continue
                    full_response = result.get("result", t(K.LOG_TASK_DONE, agent.lang))
                    break
                msg = _get_phase_auto_complete_msg(task_node, tool_name, result, agent, called_tools=called_tools, full_response=full_response)
                if msg:
                    agent._log("INFO", msg, "")
                    full_response = msg
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
                messages = _truncate_messages(messages, agent.max_conversation_chars)
                total_calls = sum(called_tools.values())
                if total_calls >= _get_max_tool_calls(task_node.name):
                    full_response = t(K.LOG_AUTO_DONE, agent.lang).format(count=total_calls)
                    break
            if full_response:
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
                messages = _truncate_messages(messages, agent.max_conversation_chars)
                continue
            for entry in agent.agent_log[_report_logs:]:
                yield {"type": "log", "log": entry}
            _report_logs = len(agent.agent_log)
            yield {"type": "tool_call", "tool": tool_result["tool"], "args": tool_result["args"]}
            yield {"type": "tool_result", "tool": tool_result["tool"], "result": tool_result["result"]}
            if tool_result.get("checkpoint_msg"):
                yield {"type": "checkpoint", "message": tool_result["checkpoint_msg"], "tool": parsed["tool"]}
            msg = _get_phase_auto_complete_msg(task_node, tool_result.get("tool"), tool_result.get("result"), agent, called_tools=called_tools, full_response=full_response)
            if msg:
                agent._log("INFO", msg, "")
                full_response = msg
                break
            messages = _truncate_messages(messages, agent.max_conversation_chars)
            total_calls = sum(called_tools.values())
            if total_calls >= _get_max_tool_calls(task_node.name):
                full_response = t(K.LOG_AUTO_DONE, agent.lang).format(count=total_calls)
                break
            continue

        if parsed["type"] == "done":
            if agent._write_failed:
                _add_user_msg(messages, f"{t(K.SYS_ERROR_PREFIX, agent.lang)}: DU KAN IKKE afslutte med <<<DONE>>> n\u00e5r edit_file har fejlet. Ret din anmodning og kald edit_file igen.")
                messages = _truncate_messages(messages, agent.max_conversation_chars)
                continue
            if agent._tests_failed and "test" not in _normalize_phase(task_node.name).lower():
                _add_user_msg(messages, f"{t(K.SYS_ERROR_PREFIX, agent.lang)}: DU KAN IKKE afslutte med <<<DONE>>> n\u00e5r tests fejler. Ret koden med edit_file og k\u00f8r run_tests() igen indtil ALLE tests best\u00e5r.")
                messages = _truncate_messages(messages, agent.max_conversation_chars)
                continue
            if not _check_done_pr_requirements(agent, messages, called_tools, original_prompt, task_node.name):
                messages = _truncate_messages(messages, agent.max_conversation_chars)
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
                messages = _truncate_messages(messages, agent.max_conversation_chars)
                continue
            missing_msg = _check_required_tools(agent, called_tools, task_node.name)
            if missing_msg:
                _add_user_msg(messages, f"{t(K.SYS_ERROR_PREFIX, agent.lang)}: {missing_msg}")
                messages = _truncate_messages(messages, agent.max_conversation_chars)
                continue
            validation_err = _validate_done_output(agent, parsed.get("result", ""), task_node.name, task_node)
            if validation_err:
                _add_user_msg(messages, f"{t(K.SYS_ERROR_PREFIX, agent.lang)}: {validation_err}")
                messages = _truncate_messages(messages, agent.max_conversation_chars)
                continue
            fix_err = _count_fix_attempts(agent, called_tools)
            if fix_err:
                _add_user_msg(messages, f"{t(K.SYS_ERROR_PREFIX, agent.lang)}: {fix_err}")
                messages = _truncate_messages(messages, agent.max_conversation_chars)
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
                if config.NATIVE_TOOLS:
                    yield {"type": "error", "message": f"3 consecutive format errors — stopping. Use the available tools to complete the task."}
                else:
                    yield {"type": "error", "message": f"3 consecutive format errors — stopping. Use format: {agent.tool_registry.TOOL_MARKER}{{\"tool\":\"...\",\"args\":{{...}}}}{agent.tool_registry.END_MARKER} or {agent.tool_registry.DONE_MARKER}{{...}}{agent.tool_registry.END_MARKER}"}
                break
            if consecutive_errors == 1:
                if config.NATIVE_TOOLS:
                    hint = f"Use the available tools to complete the task: {', '.join(list(called_tools.keys())[:3]) if called_tools else ', '.join(agent.tool_registry.active_tools[:3])}"
                else:
                    hint = f"Write your response in the correct format: {agent.tool_registry.TOOL_MARKER}{{\"tool\":\"{list(called_tools.keys())[0] if called_tools else 'write_file'}\",\"args\":{{...}}}}{agent.tool_registry.END_MARKER}"
                _add_user_msg(messages, f"{t(K.SYS_ERROR_PREFIX, agent.lang)}: {parsed['message']}. {hint}")
            else:
                _add_user_msg(messages, f"{t(K.SYS_ERROR_PREFIX, agent.lang)}: {parsed['message']}. Only use <<<TOOL>>> or <<<DONE>>> — no English text before or after.")
            messages = _truncate_messages(messages, agent.max_conversation_chars)
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
                if not config.NATIVE_TOOLS:
                    tool_for_msg = agent.tool_registry.active_tools[0] if agent.tool_registry.active_tools else t(K.SYS_FALLBACK_TOOL, agent.lang)
                    _add_user_msg(messages, f"{t(K.SYS_ERROR_PREFIX, agent.lang)}: {t(K.FIRST_TOOL_REQUIRED, agent.lang).format(tool=tool_for_msg)}")
                messages = _truncate_messages(messages, agent.max_conversation_chars)
                continue

        clean = response.strip() if "ERROR" not in response else ""
        if clean:
            text_fallback = clean
        _add_user_msg(messages, t(K.TOOL_NO_RESULT, agent.lang))
        messages = _truncate_messages(messages, agent.max_conversation_chars)
        full_response = response
        if i >= 3 and not called_tools:
            break

    yield from _finalize_task_stream(agent, task_node, full_response, text_fallback, called_tools, _report_logs, original_prompt, messages)
