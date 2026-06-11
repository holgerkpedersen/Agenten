"""Agenten REST API server."""

from flask import Flask, request, jsonify, send_from_directory, Response, stream_with_context
from flask_cors import CORS
from agent_core import Agent
from session_manager import SessionManager
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
from agent_phase_checks import TEMPLATE_PHASE_CHECKS
from config import get_logger
import agent_issues


log = get_logger(__name__)

config.setup_logging()

# ============ KONFIGURATION ============
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, 'static')

os.makedirs(STATIC_DIR, exist_ok=True)

app = Flask(__name__, static_folder=STATIC_DIR, static_url_path='/static')
CORS(app, resources={r"/api/*": {"origins": os.environ.get('CORS_ORIGINS', 'http://localhost:*').split(',')}})
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50 MB max upload

# ============ SECURITY CONFIGURATION ============
def _is_development_mode() -> bool:
    """Check if server is running in development mode."""
    return os.environ.get('DEV_MODE', 'true').lower() == 'true'

# ============ RATE LIMITING ============
class _RateLimiter:
    """rate limiter."""
    def __init__(self) -> None:
        """Initialize the instance.
        
        Returns:
            None"""
        self._requests: dict = {}
        self._lock: threading.Lock = threading.Lock()

    def is_allowed(self, key: str, max_requests: int = 30, window: int = 60) -> bool:
        """is allowed.
        
        Args:
            key (str):
            max_requests (int):
            window (int):
        
        Returns:
            bool"""
        now = time.time()
        with self._lock:
            bucket = self._requests.get(key, [])
            bucket = [t for t in bucket if now - t < window]
            if len(bucket) >= max_requests:
                return False
            bucket.append(now)
            self._requests[key] = bucket
        return True

rate_limiter = _RateLimiter()

@app.before_request
def _rate_limit() -> Any:
    """rate limit."""
    if not request.path.startswith('/api/'):
        return None
    if request.method == 'GET':
        limit, window = 60, 60
    else:
        limit, window = 20, 60
    client_ip = request.remote_addr or "unknown"
    key = f"{client_ip}:{request.path}"
    if not rate_limiter.is_allowed(key, limit, window):
        return jsonify({"success": False, "error": "Too many requests — prøv igen om et minut"}), 429

@app.before_request
def _guard_json_body() -> Any:
    """guard json body."""
    if request.method in ('POST', 'PUT', 'PATCH') and request.path.startswith('/api/'):
        if request.path in ('/api/upload', '/api/image/upload', '/api/file/upload', '/api/stop', '/api/git/backup', '/api/git/reset'):
            return None
        if not request.is_json:
            return jsonify({"success": False, "error": "Content-Type must be application/json"}), 400

agent = Agent()
session_manager = SessionManager(os.path.join(os.path.dirname(os.path.abspath(__file__)), "sessions"))
current_session_id = None
execution_status = {"running": False, "progress": 0, "current_task": "", "log": []}
execution_status_lock = threading.Lock()
export_folder = None
export_folder_lock = threading.Lock()
active_streams = {}
active_streams_lock = threading.Lock()
current_session_lock = threading.Lock()

# ============ VERSION ============
def _file_mtime(path: str) -> str:
    """file mtime.
    
    Args:
        path:"""
    try: return datetime.fromtimestamp(os.path.getmtime(os.path.join(BASE_DIR, path))).strftime("%H:%M:%S")
    except OSError: return "?"

VERSION_FILES = ["api_server.py", "agent_core.py", "llm_wrapper.py", "tools.py", "lang.py", "i18n.py"]
BUILD_INFO = {f: _file_mtime(f) for f in VERSION_FILES}
BUILD_INFO["started"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

log.info("Startet: %s | api_server=%s | llm=%s", BUILD_INFO['started'], BUILD_INFO['api_server.py'], BUILD_INFO['llm_wrapper.py'])

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

@app.route("/preview-exports/<path:filename>")
def preview_export(filename: str) -> Any:
    """preview export.
    
    Args:
        filename:"""
    import re
    base = export_folder or os.path.join(BASE_DIR, "exports")
    filepath = os.path.join(base, filename)
    
    # Security check: Path Traversal Prevention (SEC-013)
    if not _is_safe_path(base, filepath):
        return "<h2>Access denied</h2>", 403
    try:
        with open(filepath, encoding="utf-8") as f:
            md_content = f.read()
    except (FileNotFoundError, IOError, OSError):
        return "<h2>File not found</h2>", 404
    md_content = md_content.replace('<<<', '&lt;&lt;&lt;').replace('>>>', '&gt;&gt;&gt;')
    md_content = re.sub(r'&lt;&lt;&lt;TOOL&gt;&gt;&gt;(\{.*?\})&lt;&lt;&lt;END&gt;&gt;&gt;', r'<pre class="tool-call">&lt;&lt;&lt;TOOL&gt;&gt;&gt;\1&lt;&lt;&lt;END&gt;&gt;&gt;</pre>', md_content)
    md_content = re.sub(r'&lt;&lt;&lt;DONE&gt;&gt;&gt;(\{.*?\})&lt;&lt;&lt;END&gt;&gt;&gt;', r'<pre class="tool-result">&lt;&lt;&lt;DONE&gt;&gt;&gt;\1&lt;&lt;&lt;END&gt;&gt;&gt;</pre>', md_content)
    safe_json = json.dumps(md_content).replace('</', '<\\/')
    return f"""<!DOCTYPE html>
<html lang="da">
<head><meta charset="UTF-8"><title>{filename}</title>
<script src="https://cdn.jsdelivr.net/npm/dompurify@3/dist/purify.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
<style>
    body {{ font-family: 'Segoe UI', system-ui; max-width: 960px; margin: 40px auto; padding: 20px; background: #0f172a; color: #e2e8f0; }}
    h1 {{ border-bottom: 2px solid #334155; padding-bottom: 10px; }}
    h2 {{ border-bottom: 1px solid #334155; padding-bottom: 6px; margin-top: 28px; }}
    code {{ background: #1e293b; padding: 2px 6px; border-radius: 4px; }}
    pre {{ background: #1e293b; padding: 14px; border-radius: 8px; overflow-x: auto; }}
    pre code {{ background: none; padding: 0; }}
    .tool-call {{ border-left: 3px solid #f59e0b; background: #451a03; }}
    .tool-result {{ border-left: 3px solid #10b981; background: #064e3b; }}
    blockquote {{ border-left: 3px solid #3b82f6; padding-left: 14px; color: #94a3b8; }}
    table {{ border-collapse: collapse; width: 100%; }}
    th, td {{ border: 1px solid #334155; padding: 8px 12px; text-align: left; }}
    th {{ background: #1e293b; }}
    a {{ color: #60a5fa; }}
    img {{ max-width: 100%; border-radius: 8px; }}
</style></head>
<body><div id="content"></div>
<script>document.getElementById('content').innerHTML = DOMPurify.sanitize(marked.parse({safe_json}));</script>
</body></html>"""

@app.route("/")
def index() -> Any:
    """index."""
    index_path = os.path.join(STATIC_DIR, 'index.html')
    if os.path.exists(index_path):
        return send_from_directory(STATIC_DIR, 'index.html')
    else:
        return """
        <!DOCTYPE html>
        <html>
        <head><title>Agenten</title></head>
        <body style="background:#0f172a; color:#e2e8f0; font-family: monospace; padding: 20px;">
            <h1>🤖 Agenten</h1>
            <p>Static mappe: """ + STATIC_DIR + """</p>
            <p>index.html ikke fundet. Opret venligst filen i static mappen.</p>
        </body>
        </html>
        """

@app.route('/flow')
def flow_page() -> Any:
    """flow page."""
    return send_from_directory(STATIC_DIR, 'flow.html')

@app.route('/static/<path:path>')
def serve_static(path: str) -> Any:
    """serve static.
    
    Args:
        path:"""
    return send_from_directory(STATIC_DIR, path)

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

# ============ FIL-LÆSNING ENDPOINTS ============
import tempfile

UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)


