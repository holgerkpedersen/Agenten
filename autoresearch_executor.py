from typing import Any, Generator
import agent_phase_checks
import agent_issues



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
