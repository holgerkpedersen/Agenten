from flask import Flask, request, jsonify, send_from_directory, Response, stream_with_context
from config import app
from typing import Any, Generator
import agent_autoresearch
from session_manager import SessionManager, _guard_json_body, agent, session_manager, execution_status, execution_status_lock, export_folder_lock

@app.route("/api/autoresearch/events/<research_id>", methods=["GET"])
def autoresearch_events(research_id: str) -> Any:
    """Return autoresearch events since a timestamp."""
    since = request.args.get("since", "0")
    try:
        since_f = float(since)
    except (ValueError, TypeError):
        since_f = 0.0
    events = agent_autoresearch.get_events(research_id, since_f)
    return jsonify({"success": True, "events": events})



@app.route("/api/autoresearch/sessions", methods=["GET"])
def autoresearch_sessions() -> Any:
    """Return active autoresearch sessions."""
    sessions = agent_autoresearch.get_active_sessions()
    return jsonify({"success": True, "sessions": sessions})



@app.route("/api/autoresearch/<research_id>/pause", methods=["POST"])
def autoresearch_pause(research_id: str) -> Any:
    """Pause a running autoresearch session."""
    ok = agent_autoresearch.pause_session(research_id)
    if ok:
        return jsonify({"success": True, "status": "paused"})
    return jsonify({"success": False, "error": "Session not found or not running"}), 404



@app.route("/api/autoresearch/<research_id>/resume", methods=["POST"])
def autoresearch_resume(research_id: str) -> Any:
    """Resume a paused autoresearch session."""
    ok = agent_autoresearch.resume_session(research_id)
    if ok:
        return jsonify({"success": True, "status": "running"})
    return jsonify({"success": False, "error": "Session not found or not paused"}), 404



@app.route("/api/autoresearch/toggle", methods=["POST"])
def autoresearch_toggle() -> Any:
    """Enable or disable autoresearch."""
    data = request.json or {}
    enabled = bool(data.get("enabled", False))
    agent.autoresearch_enabled = enabled
    agent._log("AUTOR", f"Auto-research {'aktiveret' if enabled else 'deaktiveret'}", "")
    return jsonify({"success": True, "autoresearch_enabled": enabled})



@app.route("/api/autoresearch/status", methods=["GET"])
def autoresearch_status() -> Any:
    """Return whether autoresearch is enabled."""
    return jsonify({
        "success": True,
        "autoresearch_enabled": getattr(agent, "autoresearch_enabled", False),
    })



@app.route("/api/autoresearch/run/<issue_id>", methods=["POST"])
def autoresearch_run(issue_id: str) -> Any:
    """Manually start autoresearch for an issue."""
    agent_autoresearch.start_research_for_issue(agent, issue_id)
    return jsonify({"success": True, "issue_id": issue_id})



@app.route("/api/autoresearch/filters", methods=["GET", "POST"])
def autoresearch_filters() -> Any:
    """Get or set autoresearch filters."""
    if request.method == "POST":
        data = request.json or {}
        current = getattr(agent, "autoresearch_filters", {}) or {}
        if "types" in data:
            current["types"] = data["types"]
        if "templates" in data:
            current["templates"] = data["templates"]
        if "failure_types" in data:
            current["failure_types"] = data["failure_types"]
        agent.autoresearch_filters = current
        agent._log("AUTOR", "Auto-research filtre opdateret", str(current))
    return jsonify({
        "success": True,
        "autoresearch_enabled": getattr(agent, "autoresearch_enabled", False),
        "filters": getattr(agent, "autoresearch_filters", {}),
    })



@app.route("/api/autoresearch/sessions/all", methods=["GET"])
def autoresearch_all_sessions() -> Any:
    """Return all research sessions (active + completed)."""
    sessions = agent_autoresearch.get_all_sessions()
    return jsonify({"success": True, "sessions": sessions})



@app.route("/api/autoresearch/run-from-phase", methods=["POST"])
def autoresearch_run_from_phase() -> Any:
    """Start auto-research from a failed phase in the tree.

    Creates a CORE-issue for the failed phase using the auto-research
    issue builder. Does NOT execute inline (that happens automatically
    during SSE streaming via _finalize_task_stream).
    """
    data = request.json or {}
    phase = data.get("phase", "ukendt")
    template = data.get("template", "ukendt")

    # Use trigger_if_needed to create a properly classified CORE issue
    # with dedup and rate-limiting. Pass minimal context; the automatic
    # inline flow handles the full classification during SSE execution.
    from unittest.mock import MagicMock
    task_node = MagicMock()
    task_node.status = "failed"
    task_node.name = phase

    issue_id = agent_autoresearch.trigger_if_needed(
        agent, task_node, {}, "", []
    )

    issue_data = None
    if issue_id:
        try:
            from agent_issues import _load_issues as _li
            for i in _li().get("issues", []):
                if i.get("id") == issue_id:
                    issue_data = i
                    break
        except Exception:
            pass

    return jsonify({
        "success": bool(issue_id),
        "issue_id": issue_id,
        "issue": issue_data,
    })
