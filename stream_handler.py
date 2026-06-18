from flask import Flask, request, jsonify, send_from_directory, Response, stream_with_context
from agent_core import Agent
from session_manager import SessionManager, _guard_json_body, agent, session_manager, current_session_id, execution_status, execution_status_lock, export_folder, export_folder_lock, list_sessions, create_session, rename_session, delete_session, manage_token, get_lang, user_reply, save_layout, load_layout, get_session_prompts, get_context_for_prompt, add_prompt_to_session, reset_execution, execute_without_stream
import agent_skills
import json
import os
from typing import Any, Generator
from lang import t, get_ui_translations
from i18n import K
from middleware import log, BASE_DIR, STATIC_DIR, app, _is_development_mode, _RateLimiter, rate_limiter, _rate_limit
from stream_manager import active_streams, active_streams_lock, current_session_lock, _file_mtime, VERSION_FILES, BUILD_INFO, serve_upload, preview_export
from image_handler import sanitize_filename, _validate_image_content, _normalize_images, image_upload, image_list, image_clear, image_remove, list_python_files
from model_manager import load_session, save_current_session, get_models, set_model, loaded_models, load_model_route, unload_model_route, stop_execution, _ensure_model_loaded, TEMPLATE_GUIDANCE, _validate_template_prompt, decompose, redecompose, _count_tasks, _check_client, _count_source_symbols, autoresearch_events, autoresearch_sessions, autoresearch_pause, autoresearch_resume, autoresearch_toggle, autoresearch_status, autoresearch_run
from session_manager import SessionManager, _guard_json_body, agent, session_manager, current_session_id, execution_status, execution_status_lock, export_folder, export_folder_lock, list_sessions, create_session, rename_session, delete_session, manage_token, get_lang, user_reply, save_layout, load_layout, get_session_prompts, get_context_for_prompt, add_prompt_to_session, reset_execution, execute_without_stream, _extract_batch_results, _extract_retry_context, _build_retry_lessons, _save_session_data
import time

