from config import app, get_logger, log
from flask import Flask, request, jsonify, send_from_directory, Response, stream_with_context
from folder_manager import UPLOAD_DIR, sanitize_filename
from session_manager import SessionManager, _guard_json_body, agent, session_manager, current_session_id, execution_status, execution_status_lock, export_folder, export_folder_lock
import os
from typing import Any, Generator



_IMAGE_MAGIC_BYTES = {
    b'\x89PNG\r\n\x1a\n': 'png',
    b'\xff\xd8\xff': 'jpg',
    b'GIF8': 'gif',
    b'RIFF': 'webp',
    b'BM': 'bmp',
}



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



@app.route("/api/image/upload", methods=["POST"])
def image_upload() -> Any:
    """image upload."""
    sid = current_session_id  # capture locally to avoid race (BUG-063)
    if 'image' not in request.files:
        return jsonify({"success": False, "error": "Ingen billedfil modtaget"}), 400
    f = request.files['image']
    if f.filename == '':
        return jsonify({"success": False, "error": "Intet filnavn"}), 400
    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in ('.png','.jpg','.jpeg','.gif','.webp','.bmp'):
        return jsonify({"success": False, "error": f"Ikke understøttet format: {ext}"}), 400
    raw_bytes = f.read()
    if not _validate_image_content(raw_bytes, ext):
        return jsonify({"success": False, "error": "Filens indhold matcher ikke et gyldigt billedformat"}), 400
    f.stream.seek(0)
    import base64
    mime = "jpeg" if ext in ('.jpg','.jpeg') else ext.lstrip('.')
    safe_filename = sanitize_filename(f.filename)
    filepath = os.path.join(UPLOAD_DIR, safe_filename)
    f.save(filepath)
    raw_b64 = base64.b64encode(raw_bytes).decode('utf-8')

    # Use plain list instead of creating Agent() — avoids ToolRegistry/GithubAPI overhead (PRF-005)
    new_entry = {"b64": raw_b64, "mime": mime, "filename": f.filename, "filepath": filepath}
    uploaded_images = [new_entry]
    if sid:
        def _update_images(data: dict) -> dict:
            existing_images = _normalize_images(data.get("images", []))
            images = existing_images + [new_entry]
            data["images"] = images
            return data
        session_manager.update_session(sid, _update_images)
        # Reload to get authoritative session image list
        loaded = session_manager.load_session(sid) or {}
        uploaded_images = _normalize_images(loaded.get("images", []))

    return jsonify({"success": True, "filename": f.filename, "filepath": filepath, "size": os.path.getsize(filepath), "count": len(uploaded_images)})


@app.route("/api/image/list", methods=["GET"])
def image_list() -> Any:
    """image list."""
    with agent.images_lock:
        images_copy = list(agent.images)
        total = len(images_copy)
    result = []
    for img in images_copy:
        if isinstance(img, dict):
            mime = img.get('mime','png')
            url = f"data:image/{mime};base64,{img['b64']}"
            result.append({"url": url, "filename": img.get("filename","")})
        else:
            result.append({"url": img[:80] + "...", "filename": ""})
    return jsonify({"success": True, "images": result, "count": total})


@app.route("/api/image/clear", methods=["POST"])
def image_clear() -> Any:
    """image clear."""
    with agent.images_lock:
        count = len(agent.images)
        agent.images = []
    if count:
        agent._log("TOOL", "🗑️ Billeder ryddet", f"{count} billeder fjernet")
    return jsonify({"success": True})


@app.route("/api/image/remove/<int:index>", methods=["POST"])
def image_remove(index: int) -> Any:
    """image remove.

    Args:
        index:"""
    with agent.images_lock:
        if 0 <= index < len(agent.images):
            img = agent.images.pop(index)
            name = img.get("filename", "?") if isinstance(img, dict) else "?"
            remaining = len(agent.images)
        else:
            return jsonify({"success": False, "error": "Invalid index"}), 400
    agent._log("TOOL", "✕ Billede fjernet", f"{name} (indeks {index}, {remaining} tilbage)")
    return jsonify({"success": True, "count": remaining})
