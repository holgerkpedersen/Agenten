import time
import threading
from config import app
from lang import t, get_ui_translations
from flask import Flask, request, jsonify, send_from_directory, Response, stream_with_context
from typing import Any, Generator

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
