from typing import Any, Generator
import os
from lang import t
import json
from i18n import K
import agent_files
import agent_phase_checks
from agent_refactor_helpers import _resolve_refactor_plan_path, _check_import_placement, _refactor_actually_moved_code, _build_refactor_phase_context, _save_full_context_for_refactor, _count_symbols_in_file, _get_symbol_names_in_file, _validate_ekstraher_symbols, _build_module_progress_msg, _detect_module_deps, _resolve_source_file, _check_refactor_progress, _all_planned_modules_exist
from agent_task_phase import _get_max_iterations, _get_max_tool_calls, _normalize_phase, _set_phase_model, set_task_tools
from agent_utils import _is_greenfield, _use_native_tools, _normalize_phase, _inject_todo_tools

def _get_phase_auto_complete_msg(task_node: Any, tool_name: str, tool_result: dict | Any, agent: Any, called_tools: dict | None = None, full_response: str = "") -> str | None:
    """Return auto-complete message if the phase goal was just met, else None.
    Checks phase-specific success conditions after each tool call.

    Two layers of auto-complete:
      1. Tool-result-based (run_tests passed, update_issue_status succeeded)
      2. Deterministic phase-check (file_exists, files_from_plan, etc.) —
         runs after any tool call when the template defines a check for
         this phase

    Args:
        called_tools: dict of ``"{tool_name}{args_repr}"`` → count. Forwarded
            to ``check_phase_done`` so ``tool_called`` and ``tests_pass``
            checks can see the full call history.
        full_response: accumulated LLM streaming text. Forwarded to
            ``check_phase_done`` for ``min_text_length``.
    """
    task_name = getattr(task_node, "name", "") or ""
    phase = _normalize_phase(task_name).lower()

    # Bloker auto-complete for Analyse og Plan hvis LLM'en ikke har
    # oprettet sin egen opgaveplan endnu (kræver plan_phase kaldt).
    if phase in ("analyse", "plan") and not getattr(agent, '_llm_has_planned', False):
        return None

    if tool_name == "run_tests" and not agent._tests_failed:
        if "test" in phase:
            # For refactor: tests passing IS proof of work — modules on disk + imports = done
            if getattr(agent, 'active_template', '') == 'refactor':
                agent.issue_resolved = True
                agent._needs_resolve_persist = True
                return t(K.LOG_RED_TEST_PASSED, agent.lang)
            if _refactor_actually_moved_code(agent):
                agent.issue_resolved = True
                agent._needs_resolve_persist = True
                return t(K.LOG_RED_TEST_PASSED, agent.lang)
            return (
                t(K.LOG_RED_TEST_PASSED, agent.lang)
                + "\n\n"
                + t(K.TEST_BUT_NO_REFACTOR, agent.lang)
            )
        if any(k in phase for k in ["implementering", "fix", "verifikation",
                                     "opdatering", "luk", "close", "green"]):
            return t(K.LOG_PHASE_COMPLETE, agent.lang)

    if tool_name == "update_issue_status":
        if isinstance(tool_result, dict) and tool_result.get("success"):
            if any(k in phase for k in ["opdatering", "update", "luk", "close"]):
                return t(K.LOG_PHASE_COMPLETE, agent.lang)
            # Bug already fixed — auto-complete implementering/fix phases
            if any(k in phase for k in ["implementering", "fix", "verifikation", "green"]):
                return t(K.LOG_PHASE_COMPLETE, agent.lang)
            # Analyse phase resolved the issue — auto-complete
            if any(k in phase for k in ["analyse", "analysis"]):
                return t(K.LOG_PHASE_COMPLETE, agent.lang)

    # Phase output verification — prevent auto-complete when no output was produced
    if tool_name in ("write_file",):
        if "plan" in phase:
            plan_path = getattr(agent, '_refactor_plan_path', '') or os.path.join(agent_files._resolve_workdir(), "refactor_plan.md")
            if not os.path.exists(plan_path) or os.path.getsize(plan_path) == 0:
                agent._log("DEBUG", "Plan output verification", f"{plan_path} mangler eller er tom — afslutter IKKE auto-complete")
                return None
        if "ekstraher" in phase:
            # Check if write_file was actually called with a module name (not refactor_plan.md)
            wrote_module = False
            for tool_key in (called_tools or {}):
                if tool_key.startswith("write_file"):
                    try:
                        args_str = tool_key[len("write_file"):]
                        args = json.loads(args_str) if args_str else {}
                        fname = args.get("filepath", "") or args.get("file_path", "") or ""
                        if fname and fname != "refactor_plan.md":
                            wrote_module = True
                            break
                    except (json.JSONDecodeError, ValueError):
                        wrote_module = True
                        break
            if not wrote_module:
                agent._log("DEBUG", "Ekstraher output verification", "write_file ikke kaldt med et modulnavn — afslutter IKKE auto-complete")
                return None

    # Deterministic phase check (template-defined file existence criteria).
    # Only run after a successful productive tool call — no point
    # auto-completing when the tool itself failed (e.g. update_issue_status
    # with a non-existent issue ID, or edit_file via edit_file2 where the
    # symbol wasn't found but still returned success=True).
    tool_failed = isinstance(tool_result, dict) and not tool_result.get("success")
    # edit_file via edit_file2 pipeline can return success=True even when
    # extraction failed (extract_error). Check for real changes.
    if not tool_failed and tool_name == "edit_file":
        has_changes = isinstance(tool_result, dict) and tool_result.get("lines_changed", 0) > 0
        if not has_changes:
            tool_failed = True
    if not tool_failed:
        PRODUCTIVE_TOOLS = {"write_file", "edit_file", "run_tests", "update_issue_status", "batch_extract_symbols", "extract_symbol", "verify_refactor"}
        if tool_name in PRODUCTIVE_TOOLS:
            try:
                passed, reason = agent_phase_checks.check_phase_done(
                    agent, task_node, called_tools=called_tools,
                    tool_name=tool_name, full_response=full_response,
                )
                if passed:
                    return t(K.PHASE_AUTO_ADVANCED, agent.lang).format(reason=reason)
            except Exception as _e:
                agent._log("DEBUG", f"phase check error: {_e}", "")

    return None



