from flask import Flask, request, jsonify, send_from_directory, Response, stream_with_context
from session_manager import SessionManager, _guard_json_body, agent, session_manager, current_session_id, execution_status, execution_status_lock, export_folder, export_folder_lock
import os
from typing import Any, Generator
from lang import t, get_ui_translations
from i18n import K
from agent_files import _is_safe_path
from server_config import BASE_DIR, STATIC_DIR, app, UPLOAD_DIR, sanitize_filename, _is_development_mode, _RateLimiter, rate_limiter, _rate_limit, active_streams, active_streams_lock, current_session_lock, _file_mtime, VERSION_FILES, BUILD_INFO
from refactoring_engine import clear_extracted_registry
from image_handler import _normalize_images
import time
import threading
from datetime import datetime

_session_save_lock = threading.Lock()
_session_save_debounce: dict[str, float] = {}

@app.route("/api/file/list-python", methods=["POST"])
def list_python_files() -> Any:
    """List alle Python filer i en mappe"""
    data = request.json
    folder_path = data.get("folder", BASE_DIR)

    if not os.path.exists(folder_path):
        return jsonify({"success": False, "error": t(K.ERR_FOLDER_NOT_FOUND, agent.lang)}), 404

    if not _is_safe_path(BASE_DIR, folder_path):
        return jsonify({"success": False, "error": "Adgang nægtet: stien er uden for projektmappen"}), 403

    try:
        python_files = []
        for root, dirs, files in os.walk(folder_path, followlinks=False):
            for file in files:
                if file.endswith('.py'):
                    full_path = os.path.join(root, file)
                    if not os.path.realpath(full_path).startswith(BASE_DIR + os.sep):
                        continue
                    rel_path = os.path.relpath(full_path, BASE_DIR)
                    python_files.append({
                        "name": file,
                        "path": full_path,
                        "rel_path": rel_path,
                        "size": os.path.getsize(full_path)
                    })
        return jsonify({"success": True, "files": python_files, "folder": folder_path})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/sessions", methods=["GET"])
def list_sessions() -> Any:
    """list all sessions."""
    sessions = session_manager.list_sessions()
    return jsonify({"success": True, "sessions": sessions})


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
    clear_extracted_registry()  # nulstil extraction-register til ny session
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
        # Restore LLM todos from session
        llm_todos = session_data.get("llm_todos")
        if llm_todos is not None:
            agent._llm_todos = llm_todos
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


@app.route("/api/sessions/save", methods=["POST"])
def save_current_session() -> Any:
    """save current session."""
    global current_session_id
    data = request.json
    session_id = data.get("session_id", current_session_id)

    if not session_id:
        return jsonify({"success": False, "error": t(K.ERR_NO_SESSION, agent.lang)}), 400
    now = time.time()
    with _session_save_lock:
        last = _session_save_debounce.get(session_id, 0)
        if now - last < 0.5:
            return jsonify({"success": True, "debounced": True})
        _session_save_debounce[session_id] = now
    with active_streams_lock:
        stream_agent = active_streams.get(session_id)
    source = stream_agent if stream_agent else agent

    def _merge_session(existing: dict) -> dict:
        existing_agent_log = existing.get("agent_log", [])
        existing_timestamps = {e.get("timestamp") for e in existing_agent_log}
        merged_log = existing_agent_log + [
            e for e in (source.agent_log or [])
            if e.get("timestamp") not in existing_timestamps
        ]
        existing.update({
            "id": session_id,
            "name": data.get("name", existing.get("name", t(K.SESSION_DEFAULT_NAME, agent.lang).format(n=session_id[:8]))),
            "tree": data.get("tree") or existing.get("tree") or (source.task_tree_to_dict() if source.task_tree else None),
            "layout": data.get("layout") or existing.get("layout"),
            "execution_log": source.execution_log or existing.get("execution_log", []),
            "agent_log": merged_log,
            "original_prompt": data.get("original_prompt") or source.original_prompt or "",
            "full_prompt_with_context": getattr(source, 'full_prompt_with_context', '') or '',
            "show_thinking": data.get("show_thinking", source.show_thinking),
            "template": data.get("template") or getattr(source, 'active_template', None) or "fri",
            "lang": data.get("lang") or getattr(source, 'lang', 'da'),
            "ui_lang": data.get("ui_lang") or data.get("lang") or getattr(source, 'lang', 'da'),
            "prompt_history": data.get("prompt_history") or existing.get("prompt_history", []),
            "file_context": data.get("file_context", ""),
            "file_chunks": getattr(source, 'file_chunks', None) or existing.get("file_chunks", {}),
            "images": getattr(source, 'images', None) or existing.get("images", []),
            "created": existing.get("created", datetime.now().isoformat()),
            "learned_knowledge": existing.get("learned_knowledge", []),
            "llm_todos": getattr(source, '_llm_todos', None) or existing.get("llm_todos"),
            "decompose_model": data.get("decompose_model") or existing.get("decompose_model") or getattr(source.decompose_llm, 'model', ''),
            "execute_model": data.get("execute_model") or existing.get("execute_model") or getattr(source.llm, 'model', ''),
        })
        return existing
    session_manager.update_session(session_id, _merge_session)
    current_session_id = session_id
    return jsonify({"success": True, "session_id": session_id})


@app.route("/api/sessions/rename", methods=["POST"])
def rename_session() -> Any:
    """rename session."""
    data = request.json
    session_id = data.get("session_id")
    new_name = data.get("name", "")
    if not session_id or not new_name:
        return jsonify({"success": False, "error": t(K.ERR_MISSING_SESSION, agent.lang)}), 400
    if session_manager.rename_session(session_id, new_name):
        return jsonify({"success": True})
    return jsonify({"success": False, "error": t(K.ERR_SESSION_NOT_FOUND, agent.lang)}), 404


@app.route("/api/sessions/<session_id>", methods=["DELETE"])
def delete_session(session_id: str) -> Any:
    """delete session.

    Args:
        session_id:"""
    global current_session_id
    if not session_id:
        return jsonify({"success": False, "error": t(K.ERR_MISSING_SESSION, agent.lang)}), 400
    if session_manager.delete_session(session_id):
        if current_session_id == session_id:
            current_session_id = None
        return jsonify({"success": True, "deleted": session_id})
    return jsonify({"success": False, "error": t(K.ERR_SESSION_NOT_FOUND, agent.lang)}), 404



@app.route("/api/sessions/clear-execution", methods=["POST"])
def clear_execution_state() -> Any:
    """Clear execution-related state from the current session.

    Called before undo to prevent stale execution data (todos, logs,
    task tree) from persisting after git stash pop + reload.
    """
    global current_session_id
    if not current_session_id:
        return jsonify({"success": False, "error": "Ingen aktiv session"}), 400
    session_data = session_manager.load_session(current_session_id) or {}
    # Remove execution artifacts but keep the prompt/tree structure
    session_data.pop("agent_log", None)
    session_data.pop("execution_log", None)
    session_data.pop("tool_log", None)
    session_data.pop("llm_todos", None)
    session_data["issue_resolved"] = False
    session_manager.save_session(current_session_id, session_data)
    # Also reset in-memory state on global agent
    agent.agent_log = []
    agent.execution_log = []
    if hasattr(agent, '_llm_todos'):
        del agent._llm_todos
    return jsonify({"success": True})
