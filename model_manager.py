"""Model management for LM Studio integration."""

import subprocess
import shutil
import os
import requests
import difflib
from typing import Any
import config
from urllib.parse import urlparse
from config import get_logger
from flask import Flask, request, jsonify, send_from_directory, Response, stream_with_context
from session_manager import SessionManager, _guard_json_body, agent, session_manager, current_session_id, execution_status, execution_status_lock, export_folder, export_folder_lock, list_sessions, create_session, rename_session, delete_session, manage_token, get_lang
from typing import Any, Generator
from lang import t, get_ui_translations
from i18n import K
from middleware import log, BASE_DIR, STATIC_DIR, app, _is_development_mode, _RateLimiter, rate_limiter, _rate_limit
from image_handler import sanitize_filename, _validate_image_content, _normalize_images, image_upload, image_list, image_clear, image_remove, list_python_files
import time
from datetime import datetime
from stream_manager import active_streams, active_streams_lock, current_session_lock, _file_mtime, VERSION_FILES, BUILD_INFO, serve_upload, preview_export
import model_manager
from session_manager import SessionManager, _guard_json_body, agent, session_manager, current_session_id, execution_status, execution_status_lock, export_folder, export_folder_lock, list_sessions, create_session, rename_session, delete_session, manage_token, get_lang, user_reply, save_layout, load_layout, get_session_prompts, get_context_for_prompt, add_prompt_to_session, reset_execution, execute_without_stream
import agent_autoresearch

log = get_logger(__name__)
import threading

_session_save_debounce: dict[str, float] = {}
_session_save_lock = threading.Lock()

_LMS_CACHE = None


def _get_lms_path() -> str | None:
    """get lms path."""
    global _LMS_CACHE
    if _LMS_CACHE is None:
        _LMS_CACHE = shutil.which('lms') or shutil.which('lms.exe') or os.path.join(
            os.environ.get('USERPROFILE', os.environ.get('HOME', '')), '.lmstudio', 'bin', 'lms.exe'
        )
    return _LMS_CACHE


def _rest_api_base() -> str:
    """Derive LM Studio REST API base (no /v1 path suffix) from config LLM_BASE_URL."""
    parsed = urlparse(config.LLM_BASE_URL)
    base = f"{parsed.scheme}://{parsed.hostname}"
    if parsed.port:
        base += f":{parsed.port}"
    if parsed.path and parsed.path.rstrip('/') != '/v1':
        base += parsed.path.rstrip('/')
    return base + '/api/v1'


def get_loaded_models() -> dict[str, Any] | None:
    """Fetch currently loaded models from LM Studio REST API."""
    if os.environ.get('OPENCODE_BASE_URL'):
        return None
    try:
        r = requests.get(f'{config.LLM_BASE_URL.replace("/v1", "")}/api/v1/models', timeout=5)
        if r.status_code == 200:
            data = r.json()
            loaded = {}
            for m in data.get('models', data.get('data', [])):
                mtype = m.get('type', '')
                if mtype and mtype != 'llm':
                    continue
                key = m.get('key', m.get('id', ''))
                instances = m.get('loaded_instances', [])
                loaded[key] = {
                    'key': key,
                    'loaded_instances': instances,
                    'is_loaded': len(instances) > 0,
                }
            return loaded
    except Exception as e:
        log.warning("Failed to fetch models: %s", e)
        return None


def is_model_loaded(model_key: str) -> tuple[bool, str | None]:
    """Check if a model (by any identifier) is currently loaded in LM Studio."""
    loaded = get_loaded_models()
    if not loaded:
        return False, None
    for info in loaded.values():
        if not info.get('is_loaded'):
            continue
        # Check both the model key and all loaded instance IDs
        if model_key == info['key'] or model_key in info['key'] or info['key'] in model_key:
            return True, info['key']
        for inst in info.get('loaded_instances', []):
            iid = inst.get('id', '')
            if iid and (model_key == iid or model_key in iid or iid in model_key):
                return True, iid
    return False, None


