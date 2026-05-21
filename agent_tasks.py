import os
import time
import json
from lang import t
from i18n import K
from tools import ToolRegistry
from llm_wrapper import LMStudioWrapper
import agent_skills
import agent_git


def add_image(agent, path):
    basename = os.path.basename(path)
    for img in agent.images:
        if isinstance(img, dict):
            if img.get("filename") == basename or img.get("filepath") == path:
                return {"success": True, "file": basename, "size": len(img.get("b64","")), "mime": img.get("mime",""), "note": "Allerede indl\u00e6st"}
            if img.get("filepath") and os.path.normpath(img["filepath"]) == os.path.normpath(path):
                return {"success": True, "file": basename, "size": len(img.get("b64","")), "mime": img.get("mime",""), "note": "Allerede indl\u00e6st"}

    if os.path.exists(path):
        return encode_and_store(agent, path)

    upload_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploads", basename)
    if os.path.exists(upload_path):
        return encode_and_store(agent, upload_path)

    loaded = [f"{i.get('filename','?')} ({i.get('filepath','?')})" if isinstance(i,dict) else str(i)[:40] for i in agent.images]
    return {"success": False, "error": f"Fil ikke fundet: {path}. Allerede indl\u00e6ste: {loaded or 'ingen'}"}


def encode_and_store(agent, path):
    raw_b64 = LMStudioWrapper.encode_image(path)
    size = os.path.getsize(path)
    ext = os.path.splitext(path)[1].lower().lstrip(".")
    mime = "jpeg" if ext in ("jpg","jpeg") else ext
    agent.images.append({"b64": raw_b64, "mime": mime, "filename": os.path.basename(path), "filepath": path})
    agent._log("TOOL", f"Billede tilf\u00f8jet: {os.path.basename(path)}", f"{size:,} bytes ({ext})")
    return {"success": True, "file": os.path.basename(path), "size": size, "mime": mime}


def truncate_conversation(agent, conversation, system_prompt):
    if len(conversation) > agent.max_conversation_chars and len(system_prompt) < agent.max_conversation_chars:
        mid = "\n\n[... tidligere kontekst afkortet / previous context truncated...]"
        keep = agent.max_conversation_chars - len(system_prompt) - len(mid)
        if keep > 0:
            return system_prompt + mid + conversation[-keep:]
    return conversation


def build_tool_guidance(agent, attempt):
    if attempt > 0:
        return ""

    tool_list = ', '.join(agent.tool_registry.tools.keys())
    if agent.tool_registry.active_tools is not None:
        tool_list = ', '.join(agent.tool_registry.active_tools)
    return f"\n\n" + t(K.TOOL_CONTINUATION, agent.lang).format(
        tools_list=tool_list,
        TOOL_MARKER=agent.tool_registry.TOOL_MARKER,
        DONE_MARKER=agent.tool_registry.DONE_MARKER
    )


def ask_ai(agent, prompt):
    response = ""
    for chunk in agent.llm.generate_stream(prompt, temperature=0.3, max_tokens=agent.max_tokens):
        response += chunk
    return response


def handle_tool_call(agent, action, conversation, already_called, attempt):
    tool_name = action["tool"]
    arguments = action.get("args", {})

    key = f"{tool_name}_{arguments}"
    times_called = already_called.get(key, 0)
    already_called[key] = times_called + 1

    if times_called >= 2:
        conversation += f"\n\n{t(K.SYS_ERROR_PREFIX, agent.lang)}: {t(K.TOOL_DUPLICATE_MSG, agent.lang).format(tool=tool_name)}"
        return conversation

    result = agent.tool_registry.execute(tool_name, arguments)
    result_text = json.dumps(result, ensure_ascii=False)

    conversation += f"\n\n{t(K.TOOL_RESULT_PREFIX, agent.lang).format(tool=tool_name)}\n{result_text}"
    return conversation


