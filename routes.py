"""Routes module — extracted from api_server.py by REFAC session."""

from flask import Blueprint, request, jsonify, send_from_directory, current_app
import os

routes_bp = Blueprint("routes", __name__)


@routes_bp.route("/api/files/<path:filename>", methods=["GET"])
def upload_file(filename):
    return send_from_directory(current_app.config.get("UPLOAD_FOLDER", "uploads"), filename)


@routes_bp.route("/api/files/<path:filename>", methods=["GET", "POST"])
def read_file(filename):
    return jsonify({"success": False, "error": "Not implemented"})


@routes_bp.route("/api/files/<path:filename>", methods=["GET"])
def view_file(filename):
    return send_from_directory(current_app.config.get("UPLOAD_FOLDER", "uploads"), filename)


def get_current_session():
    from api_server import current_session_id
    return current_session_id

from flask import Flask, request, jsonify, send_from_directory, Response, stream_with_context


@app.route("/")
def index() -> Any:
    """index."""
    index_path = os.path.join(STATIC_DIR, 'index.html')
    if os.path.exists(index_path):
        return send_from_directory(STATIC_DIR, 'index.html')
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