def get_available_models() -> list[str]:
    """Fetch all known models from LM Studio (OpenAI-compatible endpoint)."""
    if os.environ.get('OPENCODE_BASE_URL'):
        return []
    try:
        r = requests.get(f'{config.LLM_BASE_URL}/models', timeout=5)
        if r.status_code == 200:
            return [m['id'] for m in r.json().get('data', []) if 'embedding' not in m.get('id', '').lower()]
    except Exception as e:
        log.warning("Failed to fetch models from LM Studio: %s", e)
    return []


def get_all_rest_models() -> list[dict[str, Any]]:
    """Fetch ALL models (local + remote/LM Link) from LM Studio v1 REST API.
    LM Link is transparent — remote models appear with same key as local ones.
    Requests to localhost:1234 are automatically routed to the right device."""
    if os.environ.get('OPENCODE_BASE_URL'):
        return []
    try:
        r = requests.get(f'{_rest_api_base()}/models', timeout=5)
        if r.status_code == 200:
            data = r.json()
            models = []
            for m in data.get('models', data.get('data', [])):
                mtype = m.get('type', '')
                if mtype and mtype != 'llm':
                    continue
                key = m.get('key', m.get('id', ''))
                loaded_instances = m.get('loaded_instances', [])
                models.append({
                    'id': key,
                    'display_name': m.get('display_name', key),
                    'publisher': m.get('publisher', ''),
                    'state': 'loaded' if loaded_instances else 'not-loaded',
                    'is_loaded': len(loaded_instances) > 0,
                    'loaded_instances': loaded_instances,
                    'quantization': m.get('quantization', {}),
                    'params_string': m.get('params_string', ''),
                    'max_context_length': m.get('max_context_length', 0),
                })
            return models
    except Exception as e:
        log.warning("Failed to fetch REST models: %s", e)
    return []


def resolve_model_key(partial_name: str) -> str:
    """Fuzzy match a partial model name to a full key."""
    available = get_available_models()
    if not available:
        return partial_name
    if partial_name in available:
        return partial_name
    matches = difflib.get_close_matches(partial_name, available, n=1, cutoff=0.3)
    if matches:
        return matches[0]
    substring = [m for m in available if partial_name.lower() in m.lower()]
    if substring:
        return substring[0]
    return partial_name


def load_model(model_key: str, parallel: int = 4, identifier: str | None = None, callback: Any = None) -> tuple[bool, str]:
    """Load a model into LM Studio using lms CLI.
    Returns (success: bool, message: str)."""
    lms_path = _get_lms_path()
    if not lms_path:
        return False, 'lms CLI not found'
    if not os.path.exists(lms_path):
        return False, f'lms not found at {lms_path}'

    resolved = resolve_model_key(model_key)

    # SEC-004 Fix: Validate model key against available models to prevent command injection
    available_models = get_available_models()
    if not available_models or resolved not in available_models:
        return False, f'Invalid model key: {resolved}'

    if callback:
        callback(f'Loading {resolved}...')

    cmd = [lms_path, 'load', resolved, '--parallel', str(parallel), '--yes']
    if identifier:
        cmd.extend(['--identifier', identifier])

    try:
        import config
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=config.SUBPROCESS_TIMEOUT, errors='replace')
        if result.returncode == 0:
            return True, f'Loaded: {resolved}'
        else:
            return False, f'Error: {result.stderr.strip() or "unknown error"}'
    except subprocess.TimeoutExpired:
        return False, 'Timeout (120s) — model may still be loading'
    except Exception as e:
        return False, str(e)


