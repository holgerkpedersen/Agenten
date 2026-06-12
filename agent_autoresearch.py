"""Auto-research engine for Agenten.

When a task/phase fails, this module analyses the failure, checks for
duplicate issues, and creates a CORE-issue for the self-improvement
cycle.  Inspired by Karpathy's "agent auto-research" concept.

Design:
  - Runs async in a daemon thread — never blocks the SSE stream.
  - Classifies failure into one of five types via heuristics.
  - Delegates root-cause analysis to the LLM (autoresearch template).
  - Deduplicates against open issues (template + phase + failure type).
  - Creates a structured CORE-issue for the next self-improvement run.
  - Rate-limited: 1 analysis per 5 minutes per session.
"""

import json
import os
import re
import threading
import time
from typing import Any

from i18n import K
from lang import t

# ── Rate limiting ──────────────────────────────────────────────
_RATE_LIMIT_SEC = 300        # 5 minutes between analyses
_last_analysis: dict[str, float] = {}  # session_id → timestamp

# ── Failure types ──────────────────────────────────────────────
FAILURE_MISSING_TOOL      = "missing_tool"
FAILURE_TOOL_FAILED       = "tool_failed"
FAILURE_READ_LOOP         = "read_loop"
FAILURE_SHORT_OUTPUT      = "short_output"
FAILURE_PHASE_CHECK       = "phase_check"
FAILURE_UNKNOWN           = "unknown"


def classify_failure(task_node: Any, called_tools: dict,
                     tool_log: list, full_response: str,
                     agent: Any) -> tuple[str, dict]:
    """Classify the failure and return (type, evidence).

    Returns:
        Tuple of (failure_type_string, evidence_dict).
    """
    called_names = {k.split("{")[0] for k in (called_tools or {})}
    active = set(agent.tool_registry.active_tools or [])
    required_action = {"edit_file", "write_file", "update_issue_status"}

    # 1. MISSING_TOOL — required action tool never called
    needed = active & required_action
    uncalled = needed - called_names
    if uncalled:
        return FAILURE_MISSING_TOOL, {
            "required": list(needed),
            "called": list(called_names),
            "uncalled": list(uncalled),
        }

    # 2. READ_LOOP — 5+ consecutive reads, no writes
    #    (only checks when no required action tools are missing)
    if tool_log and not (active & required_action):
        recent = tool_log[-8:]
        read_tools = {"read_location", "read_chunk", "list_chunks",
                       "list_files", "list_symbols", "locate", "read_issue"}
        reads = sum(1 for e in recent if e.get("tool") in read_tools)
        writes = sum(1 for e in recent if e.get("tool") in
                       {"edit_file", "write_file", "update_issue_status"})
        if reads >= 5 and writes == 0:
            return FAILURE_READ_LOOP, {
                "consecutive_reads": reads, "total_recent": len(recent)}

    # 3. TOOL_FAILED — tool was called but ALL attempts failed
    if tool_log:
        for tool_name in needed:
            attempts = [e for e in tool_log if e.get("tool") == tool_name]
            if attempts and all(not e.get("success") for e in attempts):
                last_err = (attempts[-1].get("error", "") or
                            str(attempts[-1].get("args", {})))
                return FAILURE_TOOL_FAILED, {
                    "tool": tool_name,
                    "attempts": len(attempts),
                    "last_error": last_err[:200],
                    "last_args": str(attempts[-1].get("args", {}))[:200],
                }

    # 4. SHORT_OUTPUT — no tools called, short text response
    if not called_tools and full_response and len(full_response) < 100:
        return FAILURE_SHORT_OUTPUT, {
            "response_length": len(full_response),
            "response_preview": full_response[:100],
        }

    return FAILURE_UNKNOWN, {
        "called_tools": list(called_names) if called_tools else [],
        "response_length": len(full_response or ""),
    }


