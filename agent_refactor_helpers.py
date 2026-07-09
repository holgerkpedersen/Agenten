import os
import ast
import re
import agent_files
from typing import Any, Generator
import agent_phase_checks
import json
from i18n import K
from lang import t


def _classify_test_failure(test_output: str, source_file: str = "api_server.py") -> dict:
    """Parse pytest output and classify the failure into a known refactor error type.

    Returns a dict with:
      - ``category``: one of ``import_error``, ``module_not_found``,
        ``circular_import``, ``attribute_error``, ``name_error``,
        ``syntax_error``, ``test_logic``, ``unknown``
      - ``detail``: the actual error message (first relevant line)
      - ``suggestion``: what the LLM should do to fix it (Danish)
    """
    detail = ""
    for line in test_output.splitlines():
        line_s = line.strip()
        if not detail and ("Error:" in line_s or "E       " in line_s or "ERROR:" in line_s):
            detail = line_s.replace("E       ", "").strip()[:200]
        if "short test summary" in line_s.lower():
            break

    if not detail:
        return {"category": "unknown", "detail": "",
                "suggestion": "Læs test-output for at identificere fejlen."}

    dl = detail.lower()

    if "circular import" in dl or "circularimport" in dl:
        return {"category": "circular_import", "detail": detail,
                "suggestion": "To moduler importerer hinanden. Opret et tredje modul med delte symboler "
                              "(f.eks. shared_state.py eller types.py) og importer dérfra i stedet."}

    if "modulenotfound" in dl or "no module named" in dl:
        return {"category": "module_not_found", "detail": detail,
                "suggestion": "Modulfilen findes ikke på disk. Brug list_files() for at se hvilke "
                              "filer der findes, eller write_file() for at oprette det manglende modul."}

    if "cannot import name" in dl or "unable to import" in dl:
        return {"category": "import_error", "detail": detail,
                "suggestion": "Et symbol er ikke tilgængeligt i modulet. Brug list_symbols() for at "
                              "se hvad modulet indeholder, og batch_extract_symbols() for at flytte "
                              "det manglende symbol fra kildefilen til modulet."}

    if "attributeerror" in dl or "has no attribute" in dl:
        return {"category": "attribute_error", "detail": detail,
                "suggestion": "Et symbol findes ikke i det forventede modul. Brug list_symbols() på "
                              "modulfilen og locate() i kildefilen. Flyt det manglende symbol med "
                              "batch_extract_symbols() hvis det mangler i modulet."}

    if "nameerror" in dl or "is not defined" in dl:
        return {"category": "name_error", "detail": detail,
                "suggestion": "Flyttet kode refererer et symbol der stadig findes i kildefilen. "
                              "Brug verify_refactor(source='{source_file}', source_for_deps='<modul>') "
                              "for at opdage manglende afhængigheder. Tilføj import med add_import() "
                              "eller flyt det manglende symbol med batch_extract_symbols()."}

    if "syntaxerror" in dl or "invalid syntax" in dl:
        return {"category": "syntax_error", "detail": detail,
                "suggestion": "AST-brud i en modulfil. Brug verify_refactor(source='<modul>') for "
                              "at finde den præcise syntax-fejl, og ret med edit_file()."}

    if "assertionerror" in dl or "assert " in dl:
        return {"category": "test_logic", "detail": detail,
                "suggestion": "En test fejler på assert. Læs testen og produktionskoden med "
                              "read_location() for at forstå forventet vs. faktisk opførsel. "
                              "Ret produktionskoden med edit_file()."}

    return {"category": "unknown", "detail": detail,
            "suggestion": "Læs test-output for at identificere fejlen. Brug read_location() til at "
                          "læse den kode der refereres i fejlmeddelelsen."}


