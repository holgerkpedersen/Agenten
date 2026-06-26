"""Auto-research engine for Agenten.

Design:
  - trigger_if_needed() is called from _finalize_task_stream when a phase fails.
  - It classifies the failure, deduplicates, rate-limits, and creates a CORE-issue.
  - The caller (agent_tasks._finalize_task_stream) then executes the CORE-issue
    inline via _execute_autoresearch_issue() as a selvforbedring sub-session.
  - The sub-session runs in the same SSE stream — user sees Analyser→Diagnosticér
    →Ret→Verificér→Commit live in the UI.
  - Depth-limited (max 2 nested sessions) to prevent infinite recursion.
"""

import json
import os
import re
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
FAILURE_INCOMPLETE        = "incomplete"
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

    # 5. INCOMPLETE — budget exhausted before all planned work completed
    _phase_name = getattr(task_node, "name", "") or ""
    _active_tmpl = getattr(agent, "active_template", "") or ""
    _phase_v = _phase_name.lower()
    if _active_tmpl == "refactor" and _phase_v in ("ekstraher", "opdatér"):
        try:
            import os as _os
            _wd = _os.environ.get('AGENT_WORKDIR', '') or _os.getcwd()
            _plan_path = _os.path.join(_wd, "refactor_plan.md")
            if _os.path.exists(_plan_path):
                from file_checks import _parse_refactor_plan_modules
                mods = _parse_refactor_plan_modules(_plan_path)
                if mods:
                    created = [m for m in mods if _os.path.exists(m)]
                    if len(created) < len(mods):
                        return FAILURE_INCOMPLETE, {
                            "modules_planned": len(mods),
                            "modules_created": len(created),
                            "missing_modules": sorted(set(mods) - set(created)),
                            "all_modules": mods,
                        }
        except Exception:
            pass

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
                       messages: list[dict] | None = None) -> str | None:
    """Called from _finalize_task_stream when a task fails.

    Checks autoresearch_enabled + filters, rate-limits, deduplicates,
    creates a CORE-issue, and returns the issue_id so the caller can
    start an inline sub-session (instead of a background thread).
    """
    if getattr(task_node, "status", "") != "failed":
        return None

    # Belt-and-suspenders: refuse if already inside an auto-research sub-session
    _d = getattr(agent, '_autoresearch_depth', 0)
    if isinstance(_d, int) and _d > 0:
        return None

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
        return None

    if not _rate_limit_ok(session_id):
        agent._log("AUTOR", "Auto-research: rate-limited", session_id)
        return None

    # Dedup
    try:
        from agent_issues import _load_issues
        data = _load_issues()
        dup_id = _find_duplicate_issue(
            failure_type, template, phase, evidence, data.get("issues", []))
        if dup_id:
            agent._log("AUTOR", f"Auto-research: dublet — {dup_id}",
                       f"{failure_type} i {template}/{phase}")
            return None
    except Exception as exc:
        agent._log("AUTOR", "Auto-research: dedup fejlede", str(exc))

    # Create a CORE-issue documenting the failure
    issue_id = _create_issue(agent, failure_type, evidence, template, phase, "")
    if issue_id:
        _save_core_reference(session_id, issue_id, template, phase, failure_type)
        agent._log("AUTOR", f"Oprettede {issue_id} — original session: {session_id[:12]}",
                   f"{template}/{phase} fejlede med {failure_type}")
        # NOTE: No longer starts background thread — caller handles inline execution
    return issue_id


def start_research_for_issue(agent: Any, issue_id: str) -> None:
    """Start autonomous research for a specific issue.

    Now uses the inline sub-session flow. For POST endpoints without
    SSE context (e.g. /api/autoresearch/run/<issue_id>), this logs
    a depreciation notice. Use the automatic inline flow instead
    (trigger_if_needed → _finalize_task_stream → _execute_autoresearch_issue).
    """
    agent._log("AUTOR", f"Auto-research: {issue_id} — brug automatisk inline flow",
               "start_research_for_issue er deprecated. Auto-research kører nu "
               "automatisk via SSE under fase-eksekvering.")



