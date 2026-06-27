import os
from flask import Flask, request, jsonify, send_from_directory, Response, stream_with_context
from server_config import BASE_DIR, STATIC_DIR, app, UPLOAD_DIR, sanitize_filename
from session_manager import SessionManager, _guard_json_body, agent, session_manager, current_session_id, execution_status, execution_status_lock, export_folder, export_folder_lock
from typing import Any, Generator
from lang import t, get_ui_translations
from i18n import K
from agent_files import _is_safe_path


# ============ FILHÅNDTERING ENDPOINTS ============
@app.route("/api/folder/set", methods=["POST"])
def set_folder() -> Any:
    """set folder."""
    global export_folder
    data = request.json
    folder = data.get("folder", "")
    if not folder:
        return jsonify({"success": False, "error": t(K.ERR_INVALID_PATH, agent.lang)})
    if not _is_safe_path(BASE_DIR, folder):
        return jsonify({"success": False, "error": "Adgang nægtet: stien er uden for projektmappen"}), 403
    if not os.path.exists(folder):
        try:
            os.makedirs(folder, exist_ok=True)
        except Exception as e:
            return jsonify({"success": False, "error": t(K.ERR_CREATE_FOLDER, agent.lang).format(e=str(e))})
    with export_folder_lock:
        export_folder = folder
    return jsonify({"success": True, "folder": export_folder})


@app.route("/api/folder/status", methods=["GET"])
def folder_status() -> Any:
    """folder status."""
    global export_folder
    if export_folder and os.path.exists(export_folder):
        return jsonify({"success": True, "folder": export_folder})
    return jsonify({"success": False, "error": t(K.ERR_NO_FOLDER, agent.lang), "folder": None})


@app.route("/api/folder/save", methods=["POST"])
def save_to_folder() -> Any:
    """save to folder."""
    global export_folder
    data = request.json
    filename = data.get("filename", "export.md")
    content = data.get("content", "")
    path = data.get("path") or export_folder

    if not path:
        return jsonify({"success": False, "error": t(K.ERR_NO_FOLDER, agent.lang)}), 400

    try:
        os.makedirs(path, exist_ok=True)
        safe_filename = sanitize_filename(filename)
        filepath = os.path.join(path, safe_filename)
        if not _is_safe_path(BASE_DIR, filepath):
            return jsonify({"success": False, "error": "Adgang nægtet: stien er uden for projektmappen"}), 403
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        return jsonify({"success": True, "filepath": filepath})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/folder/list", methods=["POST"])
def list_folder_contents() -> Any:
    """list folder contents."""
    data = request.json
    folder_path = data.get("path", export_folder)
    if not folder_path or not os.path.exists(folder_path):
        return jsonify({"success": False, "error": t(K.ERR_FOLDER_NOT_FOUND, agent.lang)}), 400
    if not _is_safe_path(BASE_DIR, folder_path):
        return jsonify({"success": False, "error": "Adgang nægtet: stien er uden for projektmappen"}), 403
    try:
        items = []
        for item in os.listdir(folder_path):
            full_path = os.path.join(folder_path, item)
            try:
                items.append({
                    "name": item,
                    "is_dir": os.path.isdir(full_path),
                    "size": os.path.getsize(full_path) if os.path.isfile(full_path) else 0,
                    "modified": os.path.getmtime(full_path)
                })
            except OSError:
                continue
        return jsonify({"success": True, "items": items, "current_path": folder_path})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