def unload_model(identifier: str, callback: Any = None) -> tuple[bool, str]:
    """Unload a model from LM Studio using lms CLI.
    Use identifier='--all' to unload all models.
    Returns (success: bool, message: str)."""
    if not _get_lms_path():
        return False, 'lms CLI not found'

    if identifier == '--all':
        cmd = [_get_lms_path(), 'unload', '--all']
        if callback:
            callback('Unloading all models...')
    else:
        # Try to fuzzy-match identifier
        loaded = get_loaded_models() or {}
        match_id = identifier
        for key, info in loaded.items():
            for inst in info.get('loaded_instances', []):
                iid = inst.get('id', '')
                if identifier.lower() in iid.lower() or identifier.lower() in key.lower():
                    match_id = iid
                    break
        cmd = [_get_lms_path(), 'unload', match_id]
        if callback:
            callback(f'Unloading {match_id}...')

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30, errors='replace')
        if result.returncode == 0:
            return True, f'Unloaded: {identifier}'
        else:
            return False, f'Error: {result.stderr.strip() or "unknown error"}'
    except subprocess.TimeoutExpired:
        return False, 'Timeout'
    except Exception as e:
        return False, str(e)


@app.route("/api/sessions/load/<session_id>", methods=["GET"])
def load_session(session_id: str) -> Any:
    """load session.
    
    Args:
        session_id:"""
    global current_session_id
    agent.agent_log = []
    agent.execution_log = []
    session_data = session_manager.load_session(session_id)
    if session_data:
        current_session_id = session_id
        if session_data.get("tree"):
            from task_tree import TaskTree, TaskNode
            agent.task_tree = TaskTree(session_data.get("original_prompt", ""))
            agent.original_prompt = session_data.get("original_prompt", "")
            agent.full_prompt_with_context = session_data.get("full_prompt_with_context", "")
            agent.show_thinking = session_data.get("show_thinking", True)
            if session_data.get("decompose_model"):
                agent.decompose_llm.set_model(session_data["decompose_model"])
            if session_data.get("execute_model"):
                agent.llm.set_model(session_data["execute_model"])
        with agent.images_lock:
            agent.images = _normalize_images(session_data.get("images", []))
        from agent_files import auto_detect_workdir
        auto_detect_workdir(session_data.get("file_chunks"), session_data.get("original_prompt", ""))
        # Re-validate prompt against current code — append fresh VALIDERING entry
        agent.lang = session_data.get("lang", agent.lang)
        prompt_text = session_data.get("original_prompt", "") or ""
        if prompt_text:
            from agent_core import _validate_prompt_against_code
            note = _validate_prompt_against_code(agent, prompt_text)
            fresh_logs = list(agent.agent_log)  # _validate_prompt_against_code appends to agent
            agent.agent_log = []
            existing_timestamps = {e.get("timestamp") for e in (session_data.get("agent_log") or [])}
            session_data["agent_log"] = (session_data.get("agent_log") or []) + [
                e for e in fresh_logs if e.get("timestamp") not in existing_timestamps
            ]
        return jsonify({"success": True, "session": session_data})
    return jsonify({"success": False, "error": t(K.ERR_SESSION_NOT_FOUND, agent.lang)}), 404