def _save_state(research_id: str, state: dict) -> None:
    """Save research session state to disk."""
    dirpath = os.path.join(_LOG_DIR, research_id)
    os.makedirs(dirpath, exist_ok=True)
    try:
        with open(_state_path(research_id), "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except OSError:
        pass




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
    elif failure_type == FAILURE_INCOMPLETE:
        p = evidence.get("modules_planned", "?")
        c = evidence.get("modules_created", "?")
        return (f"Ufuldstændig ekstrahering i {template}/{phase} "
                f"— {c}/{p} moduler oprettet")
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
    elif failure_type == FAILURE_INCOMPLETE:
        p = evidence.get("modules_planned", "?")
        c = evidence.get("modules_created", "?")
        missing = evidence.get("missing_modules", [])
        lines.append("### Hvad skete der?")
        lines.append(f"Fasen løb tør for iterationer før alt planlagt arbejde "
                     f"var færdigt ({c}/{p} moduler oprettet).")
        lines.append(f"Manglende moduler: {', '.join(missing)}")
        lines.append("")
        lines.append("### Analyse")
        lines.append("LLM'en kaldte værktøjer korrekt, men iteration budgettet "
                     "var for lavt til at fuldføre alle moduler. "
                     "Budgettet skal beregnes dynamisk baseret på antal moduler "
                     "i refactor_plan.md i stedet for at være fast.")
        lines.append("")
        lines.append("### Forventet næste skridt")
        lines.append("1. Læs _get_max_iterations() i agent_tasks.py\n"
                     "2. Beregn dynamisk budget: max(20, 2 + antal_moduler * 2 + 5)\n"
                     "3. Tilføj system-besked når todos auto-opdateres\n"
                     "4. Opdater instructions/refactor.json — fjern 'Brug update_todo'")
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
    elif failure_type == FAILURE_INCOMPLETE:
        c = evidence.get("modules_created", 0)
        p = evidence.get("modules_planned", 0)
        return (f"Kun {c}/{p} moduler blev oprettet i {template}/{phase}. "
                f"Fasen kan ikke fuldføres før ALLE planlagte moduler findes.")
    else:
        return f"Fasen {phase} i {template} fejler af uforklarede årsager."


def _build_issue_fix(failure_type: str, evidence: dict,
                      template: str, phase: str) -> str:
    """Build a specific, actionable fix proposal based on failure context.

    Generates an EXECUTABLE proposed_fix — not just a description.
    The fix includes exact file paths, code context, and edit_file
    instructions so the LLM can execute it directly.
    """

    # Helper: try to find relevant code context
    def _find_context(*symbols: str) -> str:
        """Try to locate symbols and return file + line context."""
        try:
            from agent_files import locate_code
            from agent_skills import TEMPLATE_PHASE_ITERATION_LIMITS
            for sym in symbols:
                loc = locate_code("agent_skills.py", sym)
                if loc.get("success"):
                    return (f"agent_skills.py omkring linje {loc['line']}-{loc['end_line']}. "
                            f"Brug locate(name=\"{sym}\") for at se den præcise kode.")
        except Exception:
            pass
        return ""

    if failure_type == FAILURE_MISSING_TOOL:
        uncalled = evidence.get("uncalled", [])
        lines = [
            f"Fasen \"{phase}\" i \"{template}\" kræver at LLM'en kalder "
            f"{', '.join(uncalled)}.",
        ]

        ctx = _find_context(*uncalled, "SECTION_INSTRUCTIONS",
                            f"TEMPLATE_TASK_TOOLS", "TEMPLATE_PHASE_ITERATION_LIMITS")
        if ctx:
            lines.append(f"\nKontekst: {ctx}")

        for tool in uncalled:
            if tool == "edit_file":
                lines.extend([
                    "",
                    f"Problem: {template}/{phase} har edit_file i active_tools, "
                    "men LLM'en kaldte det ikke.",
                    "",
                    "Løsning (vælg én):",
                    "1. Hvis edit_file skal være påkrævet: Opdater sektionsinstruktionen "
                    f"i instructions/{template}.json så \"{phase}\" starter med "
                    '"DIN FØRSTE handling SKAL være edit_file".',
                    "2. Hvis create_issue er et acceptabelt alternativ: Tilføj "
                    f"create_issue til TEMPLATE_TASK_TOOLS for {template}/{phase} "
                    "i agent_skills.py.",
                    "3. Hvis fasen er read-only: Fjern edit_file fra "
                    f"TEMPLATE_TASK_TOOLS for {template}/{phase}.",
                ])
            elif tool == "write_file":
                lines.append(
                    f"\nTilføj write_file til TEMPLATE_TASK_TOOLS for "
                    f"{template}/{phase} i agent_skills.py."
                )
            elif tool == "update_issue_status":
                lines.append(
                    f"\nTjek at {template}/{phase} er i CLOSE_PHASE_ALIASES "
                    "i agent_tasks.py:993."
                )

        lines.append(
            "\nRodårsag: _check_required_tools() håndhæver at påkrævede "
            "værktøjer kaldes før <<<DONE>>>."
        )
        return "\n".join(lines)

    elif failure_type == FAILURE_TOOL_FAILED:
        tool = evidence.get("tool", "?")
        ctx = _find_context(tool, "git_ops.edit_file", "tools.ToolRegistry.execute")
        return (
            f"Værktøjet {tool} fejlede i {template}/{phase} "
            f"efter {evidence.get('attempts', 0)} forsøg.\n"
            f"Sidste args: {evidence.get('last_args', 'ukendt')}\n"
            f"Sidste fejl: {evidence.get('last_error', 'ukendt')}\n"
            f"{('\\nKontekst: ' + ctx) if ctx else ''}\n\n"
            f"Løsning: Tjek {tool}'s implementering for denne edge case. "
            f"Overvej at tilføje bedre fejlhåndtering."
        )

    elif failure_type == FAILURE_READ_LOOP:
        ctx = _find_context("TEMPLATE_PHASE_ITERATION_LIMITS", "MAX_TASK_ITERATIONS")
        return (
            f"LLM'en læste {evidence.get('consecutive_reads', 0)} gange "
            f"uden at skrive i {template}/{phase}.\n"
            f"{('\\nKontekst: ' + ctx) if ctx else ''}\n\n"
            f"Løsning: Øg iteration budget for {template}/{phase} "
            f"i TEMPLATE_PHASE_ITERATION_LIMITS, eller tilføj "
            f"\"DIN FØRSTE handling SKAL være edit_file\" i instructions/{template}.json."
        )

    elif failure_type == FAILURE_INCOMPLETE:
        p = evidence.get("modules_planned", 0)
        c = evidence.get("modules_created", 0)
        missing = evidence.get("missing_modules", [])
        _current_budget = 20
        try:
            from config import TEMPLATE_PHASE_ITERATION_LIMITS
            _current_budget = TEMPLATE_PHASE_ITERATION_LIMITS.get("refactor", {}).get("Ekstraher", 20)
        except Exception:
            pass
        guess_budget = max(_current_budget, 2 + p * 2 + 5)
        return (
            f"Fasen \"{phase}\" i \"{template}\" løb tør for iterationer "
            f"før ALLE planlagte moduler var oprettet ({c}/{p}).\n"
            f"Manglende moduler: {', '.join(missing)}\n\n"
            f"Tre forbedringer er nødvendige:\n\n"
            f"=== 1. Dynamisk iteration budget (agent_tasks.py) ===\n"
            f"I funktionen _get_max_iterations() tilføj et tjek for "
            f"refactor/Ekstraher der beregner budgettet dynamisk:\n\n"
            f"  if template == \"refactor\" and task_lower == \"ekstraher\":\n"
            f"      import os\n"
            f"      from file_checks import _parse_refactor_plan_modules\n"
            f"      wd = os.environ.get('AGENT_WORKDIR', '') or os.getcwd()\n"
            f"      pp = os.path.join(wd, 'refactor_plan.md')\n"
            f"      if os.path.exists(pp):\n"
            f"          mods = _parse_refactor_plan_modules(pp)\n"
            f"          if mods:\n"
            f"              return max({_current_budget}, 2 + len(mods) * 2 + 5)\n\n"
            f"Anslået budget for denne fase: {guess_budget} iterationer "
            f"(nuværende: {_current_budget} for refactor Ekstraher).\n\n"
            f"=== 2. System-besked ved auto-todo opdatering (agent_tasks.py) ===\n"
            f"I solve_task_stream(), efter _reconcile_llm_todos(), tilføj:\n\n"
            f"  if _auto_done_ids:\n"
            f"      messages.append({{'role': 'user', 'content':\n"
            f"          f'[SYSTEM: ✅ TODO auto-opdateret: {{\", \".join(_auto_done_ids)}}]'}})\n\n"
            f"=== 3. Fjern 'Brug update_todo' fra instruktion "
            f"(instructions/refactor.json) ===\n"
            f"Erstat '📋 Brug **update_todo** for at markere hvert modul færdigt.'\n"
            f"med: '✅ TODO\\'er opdateres automatisk — spring update_todo over.'"
        )

    elif failure_type == FAILURE_SHORT_OUTPUT:
        ctx = _find_context("SECTION_INSTRUCTIONS", "get_templates")
        lines = [
            f"Fasen \"{phase}\" i \"{template}\" har ingen eller for kort "
            "sektionsinstruktion.",
        ]
        if ctx:
            lines.append(f"\nKontekst: {ctx}\n")

        if template == "fri":
            lines.append(
                f"Løsning: Tilføj en \"{phase}\"-sektion til SECTION_INSTRUCTIONS "
                f"for \"{template}\"-templaten.\n\n"
                f"Åbn instructions/selvforbedring.json (eller "
                f"instructions/{template}.json) og tilføj:\n\n"
                f'  "{phase}": "Kald relevante værktøjer og producér '
                f'mindst 200 tegn output. Brug edit_file til at redigere '
                f'og run_tests til at verificere."\n\n'
                f"Brug edit_file med old_text/new_text fra JSON-filen."
            )
        else:
            lines.append(
                f"Løsning: Tjek instructions/{template}.json og tilføj en "
                f"instruktion for \"{phase}\" der beder LLM'en om at "
                f"kalde værktøjer og producere mindst 200 tegn."
            )

        lines.append(
            "\nRodårsag: LLM'en afsluttede uden værktøjskald eller output. "
            "Manglende eller for vag sektionsinstruktion."
        )
        return "\n".join(lines)

    else:
        # Unknown failure — try to provide specific context
        ctx_lines = [f"Uforklaret fejl i {template}/{phase}."]
        ctx = _find_context("SECTION_INSTRUCTIONS",
                            f"TEMPLATE_TASK_TOOLS",
                            "TEMPLATE_PHASE_ITERATION_LIMITS",
                            "get_templates")
        if ctx:
            ctx_lines.append(f"\nKontekst: {ctx}\n")

        ctx_lines.append(
            "Fremgangsmåde:\n"
            "1. Læs agent_log for at forstå hvad der skete.\n"
            "2. Tjek om fasen har en instruktion i instructions/ mappen.\n"
            "3. Hvis instruktionen mangler: tilføj den.\n"
            "4. Hvis instruktionen er for vag: gør den mere specifik.\n"
            "5. Kør run_tests() for at verificere."
        )
        return "\n".join(ctx_lines)


def _save_core_reference(session_id: str, core_id: str,
                          template: str, phase: str,
                          failure_type: str) -> None:
    """Gem en reference i den originale session så CORE-issue kan spores tilbage.

    Skriver direkte til session JSON-filen for at sikre at referencen
    overlever selv hvis sessionen ikke gemmes normalt.
    """
    if not session_id or session_id == "unknown":
        return
    sess_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sessions")
    path = os.path.join(sess_dir, f"{session_id}.json")
    if not os.path.exists(path):
        return
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        log = data.setdefault("agent_log", [])
        log.append({
            "timestamp": time.time(),
            "level": "CORE",
            "message": f"Oprettede {core_id} for fejl i {template}/{phase}",
            "detail": f"failure_type={failure_type}",
        })
        data.setdefault("core_issues", []).append({
            "id": core_id,
            "template": template,
            "phase": phase,
            "failure_type": failure_type,
            "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
        })
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _update_sessions_for_core_resolution(core_id: str, resolution_session: str) -> None:
    """Når et CORE-issue resolves, opdater alle sessions der refererer til det.

    Scannner sessions-mappen for JSON-filer med core_issues referencer.
    """
    if not core_id:
        return
    sess_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sessions")
    if not os.path.isdir(sess_dir):
        return
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    for fname in os.listdir(sess_dir):
        if not fname.endswith(".json"):
            continue
        path = os.path.join(sess_dir, fname)
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception:
            continue
        refs = data.get("core_issues", [])
        if not refs:
            continue
        updated = False
        for ref in refs:
            if ref.get("id", "").upper() == core_id.upper():
                if not ref.get("resolved"):
                    ref["resolved"] = now
                    ref["resolved_by"] = resolution_session
                    updated = True
                break
        if updated:
            log = data.setdefault("agent_log", [])
            log.append({
                "timestamp": time.time(),
                "level": "CORE",
                "message": f"{core_id} er resolved af session {resolution_session[:12]}",
                "detail": "",
            })
            try:
                with open(path, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
            except Exception:
                pass