def _extract_last_assistant_text(messages: list[dict]) -> str:
    """Extract the last substantive assistant text from messages, skipping tool-only responses."""
    if not messages:
        return ""
    for m in reversed(messages):
        if m["role"] != "assistant":
            continue
        content = m.get("content")
        if not content:
            continue
        text = content if isinstance(content, str) else ""
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    text = part.get("text", "")
                    break
        text = text.strip()
        if len(text) > 50:
            return text
    return ""



def _generate_phase_todos(template: str, phase_name: str, prompt: str = "", agent: Any | None = None) -> list[dict]:
    """Generate a todo checklist for a phase based on template and phase name."""
    phase = _normalize_phase(phase_name).lower()
    todos = []

    if template == "bugfix":
        if phase == "analyse":
            todos.extend([
                {"id": "bf_a1", "text": "L\u00e6s issue med read_issue()", "done": False},
                {"id": "bf_a2", "text": "Find relevant kode med locate()", "done": False},
                {"id": "bf_a3", "text": "Sammenlign koden med buggens p\u00e5stand", "done": False},
                {"id": "bf_a4", "text": "Afg\u00f8r om fejlen findes eller er rettet", "done": False},
            ])
        elif phase == "test" or "test" in phase:
            todos.extend([
                {"id": "bf_t1", "text": "Opret testfil i tests/temp/ med write_file", "done": False},
                {"id": "bf_t2", "text": "K\u00f8r specifik test - den SKAL fejle (r\u00f8d fase)", "done": False},
            ])
        elif phase == "implementering":
            todos.extend([
                {"id": "bf_i1", "text": "Ret kildekoden med edit_file/add_method/add_function", "done": False},
                {"id": "bf_i2", "text": "Undg\u00e5 write_file - filen findes allerede", "done": False},
                {"id": "bf_i3", "text": "Brug add_method til nye metoder i klasse", "done": False},
            ])
        elif phase == "verifikation":
            todos.extend([
                {"id": "bf_v1", "text": "K\u00f8r specifik test - den SKAL best\u00e5 (gr\u00f8n fase)", "done": False},
                {"id": "bf_v2", "text": "K\u00f8r HELE testsuiten for at tjekke regression", "done": False},
            ])
        elif phase == "opdatering":
            todos.append({"id": "bf_o1", "text": "Opdater issue status til 'resolved'", "done": False})

    elif template == "refactor":
        import os as _os
        import re as _re
        # Use session-scoped plan path when available
        if agent and getattr(agent, '_refactor_plan_path', ''):
            _plan_path = agent._refactor_plan_path
        else:
            _plan_path = 'refactor_plan.md'
        plan_content = ''
        plan_fresh = True
        if _os.path.exists(_plan_path):
            try:
                with open(_plan_path, 'r', encoding='utf-8') as _f:
                    plan_content = _f.read()
                if prompt:
                    _prompt_target = _re.search(r'(?:REFAC|ARC|BUG)[-\s]*\d+.*?([a-zA-Z_][\w.]+\.py)', prompt)
                    _plan_target = _re.search(r'([a-zA-Z_][\w.]+\.py)', plan_content[:300])
                    if _prompt_target and _plan_target and _prompt_target.group(1) != _plan_target.group(1):
                        plan_fresh = False
            except (OSError, UnicodeDecodeError):
                pass

        # Extract module names from plan (only if plan is fresh)
        # Matcher både backtick-format (`config.py`) og heading-format (## Modul: config.py)
        _mods = set(_re.findall(r'`([a-zA-Z_][\w.]+\.py)`', plan_content))
        _mods |= set(_re.findall(r'(?:^|\n)#{1,6}\s+(?:Modul:\s*)?([a-zA-Z_][\w]*\.py)', plan_content, _re.MULTILINE | _re.IGNORECASE))
        plan_modules = sorted(_mods) if (plan_content and plan_fresh) else []
        existing_modules = [m for m in plan_modules if _os.path.exists(m)]

        if phase == "analyse":
            todos.extend([
                {"id": "rf_a1", "text": "List alle symboler med list_symbols()", "done": False},
                {"id": "rf_a2", "text": "Læs de vigtigste metoder med read_location()", "done": False},
                {"id": "rf_a3", "text": "Analyser afhængigheder med analyze_dependencies()", "done": False},
                {"id": "rf_a4", "text": "Identificer SOLID-overtrædelser", "done": False},
                {"id": "rf_a5", "text": "Kortlæg ansvarsområder for modulopdeling", "done": False},
                {"id": "rf_a6", "text": "Gem analyse i refactor_analyse.md med write_file()", "done": False},
            ])
            if plan_modules:
                todos.append({
                    "id": "rf_a_modules",
                    "text": "Planen nævner {} moduler: {}".format(len(plan_modules), ', '.join(plan_modules)),
                    "done": False
                })

        elif phase == "plan":
            todos.extend([
                {"id": "rf_p0", "text": "Læs refactor_analyse.md for at få analyse-resultater", "done": False},
                {"id": "rf_p1", "text": "Beslut modulopdeling", "done": False},
                {"id": "rf_p2", "text": f"Skriv {_plan_path} med write_file()", "done": False},
                {"id": "rf_p3", "text": "Inkluder alle moduler og symboler i planen", "done": False},
            ])
            if existing_modules:
                todos.append({
                    "id": "rf_p_existing",
                    "text": "Findes allerede: {}".format(', '.join(existing_modules)),
                    "done": True
                })

        elif phase == "ekstraher":
            # Parse module→symbols mapping from plan
            _mod_symbols: dict[str, list[str]] = {}
            if plan_content:
                # Pattern 1: "### modul.py\nsymbol1, symbol2, ..." or "## Modul: config.py\n- symbol1\n- symbol2"
                for _m in _re.finditer(
                    r'#{1,4}\s+(?:Modul(?:e)?:\s*)?`?([a-zA-Z_][\w./-]+\.py)`?\s*\n(.*?)(?=\n#{1,4}\s+|$)',
                    plan_content, _re.MULTILINE | _re.DOTALL | _re.IGNORECASE
                ):
                    _mod = _m.group(1).strip('`')
                    _body = _m.group(2).strip()
                    _syms = _re.findall(r'`?([A-Za-z_]\w*)`?', _body)
                    _mod_symbols[_mod] = [s for s in _syms if s not in ('py', 'txt', 'md') and not s.startswith(('.', '/'))]
                # Pattern 2: inline "modul.py → sym1, sym2"
                if not _mod_symbols:
                    for _m in _re.finditer(
                        r'`([a-zA-Z_][\w./-]+\.py)`[^`]*?([A-Z][a-zA-Z_][\w,\s]*)',
                        plan_content
                    ):
                        _mod = _m.group(1)
                        _syms = [s.strip() for s in _m.group(2).split(',') if s.strip()]
                        _mod_symbols[_mod] = _syms

            # Bestem kildefil fra prompt eller plan
            _src_match = _re.search(r"([a-zA-Z_][\w.]+\.py)", prompt or "")
            _ekstraher_src = _src_match.group(1) if _src_match else "api_server.py"

            todos.extend([
                {"id": "rf_e1", "text": "Følg refactor_plan.md nøjagtigt — opfyld ALLE moduler deri", "done": False},
                {"id": "rf_e2", "text": "Brug batch_extract_symbols() til at flytte symboler fra {} til hvert modul".format(_ekstraher_src), "done": False},
                {"id": "rf_e3", "text": "Rækkefølge: batch_extract_symbols → verify_refactor → næste modul", "done": False},
                {"id": "rf_e4", "text": "Verificer syntaks med verify_refactor() efter hver batch", "done": False},
            ])
            # Brug plan_modules eller _mod_symbols keys (fra sektions-headers) som modul-liste
            _all_plan_mods = plan_modules or sorted(_mod_symbols.keys())
            _existing_mods = [m for m in _all_plan_mods if _os.path.exists(m)]
            if _all_plan_mods:
                total = len(_all_plan_mods)
                done_count = len(_existing_mods)
                todos.append({
                    "id": "rf_e_progress",
                    "text": "Fremskridt: {}/{} moduler oprettet ({})".format(done_count, total, ', '.join(_all_plan_mods)),
                    "done": False,
                })
            # Ta l faktiske symboler i eksisterende moduler for status
            if _existing_mods:
                for _mod in _existing_mods:
                    _planned = _mod_symbols.get(_mod, [])
                    _actual = _count_symbols_in_file(_mod)
                    if _planned:
                        _done_count = min(_actual, len(_planned))
                        _status = "{}/{} symbols i filen".format(_done_count, len(_planned))
                    elif _actual > 0:
                        _status = "{} symbols i filen".format(_actual)
                    else:
                        _status = "f\u00e6rdig"
                    todos.append({
                        "id": "rf_e_done_" + _mod.replace('.py', '').replace('.', '_'),
                        "text": "\u2705 {} f\u00e6rdig \u2014 {}".format(_mod, _status),
                        "done": True,
                    })
            to_create = [m for m in _all_plan_mods if m not in _existing_mods]
            for idx, _mod in enumerate(to_create, 1):
                _syms = _mod_symbols.get(_mod, [])
                _count_info = " ({} symbols)".format(len(_syms)) if _syms else ""
                # Afha ngighedsdetektion
                _deps = _detect_module_deps(_mod, _all_plan_mods)
                _dep_info = " \u2014 afh\u00e6nger af: " + ", ".join(_deps) if _deps else ""
                todos.append({
                    "id": "rf_e_create_" + _mod.replace('.py', '').replace('.', '_'),
                    "text": "[{}] Flyt til {}{}{}".format(idx, _mod, _count_info, _dep_info),
                    "done": False,
                })

        elif phase in ("opdater", "opdatering", "opdat\u00e9r"):
            # Determine target file from prompt + plan — fallback to agent_core.py
            _target_file = 'agent_core.py'
            _file_match = _re.search(r"([a-zA-Z_][\w.]+\.py)", prompt)
            if _file_match:
                _target_file = _file_match.group(1)
            if plan_content:
                _plan_match = _re.search(r'([a-zA-Z_][\w.]+\.py)', plan_content[:300])
                if _plan_match and _plan_match.group(1) != _target_file:
                    _target_file = _plan_match.group(1)

            core_path = _os.path.join(_os.environ.get('AGENT_WORKDIR', ''), _target_file) if _os.environ.get('AGENT_WORKDIR') else _target_file
            core_symbols = []
            if _os.path.exists(core_path):
                try:
                    with open(core_path, 'r', encoding='utf-8') as _f:
                        core_content = _f.read()
                    core_nodes = _re.findall(r'^def (\w+)|^class (\w+)', core_content, _re.MULTILINE)
                    core_symbols = sorted(set(n[0] or n[1] for n in core_nodes))
                except (OSError, UnicodeDecodeError):
                    pass

            todos.append({"id": "rf_u1", "text": "List symboler i {} med list_symbols()".format(_target_file), "done": False})

            # Find symbols mentioned in plan that are still in the target file
            # Format: "- `symbol_name` (linje N) -> `target_module.py`"
            if plan_content and core_symbols:
                symbol_map = {}  # symbol -> target_module
                for m in _re.finditer(r'- `(\w+)`[^`]+`([\w.]+\.py)`', plan_content):
                    sym = m.group(1)
                    target = m.group(2)
                    symbol_map[sym] = target
                # Also match explicit symbol_name references
                for m in _re.finditer(r"symbol_name='(\w+)'", plan_content):
                    sym = m.group(1)
                    if sym not in symbol_map:
                        symbol_map[sym] = '?'
                still_in_core = [s for s in symbol_map if s in core_symbols]
                for sym in still_in_core:
                    target_mod = symbol_map.get(sym, '?')
                    todos.append({
                        "id": "rf_u_remove_" + sym,
                        "text": "Fjern `{}` fra {} (i {})".format(sym, _target_file, target_mod),
                        "done": False
                    })

            if existing_modules:
                _target_base = _os.path.splitext(_target_file)[0]
                for mod in existing_modules:
                    mod_name = _os.path.splitext(mod)[0]
                    if mod_name != _target_base:
                        todos.append({
                            "id": "rf_u_import_" + mod_name,
                            "text": "Tilf\u00f8j import fra {} i {}".format(mod, _target_file),
                            "done": False
                        })

            todos.append({"id": "rf_u_verify", "text": "Verificer syntaks med verify_refactor()", "done": False})
            todos.append({"id": "rf_u_tests", "text": "K\u00f8r tests for at bekr\u00e6fte ingen regression", "done": False})
            todos.append({"id": "rf_u_status", "text": "Mark\u00e9r REFAC som resolved med update_issue_status()", "done": False})

        elif phase == "test":
            todos.extend([
                {"id": "rf_t1", "text": "K\u00f8r alle tests for at bekr\u00e6fte ingen regression", "done": False},
                {"id": "rf_t2", "text": "Opdater issue status til 'resolved'", "done": False},
            ])

    elif template == "kodeanalyse":
        if phase == "analyse" or "form\u00e5l" in phase:
            todos.extend([
                {"id": "ka_a1", "text": "List symboler med list_symbols()", "done": False},
                {"id": "ka_a2", "text": "L\u00e6s vigtige funktioner med read_location()", "done": False},
                {"id": "ka_a3", "text": "Analyser afh\u00e6ngigheder", "done": False},
                {"id": "ka_a4", "text": "Skriv analyserapport med write_file()", "done": False},
            ])
        elif "import" in phase:
            todos.extend([
                {"id": "ka_i1", "text": "Gennemg\u00e5 imports med read_location()", "done": False},
                {"id": "ka_i2", "text": "Not\u00e9r ubrugte og cirkul\u00e6re imports", "done": False},
                {"id": "ka_i3", "text": "Skriv import-rapport med write_file()", "done": False},
            ])
        elif "arkitektur" in phase:
            todos.extend([
                {"id": "ka_k1", "text": "Analys\u00e9r klasse- og funktionsstruktur", "done": False},
                {"id": "ka_k2", "text": "Vurd\u00e9r design patterns og SOLID", "done": False},
                {"id": "ka_k3", "text": "Skriv arkitektur-rapport med write_file()", "done": False},
            ])
        elif "kvalitet" in phase or "kodekvalitet" in phase:
            todos.extend([
                {"id": "ka_q1", "text": "Vurd\u00e9r l\u00e6sbarhed, navngivning, type hints", "done": False},
                {"id": "ka_q2", "text": "Tjek test coverage og kompleksitet", "done": False},
                {"id": "ka_q3", "text": "Skriv kodekvalitets-rapport med write_file()", "done": False},
            ])
        elif "sikkerhed" in phase:
            todos.extend([
                {"id": "ka_s1", "text": "Analys\u00e9r inputvalidering og autentifikation", "done": False},
                {"id": "ka_s2", "text": "Tjek for OWASP-top-10 s\u00e5rbarheder", "done": False},
                {"id": "ka_s3", "text": "Skriv sikkerheds-rapport med write_file()", "done": False},
            ])

    elif template == "programmering":
        if phase == "analyse" or "krav" in phase:
            todos.extend([
                {"id": "pr_a1", "text": "Analys\u00e9r krav og behov", "done": False},
                {"id": "pr_a2", "text": "Skriv kravanalyse i docs/kravanalyse.md", "done": False},
            ])
        elif "arkitektur" in phase:
            todos.extend([
                {"id": "pr_d1", "text": "Design arkitektur med komponenter og gr\u00e6nseflader", "done": False},
                {"id": "pr_d2", "text": "Skriv arkitektur i docs/arkitektur.md", "done": False},
            ])
        elif "implementeringsplan" in phase:
            todos.extend([
                {"id": "pr_p1", "text": "Lav implementeringsplan med moduler og r\u00e6kkef\u00f8lge", "done": False},
                {"id": "pr_p2", "text": "Skriv plan i docs/implementeringsplan.md", "done": False},
            ])
        elif "sikkerhed" in phase:
            todos.extend([
                {"id": "pr_s1", "text": "Analys\u00e9r sikkerhedsaspekter", "done": False},
                {"id": "pr_s2", "text": "Skriv sikkerhedsanalyse i docs/sikkerhedsanalyse.md", "done": False},
            ])
        elif "refinement" in phase or "uddyb" in phase:
            todos.extend([
                {"id": "pr_r1", "text": "Udfyld detaljer og pr\u00e6cis\u00e9r specifikationer", "done": False},
            ])
        elif "kodeimplementering" in phase or "implementer" in phase:
            todos.extend([
                {"id": "pr_i1", "text": "Implement\u00e9r koden med write_file()", "done": False},
                {"id": "pr_i2", "text": "K\u00f8r tests med run_tests()", "done": False},
            ])

    elif template == "issue_handler":
        if phase in ("l\u00e6s", "read", "analyse"):
            todos.extend([
                {"id": "ih_a1", "text": "L\u00e6s issue med read_issue()", "done": False},
                {"id": "ih_a2", "text": "Find relevant kode med locate()", "done": False},
            ])
        elif phase in ("afklar", "clarify"):
            todos.extend([
                {"id": "ih_c1", "text": "Forst\u00e5 problemet og afg\u00f8r l\u00f8sning", "done": False},
            ])
        elif phase in ("fix", "implementering"):
            todos.extend([
                {"id": "ih_f1", "text": "Ret koden med edit_file()", "done": False},
                {"id": "ih_f2", "text": "K\u00f8r tests med run_tests()", "done": False},
            ])
        elif phase in ("luk", "close"):
            todos.extend([
                {"id": "ih_l1", "text": "Opdater issue status til 'resolved'", "done": False},
            ])

    elif template == "selvforbedring":
        if phase == "analyse" or "analyser" in phase:
            todos.extend([
                {"id": "sf_a1", "text": "L\u00e6s CORE-issue med read_issue()", "done": False},
                {"id": "sf_a2", "text": "Find relevant kode med locate()", "done": False},
            ])
        elif "diagnostic" in phase:
            todos.extend([
                {"id": "sf_d1", "text": "K\u00f8r tests med run_tests()", "done": False},
                {"id": "sf_d2", "text": "Identific\u00e9r rod\u00e5rsag", "done": False},
            ])
        elif phase in ("ret", "fix"):
            todos.extend([
                {"id": "sf_r1", "text": "Ret koden med edit_file()", "done": False},
                {"id": "sf_r2", "text": "K\u00f8r tests for at bekr\u00e6fte fix", "done": False},
            ])
        elif "verific" in phase or "test" in phase:
            todos.extend([
                {"id": "sf_v1", "text": "K\u00f8r HELE testsuiten", "done": False},
                {"id": "sf_v2", "text": "Opdater CORE-issue status til 'resolved'", "done": False},
            ])
        elif "commit" in phase:
            todos.extend([
                {"id": "sf_c1", "text": "Commit \u00e6ndringer med git_commit()", "done": False},
            ])

    elif template == "testgenerering":
        if phase == "analyse":
            todos.extend([
                {"id": "tg_a1", "text": "Analys\u00e9r koden og find testbare enheder", "done": False},
            ])
        elif "test" in phase:
            todos.extend([
                {"id": "tg_t1", "text": "Opret testfil i tests/temp/ med write_file()", "done": False},
                {"id": "tg_t2", "text": "K\u00f8r test - den SKAL fejle f\u00f8rst (r\u00f8d)", "done": False},
            ])
        elif "implementer" in phase:
            todos.extend([
                {"id": "tg_i1", "text": "Implement\u00e9r koden der g\u00f8r testen gr\u00f8n", "done": False},
                {"id": "tg_i2", "text": "K\u00f8r specifik test - skal best\u00e5", "done": False},
            ])
        elif "verifikation" in phase or "green" in phase:
            todos.extend([
                {"id": "tg_v1", "text": "K\u00f8r HELE testsuiten for regression", "done": False},
            ])

    elif template == "agenten":
        if "branch" in phase:
            todos.extend([
                {"id": "ag_b1", "text": "Opret og skift til ny branch", "done": False},
            ])
        elif "commit" in phase:
            todos.extend([
                {"id": "ag_c1", "text": "Commit \u00e6ndringer", "done": False},
            ])
        elif "push" in phase:
            todos.extend([
                {"id": "ag_p1", "text": "Push til remote", "done": False},
            ])
        elif "pull" in phase or "pr" in phase or "request" in phase:
            todos.extend([
                {"id": "ag_pr1", "text": "Opret Pull Request", "done": False},
            ])

    if not todos:
        todos.append({"id": "todo_generic", "text": f"Gennemf\u00f8r fasen: {phase_name}", "done": False})

    # Add verification todo — auto-marked by reconcile when check_phase_done passes
    todos.append({
        "id": "verify_criteria",
        "text": "Verific\u00e9r at fasens succeskriterier er opfyldt",
        "done": False,
    })

    return todos