@app.route("/api/sessions/save", methods=["POST"])
def save_current_session() -> Any:
    """save current session."""
    global current_session_id
    data = request.json
    session_id = data.get("session_id", current_session_id)
    
    if not session_id:
        return jsonify({"success": False, "error": t(K.ERR_NO_SESSION, agent.lang)}), 400
    now = time.time()
    with _session_save_lock:
        last = _session_save_debounce.get(session_id, 0)
        if now - last < 0.5:
            return jsonify({"success": True, "debounced": True})
        _session_save_debounce[session_id] = now
    with active_streams_lock:
        stream_agent = active_streams.get(session_id)
    source = stream_agent if stream_agent else agent

    def _merge_session(existing: dict) -> dict:
        existing_agent_log = existing.get("agent_log", [])
        existing_timestamps = {e.get("timestamp") for e in existing_agent_log}
        merged_log = existing_agent_log + [
            e for e in (source.agent_log or [])
            if e.get("timestamp") not in existing_timestamps
        ]
        existing.update({
            "id": session_id,
            "name": data.get("name", existing.get("name", t(K.SESSION_DEFAULT_NAME, agent.lang).format(n=session_id[:8]))),
            "tree": data.get("tree") or existing.get("tree") or (source.task_tree_to_dict() if source.task_tree else None),
            "layout": data.get("layout") or existing.get("layout"),
            "execution_log": source.execution_log or existing.get("execution_log", []),
            "agent_log": merged_log,
            "original_prompt": data.get("original_prompt") or source.original_prompt or "",
            "full_prompt_with_context": getattr(source, 'full_prompt_with_context', '') or '',
            "show_thinking": data.get("show_thinking", source.show_thinking),
            "template": data.get("template") or getattr(source, 'active_template', None) or "fri",
            "lang": data.get("lang") or getattr(source, 'lang', 'da'),
            "ui_lang": data.get("ui_lang") or data.get("lang") or getattr(source, 'lang', 'da'),
            "prompt_history": data.get("prompt_history") or existing.get("prompt_history", []),
            "file_context": data.get("file_context", ""),
            "file_chunks": getattr(source, 'file_chunks', None) or existing.get("file_chunks", {}),
            "images": getattr(source, 'images', None) or existing.get("images", []),
            "created": existing.get("created", datetime.now().isoformat()),
            "learned_knowledge": existing.get("learned_knowledge", []),
            "decompose_model": data.get("decompose_model") or existing.get("decompose_model") or getattr(source.decompose_llm, 'model', ''),
            "execute_model": data.get("execute_model") or existing.get("execute_model") or getattr(source.llm, 'model', ''),
        })
        return existing
    session_manager.update_session(session_id, _merge_session)
    current_session_id = session_id
    return jsonify({"success": True, "session_id": session_id})


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


@app.route("/api/stop", methods=["POST"])
def stop_execution() -> Any:
    """stop execution."""
    global current_session_id
    
    if current_session_id:
        with active_streams_lock:
            if current_session_id in active_streams:
                active_streams[current_session_id].stop_requested = True
                return jsonify({"success": True})
    agent.stop_requested = True
    
    return jsonify({"success": True})


def _ensure_model_loaded(model_key: str | None) -> None:
    """ensure model loaded.
    
    Args:
        model_key:"""
    if not model_key:
        return
    if app.config.get("TESTING"):
        return
    base = config.LLM_BASE_URL
    if 'opencode' in base or ('localhost' not in base and '127.0.0.1' not in base):
        return
    loaded, matched = model_manager.is_model_loaded(model_key)
    if loaded:
        log.info("Model already loaded: %s", matched)
        return matched
    log.info("Loading model: %s...", model_key)
    ok, msg = model_manager.load_model(model_key)
    if ok:
        log.info(msg)
    else:
        log.warning(msg)