def _find_duplicate_issue(failure_type: str, template: str,
                           phase: str, evidence: dict,
                           issues: list[dict]) -> str | None:
    """Return issue_id if an open issue matches > 70 %.

    Match is based on:
      - template (weight 25 %)
      - phase (weight 25 %)
      - failure_type (weight 30 %)
      - title keywords (weight 20 %)
    """
    for issue in issues:
        if issue.get("status") not in ("open", "in_progress"):
            continue
        title = (issue.get("title", "") or "").lower()
        desc = (issue.get("description", "") or "").lower()
        combined = title + " " + desc

        score = 0.0

        # Template match (25 %)
        if template and template.lower() in combined:
            score += 0.25

        # Phase match (25 %)
        if phase and phase.lower() in combined:
            score += 0.25

        # Failure type match (30 %) — check both English and Danish labels
        type_label = failure_type.replace("_", " ")
        type_matched = type_label in combined
        if not type_matched:
            # Danish equivalents
            da_labels = {
                "missing_tool": ["manglende vaerktoej", "manglende v\u00e6rkt\u00f8j",
                                 "ikke kaldt"],
                "tool_failed": ["vaerktoej fejlede", "v\u00e6rkt\u00f8j fejlede",
                                "fejlede"],
                "read_loop": ["laese-loop", "l\u00e6se-loop", "laeser gentagne",
                              "l\u00e6ser gentagne"],
                "short_output": ["kort output", "for kort"],
                "unknown": ["uforklaret"],
            }
            da_matches = da_labels.get(failure_type, [])
            type_matched = any(dl in combined for dl in da_matches)
        if type_matched:
            score += 0.30

        # Keyword overlap (20 %)
        ev_text = ""
        for v in (evidence or {}).values():
            if isinstance(v, str):
                ev_text += " " + v
            elif isinstance(v, list):
                ev_text += " " + " ".join(str(x) for x in v)
        ev_keywords = {w for w in ev_text.lower().split()
                       if len(w) > 4}
        title_keywords = {w for w in title.split() if len(w) > 4}
        if ev_keywords and title_keywords:
            overlap = ev_keywords & title_keywords
            ratio = len(overlap) / max(len(ev_keywords), len(title_keywords))
            score += 0.20 * ratio

        if score >= 0.70:
            return issue.get("id")

    return None


def _rate_limit_ok(session_id: str) -> bool:
    """Check rate limit — max 1 analysis per 5 min per session."""
    now = time.time()
    last = _last_analysis.get(session_id, 0)
    if now - last < _RATE_LIMIT_SEC:
        return False
    _last_analysis[session_id] = now
    return True


def _build_failure_report(agent: Any, task_node: Any,
                           failure_type: str, evidence: dict) -> dict:
    """Build a structured failure report for issue creation."""
    template = getattr(agent, "active_template", "ukendt")
    phase = getattr(task_node, "name", "ukendt")

    if failure_type == FAILURE_MISSING_TOOL:
        title = (f"Manglende påkrævet værktøj i {template}/{phase}: "
                 f"{', '.join(evidence.get('uncalled', []))}")
        desc = (f"**Template:** {template}\n"
                f"**Fase:** {phase}\n"
                f"**Fejltype:** Manglende værktøjskald\n"
                f"**Påkrævede værktøjer:** {evidence.get('required')}\n"
                f"**Kaldte værktøjer:** {evidence.get('called')}\n"
                f"**Ikke kaldt:** {evidence.get('uncalled')}\n\n"
                f"LLM'en kaldte ikke de påkrævede værktøjer. "
                f"Overvej at opdatere sektionsinstruktionen "
                f"eller TEMPLATE_TASK_TOOLS.")
        proposed_fix = (f"Opdater SECTION_INSTRUCTIONS for "
                        f"{template}/{phase} så LLM'en guides "
                        f"til at kalde {evidence.get('uncalled')} "
                        f"på første iteration.")
    elif failure_type == FAILURE_TOOL_FAILED:
        title = (f"Værktøj fejlede i {template}/{phase}: "
                 f"{evidence.get('tool', '?')}")
        desc = (f"**Template:** {template}\n"
                f"**Fase:** {phase}\n"
                f"**Fejltype:** Værktøjskald fejlede\n"
                f"**Værktøj:** {evidence.get('tool')}\n"
                f"**Antal forsøg:** {evidence.get('attempts')}\n"
                f"**Sidste fejl:** {evidence.get('last_error')}\n"
                f"**Sidste args:** {evidence.get('last_args')}")
        proposed_fix = (f"Analysér hvorfor "
                        f"{evidence.get('tool')} fejler i "
                        f"{template}/{phase}. "
                        f"{evidence.get('last_error', '')}")
    elif failure_type == FAILURE_READ_LOOP:
        title = (f"Læse-loop i {template}/{phase}: "
                 f"{evidence.get('consecutive_reads', 0)} læsninger uden skrivning")
        desc = (f"**Template:** {template}\n"
                f"**Fase:** {phase}\n"
                f"**Fejltype:** Læse-loop\n"
                f"**Sekventielle læsninger:** {evidence.get('consecutive_reads')}\n"
                f"**Seneste værktøjer:** {evidence.get('total_recent')}")
        proposed_fix = (f"LLM'en læser gentagne gange uden at skrive. "
                        f"Overvej at tilføje write-påbud tidligere "
                        f"i sektionsinstruktionen.")
    elif failure_type == FAILURE_SHORT_OUTPUT:
        title = (f"Kort output i {template}/{phase}: "
                 f"{evidence.get('response_length', 0)} tegn")
        desc = (f"**Template:** {template}\n"
                f"**Fase:** {phase}\n"
                f"**Fejltype:** Kort output\n"
                f"**Længde:** {evidence.get('response_length')} tegn\n"
                f"**Output:** {evidence.get('response_preview')}")
        proposed_fix = (f"LLM'en afsluttede uden at producere "
                        f"tilstrækkeligt output eller kalde værktøjer. "
                        f"Overvej at øge min_text_length for fasen "
                        f"eller tydeliggøre instruktionen.")
    else:
        title = (f"Uforklaret fejl i {template}/{phase}")
        desc = (f"**Template:** {template}\n"
                f"**Fase:** {phase}\n"
                f"**Fejltype:** Uforklaret\n"
                f"**Kaldte værktøjer:** {evidence.get('called_tools')}\n"
                f"**Output længde:** {evidence.get('response_length')}")
        proposed_fix = "Kræver manuel analyse."

    return {
        "title": title[:120],
        "type": "bug",
        "severity": "medium",
        "description": desc[:2000],
        "location": f"agent_skills.py:{template}:{phase}",
        "impact": (f"Fasen {phase} i {template}-skabelonen "
                   f"fejler konsekvent."),
        "proposed_fix": proposed_fix[:500],
        "acceptance_criteria": (f"Fasen {phase} i {template} "
                                f"gennemføres uden denne fejl."),
    }