def sanitize_filename(filename: str) -> str:
    """Sanitize a filename by removing potentially dangerous characters."""
    if not filename:
        return ""
    # Keep alphanumeric and safe punctuation (., -, _)
    result = "".join(c for c in filename if c.isalnum() or c in '._- ')
    # Replace spaces with underscores for URL safety
    return result.replace(' ', '_')


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


from llm_wrapper import LMStudioWrapper


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

@app.route("/api/tools/token", methods=["GET", "POST"])
def manage_token() -> Any:
    """manage token."""
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if request.method == "GET":
        has_token = False
        if os.path.exists(env_path):
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip().startswith("GITHUB_TOKEN="):
                        has_token = True
                        break
        return jsonify({"success": True, "exists": has_token, "has_token": has_token})
    
    data = request.json
    raw = data.get("content", "")
    # Parse token from "GITHUB_TOKEN=abc123\n" format
    token = ""
    for line in raw.splitlines():
        line = line.strip()
        if line.startswith("GITHUB_TOKEN="):
            token = line.split("=", 1)[1]
            break
    if not token:
        return jsonify({"success": False, "error": "GITHUB_TOKEN ikke fundet i indhold"}), 400
    
    # Only write GITHUB_TOKEN — never arbitrary content
    lines = []
    updated = False
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip().startswith("GITHUB_TOKEN="):
                    lines.append(f"GITHUB_TOKEN={token}\n")
                    updated = True
                else:
                    lines.append(line)
    if not updated:
        lines.append(f"GITHUB_TOKEN={token}\n")
    with open(env_path, "w", encoding="utf-8") as f:
        f.writelines(lines)
    return jsonify({"success": True, "message": "GITHUB_TOKEN gemt"})

@app.route("/api/lang/<lang>")
def get_lang(lang: str) -> Any:
    """get lang.
    
    Args:
        lang:"""
    resp = jsonify(get_ui_translations(lang))
    resp.headers['Cache-Control'] = 'no-cache'
    return resp

@app.route("/api/models")
def get_models() -> Any:
    """get models."""
    openai_models = agent.llm.list_models()
    loaded = None
    rest_models = []
    if app.config.get("TESTING") or 'opencode' in config.LLM_BASE_URL or ('localhost' not in config.LLM_BASE_URL and '127.0.0.1' not in config.LLM_BASE_URL):
        merged = sorted(openai_models)
    else:
        loaded = model_manager.get_loaded_models()
        rest_models = model_manager.get_all_rest_models()
        all_ids = set(openai_models)
        if rest_models:
            for m in rest_models:
                mid = m.get('id')
                if mid:
                    all_ids.add(mid)
        if loaded:
            for k in loaded:
                if loaded[k].get('is_loaded'):
                    all_ids.add(k)
                    for inst in loaded[k].get('loaded_instances', []):
                        all_ids.add(inst.get('id', ''))
        merged = sorted(all_ids)

    log.info("Merged models (%s): %s...", len(merged), str(merged[:10]))

    return jsonify({
        "models": merged,
        "openai_models": openai_models,
        "loaded": loaded,
        "rest_models": rest_models,
        "current": agent.llm.model,
        "decompose_model": agent.decompose_llm.model,
    })

@app.route("/api/models/set", methods=["POST"])
def set_model() -> Any:
    """set model."""
    data = request.json
    model = data.get("model", agent.llm.model)
    dtype = data.get("type", "execute")
    if dtype == "decompose":
        agent.decompose_llm.set_model(model)
    else:
        agent.llm.set_model(model)
    if current_session_id:
        def _update_model(data: dict) -> dict:
            data["decompose_model"] = agent.decompose_llm.model
            data["execute_model"] = agent.llm.model
            return data
        session_manager.update_session(current_session_id, _update_model)
    return jsonify({"success": True, "model": model, "type": dtype})

@app.route("/api/models/loaded")
def loaded_models() -> Any:
    """loaded models."""
    return jsonify(model_manager.get_loaded_models())

@app.route("/api/models/load", methods=["POST"])
def load_model_route() -> Any:
    """load model route."""
    data = request.json
    key = data.get("key", "")
    if not key:
        return jsonify({"success": False, "error": "No model key"}), 400
    ok, msg = model_manager.load_model(key)
    return jsonify({"success": ok, "error": msg if not ok else None, "message": msg})

@app.route("/api/models/unload", methods=["POST"])
def unload_model_route() -> Any:
    """unload model route."""
    data = request.json
    identifier = data.get("identifier", "--all")
    ok, msg = model_manager.unload_model(identifier)
    return jsonify({"success": ok, "message": msg})

@app.route("/api/stop", methods=["POST"])
def stop_execution() -> Any:
    """stop execution."""
    global current_session_id
    
    if current_session_id:
        with active_streams_lock:
            if current_session_id in active_streams:
                active_streams[current_session_id].stop_requested = True
                return jsonify({"success": True})
    agent.stop_requested = True
    
    return jsonify({"success": True})

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
    session_id = data.get("session_id", current_session_id)
    prompt = data.get("prompt", "")
    if session_id and prompt:
        context = session_manager.get_knowledge_for_context(session_id, prompt)
        return jsonify({"success": True, "context": context})
    return jsonify({"success": False, "error": t(K.ERR_SESSION_NOT_FOUND, agent.lang), "context": ""})

@app.route("/api/sessions/add-prompt", methods=["POST"])
def add_prompt_to_session() -> Any:
    """add prompt to session."""
    data = request.json
    session_id = data.get("session_id", current_session_id)
    prompt = data.get("prompt", "")
    result = data.get("result", "")
    tree = data.get("tree")
    if session_id and prompt:
        session_manager.add_prompt_result(session_id, prompt, result, tree)
        return jsonify({"success": True})
    return jsonify({"success": False, "error": t(K.ERR_SESSION_NOT_FOUND, agent.lang)})

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
        return jsonify({"success": True, "results": results, "total_tasks": total_tasks})
    except Exception as e:
        with execution_status_lock:
            execution_status["running"] = False
        return jsonify({"success": False, "error": str(e)}), 500

