import json
import os
from datetime import datetime
import uuid
import threading
from lang import t
from i18n import K

class SessionManager:
    _lock = threading.Lock()

    def __init__(self, storage_dir="sessions"):
        self.storage_dir = storage_dir
        os.makedirs(storage_dir, exist_ok=True)
        
    def save_session(self, session_id, session_data):
        session_data["last_modified"] = datetime.now().isoformat()
        filepath = os.path.join(self.storage_dir, f"{session_id}.json")
        with SessionManager._lock:
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(session_data, f, ensure_ascii=False, indent=2)
        return session_id
    
    def load_session(self, session_id):
        filepath = os.path.join(self.storage_dir, f"{session_id}.json")
        if os.path.exists(filepath):
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except (json.JSONDecodeError, UnicodeDecodeError):
                pass
        return None
    
    def list_sessions(self):
        sessions = []
        for filename in os.listdir(self.storage_dir):
            if filename.endswith('.json') and not filename.endswith('.tmp'):
                session_id = filename[:-5]
                filepath = os.path.join(self.storage_dir, filename)
                try:
                    with open(filepath, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                    sessions.append({
                        "id": session_id,
                        "name": data.get("name", session_id[:8]),
                        "created": data.get("created", ""),
                        "last_modified": data.get("last_modified", ""),
                        "prompt_count": len(data.get("prompt_history", []))
                    })
                except (json.JSONDecodeError, UnicodeDecodeError):
                    pass
        return sorted(sessions, key=lambda x: x["last_modified"], reverse=True)
    
    def create_session(self, name):
        session_id = str(uuid.uuid4())[:8]
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
        session_data = self.load_session(session_id)
        if session_data:
            session_data["name"] = new_name
            self.save_session(session_id, session_data)
            return True
        return False

    def add_prompt_result(self, session_id, prompt, result, tree=None):
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
        session_data = self.load_session(session_id)
        if session_data:
            return session_data.get("prompt_history", [])
        return []
    
    def _extract_knowledge(self, session_data, prompt, result):
        knowledge = session_data.get("learned_knowledge", [])
        prompt_lower = prompt.lower()
        if "2 + 2" in prompt_lower or "2 plus 2" in prompt_lower:
            knowledge.append({"type": "mathematical_fact", "content": t("session.demo_math_fact", "da"), "source_prompt": prompt[:50], "timestamp": datetime.now().isoformat()})
        if "token" in prompt_lower or "komprimere" in prompt_lower:
            knowledge.append({"type": "optimization", "content": t("session.demo_optimization", "da"), "source_prompt": prompt[:50], "timestamp": datetime.now().isoformat()})
        session_data["learned_knowledge"] = knowledge[-20:]
    
    def get_knowledge_for_context(self, session_id, current_prompt, lang="da"):
        session_data = self.load_session(session_id)
        if not session_data:
            return ""
        knowledge = session_data.get("learned_knowledge", [])
        if not knowledge:
            return ""
        current_lower = current_prompt.lower()
        relevant = []
        for k in knowledge[-10:]:
            content = k.get("content", "").lower()
            keywords = current_lower.split()[:5]
            if any(keyword in content for keyword in keywords if len(keyword) > 3):
                relevant.append(k)
        if relevant:
            context = "\n\n" + t(K.DEMO_KNOWLEDGE_HDR, lang)
            for k in relevant[:3]:
                context += f"- {k.get('content', '')[:200]}\n"
            return context
        return ""