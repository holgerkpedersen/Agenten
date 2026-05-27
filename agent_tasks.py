import os
import re
import time
import json
from i18n import K
from lang import t
import agent_skills
import agent_git
import agent_files
import config

EXECUTION_TIMEOUT = config.EXECUTION_TIMEOUT

PHASE_ALIASES = {
    "analyse": "analyse", "analysis": "analyse",
    "test": "test",
    "implementering": "implementering", "implementation": "implementering",
    "verifikation": "verifikation", "verification": "verifikation", "green": "verifikation",
    "opdatering": "opdatering", "update": "opdatering",
    "ekstraher": "ekstraher", "extract": "ekstraher",
    "plan": "plan",
    "opdatér": "opdatér",
    "læs": "læs", "read": "læs",
    "afklar": "afklar", "clarify": "afklar",
    "afklar & opdater": "afklar", "clarify & update": "afklar",
    "verificer": "afklar", "verify": "afklar",
    "fix": "fix",
    "luk": "luk", "close": "luk",
    "luk issue": "luk", "close issue": "luk",
}


def _normalize_phase(name):
    lower = name.lower().split("(")[0].strip()
    lower = re.sub(r'^[\d.]+[\)\s]*', '', lower).strip()
    return PHASE_ALIASES.get(lower, lower)


def set_task_tools(agent, task_name):
    if not agent.active_template or agent.active_template not in agent_skills.TEMPLATE_TASK_TOOLS:
        return
    template_tools = agent_skills.TEMPLATE_TASK_TOOLS[agent.active_template]
    phase = _normalize_phase(task_name)
    if phase in template_tools:
        agent.tool_registry.set_active_tools(template_tools[phase])
        agent._log("TOOL", f"Aktive tools for '{task_name[:40]}'", ', '.join(template_tools[phase]))
        return
    for keyword, tools in template_tools.items():
        if keyword in phase.lower():
            agent.tool_registry.set_active_tools(tools)
            agent._log("TOOL", f"Aktive tools for '{task_name[:40]}'", ', '.join(tools))
            return
    allowed = agent_skills.TEMPLATE_TOOLS.get(agent.active_template)
    if allowed is not None:
        agent.tool_registry.set_active_tools(allowed)


def solve_task(agent, task_node, original_prompt):
    agent._log("INFO", f"Starting task: {task_node.name}")
    full_response = ""
    for event in solve_task_stream(agent, task_node, original_prompt):
        if event["type"] == "done":
            full_response = event["result"]
    return full_response or "Task failed"


def _build_chunk_hint(agent):
    available_keys = list(agent.file_chunks.keys())
    is_chunked = any(len(v) > 1 for v in agent.file_chunks.values())
    hint = ""
    if is_chunked:
        parts = []
        for key in available_keys:
            total = len(agent.file_chunks[key])
            display = key.replace("file_", "", 1)
            parts.append(f"\n  read_chunk(file_key='{display}', index=2..{total}) eller file_key='{key}', index=2..{total}")
        hint = f"\n\n## TILG\u00c6NGELIGE FILER (brug read_chunk for at l\u00e6se alle chunks):{''.join(parts)}\n"
    delegation_lines = []
    for key, chunks in agent.file_chunks.items():
        content = chunks[0] if chunks else ''
        if not content:
            continue
        for func_name, target_mod in agent_files.detect_delegations(content):
            target_key = f'file_{target_mod}.py'
            if target_key in agent.file_chunks:
                delegation_lines.append(f'  - {key.replace("file_", "", 1)}:{func_name} \u2192 rediger i stedet {target_mod}.py:{func_name}')
            else:
                delegation_lines.append(f'  - {key.replace("file_", "", 1)}:{func_name} \u2192 {target_mod}.py (ikke indl\u00e6st)')
    if delegation_lines:
        hint += '\n\n## DELEGERINGER\nNogle funktioner i de indl\u00e6ste filer er stubs, der kun videresender til en anden fil.\n' + '\n'.join(delegation_lines) + '\n'
    return hint