def _ensure_model_loaded(model_key: str | None) -> None:
    """ensure model loaded.
    
    Args:
        model_key:"""
    if not model_key:
        return
    if app.config.get("TESTING"):
        return
    base = config.LLM_BASE_URL
    if 'opencode' in base or ('localhost' not in base and '127.0.0.1' not in base):
        return
    loaded, matched = model_manager.is_model_loaded(model_key)
    if loaded:
        log.info("Model already loaded: %s", matched)
        return matched
    log.info("Loading model: %s...", model_key)
    ok, msg = model_manager.load_model(model_key)
    if ok:
        log.info(msg)
    else:
        log.warning(msg)


TEMPLATE_GUIDANCE = {
    "resume": {
        "keywords": ["resume", "referat", "opsummer", "analyser", "review", "beskriv", "sammenfat", "referér", "sammendrag"],
        "examples": [
            'Lav et resume af [filnavn.py]',
            'Opsummer [filnavn] i et kort referat',
            'Analyser [fil] og lav en struktureret gennemgang',
        ],
        "hint": "Vælg resume-skabelonen når du vil have en struktureret gennemgang af en bestemt fil."
    },
    "kodeanalyse": {
        "keywords": ["analyser", "kode", "gennemgå", "review", "debug", "sikkerhed", "struktur", "arkitektur", "kodekvalitet", "fejl", "sårbarhed"],
        "examples": [
            'Analyser koden i [filnavn.py]',
            'Gennemgå [fil] for fejl og sikkerhedsproblemer',
            'Review koden i [fil] og vurder kodekvaliteten',
        ],
        "hint": "Vælg kodeanalyse-skabelonen når du skal have analyseret en konkret fil eller kodebase."
    },
    "diffanalyse": {
        "keywords": ["diff", "forskel", "ændring", "change", "commit", "pull", "merge", "version", "gren", "branch"],
        "examples": [
            'Analyser forskellen mellem branch-a og branch-b',
            'Gennemgå de seneste commits og vurder risiko',
            'Sammenlign to versioner af [fil]',
        ],
        "hint": "Vælg diffanalyse-skabelonen når du sammenligner to versioner eller branches."
    },
    "agenten": {
        "keywords": ["git", "github", "commit", "push", "branch", "pull request", "pr", "workflow", "repository", "repo"],
        "examples": [
            'Opret en branch, commit, push og lav en PR',
            'Git workflow: opret branch commit push PR',
            'Commit og push mine ændringer, og opret en pull request',
        ],
        "hint": "Vælg PR Agenten-skabelonen når du skal udføre et git/github workflow."
    },
    "programmering": {
        "keywords": ["programmer", "opret", "implementer", "byg", "skriv", "kod", "app", "feature", "funktion", "system", "modul", "klasse", "program", "tool", "værktøj", "library", "bibliotek", "ret", "fix", "bug", "fejl", "compile", "ændr", "opdater", "rediger", "tilføj", "slet", "rettelse", "debug"],
        "examples": [
            'Opret en Flask-app med en health endpoint',
            'Implementer en funktion der beregner moms i Python',
            'Ret compile-fejlene i C:\\Dev\\Trading\\src\\routes\\markets.js',
            'Byg et kommandolinje-værktøj der kan søge efter filer',
        ],
        "hint": "Vælg programmeringsskabelonen når du skal designe, implementere eller rette kode i et projekt."
    },
    "python-arkitektur": {
        "keywords": ["arkitektur", "planlæg", "design", "struktur", "python", "flask", "komponent", "dokumentér", "systemoversigt", "modulopdeling"],
        "examples": [
            'Analyser [projekt] og planlæg arkitektur for en Python/Flask version',
            'Design arkitekturen for et nyt system med Flask og SQLAlchemy',
            'Planlæg komponentstruktur og dataflow for en webapp',
        ],
        "hint": "Vælg Python Arkitektur-skabelonen når du skal planlægge og dokumentere en systemarkitektur."
    },
    "billedanalyse": {
        "keywords": ["billede", "billed", "image", "screenshot", "skærmbillede", "foto", "photo", "png", "jpg", "jpeg", "analyser billed", "hvad ser du", "beskriv billedet"],
        "examples": [
            'Analyser dette skærmbillede af en fejlmeddelelse',
            'Hvad ser du på dette billede af en UI?',
            'Beskriv indholdet af dette foto og vurder kvaliteten',
        ],
        "hint": "Vælg Billedanalyse-skabelonen når du skal have analyseret et billede eller skærmbillede. Resultatet gemmes automatisk i en .md fil."
    },
    "bugfix": {
        "keywords": ["bug", "fix", "fejl", "issue", "defekt", "test", "tdd", "red", "green", "refactor", "ret", "rettelse", "patch"],
        "examples": [
            'Fix BUG-003: None crash i solve_task_stream',
            'Ret fejlen i [fil] og skriv test først',
            'Anvend TDD: skriv test, implementer fix, verificér',
        ],
        "hint": "Vælg Bugfix-skabelonen når du skal rette en kendt bug med TDD-workflow: test først → implementer → verificér."
    },
}


def _validate_template_prompt(prompt: str, template: str) -> dict:
    """validate template prompt.
    
    Args:
        prompt (str):
        template (str):
    
    Returns:
        dict"""
    if not template:
        return {"warning": "", "suggestion": "", "suggested_template": "", "matches": 0, "total": 0}
    
    if template == "fri":
        # Even with free template, check if a better template can be suggested
        try:
            from skill_loader import SkillLoader
            skills = SkillLoader.load_all()
            better = SkillLoader.suggest_template(prompt, skills)
            if better:
                better_guidance = TEMPLATE_GUIDANCE.get(better)
                hint = better_guidance["hint"] if better_guidance else ""
                warning = (
                    f"Din prompt matcher skabelonen '🐛 {better}' bedre.\n{hint}"
                )
                return {"warning": warning, "suggestion": better, "suggested_template": better, "matches": 0, "total": 0}
        except Exception as e:
            log.warning("Template validation error: %s", e)
        return {"warning": "", "suggestion": "", "suggested_template": "", "matches": 0, "total": 0}
    
    guidance = TEMPLATE_GUIDANCE.get(template)
    if not guidance:
        return {"warning": "", "suggestion": "", "suggested_template": "", "matches": 0, "total": 0}
    
    prompt_lower = prompt.lower()
    matches = sum(1 for kw in guidance["keywords"] if kw in prompt_lower)
    total = len(guidance["keywords"])
    
    if matches == 0:
        # Find better template via SkillFlow scoring
        suggested = ""
        suggested_template = ""
        try:
            from skill_loader import SkillLoader
            skills = SkillLoader.load_all()
            better = SkillLoader.suggest_template(prompt, skills)
            if better and better != template:
                suggested_template = better
                better_guidance = TEMPLATE_GUIDANCE.get(better)
                if better_guidance:
                    suggested = f"\n\nForslag: Brug skabelonen '{better}' i stedet.\n{better_guidance['hint']}"
        except Exception as e:
            log.warning("Template suggestion error: %s", e)
        
        examples = "\n".join(f"  • {ex}" for ex in guidance["examples"])
        warning = (
            f"Din prompt ligner ikke en opgave til skabelonen '{template}'.{suggested}\n\n"
            f"Eksempler på gode prompts til '{template}':\n{examples}"
        )
        return {"warning": warning, "suggestion": suggested_template, "suggested_template": suggested_template, "matches": matches, "total": total}
    
    return {"warning": "", "suggestion": "", "suggested_template": "", "matches": matches, "total": total}