def _resolve_refactor_plan_path(agent, plan_file="refactor_plan.md"):
    """Resolve refactor plan path against AGENT_WORKDIR.
    
    Tries workdir-relative first, then falls back to agent._refactor_plan_path.
    Returns the correct absolute path or empty string if not found.
    """
    wd_resolve = os.environ.get('AGENT_WORKDIR', '')
    if wd_resolve:
        refac_plan_path = os.path.join(wd_resolve, plan_file)
    else:
        refac_plan_path = plan_file
    if not os.path.exists(refac_plan_path):
        refac_plan_path = getattr(agent, '_refactor_plan_path', '')
        if not refac_plan_path and plan_file:
            refac_plan_path = plan_file
        if refac_plan_path and not os.path.isabs(refac_plan_path) and wd_resolve:
            refac_plan_path = os.path.join(wd_resolve, refac_plan_path)
    return refac_plan_path if (refac_plan_path and os.path.exists(refac_plan_path)) else ""



def _check_import_placement(filepath: str) -> str | None:
    """Check if any import statements are inside functions/classes.
    Returns a warning message if found, None otherwise."""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            code = f.read()
    except Exception:
        return None
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return None
    bad = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            for child in ast.walk(node):
                if child is node:
                    continue
                if isinstance(child, (ast.Import, ast.ImportFrom)):
                    names = ", ".join(a.name for a in child.names)
                    kind = type(node).__name__
                    bad.append(f"'{names}' inside {kind} '{node.name}' at line {child.lineno}")
    if bad:
        return "\u26a0\ufe0f Import inde i funktion/klasse: " + "; ".join(bad) + ". Flyt importen til toppen af filen."
    return None



def _refactor_actually_moved_code(agent: Any) -> bool:
    """Return True if a real refactor was performed (ALL modules moved + api_server reduced).

    For the refactor template, a passing test suite is NOT proof that the
    refactor was done — tests pass against the unchanged api_server.py if no
    code was actually moved. This helper verifies ALL three conditions:

      1. ``refactor_plan.md`` exists and lists at least one module
      2. EVERY module listed in the plan exists on disk AND has substantive
         code (functions or classes with >= 20 lines)
      3. ``api_server.py`` has been reduced to under 1000 lines (the original
         file was 1521 lines, so any real refactor must shrink it)

    Returns True unconditionally when the active template is not ``refactor``
    (e.g. bugfix, kodeanalyse) so existing behaviour is preserved.
    """
    template = getattr(agent, "active_template", "") or ""
    if template != "refactor":
        return True
    _wd = agent_files._resolve_workdir()
    plan_path = os.path.join(_wd, "refactor_plan.md")
    if not os.path.exists(plan_path):
        return False
    try:
        import agent_phase_checks as _apc
        modules = _apc._parse_refactor_plan_modules(plan_path)
    except Exception:
        return False
    if not modules:
        return False
    for mod in modules:
        if not mod or "/" in mod or "\\" in mod:
            continue
        path = os.path.join(_wd, mod)
        if not _apc._has_real_code(path, min_lines=20):
            return False
    # Check that the source file (from plan header) has been reduced
    # Use plan header: "# Refactor Plan for <file.py>"
    _target_file = None
    try:
        with open(plan_path, "r", encoding="utf-8") as _f:
            _first = _f.readline(200)
        _m = re.search(r'for\s+([a-zA-Z_][\w.]+\.py)', _first, re.IGNORECASE)
        if _m:
            _target_file = _m.group(1)
    except (OSError, UnicodeDecodeError):
        pass
    if _target_file:
        _target_path = os.path.join(_wd, _target_file)
        if os.path.exists(_target_path):
            try:
                with open(_target_path, encoding="utf-8") as f:
                    _line_count = sum(1 for _ in f)
                # Original was ~1000+, reduced version should be well under 500
                if _line_count >= 500:
                    return False
            except OSError:
                return False
    else:
        # Fallback: check api_server.py (legacy refactors)
        _api_path = os.path.join(_wd, "api_server.py")
        if os.path.exists(_api_path):
            try:
                with open(_api_path, encoding="utf-8") as f:
                    _line_count = sum(1 for _ in f)
                if _line_count >= 1000:
                    return False
            except OSError:
                return False
    return True



