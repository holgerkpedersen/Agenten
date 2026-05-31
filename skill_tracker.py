"""
SkillFlow — Outcome Tracker
Records per-skill execution outcomes for Retain/Refine/Prune/Generate analysis.
"""

import json
import os
import threading
from collections import defaultdict
from datetime import datetime


class SkillTracker:
    """skill tracker."""
    DATA_DIR = ".agent_storage"
    DATA_FILE = "skill_outcomes.json"
    _lock = threading.Lock()

    def __init__(self):
        """Initialize the instance."""
        self._ensure_dir()
        self._outcomes = self._load()
        self._session_id = datetime.now().strftime("%Y%m%d-%H%M%S")

    def _ensure_dir(self):
        """ensure dir."""
        os.makedirs(self.DATA_DIR, exist_ok=True)

    def _path(self):
        """path."""
        return os.path.join(self.DATA_DIR, self.DATA_FILE)

    def _load(self):
        """load."""
        path = self._path()
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                return []
        return []

    def _save(self):
        """save."""
        path = self._path()
        with self._lock:
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._outcomes, f, indent=2)
            os.replace(tmp, path)

    def record(self, skill_name: str, task_summary: str, success: bool,
               duration_ms: int = 0, tokens_used: int = 0, detail: str = "", template: str = ""):
        """record.
        
        Args:
            skill_name (str):
            task_summary (str):
            success (bool):
            duration_ms (int):
            tokens_used (int):
            detail (str):
            template (str):"""
        entry = {
            "skill": skill_name,
            "task": task_summary[:200],
            "success": success,
            "duration_ms": duration_ms,
            "tokens_used": tokens_used,
            "detail": detail[:500],
            "template": template,
            "session": self._session_id,
            "timestamp": datetime.now().isoformat(),
        }
        with self._lock:
            self._outcomes.append(entry)
            if len(self._outcomes) > 10000:
                self._outcomes = self._outcomes[-5000:]
        self._save()

    def get_outcomes(self, skill_name: str = None, limit: int = None):
        """get outcomes.
        
        Args:
            skill_name (str):
            limit (int):"""
        with self._lock:
            if skill_name:
                filtered = [o for o in self._outcomes if o.get("skill") == skill_name]
            else:
                filtered = list(self._outcomes)
        if limit:
            filtered = filtered[-limit:]
        return filtered

    def get_stats(self, skill_name: str = None, recent: int = 50):
        """get stats.
        
        Args:
            skill_name (str):
            recent (int):"""
        outcomes = self.get_outcomes(skill_name, recent)
        if not outcomes:
            return {"success_rate": 0, "count": 0}
        successes = sum(1 for o in outcomes if o.get("success"))
        return {
            "success_rate": successes / len(outcomes),
            "count": len(outcomes),
            "successes": successes,
            "failures": len(outcomes) - successes,
        }

    def get_all_skill_stats(self, recent: int = 50):
        """get all skill stats.
        
        Args:
            recent (int):"""
        with self._lock:
            names = set(o.get("skill") for o in self._outcomes)
        return {n: self.get_stats(n, recent) for n in sorted(names)}

    def get_unmatched_tasks(self, limit: int = 20):
        """get unmatched tasks.
        
        Args:
            limit (int):"""
        with self._lock:
            result = [
                o.get("task") for o in self._outcomes
                if o.get("skill") == "__none__" or o.get("skill") == ""
            ]
        return result[-limit:]

    def get_unmatched_outcomes(self, limit: int = 100):
        """get unmatched outcomes.
        
        Args:
            limit (int):"""
        with self._lock:
            result = [
                o for o in self._outcomes
                if o.get("skill") == "__none__" or o.get("skill") == ""
            ]
        return result[-limit:]

    def clear(self):
        """clear."""
        with self._lock:
            self._outcomes = []
        self._save()

    @property
    def total_outcomes(self):
        """total outcomes."""
        with self._lock:
            return len(self._outcomes)


tracker = SkillTracker()
