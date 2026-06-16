from flask import Flask, request, jsonify, send_from_directory, Response, stream_with_context
import agent_autoresearch



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

    f = request.files['image']



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