def _build_refactor_phase_context(agent: Any, source_file: str = "api_server.py") -> str:
    """Build a structured symbol-status block for refactor phases.
    Reads refactor_plan.md + AST of source/target modules so the LLM
    sees EXACTLY which symbols need extraction/cleanup.
    """
    plan_path = _resolve_refactor_plan_path(agent, "refactor_plan.md")
    if not plan_path or not os.path.exists(plan_path):
        return ""

    modules = agent_phase_checks._parse_refactor_plan_modules(plan_path)
    if not modules:
        return ""

    _wd2 = agent_files._resolve_workdir()

    try:
        with open(plan_path, encoding="utf-8") as f:
            plan_text = f.read()
    except Exception:
        plan_text = ""

    src_result = agent_files.list_symbols(source_file)
    source_syms: dict[str, str] = {}
    if src_result.get("success"):
        source_syms = {s["name"]: s.get("type", "?")
                       for s in src_result["symbols"]
                       if s.get("type") in ("function", "class", "async_function")}

    from symbol_checks import _parse_plan_symbol_mapping
    per_module = _parse_plan_symbol_mapping(plan_text)

    parts: list[str] = []
    parts.append(f"\n\n## STATUS: Symboler i {source_file} vs plan")

    for mod_name in sorted(modules):
        mod_path = os.path.join(_wd2, mod_name)
        planned_syms = per_module.get(mod_name, [])
        if not planned_syms:
            continue

        target_syms: set[str] = set()
        if os.path.exists(mod_path):
            tgt_result = agent_files.list_symbols(mod_path)
            if tgt_result.get("success"):
                target_syms = {s["name"] for s in tgt_result["symbols"]
                               if s.get("type") in ("function", "class", "async_function")}

        in_source = sorted(s for s in planned_syms if s in source_syms)
        in_target = sorted(s for s in planned_syms if s in target_syms)
        missing = sorted(s for s in planned_syms if s not in source_syms and s not in target_syms)

        parts.append(f"\n### {mod_name}")
        if in_source:
            parts.append(f"  I {source_file} (skal flyttes til {mod_name}): {', '.join(in_source)}")
        if in_target:
            parts.append(f"  ALLEREDE i {mod_name}: {', '.join(in_target)}")
        if missing:
            parts.append(f"  MANGLER (skal oprettes i {mod_name}): {', '.join(missing)}")

    all_planned = set()
    for syms in per_module.values():
        all_planned.update(syms)
    unplanned = sorted(s for s in source_syms if s not in all_planned)
    if unplanned:
        parts.append(f"\n### Ikke i planen (beholdes i {source_file})")
        parts.append(f"  {', '.join(unplanned)}")

    return "\n".join(parts)



def _save_full_context_for_refactor(agent: Any, messages: list[dict]) -> None:
    """Save the full conversation history to a temp file for context recovery.

    This allows the LLM (or diagnostics) to retrieve what happened in earlier iterations
    when the active message window has been truncated.
    """
    session_id = getattr(agent, '_session_id', 'unknown')
    log_dir = os.path.join(os.getcwd(), "logs", "llm_responses", str(session_id))
    try:
        os.makedirs(log_dir, exist_ok=True)
    except OSError:
        return
    path = os.path.join(log_dir, "full_context.json")
    try:
        serializable = []
        for m in messages:
            role = m["role"]
            content = m.get("content")
            tool_calls = m.get("tool_calls")
            # Skip empty assistant blocks (content=None, no tool_calls)
            if role == "assistant" and not content and not tool_calls:
                continue
            entry = {"role": role}
            if content:
                if isinstance(content, str):
                    entry["content"] = content
                elif isinstance(content, list):
                    entry["content"] = [p if not isinstance(p, dict) or p.get("type") != "image_url" else {"type": "image_url", "image_url": {"url": "[IMAGE]"}} for p in content]
            if tool_calls:
                entry["tool_calls"] = tool_calls
            serializable.append(entry)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(serializable, f, ensure_ascii=False)
    except (OSError, TypeError):
        pass



def _count_symbols_in_file(filepath: str) -> int:
    """Count top-level symbols in a Python file."""
    try:
        import agent_files as _af
        result = _af.list_symbols(filepath)
        if isinstance(result, dict) and result.get("success"):
            return result.get("count", 0)
    except Exception:
        pass
    return 0



def _get_symbol_names_in_file(filepath: str) -> set[str]:
    """Get set of top-level symbol names in a Python file."""
    try:
        import agent_files as _af
        result = _af.list_symbols(filepath)
        if isinstance(result, dict) and result.get("success"):
            return {s["name"] for s in result.get("symbols", [])}
    except Exception:
        pass
    return set()



