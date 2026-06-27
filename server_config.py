import os
from flask import Flask, request, jsonify, send_from_directory, Response, stream_with_context
import time
import threading
from lang import t, get_ui_translations
from typing import Any, Generator
from datetime import datetime

# ============ KONFIGURATION ============
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

STATIC_DIR = os.path.join(BASE_DIR, 'static')


app = Flask(__name__, static_folder=STATIC_DIR, static_url_path='/static')

UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")


def sanitize_filename(filename: str) -> str:
    """Sanitize a filename by removing potentially dangerous characters."""
    if not filename:
        return ""
    result = "".join(c for c in filename if c.isalnum() or c in '._- ')
    return result or "_"


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
