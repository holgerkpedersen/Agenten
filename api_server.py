from flask import Flask, request, jsonify, send_from_directory, Response, stream_with_context
from flask_cors import CORS
from agent_core import Agent
from session_manager import SessionManager
import json
import time
import threading

# ============ DEFINER APP FØRST ============
app = Flask(__name__, static_folder="static")
CORS(app)

# ============ INITIALISERING ============
agent = Agent()
session_manager = SessionManager()
current_session_id = None
execution_status = {"running": False, "progress": 0, "current_task": "", "log": []}

# ============ STATIC ROUTES ============
@app.route("/")
def index():
    return send_from_directory("static", "index.html")

# ============ SESSION ENDPOINTS ============
@app.route("/api/sessions", methods=["GET"])
def list_sessions():
    """Hent alle gemte sessioner"""
    sessions = session_manager.list_sessions()
    return jsonify({"success": True, "sessions": sessions})

@app.route("/api/sessions/current", methods=["GET"])
def get_current_session():
    """Hent nuværende session uden at skifte"""
    global current_session_id
    if current_session_id:
        session_data = session_manager.load_session(current_session_id)
        return jsonify({"success": True, "session": session_data})
    return jsonify({"success": False, "session": None})

@app.route("/api/sessions/create", methods=["POST"])
def create_session():
    """Opret ny session"""
    data = request.json
    name = data.get("name", f"Session {len(session_manager.list_sessions()) + 1}")
    session_id, session_data = session_manager.create_session(name)
    global current_session_id
    current_session_id = session_id
    return jsonify({"success": True, "session_id": session_id, "session": session_data})

@app.route("/api/sessions/load/<session_id>", methods=["GET"])
def load_session(session_id):
    """Indlæs eksisterende session"""
    session_data = session_manager.load_session(session_id)
    if session_data:
        global current_session_id
        current_session_id = session_id
        return jsonify({"success": True, "session": session_data})
    return jsonify({"success": False, "error": "Session not found"}), 404

@app.route("/api/sessions/save", methods=["POST"])
def save_current_session():
    """Gem nuværende session"""
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
    """Gem layout for en session"""
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
    """Hent layout for en session"""
    session_data = session_manager.load_session(session_id)
    if session_data and "layout" in session_data:
        return jsonify({"success": True, "layout": session_data["layout"]})
    return jsonify({"success": False, "layout": None}), 404

@app.route("/api/sessions/prompts/<session_id>", methods=["GET"])
def get_session_prompts(session_id):
    """Hent alle prompts og resultater for en session"""
    prompts = session_manager.get_prompt_history(session_id)
    return jsonify({"success": True, "prompts": prompts})

@app.route("/api/sessions/context", methods=["POST"])
def get_context_for_prompt():
    """Hent relevant kontekst baseret på tidligere viden"""
    data = request.json
    session_id = data.get("session_id", current_session_id)
    prompt = data.get("prompt", "")
    
    if session_id and prompt:
        context = session_manager.get_knowledge_for_context(session_id, prompt)
        return jsonify({"success": True, "context": context})
    return jsonify({"success": False, "context": ""})

@app.route("/api/sessions/add-prompt", methods=["POST"])
def add_prompt_to_session():
    """Tilføj en prompt og resultat til sessionen"""
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
    
    if not prompt:
        return jsonify({"error": "Ingen prompt angivet"}), 400
    
    global current_session_id
    if session_id:
        current_session_id = session_id
    elif not current_session_id:
        # Opret automatisk ny session
        current_session_id, _ = session_manager.create_session(prompt[:30])
    
    # Hent relevant kontekst fra sessionens tidligere viden
    session_context = session_manager.get_knowledge_for_context(current_session_id, prompt)
    
    # Tilføj kontekst til prompten
    enriched_prompt = prompt
    if session_context:
        enriched_prompt = f"{prompt}\n\n{session_context}"
        print(f"📚 Tilføjet session-kontekst: {session_context[:100]}...")
    
    try:
        print(f"🌳 Nedbryder: {enriched_prompt[:50]}...")
        tree = agent.decompose_prompt(enriched_prompt)
        
        # Gem prompt i historik
        session_manager.add_prompt_result(current_session_id, prompt, "Nedbrudt til træ", tree)
        
        # Gem automatisk efter nedbrydning
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
    """Server-Sent Events med logging"""
    def generate():
        if agent.task_tree is None:
            yield f"data: {json.dumps({'type': 'error', 'message': 'Nedbryd en opgave først'})}\n\n"
            return
        
        original_prompt = agent.original_prompt
        
        yield f"data: {json.dumps({'type': 'context', 'original_prompt': original_prompt})}\n\n"
        
        # Send initial log
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
            
            # Udfør børn først
            for child in node.children:
                yield from execute_with_stream(child)
            
            node.status = "running"
            
            solve_prompt = f"""Løs delopgave i kontekst af: {original_prompt}

DELOPGAVE: {node.name}

Svar på dansk:"""
            
            full_response = ""
            for chunk in agent.llm.generate_stream(solve_prompt):
                full_response += chunk
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
            
            # Log afsluttet opgave
            agent.agent_log.append({
                "timestamp": time.time(),
                "level": "INFO",
                "message": f"Færdig: {node.name}",
                "detail": full_response[:100]
            })
            yield f"data: {json.dumps({'type': 'log', 'log': agent.agent_log[-1]})}\n\n"
            
            # Gem resultat i prompt historik
            if current_session_id:
                session_manager.add_prompt_result(current_session_id, node.name, full_response[:500], None)
        
        try:
            yield from execute_with_stream(agent.task_tree.root)
            
            # Gem session efter udførsel
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
    """Hent agent log"""
    return jsonify({"log": agent.agent_log})

@app.route("/api/status", methods=["GET"])
def status():
    """Hent agent status"""
    return jsonify(agent.get_agent_status())

@app.route("/api/search", methods=["POST"])
def search():
    """Søg på nettet"""
    data = request.json
    query = data.get("query", "")
    if not query:
        return jsonify({"error": "No query"}), 400
    results = agent.searcher.search(query)
    return jsonify({"success": True, "search_results": results})

@app.route("/api/build-module", methods=["POST"])
def build_module():
    """Byg nyt modul baseret på gentagne handlinger"""
    result = agent.suggest_new_module()
    return jsonify({"success": True, "module_result": result})

# ============ MAIN ============
if __name__ == "__main__":
    print("=" * 50)
    print("🚀 Dansk Agent API starter...")
    print("📍 http://localhost:5000")
    print("💾 Sessions gemmes i ./sessions/")
    print("📜 Prompt historik aktiveret")
    print("=" * 50)
    app.run(debug=True, port=5000, threaded=True)