@app.route("/api/decompose", methods=["POST"])
def decompose() -> Any:
    """decompose."""
    data = request.json
    prompt = data.get("prompt", "")
    session_id = data.get("session_id")
    show_thinking = data.get("show_thinking", True)
    files = data.get("files", [])
    template = data.get("template")
    lang = data.get("lang", "da")
    ui_lang = data.get("ui_lang", lang)
    
    if not prompt:
        return jsonify({"success": False, "error": t(K.ERR_NO_PROMPT, ui_lang)}), 400
    
    global current_session_id
    if session_id:
        current_session_id = session_id
    elif not current_session_id:
        current_session_id, _ = session_manager.create_session(prompt[:100])
    
    agent.show_thinking = show_thinking
    agent.lang = lang
    session_context = session_manager.get_knowledge_for_context(current_session_id, prompt)
    
    # Guard: billedanalyse needs an image
    image_warning = ""
    with agent.images_lock:
        has_images = bool(agent.images)
    if template == "billedanalyse" and not has_images and not files:
        image_warning = "🖼️  Billedanalyse kræver et billede! Upload et billede med 🖼 knappen før du kører Nedbryd."
        agent._log("WARNING", "Billedanalyse uden billede", image_warning)

    # Guard: programmering (greenfield) warns but does not block if .py files exist
    non_greenfield = False
    if template == "programmering":
        FRAMEWORK_PY = {"api_server.py", "agent_core.py", "agent_tasks.py", "agent_skills.py", "agent_files.py", "agent_issues.py", "agent_tree.py", "agent_git.py", "agent_phase_checks.py", "agent_wta.py", "core_analytics.py", "agent_logs.py", "tools.py", "i18n.py", "lang.py", "config.py", "task_tree.py", "llm_wrapper.py", "model_manager.py", "session_manager.py", "flow_builder.py", "skill_evolution.py", "skill_loader.py", "skill_tracker.py", "refactoring_engine.py", "github_wrapper.py"}
        check_dir = os.environ.get('AGENT_WORKDIR') or '.'
        existing_py = [f for f in os.listdir(check_dir) if f.endswith(".py") and f not in FRAMEWORK_PY and os.path.isfile(os.path.join(check_dir, f))]
        if existing_py:
            non_greenfield = True
            log.warning("Workdir indeholder allerede .py-filer: %s — kører programmering i vedligeholdelsestilstand", ', '.join(existing_py[:5]))
    
    # Validate prompt against selected template
    validation = _validate_template_prompt(prompt, template)
    if validation["warning"]:
        log.warning("Template warning (%s): only %s/%s keywords matched", template, validation['matches'], validation['total'])
    
    log.info("Decomposing: %s...%s", prompt[:50], f" template: {template}" if template else "")
    if files:
        log.info("With %s files", len(files))

    decompose_model = data.get("decompose_model")
    execute_model = data.get("execute_model")
    if decompose_model:
        agent.decompose_llm.set_model(decompose_model)
    if execute_model:
        agent.llm.set_model(execute_model)

    _ensure_model_loaded(agent.decompose_llm.model)

    # Reset re-decompose counter — each explicit user click is a fresh attempt
    agent._redecompose_count = 0

    try:
        tree = agent.decompose_prompt(prompt, files=files, template=template)

        def _update(data: dict) -> dict:
            data.update({
                "id": current_session_id,
                "name": prompt[:100],
                "tree": tree,
                "execution_log": agent.execution_log or data.get("execution_log", []),
                "agent_log": agent.agent_log,
                "original_prompt": agent.original_prompt,
                "full_prompt_with_context": agent.full_prompt_with_context,
                "show_thinking": agent.show_thinking,
                "template": template,
                "lang": agent.lang,
                "ui_lang": ui_lang,
                "file_context": files,
                "file_chunks": agent.file_chunks,
                "decompose_model": agent.decompose_llm.model,
                "execute_model": agent.llm.model
            })
            return data
        session_manager.update_session(current_session_id, _update)
        session_manager.add_prompt_result(current_session_id, prompt, t(K.LOG_DECOMPOSED, agent.lang), tree)
        
        return jsonify({
            "success": True, 
            "tree": tree,
            "original_prompt": agent.original_prompt,
            "session_id": current_session_id,
            "has_context": bool(session_context),
            "log": agent.agent_log[-20:] if agent.agent_log else [],
            "template_warning": validation.get("warning", ""),
            "suggested_template": validation.get("suggested_template", ""),
            "image_warning": image_warning,
        })
    except Exception as e:
        log.error("Error: %s", e)
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/redecompose", methods=["POST"])
def redecompose() -> Any:
    """Re-decompose tree in a new LLM language while preserving status/results.

    Expects: ``{"session_id": str, "lang": str}``.
    Re-reads the session's original prompt and re-runs decomposition with
    the new language, then maps old node statuses to new nodes by name.
    """
    data = request.json
    session_id = data.get("session_id")
    lang = data.get("lang", "da")

    if not session_id:
        return jsonify({"success": False, "error": t(K.ERR_NO_SESSION, lang)}), 400

    session_data = session_manager.load_session(session_id)
    if not session_data:
        return jsonify({"success": False, "error": t(K.ERR_SESSION_NOT_FOUND, lang)}), 404

    old_tree = session_data.get("tree")
    old_children = old_tree.get("children", []) if old_tree else []
    old_prompt = session_data.get("original_prompt", "")
    old_template = session_data.get("template", "fri")
    old_file_context = session_data.get("file_context", [])

    if not old_prompt:
        return jsonify({"success": False, "error": t(K.ERR_NO_PROMPT, lang)}), 400

    old_status_map: dict[str, dict] = {}
    for child in old_children:
        old_status_map[child.get("name", "").strip().lower()] = {
            "status": child.get("status", "pending"),
            "result": child.get("result"),
            "success_criteria": child.get("success_criteria", []),
        }

    agent.lang = lang
    agent.active_template = old_template

    prefix = "# RE-DECOMPOSE i nyt sprog\n"
    full_prompt = prefix + old_prompt

    try:
        new_tree = agent.decompose_prompt(full_prompt, files=old_file_context, template=old_template)
    except Exception as e:
        return jsonify({"success": False, "error": f"Fejl under gen-nedbrydning: {e}"}), 500

    new_children = new_tree.get("children", []) or []
    for child in new_children:
        name_lower = (child.get("name", "") or "").strip().lower()
        old_state = old_status_map.get(name_lower)
        if old_state:
            child["status"] = old_state["status"]
            child["result"] = old_state["result"]
            child["success_criteria"] = old_state.get("success_criteria", child.get("success_criteria", []))

    session_manager.update_session(session_id, lambda d: {**d, "tree": new_tree, "lang": lang})

    return jsonify({
        "success": True,
        "tree": new_tree,
        "lang": lang,
    })

