"""Agent decomposition - LLM-based prompt decomposition and task tree generation."""
from __future__ import annotations

from typing import Any

import config
from tools import _strip_llm_tags


def _decompose_via_llm(agent: Any, prompt: str, file_context: str, template_config: dict[str, Any]) -> dict[str, Any]:
    """Decompose via LLM.
    
    Args:
        agent: Agent instance.
        prompt: User prompt string.
        file_context: File context string.
        template_config: Template configuration dict.
        
    Returns:
        Task tree as dict.
    """
    from lang import t
    from i18n import K
    from agent_context import _build_fallback_tree
    
    decomposition_prompt = template_config["prompt"].replace("{prompt}", agent._sanitize_prompt(prompt))
    file_context_entry = f"\n\nMateriale:{file_context}" if file_context else ""
    decomposition_prompt += file_context_entry
    
    model_lower = agent.decompose_llm.model.lower()
    for model_prefix, channel_tag in config.CHANNEL_TAG_MODELS.items():
        if model_prefix in model_lower:
            decomposition_prompt += channel_tag
            break
    
    agent._log("LLM", t(K.LOG_SENDING_LLM, agent.lang),
               t(K.LOG_N_FILES, agent.lang).format(n=len(agent.file_context))
               if isinstance(agent.file_context, list) and agent.file_context else "")
    
    response = agent.decompose_llm.generate(decomposition_prompt, temperature=0.3, max_tokens=4096)
    agent._log("LLM", t(K.LOG_RECEIVED_LLM, agent.lang), t(K.LOG_N_CHARS, agent.lang).format(n=len(response)))
    
    response = _strip_llm_tags(response)
    agent.task_tree = agent._parse_tree_from_llm(prompt, response)
    
    task_count = agent._count_tasks(agent.task_tree.root)
    agent._log("INFO", t(K.LOG_DECOMPOSE_DONE, agent.lang), t(K.LOG_TASKS_CREATED, agent.lang).format(n=task_count))
    
    if task_count <= 1 and template_config.get("fallback"):
        agent._log("INFO", "Kun én opgave — bruger skabelonens faldback", "")
        _build_fallback_tree(agent, prompt, template_config["fallback"])
        task_count = agent._count_tasks(agent.task_tree.root)
    
    if task_count >= 2 and template_config.get("name") in (None, "", "fri"):
        # Check whether the LLM actually included success criteria.
        has_criteria = any(
            bool(getattr(c, 'success_criteria', None))
            for c in agent.task_tree.root.children
        )
        if not has_criteria and template_config.get("fallback"):
            agent._log("INFO", "Ingen succeskriterier i træet — bruger skabelonens strukturerede faldback", "")
            _build_fallback_tree(agent, prompt, template_config["fallback"])
            task_count = agent._count_tasks(agent.task_tree.root)
    
    agent._log("INFO", t(K.LOG_DECOMPOSE_DONE, agent.lang), t(K.LOG_TASKS_CREATED, agent.lang).format(n=task_count))
    return agent.task_tree_to_dict()
