"""Auto-research engine for Agenten.

Inspired by Karpathy's autoresearch concept (github.com/karpathy/autoresearch).

When a task phase fails, this module runs a research loop:
  1. Analyze   — LLM reads the failure context, determines root cause
  2. Fix       — LLM edits the relevant source code (coding phase)
  3. Verify    — run_tests, keep (git commit) or discard (git checkout)
  4. Log       — record experiment outcome in research log
  5. Repeat    — loop until fix verified or max iterations reached

Design:
  - Runs async in a daemon thread — never blocks the SSE stream.
  - Sends progress events via a JSON event queue (polled by API).
  - Supports pause/resume by saving ResearchSession state to file.
  - Rate-limited: 1 research session per 5 minutes per session.
"""

import json
import os
import re
import threading
import time
import uuid
from typing import Any

from i18n import K
from lang import t

# ── Rate limiting ──────────────────────────────────────────────
_RATE_LIMIT_SEC = 300        # 5 minutes between sessions
_last_analysis: dict[str, float] = {}  # session_id → timestamp

# ── Failure types (kept for test compatibility) ────────────────
FAILURE_MISSING_TOOL      = "missing_tool"
FAILURE_TOOL_FAILED       = "tool_failed"
FAILURE_READ_LOOP         = "read_loop"
FAILURE_SHORT_OUTPUT      = "short_output"
FAILURE_PHASE_CHECK       = "phase_check"
FAILURE_UNKNOWN           = "unknown"

# ── Research log dir ───────────────────────────────────────────
_LOG_DIR = "logs/autoresearch"

# ── Event queue ────────────────────────────────────────────────
# Each session writes events to: logs/autoresearch/{session_id}/events.jsonl
# The API endpoint reads and returns new events since last poll.


def _event_path(session_id: str) -> str:
    return os.path.join(_LOG_DIR, session_id, "events.jsonl")


def _state_path(session_id: str) -> str:
    return os.path.join(_LOG_DIR, session_id, "state.json")


