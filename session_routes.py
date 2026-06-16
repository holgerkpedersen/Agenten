from flask import Flask, request, jsonify, send_from_directory, Response, stream_with_context


@app.route("/api/sessions", methods=["GET"])
def list_sessions() -> Any:
    """list all sessions."""
    sessions = session_manager.list_sessions()
    return jsonify({"success": True, "sessions": sessions})

from lang import t, get_ui_translations
from i18n import K


@app.route("/api/sessions/create", methods=["POST"])
def create_session() -> Any:
    """create session."""
    data = request.json
    name = data.get("name", t(K.SESSION_DEFAULT_NAME, agent.lang).format(n=len(session_manager.list_sessions())+1))
    session_id, session_data = session_manager.create_session(name)
    global current_session_id
    current_session_id = session_id
    with agent.images_lock:
        agent.images = []  # clear images from previous session
    return jsonify({"success": True, "session_id": session_id, "session": session_data})


@app.route("/api/sessions/load/<session_id>", methods=["GET"])
def load_session(session_id: str) -> Any:
    """load session.
    
    Args:
        session_id:"""
    global current_session_id
    agent.agent_log = []
    agent.execution_log = []
    session_data = session_manager.load_session(session_id)
    if session_data:
        current_session_id = session_id
        if session_data.get("tree"):
            from task_tree import TaskTree, TaskNode
            agent.task_tree = TaskTree(session_data.get("original_prompt", ""))
            agent.original_prompt = session_data.get("original_prompt", "")
            agent.full_prompt_with_context = session_data.get("full_prompt_with_context", "")
            agent.show_thinking = session_data.get("show_thinking", True)
            if session_data.get("decompose_model"):
                agent.decompose_llm.set_model(session_data["decompose_model"])
            if session_data.get("execute_model"):
                agent.llm.set_model(session_data["execute_model"])
        with agent.images_lock:
            agent.images = _normalize_images(session_data.get("images", []))
        from agent_files import auto_detect_workdir
        auto_detect_workdir(session_data.get("file_chunks"), session_data.get("original_prompt", ""))
        # Re-validate prompt against current code — append fresh VALIDERING entry
        agent.lang = session_data.get("lang", agent.lang)
        prompt_text = session_data.get("original_prompt", "") or ""
        if prompt_text:
            from agent_core import _validate_prompt_against_code
            note = _validate_prompt_against_code(agent, prompt_text)
            fresh_logs = list(agent.agent_log)  # _validate_prompt_against_code appends to agent
            agent.agent_log = []
            existing_timestamps = {e.get("timestamp") for e in (session_data.get("agent_log") or [])}
            session_data["agent_log"] = (session_data.get("agent_log") or []) + [
                e for e in fresh_logs if e.get("timestamp") not in existing_timestamps
            ]
        return jsonify({"success": True, "session": session_data})
    return jsonify({"success": False, "error": t(K.ERR_SESSION_NOT_FOUND, agent.lang)}), 404
