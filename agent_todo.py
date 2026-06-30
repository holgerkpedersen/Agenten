import os
from lang import t
from typing import Any, Generator
from agent_config import log, EXECUTION_TIMEOUT, _WRITE_TOOLS, PHASE_ALIASES, REQUIRED_ACTION_TOOLS, CLOSE_PHASE_ALIASES, ISSUE_ID_PATTERN, AUTO_RESOLVE_PATTERNS, FRAMEWORK_PY, _TODO_TOOL_MAP
from agent_refactor_helpers import _resolve_refactor_plan_path, _check_import_placement, _refactor_actually_moved_code, _build_refactor_phase_context, _save_full_context_for_refactor, _count_symbols_in_file, _get_symbol_names_in_file, _validate_ekstraher_symbols, _build_module_progress_msg, _detect_module_deps, _resolve_source_file, _check_refactor_progress, _all_planned_modules_exist
import agent_files
from agent_utils import _normalize_phase

def _match_tool_to_todos(tool_name: str, args_val: dict, agent: Any, todo_list: list[dict] | None) -> list[str]:
    """Match a tool call against todo text and return matching todo IDs."""
    # Reset cross-call flag — only set True if THIS call has a deviation
    agent._batch_had_deviation = False
    if not todo_list:
        return []
    ids = []

    # Static mapping: simple tool->todo matches
    for tname, arg_check, todo_id in _TODO_TOOL_MAP:
        if tool_name == tname and todo_id:
            if any(t.get("id") == todo_id for t in todo_list):
                if arg_check is None or arg_check(args_val):
                    ids.append(todo_id)

    # Dynamic mapping: match tool calls against todo text patterns
    for todo in todo_list:
        tid = todo.get("id", "")
        ttext = todo.get("text", "")

        if tool_name == "remove_symbol":
            sym = args_val.get("symbol_name", "")
            if sym and sym in ttext and "Fjern" in ttext:
                ids.append(tid)

        if tool_name == "add_import":
            mod = args_val.get("module", "")
            if mod and mod in ttext and "import" in ttext:
                ids.append(tid)

        if tool_name == "write_file":
            path = args_val.get("path", "")
            fname = os.path.basename(path) if path else ""
            if fname and fname in ttext and "Opret" in ttext:
                ids.append(tid)

        if tool_name in ("extract_symbol", "batch_extract_symbols"):
            target = args_val.get("target", "")
            if target and (target in ttext or os.path.basename(target) in ttext):
                # Check planned symbols vs called symbols
                _planned = getattr(agent, '_planned_symbols_per_target', {})
                if _planned and target in _planned and tool_name == "batch_extract_symbols":
                    _planned_syms = set(_planned[target])
                    _called = args_val.get("symbols", "")
                    if isinstance(_called, str):
                        _called_syms = set(s.strip() for s in _called.replace("'", "").replace('"', '').split(',') if s.strip())
                    elif isinstance(_called, (list, tuple)):
                        _called_syms = set(str(s).strip() for s in _called if str(s).strip())
                    else:
                        _called_syms = set()
                    _in_file = _get_symbol_names_in_file(target) if target else set()
                    _known = _called_syms | _in_file
                    _missing = _planned_syms - _known
                    if _missing:
                        # Don't mark the todo as done — LLM hasn't covered all planned symbols
                        agent._log("ADVARSEL",
                            f"Planafvigelse for {target}: mangler {len(_missing)} plannede symboler",
                            f"Kaldte: {sorted(_called_syms)}\nMangler: {sorted(_missing)}")
                        # Store warning for handle_tool_call to inject
                        if not hasattr(agent, '_plan_warnings'):
                            agent._plan_warnings = []
                        agent._plan_warnings.append({
                            "target": target,
                            "missing": sorted(_missing),
                            "called": sorted(_called_syms),
                            "planned": sorted(_planned_syms),
                            "deviation": True,
                        })
                        # Also set persistent flag so batch_extract_symbols section can detect it
                        agent._batch_had_deviation = True
                    else:
                        ids.append(tid)
                else:
                    ids.append(tid)

        # list_symbols -> matches "List alle symboler" or "list_symbols"
        if tool_name == "list_symbols" and ("list_symbols" in ttext.lower() or "list alle symboler" in ttext.lower()):
            ids.append(tid)

        # read_location -> matches "læs" or "read_location"
        if tool_name == "read_location" and ("read_location" in ttext.lower() or "læs" in ttext.lower() or "metoder" in ttext.lower()):
            ids.append(tid)

        # run_tests -> matches "kør test" or "run_tests"
        if tool_name == "run_tests" and ("run_tests" in ttext.lower() or "test" in ttext.lower()):
            ids.append(tid)

        # analyze_dependencies -> matches "analyser afhængigheder"
        if tool_name == "analyze_dependencies" and ("afhængighed" in ttext.lower() or "dependencies" in ttext.lower()):
            ids.append(tid)

    return ids



