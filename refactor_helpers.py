import os
import ast
import re
import agent_files
from typing import Any, Generator
import agent_phase_checks
import json
from config_utils import log, EXECUTION_TIMEOUT, _WRITE_TOOLS, FRAMEWORK_PY, _get_max_tool_calls, _get_max_iterations, _set_phase_model, _is_greenfield
import agent_issues
from lang import t
import config

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



def _get_modified_core_files(agent: Any) -> set[str]:
    """Return set of core framework file basenames modified during this task."""
    modified: set[str] = set()
    for entry in getattr(agent, '_tool_log', []):
        tool = entry.get("tool", "")
        if tool not in ("write_file", "edit_file", "delete_file", "extract_symbol", "remove_symbol"):
            continue
        if not entry.get("success", False):
            continue
        args = entry.get("args", {})
        if not args:
            continue
        filepath = args.get("filepath") or args.get("path") or args.get("source") or ""
        basename = os.path.basename(filepath)
        if basename in FRAMEWORK_PY:
            modified.add(basename)
    return modified



def _execute_autoresearch_issue(agent: Any, issue_id: str) -> Generator[dict, None, bool]:
    """Execute a CORE issue inline via selvforbedring template.

    Called from _finalize_task_stream when a phase fails and auto-research
    creates a CORE issue. Builds a task tree (Analyser → Diagnosticér → Ret
    → Verificér → Commit) and executes each phase via solve_task_stream,
    yielding events through the same SSE stream so the user sees live progress.

    Depth-limited: tracks _autoresearch_depth on agent to prevent infinite
    recursion if a CORE issue's Ret phase fails and creates another CORE issue.

    Returns:
        True if ALL phases completed successfully, False if any phase failed.
    """
    from task_tree import TaskTree, TaskNode

    # Depth limit — max 2 nested auto-research sessions
    depth = getattr(agent, '_autoresearch_depth', 0)
    if depth >= 2:
        agent._log("AUTOR", f"Auto-research: max dybde ({depth}) nået for {issue_id}",
                   "Stopper for at undgå uendelig rekursion")
        if agent.agent_log:
            yield {"type": "log", "log": agent.agent_log[-1]}
        yield {"type": "autoresearch", "action": "error", "issue_id": issue_id,
               "error": "Max dybde nået — undgår uendelig rekursion"}
        return False

    # Load the issue
    try:
        data = agent_issues._load_issues()
        issue = next((i for i in data.get("issues", []) if i.get("id") == issue_id), None)
    except Exception:
        issue = None
    if not issue:
        return False

    prompt = (
        f"{issue.get('id', issue_id)}: {issue.get('title', '')}\n\n"
        f"{issue.get('description', '')}\n\n"
        f"Location: {issue.get('location', '—')}\n"
        f"Impact: {issue.get('impact', '—')}\n\n"
        f"{issue.get('proposed_fix', '')}"
    )

    yield {"type": "autoresearch", "action": "start", "issue_id": issue_id, "title": issue.get("title", "")}

    # Save original state
    orig_template = agent.active_template
    orig_prompt = getattr(agent, "original_prompt", "")
    orig_tree = getattr(agent, "task_tree", None)
    orig_file_chunks = dict(getattr(agent, "file_chunks", {}))
    orig_file_context = getattr(agent, "file_context", None)
    orig_full_prompt = getattr(agent, "full_prompt_with_context", "")

    # Configure for selvforbedring
    agent.active_template = "selvforbedring"
    agent.original_prompt = prompt
    agent._autoresearch_depth = depth + 1

    # Auto-load files from issue location
    try:
        from agent_file_context import _auto_load_issue_files, _auto_load_location_file
        _auto_load_issue_files(agent, prompt, "selvforbedring", None)
        _auto_load_location_file(agent, prompt)
    except ImportError:
        pass

    # Build task tree using title-case phase names (matches SECTION_INSTRUCTIONS
    # and TEMPLATE_PHASE_CHECKS keys).
    phase_names = list(agent_phase_checks.TEMPLATE_PHASE_CHECKS.get("selvforbedring", {}).keys())
    if not phase_names:
        phase_names = ["Analyser", "Diagnosticér", "Ret", "Verificér", "Commit"]
    tree = TaskTree(prompt)
    for phase_name in phase_names:
        tree.root.add_child(TaskNode(phase_name))
    agent.task_tree = tree

    # Execute each phase
    all_done = True
    for child in list(tree.root.children):
        child.status = "pending"
        try:
            for event in agent.solve_task_stream(child, prompt):
                yield event
                if event.get("type") == "done":
                    if child.status == "failed":
                        all_done = False
                        agent._log("AUTOR", f"Auto-research: {child.name} fejlede", issue_id)
                        yield {"type": "autoresearch", "action": "phase_failed", "issue_id": issue_id, "phase": child.name}
                        yield {"type": "log", "log": agent.agent_log[-1]}
                    else:
                        yield {"type": "autoresearch", "action": "phase_done", "issue_id": issue_id, "phase": child.name}
                    break
        except Exception as exc:
            agent._log("AUTOR", f"Auto-research: exception i {child.name}", str(exc)[:300])
            yield {"type": "log", "log": agent.agent_log[-1]}
            child.status = "failed"
            all_done = False
            break
        if not all_done:
            for remaining in list(tree.root.children)[tree.root.children.index(child) + 1:]:
                remaining.status = "skipped"
            break

    # Restore original state
    agent.active_template = orig_template
    agent.original_prompt = orig_prompt
    agent.task_tree = orig_tree
    agent.file_chunks = orig_file_chunks
    agent.file_context = orig_file_context
    agent.full_prompt_with_context = orig_full_prompt
    agent._autoresearch_depth = depth

    success = all_done and all(c.status == "done" for c in tree.root.children if c.status not in ("skipped",))
    yield {"type": "autoresearch", "action": "complete", "issue_id": issue_id, "success": success}

    if success:
        agent._log("AUTOR", f"Auto-research: {issue_id} gennemført",
                   "Alle faser i selvforbedring bestod")
        yield {"type": "log", "log": agent.agent_log[-1]}
    return success



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
            existing = [m for m in modules if _os.path.exists(m)]
            remaining = [m for m in modules if m not in existing]
            total = len(modules)
            if existing:
                parts.append("Allerede oprettet ({}/{}): {}".format(len(existing), total, ', '.join(existing)))
            if remaining:
                parts.append("Mangler ({}/{}): {}".format(len(remaining), total, ', '.join(remaining)))

    # Always count symbols in source file (fallback when no plan exists)
    try:
        import agent_files as _af
        result = _af.list_symbols(_src)
        if isinstance(result, dict) and result.get("success"):
            symbols = result.get("symbols", [])
            count = len(symbols) if isinstance(symbols, list) else 0
            parts.append("{}: {} symbols tilbage (mål: ≤50)".format(_src, count))
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