TEMPLATE_GUIDANCE = {
    "resume": {
        "keywords": ["resume", "referat", "opsummer", "analyser", "review", "beskriv", "sammenfat", "referér", "sammendrag"],
        "examples": [
            'Lav et resume af [filnavn.py]',
            'Opsummer [filnavn] i et kort referat',
            'Analyser [fil] og lav en struktureret gennemgang',
        ],
        "hint": "Vælg resume-skabelonen når du vil have en struktureret gennemgang af en bestemt fil."
    },
    "kodeanalyse": {
        "keywords": ["analyser", "kode", "gennemgå", "review", "debug", "sikkerhed", "struktur", "arkitektur", "kodekvalitet", "fejl", "sårbarhed"],
        "examples": [
            'Analyser koden i [filnavn.py]',
            'Gennemgå [fil] for fejl og sikkerhedsproblemer',
            'Review koden i [fil] og vurder kodekvaliteten',
        ],
        "hint": "Vælg kodeanalyse-skabelonen når du skal have analyseret en konkret fil eller kodebase."
    },
    "diffanalyse": {
        "keywords": ["diff", "forskel", "ændring", "change", "commit", "pull", "merge", "version", "gren", "branch"],
        "examples": [
            'Analyser forskellen mellem branch-a og branch-b',
            'Gennemgå de seneste commits og vurder risiko',
            'Sammenlign to versioner af [fil]',
        ],
        "hint": "Vælg diffanalyse-skabelonen når du sammenligner to versioner eller branches."
    },
    "agenten": {
        "keywords": ["git", "github", "commit", "push", "branch", "pull request", "pr", "workflow", "repository", "repo"],
        "examples": [
            'Opret en branch, commit, push og lav en PR',
            'Git workflow: opret branch commit push PR',
            'Commit og push mine ændringer, og opret en pull request',
        ],
        "hint": "Vælg PR Agenten-skabelonen når du skal udføre et git/github workflow."
    },
    "programmering": {
        "keywords": ["programmer", "opret", "implementer", "byg", "skriv", "kod", "app", "feature", "funktion", "system", "modul", "klasse", "program", "tool", "værktøj", "library", "bibliotek", "ret", "fix", "bug", "fejl", "compile", "ændr", "opdater", "rediger", "tilføj", "slet", "rettelse", "debug"],
        "examples": [
            'Opret en Flask-app med en health endpoint',
            'Implementer en funktion der beregner moms i Python',
            'Ret compile-fejlene i C:\\Dev\\Trading\\src\\routes\\markets.js',
            'Byg et kommandolinje-værktøj der kan søge efter filer',
        ],
        "hint": "Vælg programmeringsskabelonen når du skal designe, implementere eller rette kode i et projekt."
    },
    "python-arkitektur": {
        "keywords": ["arkitektur", "planlæg", "design", "struktur", "python", "flask", "komponent", "dokumentér", "systemoversigt", "modulopdeling"],
        "examples": [
            'Analyser [projekt] og planlæg arkitektur for en Python/Flask version',
            'Design arkitekturen for et nyt system med Flask og SQLAlchemy',
            'Planlæg komponentstruktur og dataflow for en webapp',
        ],
        "hint": "Vælg Python Arkitektur-skabelonen når du skal planlægge og dokumentere en systemarkitektur."
    },
    "billedanalyse": {
        "keywords": ["billede", "billed", "image", "screenshot", "skærmbillede", "foto", "photo", "png", "jpg", "jpeg", "analyser billed", "hvad ser du", "beskriv billedet"],
        "examples": [
            'Analyser dette skærmbillede af en fejlmeddelelse',
            'Hvad ser du på dette billede af en UI?',
            'Beskriv indholdet af dette foto og vurder kvaliteten',
        ],
        "hint": "Vælg Billedanalyse-skabelonen når du skal have analyseret et billede eller skærmbillede. Resultatet gemmes automatisk i en .md fil."
    },
    "bugfix": {
        "keywords": ["bug", "fix", "fejl", "issue", "defekt", "test", "tdd", "red", "green", "refactor", "ret", "rettelse", "patch"],
        "examples": [
            'Fix BUG-003: None crash i solve_task_stream',
            'Ret fejlen i [fil] og skriv test først',
            'Anvend TDD: skriv test, implementer fix, verificér',
        ],
        "hint": "Vælg Bugfix-skabelonen når du skal rette en kendt bug med TDD-workflow: test først → implementer → verificér."
    },
}