def set_task_tools(agent, task_name):
    if not agent.active_template or agent.active_template not in agent_skills.TEMPLATE_TASK_TOOLS:
        return
    template_tools = agent_skills.TEMPLATE_TASK_TOOLS[agent.active_template]
    for keyword, tools in template_tools.items():
        if keyword in task_name.lower():
            agent.tool_registry.set_active_tools(tools)
            agent._log("TOOL", f"Aktive tools for '{task_name[:40]}'", ', '.join(tools))
            return
    allowed = agent_skills.TEMPLATE_TOOLS.get(agent.active_template)
    if allowed is not None:
        agent.tool_registry.set_active_tools(allowed)


def solve_task(agent, task_node, original_prompt):
    task_node.status = "running"
    agent._log("INFO", f"Starting task: {task_node.name}")

    system_prompt = agent.tool_registry.build_system_prompt(task_node.name)
    conversation = system_prompt

    max_attempts = 5
    already_called_tools = {}
    answer = ""

    for attempt in range(max_attempts):
        prompt = conversation + build_tool_guidance(agent, attempt)
        ai_response = ask_ai(agent, prompt)

        action = agent.tool_registry.parse_response(ai_response)

        if action["type"] == "tool":
            conversation = handle_tool_call(agent, action, conversation, already_called_tools, attempt)

        elif action["type"] == "done":
            answer = action["result"]
            break

        elif action["type"] == "error":
            conversation += f"\n\n{t(K.SYS_ERROR_PREFIX, agent.lang)}: {action['message']}"

        elif action["type"] == "text":
            conversation += f"\n\n{t(K.SYS_ERROR_PREFIX, agent.lang)}: {t(K.TOOL_NO_RESULT, agent.lang)}"

        tool_for_msg = agent.tool_registry.active_tools[0] if agent.tool_registry.active_tools else t(K.SYS_FALLBACK_TOOL, agent.lang)
        if attempt == 0 and not already_called_tools and action["type"] != "done":
            conversation += f"\n\n{t(K.SYS_ERROR_PREFIX, agent.lang)}: {t(K.FIRST_TOOL_REQUIRED, agent.lang).format(tool=tool_for_msg)}"

        conversation = truncate_conversation(agent, conversation, system_prompt)

    if not answer:
        answer = "Task failed"
        task_node.status = "failed"
    else:
        task_node.status = "done"

    task_node.result = answer
    return answer


