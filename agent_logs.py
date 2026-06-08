"""Session log analysis — lets the LLM inspect its own execution history."""

import json
import os
import re
from typing import Any

_SESSIONS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sessions")


def _load_session(filepath: str) -> dict[str, Any] | None:
    """Load a single session JSON file."""
    try:
        with open(filepath, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _list_session_files() -> list[str]:
    """Return paths to all session JSON files sorted by last-modified descending."""
    if not os.path.isdir(_SESSIONS_DIR):
        return []
    files = [
        os.path.join(_SESSIONS_DIR, f)
        for f in os.listdir(_SESSIONS_DIR)
        if f.endswith(".json")
    ]
    files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return files


def _count_by_level(log_entries: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for entry in log_entries:
        level = entry.get("level", "UNKNOWN")
        counts[level] = counts.get(level, 0) + 1
    return counts


def _tool_call_stats(log_entries: list[dict]) -> dict[str, Any]:
    tool_calls = 0
    tool_failures = 0
    tools_used: dict[str, int] = {}
    for entry in log_entries:
        msg = entry.get("message", "")
        detail = entry.get("detail", "")
        if entry.get("level") == "TOOL" or "TOOL" in msg or "tool" in msg:
            tool_calls += 1
            # Extract tool name from message like "🔧 Kaldte værktøj: edit_file"
            m = re.search(r'(?:v\u00e6rkt\u00f8j|tool)[:\s]+(\w+)', str(msg), re.IGNORECASE)
            if m:
                tname = m.group(1)
                tools_used[tname] = tools_used.get(tname, 0) + 1
            if "ERROR" in str(level := entry.get("level", "")) or (
                "failed" in str(detail).lower() and "success" not in str(detail).lower()
            ):
                tool_failures += 1
    return {
        "total_calls": tool_calls,
        "failures": tool_failures,
        "tools": tools_used,
    }


def _task_summary(session: dict) -> dict[str, int]:
    """Count done/failed tasks from execution_log."""
    total = 0
    failed = 0
    for entry in session.get("execution_log", []):
        total += 1
        if entry.get("status") == "failed":
            failed += 1
    return {"total": total, "failed": failed}


def _error_patterns(log_entries: list[dict], top_n: int = 5) -> list[str]:
    """Extract the most common error message patterns."""
    patterns: dict[str, int] = {}
    for entry in log_entries:
        if entry.get("level") in ("ERROR", "WARNING"):
            msg = str(entry.get("message", ""))
            detail = str(entry.get("detail", ""))
            key = (msg + ": " + detail)[:150]
            patterns[key] = patterns.get(key, 0) + 1
    sorted_pats = sorted(patterns.items(), key=lambda x: -x[1])
    return [f"[{c}x] {p}" for p, c in sorted_pats[:top_n]]


def _search_logs(log_entries: list[dict], pattern: str) -> list[dict]:
    """Filter log entries matching a case-insensitive substring or regex."""
    matches: list[dict] = []
    try:
        rx = re.compile(pattern, re.IGNORECASE)
        for entry in log_entries:
            text = str(entry.get("message", "")) + " " + str(entry.get("detail", ""))
            if rx.search(text):
                matches.append(entry)
    except re.error:
        # Fall back to simple substring
        pl = pattern.lower()
        for entry in log_entries:
            text = (str(entry.get("message", "")) + " " + str(entry.get("detail", ""))).lower()
            if pl in text:
                matches.append(entry)
    return matches


def analyze_own_logs(
    session_id: str = "",
    pattern: str = "",
    max_sessions: int = 5,
) -> dict[str, Any]:
    """Analyze session logs for error patterns and execution history.

    Args:
        session_id: Specific session UUID to analyze. If empty, scans
            the most recent sessions.
        pattern: Optional substring or regex to filter log entries.
        max_sessions: Number of recent sessions to scan when no
            ``session_id`` is given (default 5).

    Returns:
        A dict with ``success``, ``summary`` text, and optionally
        ``sessions`` list or ``session`` detail.
    """
    session_files = _list_session_files()
    if not session_files:
        return {"success": False, "error": "Ingen session-filer fundet.", "summary": ""}

    if session_id:
        # Find the specific session
        target = None
        target_path = None
        for fp in session_files:
            data = _load_session(fp)
            if data and data.get("id", "").lower() == session_id.lower():
                target = data
                target_path = fp
                break
            # Also match partial UUID
            if data and session_id.lower() in data.get("id", "").lower():
                target = data
                target_path = fp
                break
        if not target:
            return {
                "success": False,
                "error": f"Session '{session_id}' ikke fundet.",
                "summary": "",
            }
        return _analyze_single(target, target_path, pattern)

    # No session_id — summarize recent sessions
    summaries: list[dict] = []
    for fp in session_files[:max_sessions]:
        data = _load_session(fp)
        if not data:
            continue
        tasks = _task_summary(data)
        levels = _count_by_level(data.get("agent_log", []))
        summaries.append({
            "id": data.get("id", "?"),
            "name": data.get("name", "?"),
            "created": data.get("created", ""),
            "template": data.get("template") or data.get("active_template", ""),
            "model": data.get("execute_model", "?"),
            "tasks": tasks["total"],
            "failed_tasks": tasks["failed"],
            "errors": levels.get("ERROR", 0),
            "warnings": levels.get("WARNING", 0),
            "log_entries": len(data.get("agent_log", [])),
        })

    return {
        "success": True,
        "summary": f"{len(summaries)} seneste sessioner analyseret.",
        "sessions": summaries,
    }


def _analyze_single(
    session: dict[str, Any],
    filepath: str | None = None,
    pattern: str = "",
) -> dict[str, Any]:
    """Analyze a single session in depth."""
    log_entries = session.get("agent_log", [])
    levels = _count_by_level(log_entries)
    tools = _tool_call_stats(log_entries)
    tasks = _task_summary(session)
    errors = _error_patterns(log_entries)

    # Build a human-readable summary
    lines: list[str] = []
    lines.append(f"Session: {session.get('id', '?')}")
    lines.append(f"  Navn: {session.get('name', '?')}")
    lines.append(f"  Oprettet: {session.get('created', '?')}")
    lines.append(f"  Skabelon: {session.get('template') or session.get('active_template', '?')}")
    lines.append(f"  Model: {session.get('execute_model', '?')}")
    lines.append(f"  Prompt: {(session.get('original_prompt', '') or '')[:200]}")
    lines.append("")
    lines.append(f"Log entries: {len(log_entries)}")
    lines.append(f"  INFO: {levels.get('INFO', 0)}")
    lines.append(f"  ERROR: {levels.get('ERROR', 0)}")
    lines.append(f"  WARNING: {levels.get('WARNING', 0)}")
    lines.append(f"  LLM calls: {levels.get('LLM', 0)}")
    lines.append(f"  TOOL calls: {levels.get('TOOL', 0)}")
    lines.append("")
    lines.append(f"Tasks: {tasks['total']} total, {tasks['failed']} fejlede")
    lines.append(f"Tool calls: {tools['total_calls']} total, {tools['failures']} fejl")
    if tools["tools"]:
        top_tools = sorted(tools["tools"].items(), key=lambda x: -x[1])[:8]
        lines.append("  Mest brugte: " + ", ".join(f"{t}({c})" for t, c in top_tools))
    lines.append("")
    if errors:
        lines.append("Hyppigste fejl/advarsler:")
        for e in errors[:5]:
            lines.append(f"  {e}")

    summary = "\n".join(lines)

    result: dict[str, Any] = {
        "success": True,
        "summary": summary,
        "session": {
            "id": session.get("id"),
            "name": session.get("name"),
            "created": session.get("created"),
            "template": session.get("template") or session.get("active_template"),
            "model": session.get("execute_model"),
            "prompt": (session.get("original_prompt", "") or "")[:500],
            "total_logs": len(log_entries),
            "errors": levels.get("ERROR", 0),
            "warnings": levels.get("WARNING", 0),
            "total_tasks": tasks["total"],
            "failed_tasks": tasks["failed"],
        },
        "tool_stats": tools,
    }

    if pattern:
        matching = _search_logs(log_entries, pattern)
        result["matching_logs"] = [
            {
                "timestamp": e.get("timestamp"),
                "level": e.get("level"),
                "message": e.get("message"),
                "detail": (e.get("detail", "") or "")[:300],
            }
            for e in matching[:30]
        ]
        result["match_count"] = len(matching)
        result["summary"] += f"\n\nMønster '{pattern}': {len(matching)} matches."

    return result