def _validate_template_prompt(prompt: str, template: str) -> dict:
    """validate template prompt.
    
    Args:
        prompt (str):
        template (str):
    
    Returns:
        dict"""
    if not template:
        return {"warning": "", "suggestion": "", "suggested_template": "", "matches": 0, "total": 0}
    
    if template == "fri":
        # Even with free template, check if a better template can be suggested
        try:
            from skill_loader import SkillLoader
            skills = SkillLoader.load_all()
            better = SkillLoader.suggest_template(prompt, skills)
            if better:
                better_guidance = TEMPLATE_GUIDANCE.get(better)
                hint = better_guidance["hint"] if better_guidance else ""
                warning = (
                    f"Din prompt matcher skabelonen '🐛 {better}' bedre.\n{hint}"
                )
                return {"warning": warning, "suggestion": better, "suggested_template": better, "matches": 0, "total": 0}
        except Exception as e:
            log.warning("Template validation error: %s", e)
        return {"warning": "", "suggestion": "", "suggested_template": "", "matches": 0, "total": 0}
    
    guidance = TEMPLATE_GUIDANCE.get(template)
    if not guidance:
        return {"warning": "", "suggestion": "", "suggested_template": "", "matches": 0, "total": 0}
    
    prompt_lower = prompt.lower()
    matches = sum(1 for kw in guidance["keywords"] if kw in prompt_lower)
    total = len(guidance["keywords"])
    
    if matches == 0:
        # Find better template via SkillFlow scoring
        suggested = ""
        suggested_template = ""
        try:
            from skill_loader import SkillLoader
            skills = SkillLoader.load_all()
            better = SkillLoader.suggest_template(prompt, skills)
            if better and better != template:
                suggested_template = better
                better_guidance = TEMPLATE_GUIDANCE.get(better)
                if better_guidance:
                    suggested = f"\n\nForslag: Brug skabelonen '{better}' i stedet.\n{better_guidance['hint']}"
        except Exception as e:
            log.warning("Template suggestion error: %s", e)
        
        examples = "\n".join(f"  • {ex}" for ex in guidance["examples"])
        warning = (
            f"Din prompt ligner ikke en opgave til skabelonen '{template}'.{suggested}\n\n"
            f"Eksempler på gode prompts til '{template}':\n{examples}"
        )
        return {"warning": warning, "suggestion": suggested_template, "suggested_template": suggested_template, "matches": matches, "total": total}
    
    return {"warning": "", "suggestion": "", "suggested_template": "", "matches": matches, "total": total}



