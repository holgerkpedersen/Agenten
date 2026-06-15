"""Agent Agent-tools registration module."""
from __future__ import annotations

from typing import Any

import agent_issues
import agent_pdf
import agent_logs
from tools import Tool
from lang import t
from i18n import K
from web_searcher import WebSearcher

from agent_helpers import _safe_int, _run_doc_refinement


def register_agent_tools(agent: Any) -> None:
    """Register agent-related tools on the agent's tool registry.

    Args:
        agent: Agent instance with tool_registry attribute.
    """
    searcher = WebSearcher()

    agent.tool_registry.register(Tool(
        "run_tests",
        t(K.TOOL_RUN_TESTS, agent.lang),
        ["test_path"],
        lambda test_path="": agent_issues.run_pytest(test_path)
    ))

    agent.tool_registry.register(Tool(
        "run_refinement",
        t(K.TOOL_RUN_REFINEMENT, agent.lang),
        ["workdir"],
        lambda workdir, rounds=7, model="": _run_doc_refinement(workdir, rounds, model)
    ))

    agent.tool_registry.register(Tool(
        "convert_pdf_html5",
        t(K.TOOL_CONVERT_PDF, agent.lang),
        ["pdf_path"],
        lambda pdf_path, output_path="", lang=agent.lang: agent_pdf.convert_pdf_to_html5(pdf_path, output_path or None, lang)
    ))

    agent.tool_registry.register(Tool(
        "search_web",
        t(K.TOOL_SEARCH_WEB, agent.lang),
        ["query"],
        lambda query, num_results=3: searcher.search(query, int(num_results))
    ))

    agent.tool_registry.register(Tool(
        "read_issue",
        t(K.TOOL_READ_ISSUE, agent.lang),
        ["issue_id"],
        lambda issue_id, include_hints=False: agent_issues.read_issue(issue_id, include_hints)
    ))

    agent.tool_registry.register(Tool(
        "update_issue_status",
        t(K.TOOL_UPDATE_ISSUE_STATUS, agent.lang),
        ["issue_id", "status"],
        lambda issue_id, status="resolved", resolution_note="": agent_issues.update_issue_status(agent, issue_id, status, resolution_note)
    ))

    agent.tool_registry.register(Tool(
        "create_refactor_issue",
        t(K.TOOL_CREATE_REFACTOR_ISSUE, agent.lang),
        ["filepath", "line_count"],
        lambda filepath, line_count, related_issues="": agent_issues.create_refactor_issue(agent, filepath, int(line_count), (related_issues.split(",") if isinstance(related_issues, str) else related_issues) if related_issues else None)
    ))

    agent.tool_registry.register(Tool(
        "done",
        t(K.TOOL_DONE, agent.lang),
        ["result"],
        lambda result="": result,
        optional_params=["result"]
    ))

    agent.tool_registry.register(Tool(
        "create_issue",
        t(K.TOOL_CREATE_ISSUE, agent.lang),
        ["title", "type", "severity", "description", "location", "impact", "proposed_fix", "acceptance_criteria"],
        lambda title, type="bug", severity="medium", description="", location="", impact="", proposed_fix="", acceptance_criteria="": agent_issues.create_issue(agent, title=title, type=type, severity=severity, description=description, location=location, impact=impact, proposed_fix=proposed_fix, acceptance_criteria=acceptance_criteria)
    ))

    agent.tool_registry.register(Tool(
        "analyze_own_logs",
        t(K.TOOL_ANALYZE_OWN_LOGS, agent.lang),
        ["session_id", "pattern", "max_sessions"],
        lambda session_id="", pattern="", max_sessions="5": agent_logs.analyze_own_logs(session_id=session_id, pattern=pattern, max_sessions=int(max_sessions) if max_sessions else 5)
    ))