def _emit_event(session_id: str, event_type: str, data: dict) -> None:
    """Append a progress event to the event queue for this session."""
    dirpath = os.path.join(_LOG_DIR, session_id)
    os.makedirs(dirpath, exist_ok=True)
    entry = {
        "timestamp": time.time(),
        "type": event_type,
        **data,
    }
    try:
        with open(_event_path(session_id), "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def get_events(session_id: str, since: float = 0.0) -> list[dict]:
    """Return events for a session since the given timestamp."""
    path = _event_path(session_id)
    if not os.path.exists(path):
        return []
    events = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                    if ev.get("timestamp", 0) > since:
                        events.append(ev)
                except (json.JSONDecodeError, ValueError):
                    continue
    except OSError:
        pass
    return events


def get_active_sessions() -> list[dict]:
    """Return all active (not done/failed) research sessions."""
    if not os.path.isdir(_LOG_DIR):
        return []
    sessions = []
    for sid in os.listdir(_LOG_DIR):
        sp = _state_path(sid)
        if os.path.exists(sp):
            try:
                with open(sp, encoding="utf-8") as f:
                    state = json.load(f)
                if state.get("status") in ("running", "paused"):
                    sessions.append(state)
            except (OSError, json.JSONDecodeError):
                pass
    return sessions


def get_all_sessions(limit: int = 50) -> list[dict]:
    """Return all research sessions (newest first)."""
    if not os.path.isdir(_LOG_DIR):
        return []
    sessions = []
    for sid in sorted(os.listdir(_LOG_DIR), reverse=True):
        sp = _state_path(sid)
        if os.path.exists(sp):
            try:
                with open(sp, encoding="utf-8") as f:
                    state = json.load(f)
                sessions.append(state)
                if len(sessions) >= limit:
                    break
            except (OSError, json.JSONDecodeError):
                pass
    return sessions


def _paused_path(session_id: str) -> str:
    return os.path.join(_LOG_DIR, session_id, ".paused")


def pause_session(session_id: str) -> bool:
    """Pause a running research session."""
    sp = _state_path(session_id)
    if not os.path.exists(sp):
        return False
    try:
        with open(sp, encoding="utf-8") as f:
            state = json.load(f)
        state["status"] = "paused"
        state["paused_at"] = time.time()
        with open(sp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        # Signal the running thread to pause
        with open(_paused_path(session_id), "w") as f:
            f.write("1")
        _emit_event(session_id, "paused", {"reason": "User requested pause"})
        return True
    except (OSError, json.JSONDecodeError):
        return False


def resume_session(session_id: str) -> bool:
    """Resume a paused research session."""
    sp = _state_path(session_id)
    if not os.path.exists(sp):
        return False
    try:
        with open(sp, encoding="utf-8") as f:
            state = json.load(f)
        state["status"] = "running"
        state.pop("paused_at", None)
        with open(sp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        # Remove pause signal
        pp = _paused_path(session_id)
        if os.path.exists(pp):
            os.remove(pp)
        # Launch a new thread to continue
        _emit_event(session_id, "resumed", {"reason": "User requested resume"})
        return True
    except (OSError, json.JSONDecodeError):
        return False


# ── Classification (preserved from original, used by tests) ────

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
    """Return issue_id if an open issue matches > 70 %."""
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

        # Failure type match (30 %)
        type_label = failure_type.replace("_", " ")
        type_matched = type_label in combined
        if not type_matched:
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


def _check_filters(agent: Any, issue: dict | None = None,
                    template: str = "", phase: str = "",
                    failure_type: str = "") -> bool:
    """Check whether autoresearch should run based on agent filters.

    Agent can have:
      agent.autoresearch_enabled = True/False (master switch)
      agent.autoresearch_filters = {
          "types": ["bug", "security", ...],       # default: all
          "templates": ["issue_handler", ...],      # default: all
          "failure_types": ["missing_tool", ...],   # default: all
      }
    """
    if not getattr(agent, "autoresearch_enabled", False):
        return False

    filters = getattr(agent, "autoresearch_filters", {}) or {}
    if not isinstance(filters, dict):
        filters = {}

    # Filter by issue type
    allowed_types = filters.get("types", [])
    if allowed_types and issue:
        itype = issue.get("type", "")
        if itype and itype not in allowed_types:
            return False

    # Filter by template
    allowed_templates = filters.get("templates", [])
    if allowed_templates and template:
        if template not in allowed_templates:
            return False

    # Filter by failure type
    allowed_failures = filters.get("failure_types", [])
    if allowed_failures and failure_type:
        if failure_type not in allowed_failures:
            return False

    return True
    """Check rate limit — max 1 analysis per 5 min per session."""
    now = time.time()
    last = _last_analysis.get(session_id, 0)
    if now - last < _RATE_LIMIT_SEC:
        return False
    _last_analysis[session_id] = now
    return True


def _rate_limit_ok(session_id: str) -> bool:
    """Check rate limit — max 1 analysis per 5 min per session."""
    now = time.time()
    last = _last_analysis.get(session_id, 0)
    if now - last < _RATE_LIMIT_SEC:
        return False
    _last_analysis[session_id] = now
    return True


def trigger_if_needed(agent: Any, task_node: Any,
                       called_tools: dict,
                       full_response: str,
                       messages: list[dict] | None = None) -> None:
    """Called from _finalize_task_stream when a task fails.

    Checks autoresearch_enabled + filters, rate-limits, deduplicates,
    creates a CORE-issue, and starts an autonomous research loop.
    """
    if getattr(task_node, "status", "") != "failed":
        return

    session_id = getattr(agent, "_session_id", "unknown")

    # Gather context for filter check
    tool_log = getattr(agent, "_tool_log", []) or []
    phase = getattr(task_node, "name", "ukendt")
    template = getattr(agent, "active_template", "ukendt")
    failure_type, evidence = classify_failure(
        task_node, called_tools, tool_log, full_response, agent)

    # Apply filters
    if not _check_filters(agent, template=template, phase=phase,
                          failure_type=failure_type):
        return

    if not _rate_limit_ok(session_id):
        agent._log("AUTOR", "Auto-research: rate-limited", session_id)
        return

    # Dedup
    try:
        from agent_issues import _load_issues
        data = _load_issues()
        dup_id = _find_duplicate_issue(
            failure_type, template, phase, evidence, data.get("issues", []))
        if dup_id:
            agent._log("AUTOR", f"Auto-research: dublet — {dup_id}",
                       f"{failure_type} i {template}/{phase}")
            return
    except Exception as exc:
        agent._log("AUTOR", "Auto-research: dedup fejlede", str(exc))

    # Create a CORE-issue documenting the failure and start research
    issue_id = _create_issue(agent, failure_type, evidence, template, phase, "")
    if issue_id:
        start_research_for_issue(agent, issue_id)


# ── Research loop ──────────────────────────────────────────────

_MAX_RESEARCH_ITERATIONS = 5


def start_research_for_issue(agent: Any, issue_id: str) -> None:
    """Start an autonomous research loop for a specific issue.

    Reads the issue, creates a research session, and launches an
    async thread that analyzes, fixes, verifies, and iterates until
    the issue is resolved or max attempts reached.
    """
    from agent_issues import _load_issues
    try:
        data = _load_issues()
    except Exception:
        return

    issue = None
    for i in data.get("issues", []):
        if i.get("id", "").lower() == issue_id.lower():
            issue = i
            break
    if not issue:
        return

    # Apply filters
    if not _check_filters(agent, issue=issue):
        agent._log("AUTOR", f"Auto-research: filtreret fra — {issue_id}",
                   issue.get("type", ""))
        return

    session_id = getattr(agent, "_session_id", "unknown")
    research_id = str(uuid.uuid4())[:8]

    state = {
        "research_id": research_id,
        "session_id": session_id,
        "issue_id": issue_id,
        "issue_title": issue.get("title", ""),
        "issue_location": issue.get("location", ""),
        "issue_type": issue.get("type", "bug"),
        "status": "running",
        "iteration": 0,
        "max_iterations": _MAX_RESEARCH_ITERATIONS,
        "experiments": [],
        "started_at": time.time(),
    }

    dirpath = os.path.join(_LOG_DIR, research_id)
    os.makedirs(dirpath, exist_ok=True)
    _save_state(research_id, state)

    agent._log("AUTOR", f"Auto-research startet for {issue_id}: {research_id}",
               issue.get("title", "")[:100])

    thread = threading.Thread(
        target=_research_loop_for_issue,
        args=(agent, issue, research_id, state),
        daemon=True,
    )
    thread.start()


def _research_loop_for_issue(agent: Any, issue: dict,
                              research_id: str, state: dict) -> None:
    """Research loop that targets a specific issue."""
    issue_id = issue.get("id", "?")
    issue_title = issue.get("title", "")
    issue_location = issue.get("location", "")
    session_id = state["session_id"]

    _emit_event(research_id, "research_started", {
        "issue_id": issue_id,
        "title": issue_title[:100],
        "location": issue_location,
    })

    for iteration in range(1, _MAX_RESEARCH_ITERATIONS + 1):
        if os.path.exists(_paused_path(research_id)):
            _emit_event(research_id, "paused", {"iteration": iteration})
            state["status"] = "paused"
            state["iteration"] = iteration
            _save_state(research_id, state)
            return

        state["iteration"] = iteration
        _emit_event(research_id, "iteration_started", {"iteration": iteration})

        # Analyze
        _emit_event(research_id, "phase", {"phase": "analyze", "iteration": iteration})
        analysis = (
            f"Research iteration {iteration} for {issue_id}: {issue_title}\n"
            f"Location: {issue_location}\n"
        )
        _emit_event(research_id, "analysis_done", {"summary": analysis[:200]})

        # Fix (coding phase) — creates a CORE-issue as placeholder
        _emit_event(research_id, "phase", {"phase": "fix", "iteration": iteration})
        from agent_issues import create_issue
        result = create_issue(
            agent,
            title=f"Auto-fix: {issue_title[:80]}",
            type="self",
            severity="medium",
            description=analysis[:2000],
            location=issue_location,
            impact=f"Issue {issue_id} forsøges løst automatisk.",
            proposed_fix="Se auto-research log.",
        )
        fix_success = result.get("success", False)
        _emit_event(research_id, "fix_done", {
            "success": False,
            "changes": [result.get("issue", {}).get("id", "?")] if fix_success else [],
        })

        # Log experiment
        state["experiments"].append({
            "iteration": iteration,
            "analysis": analysis[:500],
            "fix_attempted": fix_success,
            "kept": False,
        })
        _save_state(research_id, state)

    _emit_event(research_id, "research_failed", {
        "reason": "Max iterations reached without fix",
        "issue_id": issue_id,
    })
    state["status"] = "failed"
    _save_state(research_id, state)

    # Create an issue documenting the failure
    from agent_issues import create_issue as ci
    ci(
        agent,
        title=f"Auto-research exhausted for {issue_id}",
        type="self",
        severity="medium",
        description=f"Auto-research kunne ikke løse {issue_id} efter {_MAX_RESEARCH_ITERATIONS} iterationer.",
        location=issue_location,
        impact="Kræver manuel gennemgang.",
        proposed_fix="Se auto-research log.",
    )


def _research_loop(agent: Any, task_node: Any,
                    failure_type: str, evidence: dict,
                    research_id: str, state: dict) -> None:
    """Main research loop: analyze → fix → verify → log → repeat."""
    session_id = state["session_id"]
    template = state["template"]
    phase = state["phase"]

    _emit_event(research_id, "research_started", {
        "session_id": session_id,
        "template": template,
        "phase": phase,
        "failure_type": failure_type,
    })

    for iteration in range(1, _MAX_RESEARCH_ITERATIONS + 1):
        # Check for pause signal
        if os.path.exists(_paused_path(research_id)):
            _emit_event(research_id, "paused", {"iteration": iteration})
            state["status"] = "paused"
            state["iteration"] = iteration
            _save_state(research_id, state)
            return  # Thread exits; resume_session will spawn a new one

        state["iteration"] = iteration
        _emit_event(research_id, "iteration_started", {"iteration": iteration})

        # --- Phase 1: Analyze ---
        _emit_event(research_id, "phase", {"phase": "analyze", "iteration": iteration})
        analysis = _analyze_failure(agent, failure_type, evidence, template, phase)
        _emit_event(research_id, "analysis_done", {"summary": analysis[:200]})

        # --- Phase 2: Fix (coding phase) ---
        _emit_event(research_id, "phase", {"phase": "fix", "iteration": iteration})
        fix_result = _attempt_fix(agent, analysis, template, phase)
        _emit_event(research_id, "fix_done", {
            "success": fix_result.get("success", False),
            "changes": fix_result.get("changes", []),
        })

        if not fix_result.get("success"):
            _emit_event(research_id, "fix_failed", {
                "error": fix_result.get("error", "Unknown"),
                "iteration": iteration,
            })
            continue

        # --- Phase 3: Verify ---
        _emit_event(research_id, "phase", {"phase": "verify", "iteration": iteration})
        verify_result = _verify_fix(agent)
        kept = verify_result.get("kept", False)
        _emit_event(research_id, "verify_done", {
            "kept": kept,
            "test_summary": verify_result.get("summary", ""),
            "iteration": iteration,
        })

        # --- Log experiment ---
        experiment = {
            "iteration": iteration,
            "analysis": analysis[:500],
            "changes": fix_result.get("changes", []),
            "tests_passed": verify_result.get("tests_passed", False),
            "kept": kept,
            "summary": verify_result.get("summary", ""),
        }
        state["experiments"].append(experiment)
        _save_state(research_id, state)

        if kept:
            _emit_event(research_id, "research_done", {
                "iteration": iteration,
                "experiments": len(state["experiments"]),
            })
            state["status"] = "done"
            _save_state(research_id, state)

            # Create a CORE-issue documenting the fix
            _create_issue(agent, failure_type, evidence, template, phase, analysis)
            return

        # Check pause again before next iteration
        if os.path.exists(_paused_path(research_id)):
            _emit_event(research_id, "paused", {"iteration": iteration})
            state["status"] = "paused"
            state["iteration"] = iteration
            _save_state(research_id, state)
            return

    # Max iterations reached without success
    _emit_event(research_id, "research_failed", {
        "reason": "Max iterations reached",
        "experiments": len(state["experiments"]),
    })
    state["status"] = "failed"
    _save_state(research_id, state)

    # Still create an issue documenting the problem
    _create_issue(agent, failure_type, evidence, template, phase,
                  "Auto-research exhausted without finding a fix.")


def _save_state(research_id: str, state: dict) -> None:
    """Save research session state to disk."""
    dirpath = os.path.join(_LOG_DIR, research_id)
    os.makedirs(dirpath, exist_ok=True)
    try:
        with open(_state_path(research_id), "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def _analyze_failure(agent: Any, failure_type: str,
                      evidence: dict, template: str,
                      phase: str) -> str:
    """Phase 1: Analyze the failure and determine root cause.

    In future: uses the LLM (autoresearch template) to read source code
    and produce a detailed analysis. For now: returns a structured summary.
    """
    lines = [
        f"Auto-research analyse af {failure_type} i {template}/{phase}",
    ]
    for k, v in evidence.items():
        if isinstance(v, str):
            lines.append(f"  {k}: {v[:200]}")
        elif isinstance(v, list):
            lines.append(f"  {k}: {', '.join(str(x) for x in v)[:200]}")
    return "\n".join(lines)


def _attempt_fix(agent: Any, analysis: str,
                  template: str, phase: str) -> dict:
    """Phase 2: Attempt to fix the issue (coding phase).

    In future: uses the LLM to read relevant source files and apply
    code changes via edit_file. For now: creates a CORE-issue.
    """
    # For now, just create an issue and return "not fixed"
    from agent_issues import create_issue
    title = f"Auto-research: {template}/{phase} — {analysis[:80]}"
    result = create_issue(
        agent,
        title=title[:120],
        type="self",
        severity="medium",
        description=analysis[:2000],
        location=f"agent_skills.py:selvforbedring:{template}/{phase}",
        impact=f"Fasen {phase} i {template} fejler.",
        proposed_fix="Kræver manuel gennemgang af auto-research log.",
    )
    return {
        "success": False,
        "error": "Auto-research coding phase not yet implemented — issue created instead.",
        "changes": [result.get("issue", {}).get("id", "?")] if result.get("success") else [],
    }


def _verify_fix(agent: Any) -> dict:
    """Phase 3: Verify the fix by running tests.

    Returns:
        dict with keys: kept (bool), tests_passed (bool), summary (str)
    """
    return {
        "kept": False,
        "tests_passed": False,
        "summary": "Verify phase not yet implemented.",
    }


def _create_issue(agent: Any, failure_type: str, evidence: dict,
                   template: str, phase: str, analysis: str) -> str | None:
    """Create a CORE-issue documenting the research result.
    
    Generates a specific, actionable issue based on failure type
    and evidence - not a generic template.
    
    Returns:
        The issue_id if created, or None on failure.
    """
    from agent_issues import create_issue

    title = _build_issue_title(failure_type, evidence, template, phase)
    desc = _build_issue_description(failure_type, evidence, template, phase, analysis)
    impact = _build_issue_impact(failure_type, evidence, template, phase)
    proposed_fix = _build_issue_fix(failure_type, evidence, template, phase)

    result = create_issue(
        agent,
        title=title[:120],
        type="self",
        severity="medium",
        description=desc[:2000],
        location=f"agent_skills.py:selvforbedring:{template}/{phase}",
        impact=impact[:300],
        proposed_fix=proposed_fix[:500],
    )
    if result.get("success"):
        issue_id = result.get("issue", {}).get("id", "?")
        existing = "(eksisterende)" if result.get("existing") else "(ny)"
        agent._log("AUTOR", f"Auto-research issue {issue_id} {existing}", title[:120])
        return issue_id
    else:
        agent._log("AUTOR", "Auto-research: create_issue fejlede",
                   str(result.get("error", "")))
        return None


def _build_issue_title(failure_type: str, evidence: dict,
                        template: str, phase: str) -> str:
    """Build a specific title based on failure context."""
    if failure_type == FAILURE_MISSING_TOOL:
        uncalled = evidence.get("uncalled", [])
        return (f"Manglende {', '.join(uncalled)} i {template}/{phase} "
                f"— LLM kaldte ikke påkrævet værktøj")
    elif failure_type == FAILURE_TOOL_FAILED:
        tool = evidence.get("tool", "?")
        return (f"Værktøj {tool} fejlede i {template}/{phase} "
                f"— alle {evidence.get('attempts', 0)} forsøg slog fejl")
    elif failure_type == FAILURE_READ_LOOP:
        return (f"Læse-loop i {template}/{phase} "
                f"— {evidence.get('consecutive_reads', 0)} reads uden write")
    elif failure_type == FAILURE_SHORT_OUTPUT:
        return (f"Kort output i {template}/{phase} "
                f"— {evidence.get('response_length', 0)} tegn, ingen tools")
    else:
        return f"Uforklaret fejl i {template}/{phase}"


def _build_issue_description(failure_type: str, evidence: dict,
                               template: str, phase: str,
                               analysis: str) -> str:
    """Build a detailed description with specific context."""
    lines = [f"## Auto-research analyse: {failure_type.replace('_', ' ')}"]
    lines.append(f"**Skabelon:** {template}  |  **Fase:** {phase}")
    lines.append("")

    if failure_type == FAILURE_MISSING_TOOL:
        lines.append("### Hvad skete der?")
        lines.append(f"LLM'en kaldte IKKE de påkrævede værktøjer: "
                     f"{', '.join(evidence.get('uncalled', []))}.")
        lines.append(f"Kaldte værktøjer: {', '.join(evidence.get('called', []))}.")
        lines.append(f"Aktive værktøjer i fasen: {', '.join(evidence.get('required', []))}.")
        lines.append("")
        lines.append("### Hvorfor er dette et problem?")
        lines.append("Fasen kan ikke fuldføres uden at det påkrævede værktøj kaldes. "
                     "Systemet afviser <<<DONE>>> når _check_required_tools fejler.")
        lines.append("")
        lines.append("### Mulige årsager")
        lines.append("- LLM'en forstår ikke instruktionen (sektionsinstruktionen er uklar)")
        lines.append("- Modellen nægter at kalde skriveværktøjer (kendt begrænsning)")
        lines.append("- Fasen mangler write-tools i TEMPLATE_TASK_TOOLS")
        lines.append("- Der mangler en alternativ sti (create_issue i stedet for edit_file)")

    elif failure_type == FAILURE_TOOL_FAILED:
        tool = evidence.get("tool", "?")
        lines.append("### Hvad skete der?")
        lines.append(f"Værktøjet {tool} blev kaldt {evidence.get('attempts', 0)} "
                     f"gange men fejlede hver gang.")
        lines.append(f"Sidste fejl: {evidence.get('last_error', 'ukendt')}")
        lines.append(f"Sidste args: {evidence.get('last_args', 'ukendt')}")
        lines.append("")
        lines.append("### Analyse")
        lines.append("Tool-fejl kan skyldes ugyldige argumenter, manglende "
                     "rettigheder, eller en bug i værktøjets implementering.")

    elif failure_type == FAILURE_READ_LOOP:
        lines.append("### Hvad skete der?")
        lines.append(f"LLM'en lavede {evidence.get('consecutive_reads', 0)} "
                     "læsekald i træk uden at skrive noget.")
        lines.append("")
        lines.append("### Analyse")
        lines.append("LLM'en mangler kontekst til at skrive. "
                     "Overvej at øge iteration budget eller give en tom skabelon.")
    else:
        lines.append("### Hvad skete der?")
        lines.append(f"Kaldte værktøjer: {evidence.get('called_tools', [])}")
        lines.append(f"Output længde: {evidence.get('response_length', 0)}")
        lines.append("")
        lines.append(analysis[:500])

    return "\n".join(lines)


def _build_issue_impact(failure_type: str, evidence: dict,
                         template: str, phase: str) -> str:
    """Build impact description."""
    if failure_type == FAILURE_MISSING_TOOL:
        uncalled = evidence.get("uncalled", [])
        return (f"Fasen {phase} i {template} kan ikke gennemføres fordi "
                f"LLM'en ikke kalder {', '.join(uncalled)}. "
                f"Dette blokerer hele selvforbedrings-cyklussen.")
    elif failure_type == FAILURE_TOOL_FAILED:
        return (f"Værktøjet {evidence.get('tool', '?')} fejler i "
                f"{template}/{phase}. Alle forsøg på at bruge det slog fejl.")
    elif failure_type == FAILURE_READ_LOOP:
        return (f"LLM'en læser uden at skrive i {template}/{phase}, "
                f"hvilket spilder iterationer og fører til timeout.")
    else:
        return f"Fasen {phase} i {template} fejler af uforklarede årsager."


def _build_issue_fix(failure_type: str, evidence: dict,
                      template: str, phase: str) -> str:
    """Build a specific, actionable fix proposal based on failure context."""
    if failure_type == FAILURE_MISSING_TOOL:
        uncalled = evidence.get("uncalled", [])
        suggestions = []

        if "edit_file" in uncalled:
            suggestions.append(
                "1. Tilføj `create_issue` som alternativ i "
                "`_check_required_tools()` for `selvforbedring/ret`: "
                "hvis `create_issue` er kaldt, krav ikke `edit_file`.\n"
                "   Se agent_tasks.py:1095-1099 — tilføj samme mønster "
                "for `selvforbedring` template."
            )
            suggestions.append(
                "2. Opdater `TEMPLATE_TASK_TOOLS[\"selvforbedring\"][\"ret\"]` "
                "i agent_skills.py: tilføj `create_issue`.\n"
                "   Så LLM'en har adgang til at oprette et issue som alternativ."
            )
            suggestions.append(
                "3. Opdater `SECTION_INSTRUCTIONS[\"selvforbedring\"][\"Ret\"]` "
                "i agent_skills.py: sig eksplicit at create_issue er acceptabelt "
                "når edit_file ikke virker."
            )

        if "write_file" in uncalled:
            suggestions.append(
                f"1. Tilføj `write_file` til "
                f"TEMPLATE_TASK_TOOLS[\"{template}\"][\"{phase}\"] "
                f"i agent_skills.py."
            )

        if "update_issue_status" in uncalled:
            suggestions.append(
                f"1. Tjek at {template}/{phase} er i CLOSE_PHASE_ALIASES "
                f"i agent_tasks.py:993."
            )

        if not suggestions:
            suggestions.append(
                f"1. Undersøg hvorfor {', '.join(uncalled)} ikke kaldes "
                f"i {template}/{phase}.\n"
                f"   Tjek sektionsinstruktionen og TEMPLATE_TASK_TOOLS."
            )

        suggestions.append(
            "\nRodårsag: _check_required_tools() i agent_tasks.py "
            "håndhæver at påkrævede værktøjer skal kaldes før <<<DONE>>>. "
            "Hvis LLM'en ikke kan/vil kalde dem, skal der være en "
            "alternativ sti (create_issue, auto-advance, eller model-skift)."
        )

        return "\n\n".join(suggestions)

    elif failure_type == FAILURE_TOOL_FAILED:
        tool = evidence.get("tool", "?")
        return (
            f"1. Undersøg hvorfor {tool} fejler i {template}/{phase}.\n"
            f"   Sidste args: {evidence.get('last_args', 'ukendt')}\n"
            f"   Fejl: {evidence.get('last_error', 'ukendt')}\n\n"
            f"2. Tjek om værktøjets implementering i git_ops.py eller "
            f"tools.py håndterer denne edge case.\n\n"
            f"3. Overvej at tilføje bedre fejlhåndtering i "
            f"_check_required_tools så enkelte fejl ikke blokerer fasen."
        )

    elif failure_type == FAILURE_READ_LOOP:
        return (
            f"1. Øg MAX_TOOL_CALLS_ANALYSE i config.py så LLM'en har "
            f"flere iterationer til at skrive.\n\n"
            f"2. Tilføj en tom skabelon i starten af sektionsinstruktionen "
            f"så LLM'en ved hvordan output skal se ud.\n\n"
            f"3. Overvej at injecte et system-reminder tidligere "
            f"(nuværende grænse er 5 consecutive reads)."
        )

    elif failure_type == FAILURE_SHORT_OUTPUT:
        fix_lines = [
            f"Tilføj en sektionsinstruktion for \"{phase}\" i {template}-templaten.\n"
            f"Fasen \"{phase}\" i \"{template}\" har ingen sektionsinstruktion, "
            f"så LLM'en ved ikke hvilke værktøjer der skal kaldes.\n"
        ]

        if template == "fri":
            try:
                from agent_files import locate_code
                loc = locate_code("agent_skills.py", "SECTION_INSTRUCTIONS")
                if loc.get("success"):
                    end_line = loc.get("end_line", 331)
                    fix_lines.append(
                        f"Åbn agent_skills.py omkring linje {end_line-3}-{end_line+5}. "
                        f"Tilføj en \"fri\"-nøgle efter \"selvforbedring\"-sektionen "
                        f"og før \"agenten\"-sektionen:\n\n"
                        f'    "fri": {{\n'
                        f'        "{phase}": "Kald værktøjer og producér '
                        f'mindst 200 tegn output.",\n'
                        f"    }},\n\n"
                        f"Brug edit_file(path='agent_skills.py', old_text='...', new_text='...') "
                        f"med search-and-replace. Indsæt efter linjen der slutter "
                        f"forrige sektion (før \"agenten\")."
                    )
            except Exception:
                fix_lines.append(
                    f"Tilføj en \"{phase}\"-sektion til SECTION_INSTRUCTIONS "
                    f"for \"{template}\"-templaten i agent_skills.py."
                )
        else:
            fix_lines.append(
                f"Tjek SECTION_INSTRUCTIONS i agent_skills.py — "
                f"{template}-templaten mangler en sektion for \"{phase}\". "
                f"Tilføj en instruktion der beder LLM'en om at kalde "
                f"relevante værktøjer og producere mindst 200 tegn."
            )

        fix_lines.append(
            "\nRodårsag: LLM'en afsluttede fasen uden at kalde værktøjer "
            "eller producere nok output. Sektionsinstruktionen mangler "
            "eller er for vag."
        )
        return "\n".join(fix_lines)

    else:
        return (
            f"Gennemgå agent_log og tool_log for {template}/{phase} "
            f"for at identificere rodårsagen."
        )