@app.route("/api/decompose", methods=["POST"])
def decompose() -> Any:
    """decompose."""
    data = request.json
    prompt = data.get("prompt", "")
    session_id = data.get("session_id")
    show_thinking = data.get("show_thinking", True)
    files = data.get("files", [])
    template = data.get("template")
    lang = data.get("lang", "da")
    ui_lang = data.get("ui_lang", lang)
    
    if not prompt:
        return jsonify({"success": False, "error": t(K.ERR_NO_PROMPT, ui_lang)}), 400
    
    global current_session_id
    if session_id:
        current_session_id = session_id
    elif not current_session_id:
        current_session_id, _ = session_manager.create_session(prompt[:100])
    
    agent.show_thinking = show_thinking
    agent.lang = lang
    session_context = session_manager.get_knowledge_for_context(current_session_id, prompt)
    
    # Guard: billedanalyse needs an image
    image_warning = ""
    with agent.images_lock:
        has_images = bool(agent.images)
    if template == "billedanalyse" and not has_images and not files:
        image_warning = "🖼️  Billedanalyse kræver et billede! Upload et billede med 🖼 knappen før du kører Nedbryd."
        agent._log("WARNING", "Billedanalyse uden billede", image_warning)

    # Guard: programmering (greenfield) warns but does not block if .py files exist
    non_greenfield = False
    if template == "programmering":
        FRAMEWORK_PY = {"api_server.py", "agent_core.py", "agent_tasks.py", "agent_skills.py", "agent_files.py", "agent_issues.py", "agent_tree.py", "agent_git.py", "agent_phase_checks.py", "agent_wta.py", "core_analytics.py", "agent_logs.py", "tools.py", "i18n.py", "lang.py", "config.py", "task_tree.py", "llm_wrapper.py", "model_manager.py", "session_manager.py", "flow_builder.py", "skill_evolution.py", "skill_loader.py", "skill_tracker.py", "refactoring_engine.py", "github_wrapper.py"}
        check_dir = os.environ.get('AGENT_WORKDIR') or '.'
        existing_py = [f for f in os.listdir(check_dir) if f.endswith(".py") and f not in FRAMEWORK_PY and os.path.isfile(os.path.join(check_dir, f))]
        if existing_py:
            non_greenfield = True
            log.warning("Workdir indeholder allerede .py-filer: %s — kører programmering i vedligeholdelsestilstand", ', '.join(existing_py[:5]))
    
    # Validate prompt against selected template
    validation = _validate_template_prompt(prompt, template)
    if validation["warning"]:
        log.warning("Template warning (%s): only %s/%s keywords matched", template, validation['matches'], validation['total'])
    
    log.info("Decomposing: %s...%s", prompt[:50], f" template: {template}" if template else "")
    if files:
        log.info("With %s files", len(files))

    decompose_model = data.get("decompose_model")
    execute_model = data.get("execute_model")
    if decompose_model:
        agent.decompose_llm.set_model(decompose_model)
    if execute_model:
        agent.llm.set_model(execute_model)

    _ensure_model_loaded(agent.decompose_llm.model)

    # Reset re-decompose counter — each explicit user click is a fresh attempt
    agent._redecompose_count = 0

    try:
        tree = agent.decompose_prompt(prompt, files=files, template=template)

        def _update(data: dict) -> dict:
            data.update({
                "id": current_session_id,
                "name": prompt[:100],
                "tree": tree,
                "execution_log": agent.execution_log or data.get("execution_log", []),
                "agent_log": agent.agent_log,
                "original_prompt": agent.original_prompt,
                "full_prompt_with_context": agent.full_prompt_with_context,
                "show_thinking": agent.show_thinking,
                "template": template,
                "lang": agent.lang,
                "ui_lang": ui_lang,
                "file_context": files,
                "file_chunks": agent.file_chunks,
                "decompose_model": agent.decompose_llm.model,
                "execute_model": agent.llm.model
            })
            return data
        session_manager.update_session(current_session_id, _update)
        session_manager.add_prompt_result(current_session_id, prompt, t(K.LOG_DECOMPOSED, agent.lang), tree)
        
        return jsonify({
            "success": True, 
            "tree": tree,
            "original_prompt": agent.original_prompt,
            "session_id": current_session_id,
            "has_context": bool(session_context),
            "log": agent.agent_log[-20:] if agent.agent_log else [],
            "template_warning": validation.get("warning", ""),
            "suggested_template": validation.get("suggested_template", ""),
            "image_warning": image_warning,
        })
    except Exception as e:
        log.error("Error: %s", e)
        return jsonify({"success": False, "error": str(e)}), 500



@app.route("/api/redecompose", methods=["POST"])
def redecompose() -> Any:
    """Re-decompose tree in a new LLM language while preserving status/results.

    Expects: ``{"session_id": str, "lang": str}``.
    Re-reads the session's original prompt and re-runs decomposition with
    the new language, then maps old node statuses to new nodes by name.
    """
    data = request.json
    session_id = data.get("session_id")
    lang = data.get("lang", "da")

    if not session_id:
        return jsonify({"success": False, "error": t(K.ERR_NO_SESSION, lang)}), 400

    session_data = session_manager.load_session(session_id)
    if not session_data:
        return jsonify({"success": False, "error": t(K.ERR_SESSION_NOT_FOUND, lang)}), 404

    old_tree = session_data.get("tree")
    old_children = old_tree.get("children", []) if old_tree else []
    old_prompt = session_data.get("original_prompt", "")
    old_template = session_data.get("template", "fri")
    old_file_context = session_data.get("file_context", [])

    if not old_prompt:
        return jsonify({"success": False, "error": t(K.ERR_NO_PROMPT, lang)}), 400

    old_status_map: dict[str, dict] = {}
    for child in old_children:
        old_status_map[child.get("name", "").strip().lower()] = {
            "status": child.get("status", "pending"),
            "result": child.get("result"),
            "success_criteria": child.get("success_criteria", []),
        }

    agent.lang = lang
    agent.active_template = old_template

    prefix = "# RE-DECOMPOSE i nyt sprog\n"
    full_prompt = prefix + old_prompt

    try:
        new_tree = agent.decompose_prompt(full_prompt, files=old_file_context, template=old_template)
    except Exception as e:
        return jsonify({"success": False, "error": f"Fejl under gen-nedbrydning: {e}"}), 500

    new_children = new_tree.get("children", []) or []
    for child in new_children:
        name_lower = (child.get("name", "") or "").strip().lower()
        old_state = old_status_map.get(name_lower)
        if old_state:
            child["status"] = old_state["status"]
            child["result"] = old_state["result"]
            child["success_criteria"] = old_state.get("success_criteria", child.get("success_criteria", []))

    session_manager.update_session(session_id, lambda d: {**d, "tree": new_tree, "lang": lang})

    return jsonify({
        "success": True,
        "tree": new_tree,
        "lang": lang,
    })


