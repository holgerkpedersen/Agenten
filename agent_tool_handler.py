import os
import json
from i18n import K
from lang import t
import agent_git
from typing import Any, Generator
from agent_config import log, EXECUTION_TIMEOUT, _WRITE_TOOLS, PHASE_ALIASES, REQUIRED_ACTION_TOOLS, CLOSE_PHASE_ALIASES, ISSUE_ID_PATTERN, AUTO_RESOLVE_PATTERNS, FRAMEWORK_PY, _TODO_TOOL_MAP
from agent_message_builder import _build_chunk_hint, _build_phase_reason, _build_initial_messages, _msg_content_len, _truncate_messages, _build_truncation_summary, _cont_hint, _add_user_msg
from agent_refactor_helpers import _resolve_refactor_plan_path, _check_import_placement, _refactor_actually_moved_code, _build_refactor_phase_context, _save_full_context_for_refactor, _count_symbols_in_file, _get_symbol_names_in_file, _validate_ekstraher_symbols, _build_module_progress_msg, _detect_module_deps, _resolve_source_file, _check_refactor_progress, _all_planned_modules_exist, _classify_test_failure
from agent_utils import _is_greenfield, _use_native_tools, _normalize_phase, _inject_todo_tools
from agent_stream import _parse_test_summary, _track_produced_file, _get_phase_auto_complete_msg, _extract_last_assistant_text, _get_modified_core_files, _verify_self_modification, _run_full_test_suite, _execute_autoresearch_issue, _finalize_task_stream, _generate_phase_todos, solve_task_stream
from agent_task_phase import _get_phase_task_tools, _get_max_tool_calls, _get_max_iterations, set_task_tools, solve_task, _set_phase_model
import agent_files

