from flask import Flask, request, jsonify, send_from_directory, Response, stream_with_context
from flask_cors import CORS
from agent_core import Agent
from session_manager import SessionManager
import json
import time
import threading
import os
import tkinter as tk
from tkinter import filedialog
import threading

app = Flask(__name__, static_folder="static")
CORS(app)

agent = Agent()
session_manager = SessionManager()
current_session_id = None
execution_status = {"running": False, "progress": 0, "current_task": "", "log": []}
export_folder = None  # Gem brugerens valgte mappe

# ============ FILHÅNDTERING ENDPOINTS ============

@app.route("/api/folder/select", methods=["POST"])
def select_folder():
    """Åben dialog for at vælge mappe (kører i separat tråd)"""
    global export_folder
    
    def select():
        global export_folder
        root = tk.Tk()
        root.withdraw()  # Skjul hovedvinduet
        root.attributes('-topmost', True)  # Sæt dialog øverst
        folder_selected = filedialog.askdirectory(title="Vælg mappe til at gemme eksporterede filer")
        root.destroy()
        
        if folder_selected:
            export_folder = folder_selected
            print(f"📁 Eksportmappe valgt: {export_folder}")
    
    # Kør i separat tråd for ikke at blokere
    thread = threading.Thread(target=select)
    thread.start()
    
    return jsonify({"success": True, "message": "Vælg mappe i det åbnede vindue"})

@app.route("/api/folder/status", methods=["GET"])
def folder_status():
    """Hent nuværende eksportmappe"""
    global export_folder
    if export_folder and os.path.exists(export_folder):
        return jsonify({"success": True, "folder": export_folder})
    return jsonify({"success": False, "folder": None})

@app.route("/api/folder/save", methods=["POST"])
def save_to_folder():
    """Gem fil til den valgte mappe"""
    global export_folder
    data = request.json
    filename = data.get("filename", "export.md")
    content = data.get("content", "")
    
    if not export_folder:
        return jsonify({"success": False, "error": "Ingen mappe valgt"}), 400
    
    try:
        os.makedirs(export_folder, exist_ok=True)
        filepath = os.path.join(export_folder, filename)
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        return jsonify({"success": True, "filepath": filepath})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/folder/list", methods=["POST"])
def list_folder_contents():
    """List indhold af en mappe"""
    data = request.json
    folder_path = data.get("path", export_folder)
    
    if not folder_path or not os.path.exists(folder_path):
        return jsonify({"success": False, "error": "Mappe findes ikke"}), 400
    
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

@app.route("/api/folder/delete", methods=["POST"])
def delete_file():
    """Slet en fil eller mappe"""
    data = request.json
    filepath = data.get("path")
    
    if not filepath or not os.path.exists(filepath):
        return jsonify({"success": False, "error": "Fil/mappe findes ikke"}), 400
    
    try:
        if os.path.isdir(filepath):
            os.rmdir(filepath)
        else:
            os.remove(filepath)
        return jsonify({"success": True})
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
    name = data.get("name", f"Session {len(session_manager.list_sessions()) + 1}")
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
        return jsonify({"success": True, "session": session_data})
    return jsonify({"success": False, "error": "Session not found"}), 404

@app.route("/api/sessions/save", methods=["POST"])
def save_current_session():
    global current_session_id
    data = request.json
    session_id = data.get("session_id", current_session_id)
    
    if not session_id:
        return jsonify({"error": "No active session"}), 400
    
    session_data = {
        "id": session_id,
        "name": data.get("name", f"Session {session_id}"),
        "tree": data.get("tree") or (agent.task_tree_to_dict() if agent.task_tree else None),
        "layout": data.get("layout"),
        "execution_log": agent.execution_log,
        "agent_log": agent.agent_log,
        "original_prompt": agent.original_prompt,
        "prompt_history": data.get("prompt_history", [])
    }
    session_manager.save_session(session_id, session_data)
    current_session_id = session_id
    return jsonify({"success": True, "session_id": session_id})

@app.route("/api/sessions/save-layout", methods=["POST"])
def save_layout():
    data = request.json
    session_id = data.get("session_id")
    layout = data.get("layout")
    
    if not session_id:
        return jsonify({"error": "No session_id"}), 400
    
    session_data = session_manager.load_session(session_id)
    if session_data:
        session_data["layout"] = layout
        session_manager.save_session(session_id, session_data)
        return jsonify({"success": True})
    return jsonify({"error": "Session not found"}), 404

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

