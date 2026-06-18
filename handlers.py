from flask import Flask, request, jsonify, send_from_directory, Response, stream_with_context
from session_manager import SessionManager, _guard_json_body, agent, session_manager, current_session_id, execution_status, execution_status_lock, export_folder, export_folder_lock, list_sessions, create_session, rename_session, delete_session, manage_token, get_lang, user_reply, save_layout, load_layout, get_session_prompts, get_context_for_prompt, add_prompt_to_session, reset_execution, execute_without_stream
import os
from typing import Any, Generator
from middleware import log, BASE_DIR, STATIC_DIR, app, _is_development_mode, _RateLimiter, rate_limiter, _rate_limit
from lang import t, get_ui_translations
from i18n import K
from agent_phase_checks import TEMPLATE_PHASE_CHECKS, check_phase_done
from stream_manager import active_streams, active_streams_lock, current_session_lock, _file_mtime, VERSION_FILES, BUILD_INFO, serve_upload, preview_export

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



def _format_phase_check_description(phase_name: str, spec: dict[str, Any], lang: str = "da") -> str:
    """Format a phase check spec as a human-readable description.

    Uses i18n keys (description_key) when available, falling back to
    hardcoded Danish descriptions for backward compatibility.

    Args:
        phase_name: Name of the phase (e.g. "Plan", "Ekstraher")
        spec: The check spec dict from TEMPLATE_PHASE_CHECKS
        lang: Language code (da/en/es/zh) for i18n lookups
    """
    desc_key = spec.get("description_key")
    if desc_key:
        translated = t(desc_key, lang)
        if translated != desc_key:
            return translated
    explicit = spec.get("description")
    if explicit:
        return explicit
    check_type = spec.get("type", "")
    if check_type == "file_exists":
        paths = spec.get("paths", [])
        require_all = spec.get("require_all", True)
        if len(paths) == 1:
            return t(K.PHASE_CHECK_FILE_EXISTS_SINGLE, lang).format(path=f"`{paths[0]}`")
        if require_all:
            listed = ", ".join(f"`{p}`" for p in paths)
            return t(K.PHASE_CHECK_FILE_EXISTS_ALL, lang).format(paths=listed)
        listed = ", ".join(f"`{p}`" for p in paths)
        return t(K.PHASE_CHECK_FILE_EXISTS_ANY, lang).format(paths=listed)
    if check_type == "files_from_plan":
        plan_path = spec.get("plan_path", "refactor_plan.md")
        ext = spec.get("ext", ".py")
        min_files = int(spec.get("min_files", 1))
        if min_files <= 1:
            return t(K.PHASE_CHECK_FILES_FROM_PLAN, lang).format(plan_path=f"`{plan_path}`", ext=ext)
        return t(K.PHASE_CHECK_FILES_FROM_PLAN_MIN, lang).format(min_files=min_files, plan_path=f"`{plan_path}`", ext=ext)
    if check_type == "all_of":
        sub_specs = spec.get("checks", []) or []
        sub_descs = [
            _format_phase_check_description(f"{phase_name}.{i}", sub, lang)
            for i, sub in enumerate(sub_specs)
        ]
        if sub_descs:
            joined = " \u2022 ".join(sub_descs)
            return t(K.PHASE_CHECK_ALL_OF, lang).format(checks=joined)
        return t(K.PHASE_CHECK_ALL_OF, lang).format(checks=check_type)
    if check_type == "symbols_covered":
        source = spec.get("source_file", "kildefilen")
        plan = spec.get("plan_path", "refactor_plan.md")
        return t(K.PHASE_CHECK_SYMBOLS_COVERED, lang).format(source=f"`{source}`", plan=f"`{plan}`")
    if check_type == "min_text_length":
        return t(K.PHASE_CHECK_MIN_TEXT_LENGTH, lang).format(min_chars=spec.get("min_chars", 100))
    if check_type == "tool_called":
        tools = spec.get("tools", [])
        listed = ", ".join(f"`{t}`" for t in tools)
        if spec.get("require_all"):
            return t(K.PHASE_CHECK_TOOL_CALLED_ALL, lang).format(tools=listed)
        return t(K.PHASE_CHECK_TOOL_CALLED, lang).format(tools=listed)
    if check_type == "code_contains":
        path = spec.get("path", "")
        patterns = spec.get("patterns", [])
        n = len(patterns)
        if spec.get("require_all"):
            if n == 1:
                return t(K.PHASE_CHECK_CODE_CONTAINS, lang).format(path=f"`{path}`")
            return t(K.PHASE_CHECK_CODE_CONTAINS_ALL, lang).format(n=n, path=f"`{path}`")
        if n == 1:
            return t(K.PHASE_CHECK_CODE_CONTAINS, lang).format(path=f"`{path}`")
        return t(K.PHASE_CHECK_CODE_CONTAINS_MIN, lang).format(min_matches=spec.get("min_matches", 1), n=n, path=f"`{path}`")
    if check_type == "tests_pass":
        return t(K.PHASE_CHECK_TESTS_PASS, lang)
    return t(K.PHASE_CHECK_ALL_OF, lang).format(checks=check_type)