def _count_tasks(node: Any) -> int:
    """count tasks.
    
    Args:
        node:"""
    total = 1
    for child in node.children:
        total += _count_tasks(child)
    return total


def _check_client(agent: Any) -> bool:
    """check client.
    
    Args:
        agent:"""
    return agent.stop_requested


def _execute_with_stream(node: Any, agent: Any, total_tasks: int, completed: list[int], task_context_prompt: str, show_thinking: bool, ui_lang: str, current_session_id: str | None) -> Generator[str, None, None]:
    """execute with stream.
    
    Args:
        node:
        agent:
        total_tasks:
        completed:
        task_context_prompt:
        show_thinking:
        ui_lang:
        current_session_id:
    
    Yields:
        ..."""
    global execution_status
    if _check_client(agent):
        return

    # Skip nodes already marked done/skipped (manual checkpoint)
    if node.status in ("done", "skipped"):
        skip_msg = node.result or f"Markeret som {node.status} (manuelt)"
        yield f"data: {json.dumps({'type': 'task_start', 'task': node.name, 'success_criteria': getattr(node, 'success_criteria', []), 'skipped': True})}\n\n"
        yield f"data: {json.dumps({'type': 'task_done', 'task': node.name, 'status': node.status, 'result': skip_msg})}\n\n"
        completed[0] += _count_tasks(node)
        progress = int((completed[0] / total_tasks) * 100)
        with execution_status_lock:
            execution_status["progress"] = progress
        yield f"data: {json.dumps({'type': 'progress', 'progress': progress})}\n\n"
        agent.agent_log.append({"timestamp": time.time(), "level": "INFO", "message": f"Opgave sprunget over: {node.name}", "detail": f"Allerede markeret som {node.status}"})
        yield f"data: {json.dumps({'type': 'log', 'log': agent.agent_log[-1]})}\n\n"
        return

    task_data = {'type': 'task_start', 'task': node.name}
    if hasattr(node, 'success_criteria') and node.success_criteria:
        task_data['success_criteria'] = node.success_criteria
    yield f"data: {json.dumps(task_data)}\n\n"
    with execution_status_lock:
        execution_status["current_task"] = node.name

    child_results = []
    for child in node.children:
        if _check_client(agent):
            return
        if getattr(agent, 'issue_resolved', False) and getattr(agent, 'active_template', '') != 'refactor':
            child.status = "skipped"
            skip_msg = "Skipped — issue was already resolved in an earlier phase"
            child.result = skip_msg
            child_results.append(f"- {child.name}: {skip_msg}")
            yield f"data: {json.dumps({'type': 'task_start', 'task': child.name, 'success_criteria': getattr(child, 'success_criteria', [])})}\n\n"
            yield f"data: {json.dumps({'type': 'task_done', 'task': child.name, 'status': child.status, 'result': skip_msg})}\n\n"
            agent.agent_log.append({"timestamp": time.time(), "level": "INFO", "message": f"Opgave sprunget over: {child.name}", "detail": skip_msg})
            yield f"data: {json.dumps({'type': 'log', 'log': agent.agent_log[-1]})}\n\n"
            completed[0] += _count_tasks(child)
            progress = int((completed[0] / total_tasks) * 100)
            with execution_status_lock:
                execution_status["progress"] = progress
            yield f"data: {json.dumps({'type': 'progress', 'progress': progress})}\n\n"
            continue
        yield from _execute_with_stream(child, agent, total_tasks, completed, task_context_prompt, show_thinking, ui_lang, current_session_id)
        if child.result:
            child_results.append(f"- {child.name}: {child.result}")
        # Stop execution on failed phase — don't continue to siblings
        if child.status == "failed":
            for remaining in node.children[node.children.index(child) + 1:]:
                remaining.status = "skipped"
                skip_msg = f"Skipped — forrige fase '{child.name}' fejlede"
                remaining.result = skip_msg
                yield f"data: {json.dumps({'type': 'task_start', 'task': remaining.name, 'success_criteria': getattr(remaining, 'success_criteria', [])})}\n\n"
                yield f"data: {json.dumps({'type': 'task_done', 'task': remaining.name, 'status': remaining.status, 'result': skip_msg})}\n\n"
                agent.agent_log.append({"timestamp": time.time(), "level": "INFO", "message": f"Opgave sprunget over: {remaining.name}", "detail": skip_msg})
                yield f"data: {json.dumps({'type': 'log', 'log': agent.agent_log[-1]})}\n\n"
                completed[0] += _count_tasks(remaining)
            progress = int((completed[0] / total_tasks) * 100)
            with execution_status_lock:
                execution_status["progress"] = progress
            yield f"data: {json.dumps({'type': 'progress', 'progress': progress})}\n\n"
            break

    if node.children and all(c.status in ("done", "skipped", "failed") for c in node.children):
        has_failed = any(c.status == "failed" for c in node.children)
        node.status = "failed" if has_failed else "done"
        node.result = "\n".join(child_results) if child_results else "All subtasks completed"
        completed[0] += 1
        progress = int((completed[0] / total_tasks) * 100)
        with execution_status_lock:
            execution_status["progress"] = progress
        yield f"data: {json.dumps({'type': 'progress', 'progress': progress})}\n\n"
        yield f"data: {json.dumps({'type': 'task_done', 'task': node.name, 'status': node.status, 'result': node.result[:500]})}\n\n"
        return

    node.status = "running"
    full_response = ""
    for event in agent.solve_task_stream(node, task_context_prompt):
        if _check_client(agent):
            return
        if event["type"] == "chunk":
            full_response += event["chunk"]
            yield f"data: {json.dumps({'type': 'llm_chunk', 'task': node.name, 'chunk': event['chunk']})}\n\n"
        elif event["type"] == "tool_call":
            yield f"data: {json.dumps({'type': 'tool_call', 'task': node.name, 'tool': event['tool'], 'args': event['args']})}\n\n"
        elif event["type"] == "tool_result":
            yield f"data: {json.dumps({'type': 'tool_result', 'task': node.name, 'tool': event['tool'], 'result': event['result']})}\n\n"
        elif event["type"] == "log":
            yield f"data: {json.dumps({'type': 'log', 'log': event['log']})}\n\n"
        elif event["type"] == "done":
            full_response = event["result"]
    if not full_response:
        full_response = t(K.UI_TASK_RESULT_PREFIX, ui_lang) + ": " + node.name
    if node.status == "running":
        node.status = "done"
    node.result = full_response
    if _check_client(agent):
        return
    completed[0] += 1
    progress = int((completed[0] / total_tasks) * 100)
    with execution_status_lock:
        execution_status["progress"] = progress
        execution_status["log"].append({"task": node.name, "status": node.status, "result": full_response[:200]})
    yield f"data: {json.dumps({'type': 'progress', 'progress': progress})}\n\n"
    yield f"data: {json.dumps({'type': 'task_done', 'task': node.name, 'status': node.status, 'result': full_response})}\n\n"
    agent.agent_log.append({"timestamp": time.time(), "level": "INFO", "message": t(K.UI_TASK_DONE_PREFIX, ui_lang) + ": " + node.name, "detail": full_response})
    tests_failed = getattr(agent, '_tests_failed', None)
    agent.execution_log.append({
        "timestamp": time.time(),
        "task": node.name,
        "status": "done",
        "result_length": len(full_response),
        "tests_failed": tests_failed,
    })
    yield f"data: {json.dumps({'type': 'log', 'log': agent.agent_log[-1]})}\n\n"
    if current_session_id:
        session_manager.add_prompt_result(current_session_id, node.name, full_response, None)


