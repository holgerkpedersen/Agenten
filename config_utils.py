from config import get_logger
import config
import agent_skills
import agent_git
from typing import Any, Generator
import os

log = get_logger(__name__)


EXECUTION_TIMEOUT = config.EXECUTION_TIMEOUT


_WRITE_TOOLS = frozenset({"write_file", "edit_file", "write_file_section", "convert_pdf_html5"})



FRAMEWORK_PY = {"api_server.py", "agent_core.py", "agent_tasks.py", "agent_skills.py", "agent_files.py", "agent_issues.py", "agent_tree.py", "agent_git.py", "agent_phase_checks.py", "agent_wta.py", "core_analytics.py", "agent_logs.py", "tools.py", "i18n.py", "lang.py", "config.py", "task_tree.py", "llm_wrapper.py", "model_manager.py", "session_manager.py", "flow_builder.py", "skill_evolution.py", "skill_loader.py", "skill_tracker.py", "refactoring_engine.py", "github_wrapper.py"}



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