def solve_task_stream(agent, task_node, original_prompt):
    task_node.status = "running"
    agent._task_start_time = time.time()
    agent._log("INFO", t(K.LOG_TASK_START, agent.lang), f"{task_node.name} (model: {agent.llm.model})")
    set_task_tools(agent, task_node.name)
    agent._checkpoint_tools = set()
    agent._checkpoint_branch = ""

    available_keys = list(agent.file_chunks.keys())
    is_chunked = any(len(v) > 1 for v in agent.file_chunks.values())
    if is_chunked:
        chunk_hint_parts = []
        for key in available_keys:
            total = len(agent.file_chunks[key])
            display = key.replace("file_", "", 1)
            chunk_hint_parts.append(f"\n  read_chunk(chunk='{display}', index=2..{total}) eller chunk='{key}', index=2..{total}")
        chunk_hint = f"\n\n## TILG\u00c6NGELIGE FILER (brug read_chunk for at l\u00e6se alle chunks):{''.join(chunk_hint_parts)}\n"
    else:
        chunk_hint = ""

    section_instr = agent_skills.SECTION_INSTRUCTIONS.get(agent.active_template, {}).get(task_node.name, "")
    if section_instr:
        task_prompt = f"{section_instr}\n\nKontekst / Context: {original_prompt}{chunk_hint}"
    else:
        task_prompt = f"{task_node.name}\n\nKontekst / Context: {original_prompt}{chunk_hint}"


    agent._refresh_skills()
    agent._match_skills(original_prompt)
    skills_block = agent._format_skills_for_prompt()
    if skills_block:
        task_prompt = skills_block + task_prompt
        agent._log("SKILL", "Skills injectet i prompt", skills_block[:200])

    system_prompt = agent.tool_registry.build_system_prompt(task_prompt)
    agent._log("DEBUG", f"file_chunks keys: {list(agent.file_chunks.keys())}", "")
    agent._log("DEBUG", f"original_prompt length: {len(original_prompt)}", f"starts with: {original_prompt[:100]}")
    agent._log("DEBUG", f"system_prompt length: {len(system_prompt)}", f"contains file content: {'###' in system_prompt}")

    tools_list = ', '.join([k for k in agent.tool_registry.tools if agent.tool_registry.active_tools is None or k in agent.tool_registry.active_tools])
    lang_instr = t(K.ANSWER_IN, agent.lang)
    user_guidance = f"{lang_instr}. "
    if chunk_hint:
        user_guidance += chunk_hint.replace("## TILG\u00c6NGELIGE FILER (brug read_chunk for at l\u00e6se alle chunks):", "FILER:").strip() + " "
    if tools_list:
        user_guidance += t(K.TOOL_CONTINUATION, agent.lang).format(
            tools_list=tools_list,
            TOOL_MARKER=agent.tool_registry.TOOL_MARKER,
            DONE_MARKER=agent.tool_registry.DONE_MARKER
        )
    else:
        user_guidance += t(K.DONE_CONTINUATION, agent.lang).format(DONE_MARKER=agent.tool_registry.DONE_MARKER)

    if not chunk_hint and tools_list:
        read_only = all(t not in ('write_file',) for t in agent.tool_registry.active_tools or [])
        if read_only and not agent.images:
            user_guidance += f"\n\nOBS: Ingen filer er indl\u00e6st. Du KAN svare direkte med <<<DONE>>> uden at kalde v\u00e6rkt\u00f8jer f\u00f8rst. Sp\u00f8rg IKKE efter filnavne \u2014 brug din egen viden til at besvare opgaven."

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_guidance}
    ]
    agent._log("LLM", "System prompt", f"{len(system_prompt)} chars \u2014 {system_prompt[:300]}...")
    agent._log("LLM", "User guidance", user_guidance)

    full_response = ""
    text_fallback = ""
    max_iterations = 15 if agent_git.is_pr_workflow(task_node.name) else 10
    called_tools = {}

    def _add_user_msg(content):
        nonlocal messages
        messages.append({"role": "user", "content": content})

    def _truncate_messages():
        nonlocal messages
        def _content_len(m):
            c = m.get("content", "")
            if isinstance(c, str):
                return len(c)
            if isinstance(c, list):
                return sum(p.get("text", "").__len__() if isinstance(p, dict) and p.get("type") == "text" else 0 for p in c)
            return 0
        total = sum(_content_len(m) for m in messages)
        if total > agent.max_conversation_chars and len(messages) > 3:
            mid = "\n[... tidligere kontekst afkortet ...]"
            keep = agent.max_conversation_chars - _content_len(messages[0]) - _content_len(messages[1]) - len(mid)
            if keep > 0:
                tail_content = messages[-1]["content"]
                if isinstance(tail_content, str):
                    cropped = tail_content[-keep:] if len(tail_content) > keep else tail_content
                else:
                    cropped = "[...]"
                messages = messages[:2] + [{"role": "user", "content": mid + cropped}]

    for i in range(max_iterations):
        if agent.stop_requested:
            break

        if agent.pending_reply:
            messages.append({"role": "user", "content": agent.pending_reply})
            agent._log("USER", "Bruger svarer", agent.pending_reply[:100])
            agent.pending_reply = None

        response = ""
        for chunk in agent.llm.generate_stream(messages=messages, temperature=0.3, max_tokens=agent.max_tokens, images=agent.images):
            if agent.stop_requested:
                break
            response += chunk
            yield {"type": "chunk", "chunk": chunk}

        if agent.stop_requested:
            break

        messages.append({"role": "assistant", "content": response})

        parsed = agent.tool_registry.parse_response(response)
        agent._log("LLM", t(K.LOG_ITERATION, agent.lang).format(n=i+1), t(K.LOG_TYPE, agent.lang).format(type=parsed.get('type')))
        agent._log("LLM", "LLM response (raw)", response)

        if parsed["type"] == "tool":
            tool_key = parsed['tool'] + str(parsed.get('args', {}))
            dup_count = called_tools.get(tool_key, 0)
            called_tools[tool_key] = dup_count + 1

            if dup_count >= 2:
                _add_user_msg(f"{t(K.SYS_ERROR_PREFIX, agent.lang)}: Du har allerede dette resultat. G\u00e5 videre eller brug <<<DONE>>>.")
                _truncate_messages()
                continue

            if dup_count == 1:
                agent._log("TOOL", t(K.TOOL_DUPLICATE, agent.lang), parsed['tool'])

            agent._log("TOOL", t(K.LOG_TOOL_CALLING, agent.lang).format(tool=parsed['tool']), str(parsed.get("args", {})))
            result = agent.tool_registry.execute(parsed["tool"], parsed["args"])
            result_str = json.dumps(result, ensure_ascii=False)
            agent._log("TOOL", t(K.LOG_TOOL_RESULT, agent.lang).format(tool=parsed['tool']), result_str)
            yield {"type": "tool_call", "tool": parsed["tool"], "args": parsed.get("args", {})}
            yield {"type": "tool_result", "tool": parsed["tool"], "result": result}

            checkpoint_msg = agent_git.verify_pr_step(agent, parsed["tool"], result, task_node.name, original_prompt)
            if checkpoint_msg:
                _add_user_msg(f"!!! CHECKPOINT - {checkpoint_msg}")
                agent._log("INFO", "CHECKPOINT", checkpoint_msg)
                yield {"type": "checkpoint", "message": checkpoint_msg, "tool": parsed["tool"]}
            else:
                agent._checkpoint_tools.add(parsed["tool"] + str(parsed.get("args", {})))
                cont_hint = t(K.TOOL_CONTINUATION, agent.lang).format(
                    tools_list=tools_list,
                    TOOL_MARKER=agent.tool_registry.TOOL_MARKER,
                    DONE_MARKER=agent.tool_registry.DONE_MARKER
                )
                _add_user_msg(f"{t(K.TOOL_RESULT_PREFIX, agent.lang).format(tool=parsed['tool'])}\n{result_str}\n\n{cont_hint}")

            _truncate_messages()
            total_calls = sum(called_tools.values())
            if total_calls >= 8:
                full_response = t(K.LOG_AUTO_DONE, agent.lang).format(count=total_calls)
                break
            continue

        if parsed["type"] == "done":
            if agent_git.is_pr_workflow(task_node.name) and not called_tools:
                _add_user_msg(f"{t(K.SYS_ERROR_PREFIX, agent.lang)}: Du kaldte <<<DONE>>> uden at bruge nogen v\u00e6rkt\u00f8jer. Brug v\u00e6rkt\u00f8jerne f\u00f8rst.")
                _truncate_messages()
                continue

            if agent_git.is_pr_workflow(task_node.name):
                called_names = {t.split("{")[0] for t in agent._checkpoint_tools}
                if "github_create_pr" not in called_names:
                    msg = f"!!! CHECKPOINT - {t(K.CP_PR_FAILED, agent.lang)}"
                    _add_user_msg(msg)
                    agent._log("INFO", "CHECKPOINT", t(K.CP_PR_FAILED, agent.lang))
                    yield {"type": "checkpoint", "message": t(K.CP_PR_FAILED, agent.lang), "tool": "done"}
                    _truncate_messages()
                    continue
                missing_commit = agent_git.PR_COMMIT_TOOLS - called_names
                if missing_commit:
                    msg = f"!!! CHECKPOINT - {t(K.CP_NO_COMMIT, agent.lang)}"
                    _add_user_msg(msg)
                    agent._log("INFO", "CHECKPOINT", t(K.CP_NO_COMMIT, agent.lang))
                    yield {"type": "checkpoint", "message": t(K.CP_NO_COMMIT, agent.lang), "tool": "done"}
                    _truncate_messages()
                    continue
                if "git_push" not in called_names:
                    msg = f"!!! CHECKPOINT - {t(K.CP_NO_PUSH, agent.lang)}"
                    _add_user_msg(msg)
                    agent._log("INFO", "CHECKPOINT", t(K.CP_NO_PUSH, agent.lang))
                    yield {"type": "checkpoint", "message": t(K.CP_NO_PUSH, agent.lang), "tool": "done"}
                    _truncate_messages()
                    continue

            full_response = parsed["result"]
            done_idx = response.find(agent.tool_registry.DONE_MARKER)
            if done_idx > 0:
                pre_done = response[:done_idx].strip()
                if len(pre_done.strip()) > max(50, len(full_response) * 2):
                    full_response = pre_done
            break

        if parsed["type"] == "error":
            _add_user_msg(f"{t(K.SYS_ERROR_PREFIX, agent.lang)}: {parsed['message']}")
            _truncate_messages()
            continue

        if i == 0 and not called_tools:
            all_files_loaded = all(len(v) <= 1 for v in agent.file_chunks.values()) if agent.file_chunks else True
            if all_files_loaded and parsed["type"] in ("text", "done"):
                text_fallback = response.strip() if parsed["type"] == "text" else parsed.get("result", response.strip())
                if text_fallback and "ERROR" not in text_fallback and not text_fallback.startswith("<<<"):
                    full_response = text_fallback
                    break
            if parsed["type"] == "text":
                tool_for_msg = agent.tool_registry.active_tools[0] if agent.tool_registry.active_tools else t(K.SYS_FALLBACK_TOOL, agent.lang)
                _add_user_msg(f"{t(K.SYS_ERROR_PREFIX, agent.lang)}: {t(K.FIRST_TOOL_REQUIRED, agent.lang).format(tool=tool_for_msg)}")
                _truncate_messages()
                continue

        clean = response.strip() if "ERROR" not in response else ""
        if clean:
            text_fallback = clean
        _add_user_msg(t(K.TOOL_NO_RESULT, agent.lang))
        _truncate_messages()
        full_response = response
        if i >= 3:
            break

    if not full_response or "ERROR" in full_response:
        if called_tools:
            full_response = t(K.LOG_AUTO_DONE, agent.lang).format(count=len(called_tools))
            task_node.status = "done"
        elif text_fallback and "ERROR" not in text_fallback:
            full_response = text_fallback
            task_node.status = "done"
        else:
            full_response = t(K.LOG_TASK_FAILED, agent.lang)
            task_node.status = "failed"
    else:
        task_node.status = "done"

    task_node.result = full_response
    if task_node.status == "done":
        bad_patterns = ["angiv venligst", "hvilken fil", "hvilket filnavn", "which file", "what file",
                      "venligst angiv", "specificer fil", "give me the file", "jeg har brug for filen",
                      "send mig filen"]
        is_short = len(full_response) < 100
        asks_for_files = any(p in full_response.lower() for p in bad_patterns)
        if is_short or asks_for_files:
            agent._log("WARNING", "Mist\u00e6nkeligt kort resultat", f"{len(full_response)} tegn, asks_for_files={asks_for_files}")
            full_response = full_response + "\n\n\u26a0\ufe0f  ADVARSEL: Dette resultat ser ufuldst\u00e6ndigt ud. Overvej at k\u00f8re opgaven igen med en tydeligere prompt."
    agent.action_history.append(task_node.name.split()[0] if task_node.name else "unknown")
    agent._record_outcome(task_node)
    if task_node.status == "failed":
        agent._log("INFO", t(K.LOG_TASK_FAILED, agent.lang), task_node.name)
    else:
        agent._log("INFO", t(K.LOG_TASK_DONE, agent.lang), task_node.name)
    agent._evolve_if_needed()
    yield {"type": "done", "result": full_response}
