from flask import Flask, request, jsonify, send_from_directory, Response, stream_with_context
import os
from typing import Any, Generator
from agent_files import _is_safe_path
from folder_manager import set_folder, folder_status, save_to_folder, list_folder_contents
from config import BASE_DIR, STATIC_DIR, app, _is_development_mode, _file_mtime, VERSION_FILES, BUILD_INFO
from folder_manager import UPLOAD_DIR
from middleware import _RateLimiter, rate_limiter, _rate_limit
from session_manager import SessionManager, _guard_json_body, agent, session_manager, execution_status, execution_status_lock, export_folder_lock
import json

# ============ STATIC ROUTES ============
@app.route("/uploads/<path:filename>")
def serve_upload(filename: str) -> Any:
    """serve upload.

    Args:
        filename:"""
    filepath = os.path.join(UPLOAD_DIR, filename)
    if not _is_safe_path(UPLOAD_DIR, filepath):
        return "<h2>Access denied</h2>", 403
    return send_from_directory(UPLOAD_DIR, filename)


@app.route("/preview-exports/<path:filename>")
def preview_export(filename: str) -> Any:
    """preview export.

    Args:
        filename:"""
    import re
    base = session_manager.export_folder or os.path.join(BASE_DIR, "exports")
    filepath = os.path.join(base, filename)

    # Security check: Path Traversal Prevention (SEC-013)
    if not _is_safe_path(base, filepath):
        return "<h2>Access denied</h2>", 403
    try:
        with open(filepath, encoding="utf-8") as f:
            md_content = f.read()
    except (FileNotFoundError, IOError, OSError):
        return "<h2>File not found</h2>", 404
    md_content = md_content.replace('<<<', '&lt;&lt;&lt;').replace('>>>', '&gt;&gt;&gt;')
    md_content = re.sub(r'&lt;&lt;&lt;TOOL&gt;&gt;&gt;(\{.*?\})&lt;&lt;&lt;END&gt;&gt;&gt;', r'<pre class="tool-call">&lt;&lt;&lt;TOOL&gt;&gt;&gt;\1&lt;&lt;&lt;END&gt;&gt;&gt;</pre>', md_content)
    md_content = re.sub(r'&lt;&lt;&lt;DONE&gt;&gt;&gt;(\{.*?\})&lt;&lt;&lt;END&gt;&gt;&gt;', r'<pre class="tool-result">&lt;&lt;&lt;DONE&gt;&gt;&gt;\1&lt;&lt;&lt;END&gt;&gt;&gt;</pre>', md_content)
    safe_json = json.dumps(md_content).replace('</', '<\\/')
    return f"""<!DOCTYPE html>
<html lang="da">
<head><meta charset="UTF-8"><title>{filename}</title>
<script src="https://cdn.jsdelivr.net/npm/dompurify@3/dist/purify.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
<style>
    body {{ font-family: 'Segoe UI', system-ui; max-width: 960px; margin: 40px auto; padding: 20px; background: #0f172a; color: #e2e8f0; }}
    h1 {{ border-bottom: 2px solid #334155; padding-bottom: 10px; }}
    h2 {{ border-bottom: 1px solid #334155; padding-bottom: 6px; margin-top: 28px; }}
    code {{ background: #1e293b; padding: 2px 6px; border-radius: 4px; }}
    pre {{ background: #1e293b; padding: 14px; border-radius: 8px; overflow-x: auto; }}
    pre code {{ background: none; padding: 0; }}
    .tool-call {{ border-left: 3px solid #f59e0b; background: #451a03; }}
    .tool-result {{ border-left: 3px solid #10b981; background: #064e3b; }}
    blockquote {{ border-left: 3px solid #3b82f6; padding-left: 14px; color: #94a3b8; }}
    table {{ border-collapse: collapse; width: 100%; }}
    th, td {{ border: 1px solid #334155; padding: 8px 12px; text-align: left; }}
    th {{ background: #1e293b; }}
    a {{ color: #60a5fa; }}
    img {{ max-width: 100%; border-radius: 8px; }}
</style></head>
<body><div id="content"></div>
<script>document.getElementById('content').innerHTML = DOMPurify.sanitize(marked.parse({safe_json}));</script>
</body></html>"""


@app.route("/")
def index() -> Any:
    """index."""
    index_path = os.path.join(STATIC_DIR, 'index.html')
    if os.path.exists(index_path):
        resp = send_from_directory(STATIC_DIR, 'index.html')
        resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        resp.headers['Pragma'] = 'no-cache'
        resp.headers['Expires'] = '0'
        return resp
    else:
        return """
        <!DOCTYPE html>
        <html>
        <head><title>Agenten</title></head>
        <body style="background:#0f172a; color:#e2e8f0; font-family: monospace; padding: 20px;">
            <h1>🤖 Agenten</h1>
            <p>Static mappe: """ + STATIC_DIR + """</p>
            <p>index.html ikke fundet. Opret venligst filen i static mappen.</p>
        </body>
        </html>
        """


@app.route('/flow')
def flow_page() -> Any:
    """flow page."""
    return send_from_directory(STATIC_DIR, 'flow.html')


@app.route('/static/<path:path>')
def serve_static(path: str) -> Any:
    """serve static.

    Args:
        path:"""
    resp = send_from_directory(STATIC_DIR, path)
    resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    resp.headers['Pragma'] = 'no-cache'
    resp.headers['Expires'] = '0'
    return resp
