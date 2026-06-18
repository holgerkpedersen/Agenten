"""Session persistence and management for Agent."""

import json
import os
import re
from datetime import datetime
import uuid
import threading
from collections.abc import Callable
from typing import Any
from lang import t
from i18n import K
from flask import Flask, request, jsonify, send_from_directory, Response, stream_with_context
from typing import Any, Generator
from middleware import log, BASE_DIR, STATIC_DIR, app, _is_development_mode, _RateLimiter, rate_limiter, _rate_limit
from agent_core import Agent
from lang import t, get_ui_translations

SESSION_ID_PATTERN = re.compile(r'^[a-f0-9-]{8,36}$')
MAX_SESSION_FILE_SIZE = 10 * 1024 * 1024

def _valid_session_id(session_id: str) -> bool:
    """valid session id.
    
    Args:
        session_id:"""
    return bool(session_id and SESSION_ID_PATTERN.match(session_id))

class SessionManager:
    """session manager."""
    _lock = threading.RLock()

    def __init__(self, storage_dir: str = "sessions") -> None:
        """Initialize the instance.
        
        Args:
            storage_dir:"""
        self.storage_dir = storage_dir
        os.makedirs(storage_dir, exist_ok=True)
        
    def save_session(self, session_id: str, session_data: dict[str, Any]) -> str | None:
        """save session.
        
        Args:
            session_id:
            session_data:"""
        if not _valid_session_id(session_id):
            return None
        session_data["last_modified"] = datetime.now().isoformat()
        filepath = os.path.join(self.storage_dir, f"{session_id}.json")
        tmppath = filepath + ".tmp"
        with SessionManager._lock:
            with open(tmppath, 'w', encoding='utf-8') as f:
                json.dump(session_data, f, ensure_ascii=False, indent=2)
            os.replace(tmppath, filepath)
        return session_id
    
    def load_session(self, session_id: str) -> dict[str, Any] | None:
        """load session.
        
        Args:
            session_id:"""
        if not _valid_session_id(session_id):
            return None
        filepath = os.path.join(self.storage_dir, f"{session_id}.json")
        try:
            if os.path.getsize(filepath) > MAX_SESSION_FILE_SIZE:
                return None
        except OSError:
            return None
        try:
            with SessionManager._lock:
                with open(filepath, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError):
            return None
    
    def list_sessions(self) -> list[dict[str, Any]]:
        """list sessions."""
        sessions = []
        with SessionManager._lock:
            for filename in os.listdir(self.storage_dir):
                if filename.endswith('.json') and not filename.endswith('.tmp'):
                    session_id = filename[:-5]
                    filepath = os.path.join(self.storage_dir, filename)
                    try:
                        if os.path.getsize(filepath) > MAX_SESSION_FILE_SIZE:
                            continue
                        with open(filepath, 'r', encoding='utf-8') as f:
                            data = json.load(f)
                        sessions.append({
                            "id": session_id,
                            "name": data.get("name", session_id[:8]),
                            "created": data.get("created", ""),
                            "last_modified": data.get("last_modified", ""),
                            "prompt_count": len(data.get("prompt_history", []))
                        })
                    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
                        pass
        return sorted(sessions, key=lambda x: x["last_modified"], reverse=True)
    
    def create_session(self, name: str) -> tuple[str, dict[str, Any]]:
        """create session.
        
        Args:
            name:"""
        session_id = str(uuid.uuid4())
        session_data = {
            "id": session_id,
            "name": name,
            "created": datetime.now().isoformat(),
            "prompt_history": [],
            "tree": None,
            "execution_log": [],
            "agent_log": [],
            "learned_knowledge": [],
            "original_prompt": "",
            "layout": {}
        }
        self.save_session(session_id, session_data)
        return session_id, session_data
    
    def update_session(self, session_id: str, update_fn: Callable[[dict[str, Any]], dict[str, Any] | None]) -> str | None:
        """Atomically load, modify via update_fn, and save session.
        
        update_fn receives session_data dict and returns modified session_data.
        Prevents TOCTOU races between separate load+save calls.
        """
        if not _valid_session_id(session_id):
            return None
        filepath = os.path.join(self.storage_dir, f"{session_id}.json")
        with SessionManager._lock:
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    session_data = json.load(f)
            except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError):
                session_data = {}
            session_data = update_fn(session_data)
            if session_data is not None:
                session_data["last_modified"] = datetime.now().isoformat()
                tmppath = filepath + ".tmp"
                with open(tmppath, 'w', encoding='utf-8') as f:
                    json.dump(session_data, f, ensure_ascii=False, indent=2)
                os.replace(tmppath, filepath)
            return session_id

    def rename_session(self, session_id: str, new_name: str) -> bool:
        """rename session.
        
        Args:
            session_id:
            new_name:"""
        if not _valid_session_id(session_id):
            return False
        with SessionManager._lock:
            session = self.load_session(session_id)
            if not session:
                return False
            session["name"] = new_name
            self.save_session(session_id, session)
            return True

    def delete_session(self, session_id: str) -> bool:
        """delete session.
        
        Args:
            session_id:"""
        if not _valid_session_id(session_id):
            return False
        filepath = os.path.join(self.storage_dir, f"{session_id}.json")
        with SessionManager._lock:
            try:
                os.remove(filepath)
                return True
            except FileNotFoundError:
                return False

    def add_prompt_result(self, session_id: str, prompt: str, result: str | None, tree: Any = None) -> bool:
        """add prompt result.
        
        Args:
            session_id:
            prompt:
            result:
            tree:"""
        if not _valid_session_id(session_id):
            return False
        with SessionManager._lock:
            session_data = self.load_session(session_id)
            if session_data:
                if "prompt_history" not in session_data:
                    session_data["prompt_history"] = []
                session_data["prompt_history"].append({
                    "id": str(uuid.uuid4())[:6],
                    "prompt": prompt,
                    "result": result if result else "",
                    "tree": tree,
                    "timestamp": datetime.now().isoformat(),
                    "type": "user_prompt"
                })
                if len(session_data["prompt_history"]) > 50:
                    session_data["prompt_history"] = session_data["prompt_history"][-50:]
                self._extract_knowledge(session_data, prompt, result)
                self.save_session(session_id, session_data)
                return True
            return False
    
    def get_prompt_history(self, session_id: str) -> list[dict[str, Any]]:
        """get prompt history.
        
        Args:
            session_id:"""
        if not _valid_session_id(session_id):
            return []
        session_data = self.load_session(session_id)
        if session_data:
            return session_data.get("prompt_history", [])
        return []
    
    def _extract_knowledge(self, session_data: dict[str, Any], prompt: str, result: Any) -> None:
        """extract knowledge.
        
        Args:
            session_data:
            prompt:
            result:"""
        if not isinstance(prompt, str) or not prompt:
            return
        knowledge = session_data.get("learned_knowledge", [])
        prompt_lower = prompt.lower()
        keywords = [w for w in re.split(r'[\s.,;:!?()\[\]{}"\']+', prompt_lower) if w]
        stopwords = {'the', 'and', 'for', 'til', 'der', 'som', 'det', 'den', 'har', 'med', 'kan', 'skal', 'are', 'you', 'not', 'but', 'how', 'was', 'what', 'did', 'all', 'fra', 'is', 'in', 'it', 'to', 'of'}
        keywords = [kw for kw in keywords if kw not in stopwords]
        if keywords and result and len(str(result)) > 0:
            knowledge.append({
                "type": "task_outcome",
                "content": f"Prompt: {prompt[:150]} ... Result: {str(result)[:150]}",
                "source_prompt": prompt[:100],
                "keywords": keywords[:10],
                "timestamp": datetime.now().isoformat(),
            })
        session_data["learned_knowledge"] = knowledge[-20:]
    
    def get_knowledge_for_context(self, session_id: str, current_prompt: str, lang: str = "da") -> str:
        """get knowledge for context.
        
        Args:
            session_id:
            current_prompt:
            lang:"""
        if not _valid_session_id(session_id):
            return ""
        session_data = self.load_session(session_id)
        if not session_data:
            return ""
        knowledge = session_data.get("learned_knowledge", [])
        if not knowledge:
            return ""
        if not isinstance(current_prompt, str):
            return ""
        current_lower = current_prompt.lower()
        relevant = []
        for k in knowledge[-10:]:
            kws = k.get("keywords", [])
            if kws and any(kw in current_lower for kw in kws if len(kw) > 3):
                relevant.append(k)
        if relevant:
            context = "\n\n" + t(K.DEMO_KNOWLEDGE_HDR, lang)
            for k in relevant[:3]:
                context += f"- {k.get('content', '')[:200]}\n"
            return context
        return ""


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