def _handle_tool_call(agent: Any, parsed: dict, messages: list[dict], called_tools: dict[str, int], tools_list: str, task_node: Any, original_prompt: str) -> dict | None:
    """handle tool call.
    
    Args:
        agent:
        parsed:
        messages:
        called_tools:
        tools_list:
        task_node:
        original_prompt:"""
    tool_key = parsed['tool'] + str(parsed.get('args', {}))
    dup_count = called_tools.get(tool_key, 0)
    called_tools[tool_key] = dup_count + 1
    # verify_refactor results change as symbols are extracted — don't dedup-block
    _DEDUP_EXEMPT = {"verify_refactor"}
    if dup_count >= 1 and parsed['tool'] not in _DEDUP_EXEMPT:
        # For batch_extract_symbols/extract_symbol: show module progress
        if parsed['tool'] in ("batch_extract_symbols", "extract_symbol"):
            _progress = _build_module_progress_msg(agent)
            if _progress:
                _target = (parsed.get('args') or {}).get('target', '?')
                _add_user_msg(messages, (
                    f"{t(K.SYS_ERROR_PREFIX, agent.lang)}: "
                    f"Du har allerede dette resultat for {_target}.\n\n"
                    f"📊 Fremgang:\n{_progress}\n\n"
                    f"Gå videre til næste modul med batch_extract_symbols."
                ))
                return None
        _add_user_msg(messages, f"{t(K.SYS_ERROR_PREFIX, agent.lang)}: {t(K.SYS_DUP_RESULT, agent.lang)}")
        return None
    # Block docs/ writes during Ekstraher — they waste iterations on status reports
    if parsed["tool"] == "write_file" and isinstance(parsed.get("args"), dict):
        _wf_path = (parsed.get("args") or {}).get("path", "")
        _phase_lower = _normalize_phase(task_node.name).lower() if hasattr(task_node, 'name') else ''
        if getattr(agent, 'active_template', '') == 'refactor' and _phase_lower == "ekstraher":
            _wf_norm = _wf_path.replace("\\", "/").lower()
            if _wf_norm.startswith("docs/") or "/docs/" in _wf_norm or _wf_norm.endswith(".md") and not _wf_norm.endswith("refactor_plan.md"):
                _add_user_msg(messages, (
                    f"{t(K.SYS_ERROR_PREFIX, agent.lang)}: "
                    f"Skriv IKKE dokumentation eller statusrapporter i Ekstraher-fasen. "
                    f"Brug batch_extract_symbols til at oprette manglende .py moduler. "
                    f"Tjek 'Mangler' listen i din budget-besked."
                ))
                return None

    # In test phases, force write_file as the first tool call — block reads before write
    if "test" in _normalize_phase(task_node.name):
        write_file_called = any(k.startswith("write_file{") for k in called_tools if k.split("{")[0] != parsed["tool"])
        if not write_file_called and parsed["tool"] != "write_file":
            _add_user_msg(messages, f"{t(K.SYS_ERROR_PREFIX, agent.lang)}: {t(K.WRITE_FILE_FIRST, agent.lang)}")
            return None

    agent._log("TOOL", t(K.LOG_TOOL_CALLING, agent.lang).format(tool=parsed['tool']), str(parsed.get("args", {})))
    # Cache list_symbols results per file
    if parsed["tool"] == "list_symbols" and isinstance(parsed.get("args"), dict):
        _ls_file = parsed["args"].get("filepath", "")
        if _ls_file and _ls_file in getattr(agent, '_list_symbols_cache', {}):
            _cached = agent._list_symbols_cache[_ls_file]
            agent._log("TOOL", t(K.LOG_TOOL_RESULT, agent.lang).format(tool=parsed['tool']), f"(cached) cached result")
            return {"tool": parsed["tool"], "args": parsed.get("args", {}), "result": _cached, **({"checkpoint_msg": ""} if False else {})}
    # Plan-deviation check: block batch_extract_symbols if symbols go to wrong module
    if parsed["tool"] == "batch_extract_symbols" and isinstance(parsed.get("args"), dict):
        _planned = getattr(agent, '_planned_symbols_per_target', None)
        if _planned:
            _target = os.path.basename(parsed["args"].get("target", ""))
            _symbols_raw = parsed["args"].get("symbols", "")
            _called_syms = set(s.strip() for s in _symbols_raw.split(",") if s.strip())
            # Find which module each symbol is PLANNED for
            _wrong = []
            for _sym in _called_syms:
                for _mod, _plan_syms in _planned.items():
                    if _sym in _plan_syms and os.path.basename(_mod) != _target:
                        _wrong.append((_sym, os.path.basename(_mod)))
                        break
            if _wrong:
                _wrong_str = ", ".join(f"{s} → {m}" for s, m in _wrong[:5])
                _deviation_msg = (
                    f"{t(K.SYS_ERROR_PREFIX, agent.lang)}: "
                    f"Plan-deviation: {_wrong_str}\n"
                    f"Du sender symboler til {_target} men planen siger de hører til et andet modul. "
                    f"Ret batch kaldet til at bruge det korrekte target."
                )
                _add_user_msg(messages, _deviation_msg)
                return {"tool": parsed["tool"], "args": parsed["args"],
                        "result": {"success": False, "error": "Plan deviation blocked"}}
    result = agent.tool_registry.execute(parsed["tool"], parsed["args"])
    if parsed["tool"] == "list_symbols" and isinstance(result, dict) and result.get("success"):
        _f = (parsed.get("args") or {}).get("filepath", "")
        if _f:
            agent._list_symbols_cache[_f] = result
    if parsed["tool"] in ("extract_symbol", "batch_extract_symbols") and isinstance(parsed.get("args"), dict):
        _src = parsed["args"].get("source", "")
        if _src in getattr(agent, '_list_symbols_cache', {}):
            del agent._list_symbols_cache[_src]
    # verify_refactor: clear source symbol cache so next list_symbols sees updated file
    if parsed["tool"] == "verify_refactor" and isinstance(parsed.get("args"), dict):
        _src = parsed["args"].get("source", "")
        if _src in getattr(agent, '_list_symbols_cache', {}):
            del agent._list_symbols_cache[_src]
    result_str = json.dumps(result, ensure_ascii=False)
    agent._log("TOOL", t(K.LOG_TOOL_RESULT, agent.lang).format(tool=parsed['tool']), result_str)
    agent._record_tool_call(
        phase=getattr(task_node, 'name', '?'),
        tool=parsed['tool'],
        args=parsed.get('args', {}),
        success=result.get('success', False) if isinstance(result, dict) else True,
        error=result.get('error', '') if isinstance(result, dict) else '',
    )

    if parsed["tool"] in ("write_file", "edit_file", "extract_symbol"):
        if parsed["tool"] == "extract_symbol":
            if isinstance(result, dict) and not result.get("success"):
                called_tools.pop(parsed['tool'] + str(parsed.get('args', {})), None)
            else:
                agent._current_task_iteration = 0
                agent._non_productive_reminder_sent = False
                # Inject compact progress for extract_symbol
                inner = result.get("result", {})
                if isinstance(inner, dict) and inner.get("success"):
                    sym = inner.get("symbol", "?")
                    tgt = os.path.basename(inner.get("target", "?"))
                    _add_user_msg(messages, f"[SYSTEM: ✅ {sym} flyttet til {tgt}]")
        else:
            agent._current_task_iteration = 0
            agent._non_productive_reminder_sent = False
            if isinstance(result, dict) and result.get("success") is False:
                agent._write_failed = True
            unread_hints = agent._hints_available - agent._hints_requested
            if unread_hints:
                ids = ", ".join(sorted(unread_hints)[:3])
                _add_user_msg(messages, f"\u26a0\ufe0f {t(K.ANTI_LEAKAGE_WARNING, agent.lang)} ({ids})")
    if parsed["tool"] == "run_tests":
        inner = result.get("result", {}) if isinstance(result, dict) else {}
        if isinstance(inner, dict) and inner.get("success") is False:
            agent._tests_failed = True
        else:
            agent._tests_failed = False
        test_summary = _parse_test_summary(inner)
        exit_code = inner.get("exit_code")
        if hasattr(agent, '_core'):
            agent._core.record_test_outcome(
                test_file=parsed.get("args", {}).get("test_path", "") or "run_tests",
                passed=exit_code == 0,
                summary=test_summary,
            )
        if test_summary:
            if exit_code == 0:
                agent._log("INFO", f"✅ Tests PASSED: {test_summary}",
                           json.dumps({"exit_code": exit_code, "summary": test_summary}, ensure_ascii=False))
            else:
                agent._log("ERROR", f"❌ Tests FAILED: {test_summary}",
                           json.dumps({"exit_code": exit_code, "summary": test_summary}, ensure_ascii=False))
        elif exit_code == 0:
            agent._log("INFO", "✅ Tests passed",
                       json.dumps({"exit_code": 0}))
        elif exit_code:
            agent._log("ERROR", f"❌ Tests failed (exit code: {exit_code})",
                       json.dumps({"exit_code": exit_code}))
        if exit_code and exit_code != 0:
            _is_refactor_test = (
                getattr(agent, 'active_template', '') == 'refactor'
                and _normalize_phase(getattr(task_node, 'name', '')).lower() == 'test'
            )
            if _is_refactor_test:
                _raw = (inner or {}).get("stdout", "") or ""
                _diag = _classify_test_failure(_raw)
                result_str += (
                    f"\n\n[SYSTEM: Fejltype: {_diag['category']}\n"
                    f"Detalje: {_diag['detail']}\n"
                    f"Forslag: {_diag['suggestion']}]"
                )

    if parsed["tool"] == "locate":
        if isinstance(result, dict) and result.get("success"):
            agent._located_files.add(os.path.abspath(result.get("file", "")))

    if parsed["tool"] == "read_chunk":
        file_key = parsed.get("args", {}).get("file_key", "")
        if file_key.startswith("file_"):
            file_path = os.path.abspath(file_key[5:])
            if file_path in agent._located_files:
                result_str += "\n\n📌 OBS: Du har allerede læst funktion(er) i denne fil med locate. Brug locate(filepath='...', name='andet_navn') i stedet for read_chunk — det er hurtigere."

    if parsed["tool"] == "read_issue":
        if isinstance(result, dict) and result.get("success"):
            issue_data = result.get("issue", {})
            iid = issue_data.get("id", "")
            if iid:
                if issue_data.get("_hints_available"):
                    agent._hints_available.add(iid)
                if issue_data.get("_hints_read"):
                    agent._hints_requested.add(iid)
                if issue_data.get("status") == "resolved":
                    agent.issue_resolved = True
                    log.info("Issue %s already resolved — marking agent.issue_resolved=True", iid)

    # Inject compact progress summary after batch_extract_symbols
    if parsed["tool"] == "batch_extract_symbols" and result.get("success"):
        inner = result.get("result", {})
        if isinstance(inner, dict) and inner.get("succeeded", 0):
            target = os.path.basename(inner.get("target", "?"))
            source = os.path.basename(inner.get("source", "?"))
            succeeded = inner.get("succeeded", 0)
            failed = inner.get("failed", 0)
            total = inner.get("total", 0)
            symbols_in_batch = [r.get("symbol", "") for r in inner.get("results", []) if r.get("success")]
            log.info("batch_extract_symbols RESULT: %s → %s (%d/%d succeeded, %d failed): %s",
                     source, target, succeeded, total, failed, ', '.join(symbols_in_batch))
            progress_msg = f"[SYSTEM: ✅ {succeeded} symboler flyttet til {target}: {', '.join(symbols_in_batch)}]"
            _add_user_msg(messages, progress_msg)

            # Check for missing dependencies after batch_extract_symbols
            missing_deps = inner.get("missing_dependencies", []) if isinstance(inner, dict) else []
            if missing_deps:
                deps_str = ', '.join(missing_deps)
                source_name = os.path.basename(inner.get("source", "?"))
                warning = (
                    f"[SYSTEM: ⚠️ ADVARSEL — {target} refererer symboler der kun "
                    f"findes i {source_name}: {deps_str}. Ekstraher disse symboler "
                    f"først eller tilføj imports for at undgå NameError.]"
                )
                log.warning("batch_extract_symbols: missing deps in %s from %s: %s",
                            target, source_name, deps_str)
                _add_user_msg(messages, warning)

    checkpoint_msg = agent_git.verify_pr_step(agent, parsed["tool"], result, task_node.name, original_prompt)
    if checkpoint_msg:
        _add_user_msg(messages, f"!!! CHECKPOINT - {checkpoint_msg}")
        agent._log("INFO", "CHECKPOINT", checkpoint_msg)
        return {"type": "checkpoint", "tool": parsed["tool"], "args": parsed.get("args", {}), "result": result, "checkpoint_msg": checkpoint_msg}
    else:
        agent._checkpoint_tools.add(parsed["tool"] + str(parsed.get("args", {})))
        msg = f"{t(K.TOOL_RESULT_PREFIX, agent.lang).format(tool=parsed['tool'])}\n{result_str}\n\n{_cont_hint(agent, tools_list)}"
        if agent._write_failed:
            msg += f"\n\n⚠️ {t(K.SYS_ERROR_PREFIX, agent.lang)}: edit_file mislykkedes. Læs filen igen og kopier teksten nøjagtigt som old_text."
        _add_user_msg(messages, msg)
        return {"type": "tool_result", "tool": parsed["tool"], "args": parsed.get("args", {}), "result": result}



