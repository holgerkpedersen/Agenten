from flask import Flask, request, jsonify, send_from_directory, Response, stream_with_context
from session_manager import SessionManager, _guard_json_body, agent, session_manager, current_session_id, execution_status, execution_status_lock, export_folder, export_folder_lock, list_sessions, create_session, rename_session, delete_session, manage_token, get_lang, user_reply, save_layout, load_layout, get_session_prompts, get_context_for_prompt, add_prompt_to_session, reset_execution, execute_without_stream
import agent_skills
from typing import Any, Generator
from middleware import log, BASE_DIR, STATIC_DIR, app, _is_development_mode, _RateLimiter, rate_limiter, _rate_limit
import agent_autoresearch

@app.route("/api/templates", methods=["GET"])
def list_templates() -> Any:
    """Return public template list for the UI dropdown."""
    templates = agent_skills.get_templates(agent)
    internal = {"autoresearch"}
    public = {}
    for key, tpl in templates.items():
        public[key] = {
            "name": tpl.get("name", key),
            "internal": key in internal,
        }
    return jsonify({"success": True, "templates": public})



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
