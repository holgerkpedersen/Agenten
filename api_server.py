from flask import Flask, request, jsonify, send_from_directory, Response, stream_with_context
from flask_cors import CORS
from agent_core import Agent
from session_manager import SessionManager
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

# ============ STATIC ROUTES ============
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

@app.route("/api/file/list-python", methods=["POST"])
def list_python_files():
    """List alle Python filer i en mappe"""
    data = request.json
    folder_path = data.get("folder", BASE_DIR)
    
    if not os.path.exists(folder_path):
        return jsonify({"success": False, "error": t(K.ERR_FOLDER_NOT_FOUND, agent.lang)}), 404
    
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
    return jsonify({"success": True, "session_id": session_id, "session": session_data})

@app.route("/api/sessions/load/<session_id>", methods=["GET"])
def load_session(session_id):
    session_data = session_manager.load_session(session_id)
    if session_data:
        global current_session_id
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
            return jsonify({"success": True, "exists": True, "content": content})
        return jsonify({"success": True, "exists": False, "content": "GITHUB_TOKEN=dit_token_her"})
    
    data = request.json
    content = data.get("content", "")
    with open(env_path, "w", encoding="utf-8") as f:
        f.write(content)
    return jsonify({"success": True, "message": ".env gemt"})

@app.route("/api/lang/<lang>")
def get_lang(lang):
    return jsonify(get_ui_translations(lang))

@app.route("/api/models")
def get_models():
    models = agent.llm.list_models()
    loaded = model_manager.get_loaded_models()
    loaded_llm = [k for k, v in loaded.items() if v['is_loaded']]
    if len(loaded_llm) == 1:
        only = loaded_llm[0]
        if agent.llm.model not in models:
            agent.llm.set_model(only)
        if agent.decompose_llm.model not in models:
            agent.decompose_llm.set_model(only)
    return jsonify({
        "models": models,
        "loaded": loaded,
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
    
    print(f"🌳 Nedbryder: {prompt[:50]}..." + (f" skabelon: {template}" if template else ""))
    if files:
        print(f"📄 Med {len(files)} filer")

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
            "log": agent.agent_log[-20:] if agent.agent_log else []
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
            if session_data.get("template"):
                agent.active_template = session_data["template"]
                allowed = agent.TEMPLATE_TOOLS.get(session_data["template"]) if session_data["template"] in agent.TEMPLATE_TOOLS else None
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
            yield f"data: {json.dumps({'type': 'error', 'message': t(K.ERR_DECOMPOSE_FIRST, _ui)})}\n\n"
            return
        
        original_prompt = getattr(agent, 'full_prompt_with_context', '') or agent.original_prompt
        show_thinking = getattr(agent, 'show_thinking', True)
        yield f"data: {json.dumps({'type': 'context', 'original_prompt': original_prompt, 'show_thinking': show_thinking})}\n\n"

        def _check_client():
            return agent.stop_requested

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
                yield from execute_with_stream(child)
                if child.result:
                    child_results.append(f"- {child.name}: {child.result}")
            
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
            result_preview = full_response[:500] if show_thinking else full_response
            yield f"data: {json.dumps({'type': 'task_done', 'task': node.name, 'result': result_preview})}\n\n"
            agent.agent_log.append({"timestamp": time.time(), "level": "INFO", "message": t(K.UI_TASK_DONE_PREFIX, _ui) + ": " + node.name, "detail": full_response[:100]})
            yield f"data: {json.dumps({'type': 'log', 'log': agent.agent_log[-1]})}\n\n"
            if current_session_id:
                session_manager.add_prompt_result(current_session_id, node.name, full_response[:500], None)
        
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

@app.route("/api/build-module", methods=["POST"])
def build_module():
    result = agent.suggest_new_module()
    return jsonify({"success": True, "module_result": result})

@app.route("/api/test", methods=["GET"])
def test():
    return jsonify({"status": "ok", "message": t(K.UI_API_RUNNING, agent.lang), "static_folder": STATIC_DIR, "has_agent": agent is not None})

if __name__ == "__main__":
    print("=" * 50)
    print("🚀 Dansk Agent API starter...")
    print("📍 http://localhost:5000")
    print(f"📁 Static mappe: {STATIC_DIR}")
    print("💾 Sessions gemmes i ./sessions/")
    print("📁 Filhåndtering via Python (tkinter)")
    print("=" * 50)
    app.run(debug=True, port=5000, threaded=True)