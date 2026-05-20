import re
import time
from task_tree import TaskTree, TaskNode
from lang import t
from i18n import K


def _clean_task_name(name):
    name = re.sub(r'^[\*\-+]\s+', '', name.strip())
    name = re.sub(r'^\d+\.\s+', '', name)
    patterns = [
        r'<think>.*?</think>',
        r'Here\'s a thinking process:.*$',
        r'^\*\*.*\*\*$',
        r'^[•\-]\s*$',
        r'^Let\'s.*$',
        r'^Check.*$',
        r'^Draft:.*$',
        r'^No repetition.*$',
        r'^Drop politeness.*$',
        r'^Indentation:.*$',
        r'^Structure:.*$',
        r'^Task to break down:.*$',
        r'^Brainstorming.*$',
        r'^Main task:.*$',
        r'^Level \d+ steps.*$',
        r'^Let\'s break each down.*$',
        r'^Let\'s ensure.*$',
        r'^Udf\u00f8r opgave.*$',
        r'^Analyze User Input:.*$',
        r'^Deconstruct Constraints.*$',
        r'^-\s*Task:.*$',
        r'^-\s*Input Task:.*$',
        r'^-\s*Example Provided:.*$',
        r'^-\s*Language:.*$',
        r'^-\s*Must be a tree structure.*$',
        r'^-\s*Output:.*$',
        r'^What does it mean to analyze.*$',
        r'^Wait, the example shows.*$',
        r'^This is a bit linear.*$',
        r'^Final check of the prompt.*$',
        r'^I will output exactly.*$',
        r'^Ready. Output matches exactly.*$',
        r'^\u2705$',
        r'^Nedbryd nu opgaven.*$',
        r'^KUN tr\u00e6struktur.*$',
        r'^Return\u00e9r KUN tr\u00e6struktur.*$',
        r'^Returner KUN tr\u00e6struktur.*$',
        r'^Now break down the task.*$',
        r'^ONLY tree structure.*$',
        r'^Return ONLY the tree structure.*$',
        r'^Ahora descomp\u00f3n la tarea.*$',
        r'^SOLO estructura de \u00e1rbol.*$',
        r'^Devuelve SOLO la estructura.*$',
        r'^\u73b0\u5728\u5206\u89e3\u4efb\u52a1.*$',
        r'^\u4ec5\u6811\u7ed3\u6784.*$',
        r'^<\|?channel\|?>.*$',
        r'^thought$',
        r'^namesearch$',
        r'^namesekundar.*$',
    ]
    for pattern in patterns:
        name = re.sub(pattern, '', name, flags=re.IGNORECASE | re.DOTALL)
    name = re.sub(r'\*\*', '', name)
    name = re.sub(r'`.*?`', '', name)
    name = re.sub(r'\s+', ' ', name).strip()
    if len(name) < 3 or name in ['', '-', '\u2022', '*']:
        return None
    return name


def create_fallback_tree(agent, prompt):
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


def parse_tree_from_llm(agent, prompt, llm_response):
    tree = TaskTree(prompt)
    if llm_response.startswith("ERROR") or not llm_response.strip():
        agent._log("ERROR", t(K.LOG_LLM_ERROR_FALLBACK, agent.lang), llm_response[:100] if llm_response else t(K.LOG_EMPTY_RESPONSE, agent.lang))
        return create_fallback_tree(agent, prompt)

    lines = llm_response.strip().split('\n')
    stack = [(tree.root, 0)]
    added_count = 0

    for line in lines:
        if not line.strip():
            continue
        stripped = line.lstrip(' ')
        indent = len(line) - len(stripped)
        task_name = _clean_task_name(stripped)
        if task_name is None:
            continue
        skip_words = ['think', 'thinking', 'brainstorm', 'draft', 'constraint',
                     'repetition', 'politeness', 'indentation', 'compliance',
                     'thought', 'channel', 'namesekundar', 'namesearch',
                     'analyze user input', 'deconstruct', 'example provided',
                     'must be a tree', 'output:', 'language:']
        if any(word in task_name.lower() for word in skip_words):
            continue
        if not task_name or len(task_name) < 3:
            continue
        level = indent // 2
        while len(stack) > level + 1:
            stack.pop()
        parent = stack[-1][0]
        new_node = TaskNode(task_name[:80])
        parent.add_child(new_node)
        stack.append((new_node, level))
        added_count += 1

    if added_count == 0:
        agent._log("WARNING", t(K.LOG_NO_VALID_TASKS, agent.lang), "")
        return create_fallback_tree(agent, prompt)

    agent._log("INFO", t(K.LOG_PARSED_TASKS, agent.lang).format(n=added_count), "")
    return tree


def count_tasks(node):
    count = 1
    for child in node.children:
        count += count_tasks(child)
    return count


def task_tree_to_dict(agent):
    if not agent.task_tree or not agent.task_tree.root:
        return None

    def node_to_dict(node):
        return {
            "name": node.name,
            "status": node.status,
            "children": [node_to_dict(child) for child in node.children] if node.children else []
        }

    return node_to_dict(agent.task_tree.root)


def task_tree_from_dict(agent, d):
    def dict_to_node(item):
        node = TaskNode(item["name"])
        node.status = item.get("status", "pending")
        for child_data in item.get("children", []):
            node.add_child(dict_to_node(child_data))
        return node
    agent.task_tree = TaskTree("temp")
    agent.task_tree.root = dict_to_node(d)


def record_outcome(agent, task_node):
    try:
        from skill_tracker import tracker
        skill_name = "__none__"
        for s in agent._active_skills:
            if not s.get("base"):
                skill_name = s["name"]
                break
        duration = int((time.time() - agent._task_start_time) * 1000) if agent._task_start_time else 0
        tracker.record(
            skill_name=skill_name,
            task_summary=task_node.name,
            success=task_node.status == "done",
            duration_ms=duration,
            template=agent.active_template or "",
        )
    except ImportError:
        pass


def evolve_if_needed(agent):
    try:
        from skill_evolution import evolve_if_needed as _evolve
        result = _evolve(dry_run=True)
        if result.get("status") == "evolved":
            agent._log("SKILLFLOW", "Evolution triggered", f"{len(result.get('analysis', {}).get('actions', []))} actions")
        elif result.get("status") == "ok":
            agent._log("SKILLFLOW", "Analysis ready", f"{len(result.get('actions', []))} actions available")
    except ImportError:
        pass
