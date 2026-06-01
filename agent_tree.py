"""Task tree operations and cleanup for Agent."""

import re
import time
from typing import Any
from task_tree import TaskTree, TaskNode
from lang import t
from i18n import K


def _clean_task_name(name: str) -> str | None:
    """clean task name.
    
    Args:
        name:"""
    name = re.sub(r'^[\*\-+]\s+', '', name.strip())
    name = re.sub(r'^\d+\.\s+', '', name)
    name = re.sub(r'<think>.*?</think>', '', name, flags=re.DOTALL)
    name = re.sub(r'<\|?channel\|?>.*$', '', name, flags=re.DOTALL)
    if re.match(r'^\*\*.*\*\*$', name):
        return None
    name = re.sub(r'\*\*', '', name)
    name = re.sub(r'`.*?`', '', name)
    name = re.sub(r'\s+', ' ', name).strip()
    if len(name) < 3 or name in ['', '-', '\u2022', '*']:
        return None
    meta_prefix = re.compile(
        r'^(?:'
        r'let\'s|let\s+me\s|i\s|my\s|we\s|'
        r'the\s+(?:user|instruction|example|prompt)\s|'
        r'okay|alright|'
        r'first[,:]?\s|second[,:]?\s|third[,:]?\s|'
        r'next[,:]?\s|finally[,:]?\s|lastly[,:]?\s|then\s|'
        r'now[,:]?\s|so[,:]?\s|but\s|however|'
        r'perhaps|maybe|probably\s|'
        r'that\'?s\s|that\s+is\s|'
        r'in\s+my\s|i\s+(?:recall|think|believe|found|remember|guess|wonder)\s|'
        r'i\s+(?:can|will|could|would|shall|want|need|should|have|might|must|am)\s|'
        r'recall\s+that|remember\s+that|'
        r'for\s+the\s+(?:output|tree|purpose|sake|moment)|'
        r'to\s+be\s+(?:safe|precise|thorough|consistent|fair|honest)|'
        r'another\s+(?:way|approach|option)|common\s+approach|looking\s+at|'
        r'final\s+(?:decision|check|output|answer|thought|tree|version|step|result)|'
        r'yes\.?\s*$|good\.?\s*$|right\.?\s*$|true\.?\s*$|'
        r'ready[\.!]?|'
        r'check|draft:|no\s+\w+|drop\s+|indentation:|structure:|'
        r'task to break down:|brainstorming|main task:|level\s+\d+|'
        r'here\'s a thinking process|udf\u00f8r opgave|analyze user input|deconstruct|'
        r'-\s*(?:task:|input task:|example provided:|language:|must be|output:)|'
        r'what does it mean|wait, the example|this is a bit linear|'
        r'nedbryd nu|kun tr[æe]|return[ée]r kun|now break down|'
        r'only tree|return only|ahora descomp|solo estructura|'
        r'devuelve solo|\u73b0\u5728\u5206\u89e3|\u4ec5\u6811\u7ed3\u6784|'
        r'thought$|namesearch$|namesekundar)', re.IGNORECASE
    )
    if re.match(r'^\w+\s*:\s*$', name.strip()):
        return None
    if meta_prefix.match(name):
        return None
    return name


def create_fallback_tree(agent: Any, prompt: str) -> TaskTree:
    """create fallback tree.
    
    Args:
        agent:
        prompt:"""
    tree = TaskTree(prompt)
    prompt_lower = prompt.lower()

    if "analyser" in prompt_lower and (".py" in prompt_lower or "api_server" in prompt_lower):
        tree.root.add_child(TaskNode(t(K.FT_UNDERSTAND_PURPOSE, agent.lang)))
        tree.root.children[0].add_child(TaskNode(t(K.FT_READ_IMPORTS, agent.lang)))
        tree.root.children[0].add_child(TaskNode(t(K.FT_IDENTIFY_FRAMEWORKS, agent.lang)))
        tree.root.add_child(TaskNode(t(K.FT_ANALYZE_STRUCTURE, agent.lang)))
        tree.root.children[1].add_child(TaskNode(t(K.FT_REVIEW_ENDPOINTS, agent.lang)))
        tree.root.children[1].add_child(TaskNode(t(K.FT_CHECK_CONFIG, agent.lang)))
        tree.root.add_child(TaskNode(t(K.FT_ASSESS_QUALITY, agent.lang)))
        tree.root.children[2].add_child(TaskNode(t(K.FT_SECURITY_ANALYSIS, agent.lang)))
        tree.root.children[2].add_child(TaskNode(t(K.FT_ERROR_HANDLING, agent.lang)))
        tree.root.add_child(TaskNode(t(K.FT_DOCUMENT_FINDINGS, agent.lang)))
    elif "2 + 2" in prompt_lower or "2 plus 2" in prompt_lower:
        tree.root.add_child(TaskNode(t(K.FT_UNDERSTAND_NUMBERS, agent.lang)))
        tree.root.add_child(TaskNode(t(K.FT_PERFORM_ADDITION, agent.lang)))
        tree.root.add_child(TaskNode(t(K.FT_CONCLUDE, agent.lang)))
    else:
        tree.root.add_child(TaskNode(t(K.FT_ANALYZE_PROBLEM, agent.lang)))
        tree.root.add_child(TaskNode(t(K.FT_FIND_STRATEGY, agent.lang)))
        tree.root.add_child(TaskNode(t(K.FT_IMPLEMENT_SOLUTION, agent.lang)))
        tree.root.add_child(TaskNode(t(K.FT_TEST_VALIDATE, agent.lang)))
    return tree


