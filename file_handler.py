from flask import Flask, request, jsonify, send_from_directory, Response, stream_with_context
import os
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

from autoresearch import autoresearch_events, f, autoresearch_sessions, autoresearch_pause, autoresearch_resume, autoresearch_toggle, autoresearch_status, autoresearch_run


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



def sanitize_filename(filename: str) -> str:
    """Sanitize a filename by removing potentially dangerous characters."""
    if not filename:
        return ""
    # Keep alphanumeric and safe punctuation (., -, _)
    result = "".join(c for c in filename if c.isalnum() or c in '._- ')
    # Replace spaces with underscores for URL safety
    return result.replace(' ', '_')



def _validate_image_content(file_bytes: bytes, ext: str) -> bool:
    """Validate that file_bytes contains actual image content matching ext."""
    for magic, fmt in _IMAGE_MAGIC_BYTES.items():
        if file_bytes.startswith(magic):
            if fmt == 'webp':
                return len(file_bytes) > 12 and file_bytes[8:12] == b'WEBP'
            if fmt == 'jpg':
                return ext in ('.jpg', '.jpeg')
            return True
    return False



def _normalize_images(images: list[dict]) -> list[dict]:
    """Convert url-safe base64 back to standard base64 for browser compatibility."""
    import base64
    for img in images:
        if isinstance(img, dict):
            b64 = img.get("b64", "")
            if not isinstance(b64, str):
                continue
            if b64 and "/" not in b64 and "+" not in b64 and ("-" in b64 or "_" in b64):
                try:
                    decoded = base64.urlsafe_b64decode(b64)
                    img["b64"] = base64.b64encode(decoded).decode("utf-8")
                except Exception as e:
                    log.warning("Failed to normalize image: %s", e)
    return images


# ============ STATIC ROUTES ============
@app.route("/uploads/<path:filename>")
def serve_upload(filename: str) -> Any:
    """serve upload.
    
    Args:
        filename:"""
    filepath = os.path.join(UPLOAD_DIR, filename)
    if not _is_safe_path(UPLOAD_DIR, filepath):
        return "<h2>Access denied</h2>", 403
    return send_from_directory(UPLOAD_DIR, filename)

    f = request.files['image']

from datetime import datetime


# ============ VERSION ============
def _file_mtime(path: str) -> str:
    """file mtime.
    
    Args:
        path:"""
    try: return datetime.fromtimestamp(os.path.getmtime(os.path.join(BASE_DIR, path))).strftime("%H:%M:%S")
    except OSError: return "?"

    f = request.files['image']
