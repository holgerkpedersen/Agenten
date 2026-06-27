"""Level-0 utility module for pure functions with no agent-module dependencies."""
import os
import re
import config
from llm_wrapper import LMStudioWrapper
from agent_config import PHASE_ALIASES


def _use_native_tools(agent=None):
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


def _normalize_phase(name: str) -> str:
    """Normalize phase name — lowercase, strip numbering, apply aliases."""
    lower = name.lower().split("(")[0].strip()
    lower = re.sub(r'^[\d.]+[\)\s]*', '', lower).strip()
    return PHASE_ALIASES.get(lower, lower)


_TODO_TOOLS = {"plan_phase", "create_todo", "update_todo", "delete_todo", "list_todos"}


def _inject_todo_tools(tools: list[str]) -> list[str]:
    """Add universal todo tools to a tool list if missing."""
    for t in _TODO_TOOLS:
        if t not in tools:
            tools.append(t)
    return tools
