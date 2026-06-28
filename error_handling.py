from flask import Flask, request, jsonify, send_from_directory, Response, stream_with_context
from config import app, STATIC_DIR, get_logger, log
from session_manager import SessionManager, _guard_json_body, agent, session_manager, execution_status, execution_status_lock, export_folder_lock
from typing import Any, Generator
from lang import t, get_ui_translations
from i18n import K
import config

@app.route("/api/test", methods=["GET"])
def test() -> Any:
    """test."""
    return jsonify({"success": True, "status": "ok", "message": t(K.UI_API_RUNNING, agent.lang), "static_folder": STATIC_DIR, "has_agent": agent is not None})



@app.errorhandler(500)
def _log_500(e):
    import traceback
    config.log.error("500: %s", traceback.format_exc())
    return jsonify({"success": False, "error": str(e)}), 500