def _extract_batch_results(agent: Any) -> list[dict]:
    """Extract successful batch_extract_symbols results from tool_log.

    Returns a list of dicts with 'symbols' and 'target' keys.
    """
    from pathlib import PurePath as _PurePath
    results = []
    for entry in getattr(agent, "_tool_log", []):
        if entry.get("tool") == "batch_extract_symbols" and entry.get("success"):
            args = entry.get("args", {})
            symbols = args.get("symbols", "")
            target = _PurePath(args.get("target", "")).name
            results.append({"symbols": symbols, "target": target})
    return results



def _extract_retry_context(node: Any, agent: Any, full_response: str,
                           symbols_before: int = -1, symbols_after: int = -1) -> dict:
    """Extract failure context for retry.

    Captures what went wrong, what tools were called,
    and relevant log entries so the next attempt can learn.
    """
    tool_log = getattr(agent, "_tool_log", []) or []
    phase_tools = [t for t in tool_log
                   if t.get("phase", "").lower() == (node.name or "").lower()]

    called_tools = {}
    for t in phase_tools:
        tname = t.get("tool", "")
        key = tname + str(t.get("args", {}))
        called_tools[key] = called_tools.get(key, 0) + 1

    called_names = {k.split("{")[0] for k in called_tools}
    active = set(agent.tool_registry.active_tools or []) if hasattr(agent, 'tool_registry') else set()
    required_action = {"edit_file", "write_file", "update_issue_status"}
    needed = active & required_action
    uncalled = needed - called_names

    moved = max(0, symbols_before - symbols_after) if symbols_before >= 0 and symbols_after >= 0 else 0

    return {
        "phase": node.name,
        "failure_reason": full_response[:300],
        "called_tools": list(called_names),
        "uncalled_tools": list(uncalled),
        "tool_count": len(phase_tools),
        "symbols_moved": moved,
        "symbols_before": symbols_before,
        "symbols_after": symbols_after,
        "successful_batches": _extract_batch_results(agent),
        "all_messages": [],
    }