def _old_text_was_in_prior_result(old_text: str, messages: list[dict], min_match_ratio: float = 0.8) -> bool:
    """Check if old_text appears (or substantially appears) in a prior tool result.

    This prevents the LLM from fabricating old_text from memory — it must have
    actually read the file content first.

    Returns True if old_text (or a large substring of it) was found in a prior
    tool result message.
    """
    if not old_text or len(old_text.strip()) < 10:
        return True  # too short to meaningfully check
    # Collect all prior tool result content
    prior_content = []
    for msg in messages:
        if msg.get("role") == "tool":
            content = msg.get("content", "")
            if isinstance(content, str):
                prior_content.append(content)
    if not prior_content:
        return False
    combined = "\n".join(prior_content)
    # Exact match
    if old_text in combined:
        return True
    # Substring match: check if the longest contiguous part of old_text is in results
    lines = old_text.split("\n")
    if len(lines) >= 3:
        # Check sliding windows of increasing size
        for window_size in range(len(lines), max(2, len(lines) // 2), -1):
            for start in range(0, len(lines) - window_size + 1):
                chunk = "\n".join(lines[start:start + window_size])
                if chunk in combined:
                    return True
    # Fallback: check if at least 80% of non-empty lines appear
    non_empty = [l for l in lines if l.strip()]
    if non_empty:
        found = sum(1 for l in non_empty if l.strip() in combined)
        if found >= len(non_empty) * min_match_ratio:
            return True
    return False



def _check_required_tools(agent: Any, called_tools: dict, task_name: str = "") -> str | None:
    """check required tools.

    Args:
        agent:
        called_tools:
        task_name:"""
    template = getattr(agent, "active_template", "")
    if template == "refactor" and task_name:
        refactor_writing_phases = ("plan", "ekstraher", "opdat")
        # Check BOTH current called_tools AND historical tool_log (covers retries)
        _all_writes = set()
        for k in (called_tools or {}):
            _all_writes.add(k.split("{")[0])
        for e in (getattr(agent, "_tool_log", None) or []):
            tn = e.get("tool", "")
            if tn:
                _all_writes.add(tn)
        has_written = any(t in _all_writes for t in
            ("write_file", "edit_file", "delete_file", "extract_symbol", "batch_extract_symbols", "remove_symbol", "add_import"))
        if any(k in _normalize_phase(task_name).lower() for k in refactor_writing_phases) and not has_written:
            iteration = getattr(agent, "_current_task_iteration", 0)
            if iteration >= 3 and not getattr(agent, "_non_productive_reminder_sent", False):
                agent._non_productive_reminder_sent = True
                return t(K.SYS_REQUIRED_TOOLS_REFACTOR, agent.lang).format(count=iteration)
        # For refactor Ekstraher: even when tools were called, verify actual symbol removal
        if template == "refactor" and has_written and "ekstraher" in _normalize_phase(task_name).lower():
            try:
                src_file = getattr(agent, '_source_file', '') or 'api_server.py'
                _wd_src = os.environ.get('AGENT_WORKDIR', '')
                if _wd_src and not os.path.isabs(src_file):
                    abs_src = os.path.join(_wd_src, src_file)
                else:
                    abs_src = src_file if os.path.isabs(src_file) else os.path.abspath(src_file)
                sym_result = agent_files.list_symbols(abs_src)
                if sym_result.get("success"):
                    remaining = len(sym_result.get("symbols", []))
                    planned = getattr(agent, '_planned_symbols_per_target', None) or {}
                    planned_count = sum(len(syms) for syms in planned.values())
                    if planned_count > 0 and remaining > planned_count:
                        return t(K.LOG_EXTRACT_INCOMPLETE, agent.lang).format(remaining=remaining)
                    elif planned_count == 0 and remaining >= 50:
                        return t(K.LOG_EXTRACT_INCOMPLETE, agent.lang).format(remaining=remaining)
            except Exception:
                pass
        programming_writing_phases = ("arkitekturdesign", "implementeringsplan", "kodeimplementering")
        has_written = any(k in (called_tools or {}) for k in called_tools if k.startswith("write_file") or k.startswith("edit_file"))
        if any(k in _normalize_phase(task_name).lower() for k in programming_writing_phases) and not has_written:
            iteration = getattr(agent, "_current_task_iteration", 0)
            if iteration >= 5 and not getattr(agent, "_non_productive_reminder_sent", False):
                agent._non_productive_reminder_sent = True
                return t(K.SYS_REQUIRED_TOOLS_PROGRAMMING, agent.lang).format(count=iteration)
    available = set(agent.tool_registry.active_tools or [])
    # Phase-specific required tools: only require action tools that the
    # phase's TEMPLATE_TASK_TOOLS actually lists. This prevents phases
    # from requiring tools they don't use (e.g., refactor Opdatér should
    # not require extract_symbol or write_file if remove_symbol+add_import suffice).
    phase_tools = _get_phase_task_tools(agent, task_name)
    if phase_tools is not None:
        required = phase_tools
    else:
        required = available & REQUIRED_ACTION_TOOLS
    if not required:
        return None
    phase = _normalize_phase(task_name).lower() if task_name else ""
    if "update_issue_status" in required:
        if phase not in CLOSE_PHASE_ALIASES:
            required.discard("update_issue_status")
    # In verification phases, edit_file/write_file are optional — they're only
    # allowed for quick test-fix cycles, not required.
    if phase in ("verifikation", "green"):
        required -= {"edit_file", "write_file"}
    if not required:
        return None
    called_names = set()
    for k in called_tools:
        name = k.split("{")[0]
        called_names.add(name)
    # If update_issue_status was called, the phase is complete regardless
    # of which write tools were or weren't called — return None immediately.
    if "update_issue_status" in called_names:
        return None
    # If the run_tests auto-complete already marked the issue resolved
    # (Test phase with passing tests), edit_file/write_file are no longer needed.
    if getattr(agent, "issue_resolved", False) and getattr(agent, 'active_template', '') != 'refactor':
        required -= {"edit_file", "write_file"}
    # For refactor Test phase: if run_tests was called and passed, no editing needed
    if template == "refactor" and "run_tests" in called_names and not getattr(agent, '_tests_failed', False):
        required -= {"edit_file", "verify_refactor"}
    uncalled = required - called_names
    import logging as _llog
    _llog.getLogger(__name__).debug(
        f"_check_req: template={template} phase={_normalize_phase(task_name).lower() if task_name else '?'} "
        f"called_names={sorted(called_names)} required={sorted(required)} uncalled={sorted(uncalled)} "
        f"issue_resolved={getattr(agent,'issue_resolved',False)} tests_failed={getattr(agent,'_tests_failed',None)}"
    )
    # write_file and extract_symbol are alternatives — calling either satisfies the requirement
    if "write_file" in uncalled and "extract_symbol" in called_names:
        uncalled.discard("write_file")
    if "extract_symbol" in uncalled and "write_file" in called_names:
        uncalled.discard("extract_symbol")
    # write_file and edit_file are alternatives — you either create new files or edit existing ones
    if "write_file" in uncalled and "edit_file" in called_names:
        uncalled.discard("write_file")
    if "edit_file" in uncalled and "write_file" in called_names:
        uncalled.discard("edit_file")
    # For selvforbedring/ret: create_issue is an acceptable alternative to edit_file
    # — the LLM documents the needed fix instead of applying it directly.
    if template == "selvforbedring" and "ret" in phase:
        if "edit_file" in uncalled and "create_issue" in called_names:
            uncalled.discard("edit_file")
    # remove_symbol + add_import together are the AST-based refactoring
    # approach — they achieve the same goal as edit_file/write_file/
    # delete_file/add_method/add_function.
    if "remove_symbol" in called_names and "add_import" in called_names:
        uncalled -= {"edit_file", "write_file", "delete_file", "add_method", "add_function"}
    # write_file/edit_file are supersets of add_method/add_function —
    # if the LLM wrote or edited the whole file, per-method tools aren't needed.
    if "write_file" in called_names or "edit_file" in called_names:
        uncalled -= {"add_method", "add_function"}
    # extract_symbol/batch_extract_symbols are supersets of add_method/add_function
    # — extracting a symbol to a new file achieves the same goal as adding a method.
    if "extract_symbol" in called_names or "batch_extract_symbols" in called_names:
        uncalled -= {"add_method", "add_function"}
    # write_file(overwrite="force") on an existing file is an alternative to
    # remove_symbol — the entire file was rewritten including all changes.
    if "remove_symbol" in uncalled:
        for k in called_tools:
            if k.startswith("write_file") and '"force"' in k.split("{", 1)[-1]:
                uncalled.discard("remove_symbol")
                break
    # tool_log success check: tools where ALL attempts failed due to LLM error
    # do NOT count as satisfied — only system blocks (hash, path safety) are excused.
    if agent._tool_log and not uncalled:
        for req_tool in required:
            if req_tool in called_names:
                attempts = [e for e in agent._tool_log if e.get("tool") == req_tool and e.get("success") is False]
                all_attempts = [e for e in agent._tool_log if e.get("tool") == req_tool]
                if all_attempts and len(attempts) == len(all_attempts):
                    # All attempts failed — check if system-blocked or LLM error
                    system_blocked = any(
                        "HARD BLOCK" in str(e.get("error", ""))
                        or "Adgang n\u00e6gtet" in str(e.get("error", ""))
                        for e in all_attempts
                    )
                    if system_blocked:
                        continue  # system prevented — still counts as satisfied
                    # LLM error — add back to uncalled (must try differently)
                    uncalled.add(req_tool)
    if uncalled:
        return t(K.LOG_REQUIRED_TOOLS_MISSING, agent.lang).format(tools=", ".join(sorted(uncalled)))
    return None



def _extract_issue_id(text: str) -> str | None:
    """extract issue id.
    
    Args:
        text:"""
    m = ISSUE_ID_PATTERN.search(text)
    return m.group(0).upper() if m else None