@app.route("/api/phase-checks", methods=["GET"])
def phase_checks() -> Any:
    """Return the deterministic phase success checks for a template.

    Query: ``?template=<template_name>&lang=<lang>``. If ``lang`` omitted, defaults to ``"da"``.
    Used by the frontend to display "✓ auto-completes when..." under each phase.
    """
    from agent_phase_checks import PHASE_ALIASES

    lang = request.args.get("lang", "da").strip() or "da"

    def _build_phase_entry(phase_name: str, spec: dict[str, Any]) -> dict[str, Any]:
        entry: dict[str, Any] = {
            "spec": spec,
            "description": _format_phase_check_description(phase_name, spec, lang),
        }
        lower_name = phase_name.lower()
        aliases = PHASE_ALIASES.get(lower_name, [])
        if aliases:
            entry["aliases"] = aliases
        return entry

    def _expand_with_aliases(phases: dict[str, dict[str, Any]]) -> dict[str, Any]:
        """Return a dict with canonical keys + alias entries pointing to the same data."""
        result: dict[str, Any] = {}
        for phase_name, spec in phases.items():
            entry = _build_phase_entry(phase_name, spec)
            result[phase_name] = entry
            lower_name = phase_name.lower()
            for alias in PHASE_ALIASES.get(lower_name, []):
                result[alias] = entry
        return result

    template = request.args.get("template", "").strip()
    if template:
        phases = TEMPLATE_PHASE_CHECKS.get(template, {})
        out = {template: _expand_with_aliases(phases)}
        return jsonify({"success": True, "template": template, "phases": out})
    out: dict[str, Any] = {}
    for tmpl, phases in TEMPLATE_PHASE_CHECKS.items():
        out[tmpl] = _expand_with_aliases(phases)
    return jsonify({"success": True, "templates": out})



@app.route("/api/update-task-status", methods=["POST"])
def update_task_status() -> Any:
    """Mark a task node as done/skipped so it won't be re-executed.

    POST JSON::
        {"session_id": "...", "task_path": "0.1.2", "status": "done"}

    ``task_path`` is dot-notation into ``root.children[]`` (e.g. "0" = first task,
    "0.1" = second child of first task).  Returns the updated node name + status.
    """
    data = request.json
    if not data:
        return jsonify({"success": False, "error": "Ingen JSON-body"}), 400

    session_id = data.get("session_id") or current_session_id
    task_path = data.get("task_path", "")
    new_status = data.get("status", "done")

    if not session_id:
        return jsonify({"success": False, "error": "Ingen session"}), 400
    if new_status not in ("done", "skipped", "pending"):
        return jsonify({"success": False, "error": "Status skal v\u00e6re 'done', 'skipped' eller 'pending'"}), 400

    # Resolve agent -- prefer stream agent if active
    stream_agent = agent
    if session_id:
        with active_streams_lock:
            if session_id in active_streams:
                stream_agent = active_streams[session_id]

    # Load tree from session if needed
    if stream_agent is agent or not stream_agent.task_tree:
        session_data = session_manager.load_session(session_id)
        if session_data and session_data.get("tree"):
            stream_agent.task_tree_from_dict(session_data["tree"])
        else:
            return jsonify({"success": False, "error": "Intet tr\u00e6 i session"}), 400

    if not stream_agent.task_tree:
        return jsonify({"success": False, "error": "Intet tr\u00e6"}), 400

    # Navigate to node via dot-path
    node = stream_agent.task_tree.root
    if task_path:
        for p in task_path.split("."):
            idx = int(p)
            if node.children and idx < len(node.children):
                node = node.children[idx]
            else:
                return jsonify({"success": False, "error": f"Ugyldig sti: {task_path}"}), 400

    node.status = new_status
    if not node.result:
        node.result = f"Markeret som {new_status} (manuelt)"

    # Persist session
    if session_id:
        try:
            session_manager.save_session(session_id, {
                "id": session_id,
                "tree": stream_agent.task_tree_to_dict(),
                "file_chunks": getattr(stream_agent, "file_chunks", {}),
                "images": getattr(stream_agent, "images", []),
                "template": getattr(stream_agent, "active_template", ""),
                "lang": getattr(stream_agent, "lang", "da"),
                "ui_lang": getattr(stream_agent, "lang", "da"),
                "original_prompt": getattr(stream_agent, "original_prompt", ""),
                "full_prompt_with_context": getattr(stream_agent, "full_prompt_with_context", ""),
                "show_thinking": getattr(stream_agent, "show_thinking", True),
            })
        except Exception:
            pass

    return jsonify({"success": True, "task": node.name, "status": node.status, "path": task_path})


@app.route("/api/test", methods=["GET"])
def test() -> Any:
    """test."""
    return jsonify({"success": True, "status": "ok", "message": t(K.UI_API_RUNNING, agent.lang), "static_folder": STATIC_DIR, "has_agent": agent is not None})