def _auto_todo_update(tool_name: str, args_val: dict, agent: Any) -> list[str]:
    """Check if a tool call matches any active todo and returns todo_ids to mark done."""
    if not hasattr(agent, '_phase_todos'):
        return []
    ids = []
    ids = _match_tool_to_todos(tool_name, args_val, agent, getattr(agent, '_phase_todos', None))

    # Sequential order validation: only allow checkmarking the FIRST
    # unfinished todo that has a tool mapping. Soft todos (analysis/
    # planning tasks without tool matches) don't block — they're
    # implicitly completed when the next hard todo is triggered.
    # Non-blocking tools (verify_refactor, add_import) are always
    # allowed — they're incremental/verification tools where
    # out-of-order calling is harmless.
    NON_BLOCKING_TOOLS = {"verify_refactor", "add_import", "run_tests", "read_issue", "analyze_dependencies", "extract_symbol", "batch_extract_symbols", "list_symbols", "edit_file"}
    if ids:
        valid_ids = []
        for checked_id in ids:
            checked_idx = -1
            first_hard_unfinished = -1
            for i, t in enumerate(agent._phase_todos):
                if t.get("id") == checked_id:
                    checked_idx = i
                if not t.get("done") and first_hard_unfinished == -1:
                    # Check if this todo has any tool mapping
                    tid = t.get("id", "")
                    has_mapping = any(m[2] == tid for m in _TODO_TOOL_MAP)
                    # Also check dynamic patterns
                    ttext = t.get("text", "")
                    has_dynamic = any(kw in ttext for kw in ["Opret", "Fjern", "import", "extract"])
                    if has_mapping or has_dynamic:
                        first_hard_unfinished = i
            if (checked_idx == first_hard_unfinished or checked_idx <= first_hard_unfinished or
                tool_name in NON_BLOCKING_TOOLS):
                valid_ids.append(checked_id)
            else:
                checked_text = next((t.get("text","") for t in agent._phase_todos if t.get("id") == checked_id), checked_id)
                first_text = next((t.get("text","") for t in agent._phase_todos if t.get("id") == agent._phase_todos[first_hard_unfinished].get("id")), "")
                agent._log("TODO", f"Rækkefølge: '{checked_text[:60]}...' venter på '{first_text[:60]}...'", "")
        ids = valid_ids

    return ids