def _build_retry_lessons(context: dict, agent: Any,
                         all_contexts: list[dict] | None = None) -> str:
    """Build a 'Lessons Learned' prompt section from a failed attempt.

    When all_contexts is provided, includes cumulative progress across
    ALL retry attempts so the LLM knows what was already accomplished.
    """
    lessons = []
    lessons.append("\u26a0\ufe0f  TIDLIGERE FORS\u00d8G MISLYKKEDES")
    lessons.append(f"\u00c5rsag: {context.get('failure_reason', 'Ukendt')[:200]}")
    lessons.append("")

    # --- HVAD BLEV OPN\u00c5ET (selvom fors\u00f8get fejlede) ---
    moved = context.get("symbols_moved", 0)
    if moved > 0:
        lessons.append("\u2705 HVAD BLEV OPN\u00c5ET:")
        lessons.append(f"- {moved} symboler flyttet i dette fors\u00f8g")
        after = context.get("symbols_after", -1)
        before = context.get("symbols_before", -1)
        if after >= 0:
            lessons.append(f"- api_server.py: {after} symbols tilbage" +
                           (f" (var {before})" if before >= 0 else ""))
        batches = context.get("successful_batches", [])
        for b in batches:
            lessons.append(f"- batch_extract_symbols: {b['symbols']} \u2192 {b['target']}")
        lessons.append("")

        # Kumulativ fremgang p\u00e5 tv\u00e6rs af ALLE hidtidige retries
        if all_contexts and len(all_contexts) > 1:
            total = sum(c.get("symbols_moved", 0) for c in all_contexts)
            first_before = all_contexts[0].get("symbols_before", -1)
            current = context.get("symbols_after", -1)
            lessons.append(f"\U0001f4ca SAMLET FREMGANG ({len(all_contexts)} fors\u00f8g):")
            lessons.append(f"- {total} symboler flyttet i alt")
            if first_before >= 0 and current >= 0:
                lessons.append(f"- api_server.py: {first_before} \u2192 {current} symbols")
            lessons.append("")

    uncalled = context.get("uncalled_tools", [])
    if uncalled:
        lessons.append("V\u00c6RKT\u00d8JER DER SKULLE HAVE V\u00c6RET KALDT:")
        lessons.append(f"- {', '.join(uncalled)}")
        if "edit_file" in uncalled:
            lessons.append("  Brug edit_file med symbol= parameter (AST-tilstand).")
            lessons.append("  Eksempel: edit_file(path='fil.py', symbol='funktionsnavn', new_text='...')")
        if "write_file" in uncalled:
            lessons.append("  Brug write_file til at oprette nye filer.")
        if "update_issue_status" in uncalled:
            lessons.append("  Brug update_issue_status til at markere issue som l\u00f8st.")
        lessons.append("")

    called = context.get("called_tools", [])
    if called:
        lessons.append("V\u00c6RKT\u00d8JER DER BLEV KALDT (men ikke nok):")
        lessons.append(f"- {', '.join(called)}")
        lessons.append("")

    lessons.append("INSTRUKTION:")
    lessons.append("L\u00e6r af fejlen ovenfor. S\u00f8rg for at kalde ALLE p\u00e5kr\u00e6vede v\u00e6rkt\u00f8jer.")
    lessons.append("Brug <<<DONE>>> f\u00f8rst n\u00e5r fasen er fuldf\u00f8rt med de rigtige v\u00e6rkt\u00f8jskald.")

    return "\n".join(lessons)


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
            "issue_resolved": stream_agent.issue_resolved,
        })
        return data
    try:
        stream_agent._wta.save()
        stream_agent._seq.save()
    except Exception:
        pass
    session_manager.update_session(current_session_id, _update)
