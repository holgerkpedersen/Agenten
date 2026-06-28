from flask import Flask, request, jsonify, send_from_directory, Response, stream_with_context
from config import app
from session_manager import SessionManager, _guard_json_body, agent, session_manager, execution_status, execution_status_lock, export_folder_lock
from typing import Any, Generator
from lang import t, get_ui_translations
from i18n import K
from decomposition import _ensure_model_loaded, TEMPLATE_GUIDANCE, _validate_template_prompt, decompose, redecompose, _count_tasks, _check_client
from stream_execution import _save_session_data

@app.route("/api/stop", methods=["POST"])
def stop_execution() -> Any:
    """stop execution."""
    current_session_id = session_manager.current_session_id

    if current_session_id:
        with active_streams_lock:
            if current_session_id in active_streams:
                active_streams[current_session_id].stop_requested = True
                return jsonify({"success": True})
    agent.stop_requested = True

    return jsonify({"success": True})



@app.route("/api/execute-pause", methods=["POST"])
def pause_execution() -> Any:
    """Pause execution — set pause flag so solve_task_stream saves messages."""
    current_session_id = session_manager.current_session_id
    if current_session_id:
        with active_streams_lock:
            sa = active_streams.get(current_session_id)
            if sa:
                sa.stop_requested = True
                sa._pause_requested = True
                return jsonify({"success": True})
    return jsonify({"success": False, "error": "Ingen aktiv stream at pause"})



@app.route("/api/reply", methods=["POST"])
def user_reply() -> Any:
    """user reply."""
    data = request.json
    msg = data.get("message", "")
    if not msg:
        return jsonify({"success": False, "error": "Empty message"}), 400
    agent.pending_reply = msg
    agent._log("USER", "Bruger svar", msg[:100])
    return jsonify({"success": True})


# ============ AGENT ENDPOINTS ============
@app.route("/api/reset-execution", methods=["POST"])
def reset_execution() -> Any:
    """reset execution."""
    agent.reset_execution()
    return jsonify({"success": True, "message": t(K.UI_STREAM_RESET, agent.lang)})


@app.route("/api/execute-without-stream", methods=["POST"])
def execute_without_stream() -> Any:
    """execute without stream."""
    global execution_status
    current_session_id = session_manager.current_session_id
    if agent.task_tree is None:
        return jsonify({"success": False, "error": t(K.ERR_DECOMPOSE_FIRST, agent.lang)}), 400


    total_tasks = _count_tasks(agent.task_tree.root)
    completed = 0

    def execute_with_progress(node: Any) -> str:
        """execute with progress.

        Args:
            node:"""
        nonlocal completed
        with execution_status_lock:
            execution_status["current_task"] = node.name
        for child in node.children:
            execute_with_progress(child)
        result = agent.solve_task(node, agent.original_prompt)
        completed += 1
        with execution_status_lock:
            execution_status["progress"] = int((completed / total_tasks) * 100)
            execution_status["log"].append({"task": node.name, "status": node.status, "result": result[:200]})
        return result

    try:
        results = execute_with_progress(agent.task_tree.root)
        with execution_status_lock:
            execution_status["results"] = results
            execution_status["running"] = False
        # Save session after execution so tree status is persisted
        try:
            _save_session_data(current_session_id, agent, "da")
        except Exception:
            pass
        return jsonify({"success": True, "results": results, "total_tasks": total_tasks})
    except Exception as e:
        with execution_status_lock:
            execution_status["running"] = False
        # Save session even on failure so partial progress is captured
        try:
            _save_session_data(current_session_id, agent, "da")
        except Exception:
            pass
        return jsonify({"success": False, "error": str(e)}), 500
