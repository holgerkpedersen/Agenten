"""Agenten REST API server."""

from flask import Flask, request, jsonify, send_from_directory, Response, stream_with_context
from flask_cors import CORS
from agent_core import Agent
from session_manager import SessionManager, _guard_json_body, agent, session_manager, current_session_id, execution_status, execution_status_lock, export_folder, export_folder_lock, list_sessions, create_session, rename_session, delete_session, manage_token, get_lang, user_reply, save_layout, load_layout, get_session_prompts, get_context_for_prompt, add_prompt_to_session, reset_execution, execute_without_stream, _extract_batch_results, _extract_retry_context, _build_retry_lessons, _save_session_data
import agent_skills
import model_manager
import config
import json
import time
import threading
import os
import hmac
import tempfile
from datetime import datetime
from typing import Any, Generator
from lang import t, get_ui_translations
from i18n import K
from agent_files import _is_safe_path
from agent_phase_checks import TEMPLATE_PHASE_CHECKS, check_phase_done
from config import get_logger
import agent_issues
import agent_autoresearch
from middleware import log, BASE_DIR, STATIC_DIR, app, _is_development_mode, _RateLimiter, rate_limiter, _rate_limit
from stream_manager import active_streams, active_streams_lock, current_session_lock, _file_mtime, VERSION_FILES, BUILD_INFO, serve_upload, preview_export
from file_handler import index, flow_page, serve_static, set_folder, folder_status, save_to_folder, list_folder_contents, UPLOAD_DIR
from image_handler import sanitize_filename, _validate_image_content, _normalize_images, image_upload, image_list, image_clear, image_remove, list_python_files
from model_manager import load_session, save_current_session, get_models, set_model, loaded_models, load_model_route, unload_model_route, stop_execution, _ensure_model_loaded, TEMPLATE_GUIDANCE, _validate_template_prompt, decompose, redecompose, _count_tasks, _check_client, _count_source_symbols, autoresearch_events, autoresearch_sessions, autoresearch_pause, autoresearch_resume, autoresearch_toggle, autoresearch_status, autoresearch_run
from templates import list_templates, autoresearch_filters, autoresearch_all_sessions, autoresearch_run_from_phase
from issues import list_issues, delete_issue, create_issue_from_ui
from handlers import status, _format_phase_check_description, phase_checks, update_task_status, test

config.setup_logging()

os.makedirs(STATIC_DIR, exist_ok=True)
CORS(app, resources={r"/api/*": {"origins": os.environ.get('CORS_ORIGINS', 'http://localhost:*').split(',')}})
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50 MB max upload
BUILD_INFO["started"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

log.info("Startet: %s | api_server=%s | llm=%s", BUILD_INFO['started'], BUILD_INFO['api_server.py'], BUILD_INFO['llm_wrapper.py'])

# ============ FIL-LÆSNING ENDPOINTS ============
import tempfile
os.makedirs(UPLOAD_DIR, exist_ok=True)


from llm_wrapper import LMStudioWrapper


# ============ REGISTER EXTRACTED ROUTES ============
from routes import upload_file, read_file, view_file, get_current_session, search, flow_search, flow_generate, build_module, version
from api_git import git_backup, git_reset
from api_skillflow import skillflow_report, skillflow_apply, skillflow_status

app.add_url_rule('/api/file/upload', 'upload_file', upload_file, methods=['POST'])
app.add_url_rule('/api/file/read', 'read_file', read_file, methods=['POST'])
app.add_url_rule('/api/file/view', 'view_file', view_file, methods=['POST'])
app.add_url_rule('/api/sessions/current', 'get_current_session', get_current_session, methods=['GET'])
app.add_url_rule('/api/git/backup', 'git_backup', git_backup, methods=['POST'])
app.add_url_rule('/api/git/reset', 'git_reset', git_reset, methods=['POST'])
app.add_url_rule('/skillflow', 'skillflow_report', skillflow_report, methods=['GET'])
app.add_url_rule('/api/skillflow/apply', 'skillflow_apply', skillflow_apply, methods=['GET'])
app.add_url_rule('/api/skillflow/status', 'skillflow_status', skillflow_status, methods=['GET'])


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description='Agenten API Server')
    parser.add_argument('--workdir', '-w', type=str, default='',
                        help='Arbejdsmappe for filoperationer (f.eks. stien til projektet agenten skal arbejde på)')
    args = parser.parse_args()
    if args.workdir:
        workdir_abs = os.path.abspath(args.workdir)
        os.environ['AGENT_WORKDIR'] = workdir_abs
        log.info("Arbejdsmappe: %s", workdir_abs)
        session_manager = SessionManager(storage_dir=os.path.join(workdir_abs, 'sessions'))
    else:
        # Clear stale AGENT_WORKDIR from previous session.
        # auto_detect_workdir will NOT override because
        # AGENT_WORKDIR is not set at startup time.
        os.environ.pop('AGENT_WORKDIR', None)

    _sessions_dir = session_manager.storage_dir
    started = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log.info("=" * 50)
    log.info("Dansk Agent API starter...")
    log.info("Startet: %s", started)
    log.info("http://localhost:5000")
    log.info("Static mappe: %s", STATIC_DIR)
    log.info("api_server=%s | agent_core=%s | llm=%s", BUILD_INFO['api_server.py'], BUILD_INFO['agent_core.py'], BUILD_INFO['llm_wrapper.py'])
    log.info("Sessions gemmes i: %s", _sessions_dir)
    log.info("Filhåndtering via Python (tkinter)")
    if args.workdir:
        log.info("Målprojekt: %s", workdir_abs)
    log.info("=" * 50)
    debug = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    app.run(debug=debug, use_reloader=False, port=5000, threaded=True)
