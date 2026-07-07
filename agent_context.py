"""Agent file context building."""
from __future__ import annotations

import os
import re
from typing import Any

import agent_files
import agent_issues
import config
from task_tree import TaskTree, TaskNode
from lang import t
from i18n import K


def _add_file_entry(file_context: str, agent: Any, filename: str, content: str) -> str:
    """Add file entry to context."""
    chunk_key = f"file_{filename}"
    chunks = agent_files.chunk_text(content)
    agent.file_chunks[chunk_key] = chunks
    agent_issues.detect_oversize_file(agent, filename, content)
    if agent._pending_refactor:
        # Skip auto-creation af REFAC issues når sessionen selv er en
        # refactor for den pågældende fil — issue er redundant; sessionen
        # ER fixet. Sæt flag så decompose() kan justere oversize-noten.
        prompt = (getattr(agent, 'original_prompt', '') or '').lower()
        template = (getattr(agent, 'active_template', '') or '').lower()
        is_self_refactor = (
            template == 'refactor'
            and filename.lower().replace('\\', '/').split('/')[-1] in prompt
        )
        if is_self_refactor:
            agent._self_refactor_file = True
        else:
            agent_issues.create_refactor_issue(agent, filename, agent._pending_refactor["lines"])
        agent._pending_refactor = None
    is_python = filename.endswith('.py')
    if is_python:
        ast_index = agent_files.build_ast_index(content, filename)
        if ast_index:
            file_context += f"\n{ast_index}\n"
    if len(chunks) > 1:
        file_context += f"\n*Filen er stor ({len(chunks)} chunks) — brug read_chunk(file_key='{chunk_key}', index=1..{len(chunks)}) for at læse indhold.*\n"
        file_context += f"\n*Brug locate(filepath='{filename}', name='funktionsnavn') for at læse en bestemt funktion/metode.*\n"
    display_content = content[:config.MAX_FILE_CONTEXT_CHARS]
    truncated_note = "\n[... indhold afkortet — brug read_chunk() for at læse hele filen ...]" if len(content) > config.MAX_FILE_CONTEXT_CHARS else ""
    if len(chunks) <= 1:
        file_context += f"\n### {filename}\n\n```{filename}\n{display_content}{truncated_note}\n```\n"
    else:
        file_context += f"\n### {filename} (chunk 1/{len(chunks)}, ~{agent_files.CHUNK_SIZE}tgn/chunk)\n\n```{filename}\n{display_content}{truncated_note}\n```\n"
        file_context += f"\n*Filen er stor — indlæs flere chunks med read_chunk(file_key='{chunk_key}', index=2..{len(chunks)})*\n"
    return file_context


def _build_fallback_tree(agent: Any, prompt: str, fallback_sections: list[str]) -> None:
    """Build a structured fallback task tree from a template's section list."""
    tree = TaskTree(prompt)
    _criteria_re = re.compile(r'^(.+?)\s*\(([^)]+)\)\s*$')
    for section in fallback_sections:
        section_str = str(section)
        m = _criteria_re.match(section_str)
        if m:
            name = m.group(1).strip()
            criteria = [c.strip() for c in m.group(2).split(",")]
        else:
            name = section_str
            criteria = []
        node = TaskNode(name)
        node.success_criteria = criteria
        tree.root.add_child(node)
    agent.task_tree = tree



def _build_file_context(agent: Agent, files: list[dict[str, Any]] | None, prompt: str) -> str:
    """build file context.
    
    Args:
        agent:
        files:
        prompt:
    
    Returns:
        str"""
    file_context = ""
    if files and len(files) > 0:
        file_context = t(K.FILE_CONTEXT_HEADER, agent.lang)
        for f in files:
            file_context = _add_file_entry(file_context, agent, f.get('filename', t(K.UNKNOWN, agent.lang)), f.get('content', ''))
        agent._log("INFO", t(K.LOG_ADDING_FILES, agent.lang), t(K.LOG_N_FILES, agent.lang).format(n=len(files)))
    else:
        scanned_files = agent._get_folder_context(prompt)
        if scanned_files:
            file_context = t(K.FILE_CONTEXT_HEADER, agent.lang)
            agent.file_context = scanned_files
            for item in scanned_files:
                file_context = _add_file_entry(file_context, agent, item['filename'], item['content'])
            agent._log("INFO", t(K.LOG_ADDING_FILES, agent.lang), t(K.LOG_N_FILES, agent.lang).format(n=len(scanned_files)))
        else:
            file_path, file_content = agent._get_single_file_context(prompt)
            if file_content:
                filename = os.path.basename(file_path)
                file_context = _add_file_entry("", agent, filename, file_content)
                file_context = t(K.FILE_CONTEXT_HEADER, agent.lang) + file_context.lstrip()
    return file_context
