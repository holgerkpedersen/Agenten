"""Extracted route handlers for api_server.py."""

import os
from typing import Any
from flask import request, jsonify, send_from_directory

from lang import t
from i18n import K
from flask import Flask, request, jsonify, send_from_directory, Response, stream_with_context
from session_manager import SessionManager, _guard_json_body, agent, session_manager, current_session_id, execution_status, execution_status_lock, export_folder, export_folder_lock, list_sessions, create_session, rename_session, delete_session, manage_token, get_lang, user_reply, save_layout, load_layout, get_session_prompts, get_context_for_prompt, add_prompt_to_session, reset_execution, execute_without_stream
from typing import Any, Generator
from middleware import log, BASE_DIR, STATIC_DIR, app, _is_development_mode, _RateLimiter, rate_limiter, _rate_limit
from stream_manager import active_streams, active_streams_lock, current_session_lock, _file_mtime, VERSION_FILES, BUILD_INFO, serve_upload, preview_export

def index() -> Any:
    """Serve index.html from static directory."""
    from middleware import STATIC_DIR
    index_path = os.path.join(STATIC_DIR, 'index.html')
    if os.path.exists(index_path):
        return send_from_directory(STATIC_DIR, 'index.html')
    return f"""
        <!DOCTYPE html>
        <html>
        <head><title>Agenten</title></head>
        <body style="background:#0f172a; color:#e2e8f0; font-family: monospace; padding: 20px;">
            <h1>🤖 Agenten</h1>
            <p>Static mappe: {STATIC_DIR}</p>
            <p>index.html ikke fundet. Opret venligst filen i static mappen.</p>
        </body>
        </html>
        """


def upload_file() -> Any:
    """Upload a file from the browser and save it with original name."""
    from session_manager import agent
    from file_handler import UPLOAD_DIR
    from image_handler import sanitize_filename
    if 'file' not in request.files:
        return jsonify({"success": False, "error": t(K.ERR_NO_FILE, agent.lang)}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({"success": False, "error": t(K.ERR_EMPTY_FILENAME, agent.lang)}), 400
    ext = os.path.splitext(file.filename)[1].lower()
    SAFE_UPLOAD_EXTS = {'.py', '.js', '.ts', '.html', '.css', '.md', '.txt', '.json', '.yaml', '.yml', '.toml', '.cfg', '.ini', '.xml', '.csv', '.env.example', '.gitignore'}
    if ext and ext not in SAFE_UPLOAD_EXTS:
        return jsonify({"success": False, "error": f"Filtypen '{ext}' er ikke tilladt. Tilladte typer: {', '.join(sorted(SAFE_UPLOAD_EXTS))}"}), 400
    try:
        safe_filename = sanitize_filename(file.filename)
        filepath = os.path.join(UPLOAD_DIR, safe_filename)
        file.save(filepath)
        return jsonify({"success": True, "filepath": filepath, "filename": safe_filename})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


def read_file() -> Any:
    """Read the content of a file."""
    from session_manager import agent
    from middleware import BASE_DIR
    from agent_files import _is_safe_path
    data = request.json
    filepath = data.get("filepath", "")
    if not filepath:
        return jsonify({"success": False, "error": t(K.ERR_NO_PATH, agent.lang)}), 400
    if not _is_safe_path(BASE_DIR, filepath):
        return jsonify({"success": False, "error": "Adgang nægtet: stien er uden for projektmappen"}), 403
    try:
        if not os.path.exists(filepath):
            return jsonify({"success": False, "error": t(K.ERR_FILE_NOT_FOUND, agent.lang).format(path=filepath)}), 404
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
        return jsonify({
            "success": True,
            "filepath": filepath,
            "filename": os.path.basename(filepath),
            "content": content,
            "size": len(content),
            "lines": len(content.split('\n'))
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


def view_file() -> Any:
    """View a file's content via POST request."""
    data = request.json
    filepath = data.get("filepath", "")
    if not filepath:
        return jsonify({"success": False, "error": "Ingen filsti angivet"}), 400
    try:
        if not os.path.exists(filepath):
            return jsonify({"success": False, "error": f"Filen findes ikke: {filepath}"}), 404
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
        return jsonify({
            "success": True,
            "filepath": filepath,
            "filename": os.path.basename(filepath),
            "content": content,
            "size": len(content),
            "lines": len(content.split('\n'))
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


def get_current_session() -> Any:
    """Get current session data."""
    from session_manager import agent, current_session_id, session_manager
    if current_session_id:
        session_data = session_manager.load_session(current_session_id)
        return jsonify({"success": True, "session": session_data})
    return jsonify({"success": False, "error": t(K.ERR_NO_SESSION, agent.lang), "session": None})


@app.route("/api/search", methods=["POST"])
def search() -> Any:
    """search."""
    data = request.json
    query = data.get("query", "")
    if not query:
        return jsonify({"success": False, "error": "No query"}), 400
    results = agent.searcher.search(query)
    return jsonify({"success": True, "search_results": results})


@app.route("/api/flow/search", methods=["POST"])
def flow_search() -> Any:
    """flow search."""
    data = request.json
    query = data.get("query", "")
    try:
        max_results = int(data.get("maxResults", 10))
    except (ValueError, TypeError):
        max_results = 10
    if not query:
        return jsonify({"success": False, "error": "No query"}), 400

    from ddg_search import websearch
    results = websearch(query, max_results)
    return jsonify({"success": True, "query": query, "results": results})



@app.route("/api/flow/generate", methods=["POST"])
def flow_generate() -> Any:
    """flow generate."""
    data = request.json
    topic = data.get("topic", "")
    try:
        max_results = int(data.get("maxResults", 10))
    except (ValueError, TypeError):
        max_results = 10

    if not topic:
        return jsonify({"success": False, "error": "No topic"}), 400

    from ddg_search import websearch
    from flow_builder import generate_research_flow, flow_to_mermaid_full, format_flow_json

    results = websearch(topic, max_results)
    flow = generate_research_flow(topic, results)
    mermaid = flow_to_mermaid_full(flow)
    flow_str = format_flow_json(flow)

    return jsonify({
        "success": True,
        "topic": topic,
        "results": results,
        "flow": flow,
        "flow_json": flow_str,
        "mermaid": mermaid
    })



@app.route("/api/build-module", methods=["POST"])
def build_module() -> Any:
    """build module."""
    result = agent.suggest_new_module()
    return jsonify({"success": True, "module_result": result})


@app.route("/api/version", methods=["GET"])
def version() -> Any:
    """version."""
    return jsonify({"success": True, "started": BUILD_INFO.get("started", "?"), "version": {k:v for k,v in BUILD_INFO.items() if k != "started"}})
