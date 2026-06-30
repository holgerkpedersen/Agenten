"""LLM-driven todo management tools.

The LLM uses these tools to create, update, and track its own task plan
during a phase. Todos appear in the frontend alongside the auto-generated
``_phase_todos`` so both the LLM and the user can see progress.
"""
from __future__ import annotations

import re
import uuid
from typing import Any

from tools import Tool
from lang import t
from i18n import K

# ── helpers ──────────────────────────────────────────────────────────────

def _ensure_llm_todos(agent: Any) -> list[dict]:
    """Return agent._llm_todos, initializing if missing."""
    if not hasattr(agent, '_llm_todos') or agent._llm_todos is None:
        agent._llm_todos = []
    return agent._llm_todos


def _extract_planned_symbols_from_steps(steps: str) -> dict[str, list[str]]:
    """Extract target→[symbols] mapping from plan_phase steps text.

    Parses lines like::

        batch_extract_symbols(source='...', symbols='sym1, sym2', target='mod.py')
        batch_extract_symbols(source='...', symbols='sym1, sym2', target='mod.py') → verify_refactor

    Returns dict mapping target filename → list of planned symbol names.
    """
    mapping: dict[str, list[str]] = {}

    # Match batch_extract_symbols or extract_symbol calls with symbols= and target=
    for line in steps.split('\n'):
        line = line.strip()
        # Extract target
        tm = re.search(r"target\s*=\s*['\"]([^'\"]+)['\"]", line)
        if not tm:
            continue
        target = tm.group(1)

        # Extract symbols — handles: 'a, b', ['a', 'b'], "a, b"
        sm = re.search(r"symbols\s*=\s*(.+)", line)
        if not sm:
            continue

        symbols_str = sm.group(1).strip()
        symbols: list[str] = []

        # JSON/Python list format: ['a', 'b'] or ["a", "b"]
        if symbols_str.startswith('['):
            try:
                import ast as _ast
                # Find the matching closing bracket
                _end = symbols_str.find(']')
                if _end > 0:
                    parsed = _ast.literal_eval(symbols_str[:_end+1])
                    if isinstance(parsed, (list, tuple)):
                        symbols = [str(s).strip() for s in parsed if str(s).strip()]
            except (ValueError, SyntaxError):
                pass

        # Quoted comma-separated: 'a, b' or "a, b"
        if not symbols:
            # Match content inside quotes (single or double)
            _qm = re.match(r"""['\"]([^'\"]+)['\"]""", symbols_str)
            if _qm:
                raw = _qm.group(1)
                symbols = [s.strip() for s in raw.split(',') if s.strip()]

        if symbols:
            mapping[target] = symbols

    return mapping


def _emit(agent: Any, event_type: str, data: dict) -> None:
    """Store an SSE event on the agent for the generator to yield."""
    if not hasattr(agent, '_pending_sse_events'):
        agent._pending_sse_events = []
    agent._pending_sse_events.append({"type": event_type, **data})


# ── tools ────────────────────────────────────────────────────────────────

def _sanitize_refactor_path(text: str) -> str:
    """Replace docs/refactor*.md → refactor*.md in plan text."""
    return text.replace("docs/refactor_analyse.md", "refactor_analyse.md") \
               .replace("docs/refactor_plan.md", "refactor_plan.md") \
               .replace("docs/refactor", "refactor")


def _plan_phase(agent: Any, phase_name: str, phase_goal: str, steps: str | None = None) -> dict[str, Any]:
    """Analyze the phase and create a structured plan with todos.

    This is the primary entry point called at phase start. It clears any
    existing LLM todos and creates a fresh set based on the phase goal.
    The optional ``steps`` parameter (newline-separated) allows the LLM
    to seed the plan with concrete steps.

    Always starts fresh — clears previous todos to avoid duplication on retry.
    """
    todos = _ensure_llm_todos(agent)

    # Prevent duplicate plan_phase calls — if LLM already has a plan,
    # warn and tell them to use update_todo instead.
    # This prevents the LLM from stacking multiple plans on each retry.
    if getattr(agent, '_llm_has_planned', False) and steps and steps.strip():
        return {
            "success": True,
            "note": "\u26a0\ufe0f Du har allerede en plan for denne fase. Brug **update_todo(id, done=true)** "
                    "til at markere fremskridt i stedet for at oprette en ny plan.",
            "todos": list(agent._llm_todos),
            "count": len(agent._llm_todos),
        }

    # Clear any previous plan — start fresh every time (handles retries)
    if agent._llm_todos:
        if steps and steps.strip():
            # Store planned symbols per target for post-call validation
            agent._planned_symbols_per_target = _extract_planned_symbols_from_steps(steps)
            lines = [s.strip() for s in steps.strip().split("\n") if s.strip()]
            for line in lines:
                # Skip if duplicate text already exists
                if any(t.get("text","").strip() == line for t in agent._llm_todos):
                    continue
                todo_id = "lt_" + uuid.uuid4().hex[:8]
                agent._llm_todos.append({
                    "id": todo_id, "text": line, "done": False,
                    "parent_id": None, "phase": phase_name,
                })
                _emit(agent, "llm_todo_add", {"id": todo_id, "text": line, "parent_id": None})
        agent._llm_has_planned = True
        return {
            "success": True,
            "todos": list(agent._llm_todos),
            "count": len(agent._llm_todos),
        }

    # Clear previous LLM todos
    agent._llm_todos = []
    _emit(agent, "llm_todo_clear", {})
    agent._llm_has_planned = True

    # If LLM provided explicit steps, use them; otherwise create a generic plan
    if steps and steps.strip():
        # Store planned symbols per target for post-call validation
        agent._planned_symbols_per_target = _extract_planned_symbols_from_steps(steps)
        lines = [s.strip() for s in steps.strip().split("\n") if s.strip()]
        for line in lines:
            line = _sanitize_refactor_path(line)
            todo_id = "lt_" + uuid.uuid4().hex[:8]
            agent._llm_todos.append({
                "id": todo_id,
                "text": line,
                "done": False,
                "parent_id": None,
                "phase": phase_name,
            })
            _emit(agent, "llm_todo_add", {"id": todo_id, "text": line, "parent_id": None})
    else:
        # Fallback: create a single high-level todo based on the goal
        todo_id = "lt_" + uuid.uuid4().hex[:8]
        agent._llm_todos.append({
            "id": todo_id,
            "text": phase_goal,
            "done": False,
            "parent_id": None,
            "phase": phase_name,
        })
        _emit(agent, "llm_todo_add", {"id": todo_id, "text": phase_goal, "parent_id": None})

    return {
        "success": True,
        "todos": list(agent._llm_todos),
        "count": len(agent._llm_todos),
        "note": "✅ Din plan er oprettet. Brug IKKE create_todo/update_todo — disse er allerede todo'er fra plan_phase. Udfør dem ét ad gangen.",
    }