_session_save_debounce = {}
_session_save_lock = threading.Lock()

def _save_session_data(current_session_id: str | None, stream_agent: Any, ui_lang: str) -> None:
    """save session data.
    
    Args:
        current_session_id:
        stream_agent:
        ui_lang:"""
    if not current_session_id:
        return
    def _update(data: dict) -> dict:
        existing_agent_log = data.get("agent_log", [])
        existing_timestamps = {e.get("timestamp") for e in existing_agent_log}
        merged_log = existing_agent_log + [
            e for e in stream_agent.agent_log
            if e.get("timestamp") not in existing_timestamps
        ]
        data.update({
            "tree": stream_agent.task_tree_to_dict() if stream_agent.task_tree else data.get("tree"),
            "execution_log": stream_agent.execution_log,
            "agent_log": merged_log,
            "tool_log": stream_agent._tool_log,
            "original_prompt": stream_agent.original_prompt or (stream_agent.task_tree.root.name if stream_agent.task_tree else ""),
            "prompt_history": data.get("prompt_history", []),
            "lang": stream_agent.lang,
            "ui_lang": ui_lang,
            "template": stream_agent.active_template,
            "file_chunks": stream_agent.file_chunks,
            "images": stream_agent.images,
            "decompose_model": stream_agent.decompose_llm.model,
            "execute_model": stream_agent.llm.model,
        })
        return data
    try:
        stream_agent._wta.save()
        stream_agent._seq.save()
    except Exception:
        pass
    session_manager.update_session(current_session_id, _update)