@app.route("/api/decompose", methods=["POST"])
def decompose():
    data = request.json
    prompt = data.get("prompt", "")
    session_id = data.get("session_id")
    show_thinking = data.get("show_thinking", True)  # Ny parameter
    
    if not prompt:
        return jsonify({"error": "Ingen prompt angivet"}), 400
    
    global current_session_id
    if session_id:
        current_session_id = session_id
    elif not current_session_id:
        current_session_id, _ = session_manager.create_session(prompt[:30])
    
    # Sæt thinking mode på agenten
    agent.show_thinking = show_thinking
    
    # Hent relevant kontekst fra sessionens tidligere viden
    session_context = session_manager.get_knowledge_for_context(current_session_id, prompt)
    
    enriched_prompt = prompt
    if session_context:
        enriched_prompt = f"{prompt}\n\n{session_context}"
        print(f"📚 Tilføjet session-kontekst: {session_context[:100]}...")
    
    try:
        print(f"🌳 Nedbryder: {enriched_prompt[:50]}...")
        tree = agent.decompose_prompt(enriched_prompt)
        
        session_manager.add_prompt_result(current_session_id, prompt, "Nedbrudt til træ", tree)
        
        session_data = {
            "id": current_session_id,
            "name": prompt[:30],
            "tree": tree,
            "execution_log": agent.execution_log,
            "agent_log": agent.agent_log,
            "original_prompt": agent.original_prompt
        }
        session_manager.save_session(current_session_id, session_data)
        
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
    """Server-Sent Events med valgfri tænkning"""
    def generate():
        if agent.task_tree is None:
            yield f"data: {json.dumps({'type': 'error', 'message': 'Nedbryd en opgave først'})}\n\n"
            return
        
        original_prompt = agent.original_prompt
        show_thinking = getattr(agent, 'show_thinking', True)
        
        yield f"data: {json.dumps({'type': 'context', 'original_prompt': original_prompt, 'show_thinking': show_thinking})}\n\n"
        
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
            
            yield f"data: {json.dumps({'type': 'task_start', 'task': node.name})}\n\n"
            
            for child in node.children:
                yield from execute_with_stream(child)
            
            node.status = "running"
            
            solve_prompt = f"""Løs delopgave i kontekst af: {original_prompt}

DELOPGAVE: {node.name}

Svar på dansk:"""
            
            full_response = ""
            chunk_count = 0
            
            for chunk in agent.llm.generate_stream(solve_prompt):
                full_response += chunk
                chunk_count += 1
                
                # Send chunk (kun hvis thinking er aktiveret eller kun resultat)
                if show_thinking or chunk_count % 10 == 0:
                    yield f"data: {json.dumps({'type': 'llm_chunk', 'task': node.name, 'chunk': chunk})}\n\n"
                time.sleep(0.01)
            
            if not full_response:
                full_response = f"Løsning: {node.name}"
            
            node.status = "done"
            node.result = full_response
            completed += 1
            
            progress = int((completed / total_tasks) * 100)
            yield f"data: {json.dumps({'type': 'progress', 'progress': progress})}\n\n"
            yield f"data: {json.dumps({'type': 'task_done', 'task': node.name, 'result': full_response[:500]})}\n\n"
            
            agent.agent_log.append({
                "timestamp": time.time(),
                "level": "INFO",
                "message": f"Færdig: {node.name}",
                "detail": full_response[:100]
            })
            yield f"data: {json.dumps({'type': 'log', 'log': agent.agent_log[-1]})}\n\n"
            
            if current_session_id:
                session_manager.add_prompt_result(current_session_id, node.name, full_response[:500], None)
        
        try:
            yield from execute_with_stream(agent.task_tree.root)
            
            session_data = {
                "id": current_session_id,
                "name": original_prompt[:30] if original_prompt else "Session",
                "tree": agent.task_tree_to_dict(),
                "execution_log": agent.execution_log,
                "agent_log": agent.agent_log,
                "original_prompt": original_prompt
            }
            if current_session_id:
                session_manager.save_session(current_session_id, session_data)
            
            yield f"data: {json.dumps({'type': 'complete', 'message': 'Alle opgaver færdige'})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
    
    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
            'Connection': 'keep-alive'
        }
    )

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

# ============ MAIN ============
if __name__ == "__main__":
    print("=" * 50)
    print("🚀 Dansk Agent API starter...")
    print("📍 http://localhost:5000")
    print("💾 Sessions gemmes i ./sessions/")
    print("📁 Filhåndtering via Python (tkinter)")
    print("🧠 Toggle thinking mode understøttet")
    print("=" * 50)
    app.run(debug=True, port=5000, threaded=True)