@app.route("/api/execute-stream", methods=["GET", "POST"])
def execute_stream() -> Any:
    """execute stream.
    
    Yields:
        ..."""
    global current_session_id
    ui_lang = "da"

    # Create a session-scoped agent to avoid race conditions with concurrent SSE requests (ARC-007)
    stream_agent = Agent()
    stream_agent.llm = agent.llm
    stream_agent.decompose_llm = agent.decompose_llm
    stream_agent.searcher = agent.searcher
    stream_agent._session_id = current_session_id or "unknown"
    if current_session_id:
        os.environ['AGENT_SESSION_ID'] = current_session_id
    else:
        os.environ.pop('AGENT_SESSION_ID', None)

    log.info("Execute stream - session: %s", current_session_id)
    if current_session_id:
        session_data = session_manager.load_session(current_session_id)
        if session_data:
            st = session_data.get("show_thinking", True)
            ui_lang = session_data.get("ui_lang", session_data.get("lang", "da"))
            log.info("Session show_thinking: %s", st)
            if session_data.get("original_prompt"):
                stream_agent.original_prompt = session_data["original_prompt"]
            if session_data.get("tree"):
                stream_agent.task_tree_from_dict(session_data["tree"])
            if session_data.get("lang"):
                stream_agent.lang = session_data["lang"]
                stream_agent.tool_registry.lang = stream_agent.lang
            if session_data.get("file_chunks"):
                stream_agent.file_chunks = session_data["file_chunks"]
                from agent_files import auto_detect_workdir
                auto_detect_workdir(session_data["file_chunks"], session_data.get("original_prompt", ""))
            stream_agent.images = _normalize_images(session_data.get("images", []))
            if session_data.get("template"):
                stream_agent.active_template = session_data["template"]
                allowed = agent_skills.TEMPLATE_TOOLS.get(session_data["template"]) if session_data["template"] in agent_skills.TEMPLATE_TOOLS else None
                stream_agent.tool_registry.set_active_tools(allowed)
            if session_data.get("decompose_model"):
                stream_agent.decompose_llm.set_model(session_data["decompose_model"])
            if session_data.get("execute_model"):
                stream_agent.llm.set_model(session_data["execute_model"])
            if session_data.get("issue_resolved"):
                stream_agent.issue_resolved = True

            fpc = session_data.get("full_prompt_with_context", "")
            if not fpc:
                fc = session_data.get("file_context", "")
                if fc and isinstance(fc, list):
                    file_context_content = "\n\n" + t(K.FILE_CONTEXT_HEADER, stream_agent.lang)
                    for f in fc:
                        filename = f.get('filename', t(K.UNKNOWN, stream_agent.lang))
                        content = f.get('content', '')
                        file_context_content += f"\n### {filename}\n\n```{filename}\n{content}\n```\n"
                    fpc = stream_agent.original_prompt + file_context_content
                else:
                    fpc = stream_agent.original_prompt
            stream_agent.full_prompt_with_context = fpc
            stream_agent.show_thinking = st
            log.info("Agent show_thinking set to: %s", stream_agent.show_thinking)
            stream_agent.stop_requested = False

    # Register this agent in active streams for session-scoped access (BUG-001)
    session_id = current_session_id  # capture locally to avoid race condition (BUG-011)
    if session_id:
        with active_streams_lock:
            active_streams[session_id] = stream_agent

    _ensure_model_loaded(stream_agent.llm.model)

    def generate(agent: Any) -> Generator[str, None, None]:
        """generate.
        
        Args:
            agent:
        
        Yields:
            ..."""
        global execution_status
        _ui = ui_lang
        if agent.task_tree is None:
            if session_id:
                session_data = session_manager.load_session(session_id)
                if session_data and session_data.get("tree"):
                    agent.task_tree_from_dict(session_data["tree"])
                    log.info("Tree restored from session in generate()")
            if agent.task_tree is None:
                yield f"data: {json.dumps({'type': 'error', 'message': t(K.ERR_DECOMPOSE_FIRST, _ui)})}\n\n"
                return

        original_prompt = getattr(agent, 'full_prompt_with_context', '') or agent.original_prompt
        show_thinking = getattr(agent, 'show_thinking', True)
        yield f"data: {json.dumps({'type': 'context', 'original_prompt': original_prompt, 'show_thinking': show_thinking})}\n\n"

        agent.agent_log = []
        agent.execution_log = []
        agent.issue_resolved = False
        agent.current_phase = None

        agent._log("INFO", "Nedbryd LLM", agent.decompose_llm.model if hasattr(agent, 'decompose_llm') else '?')
        agent._log("INFO", "Udfør LLM", agent.llm.model)

        for log in agent.agent_log[-10:]:
            yield f"data: {json.dumps({'type': 'log', 'log': log})}\n\n"

        MAX_CTX = 150000
        task_context_prompt = original_prompt[:MAX_CTX] + ("\n\n[... trunkeret — brug read_chunk() for at læse flere chunks ...]" if len(original_prompt) > MAX_CTX else "")

        total_tasks = _count_tasks(agent.task_tree.root)
        completed = [0]
        yield f"data: {json.dumps({'type': 'start', 'total_tasks': total_tasks})}\n\n"

        saved = False
        with execution_status_lock:
            execution_status["running"] = True
            execution_status["log"] = []
        try:
            if _check_client(agent):
                yield f"data: {json.dumps({'type': 'stopped', 'message': t(K.UI_STREAM_STOPPED, _ui)})}\n\n"
                return
            yield from _execute_with_stream(agent.task_tree.root, agent, total_tasks, completed, task_context_prompt, show_thinking, _ui, session_id)
            _save_session_data(session_id, agent, _ui)
            saved = True
            with execution_status_lock:
                execution_status["running"] = False
                execution_status["progress"] = 100
            yield f"data: {json.dumps({'type': 'complete', 'message': t(K.UI_ALL_DONE, _ui)})}\n\n"
        except Exception as e:
            if not saved:
                _save_session_data(session_id, agent, _ui)
            with execution_status_lock:
                execution_status["running"] = False
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
        finally:
            if not saved:
                _save_session_data(session_id, agent, _ui)
                with execution_status_lock:
                    execution_status["running"] = False

    return Response(stream_with_context(generate(stream_agent)), mimetype='text/event-stream', headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no', 'Connection': 'keep-alive'})


@app.route("/api/log", methods=["GET"])
def get_log() -> Any:
    """get log."""
    return jsonify({"log": agent.agent_log})



def _execute_with_stream(node: Any, agent: Any, total_tasks: int, completed: list[int], task_context_prompt: str, show_thinking: bool, ui_lang: str, current_session_id: str | None) -> Generator[str, None, None]:
    """execute with stream.
    
    Args:
        node:
        agent:
        total_tasks:
        completed:
        task_context_prompt:
        show_thinking:
        ui_lang:
        current_session_id:
    
    Yields:
        ..."""
    global execution_status
    if _check_client(agent):
        return

    # Skip nodes already marked done/skipped (manual checkpoint)
    if node.status in ("done", "skipped"):
        skip_msg = node.result or f"Markeret som {node.status} (manuelt)"
        yield f"data: {json.dumps({'type': 'task_start', 'task': node.name, 'success_criteria': getattr(node, 'success_criteria', []), 'skipped': True})}\n\n"
        yield f"data: {json.dumps({'type': 'task_done', 'task': node.name, 'status': node.status, 'result': skip_msg})}\n\n"
        completed[0] += _count_tasks(node)
        progress = int((completed[0] / total_tasks) * 100)
        with execution_status_lock:
            execution_status["progress"] = progress
        yield f"data: {json.dumps({'type': 'progress', 'progress': progress})}\n\n"
        agent.agent_log.append({"timestamp": time.time(), "level": "INFO", "message": f"Opgave sprunget over: {node.name}", "detail": f"Allerede markeret som {node.status}"})
        yield f"data: {json.dumps({'type': 'log', 'log': agent.agent_log[-1]})}\n\n"
        return

    task_data = {'type': 'task_start', 'task': node.name}
    if hasattr(node, 'success_criteria') and node.success_criteria:
        task_data['success_criteria'] = node.success_criteria
    yield f"data: {json.dumps(task_data)}\n\n"
    with execution_status_lock:
        execution_status["current_task"] = node.name

    child_results = []
    for child in node.children:
        if _check_client(agent):
            return
        if getattr(agent, 'issue_resolved', False) and getattr(agent, 'active_template', '') not in ('refactor', 'programmering'):
            skip_msg = "Skipped — issue was already resolved in an earlier phase"
            for remaining in node.children[node.children.index(child):]:
                remaining.status = "skipped"
                remaining.result = skip_msg
                yield f"data: {json.dumps({'type': 'task_start', 'task': remaining.name, 'success_criteria': getattr(remaining, 'success_criteria', [])})}\n\n"
                yield f"data: {json.dumps({'type': 'task_done', 'task': remaining.name, 'status': remaining.status, 'result': skip_msg})}\n\n"
                agent.agent_log.append({"timestamp": time.time(), "level": "INFO", "message": f"Opgave sprunget over: {remaining.name}", "detail": skip_msg})
                yield f"data: {json.dumps({'type': 'log', 'log': agent.agent_log[-1]})}\n\n"
                completed[0] += _count_tasks(remaining)
            progress = int((completed[0] / total_tasks) * 100)
            with execution_status_lock:
                execution_status["progress"] = progress
            yield f"data: {json.dumps({'type': 'progress', 'progress': progress})}\n\n"
            break
        yield from _execute_with_stream(child, agent, total_tasks, completed, task_context_prompt, show_thinking, ui_lang, current_session_id)
        if child.result:
            child_results.append(f"- {child.name}: {child.result}")
        # Stop execution on failed phase — don't continue to siblings
        if child.status == "failed":
            for remaining in node.children[node.children.index(child) + 1:]:
                remaining.status = "skipped"
                skip_msg = f"Skipped — forrige fase '{child.name}' fejlede"
                remaining.result = skip_msg
                yield f"data: {json.dumps({'type': 'task_start', 'task': remaining.name, 'success_criteria': getattr(remaining, 'success_criteria', [])})}\n\n"
                yield f"data: {json.dumps({'type': 'task_done', 'task': remaining.name, 'status': remaining.status, 'result': skip_msg})}\n\n"
                agent.agent_log.append({"timestamp": time.time(), "level": "INFO", "message": f"Opgave sprunget over: {remaining.name}", "detail": skip_msg})
                yield f"data: {json.dumps({'type': 'log', 'log': agent.agent_log[-1]})}\n\n"
                completed[0] += _count_tasks(remaining)
            progress = int((completed[0] / total_tasks) * 100)
            with execution_status_lock:
                execution_status["progress"] = progress
            yield f"data: {json.dumps({'type': 'progress', 'progress': progress})}\n\n"
            break

    if node.children and all(c.status in ("done", "skipped", "failed") for c in node.children):
        has_failed = any(c.status == "failed" for c in node.children)
        node.status = "failed" if has_failed else "done"
        node.result = "\n".join(child_results) if child_results else "All subtasks completed"
        completed[0] += 1
        progress = int((completed[0] / total_tasks) * 100)
        with execution_status_lock:
            execution_status["progress"] = progress
        yield f"data: {json.dumps({'type': 'progress', 'progress': progress})}\n\n"
        yield f"data: {json.dumps({'type': 'task_done', 'task': node.name, 'status': node.status, 'result': node.result[:500]})}\n\n"
        return

    node.status = "running"
    full_response = ""

    is_refactor = getattr(agent, 'active_template', '') == 'refactor'
    _MAX_RETRIES = 5 if is_refactor else 3
    retry_contexts = []
    initial_symbols = _count_source_symbols()

    for retry_attempt in range(_MAX_RETRIES + 1):
        pre_symbols = _count_source_symbols()
        if pre_symbols < 0:
            pre_symbols = initial_symbols

        if retry_attempt > 0:
            if _check_client(agent):
                return
            lessons = _build_retry_lessons(retry_contexts[-1], agent,
                                           all_contexts=retry_contexts)
            improved_prompt = lessons + "\n\n" + task_context_prompt
            agent._log("INFO", f"Genforsøg {retry_attempt}/{_MAX_RETRIES} for {node.name}",
                       f"Forrige fejl: {retry_contexts[-1].get('failure_reason','?')}")
            yield f"data: {json.dumps({'type': 'retry', 'task': node.name, 'attempt': retry_attempt, 'max': _MAX_RETRIES})}\n\n"

        full_response = ""
        for event in agent.solve_task_stream(node, improved_prompt if retry_attempt > 0 else task_context_prompt):
            if _check_client(agent):
                return
            if event["type"] == "chunk":
                full_response += event["chunk"]
                yield f"data: {json.dumps({'type': 'llm_chunk', 'task': node.name, 'chunk': event['chunk']})}\n\n"
            elif event["type"] == "tool_call":
                yield f"data: {json.dumps({'type': 'tool_call', 'task': node.name, 'tool': event['tool'], 'args': event['args']})}\n\n"
            elif event["type"] == "tool_result":
                yield f"data: {json.dumps({'type': 'tool_result', 'task': node.name, 'tool': event['tool'], 'result': event['result']})}\n\n"
            elif event["type"] == "output_files":
                yield f"data: {json.dumps({'type': 'output_files', 'task': node.name, 'files': event['files']})}\n\n"
            elif event["type"] == "log":
                yield f"data: {json.dumps({'type': 'log', 'log': event['log']})}\n\n"
            elif event["type"] == "todo_add":
                yield f"data: {json.dumps({'type': 'todo_add', 'todo': event['todo']})}\n\n"
            elif event["type"] == "todo_update":
                yield f"data: {json.dumps({'type': 'todo_update', 'id': event['id'], 'done': event['done']})}\n\n"
            elif event["type"] == "budget":
                yield f"data: {json.dumps({'type': 'budget', 'iteration': event['iteration'], 'max': event['max'], 'remaining': event['remaining']})}\n\n"
            elif event["type"] == "done":
                full_response = event["result"]
            elif event["type"] == "autoresearch":
                yield f"data: {json.dumps(event)}\n\n"

        if not full_response:
            full_response = t(K.UI_TASK_RESULT_PREFIX, ui_lang) + ": " + node.name
        if node.status == "running":
            node.status = "done"
        node.result = full_response

        if node.status == "failed" and retry_attempt < _MAX_RETRIES:
            post_symbols = _count_source_symbols()
            context = _extract_retry_context(
                node, agent, full_response,
                symbols_before=pre_symbols,
                symbols_after=post_symbols,
            )
            retry_contexts.append(context)
            node.status = "pending"

            # Momentum: stop hvis sidste 2 forsøg flyttede 0 symbols (KUN Ekstraher)
            if len(retry_contexts) >= 2 and getattr(node, 'name', '') and 'ekstraher' in node.name.lower():
                last_two_moved = sum(c.get("symbols_moved", 0) for c in retry_contexts[-2:])
                if last_two_moved == 0:
                    agent._log("INFO",
                               f"Stopper retry tidligt — 0 symbols flyttet i sidste 2 forsøg",
                               f"({retry_attempt+1}/{_MAX_RETRIES} forsøg brugt)")
                    yield f"data: {json.dumps({'type': 'log', 'log': {'timestamp': time.time(), 'level': 'INFO', 'message': f'Stopper retry: 0 symbols flyttet i sidste 2 forsøg', 'detail': ''}})}\n\n"
                    node.status = "failed"
                    break
            continue
        break

    if _check_client(agent):
        return
    completed[0] += 1
    progress = int((completed[0] / total_tasks) * 100)
    with execution_status_lock:
        execution_status["progress"] = progress
        execution_status["log"].append({"task": node.name, "status": node.status, "result": full_response[:200]})
    yield f"data: {json.dumps({'type': 'progress', 'progress': progress})}\n\n"
    yield f"data: {json.dumps({'type': 'task_done', 'task': node.name, 'status': node.status, 'result': full_response})}\n\n"
    agent.agent_log.append({"timestamp": time.time(), "level": "INFO", "message": t(K.UI_TASK_DONE_PREFIX, ui_lang) + ": " + node.name, "detail": full_response})
    tests_failed = getattr(agent, '_tests_failed', None)
    agent.execution_log.append({
        "timestamp": time.time(),
        "task": node.name,
        "status": "done",
        "result_length": len(full_response),
        "tests_failed": tests_failed,
    })
    yield f"data: {json.dumps({'type': 'log', 'log': agent.agent_log[-1]})}\n\n"
    if current_session_id:
        session_manager.add_prompt_result(current_session_id, node.name, full_response, None)
