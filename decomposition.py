import model_manager
import config
from config import app, get_logger, log
from flask import Flask, request, jsonify, send_from_directory, Response, stream_with_context
from session_manager import SessionManager, _guard_json_body, agent, session_manager, execution_status, execution_status_lock, export_folder_lock
import os
from typing import Any, Generator
from lang import t, get_ui_translations
from i18n import K

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
