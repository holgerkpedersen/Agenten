import json
import os
import re
from datetime import datetime
import uuid
import threading
from lang import t
from i18n import K

SESSION_ID_PATTERN = re.compile(r'^[a-f0-9-]{8,36}$')
MAX_SESSION_FILE_SIZE = 10 * 1024 * 1024

def _valid_session_id(session_id):
    return bool(session_id and SESSION_ID_PATTERN.match(session_id))

class SessionManager:
    _lock = threading.RLock()

    def __init__(self, storage_dir="sessions"):
        self.storage_dir = storage_dir
        os.makedirs(storage_dir, exist_ok=True)
        
    def save_session(self, session_id, session_data):
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
    
    def load_session(self, session_id):
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
    
    def list_sessions(self):
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
    
    def create_session(self, name):
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
    
    def rename_session(self, session_id, new_name):
        if not _valid_session_id(session_id):
            return False
        with SessionManager._lock:
            session = self.load_session(session_id)
            if not session:
                return False
            session["name"] = new_name
            self.save_session(session_id, session)
            return True

    def delete_session(self, session_id):
        if not _valid_session_id(session_id):
            return False
        filepath = os.path.join(self.storage_dir, f"{session_id}.json")
        with SessionManager._lock:
            try:
                os.remove(filepath)
                return True
            except FileNotFoundError:
                return False

    def add_prompt_result(self, session_id, prompt, result, tree=None):
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
    
    def get_prompt_history(self, session_id):
        if not _valid_session_id(session_id):
            return []
        session_data = self.load_session(session_id)
        if session_data:
            return session_data.get("prompt_history", [])
        return []
    
    def _extract_knowledge(self, session_data, prompt, result):
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
    
    def get_knowledge_for_context(self, session_id, current_prompt, lang="da"):
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