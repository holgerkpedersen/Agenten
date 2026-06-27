import os
import json
from typing import Any, Generator
import config
from agent_utils import _is_greenfield

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