AUTO_RESOLVE_PATTERNS = [
    r'allerede (?:løst|rettet|fikset|fixet)',
    r'(?:fejlen|buggen|problemet) (?:findes ikke|eksisterer ikke|er væk)',
    r'koden er allerede (?:korrekt|rettet|fikset)',
    r'allerede (?:implementeret|udført)',
    r'already (?:fixed|resolved|solved|correct)',
    r'(?:bug|issue|problem) no longer (?:exists|reproducible|applicable)',
    r'(?:no change|nothing to fix)',
    r'intet at (?:rette|fikse|gøre)',
]



def _track_produced_file(agent: Any, tool_result: dict) -> None:
    """Extract file path from a successful write/edit tool result and track it."""
    tool = tool_result.get("tool", "")
    if tool not in _WRITE_TOOLS:
        return
    result = tool_result.get("result", {})
    if not isinstance(result, dict) or not result.get("success"):
        return
    path = result.get("result") or tool_result.get("args", {}).get("path", "")
    if not path and isinstance(result, dict):
        path = result.get("path", "")
    if path:
        if not os.path.isabs(path):
            workdir = os.environ.get("AGENT_WORKDIR", "")
            base = os.path.abspath(workdir) if workdir else os.path.abspath(".")
            path = os.path.normpath(os.path.join(base, path))
        agent._produced_files.add(os.path.abspath(path))



def _save_llm_prompt_file(agent: Any, task_name: str, iteration: int, messages: list[dict]) -> str:
    """Save full LLM prompt (all messages) to a file for later inspection."""
    session_id = getattr(agent, '_session_id', 'unknown')
    session_dir = os.path.join("logs", "llm_prompts", session_id)
    os.makedirs(session_dir, exist_ok=True)
    safe_name = task_name.replace(" ", "_").replace("/", "_").replace("\\", "_")[:30]
    filename = f"{safe_name}_iter{iteration}.json"
    filepath = os.path.join(session_dir, filename)
    try:
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(messages, f, ensure_ascii=False, indent=2, default=str)
    except Exception:
        pass
    return filepath



def _save_maintenance_prompt_dump(agent: Any, task_name: str, iteration: int, messages: list[dict], tool_defs: list[dict], pending_tc: list | None = None) -> str:
    session_id = getattr(agent, '_session_id', 'unknown')
    session_dir = os.path.join("logs", "maintenance_prompt_dump", session_id)
    os.makedirs(session_dir, exist_ok=True)
    safe_name = task_name.replace(" ", "_").replace("/", "_").replace("\\", "_")[:30]
    filepath = os.path.join(session_dir, f"{safe_name}_iter{iteration}.json")
    dump = {
        "session_id": session_id,
        "template": agent.active_template,
        "phase": task_name,
        "iteration": iteration,
        "is_greenfield": _is_greenfield(),
        "active_tools": list(agent.tool_registry.active_tools or []),
        "native_tools_enabled": config.NATIVE_TOOLS,
        "tool_choices": [tc.get("function", {}).get("name", "?") for tc in pending_tc] if pending_tc else [],
        "messages": messages[:2],
        "tool_definitions": tool_defs,
    }
    try:
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(dump, f, ensure_ascii=False, indent=2, default=str)
    except Exception:
        pass
    return filepath



def _save_llm_log_file(agent: Any, task_name: str, iteration: int, content: str) -> str:
    """Save LLM response to a file for later inspection."""
    session_id = getattr(agent, '_session_id', 'unknown')
    session_dir = os.path.join("logs", "llm_responses", session_id)
    os.makedirs(session_dir, exist_ok=True)
    safe_name = task_name.replace(" ", "_").replace("/", "_").replace("\\", "_")[:30]
    filename = f"{safe_name}_iter{iteration}.txt"
    filepath = os.path.join(session_dir, filename)
    try:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
    except Exception:
        pass
    return filepath