def _validate_ekstraher_symbols(agent: Any) -> str | None:
    """Validate that ALL planned symbols from refactor plan exist in their target modules.

    Returns None if every module has all its planned symbols.
    Returns a detailed error string listing missing files/symbols otherwise.
    """
    planned = getattr(agent, '_planned_symbols_per_target', None)
    if not planned:
        plan_path = "refactor_plan.md"
        _wd = os.environ.get("AGENT_WORKDIR", "")
        if _wd:
            plan_path = os.path.join(_wd, plan_path)
        if os.path.exists(plan_path):
            try:
                with open(plan_path, "r", encoding="utf-8") as f:
                    from symbol_checks import _parse_plan_symbol_mapping
                    planned = _parse_plan_symbol_mapping(f.read())
            except Exception:
                return None
        if not planned:
            return None

    issues: list[str] = []
    total_planned = 0
    total_actual = 0

    for mod_file, expected_syms in planned.items():
        if not expected_syms:
            continue
        mod_file = mod_file.strip('`')
        total_planned += len(expected_syms)

        mod_path = mod_file
        _wd = os.environ.get("AGENT_WORKDIR", "")
        if _wd and not os.path.isabs(mod_file):
            mod_path = os.path.join(_wd, mod_file)

        if not os.path.exists(mod_path):
            issues.append(
                f"  \u26a0\ufe0f {mod_file}: FIL MANGLER ({len(expected_syms)} plannede symboler)"
            )
            continue

        actual = _get_symbol_names_in_file(mod_path)
        total_actual += len(actual & set(expected_syms))

        missing_syms = [s for s in expected_syms if s not in actual]
        if missing_syms:
            # Filter out nested functions that can't be extracted
            # Check if the missing symbol still exists in the source as a nested function
            try:
                from refactoring_engine import _is_nested_function
                source_path = os.environ.get("AGENT_WORKDIR", "")
                if source_path and os.path.exists(os.path.join(source_path, 'api_server.py')):
                    source_path = os.path.join(source_path, 'api_server.py')
                elif os.path.exists('api_server.py'):
                    source_path = 'api_server.py'
                if source_path and os.path.exists(source_path):
                    with open(source_path, 'r', encoding='utf-8') as _sf:
                        _src_content = _sf.read()
                    _src_tree = ast.parse(_src_content)
                    nested_still_present = []
                    for sym in missing_syms[:]:
                        for _node in ast.walk(_src_tree):
                            if isinstance(_node, (ast.FunctionDef, ast.AsyncFunctionDef)) and _node.name == sym and _is_nested_function(_src_tree, _node):
                                nested_still_present.append(sym)
                                missing_syms.remove(sym)
                                total_actual += 1  # not really missing
                                break
            except Exception:
                pass

        if missing_syms:
            desc = (
                f"  \u26a0\ufe0f {mod_file}: mangler {len(missing_syms)}/{len(expected_syms)} "
                f"symboler: {', '.join(missing_syms[:8])}"
            )
            if len(missing_syms) > 8:
                desc += f" ...og {len(missing_syms) - 8} flere"
            issues.append(desc)

    if not issues:
        return None

    missing_count = total_planned - total_actual
    msg = (
        f"{t(K.SYS_ERROR_PREFIX, agent.lang)}: Ekstraher valideringsfejl \u2014 "
        f"{missing_count} symboler er IKKE placeret korrekt i deres moduler.\n"
        f"Fremdrift: {total_actual}/{total_planned} symboler.\n\n"
        + "\n".join(issues)
        + f"\n\nBrug batch_extract_symbols for at flytte de manglende symboler "
          f"til de rigtige filer, eller skriv filerne manuelt med write_file."
    )
    return msg