def _count_tasks(node: Any) -> int:
    """count tasks.
    
    Args:
        node:"""
    total = 1
    for child in node.children:
        total += _count_tasks(child)
    return total



def _check_client(agent: Any) -> bool:
    """check client.
    
    Args:
        agent:"""
    return agent.stop_requested



def _count_source_symbols(source_file: str = "api_server.py") -> int:
    """Count top-level symbols in a Python source file.

    Returns -1 on error (file not found, parse error).
    """
    try:
        import agent_files as _af
        res = _af.list_symbols(filepath=source_file)
        if isinstance(res, dict) and res.get("success"):
            sym_data = res.get("result", res)
            return len(sym_data.get("symbols", []))
    except Exception:
        pass
    return -1



@app.route("/api/autoresearch/events/<research_id>", methods=["GET"])
def autoresearch_events(research_id: str) -> Any:
    """Return autoresearch events since a timestamp."""
    since = request.args.get("since", "0")
    try:
        since_f = float(since)
    except (ValueError, TypeError):
        since_f = 0.0
    events = agent_autoresearch.get_events(research_id, since_f)
    return jsonify({"success": True, "events": events})



@app.route("/api/autoresearch/sessions", methods=["GET"])
def autoresearch_sessions() -> Any:
    """Return active autoresearch sessions."""
    sessions = agent_autoresearch.get_active_sessions()
    return jsonify({"success": True, "sessions": sessions})



@app.route("/api/autoresearch/<research_id>/pause", methods=["POST"])
def autoresearch_pause(research_id: str) -> Any:
    """Pause a running autoresearch session."""
    ok = agent_autoresearch.pause_session(research_id)
    if ok:
        return jsonify({"success": True, "status": "paused"})
    return jsonify({"success": False, "error": "Session not found or not running"}), 404



@app.route("/api/autoresearch/<research_id>/resume", methods=["POST"])
def autoresearch_resume(research_id: str) -> Any:
    """Resume a paused autoresearch session."""
    ok = agent_autoresearch.resume_session(research_id)
    if ok:
        return jsonify({"success": True, "status": "running"})
    return jsonify({"success": False, "error": "Session not found or not paused"}), 404



@app.route("/api/autoresearch/toggle", methods=["POST"])
def autoresearch_toggle() -> Any:
    """Enable or disable autoresearch."""
    data = request.json or {}
    enabled = bool(data.get("enabled", False))
    agent.autoresearch_enabled = enabled
    agent._log("AUTOR", f"Auto-research {'aktiveret' if enabled else 'deaktiveret'}", "")
    return jsonify({"success": True, "autoresearch_enabled": enabled})



@app.route("/api/autoresearch/status", methods=["GET"])
def autoresearch_status() -> Any:
    """Return whether autoresearch is enabled."""
    return jsonify({
        "success": True,
        "autoresearch_enabled": getattr(agent, "autoresearch_enabled", False),
    })



@app.route("/api/autoresearch/run/<issue_id>", methods=["POST"])
def autoresearch_run(issue_id: str) -> Any:
    """Manually start autoresearch for an issue."""
    agent_autoresearch.start_research_for_issue(agent, issue_id)
    return jsonify({"success": True, "issue_id": issue_id})