def _reconcile_llm_todos(agent: Any) -> list[str]:
    """Check actual disk state against LLM todos and return newly satisfiable llm todo IDs.

    For refactor Ekstraher: checks if target module files exist with content.
    """
    import re as _re
    import os as _os
    llm_todos = getattr(agent, '_llm_todos', None)
    if not llm_todos:
        return []
    ids = []
    for todo in llm_todos:
        if todo.get("done"):
            continue
        text = todo.get("text", "")
        tid = todo.get("id", "")
        if not text or not tid:
            continue

        # lt_total: mark done when all other module todos are done
        if "lt_total" in tid:
            _other_undone = [t for t in llm_todos if t.get("id") != tid and not t.get("done")]
            if not _other_undone:
                ids.append(tid)
            continue

        # For batch_extract_symbols calls, extract the TARGET module file
        # (e.g. task_config.py from target='task_config.py') rather than
        # the source file (agent_tasks.py from source='agent_tasks.py').
        # The first .py match in the raw text is always the source file,
        # which would cause ALL todos to point at the source file.
        if "batch_extract_symbols" in text:
            tm = _re.search(r"target=['\"]([^'\"]+\.py)['\"]", text)
            fpath = tm.group(1) if tm else None
        else:
            fpath = None
        if not fpath:
            m = _re.search(r'([a-zA-Z_][\w./-]+\.py)', text)
            if m:
                fpath = m.group(1)
        if fpath and _os.path.exists(fpath) and _os.path.getsize(fpath) > 0:
            _actual = _count_symbols_in_file(fpath)
            ids.append(tid)
            # Also update text with symbol count
            todo["text"] = f"Flyt symboler til {fpath} med batch_extract_symbols ({_actual} symbols)"
    return ids



def _reconcile_todos_with_disk(agent: Any) -> list[str]:
    """Check actual file state against todos and return newly satisfiable todo IDs.

    For each template, checks if work is already done on disk:
    - Opdatér: imports already exist in target file → checkmark import todos
    - kodeanalyse: docs/*.md already exist → checkmark write reports
    - programmering: docs/*.md already exist → checkmark write plans
    - bugfix: tests/temp/test_*.py already exists → checkmark test creation
    """
    import re as _re
    import os as _os
    todos = getattr(agent, '_phase_todos', None)
    if not todos:
        return []
    template = getattr(agent, 'active_template', '')
    phase = getattr(agent, 'current_phase', '')
    norm_phase = _normalize_phase(phase).lower()

    ids = []
    for todo in todos:
        if todo.get("done"):
            continue
        text = todo.get("text", "")
        tid = todo.get("id", "")
        if not text:
            continue

        # Check docs/*.md existence (kodeanalyse, programmering)
        if template in ("kodeanalyse", "programmering") and "skriv" in text.lower():
            m = _re.search(r'docs/[\w./-]+\.md', text)
            if m:
                doc_path = m.group(0)
                if _os.path.exists(doc_path):
                    ids.append(tid)

        # Check tests/temp/ files (bugfix, testgenerering)
        if template in ("bugfix", "testgenerering") and ("test" in text.lower() or "tests/temp" in text):
            m = _re.search(r'tests/temp/[\w./-]+\.py', text)
            if m:
                test_path = m.group(0)
                if _os.path.exists(test_path):
                    ids.append(tid)

        # Check import existence (refactor Opdatér)
        if template == "refactor" and norm_phase in ("opdater", "opdatering", "opdat\u00e9r"):
            m = _re.match(r'Tilf\u00f8j import fra ([\w./-]+\.py) i ([\w./-]+\.py)', text)
            if m:
                mod_file = m.group(1)
                target_file = m.group(2)
                target_path = target_file if _os.path.exists(target_file) else _os.path.join(agent_files._resolve_workdir(), target_file)
                if _os.path.exists(target_path):
                    try:
                        with open(target_path, 'r', encoding='utf-8', errors='replace') as f:
                            content = f.read()
                        mod_name = _os.path.splitext(mod_file)[0]
                        if f"from {mod_name}" in content or f"import {mod_name}" in content:
                            ids.append(tid)
                    except (OSError, UnicodeDecodeError):
                        pass

        # Check issue status (any template with update_issue_status todo)
        if "resolved" in text.lower() or "issue status" in text.lower():
            # Already checked by tool call — no disk check needed
            pass

        # Auto-check rf_t2 and verify_criteria when issue_resolved is set
        # by the auto-advance path (e.g. Test phase passes without explicit
        # update_issue_status call).
        if tid in ("rf_t2", "verify_criteria") and getattr(agent, 'issue_resolved', False):
            ids.append(tid)

    return ids