def _build_initial_messages(agent, task_node, original_prompt, chunk_hint):
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
        user_guidance += t(K.TOOL_CONTINUATION, agent.lang).format(tools_list=tools_list, TOOL_MARKER=agent.tool_registry.TOOL_MARKER, DONE_MARKER=agent.tool_registry.DONE_MARKER)
    else:
        user_guidance += t(K.DONE_CONTINUATION, agent.lang).format(DONE_MARKER=agent.tool_registry.DONE_MARKER)
    if not chunk_hint and tools_list:
        read_only = all(t not in ('write_file',) for t in agent.tool_registry.active_tools or [])
        if read_only and not agent.images and not agent.file_chunks:
            user_guidance += f"\n\nOBS: Ingen filer er indl\u00e6st. Du KAN svare direkte med <<<DONE>>> uden at kalde v\u00e6rkt\u00f8jer f\u00f8rst. Sp\u00f8rg IKKE efter filnavne \u2014 brug din egen viden til at besvare opgaven."

    messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_guidance}]
    agent._log("LLM", "System prompt", f"{len(system_prompt)} chars \u2014 {system_prompt[:300]}...")
    agent._log("LLM", "User guidance", user_guidance)
    return messages, tools_list


def _msg_content_len(m):
    c = m.get("content", "")
    if isinstance(c, str):
        return len(c)
    if isinstance(c, list):
        return sum(p.get("text", "").__len__() if isinstance(p, dict) and p.get("type") == "text" else 0 for p in c)
    return 0


def _truncate_messages(messages, max_chars):
    total = sum(_msg_content_len(m) for m in messages)
    if total > max_chars and len(messages) > 3:
        mid = "\n[... tidligere kontekst afkortet ...]"
        keep = max_chars - _msg_content_len(messages[0]) - _msg_content_len(messages[1]) - len(mid)
        if keep > 0:
            tail_content = messages[-1]["content"]
            if isinstance(tail_content, str):
                cropped = tail_content[-keep:] if len(tail_content) > keep else tail_content
            else:
                cropped = "[...]"
            return messages[:2] + [{"role": "user", "content": mid + cropped}]
    return messages


def _cont_hint(agent, tools_list):
    return t(K.TOOL_CONTINUATION, agent.lang).format(tools_list=tools_list, TOOL_MARKER=agent.tool_registry.TOOL_MARKER, DONE_MARKER=agent.tool_registry.DONE_MARKER)


def _add_user_msg(messages, content):
    messages.append({"role": "user", "content": content})


def _handle_tool_call(agent, parsed, messages, called_tools, tools_list, task_node, original_prompt):
    tool_key = parsed['tool'] + str(parsed.get('args', {}))
    dup_count = called_tools.get(tool_key, 0)
    called_tools[tool_key] = dup_count + 1
    if dup_count >= 1:
        _add_user_msg(messages, f"{t(K.SYS_ERROR_PREFIX, agent.lang)}: Du har allerede dette resultat. G\u00e5 videre eller brug <<<DONE>>>.")
        return None

    agent._log("TOOL", t(K.LOG_TOOL_CALLING, agent.lang).format(tool=parsed['tool']), str(parsed.get("args", {})))
    result = agent.tool_registry.execute(parsed["tool"], parsed["args"])
    result_str = json.dumps(result, ensure_ascii=False)
    agent._log("TOOL", t(K.LOG_TOOL_RESULT, agent.lang).format(tool=parsed['tool']), result_str)

    checkpoint_msg = agent_git.verify_pr_step(agent, parsed["tool"], result, task_node.name, original_prompt)
    if checkpoint_msg:
        _add_user_msg(messages, f"!!! CHECKPOINT - {checkpoint_msg}")
        agent._log("INFO", "CHECKPOINT", checkpoint_msg)
        return {"type": "checkpoint", "tool": parsed["tool"], "args": parsed.get("args", {}), "result": result, "checkpoint_msg": checkpoint_msg}
    else:
        agent._checkpoint_tools.add(parsed["tool"] + str(parsed.get("args", {})))
        _add_user_msg(messages, f"{t(K.TOOL_RESULT_PREFIX, agent.lang).format(tool=parsed['tool'])}\n{result_str}\n\n{_cont_hint(agent, tools_list)}")
        return {"type": "tool_result", "tool": parsed["tool"], "args": parsed.get("args", {}), "result": result}


