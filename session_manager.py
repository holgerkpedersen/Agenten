import json
import os
from datetime import datetime
import uuid

class SessionManager:
    def __init__(self, storage_dir="sessions"):
        self.storage_dir = storage_dir
        os.makedirs(storage_dir, exist_ok=True)
        
    def save_session(self, session_id, session_data):
        """Gem en session til disk inklusiv layout"""
        session_data["last_modified"] = datetime.now().isoformat()
        
        # Sikr at layout er gemt
        if "layout" not in session_data:
            session_data["layout"] = None
            
        filepath = os.path.join(self.storage_dir, f"{session_id}.json")
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(session_data, f, ensure_ascii=False, indent=2)
        return session_id
    
    def load_session(self, session_id):
        """Hent en session fra disk"""
        filepath = os.path.join(self.storage_dir, f"{session_id}.json")
        if os.path.exists(filepath):
            with open(filepath, 'r', encoding='utf-8') as f:
                return json.load(f)
        return None
    
    def list_sessions(self):
        """List alle gemte sessioner"""
        sessions = []
        for filename in os.listdir(self.storage_dir):
            if filename.endswith('.json'):
                session_id = filename[:-5]
                filepath = os.path.join(self.storage_dir, filename)
                with open(filepath, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    sessions.append({
                        "id": session_id,
                        "name": data.get("name", session_id[:8]),
                        "created": data.get("created", ""),
                        "last_modified": data.get("last_modified", ""),
                        "task_count": len(data.get("execution_log", []))
                    })
        return sorted(sessions, key=lambda x: x["last_modified"], reverse=True)
    
    def create_session(self, name):
        """Opret ny session med standard layout"""
        session_id = str(uuid.uuid4())[:8]
        session_data = {
            "id": session_id,
            "name": name,
            "created": datetime.now().isoformat(),
            "tree": None,
            "execution_log": [],
            "agent_log": [],
            "layout": {
                "items": [
                    {"id": "tree", "x": 0, "y": 0, "w": 4, "h": 2},
                    {"id": "llm", "x": 4, "y": 0, "w": 4, "h": 2},
                    {"id": "log", "x": 8, "y": 0, "w": 4, "h": 2}
                ]
            }
        }
        self.save_session(session_id, session_data)
        return session_id, session_data