def _auto_populate_llm_todos(agent: Any, task_node: Any) -> list[dict]:
    """Auto-generate LLM-driven todos from phase context.

    For ALL templates and phases, creates a pre-populated checklist
    mirroring the Agent's auto-todos (``_phase_todos``) so the LLM
    has a ready-made plan.  The LLM can then call ``update_todo`` to
    mark progress and ``create_todo`` to add custom steps.

    For refactor-template phases that have a ``refactor_plan.md``,
    per-module tasks are created instead for finer granularity.

    Returns a list of event dicts (llm_todo_clear, llm_todo_add) that
    the caller can yield.
    """
    events: list[dict] = []
    import os as _os
    import re as _re

    template = getattr(agent, 'active_template', '') or ''
    phase = _normalize_phase(task_node.name).lower()

    # Bevar eksisterende LLM-todos hvis vi er i samme fase (retry).
    # Nulstil KUN ved fase-skift, så LLM's plan overlever iterationer.
    _prev_phase = getattr(agent, '_llm_todo_phase', '')
    if _prev_phase == phase and getattr(agent, '_llm_todos', None):
        if getattr(agent, '_llm_todos', None):
            # Retry — bevar planen, genudsender bare eksisterende todos
            agent._llm_has_planned = True
            import logging
            logging.getLogger(__name__).info("LLM-todos bevaret ved retry (%s, %d items)", _prev_phase, len(agent._llm_todos))
            for todo in agent._llm_todos:
                events.append({"type": "llm_todo_add", "id": todo.get("id",""), "text": todo.get("text",""), "parent_id": None})
            return events
        else:
            import logging
            logging.getLogger(__name__).info("LLM-todos TØMT ved retry (%s) — genopretter", _prev_phase)

    agent._llm_todos = []
    agent._llm_has_planned = False
    agent._llm_todo_phase = phase
    events.append({"type": "llm_todo_clear"})

    template = getattr(agent, 'active_template', '') or ''
    phase = _normalize_phase(task_node.name).lower()

    # ── Refactor-template: file-based todos from refactor_plan.md ──
    if template == 'refactor' and phase in ('plan', 'ekstraher', 'opdatering', 'opdatér', 'opdater'):
        plan_path = _resolve_refactor_plan_path(agent, 'refactor_plan.md')
        if not _os.path.isabs(plan_path):
            _wd = _os.environ.get('AGENT_WORKDIR', '')
            if _wd:
                plan_path = _os.path.normpath(_os.path.join(_wd, plan_path))
        try:
            from file_checks import _parse_refactor_plan_modules, _extract_modules_from_plan
            _all_mods = _parse_refactor_plan_modules(plan_path)
        except Exception:
            _all_mods = []
        if _all_mods:
            # Filter out the source file (the file being refactored)
            _src_match = _re.search(r"([a-zA-Z_][\w.]+\.py)", getattr(agent, 'original_prompt', '') or '')
            _src = _src_match.group(1) if _src_match else ''
            _tgt_mods = [m for m in _all_mods if _src not in m] if _src else _all_mods
            if not _tgt_mods:
                _tgt_mods = _all_mods
            # Hent symbol-mapping fra planen én gang per fase (ikke per modul)
            _ekstraher_sym_map = {}
            if phase == "ekstraher":
                try:
                    with open(plan_path, encoding="utf-8") as _pf:
                        import symbol_checks as _sc2
                        _ekstraher_sym_map = _sc2._parse_plan_symbol_mapping(_pf.read())
                except Exception:
                    pass
            for mod in _tgt_mods:
                _exists = _os.path.exists(mod)
                _done = _exists
                _todo_id = "lt_" + _re.sub(r'[^a-zA-Z0-9]', '', mod.replace('.py', ''))[:12]
                if phase == "ekstraher":
                    _actual = _count_symbols_in_file(mod) if _exists else 0
                    _gen_text = ""
                    _planned_syms = _ekstraher_sym_map.get(mod, [])
                    if _planned_syms:
                        _src_f = _src or "source_file"
                        # Chunk symbols into batches of max 15 symbols or ~500 chars.
                        # Each chunk contains COMPLETE symbols — never cut mid-symbol.
                        _chunks: list[list[str]] = []
                        _current: list[str] = []
                        _current_chars = 0
                        for _sym in _planned_syms:
                            _sym_len = len(_sym) + 2  # ", " separator
                            _would_exceed_count = len(_current) >= 15
                            _would_exceed_chars = _current and _current_chars + _sym_len > 500
                            if _would_exceed_count or _would_exceed_chars:
                                _chunks.append(_current)
                                _current = []
                                _current_chars = 0
                            _current.append(_sym)
                            _current_chars += _sym_len
                        if _current:
                            _chunks.append(_current)
                        _batch_parts = []
                        for _chunk in _chunks:
                            _sym_str = ", ".join(_chunk)
                            _batch_parts.append(f"batch_extract_symbols(source='{_src_f}', symbols='{_sym_str}', target='{mod}')")
                        _gen_text = " -> ".join(_batch_parts)
                        # Tjek om ALLE planlagte symboler findes i modulet
                        # (ikke bare om filen eksisterer — kan være stale fra tidl. session)
                        if _exists:
                            _actual_names = _get_symbol_names_in_file(mod)
                            _done = all(s in _actual_names for s in _planned_syms)
                    _text = f"[{_tgt_mods.index(mod)+1}/{len(_tgt_mods)}] {_gen_text or 'Flyt symboler til ' + mod + ' med batch_extract_symbols'}"
                    if _exists and _actual > 0:
                        _text += f" ({_actual} symbols)"
                elif phase == "plan":
                    _text = f"Planlæg indhold af {mod}"
                else:
                    _text = f"Opdatér referencer i {mod}"
                agent._llm_todos.append({"id": _todo_id, "text": _text, "done": _done, "parent_id": None, "phase": phase})
                events.append({"type": "llm_todo_add", "id": _todo_id, "text": _text, "parent_id": None})
            _n = len(_tgt_mods)
            _total_text = f"Verificér at alle {_n} moduler er korrekt oprettet" if _n != 1 else "Verificér at modulet er korrekt oprettet"
            _total_done = False
            if phase == "ekstraher" and _ekstraher_sym_map:
                _total_done = all(
                    _os.path.exists(m) and all(s in _get_symbol_names_in_file(m) for s in _ekstraher_sym_map.get(m, []))
                    for m in _tgt_mods
                )
            else:
                _total_done = all(_os.path.exists(m) for m in _tgt_mods)
            agent._llm_todos.append({"id": "lt_total", "text": _total_text, "done": _total_done, "parent_id": None, "phase": phase})
            events.append({"type": "llm_todo_add", "id": "lt_total", "text": _total_text, "parent_id": None})
            # Ekstraher: auto-populated todos har konkrete batch_extract_symbols
            # kald fra planen — LLM behøver IKKE kalde plan_phase/create_todo.
            if phase == "ekstraher" and _ekstraher_sym_map:
                agent._llm_has_planned = True
                # Store plan mapping so deviation detection can compare
                # batch_extract_symbols calls against planned symbols per module
                agent._planned_symbols_per_target = dict(_ekstraher_sym_map)
            return events

    # ── For other phases: leave LLM's plan empty — LLM creates its own ──
    # _llm_has_planned is left False so the budget nudge tells the LLM
    # to call plan_phase/create_todo to build its own plan.
    agent._llm_todos = []
    agent._llm_has_planned = False
    return events
