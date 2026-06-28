from flask import Flask, request, jsonify, send_from_directory, Response, stream_with_context
from config import app
from session_manager import SessionManager, _guard_json_body, agent, session_manager, execution_status, execution_status_lock, export_folder_lock
from typing import Any, Generator
from lang import t, get_ui_translations
from i18n import K

@app.route("/api/sessions/save-layout", methods=["POST"])
def save_layout() -> Any:
    """save layout."""
    data = request.json
    session_id = data.get("session_id")
    layout = data.get("layout")
    if not session_id:
        return jsonify({"success": False, "error": t(K.ERR_NO_SESSION_ID, agent.lang)}), 400
    def _update_layout(data: dict) -> dict:
        data["layout"] = layout
        return data
    if session_manager.update_session(session_id, _update_layout):
        return jsonify({"success": True})
    return jsonify({"success": False, "error": t(K.ERR_SESSION_NOT_FOUND, agent.lang)}), 404


@app.route("/api/sessions/load-layout/<session_id>", methods=["GET"])
def load_layout(session_id: str) -> Any:
    """load layout.

    Args:
        session_id:"""
    session_data = session_manager.load_session(session_id)
    if session_data and "layout" in session_data:
        return jsonify({"success": True, "layout": session_data["layout"]})
    return jsonify({"success": False, "error": t(K.ERR_LAYOUT_NOT_FOUND, agent.lang), "layout": None}), 404


@app.route("/api/sessions/prompts/<session_id>", methods=["GET"])
def get_session_prompts(session_id: str) -> Any:
    """get session prompts.

    Args:
        session_id:"""
    prompts = session_manager.get_prompt_history(session_id)
    return jsonify({"success": True, "prompts": prompts})


@app.route("/api/sessions/context", methods=["POST"])
def get_context_for_prompt() -> Any:
    """get context for prompt."""
    data = request.json
    session_id = data.get("session_id", session_manager.current_session_id)
    prompt = data.get("prompt", "")
    if session_id and prompt:
        context = session_manager.get_knowledge_for_context(session_id, prompt)
        return jsonify({"success": True, "context": context})
    return jsonify({"success": False, "error": t(K.ERR_SESSION_NOT_FOUND, agent.lang), "context": ""})


@app.route("/api/sessions/add-prompt", methods=["POST"])
def add_prompt_to_session() -> Any:
    """add prompt to session."""
    data = request.json
    session_id = data.get("session_id", session_manager.current_session_id)
    prompt = data.get("prompt", "")
    result = data.get("result", "")
    tree = data.get("tree")
    if session_id and prompt:
        session_manager.add_prompt_result(session_id, prompt, result, tree)
        return jsonify({"success": True})
    return jsonify({"success": False, "error": t(K.ERR_SESSION_NOT_FOUND, agent.lang)})
