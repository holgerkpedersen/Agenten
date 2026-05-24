from flask import Flask, request, jsonify, send_from_directory, Response, stream_with_context
from flask_cors import CORS
from agent_core import Agent
from session_manager import SessionManager
import agent_skills
import model_manager
import json
import time
import threading
import os
import tempfile
from datetime import datetime
from lang import t, get_ui_translations
from i18n import K

# ============ KONFIGURATION ============
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, 'static')

os.makedirs(STATIC_DIR, exist_ok=True)

app = Flask(__name__, static_folder=STATIC_DIR, static_url_path='/static')
CORS(app)

agent = Agent()
session_manager = SessionManager()
current_session_id = None
execution_status = {"running": False, "progress": 0, "current_task": "", "log": []}
export_folder = None

# ============ VERSION ============
def _file_mtime(path):
    try: return datetime.fromtimestamp(os.path.getmtime(os.path.join(BASE_DIR, path))).strftime("%H:%M:%S")
    except: return "?"

VERSION_FILES = ["api_server.py", "agent_core.py", "llm_wrapper.py", "tools.py", "lang.py", "i18n.py"]
BUILD_INFO = {f: _file_mtime(f) for f in VERSION_FILES}
BUILD_INFO["started"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

print(f"📦 Startet: {BUILD_INFO['started']} | api_server={BUILD_INFO['api_server.py']} | llm={BUILD_INFO['llm_wrapper.py']}")

# ============ STATIC ROUTES ============
@app.route("/uploads/<path:filename>")
def serve_upload(filename):
    return send_from_directory(UPLOAD_DIR, filename)

@app.route("/preview-exports/<path:filename>")
def preview_export(filename):
    import re
    base = export_folder or os.path.join(BASE_DIR, "exports")
    filepath = os.path.join(base, filename)
    
    # Security check: Path Traversal Prevention (SEC-013)
    if not _is_safe_path(base, filepath):
        return "<h2>Access denied</h2>", 403
        
    if not os.path.exists(filepath):
        return "<h2>File not found</h2>", 404
    with open(filepath, encoding="utf-8") as f:
        md_content = f.read()
    md_content = md_content.replace('<<<', '&lt;&lt;&lt;').replace('>>>', '&gt;&gt;&gt;')
    md_content = re.sub(r'&lt;&lt;&lt;TOOL&gt;&gt;&gt;(\{.*?\})&lt;&lt;&lt;END&gt;&gt;&gt;', r'<pre class="tool-call">&lt;&lt;&lt;TOOL&gt;&gt;&gt;\1&lt;&lt;&lt;END&gt;&gt;&gt;</pre>', md_content)
    md_content = re.sub(r'&lt;&lt;&lt;DONE&gt;&gt;&gt;(\{.*?\})&lt;&lt;&lt;END&gt;&gt;&gt;', r'<pre class="tool-result">&lt;&lt;&lt;DONE&gt;&gt;&gt;\1&lt;&lt;&lt;END&gt;&gt;&gt;</pre>', md_content)
    return f"""<!DOCTYPE html>
<html lang="da">
<head><meta charset="UTF-8"><title>{filename}</title>
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
<script>document.getElementById('content').innerHTML = marked.parse({md_content!r});</script>
</body></html>"""

@app.route("/")
def index():
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
def flow_page():
    return send_from_directory(STATIC_DIR, 'flow.html')

@app.route('/static/<path:path>')
def serve_static(path):
    return send_from_directory(STATIC_DIR, path)

# ============ FILHÅNDTERING ENDPOINTS ============
@app.route("/api/folder/set", methods=["POST"])
def set_folder():
    global export_folder
    data = request.json
    folder = data.get("folder", "")
    if folder and os.path.isdir(folder):
        export_folder = folder
        return jsonify({"success": True, "folder": export_folder})
    elif folder and not os.path.exists(folder):
        try:
            os.makedirs(folder, exist_ok=True)
            export_folder = folder
            return jsonify({"success": True, "folder": export_folder})
        except Exception as e:
            return jsonify({"success": False, "error": t(K.ERR_CREATE_FOLDER, agent.lang).format(e=str(e))})
    else:
        return jsonify({"success": False, "error": t(K.ERR_INVALID_PATH, agent.lang)})

@app.route("/api/folder/status", methods=["GET"])
def folder_status():
    global export_folder
    if export_folder and os.path.exists(export_folder):
        return jsonify({"success": True, "folder": export_folder})
    return jsonify({"success": False, "folder": None})

@app.route("/api/folder/save", methods=["POST"])
def save_to_folder():
    global export_folder
    data = request.json
    filename = data.get("filename", "export.md")
    content = data.get("content", "")
    path = data.get("path") or export_folder
    
    if not path:
        return jsonify({"success": False, "error": t(K.ERR_NO_FOLDER, agent.lang)}), 400
    
    try:
        os.makedirs(path, exist_ok=True)
        safe_filename = "".join(c for c in filename if c.isalnum() or c in '._- ')
        filepath = os.path.join(path, safe_filename)
        if not _is_safe_path(BASE_DIR, filepath):
            return jsonify({"success": False, "error": "Adgang nægtet: stien er uden for projektmappen"}), 403
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        return jsonify({"success": True, "filepath": filepath})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/folder/list", methods=["POST"])
def list_folder_contents():
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
            items.append({
                "name": item,
                "is_dir": os.path.isdir(full_path),
                "size": os.path.getsize(full_path) if os.path.isfile(full_path) else 0,
                "modified": os.path.getmtime(full_path)
            })
        return jsonify({"success": True, "items": items, "current_path": folder_path})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

# ============ FIL-LÆSNING ENDPOINTS ============
import tempfile

UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

def _is_safe_path(base_dir, target_path):
    """Ensures that target_path resolves within base_dir to prevent path traversal."""
    try:
        real_base = os.path.realpath(base_dir)
        # For non-existent paths (e.g., saving), resolve what we can.
        real_target = os.path.realpath(target_path) if os.path.exists(target_path) else os.path.abspath(target_path)
        return real_target.startswith(real_base + os.sep) or real_target == real_base
    except Exception:
        return False

@app.route("/api/file/upload", methods=["POST"])
def upload_file():
    """Upload en fil fra browseren og gem den med original navn"""
    if 'file' not in request.files:
        return jsonify({"success": False, "error": t(K.ERR_NO_FILE, agent.lang)}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({"success": False, "error": t(K.ERR_EMPTY_FILENAME, agent.lang)}), 400
    try:
        safe_filename = "".join(c for c in file.filename if c.isalnum() or c in '._- ')
        filepath = os.path.join(UPLOAD_DIR, safe_filename)
        file.save(filepath)
        return jsonify({"success": True, "filepath": filepath, "filename": file.filename})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/file/read", methods=["POST"])
def read_file():
    """Læs indholdet af en fil"""
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


from llm_wrapper import LMStudioWrapper

def _normalize_images(images):
    """Convert url-safe base64 back to standard base64 for browser compatibility."""
    import base64
    for img in images:
        if isinstance(img, dict):
            b64 = img.get("b64", "")
            if b64 and "/" not in b64 and "+" not in b64 and ("-" in b64 or "_" in b64):
                try:
                    decoded = base64.urlsafe_b64decode(b64)
                    img["b64"] = base64.b64encode(decoded).decode("utf-8")
                except Exception:
                    pass
    return images


@app.route("/api/image/upload", methods=["POST"])
def image_upload():
    if 'image' not in request.files:
        return jsonify({"success": False, "error": "Ingen billedfil modtaget"}), 400
    f = request.files['image']
    if f.filename == '':
        return jsonify({"success": False, "error": "Intet filnavn"}), 400
    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in ('.png','.jpg','.jpeg','.gif','.webp','.bmp'):
        return jsonify({"success": False, "error": f"Ikke understøttet format: {ext}"}), 400
    import base64
    mime = "jpeg" if ext in ('.jpg','.jpeg') else ext.lstrip('.')
    safe_filename = "".join(c for c in f.filename if c.isalnum() or c in '._- ')
    filepath = os.path.join(UPLOAD_DIR, safe_filename)
    f.save(filepath)
    with open(filepath, "rb") as bf:
        raw_b64 = base64.b64encode(bf.read()).decode('utf-8')
    agent.images.append({"b64": raw_b64, "mime": mime, "filename": f.filename, "filepath": filepath})
    agent._log("TOOL", "🖼️ Billede uploadet", f"{f.filename} → {filepath} ({len(raw_b64)} bytes, {len(agent.images)} billeder i alt)")
    return jsonify({"success": True, "filename": f.filename, "filepath": filepath, "size": os.path.getsize(filepath), "count": len(agent.images)})

@app.route("/api/image/list", methods=["GET"])
def image_list():
    result = []
    for img in agent.images:
        if isinstance(img, dict):
            mime = img.get('mime','png')
            url = f"data:image/{mime};base64,{img['b64']}"
            result.append({"url": url, "filename": img.get("filename","")})
        else:
            result.append({"url": img[:80] + "...", "filename": ""})
    return jsonify({"success": True, "images": result, "count": len(agent.images)})

@app.route("/api/image/clear", methods=["POST"])
def image_clear():
    count = len(agent.images)
    agent.images = []
    if count:
        agent._log("TOOL", "🗑️ Billeder ryddet", f"{count} billeder fjernet")
    return jsonify({"success": True})

@app.route("/api/image/remove/<int:index>", methods=["POST"])
def image_remove(index):
    if 0 <= index < len(agent.images):
        img = agent.images.pop(index)
        name = img.get("filename", "?") if isinstance(img, dict) else "?"
        agent._log("TOOL", "✕ Billede fjernet", f"{name} (indeks {index}, {len(agent.images)} tilbage)")
        return jsonify({"success": True, "count": len(agent.images)})
    return jsonify({"success": False, "error": "Invalid index"}), 400


@app.route("/api/file/list-python", methods=["POST"])
def list_python_files():
    """List alle Python filer i en mappe"""
    data = request.json
    folder_path = data.get("folder", BASE_DIR)
    
    if not os.path.exists(folder_path):
        return jsonify({"success": False, "error": t(K.ERR_FOLDER_NOT_FOUND, agent.lang)}), 404
    
    if not _is_safe_path(BASE_DIR, folder_path):
        return jsonify({"success": False, "error": "Adgang nægtet: stien er uden for projektmappen"}), 403
    
    try:
        python_files = []
        for root, dirs, files in os.walk(folder_path):
            for file in files:
                if file.endswith('.py'):
                    full_path = os.path.join(root, file)
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

# ============ SESSION ENDPOINTS ============
@app.route("/api/sessions", methods=["GET"])
def list_sessions():
    sessions = session_manager.list_sessions()
    return jsonify({"success": True, "sessions": sessions})

@app.route("/api/sessions/current", methods=["GET"])
def get_current_session():
    global current_session_id
    if current_session_id:
        session_data = session_manager.load_session(current_session_id)
        return jsonify({"success": True, "session": session_data})
    return jsonify({"success": False, "session": None})

@app.route("/api/sessions/create", methods=["POST"])
def create_session():
    data = request.json
    name = data.get("name", t(K.SESSION_DEFAULT_NAME, agent.lang).format(n=len(session_manager.list_sessions())+1))
    session_id, session_data = session_manager.create_session(name)
    global current_session_id
    current_session_id = session_id
    agent.images = []  # clear images from previous session
    return jsonify({"success": True, "session_id": session_id, "session": session_data})

@app.route("/api/sessions/load/<session_id>", methods=["GET"])
def load_session(session_id):
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
        agent.images = _normalize_images(session_data.get("images", []))
        return jsonify({"success": True, "session": session_data})
    return jsonify({"success": False, "error": t(K.ERR_SESSION_NOT_FOUND, agent.lang)}), 404

@app.route("/api/sessions/save", methods=["POST"])
def save_current_session():
    global current_session_id
    data = request.json
    session_id = data.get("session_id", current_session_id)
    
    if not session_id:
        return jsonify({"error": t(K.ERR_NO_SESSION, agent.lang)}), 400
    
    existing = session_manager.load_session(session_id) or {}
    session_data = {
        "id": session_id,
        "name": data.get("name", t(K.SESSION_DEFAULT_NAME, agent.lang).format(n=session_id[:8])),
        "tree": data.get("tree") or (agent.task_tree_to_dict() if agent.task_tree else None),
        "layout": data.get("layout"),
        "execution_log": agent.execution_log,
        "agent_log": agent.agent_log,
        "original_prompt": data.get("original_prompt") or agent.original_prompt or "",
        "full_prompt_with_context": getattr(agent, 'full_prompt_with_context', '') or '',
        "show_thinking": data.get("show_thinking", agent.show_thinking),
        "template": data.get("template") or getattr(agent, 'active_template', None) or "fri",
        "lang": data.get("lang") or getattr(agent, 'lang', 'da'),
        "ui_lang": data.get("ui_lang") or data.get("lang") or getattr(agent, 'lang', 'da'),
        "prompt_history": data.get("prompt_history", []),
        "file_context": data.get("file_context", ""),
        "file_chunks": getattr(agent, 'file_chunks', None) or existing.get("file_chunks", {}),
        "images": getattr(agent, 'images', None) or existing.get("images", []),
        "created": existing.get("created", datetime.now().isoformat()),
        "learned_knowledge": existing.get("learned_knowledge", []),
        "decompose_model": data.get("decompose_model") or existing.get("decompose_model") or getattr(agent.decompose_llm, 'model', ''),
        "execute_model": data.get("execute_model") or existing.get("execute_model") or getattr(agent.llm, 'model', ''),
    }
    session_manager.save_session(session_id, session_data)
    current_session_id = session_id
    return jsonify({"success": True, "session_id": session_id})

@app.route("/api/sessions/rename", methods=["POST"])
def rename_session():
    data = request.json
    session_id = data.get("session_id")
    new_name = data.get("name", "")
    if not session_id or not new_name:
        return jsonify({"error": t(K.ERR_MISSING_SESSION, agent.lang)}), 400
    if session_manager.rename_session(session_id, new_name):
        return jsonify({"success": True})
    return jsonify({"error": t(K.ERR_SESSION_NOT_FOUND, agent.lang)}), 404

@app.route("/api/tools/token", methods=["GET", "POST"])
def manage_token():
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if request.method == "GET":
        if os.path.exists(env_path):
            with open(env_path, "r", encoding="utf-8") as f:
                content = f.read()
            has_token = "GITHUB_TOKEN" in content
            return jsonify({"success": True, "exists": True, "has_token": has_token})
        return jsonify({"success": True, "exists": False, "has_token": False})
    
    data = request.json
    content = data.get("content", "")
    with open(env_path, "w", encoding="utf-8") as f:
        f.write(content)
    return jsonify({"success": True, "message": ".env gemt"})

@app.route("/api/lang/<lang>")
def get_lang(lang):
    resp = jsonify(get_ui_translations(lang))
    resp.headers['Cache-Control'] = 'public, max-age=3600'
    return resp

@app.route("/api/models")
def get_models():
    openai_models = agent.llm.list_models()
    loaded = model_manager.get_loaded_models()
    rest_models = model_manager.get_all_rest_models()

    all_ids = set(openai_models)
    for m in rest_models:
        all_ids.add(m['id'])
    for k in loaded:
        if loaded[k].get('is_loaded'):
            all_ids.add(k)
            for inst in loaded[k].get('loaded_instances', []):
                all_ids.add(inst.get('id', ''))
    merged = sorted(all_ids)

    print(f'[models] Merged ({len(merged)}): {merged[:10]}{"..." if len(merged) > 10 else ""}')

    return jsonify({
        "models": merged,
        "openai_models": openai_models,
        "loaded": loaded,
        "rest_models": rest_models,
        "current": agent.llm.model,
        "decompose_model": agent.decompose_llm.model,
    })

@app.route("/api/models/set", methods=["POST"])
def set_model():
    data = request.json
    model = data.get("model", agent.llm.model)
    dtype = data.get("type", "execute")
    if dtype == "decompose":
        agent.decompose_llm.set_model(model)
    else:
        agent.llm.set_model(model)
    if current_session_id:
        existing = session_manager.load_session(current_session_id) or {}
        existing["decompose_model"] = agent.decompose_llm.model
        existing["execute_model"] = agent.llm.model
        session_manager.save_session(current_session_id, existing)
    return jsonify({"success": True, "model": model, "type": dtype})

@app.route("/api/models/loaded")
def loaded_models():
    return jsonify(model_manager.get_loaded_models())

@app.route("/api/models/load", methods=["POST"])
def load_model_route():
    data = request.json
    key = data.get("key", "")
    if not key:
        return jsonify({"success": False, "message": "No model key"}), 400
    ok, msg = model_manager.load_model(key)
    return jsonify({"success": ok, "message": msg})

@app.route("/api/models/unload", methods=["POST"])
def unload_model_route():
    data = request.json
    identifier = data.get("identifier", "--all")
    ok, msg = model_manager.unload_model(identifier)
    return jsonify({"success": ok, "message": msg})

@app.route("/api/stop", methods=["POST"])
def stop_execution():
    agent.stop_requested = True
    return jsonify({"success": True})

@app.route("/api/reply", methods=["POST"])
def user_reply():
    data = request.json
    msg = data.get("message", "")
    if not msg:
        return jsonify({"success": False, "error": "Empty message"}), 400
    agent.pending_reply = msg
    agent._log("USER", "Bruger svar", msg[:100])
    return jsonify({"success": True})

@app.route("/api/sessions/save-layout", methods=["POST"])
def save_layout():
    data = request.json
    session_id = data.get("session_id")
    layout = data.get("layout")
    if not session_id:
        return jsonify({"error": t(K.ERR_NO_SESSION_ID, agent.lang)}), 400
    session_data = session_manager.load_session(session_id)
    if session_data:
        session_data["layout"] = layout
        session_manager.save_session(session_id, session_data)
        return jsonify({"success": True})
    return jsonify({"error": t(K.ERR_SESSION_NOT_FOUND, agent.lang)}), 404

@app.route("/api/sessions/load-layout/<session_id>", methods=["GET"])
def load_layout(session_id):
    session_data = session_manager.load_session(session_id)
    if session_data and "layout" in session_data:
        return jsonify({"success": True, "layout": session_data["layout"]})
    return jsonify({"success": False, "layout": None}), 404

@app.route("/api/sessions/prompts/<session_id>", methods=["GET"])
def get_session_prompts(session_id):
    prompts = session_manager.get_prompt_history(session_id)
    return jsonify({"success": True, "prompts": prompts})

@app.route("/api/sessions/context", methods=["POST"])
def get_context_for_prompt():
    data = request.json
    session_id = data.get("session_id", current_session_id)
    prompt = data.get("prompt", "")
    if session_id and prompt:
        context = session_manager.get_knowledge_for_context(session_id, prompt)
        return jsonify({"success": True, "context": context})
    return jsonify({"success": False, "context": ""})

@app.route("/api/sessions/add-prompt", methods=["POST"])
def add_prompt_to_session():
    data = request.json
    session_id = data.get("session_id", current_session_id)
    prompt = data.get("prompt", "")
    result = data.get("result", "")
    tree = data.get("tree")
    if session_id and prompt:
        session_manager.add_prompt_result(session_id, prompt, result, tree)
        return jsonify({"success": True})
    return jsonify({"success": False})

# ============ AGENT ENDPOINTS ============
@app.route("/api/reset-execution", methods=["POST"])
def reset_execution():
    agent.reset_execution()
    return jsonify({"success": True, "message": t(K.UI_STREAM_RESET, agent.lang)})

@app.route("/api/execute-without-stream", methods=["POST"])
def execute_without_stream():
    global execution_status
    if agent.task_tree is None:
        return jsonify({"error": t(K.ERR_DECOMPOSE_FIRST, agent.lang)}), 400
    
    execution_status = {"running": True, "progress": 0, "current_task": "", "log": []}
    
    def count_tasks(node):
        total = 1
        for child in node.children:
            total += count_tasks(child)
        return total
    
    total_tasks = count_tasks(agent.task_tree.root)
    completed = 0
    
    def execute_with_progress(node):
        nonlocal completed
        execution_status["current_task"] = node.name
        for child in node.children:
            execute_with_progress(child)
        result = agent.solve_task(node, agent.original_prompt)
        completed += 1
        execution_status["progress"] = int((completed / total_tasks) * 100)
        execution_status["log"].append({"task": node.name, "status": node.status, "result": result[:200]})
        return result
    
    try:
        results = execute_with_progress(agent.task_tree.root)
        execution_status["results"] = results
        execution_status["running"] = False
        return jsonify({"success": True, "results": results, "total_tasks": total_tasks})
    except Exception as e:
        execution_status["running"] = False
        return jsonify({"success": False, "error": str(e)}), 500

def _ensure_model_loaded(model_key):
    if not model_key:
        return
    loaded, matched = model_manager.is_model_loaded(model_key)
    if loaded:
        print(f"✅ Model already loaded: {matched}")
        return
    print(f"⏳ Loading model: {model_key}...")
    ok, msg = model_manager.load_model(model_key)
    if ok:
        print(f"✅ {msg}")
    else:
        print(f"⚠️  {msg}")


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
        except Exception:
            pass
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
        except Exception:
            pass
        
        examples = "\n".join(f"  • {ex}" for ex in guidance["examples"])
        warning = (
            f"Din prompt ligner ikke en opgave til skabelonen '{template}'.{suggested}\n\n"
            f"Eksempler på gode prompts til '{template}':\n{examples}"
        )
        return {"warning": warning, "suggestion": suggested_template, "suggested_template": suggested_template, "matches": matches, "total": total}
    
    return {"warning": "", "suggestion": "", "suggested_template": "", "matches": matches, "total": total}


@app.route("/api/decompose", methods=["POST"])
def decompose():
    data = request.json
    prompt = data.get("prompt", "")
    session_id = data.get("session_id")
    show_thinking = data.get("show_thinking", True)
    files = data.get("files", [])
    template = data.get("template")
    lang = data.get("lang", "da")
    ui_lang = data.get("ui_lang", lang)
    
    if not prompt:
        return jsonify({"error": t(K.ERR_NO_PROMPT, ui_lang)}), 400
    
    global current_session_id
    if session_id:
        current_session_id = session_id
    elif not current_session_id:
        current_session_id, _ = session_manager.create_session(prompt[:30])
    
    agent.show_thinking = show_thinking
    agent.lang = lang
    session_context = session_manager.get_knowledge_for_context(current_session_id, prompt)
    
    # Guard: billedanalyse needs an image
    image_warning = ""
    if template == "billedanalyse" and not agent.images and not files:
        image_warning = "🖼️  Billedanalyse kræver et billede! Upload et billede med 🖼 knappen før du kører Nedbryd."
        agent._log("WARNING", "Billedanalyse uden billede", image_warning)
    
    # Validate prompt against selected template
    validation = _validate_template_prompt(prompt, template)
    if validation["warning"]:
        print(f"⚠️ Skabelon-advarsel ({template}): kun {validation['matches']}/{validation['total']} keywords matchede")
    
    print(f"🌳 Nedbryder: {prompt[:50]}..." + (f" skabelon: {template}" if template else ""))
    if files:
        print(f"📄 Med {len(files)} filer")

    decompose_model = data.get("decompose_model")
    execute_model = data.get("execute_model")
    if decompose_model:
        agent.decompose_llm.set_model(decompose_model)
    if execute_model:
        agent.llm.set_model(execute_model)

    _ensure_model_loaded(agent.decompose_llm.model)

    try:
        tree = agent.decompose_prompt(prompt, files=files, template=template)

        existing = session_manager.load_session(current_session_id) or {}
        existing.update({
            "id": current_session_id,
            "name": prompt[:30],
            "tree": tree,
            "execution_log": agent.execution_log,
            "agent_log": agent.agent_log,
            "original_prompt": agent.original_prompt,
            "full_prompt_with_context": agent.full_prompt_with_context,
            "show_thinking": agent.show_thinking,
            "template": template,
            "lang": agent.lang,
            "ui_lang": ui_lang,
            "file_context": files,
            "file_chunks": agent.file_chunks
        })
        session_manager.save_session(current_session_id, existing)
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
        print(f"❌ Fejl: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/execute-stream")
def execute_stream():
    global current_session_id
    ui_lang = "da"
    print(f"Execute stream - current_session_id: {current_session_id}")
    if current_session_id:
        session_data = session_manager.load_session(current_session_id)
        if session_data:
            st = session_data.get("show_thinking", True)
            ui_lang = session_data.get("ui_lang", session_data.get("lang", "da"))
            print(f"Session show_thinking: {st}")
            if session_data.get("original_prompt"):
                agent.original_prompt = session_data["original_prompt"]
            if session_data.get("tree"):
                agent.task_tree_from_dict(session_data["tree"])
            if session_data.get("lang"):
                agent.lang = session_data["lang"]
                agent.tool_registry.lang = agent.lang
            if session_data.get("file_chunks"):
                agent.file_chunks = session_data["file_chunks"]
            agent.images = _normalize_images(session_data.get("images", []))
            if session_data.get("template"):
                agent.active_template = session_data["template"]
                allowed = agent_skills.TEMPLATE_TOOLS.get(session_data["template"]) if session_data["template"] in agent_skills.TEMPLATE_TOOLS else None
                agent.tool_registry.set_active_tools(allowed)
            if session_data.get("decompose_model"):
                agent.decompose_llm.set_model(session_data["decompose_model"])
            if session_data.get("execute_model"):
                agent.llm.set_model(session_data["execute_model"])
            
            fpc = session_data.get("full_prompt_with_context", "")
            if not fpc:
                fc = session_data.get("file_context", "")
                if fc and isinstance(fc, list):
                    file_context_content = "\n\n" + t(K.FILE_CONTEXT_HEADER, agent.lang)
                    for f in fc:
                        filename = f.get('filename', t(K.UNKNOWN, agent.lang))
                        content = f.get('content', '')
                        file_context_content += f"\n### {filename}\n\n```{filename}\n{content}\n```\n"
                    fpc = agent.original_prompt + file_context_content
                else:
                    fpc = agent.original_prompt
            agent.full_prompt_with_context = fpc
            agent.show_thinking = st
            print(f"Agent show_thinking set to: {agent.show_thinking}")
            agent.stop_requested = False

    _ensure_model_loaded(agent.llm.model)

    def generate():
        _ui = ui_lang  # capture in closure
        if agent.task_tree is None:
            if current_session_id:
                session_data = session_manager.load_session(current_session_id)
                if session_data and session_data.get("tree"):
                    agent.task_tree_from_dict(session_data["tree"])
                    print("Tree restored from session in generate()")
            if agent.task_tree is None:
                yield f"data: {json.dumps({'type': 'error', 'message': t(K.ERR_DECOMPOSE_FIRST, _ui)})}\n\n"
                return
        
        original_prompt = getattr(agent, 'full_prompt_with_context', '') or agent.original_prompt
        show_thinking = getattr(agent, 'show_thinking', True)
        yield f"data: {json.dumps({'type': 'context', 'original_prompt': original_prompt, 'show_thinking': show_thinking})}\n\n"

        def _check_client():
            return agent.stop_requested

        agent.agent_log = []
        agent.execution_log = []

        # Truncate context for subtask prompts
        MAX_CTX = 150000
        task_context_prompt = original_prompt[:MAX_CTX] + ("\n\n[... trunkeret — brug read_chunk() for at læse flere chunks ...]" if len(original_prompt) > MAX_CTX else "")
        
        for log in agent.agent_log[-10:]:
            yield f"data: {json.dumps({'type': 'log', 'log': log})}\n\n"
        
        def count_tasks(node):
            total = 1
            for child in node.children:
                total += count_tasks(child)
            return total
        
        total_tasks = count_tasks(agent.task_tree.root)
        completed = 0
        yield f"data: {json.dumps({'type': 'start', 'total_tasks': total_tasks})}\n\n"
        
        def execute_with_stream(node):
            nonlocal completed
            if _check_client():
                return
            yield f"data: {json.dumps({'type': 'task_start', 'task': node.name})}\n\n"
            
            child_results = []
            for child in node.children:
                if _check_client():
                    return
                if getattr(agent, 'issue_resolved', False):
                    child.status = "skipped"
                    child.result = "Skipped — issue was already resolved in an earlier phase"
                    child_results.append(f"- {child.name}: {child.result}")
                    continue
                yield from execute_with_stream(child)
                if child.result:
                    child_results.append(f"- {child.name}: {child.result}")
            
            if child_results:
                node.status = "done"
                node.result = "\n".join(child_results)
                completed += 1
                progress = int((completed / total_tasks) * 100)
                yield f"data: {json.dumps({'type': 'progress', 'progress': progress})}\n\n"
                yield f"data: {json.dumps({'type': 'task_done', 'task': node.name, 'result': node.result[:500]})}\n\n"
                return
            
            node.status = "running"
            full_response = ""
            for event in agent.solve_task_stream(node, task_context_prompt):
                if _check_client():
                    return
                if event["type"] == "chunk":
                    full_response += event["chunk"]
                    if show_thinking:
                        yield f"data: {json.dumps({'type': 'llm_chunk', 'task': node.name, 'chunk': event['chunk']})}\n\n"
                elif event["type"] == "tool_call":
                    yield f"data: {json.dumps({'type': 'tool_call', 'task': node.name, 'tool': event['tool'], 'args': event['args']})}\n\n"
                elif event["type"] == "tool_result":
                    yield f"data: {json.dumps({'type': 'tool_result', 'task': node.name, 'tool': event['tool'], 'result': event['result']})}\n\n"
                elif event["type"] == "done":
                    full_response = event["result"]
            if not full_response:
                full_response = t(K.UI_TASK_RESULT_PREFIX, _ui) + ": " + node.name
            node.status = "done"
            node.result = full_response
            if _check_client():
                return
            completed += 1
            progress = int((completed / total_tasks) * 100)
            yield f"data: {json.dumps({'type': 'progress', 'progress': progress})}\n\n"
            yield f"data: {json.dumps({'type': 'task_done', 'task': node.name, 'result': full_response[:500]})}\n\n"
            agent.agent_log.append({"timestamp": time.time(), "level": "INFO", "message": t(K.UI_TASK_DONE_PREFIX, _ui) + ": " + node.name, "detail": full_response})
            yield f"data: {json.dumps({'type': 'log', 'log': agent.agent_log[-1]})}\n\n"
            if current_session_id:
                session_manager.add_prompt_result(current_session_id, node.name, full_response, None)
        
        try:
            if _check_client():
                yield f"data: {json.dumps({'type': 'stopped', 'message': t(K.UI_STREAM_STOPPED, _ui)})}\n\n"
                return
            yield from execute_with_stream(agent.task_tree.root)
            existing = session_manager.load_session(current_session_id) or {}
            existing.update({
                "tree": agent.task_tree_to_dict(),
                "execution_log": agent.execution_log,
                "agent_log": agent.agent_log,
                "original_prompt": agent.original_prompt or (agent.task_tree.root.name if agent.task_tree else ""),
                "prompt_history": existing.get("prompt_history", []),
                "lang": agent.lang,
                "ui_lang": ui_lang,
                "template": agent.active_template
            })
            if current_session_id:
                session_manager.save_session(current_session_id, existing)
            yield f"data: {json.dumps({'type': 'complete', 'message': t(K.UI_ALL_DONE, _ui)})}\n\n"
        except Exception as e:
            existing = session_manager.load_session(current_session_id) or {}
            existing["tree"] = agent.task_tree_to_dict() if agent.task_tree else existing.get("tree")
            existing["execution_log"] = agent.execution_log
            existing["agent_log"] = agent.agent_log
            existing["lang"] = agent.lang
            existing["template"] = agent.active_template
            if current_session_id:
                session_manager.save_session(current_session_id, existing)
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
    
    return Response(stream_with_context(generate()), mimetype='text/event-stream', headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no', 'Connection': 'keep-alive'})

@app.route("/api/log", methods=["GET"])
def get_log():
    return jsonify({"log": agent.agent_log})

@app.route("/api/status", methods=["GET"])
def status():
    return jsonify(agent.get_agent_status())

@app.route("/api/search", methods=["POST"])
def search():
    data = request.json
    query = data.get("query", "")
    if not query:
        return jsonify({"error": "No query"}), 400
    results = agent.searcher.search(query)
    return jsonify({"success": True, "search_results": results})

@app.route("/api/flow/search", methods=["POST"])
def flow_search():
    data = request.json
    query = data.get("query", "")
    max_results = int(data.get("maxResults", 10))
    if not query:
        return jsonify({"error": "No query"}), 400

    from ddg_search import websearch
    results = websearch(query, max_results)
    return jsonify({"success": True, "query": query, "results": results})


@app.route("/api/flow/generate", methods=["POST"])
def flow_generate():
    data = request.json
    topic = data.get("topic", "")
    max_results = int(data.get("maxResults", 10))

    if not topic:
        return jsonify({"error": "No topic"}), 400

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
def build_module():
    result = agent.suggest_new_module()
    return jsonify({"success": True, "module_result": result})

@app.route("/api/version", methods=["GET"])
def version():
    return jsonify({"success": True, "started": BUILD_INFO.get("started", "?"), "version": {k:v for k,v in BUILD_INFO.items() if k != "started"}})

@app.route("/api/issues", methods=["GET"])
def list_issues():
    issues_path = os.path.join(BASE_DIR, "docs", "issues", "observed", "issues.json")
    if not os.path.exists(issues_path):
        return jsonify({"success": True, "meta": {"total": 0}, "issues": []})
    with open(issues_path, encoding="utf-8") as f:
        data = json.load(f)
    return jsonify({"success": True, **data})

@app.route("/api/issues/<issue_id>", methods=["DELETE"])
def delete_issue(issue_id):
    issues_path = os.path.join(BASE_DIR, "docs", "issues", "observed", "issues.json")
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

@app.route("/api/test", methods=["GET"])
def test():
    return jsonify({"status": "ok", "message": t(K.UI_API_RUNNING, agent.lang), "static_folder": STATIC_DIR, "has_agent": agent is not None})

@app.route("/skillflow")
def skillflow_report():
    import json as _json
    outcomes_path = os.path.join(BASE_DIR, ".agent_storage", "skill_outcomes.json")
    evolution_path = os.path.join(BASE_DIR, ".agent_storage", "evolution_actions.json")

    outcomes = []
    if os.path.exists(outcomes_path):
        with open(outcomes_path, encoding="utf-8") as f:
            outcomes = _json.load(f)

    evolution = {}
    if os.path.exists(evolution_path):
        with open(evolution_path, encoding="utf-8") as f:
            evolution = _json.load(f)

    # Build stats
    from collections import Counter
    skills_c = Counter()
    success_c = Counter()
    templates_c = Counter()
    for o in outcomes:
        s = o.get("skill", "?")
        skills_c[s] += 1
        if o.get("success"):
            success_c[s] += 1
        t = o.get("template", "")
        if t:
            templates_c[t] += 1

    md = f"""# 🧬 SkillFlow Analysis

**Total outcomes:** {len(outcomes)}
**Last analysis:** {evolution.get('analyzed_at', 'never')}

## Per-Skill Statistics

| Skill | Success | Total | Rate |
|-------|---------|-------|------|
"""
    for skill, count in skills_c.most_common():
        s = success_c.get(skill, 0)
        rate = 100 * s / count if count else 0
        md += f"| {skill} | {s} | {count} | {rate:.0f}% |\n"

    if templates_c:
        md += "\n## Template Usage\n\n| Template | Outcomes |\n|----------|----------|\n"
        for t, c in templates_c.most_common():
            md += f"| {t} | {c} |\n"

    actions = evolution.get("actions", [])
    if actions:
        md += f"\n## Evolution Actions ({len(actions)})\n\n"
        for a in actions:
            act = a.get("action", "?")
            skill = a.get("skill", "?")
            reason = a.get("reason", "")
            emoji = {"retain": "✅", "refine": "🔧", "prune": "🗑️", "generate": "🆕"}.get(act, "❓")
            md += f"### {emoji} {act.upper()}: `{skill}`\n\n"
            md += f"**Reason:** {reason}\n\n"
            if a.get("success_rate") is not None:
                md += f"- Success rate: {a['success_rate']:.0%}\n"
            if a.get("frequency"):
                md += f"- Frequency: {a['frequency']}× repeated\n"
            if a.get("example_task"):
                md += f"- Example task: *{a['example_task']}*\n"
            if a.get("suggested_action_types"):
                md += f"- Suggested types: {', '.join(a['suggested_action_types'])}\n"
            md += "\n"

    if not outcomes:
        md += "\n*No outcomes recorded yet. Run some tasks to accumulate data.*\n"

    # Applied changes log
    log_path = os.path.join(BASE_DIR, ".agent_storage", "evolution_log.json")
    if os.path.exists(log_path):
        with open(log_path, encoding="utf-8") as f:
            log_entries = _json.load(f)
        if log_entries:
            md += f"\n## Applied Changes ({len(log_entries)} entries)\n\n"
            for entry in log_entries[-10:]:
                md += f"### {entry['timestamp']}\n\n"
                md += "| Action | Skill | Details |\n|--------|-------|----------|\n"
                for act in entry.get("actions", []):
                    md += f"| {act.get('action','?')} | `{act.get('skill','?')}` | {act.get('result','')[:120]} |\n"
                md += "\n"

    md += "\n\n[Apply pending actions](/api/skillflow/apply)"

    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><title>SkillFlow Analysis</title>
<script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
<style>
    body {{ font-family: 'Segoe UI', system-ui; max-width: 1000px; margin: 40px auto; padding: 20px; background: #0f172a; color: #e2e8f0; }}
    h1 {{ border-bottom: 2px solid #334155; padding-bottom: 10px; }}
    h2 {{ border-bottom: 1px solid #334155; padding-bottom: 6px; margin-top: 28px; }}
    h3 {{ color: #93c5fd; margin-top: 20px; }}
    code {{ background: #1e293b; padding: 2px 6px; border-radius: 4px; }}
    pre {{ background: #1e293b; padding: 14px; border-radius: 8px; overflow-x: auto; }}
    table {{ border-collapse: collapse; width: 100%; margin: 12px 0; }}
    th, td {{ border: 1px solid #334155; padding: 8px 12px; text-align: left; }}
    th {{ background: #1e293b; }}
    a {{ color: #60a5fa; }}
</style></head>
<body><div id="content"></div>
<script>document.getElementById('content').innerHTML = marked.parse({md!r});</script>
</body></html>"""

@app.route("/api/skillflow/apply")
def skillflow_apply():
    from skill_evolution import analyze, apply_evolution_actions, _log_applied
    analysis = analyze()
    if analysis.get("status") != "ok":
        return jsonify({"success": False, "error": analysis})
    results = apply_evolution_actions(analysis["actions"], dry_run=False)
    if results:
        _log_applied(results)
    return jsonify({"success": True, "status": "applied", "actions": len(results), "results": results})

@app.route("/api/skillflow/status")
def skillflow_status():
    import json as _json
    outcomes_path = os.path.join(BASE_DIR, ".agent_storage", "skill_outcomes.json")
    evolution_path = os.path.join(BASE_DIR, ".agent_storage", "evolution_actions.json")
    data = {"outcomes": [], "evolution": {}}
    if os.path.exists(outcomes_path):
        with open(outcomes_path, encoding="utf-8") as f:
            data["outcomes"] = _json.load(f)
    if os.path.exists(evolution_path):
        with open(evolution_path, encoding="utf-8") as f:
            data["evolution"] = _json.load(f)
    return jsonify({"success": True, "data": data})

if __name__ == "__main__":
    started = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print("=" * 50)
    print("🚀 Dansk Agent API starter...")
    print(f"🕐 Startet: {started}")
    print("📍 http://localhost:5000")
    print(f"📁 Static mappe: {STATIC_DIR}")
    print(f"📦 api_server={BUILD_INFO['api_server.py']} | agent_core={BUILD_INFO['agent_core.py']} | llm={BUILD_INFO['llm_wrapper.py']}")
    print("💾 Sessions gemmes i ./sessions/")
    print("📁 Filhåndtering via Python (tkinter)")
    print("=" * 50)
    app.run(debug=True, use_reloader=False, port=5000, threaded=True)