def _create_todo(agent: Any, text: str, parent_id: str | None = None) -> dict[str, Any]:
    """Add a new todo item to the LLM's personal plan.
    
    Skips duplicates: if an existing LLM todo has the same text, returns
    that todo's ID instead of creating a new one.
    """
    todos = _ensure_llm_todos(agent)
    # Check for duplicate text (case-insensitive)
    for existing in todos:
        if existing.get("text", "").strip().lower() == text.strip().lower():
            return {"success": True, "id": existing["id"], "text": existing["text"], "done": existing.get("done", False), "duplicate": True}
    todo_id = "lt_" + uuid.uuid4().hex[:8]
    entry = {
        "id": todo_id,
        "text": text,
        "done": False,
        "parent_id": parent_id,
        "phase": getattr(agent, 'current_phase', ''),
    }
    todos.append(entry)
    _emit(agent, "llm_todo_add", {"id": todo_id, "text": text, "parent_id": parent_id})
    return {"success": True, "id": todo_id, "text": text, "done": False}


def _update_todo(agent: Any, todo_id: str, text: str | None = None, done: bool | None = None) -> dict[str, Any]:
    """Update text or completion status of an existing LLM todo."""
    todos = _ensure_llm_todos(agent)
    for t_ in todos:
        if t_["id"] == todo_id:
            if text is not None:
                t_["text"] = text
            if done is not None:
                t_["done"] = bool(done)
            _emit(agent, "llm_todo_update", {
                "id": todo_id,
                "text": t_["text"] if text is not None else None,
                "done": t_["done"],
            })
            return {"success": True, "id": todo_id, "text": t_["text"], "done": t_["done"]}
    return {"success": False, "error": f"Todo '{todo_id}' not found"}


def _delete_todo(agent: Any, todo_id: str) -> dict[str, Any]:
    """Remove a todo from the LLM's plan."""
    todos = _ensure_llm_todos(agent)
    for i, t_ in enumerate(todos):
        if t_["id"] == todo_id:
            del agent._llm_todos[i]
            _emit(agent, "llm_todo_delete", {"id": todo_id})
            return {"success": True, "deleted": True, "id": todo_id}
    return {"success": False, "error": f"Todo '{todo_id}' not found"}


def _list_todos(agent: Any, source: str = "all") -> dict[str, Any]:
    """Return the current todo lists.

    ``source`` can be ``"auto"`` (system-generated ``_phase_todos``),
    ``"llm"`` (LLM's own todos), or ``"all"`` (default, both).
    """
    result: dict[str, Any] = {"success": True}
    if source in ("auto", "all"):
        auto = getattr(agent, '_phase_todos', None) or []
        result["auto_todos"] = list(auto)
    if source in ("llm", "all"):
        llm = _ensure_llm_todos(agent)
        result["llm_todos"] = list(llm)
    return result


# ── registration ─────────────────────────────────────────────────────────

def register_todo_tools(agent: Any) -> None:
    """Register LLM todo-management tools on the agent's tool registry.

    Args:
        agent: Agent instance with tool_registry attribute.
    """
    agent.tool_registry.register(Tool(
        "plan_phase",
        t(K.TOOL_PLAN_PHASE, agent.lang),
        ["phase_name", "phase_goal"],
        lambda phase_name, phase_goal, steps=None: _plan_phase(agent, phase_name, phase_goal, steps),
        optional_params=["steps"],
    ))

    agent.tool_registry.register(Tool(
        "create_todo",
        t(K.TOOL_CREATE_TODO, agent.lang),
        ["text"],
        lambda text, parent_id=None: _create_todo(agent, text, parent_id),
        optional_params=["parent_id"],
    ))

    agent.tool_registry.register(Tool(
        "update_todo",
        t(K.TOOL_UPDATE_TODO, agent.lang),
        ["todo_id"],
        lambda todo_id, text=None, done=None: _update_todo(agent, todo_id, text, done),
        optional_params=["text", "done"],
    ))

    agent.tool_registry.register(Tool(
        "delete_todo",
        t(K.TOOL_DELETE_TODO, agent.lang),
        ["todo_id"],
        lambda todo_id: _delete_todo(agent, todo_id),
    ))

    agent.tool_registry.register(Tool(
        "list_todos",
        t(K.TOOL_LIST_TODOS, agent.lang),
        ["source"],
        lambda source="all": _list_todos(agent, source),
        optional_params=["source"],
    ))