def _check_done_pr_requirements(agent, messages, called_tools, original_prompt, task_name):
    if not agent_git.is_pr_workflow(task_name):
        return True
    if not called_tools:
        _add_user_msg(messages, f"{t(K.SYS_ERROR_PREFIX, agent.lang)}: Du kaldte <<<DONE>>> uden at bruge nogen v\u00e6rkt\u00f8jer. Brug v\u00e6rkt\u00f8jerne f\u00f8rst.")
        return False
    called_names = {t.split("{")[0] for t in agent._checkpoint_tools}
    if "github_create_pr" not in called_names:
        _add_user_msg(messages, f"!!! CHECKPOINT - {t(K.CP_PR_FAILED, agent.lang)}")
        agent._log("INFO", "CHECKPOINT", t(K.CP_PR_FAILED, agent.lang))
        return False
    missing_commit = agent_git.PR_COMMIT_TOOLS - called_names
    if missing_commit:
        _add_user_msg(messages, f"!!! CHECKPOINT - {t(K.CP_NO_COMMIT, agent.lang)}")
        agent._log("INFO", "CHECKPOINT", t(K.CP_NO_COMMIT, agent.lang))
        return False
    if "git_push" not in called_names:
        _add_user_msg(messages, f"!!! CHECKPOINT - {t(K.CP_NO_PUSH, agent.lang)}")
        agent._log("INFO", "CHECKPOINT", t(K.CP_NO_PUSH, agent.lang))
        return False
    return True


def _finalize_task_stream(agent, task_node, full_response, text_fallback, called_tools):
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
                      "venligst angiv", "specificer fil", "give me the file", "jeg har brug for filen", "send mig filen"]
        is_short = len(full_response) < 100
        asks_for_files = any(p in full_response.lower() for p in bad_patterns)
        if (is_short or asks_for_files) and not called_tools:
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


