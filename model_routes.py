from flask import Flask, request, jsonify, send_from_directory, Response, stream_with_context
import os
from typing import Any, Generator
from config import app, get_logger, log
from lang import t, get_ui_translations
from session_manager import SessionManager, _guard_json_body, agent, session_manager, current_session_id, execution_status, execution_status_lock, export_folder, export_folder_lock
import model_manager
import config

@app.route("/api/tools/token", methods=["GET", "POST"])
def manage_token() -> Any:
    """manage token."""
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if request.method == "GET":
        has_token = False
        if os.path.exists(env_path):
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip().startswith("GITHUB_TOKEN="):
                        has_token = True
                        break
        return jsonify({"success": True, "exists": has_token, "has_token": has_token})

    data = request.json
    raw = data.get("content", "")
    # Parse token from "GITHUB_TOKEN=abc123\n" format
    token = ""
    for line in raw.splitlines():
        line = line.strip()
        if line.startswith("GITHUB_TOKEN="):
            token = line.split("=", 1)[1]
            break
    if not token:
        return jsonify({"success": False, "error": "GITHUB_TOKEN ikke fundet i indhold"}), 400

    # Only write GITHUB_TOKEN — never arbitrary content
    lines = []
    updated = False
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip().startswith("GITHUB_TOKEN="):
                    lines.append(f"GITHUB_TOKEN={token}\n")
                    updated = True
                else:
                    lines.append(line)
    if not updated:
        lines.append(f"GITHUB_TOKEN={token}\n")
    with open(env_path, "w", encoding="utf-8") as f:
        f.writelines(lines)
    return jsonify({"success": True, "message": "GITHUB_TOKEN gemt"})


@app.route("/api/lang/<lang>")
def get_lang(lang: str) -> Any:
    """get lang.

    Args:
        lang:"""
    resp = jsonify(get_ui_translations(lang))
    resp.headers['Cache-Control'] = 'no-cache'
    return resp


@app.route("/api/models")
def get_models() -> Any:
    """get models."""
    openai_models = agent.llm.list_models()
    loaded = None
    rest_models = []
    if app.config.get("TESTING") or 'opencode' in config.LLM_BASE_URL or ('localhost' not in config.LLM_BASE_URL and '127.0.0.1' not in config.LLM_BASE_URL):
        merged = sorted(openai_models)
    else:
        loaded = model_manager.get_loaded_models()
        rest_models = model_manager.get_all_rest_models()
        all_ids = set(openai_models)
        if rest_models:
            for m in rest_models:
                mid = m.get('id')
                if mid:
                    all_ids.add(mid)
        if loaded:
            for k in loaded:
                if loaded[k].get('is_loaded'):
                    all_ids.add(k)
                    for inst in loaded[k].get('loaded_instances', []):
                        all_ids.add(inst.get('id', ''))
        merged = sorted(all_ids)

    log.info("Merged models (%s): %s...", len(merged), str(merged[:10]))

    return jsonify({
        "models": merged,
        "openai_models": openai_models,
        "loaded": loaded,
        "rest_models": rest_models,
        "current": agent.llm.model,
        "decompose_model": agent.decompose_llm.model,
    })


@app.route("/api/models/set", methods=["POST"])
def set_model() -> Any:
    """set model."""
    data = request.json
    model = data.get("model", agent.llm.model)
    dtype = data.get("type", "execute")
    if dtype == "decompose":
        agent.decompose_llm.set_model(model)
    else:
        agent.llm.set_model(model)
    if current_session_id:
        def _update_model(data: dict) -> dict:
            data["decompose_model"] = agent.decompose_llm.model
            data["execute_model"] = agent.llm.model
            return data
        session_manager.update_session(current_session_id, _update_model)
    return jsonify({"success": True, "model": model, "type": dtype})


@app.route("/api/models/loaded")
def loaded_models() -> Any:
    """loaded models."""
    return jsonify(model_manager.get_loaded_models())


@app.route("/api/models/load", methods=["POST"])
def load_model_route() -> Any:
    """load model route."""
    data = request.json
    key = data.get("key", "")
    if not key:
        return jsonify({"success": False, "error": "No model key"}), 400
    ok, msg = model_manager.load_model(key)
    return jsonify({"success": ok, "error": msg if not ok else None, "message": msg})


@app.route("/api/models/unload", methods=["POST"])
def unload_model_route() -> Any:
    """unload model route."""
    data = request.json
    identifier = data.get("identifier", "--all")
    ok, msg = model_manager.unload_model(identifier)
    return jsonify({"success": ok, "message": msg})