@app.route("/api/execute-stream", methods=["GET", "POST"])
def execute_stream() -> Any:
    """execute stream.
    
    Yields:
        ..."""
    global current_session_id
    ui_lang = "da"

    # Create a session-scoped agent to avoid race conditions with concurrent SSE requests (ARC-007)
    stream_agent = Agent()
    stream_agent.llm = agent.llm
    stream_agent.decompose_llm = agent.decompose_llm
    stream_agent.searcher = agent.searcher
    stream_agent._session_id = current_session_id or "unknown"
    if current_session_id:
        os.environ['AGENT_SESSION_ID'] = current_session_id
    else:
        os.environ.pop('AGENT_SESSION_ID', None)

    log.info("Execute stream - session: %s", current_session_id)
    if current_session_id:
        session_data = session_manager.load_session(current_session_id)
        if session_data:
            st = session_data.get("show_thinking", True)
            ui_lang = session_data.get("ui_lang", session_data.get("lang", "da"))
            log.info("Session show_thinking: %s", st)
            if session_data.get("original_prompt"):
                stream_agent.original_prompt = session_data["original_prompt"]
            if session_data.get("tree"):
                stream_agent.task_tree_from_dict(session_data["tree"])
            if session_data.get("lang"):
                stream_agent.lang = session_data["lang"]
                stream_agent.tool_registry.lang = stream_agent.lang
            if session_data.get("file_chunks"):
                stream_agent.file_chunks = session_data["file_chunks"]
                from agent_files import auto_detect_workdir
                auto_detect_workdir(session_data["file_chunks"], session_data.get("original_prompt", ""))
            stream_agent.images = _normalize_images(session_data.get("images", []))
            if session_data.get("template"):
                stream_agent.active_template = session_data["template"]
                allowed = agent_skills.TEMPLATE_TOOLS.get(session_data["template"]) if session_data["template"] in agent_skills.TEMPLATE_TOOLS else None
                stream_agent.tool_registry.set_active_tools(allowed)
            if session_data.get("decompose_model"):
                stream_agent.decompose_llm.set_model(session_data["decompose_model"])
            if session_data.get("execute_model"):
                stream_agent.llm.set_model(session_data["execute_model"])

            fpc = session_data.get("full_prompt_with_context", "")
            if not fpc:
                fc = session_data.get("file_context", "")
                if fc and isinstance(fc, list):
                    file_context_content = "\n\n" + t(K.FILE_CONTEXT_HEADER, stream_agent.lang)
                    for f in fc:
                        filename = f.get('filename', t(K.UNKNOWN, stream_agent.lang))
                        content = f.get('content', '')
                        file_context_content += f"\n### {filename}\n\n```{filename}\n{content}\n```\n"
                    fpc = stream_agent.original_prompt + file_context_content
                else:
                    fpc = stream_agent.original_prompt
            stream_agent.full_prompt_with_context = fpc
            stream_agent.show_thinking = st
            log.info("Agent show_thinking set to: %s", stream_agent.show_thinking)
            stream_agent.stop_requested = False

    # Register this agent in active streams for session-scoped access (BUG-001)
    session_id = current_session_id  # capture locally to avoid race condition (BUG-011)
    if session_id:
        with active_streams_lock:
            active_streams[session_id] = stream_agent

    _ensure_model_loaded(stream_agent.llm.model)

    def generate(agent: Any) -> Generator[str, None, None]:
        """generate.
        
        Args:
            agent:
        
        Yields:
            ..."""
        global execution_status
        _ui = ui_lang
        if agent.task_tree is None:
            if session_id:
                session_data = session_manager.load_session(session_id)
                if session_data and session_data.get("tree"):
                    agent.task_tree_from_dict(session_data["tree"])
                    log.info("Tree restored from session in generate()")
            if agent.task_tree is None:
                yield f"data: {json.dumps({'type': 'error', 'message': t(K.ERR_DECOMPOSE_FIRST, _ui)})}\n\n"
                return

        original_prompt = getattr(agent, 'full_prompt_with_context', '') or agent.original_prompt
        show_thinking = getattr(agent, 'show_thinking', True)
        yield f"data: {json.dumps({'type': 'context', 'original_prompt': original_prompt, 'show_thinking': show_thinking})}\n\n"

        agent.agent_log = []
        agent.execution_log = []
        agent.issue_resolved = False
        agent.current_phase = None

        agent._log("INFO", "Nedbryd LLM", agent.decompose_llm.model if hasattr(agent, 'decompose_llm') else '?')
        agent._log("INFO", "Udfør LLM", agent.llm.model)

        for log in agent.agent_log[-10:]:
            yield f"data: {json.dumps({'type': 'log', 'log': log})}\n\n"

        MAX_CTX = 150000
        task_context_prompt = original_prompt[:MAX_CTX] + ("\n\n[... trunkeret — brug read_chunk() for at læse flere chunks ...]" if len(original_prompt) > MAX_CTX else "")

        total_tasks = _count_tasks(agent.task_tree.root)
        completed = [0]
        yield f"data: {json.dumps({'type': 'start', 'total_tasks': total_tasks})}\n\n"

        saved = False
        with execution_status_lock:
            execution_status["running"] = True
            execution_status["log"] = []
        try:
            if _check_client(agent):
                yield f"data: {json.dumps({'type': 'stopped', 'message': t(K.UI_STREAM_STOPPED, _ui)})}\n\n"
                return
            yield from _execute_with_stream(agent.task_tree.root, agent, total_tasks, completed, task_context_prompt, show_thinking, _ui, session_id)
            _save_session_data(session_id, agent, _ui)
            saved = True
            with execution_status_lock:
                execution_status["running"] = False
                execution_status["progress"] = 100
            yield f"data: {json.dumps({'type': 'complete', 'message': t(K.UI_ALL_DONE, _ui)})}\n\n"
        except Exception as e:
            if not saved:
                _save_session_data(session_id, agent, _ui)
            with execution_status_lock:
                execution_status["running"] = False
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
        finally:
            if not saved:
                _save_session_data(session_id, agent, _ui)
                with execution_status_lock:
                    execution_status["running"] = False

    return Response(stream_with_context(generate(stream_agent)), mimetype='text/event-stream', headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no', 'Connection': 'keep-alive'})

@app.route("/api/log", methods=["GET"])
def get_log() -> Any:
    """get log."""
    return jsonify({"log": agent.agent_log})

@app.route("/api/status", methods=["GET"])
def status() -> Any:
    """status."""
    with execution_status_lock:
        es = dict(execution_status)
    workdir = os.environ.get('AGENT_WORKDIR', '')
    return jsonify({
        **agent.get_agent_status(),
        "execution": es,
        "workdir": workdir
    })

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


def _format_phase_check_description(phase_name: str, spec: dict[str, Any], lang: str = "da") -> str:
    """Format a phase check spec as a human-readable description.

    Uses i18n keys (description_key) when available, falling back to
    hardcoded Danish descriptions for backward compatibility.

    Args:
        phase_name: Name of the phase (e.g. "Plan", "Ekstraher")
        spec: The check spec dict from TEMPLATE_PHASE_CHECKS
        lang: Language code (da/en/es/zh) for i18n lookups
    """
    desc_key = spec.get("description_key")
    if desc_key:
        translated = t(desc_key, lang)
        if translated != desc_key:
            return translated
    explicit = spec.get("description")
    if explicit:
        return explicit
    check_type = spec.get("type", "")
    if check_type == "file_exists":
        paths = spec.get("paths", [])
        require_all = spec.get("require_all", True)
        if len(paths) == 1:
            return t(K.PHASE_CHECK_FILE_EXISTS_SINGLE, lang).format(path=f"`{paths[0]}`")
        if require_all:
            listed = ", ".join(f"`{p}`" for p in paths)
            return t(K.PHASE_CHECK_FILE_EXISTS_ALL, lang).format(paths=listed)
        listed = ", ".join(f"`{p}`" for p in paths)
        return t(K.PHASE_CHECK_FILE_EXISTS_ANY, lang).format(paths=listed)
    if check_type == "files_from_plan":
        plan_path = spec.get("plan_path", "refactor_plan.md")
        ext = spec.get("ext", ".py")
        min_files = int(spec.get("min_files", 1))
        if min_files <= 1:
            return t(K.PHASE_CHECK_FILES_FROM_PLAN, lang).format(plan_path=f"`{plan_path}`", ext=ext)
        return t(K.PHASE_CHECK_FILES_FROM_PLAN_MIN, lang).format(min_files=min_files, plan_path=f"`{plan_path}`", ext=ext)
    if check_type == "all_of":
        sub_specs = spec.get("checks", []) or []
        sub_descs = [
            _format_phase_check_description(f"{phase_name}.{i}", sub, lang)
            for i, sub in enumerate(sub_specs)
        ]
        if sub_descs:
            joined = " \u2022 ".join(sub_descs)
            return t(K.PHASE_CHECK_ALL_OF, lang).format(checks=joined)
        return t(K.PHASE_CHECK_ALL_OF, lang).format(checks=check_type)
    if check_type == "symbols_covered":
        source = spec.get("source_file", "kildefilen")
        plan = spec.get("plan_path", "refactor_plan.md")
        return t(K.PHASE_CHECK_SYMBOLS_COVERED, lang).format(source=f"`{source}`", plan=f"`{plan}`")
    if check_type == "min_text_length":
        return t(K.PHASE_CHECK_MIN_TEXT_LENGTH, lang).format(min_chars=spec.get("min_chars", 100))
    if check_type == "tool_called":
        tools = spec.get("tools", [])
        listed = ", ".join(f"`{t}`" for t in tools)
        if spec.get("require_all"):
            return t(K.PHASE_CHECK_TOOL_CALLED_ALL, lang).format(tools=listed)
        return t(K.PHASE_CHECK_TOOL_CALLED, lang).format(tools=listed)
    if check_type == "code_contains":
        path = spec.get("path", "")
        patterns = spec.get("patterns", [])
        n = len(patterns)
        if spec.get("require_all"):
            if n == 1:
                return t(K.PHASE_CHECK_CODE_CONTAINS, lang).format(path=f"`{path}`")
            return t(K.PHASE_CHECK_CODE_CONTAINS_ALL, lang).format(n=n, path=f"`{path}`")
        if n == 1:
            return t(K.PHASE_CHECK_CODE_CONTAINS, lang).format(path=f"`{path}`")
        return t(K.PHASE_CHECK_CODE_CONTAINS_MIN, lang).format(min_matches=spec.get("min_matches", 1), n=n, path=f"`{path}`")
    if check_type == "tests_pass":
        return t(K.PHASE_CHECK_TESTS_PASS, lang)
    return t(K.PHASE_CHECK_ALL_OF, lang).format(checks=check_type)


@app.route("/api/phase-checks", methods=["GET"])
def phase_checks() -> Any:
    """Return the deterministic phase success checks for a template.

    Query: ``?template=<template_name>&lang=<lang>``. If ``lang`` omitted, defaults to ``"da"``.
    Used by the frontend to display "✓ auto-completes when..." under each phase.
    """
    from agent_phase_checks import PHASE_ALIASES

    lang = request.args.get("lang", "da").strip() or "da"

    def _build_phase_entry(phase_name: str, spec: dict[str, Any]) -> dict[str, Any]:
        entry: dict[str, Any] = {
            "spec": spec,
            "description": _format_phase_check_description(phase_name, spec, lang),
        }
        lower_name = phase_name.lower()
        aliases = PHASE_ALIASES.get(lower_name, [])
        if aliases:
            entry["aliases"] = aliases
        return entry

    def _expand_with_aliases(phases: dict[str, dict[str, Any]]) -> dict[str, Any]:
        """Return a dict with canonical keys + alias entries pointing to the same data."""
        result: dict[str, Any] = {}
        for phase_name, spec in phases.items():
            entry = _build_phase_entry(phase_name, spec)
            result[phase_name] = entry
            lower_name = phase_name.lower()
            for alias in PHASE_ALIASES.get(lower_name, []):
                result[alias] = entry
        return result

    template = request.args.get("template", "").strip()
    if template:
        phases = TEMPLATE_PHASE_CHECKS.get(template, {})
        out = {template: _expand_with_aliases(phases)}
        return jsonify({"success": True, "template": template, "phases": out})
    out: dict[str, Any] = {}
    for tmpl, phases in TEMPLATE_PHASE_CHECKS.items():
        out[tmpl] = _expand_with_aliases(phases)
    return jsonify({"success": True, "templates": out})


@app.route("/api/update-task-status", methods=["POST"])
def update_task_status() -> Any:
    """Mark a task node as done/skipped so it won't be re-executed.

    POST JSON::
        {"session_id": "...", "task_path": "0.1.2", "status": "done"}

    ``task_path`` is dot-notation into ``root.children[]`` (e.g. "0" = first task,
    "0.1" = second child of first task).  Returns the updated node name + status.
    """
    data = request.json
    if not data:
        return jsonify({"success": False, "error": "Ingen JSON-body"}), 400

    session_id = data.get("session_id") or current_session_id
    task_path = data.get("task_path", "")
    new_status = data.get("status", "done")

    if not session_id:
        return jsonify({"success": False, "error": "Ingen session"}), 400
    if new_status not in ("done", "skipped", "pending"):
        return jsonify({"success": False, "error": "Status skal v\u00e6re 'done', 'skipped' eller 'pending'"}), 400

    # Resolve agent -- prefer stream agent if active
    stream_agent = agent
    if session_id:
        with active_streams_lock:
            if session_id in active_streams:
                stream_agent = active_streams[session_id]

    # Load tree from session if needed
    if stream_agent is agent or not stream_agent.task_tree:
        session_data = session_manager.load_session(session_id)
        if session_data and session_data.get("tree"):
            stream_agent.task_tree_from_dict(session_data["tree"])
        else:
            return jsonify({"success": False, "error": "Intet tr\u00e6 i session"}), 400

    if not stream_agent.task_tree:
        return jsonify({"success": False, "error": "Intet tr\u00e6"}), 400

    # Navigate to node via dot-path
    node = stream_agent.task_tree.root
    if task_path:
        for p in task_path.split("."):
            idx = int(p)
            if node.children and idx < len(node.children):
                node = node.children[idx]
            else:
                return jsonify({"success": False, "error": f"Ugyldig sti: {task_path}"}), 400

    node.status = new_status
    if not node.result:
        node.result = f"Markeret som {new_status} (manuelt)"

    # Persist session
    if session_id:
        try:
            session_manager.save_session(session_id, {
                "id": session_id,
                "tree": stream_agent.task_tree_to_dict(),
                "file_chunks": getattr(stream_agent, "file_chunks", {}),
                "images": getattr(stream_agent, "images", []),
                "template": getattr(stream_agent, "active_template", ""),
                "lang": getattr(stream_agent, "lang", "da"),
                "ui_lang": getattr(stream_agent, "lang", "da"),
                "original_prompt": getattr(stream_agent, "original_prompt", ""),
                "full_prompt_with_context": getattr(stream_agent, "full_prompt_with_context", ""),
                "show_thinking": getattr(stream_agent, "show_thinking", True),
            })
        except Exception:
            pass

    return jsonify({"success": True, "task": node.name, "status": node.status, "path": task_path})


@app.route("/api/issues", methods=["GET"])
def list_issues() -> Any:
    """list issues."""
    issues_path = agent_issues._get_issues_path()
    if not os.path.exists(issues_path):
        return jsonify({"success": True, "meta": {"total": 0}, "issues": []})
    with open(issues_path, encoding="utf-8") as f:
        data = json.load(f)
    return jsonify({"success": True, **data})

@app.route("/api/issues/<issue_id>", methods=["DELETE"])
def delete_issue(issue_id: str) -> Any:
    """delete issue.
    
    Args:
        issue_id:"""
    issues_path = agent_issues._get_issues_path()
    if not os.path.exists(issues_path):
        return jsonify({"success": False, "error": "Issues-fil findes ikke"}), 404
    with open(issues_path, encoding="utf-8") as f:
        data = json.load(f)
    before = len(data.get("issues", []))
    data["issues"] = [i for i in data.get("issues", []) if i.get("id", "").lower() != issue_id.lower()]
    if len(data["issues"]) == before:
        return jsonify({"success": False, "error": f"Issue '{issue_id}' findes ikke"}), 404
    data["meta"]["total"] = len(data["issues"])
    with open(issues_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return jsonify({"success": True, "deleted": issue_id})

@app.route("/api/issues", methods=["POST"])
def create_issue_from_ui() -> Any:
    """create issue from UI."""
    data = request.json
    if not data or not data.get("title"):
        return jsonify({"success": False, "error": "Title er påkrævet"}), 400
    result = agent_issues.create_issue(
        agent=agent,
        title=data["title"],
        type=data.get("type", "bug"),
        severity=data.get("severity", "medium"),
        description=data.get("description", ""),
        location=data.get("location", ""),
        impact=data.get("impact", ""),
        proposed_fix=data.get("proposed_fix", ""),
        acceptance_criteria=data.get("acceptance_criteria", ""),
    )
    return jsonify(result)

@app.route("/api/test", methods=["GET"])
def test() -> Any:
    """test."""
    return jsonify({"success": True, "status": "ok", "message": t(K.UI_API_RUNNING, agent.lang), "static_folder": STATIC_DIR, "has_agent": agent is not None})


# ============ REGISTER EXTRACTED ROUTES ============
from routes import upload_file, read_file, get_current_session
from api_git import git_backup, git_reset
from api_skillflow import skillflow_report, skillflow_apply, skillflow_status

app.add_url_rule('/api/file/upload', 'upload_file', upload_file, methods=['POST'])
app.add_url_rule('/api/file/read', 'read_file', read_file, methods=['POST'])
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
