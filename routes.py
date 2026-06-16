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