def solve_task_stream(agent, task_node, original_prompt):
    task_node.status = "running"
    agent._task_start_time = time.time()
    agent.current_phase = _normalize_phase(task_node.name)
    agent._log("INFO", t(K.LOG_TASK_START, agent.lang), f"{task_node.name} (model: {agent.llm.model})")
    set_task_tools(agent, task_node.name)
    agent._checkpoint_tools = set()
    agent._checkpoint_branch = ""

    chunk_hint = _build_chunk_hint(agent)
    messages, tools_list = _build_initial_messages(agent, task_node, original_prompt, chunk_hint)

    full_response = ""
    text_fallback = ""
    max_iterations = config.MAX_PR_TASK_ITERATIONS if agent_git.is_pr_workflow(task_node.name) else config.MAX_TASK_ITERATIONS
    called_tools = {}
    _task_deadline = time.time() + EXECUTION_TIMEOUT

    for i in range(max_iterations):
        if agent.stop_requested:
            break

        if time.time() > _task_deadline:
            agent._log("WARNING", "Task timeout", f"Exceeded {EXECUTION_TIMEOUT//60}-min limit")
            yield {"type": "timeout", "message": f"Task exceeded {EXECUTION_TIMEOUT//60}-minute limit"}
            break

        if agent.pending_reply:
            messages.append({"role": "user", "content": agent.pending_reply})
            agent._log("USER", "Bruger svarer", agent.pending_reply[:100])
            agent.pending_reply = None

        response = ""
        try:
            for chunk in agent.llm.generate_stream(messages=messages, temperature=0.3, max_tokens=agent.max_tokens, images=agent.images):
                if agent.stop_requested:
                    break
                response += chunk
                yield {"type": "chunk", "chunk": chunk}
        except GeneratorExit:
            agent._log("INFO", "Client disconnected", "GeneratorExit")
            raise

        if agent.stop_requested:
            break

        if response.startswith("[ERROR:") or response.startswith("ERROR:"):
            yield {"type": "error", "message": response}
            break

        messages.append({"role": "assistant", "content": response})

        parsed = agent.tool_registry.parse_response(response)
        agent._log("LLM", t(K.LOG_ITERATION, agent.lang).format(n=i+1), t(K.LOG_TYPE, agent.lang).format(type=parsed.get('type')))
        agent._log("LLM", "LLM response (raw)", response)

        if parsed["type"] == "tool":
            tool_result = _handle_tool_call(agent, parsed, messages, called_tools, tools_list, task_node, original_prompt)
            if tool_result is None:
                messages = _truncate_messages(messages, agent.max_conversation_chars)
                continue
            yield {"type": "tool_call", "tool": tool_result["tool"], "args": tool_result["args"]}
            yield {"type": "tool_result", "tool": tool_result["tool"], "result": tool_result["result"]}
            if tool_result.get("checkpoint_msg"):
                yield {"type": "checkpoint", "message": tool_result["checkpoint_msg"], "tool": parsed["tool"]}
            messages = _truncate_messages(messages, agent.max_conversation_chars)
            total_calls = sum(called_tools.values())
            if total_calls >= config.MAX_TOOL_CALLS:
                full_response = t(K.LOG_AUTO_DONE, agent.lang).format(count=total_calls)
                break
            continue

        if parsed["type"] == "done":
            if not _check_done_pr_requirements(agent, messages, called_tools, original_prompt, task_node.name):
                messages = _truncate_messages(messages, agent.max_conversation_chars)
                if agent_git.is_pr_workflow(task_node.name):
                    yield {"type": "checkpoint", "message": t(K.CP_PR_FAILED, agent.lang), "tool": "done"}
                continue
            full_response = parsed["result"]
            done_idx = response.find(agent.tool_registry.DONE_MARKER)
            if done_idx > 0:
                pre_done = response[:done_idx].strip()
                if len(pre_done.strip()) > max(50, len(full_response) * 2):
                    full_response = pre_done
            break

        if parsed["type"] == "error":
            _add_user_msg(messages, f"{t(K.SYS_ERROR_PREFIX, agent.lang)}: {parsed['message']}")
            messages = _truncate_messages(messages, agent.max_conversation_chars)
            continue

        if i == 0 and not called_tools:
            all_files_loaded = all(len(v) <= 1 for v in agent.file_chunks.values()) if agent.file_chunks else True
            if all_files_loaded and parsed["type"] in ("text", "done"):
                text_fallback = response.strip() if parsed["type"] == "text" else parsed.get("result", response.strip())
                if text_fallback and "ERROR" not in text_fallback and not text_fallback.startswith("<<<") and len(text_fallback) > 100:
                    full_response = text_fallback
                    break
            if parsed["type"] == "text":
                tool_for_msg = agent.tool_registry.active_tools[0] if agent.tool_registry.active_tools else t(K.SYS_FALLBACK_TOOL, agent.lang)
                _add_user_msg(messages, f"{t(K.SYS_ERROR_PREFIX, agent.lang)}: {t(K.FIRST_TOOL_REQUIRED, agent.lang).format(tool=tool_for_msg)}")
                messages = _truncate_messages(messages, agent.max_conversation_chars)
                continue

        clean = response.strip() if "ERROR" not in response else ""
        if clean:
            text_fallback = clean
        _add_user_msg(messages, t(K.TOOL_NO_RESULT, agent.lang))
        messages = _truncate_messages(messages, agent.max_conversation_chars)
        full_response = response
        if i >= 3 and not called_tools:
            break

    yield from _finalize_task_stream(agent, task_node, full_response, text_fallback, called_tools)