def trigger_if_needed(agent: Any, task_node: Any,
                       called_tools: dict,
                       full_response: str,
                       messages: list[dict] | None = None) -> None:
    """Entry point — called from _finalize_task_stream on failure.

    Rate-limited and async. Creates a CORE-issue if the failure
    is novel and actionable.
    """
    # Only trigger on actual failures
    if getattr(task_node, "status", "") != "failed":
        return

    session_id = getattr(agent, "_session_id", "unknown")
    if not _rate_limit_ok(session_id):
        return

    # Gather context
    tool_log = getattr(agent, "_tool_log", []) or []
    phase = getattr(task_node, "name", "ukendt")
    template = getattr(agent, "active_template", "ukendt")

    # Classify
    failure_type, evidence = classify_failure(
        task_node, called_tools, tool_log, full_response, agent)

    # Build report
    report = _build_failure_report(
        agent, task_node, failure_type, evidence)

    # Dedup against open issues
    try:
        from agent_issues import _load_issues
        data = _load_issues()
        issues = data.get("issues", [])
        dup_id = _find_duplicate_issue(
            failure_type, template, phase, evidence, issues)
        if dup_id:
            agent._log("AUTOR", f"Auto-research: dublet — {dup_id}",
                       f"{failure_type} i {template}/{phase}")
            return
    except Exception as exc:
        agent._log("AUTOR", f"Auto-research: dedup fejlede", str(exc))

    # Launch async analysis
    thread = threading.Thread(
        target=_async_create_issue,
        args=(agent, task_node, failure_type, evidence, report),
        daemon=True,
    )
    thread.start()


def _async_create_issue(agent: Any, task_node: Any,
                         failure_type: str, evidence: dict,
                         report: dict) -> None:
    """Async: create a CORE-issue for the failure."""
    try:
        from agent_issues import create_issue
        result = create_issue(
            agent,
            title=report["title"],
            type=report["type"],
            severity=report["severity"],
            description=report["description"],
            location=report["location"],
            impact=report["impact"],
            proposed_fix=report["proposed_fix"],
            acceptance_criteria=report["acceptance_criteria"],
        )
        if result.get("success"):
            issue_id = result.get("issue", {}).get("id", "?")
            existing = "(fandtes allerede)" if result.get("existing") else "(ny)"
            agent._log("AUTOR",
                       f"Auto-research issue {issue_id} {existing}",
                       report["title"][:120])
        else:
            agent._log("AUTOR",
                       "Auto-research: create_issue fejlede",
                       str(result.get("error", "")))
    except Exception as exc:
        agent._log("AUTOR", "Auto-research: exception", str(exc))