def _build_module_progress_msg(agent: Any) -> str:
    """Build a progress summary for Ekstraher phase: completed vs pending modules.

    Returns a multi-line string like:
        ✅ config.py: 10/10 symboler
        ⏳ file_utils.py: 0/11 symboler — næste: batch_extract_symbols(...)
        ⏳ processor.py: 0/10 symboler
        ⏳ user_handler.py: 0/10 symboler

    Returns empty string if no plan data is available.
    """
    planned = getattr(agent, '_planned_symbols_per_target', None)
    if not planned:
        return ""
    lines = []
    next_batch = None
    for mod, planned_syms in planned.items():
        if not planned_syms:
            continue
        mod = mod.strip('`')
        mod_basename = os.path.basename(mod)
        exists = os.path.exists(mod)
        if exists:
            actual = _get_symbol_names_in_file(mod)
            done_count = len(actual & set(planned_syms))
            total = len(planned_syms)
            if done_count >= total:
                lines.append(f"  ✅ {mod_basename}: {total}/{total} symboler")
            else:
                missing = [s for s in planned_syms if s not in actual]
                lines.append(f"  ⏳ {mod_basename}: {done_count}/{total} symboler — mangler: {', '.join(missing[:5])}")
                if next_batch is None:
                    _src = getattr(agent, '_source_file', '') or ''
                    if not _src:
                        import re as _re_src
                        _m = _re_src.search(r"([a-zA-Z_][\w.]+\.py)", getattr(agent, 'original_prompt', '') or '')
                        if _m:
                            _src = _m.group(1)
                    next_batch = f"batch_extract_symbols(source='{_src}', symbols='{', '.join(missing)}', target='{mod_basename}')"
        else:
            lines.append(f"  ⏳ {mod_basename}: 0/{len(planned_syms)} symboler — endnu ikke oprettet")
            if next_batch is None:
                _src = getattr(agent, '_source_file', '') or ''
                if not _src:
                    import re as _re_src
                    _m = _re_src.search(r"([a-zA-Z_][\w.]+\.py)", getattr(agent, 'original_prompt', '') or '')
                    if _m:
                        _src = _m.group(1)
                next_batch = f"batch_extract_symbols(source='{_src}', symbols='{', '.join(planned_syms)}', target='{mod_basename}')"
    if not lines:
        return ""
    result = "\n".join(lines)
    if next_batch:
        result += f"\n\n📝 Næste skridt: {next_batch}"
    return result



def _detect_module_deps(module_path: str, all_modules: list[str]) -> list[str]:
    """Detect which planned modules a module depends on via imports."""
    if not os.path.exists(module_path):
        return []
    try:
        with open(module_path, 'r', encoding='utf-8') as f:
            content = f.read()
    except (OSError, IOError):
        return []
    deps = []
    for mod in all_modules:
        mod_name = os.path.splitext(os.path.basename(mod))[0]
        if mod != module_path and mod_name in content:
            deps.append(mod)
    return deps



def _resolve_source_file(agent: Any, prompt: str = "") -> str:
    """Resolve the source file being refactored from prompt or agent state.

    Priority:
    1. agent._source_file (set by _build_initial_messages)
    2. 'Location: filename.py' in prompt
    3. First 'filename.py' in prompt  
    4. Fallback to 'api_server.py'
    """
    src = getattr(agent, '_source_file', '') or ''
    if src:
        return src
    import re as _re
    m = _re.search(r'Location:\s*([a-zA-Z_][\w./-]+\.py)', prompt)
    if m:
        return m.group(1)
    m = _re.search(r'([a-zA-Z_][\w./-]+\.py)', prompt)
    if m:
        return m.group(1)
    return "api_server.py"



