"""Utility-ruter: log, status, search, flow og version."""
from typing import Any
from flask import Flask, request, jsonify, send_from_directory, Response, stream_with_context
from session_manager import SessionManager, _guard_json_body, agent, session_manager, execution_status, execution_status_lock, export_folder_lock
from typing import Any, Generator
from config import get_logger, log, BASE_DIR, STATIC_DIR, app, VERSION_FILES, BUILD_INFO, _is_development_mode, _file_mtime
import os

# Placeholder - symboler flyttes fra api_server.py via batch_extract_symbols



@app.route("/api/log", methods=["GET"])
def get_log() -> Any:
    """get log."""
    return jsonify({"log": agent.agent_log})


@app.route("/api/status", methods=["GET"])
def status() -> Any:
    """status."""
    with execution_status_lock:
        es = dict(execution_status)
    workdir = os.environ.get('AGENT_WORKDIR', '')
    return jsonify({
        **agent.get_agent_status(),
        "execution": es,
        "workdir": workdir
    })


@app.route("/api/search", methods=["POST"])
def search() -> Any:
    """search."""
    data = request.json
    query = data.get("query", "")
    if not query:
        return jsonify({"success": False, "error": "No query"}), 400
    results = agent.searcher.search(query)
    return jsonify({"success": True, "search_results": results})


@app.route("/api/flow/search", methods=["POST"])
def flow_search() -> Any:
    """flow search."""
    data = request.json
    query = data.get("query", "")
    try:
        max_results = int(data.get("maxResults", 10))
    except (ValueError, TypeError):
        max_results = 10
    if not query:
        return jsonify({"success": False, "error": "No query"}), 400

    from ddg_search import websearch
    results = websearch(query, max_results)
    return jsonify({"success": True, "query": query, "results": results})



@app.route("/api/flow/generate", methods=["POST"])
def flow_generate() -> Any:
    """flow generate."""
    data = request.json
    topic = data.get("topic", "")
    try:
        max_results = int(data.get("maxResults", 10))
    except (ValueError, TypeError):
        max_results = 10

    if not topic:
        return jsonify({"success": False, "error": "No topic"}), 400

    from ddg_search import websearch
    from flow_builder import generate_research_flow, flow_to_mermaid_full, format_flow_json

    results = websearch(topic, max_results)
    flow = generate_research_flow(topic, results)
    mermaid = flow_to_mermaid_full(flow)
    flow_str = format_flow_json(flow)

    return jsonify({
        "success": True,
        "topic": topic,
        "results": results,
        "flow": flow,
        "flow_json": flow_str,
        "mermaid": mermaid
    })



@app.route("/api/build-module", methods=["POST"])
def build_module() -> Any:
    """build module."""
    result = agent.suggest_new_module()
    return jsonify({"success": True, "module_result": result})


@app.route("/api/version", methods=["GET"])
def version() -> Any:
    """version."""
    return jsonify({"success": True, "started": BUILD_INFO.get("started", "?"), "version": {k:v for k,v in BUILD_INFO.items() if k != "started"}})
