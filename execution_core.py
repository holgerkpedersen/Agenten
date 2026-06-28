from flask import Flask, request, jsonify, send_from_directory, Response, stream_with_context
from server_config import app
from session_manager import SessionManager, _guard_json_body, agent, session_manager, execution_status, execution_status_lock, export_folder_lock
from stream_execution import _save_session_data as _ssd
from decomposition import TEMPLATE_GUIDANCE
from typing import Any, Generator
from lang import t, get_ui_translations
from i18n import K
from config import get_logger, log
import model_manager
import config
import os

@app.route("/api/stop", methods=["POST"])
def stop_execution() -> Any:
    """stop execution."""
    current_session_id = session_manager.current_session_id

    if current_session_id:
        with active_streams_lock:
            if current_session_id in active_streams:
                active_streams[current_session_id].stop_requested = True
                return jsonify({"success": True})
    agent.stop_requested = True

    return jsonify({"success": True})



@app.route("/api/execute-pause", methods=["POST"])
def pause_execution() -> Any:
    """Pause execution — set pause flag so solve_task_stream saves messages."""
    current_session_id = session_manager.current_session_id
    if current_session_id:
        with active_streams_lock:
            sa = active_streams.get(current_session_id)
            if sa:
                sa.stop_requested = True
                sa._pause_requested = True
                return jsonify({"success": True})
    return jsonify({"success": False, "error": "Ingen aktiv stream at pause"})



@app.route("/api/reply", methods=["POST"])
def user_reply() -> Any:
    """user reply."""
    data = request.json
    msg = data.get("message", "")
    if not msg:
        return jsonify({"success": False, "error": "Empty message"}), 400
    agent.pending_reply = msg
    agent._log("USER", "Bruger svar", msg[:100])
    return jsonify({"success": True})


# ============ AGENT ENDPOINTS ============
@app.route("/api/reset-execution", methods=["POST"])
def reset_execution() -> Any:
    """reset execution."""
    agent.reset_execution()
    return jsonify({"success": True, "message": t(K.UI_STREAM_RESET, agent.lang)})


@app.route("/api/execute-without-stream", methods=["POST"])
def execute_without_stream() -> Any:
    """execute without stream."""
    global execution_status
    current_session_id = session_manager.current_session_id
    if agent.task_tree is None:
        return jsonify({"success": False, "error": t(K.ERR_DECOMPOSE_FIRST, agent.lang)}), 400


    total_tasks = _count_tasks(agent.task_tree.root)
    completed = 0

    def execute_with_progress(node: Any) -> str:
        """execute with progress.

        Args:
            node:"""
        nonlocal completed
        with execution_status_lock:
            execution_status["current_task"] = node.name
        for child in node.children:
            execute_with_progress(child)
        result = agent.solve_task(node, agent.original_prompt)
        completed += 1
        with execution_status_lock:
            execution_status["progress"] = int((completed / total_tasks) * 100)
            execution_status["log"].append({"task": node.name, "status": node.status, "result": result[:200]})
        return result

    try:
        results = execute_with_progress(agent.task_tree.root)
        with execution_status_lock:
            execution_status["results"] = results
            execution_status["running"] = False
        # Save session after execution so tree status is persisted
        try:
            _ssd(current_session_id, agent, "da")
        except Exception:
            pass
        return jsonify({"success": True, "results": results, "total_tasks": total_tasks})
    except Exception as e:
        import traceback
        log.error("execute-without-stream FAILED: %s\n%s", e, traceback.format_exc())
        with execution_status_lock:
            execution_status["running"] = False
        # Save session even on failure so partial progress is captured
        try:
            _ssd(current_session_id, agent, "da")
        except Exception:
            pass
        return jsonify({"success": False, "error": str(e)}), 500


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

    if session_id:
        session_manager.current_session_id = session_id
    elif not session_manager.current_session_id:
        session_manager.current_session_id, _ = session_manager.create_session(prompt[:100])
    current_session_id = session_manager.current_session_id

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



def _extract_batch_results(agent: Any) -> list[dict]:
    """Extract successful batch_extract_symbols results from tool_log.

    Returns a list of dicts with 'symbols' and 'target' keys.
    """
    from pathlib import PurePath as _PurePath
    results = []
    for entry in getattr(agent, "_tool_log", []):
        if entry.get("tool") == "batch_extract_symbols" and entry.get("success"):
            args = entry.get("args", {})
            symbols = args.get("symbols", "")
            target = _PurePath(args.get("target", "")).name
            results.append({"symbols": symbols, "target": target})
    return results



def _extract_retry_context(node: Any, agent: Any, full_response: str,
                           symbols_before: int = -1, symbols_after: int = -1) -> dict:
    """Extract failure context for retry.

    Captures what went wrong, what tools were called,
    and relevant log entries so the next attempt can learn.
    """
    tool_log = getattr(agent, "_tool_log", []) or []
    phase_tools = [t for t in tool_log
                   if t.get("phase", "").lower() == (node.name or "").lower()]

    called_tools = {}
    for t in phase_tools:
        tname = t.get("tool", "")
        key = tname + str(t.get("args", {}))
        called_tools[key] = called_tools.get(key, 0) + 1

    called_names = {k.split("{")[0] for k in called_tools}
    active = set(agent.tool_registry.active_tools or []) if hasattr(agent, 'tool_registry') else set()
    required_action = {"edit_file", "write_file", "update_issue_status"}
    needed = active & required_action
    uncalled = needed - called_names

    moved = max(0, symbols_before - symbols_after) if symbols_before >= 0 and symbols_after >= 0 else 0

    return {
        "phase": node.name,
        "failure_reason": full_response[:300],
        "called_tools": list(called_names),
        "uncalled_tools": list(uncalled),
        "tool_count": len(phase_tools),
        "symbols_moved": moved,
        "symbols_before": symbols_before,
        "symbols_after": symbols_after,
        "successful_batches": _extract_batch_results(agent),
        "all_messages": [],
    }
