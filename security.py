"""Security module for API key validation and rate limiting."""
import os
from functools import wraps
from flask import request, jsonify
import time
import threading


# Development mode check
def _is_development_mode():
    """Check if running in development mode."""
    return os.environ.get('FLASK_ENV') == 'development' or \
           os.environ.get('DEBUG', '').lower() in ('1', 'true', 'yes')


# API Key validation
API_KEY = os.environ.get('API_KEY', '')


def check_api_key():
    """Check if the request has a valid API key."""
    if _is_development_mode():
        return True
    
    api_key = request.headers.get('X-API-Key') or \
              request.args.get('api_key') or \
              request.form.get('api_key')
    
    return api_key == API_KEY


# Rate Limiter class
class _RateLimiter:
    """Rate limiter for API endpoints."""
    
    def __init__(self, max_requests=100, window_seconds=60):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.requests = {}  # {ip: [(timestamp, count), ...]}
        self.lock = threading.Lock()
    
    def is_allowed(self, ip):
        """Check if request from IP is allowed."""
        now = time.time()
        
        with self.lock:
            if ip not in self.requests:
                self.requests[ip] = []
            
            # Remove old requests outside the window
            self.requests[ip] = [
                (ts, count) for ts, count in self.requests[ip]
                if now - ts < self.window_seconds
            ]
            
            total_requests = sum(count for _, count in self.requests[ip])
            
            if total_requests >= self.max_requests:
                return False
            
            # Add current request
            self.requests[ip].append((now, 1))
            return True
    
    def cleanup(self):
        """Clean up old entries."""
        now = time.time()
        with self.lock:
            for ip in list(self.requests.keys()):
                self.requests[ip] = [
                    (ts, count) for ts, count in self.requests[ip]
                    if now - ts < self.window_seconds * 2
                ]
                if not self.requests[ip]:
                    del self.requests[ip]


# Global rate limiter instance
rate_limiter = _RateLimiter(max_requests=100, window_seconds=60)


def _rate_limit(func):
    """Decorator to apply rate limiting to a function."""
    @wraps(func)
    def wrapper(*args, **kwargs):
        if not _is_development_mode():
            client_ip = request.remote_addr or '127.0.0.1'
            
            if not rate_limiter.is_allowed(client_ip):
                return jsonify({
                    'error': 'Rate limit exceeded',
                    'message': 'Too many requests. Please try again later.'
                }), 429
        
        return func(*args, **kwargs)
    return wrapper


def _guard_json_body(func):
    """Decorator to ensure request has JSON body."""
    @wraps(func)
    def wrapper(*args, **kwargs):
        if not request.is_json:
            return jsonify({
                'error': 'Invalid content type',
                'message': 'Content-Type must be application/json'
            }), 400
        
        try:
            request.get_json(force=True)
        except Exception as e:
            return jsonify({
                'error': 'Invalid JSON',
                'message': str(e)
            }), 400
        
        return func(*args, **kwargs)
    return wrapper