def _check_refactor_progress(agent: Any | None = None, prompt: str = "") -> str:
    """Check refactor progress from plan file OR directly from source file.

    Returns a status string like:
      'Already created (5/8): mod_a.py, mod_b.py, ...
        Remaining (3/8): mod_c.py, mod_d.py, mod_e.py'
    Falls back to counting symbols in source file when no plan exists.
    """
    import os as _os
    import re as _re
    parts = []
    plan_content = ""

    _src = _resolve_source_file(agent, prompt) if agent else "api_server.py"

    # Try plan-based progress first
    plan_path = _os.path.join(_os.environ.get('AGENT_WORKDIR', ''), 'refactor_plan.md') if _os.environ.get('AGENT_WORKDIR') else 'refactor_plan.md'
    if _os.path.exists(plan_path):
        try:
            with open(plan_path, 'r', encoding='utf-8') as f:
                plan_content = f.read()
        except (OSError, UnicodeDecodeError):
            plan_content = ""
        modules = _re.findall(r'`([a-zA-Z_][\w.]+\.py)`', plan_content)
        if modules:
            modules = sorted(set(modules))
            # Parse symbol mapping to verify each module has its planned symbols
            try:
                import symbol_checks as _sc
                sym_map = _sc._parse_plan_symbol_mapping(plan_content)
            except Exception:
                sym_map = {}
            existing_complete = []
            existing_partial = []
            remaining = []
            for m in modules:
                if not _os.path.exists(m):
                    remaining.append(m)
                else:
                    planned_syms = sym_map.get(m, [])
                    if planned_syms:
                        actual_syms = set(_get_symbol_names_in_file(m))
                        missing_in_mod = [s for s in planned_syms if s not in actual_syms]
                        if missing_in_mod:
                            existing_partial.append("{} (mangler {}/{}: {})".format(
                                m, len(missing_in_mod), len(planned_syms),
                                ', '.join(missing_in_mod[:5])))
                        else:
                            existing_complete.append(m)
                    else:
                        existing_complete.append(m)
            total = len(modules)
            if existing_complete:
                parts.append("Komplet ({}/{}): {}".format(len(existing_complete), total, ', '.join(existing_complete)))
            if existing_partial:
                parts.append("Ufuldstændig ({}/{}): {}".format(len(existing_partial), total, ', '.join(existing_partial)))
            if remaining:
                parts.append("⚠️ MANGLER ({}/{}): {}".format(len(remaining), total, ', '.join(remaining)))

    # Always count symbols in source file (fallback when no plan exists)
    try:
        import agent_files as _af
        result = _af.list_symbols(_src)
        if isinstance(result, dict) and result.get("success"):
            symbols = result.get("symbols", [])
            count = len(symbols) if isinstance(symbols, list) else 0
            parts.append("{}: {} symbols tilbage (mål: ≤50) og ingen flyttede funktioner tilbage i originalfilen".format(_src, count))
    except Exception:
        pass

    # For Opdatér phase: show symbols still in source vs already removed
    if _os.path.exists(plan_path):
        try:
            with open(plan_path, 'r', encoding='utf-8') as f:
                plan_content = f.read()
        except (OSError, UnicodeDecodeError):
            plan_content = ""
    if _os.path.exists(_src) and plan_content:
        try:
            with open(_src, 'r', encoding='utf-8') as f:
                src_content = f.read()
            src_nodes = _re.findall(r'^def (\w+)|^class (\w+)', src_content, _re.MULTILINE)
            src_symbols = sorted(set(n[0] or n[1] for n in src_nodes))
            plan_symbols = _re.findall(r'`(\w+)`[^`]*flyttes|`(\w+)`[^`]*rykkes|symbol_name=\'(\w+)\'', plan_content)
            planned = sorted(set(s for t in plan_symbols for s in t if s))
            if planned and src_symbols:
                still_in_src = [s for s in planned if s in src_symbols]
                already_removed = [s for s in planned if s not in src_symbols]
                if still_in_src:
                    parts.append("Skal fjernes fra {} ({}): {}".format(_src, len(still_in_src), ', '.join(still_in_src)))
                if already_removed:
                    parts.append("Allerede fjernet fra {} ({}): {}".format(_src, len(already_removed), ', '.join(already_removed)))
        except (OSError, UnicodeDecodeError):
            pass

    return '\n'.join(parts)



def _all_planned_modules_exist(args: dict) -> bool:
    """Check if ALL modules listed in refactor_plan.md exist on disk.

    Used as arg_check for batch_extract_symbols → rf_e1 mapping so
    'Følg refactor_plan.md nøjagtigt — opfyld ALLE moduler deri'
    is only marked done when every planned module file exists.
    """
    import os as _os
    _wd = _os.environ.get('AGENT_WORKDIR', '') or _os.getcwd()
    plan_path = _os.path.join(_wd, "refactor_plan.md")
    if not _os.path.exists(plan_path):
        return False
    try:
        from file_checks import _parse_refactor_plan_modules
        mods = _parse_refactor_plan_modules(plan_path)
        return bool(mods) and all(_os.path.exists(m) for m in mods)
    except Exception:
        return False
