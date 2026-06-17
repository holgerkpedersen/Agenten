"""Extracted route handlers for api_server.py."""

import os
from typing import Any
from flask import request, jsonify, send_from_directory

from lang import t
from i18n import K



def index() -> Any:
    """Serve index.html from static directory."""
    from api_server import STATIC_DIR
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
    from api_server import agent, UPLOAD_DIR, sanitize_filename
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
    from api_server import agent, BASE_DIR
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
    from api_server import agent, current_session_id, session_manager
    if current_session_id:
        session_data = session_manager.load_session(current_session_id)
        return jsonify({"success": True, "session": session_data})
    return jsonify({"success": False, "error": t(K.ERR_NO_SESSION, agent.lang), "session": None})
