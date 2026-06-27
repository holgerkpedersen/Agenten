import re
from agent_config import log, EXECUTION_TIMEOUT, _WRITE_TOOLS, PHASE_ALIASES, REQUIRED_ACTION_TOOLS, CLOSE_PHASE_ALIASES, ISSUE_ID_PATTERN, AUTO_RESOLVE_PATTERNS, FRAMEWORK_PY, _TODO_TOOL_MAP
from lang import t
import agent_skills
from typing import Any, Generator
import config
import agent_git
from agent_done_validation import _validate_done_output, _count_fix_attempts, _ensure_done_tool, _validate_done_completion, _check_done_pr_requirements
from agent_utils import _is_greenfield, _use_native_tools, _normalize_phase, _inject_todo_tools



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



def set_task_tools(agent: Any, task_name: str) -> None:
    """set task tools.
    
    Args:
        agent:
        task_name:"""
    _TODO_TOOLS = {"plan_phase", "create_todo", "update_todo", "delete_todo", "list_todos"}

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
    try:
        from agent_stream import solve_task_stream
        for event in solve_task_stream(agent, task_node, original_prompt):
            if event["type"] == "done":
                full_response = event["result"]
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        agent._log("ERROR", f"solve_task_stream CRASHED: {e}", tb[-500:])
        return f"ERROR: {e}"
    return full_response or "Task failed"



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