def parse_tree_from_llm(agent: Any, prompt: str, llm_response: str) -> TaskTree:
    """parse tree from llm.
    
    Args:
        agent:
        prompt:
        llm_response:"""
    tree = TaskTree(prompt)
    if llm_response.startswith("ERROR") or not llm_response.strip():
        agent._log("ERROR", t(K.LOG_LLM_ERROR_FALLBACK, agent.lang), llm_response[:100] if llm_response else t(K.LOG_EMPTY_RESPONSE, agent.lang))
        return create_fallback_tree(agent, prompt)

    lines = llm_response.strip().split('\n')
    stack = [(tree.root, 0)]
    added_count = 0
    skip_words = ['think', 'thinking', 'brainstorm', 'draft', 'constraint',
                 'repetition', 'politeness', 'indentation', 'compliance',
                 'thought', 'channel', 'namesekundar', 'namesearch',
                 'analyze user input', 'deconstruct', 'example provided',
                 'must be a tree', 'output:', 'language:',
                 'let me', "let's", "i'll", "i'm", "i've", "i'd"]

    criteria_re = re.compile(r'\s*\(([^)]+)\)\s*$')

    for line in lines:
        if not line.strip():
            continue
        stripped = line.lstrip(' ')
        indent = len(line) - len(stripped)
        task_name = _clean_task_name(stripped)
        if task_name is None:
            continue
        skip_match = False
        task_lower = task_name.lower()
        for sw in skip_words:
            if sw in task_lower:
                skip_match = True
                break
        if skip_match:
            continue
        if not task_name or len(task_name) < 3:
            continue

        # Extract success criteria from parentheses at end of task name
        criteria = []
        crit_match = criteria_re.search(task_name)
        if crit_match:
            raw = crit_match.group(1)
            criteria = [c.strip() for c in raw.split(",") if c.strip()]
            task_name = task_name[:crit_match.start()].strip()

        level = indent // 2
        while len(stack) > level + 1:
            stack.pop()
        parent = stack[-1][0]
        new_node = TaskNode(task_name)
        new_node.success_criteria = criteria
        parent.add_child(new_node)
        stack.append((new_node, level))
        added_count += 1

    if added_count == 0:
        agent._log("WARNING", t(K.LOG_NO_VALID_TASKS, agent.lang), "")
        return create_fallback_tree(agent, prompt)

    # If >20 level-1 tasks, LLM likely leaked thinking → use fallback
    flat_tasks = len(tree.root.children)
    if flat_tasks > 20 and all(len(c.children) == 0 for c in tree.root.children):
        agent._log("WARNING", f"LLM output indeholder tankeproces ({flat_tasks} flade opgaver). Bruger fallback.", "")
        return create_fallback_tree(agent, prompt)

    agent._log("INFO", t(K.LOG_PARSED_TASKS, agent.lang).format(n=added_count), "")
    return tree


def count_tasks(node: TaskNode) -> int:
    """count tasks.
    
    Args:
        node:"""
    count = 1
    for child in node.children:
        count += count_tasks(child)
    return count


def task_tree_to_dict(agent: Any) -> dict | None:
    """task tree to dict.
    
    Args:
        agent:"""
    if not agent.task_tree or not agent.task_tree.root:
        return None

    def node_to_dict(node):
        """node to dict.
        
        Args:
            node:"""
        return {
            "name": node.name,
            "status": node.status,
            "result": node.result,
            "success_criteria": node.success_criteria,
            "children": [node_to_dict(child) for child in node.children] if node.children else []
        }

    return node_to_dict(agent.task_tree.root)


def task_tree_from_dict(agent: Any, d: dict) -> None:
    """task tree from dict.
    
    Args:
        agent:
        d:"""
    def dict_to_node(item):
        """dict to node.
        
        Args:
            item:"""
        node = TaskNode(item["name"])
        node.status = item.get("status", "pending")
        node.result = item.get("result")
        node.success_criteria = item.get("success_criteria", [])
        for child_data in item.get("children", []):
            node.add_child(dict_to_node(child_data))
        return node
    agent.task_tree = TaskTree("temp")
    agent.task_tree.root = dict_to_node(d)


def record_outcome(agent: Any, task_node: TaskNode) -> None:
    """record outcome.
    
    Args:
        agent:
        task_node:"""
    try:
        from skill_tracker import tracker
        skill_name = "__none__"
        for s in agent._active_skills:
            if not s.get("base"):
                skill_name = s["name"]
                break
        ts = getattr(agent, '_task_start_time', 0)
        duration = int((time.time() - ts) * 1000) if ts else 0
        tracker.record(
            skill_name=skill_name,
            task_summary=task_node.name,
            success=task_node.status == "done",
            duration_ms=duration,
            template=agent.active_template or "",
        )
    except Exception as e:
        pass


def evolve_if_needed(agent: Any) -> bool:
    """evolve if needed.
    
    Args:
        agent:"""
    try:
        from skill_evolution import evolve_if_needed as _evo
        result = _evo(dry_run=False)
        return result.get("status") == "evolved"
    except Exception as e:
        try:
            import logging
            logging.error(f"Skill evolution failed: {e}")
        except Exception:
            pass
        return False
