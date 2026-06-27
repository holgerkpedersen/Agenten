"""Issue management, creation and status updates."""

import json as _json
import os
import re
import time
import sys
import subprocess
from typing import Any
from lang import t
from i18n import K
import config
import agent_files
from config import get_logger
log = get_logger(__name__)

REFAC_TEMPLATE = {
    "id": None,
    "title": None,
    "type": "refactor",
    "severity": "medium",
    "description": None,
    "location": None,
    "impact": None,
    "proposed_fix": None,
    "status": "open",
    "related": []
}

OVERSIZE_LINE_LIMIT = 1000


def _get_framework_issues_path() -> str:
    """Agentens egne issues (framework issues) — altid Agenten-projektets fil."""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs", "issues", "observed", "issues.json")


def _get_issues_path() -> str:
    """get issues path - uses workdir hvis AGENT_WORKDIR er sat, ellers Agentens."""
    workdir = os.environ.get("AGENT_WORKDIR", "")
    if workdir:
        wd_path = os.path.join(workdir, "docs", "issues", "observed", "issues.json")
        os.makedirs(os.path.dirname(wd_path), exist_ok=True)
        return wd_path
    return _get_framework_issues_path()


def _read_json_file(path: str) -> dict[str, Any]:
    """Læs en JSON-fil med fallback til tom struktur."""
    if not os.path.exists(path):
        return {"meta": {"total": 0}, "issues": [], "active_risks": []}
    try:
        with open(path, encoding="utf-8") as f:
            return _json.load(f)
    except (_json.JSONDecodeError, IOError, OSError) as e:
        log.warning("Failed to load %s: %s", path, e)
        return {"meta": {"total": 0}, "issues": [], "active_risks": []}


def _load_all_issues() -> dict[str, Any]:
    """Load issues from BOTH framework (Agenten) and workdir, merged.
    
    Workdir issues override framework issues with the same ID.
    active_risks from both are concatenated.
    """
    framework = _read_json_file(_get_framework_issues_path())
    workdir_path = _get_issues_path()
    workdir = _read_json_file(workdir_path)

    # If workdir points to same file as framework, no merge needed
    if os.path.abspath(_get_framework_issues_path()) == os.path.abspath(workdir_path):
        return workdir
    # If AGENT_WORKDIR is set and is NOT Agenten itself, only show workdir issues
    _wd = os.environ.get("AGENT_WORKDIR", "")
    if _wd:
        _agent_dir = os.path.dirname(os.path.abspath(__file__))
        if os.path.abspath(_wd) != _agent_dir:
            return workdir

    # Merge: framework issues first, workdir overwrites on ID conflict
    seen_ids: set[str] = set()
    merged_issues: list[dict[str, Any]] = []
    for source in (workdir.get("issues", []), framework.get("issues", [])):
        for issue in source:
            iid = issue.get("id", "")
            if iid not in seen_ids:
                seen_ids.add(iid)
                merged_issues.append(issue)
            elif iid:
                # Already seen → workdir's version wins, skip framework duplicate
                pass

    # Risks: concatenated (no dedup by ID — risks are contextual)
    all_risks = (workdir.get("active_risks") or []) + (framework.get("active_risks") or [])

    merged = dict(workdir)  # start with workdir (has meta, etc.)
    merged["issues"] = merged_issues
    merged["active_risks"] = all_risks
    merged["meta"] = dict(workdir.get("meta", {}))
    merged["meta"]["total"] = len(merged_issues)
    merged["_sources"] = {"framework": _get_framework_issues_path(), "workdir": workdir_path}
    return merged


def _load_issues() -> dict[str, Any]:
    """load issues from workdir only (for write operations)."""
    path = _get_issues_path()
    return _read_json_file(path)


def _save_issues(data: dict[str, Any]) -> None:
    """save issues.
    
    Args:
        data:"""
    path = _get_issues_path()
    with open(path, "w", encoding="utf-8") as f:
        _json.dump(data, f, ensure_ascii=False, indent=2)


def _get_issues(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Get issues list from data dict (DRY replacement for data.get('issues', []))."""
    return data.get("issues", [])


def _next_refac_id(data: dict[str, Any]) -> str:
    """next refac id.
    
    Args:
        data:"""
    existing = [i["id"] for i in _get_issues(data) if i["id"].startswith("REFAC-")]
    nums = [int(i.split("-")[1]) for i in existing if i.split("-")[1].isdigit()]
    return f"REFAC-{max(nums) + 1:03d}" if nums else "REFAC-001"


ISSUE_TYPE_PREFIXES = {
    "bug": "BUG",
    "security": "SEC",
    "architecture": "ARC",
    "testing": "TST",
    "performance": "PRF",
    "maintainability": "MNT",
    "refactor": "REFAC",
    "feature": "FTR",
    "self": "CORE",
    "stability": "STAB",
}


def _next_issue_id(data: dict[str, Any], issue_type: str) -> str:
    """next issue id.
    
    Args:
        data:
        issue_type:"""
    prefix = ISSUE_TYPE_PREFIXES.get(issue_type, "BUG")
    existing = [i["id"] for i in _get_issues(data) if i["id"].startswith(f"{prefix}-")]
    nums = [int(i.split("-")[1]) for i in existing if i.split("-")[1].isdigit()]
    return f"{prefix}-{max(nums) + 1:03d}" if nums else f"{prefix}-001"


def run_pytest(test_path: str = "") -> dict[str, Any]:
    """run pytest.
    
    Args:
        test_path:"""
    try:
        workdir = os.environ.get('AGENT_WORKDIR') or os.getcwd()
        cmd = [sys.executable, "-m", "pytest", "-v"]
        if test_path:
            cmd.append(test_path)
        else:
            cmd.append("--ignore=tests/temp")
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=workdir, timeout=config.SUBPROCESS_TIMEOUT)
        return {"success": result.returncode == 0, "stdout": result.stdout, "stderr": result.stderr, "exit_code": result.returncode}
    except subprocess.TimeoutExpired:
        return {"success": False, "stdout": "", "stderr": f"Timeout ({config.SUBPROCESS_TIMEOUT}s)", "exit_code": -1}
    except FileNotFoundError:
        return {"success": False, "stdout": "", "stderr": "python -m pytest not found", "exit_code": -1}


def read_issue(issue_id: str, include_hints: bool = False) -> dict[str, Any]:
    """read issue.

    Args:
        issue_id:
        include_hints:"""
    data = _load_issues()
    # Search in regular issues first
    for issue in _get_issues(data):
        if issue.get("id", "").lower() == issue_id.lower():
            result = {"success": True, "issue": dict(issue)}
            result["issue"].setdefault("acceptance_criteria", "")
            has_hints = bool(issue.get("proposed_fix"))
            result["issue"]["_hints_available"] = has_hints
            if include_hints:
                result["issue"]["_hints_read"] = True
            else:
                result["issue"]["_hints_read"] = False
                for key in ("proposed_fix", "resolution_note", "acceptance_criteria"):
                    result["issue"].pop(key, None)
            return result
    # Search in active_risks (STAB-* etc.)
    for risk in data.get("active_risks", []):
        if risk.get("id", "").lower() == issue_id.lower():
            risk_data = dict(risk)
            risk_data.setdefault("acceptance_criteria", "")
            risk_data.setdefault("description", risk_data.get("context", ""))
            risk_data.setdefault("location", ", ".join(risk_data.get("affected_files", [])))
            risk_data.setdefault("impact", "")
            risk_data.setdefault("proposed_fix", risk_data.get("action", ""))
            return {"success": True, "issue": risk_data, "from_risk": True}
    available = [i["id"] for i in _get_issues(data)] + [r["id"] for r in data.get("active_risks", [])]
    return {"success": False, "error": f"Issue '{issue_id}' not found. Available: {available}"}


def _resolve_referenced_issues(agent: Any, data: dict[str, Any], issue: dict[str, Any], status: str, resolution_note: str) -> list[tuple[str, str]]:
    """resolve referenced issues.
    
    Returns list of (ref_id, note) for callers to apply.
    """
    if status != "resolved":
        return []
    ref_pattern = re.compile(r'(BUG-\d+|SEC-\d+|TST-\d+|ARC-\d+|PRF-\d+|MNT-\d+|REFAC-\d+)')
    fields = [issue.get("title", ""), issue.get("description", ""),
              issue.get("location", ""), issue.get("impact", ""), issue.get("proposed_fix", ""),
              resolution_note]
    refs = set()
    for field in fields:
        refs.update(ref_pattern.findall(field))
    refs.discard(issue.get("id", ""))
    results = []
    for ref in refs:
        for ref_issue in _get_issues(data):
            if ref_issue.get("id", "").upper() == ref.upper() and ref_issue.get("status") in ("open", "in_progress"):
                note = f"Lukket automatisk da {issue['id']} blev resolved: {resolution_note[:100]}"
                results.append((ref, note))
                agent._log("INFO", f"Med-reference {ref} \u2192 resolved via {issue['id']}", "")
    return results


def update_issue_status(agent: Any, issue_id: str, status: str, resolution_note: str = "") -> dict[str, Any]:
    """update issue status.

    Args:
        agent:
        issue_id:
        status:
        resolution_note:"""
    data = _load_issues()
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    # Search in regular issues first
    for issue in _get_issues(data):
        if issue.get("id", "").lower() == issue_id.lower():
            issue["status"] = status
            issue["updated_at"] = now
            if resolution_note:
                issue["resolution_note"] = resolution_note
            for ref_id, ref_note in _resolve_referenced_issues(agent, data, issue, status, resolution_note):
                for ref_issue in _get_issues(data):
                    if ref_issue.get("id", "").upper() == ref_id.upper():
                        ref_issue["status"] = "resolved"
                        ref_issue["resolution_note"] = ref_note
            _save_issues(data)
            agent._log("INFO", f"Issue {issue_id} \u2192 {status}", resolution_note[:200])
            if status == "resolved":
                agent.issue_resolved = True
                # Opdater sessions der refererer til dette CORE-issue
                try:
                    from agent_autoresearch import _update_sessions_for_core_resolution
                    _update_sessions_for_core_resolution(issue_id,
                        getattr(agent, "_session_id", "ukendt"))
                except Exception:
                    pass
            return {"success": True, "issue": issue, "status": status}
    # Search in active_risks (STAB-* etc.)
    for risk in data.get("active_risks", []):
        if risk.get("id", "").lower() == issue_id.lower():
            risk["status"] = status
            risk["updated_at"] = now
            if resolution_note:
                risk["resolution_note"] = resolution_note
            _save_issues(data)
            agent._log("INFO", f"Risk {issue_id} \u2192 {status}", resolution_note[:200])
            if status == "resolved":
                agent.issue_resolved = True
            return {"success": True, "issue": risk, "status": status}
    return {"success": False, "error": f"Issue '{issue_id}' not found."}


def create_refactor_issue(agent: Any, filepath: str, line_count: int, related_issues: list | None = None) -> dict[str, Any]:
    """create refactor issue.
    
    Args:
        agent:
        filepath:
        line_count:
        related_issues:"""
    data = _load_issues()
    existing = [i for i in _get_issues(data) if i.get("location", "").startswith(filepath) and i.get("type") == "refactor"]
    if existing:
        agent._log("INFO", f"REFAC-issue findes allerede for {filepath}", existing[0]["id"])
        return {"success": True, "issue": existing[0], "existing": True}

    issue_id = _next_issue_id(data, "refactor")
    issue = dict(REFAC_TEMPLATE)
    issue["id"] = issue_id
    issue["title"] = f"{filepath} er {line_count} linjer — bryder Single Responsibility Principle"
    issue["description"] = (f"{filepath} er {line_count} linjer (gr\u00e6nse: {OVERSIZE_LINE_LIMIT}). "
                           f"En klasse p\u00e5 over 1000 linjer har typisk for mange ansvarsomr\u00e5der "
                           f"og b\u00f8r opdeles efter SOLID-principperne.")
    issue["location"] = filepath
    issue["impact"] = f"H\u00f8j vedligeholdelsesomkostning, lav overskuelighed, \u00f8get risiko for regression."
    issue["proposed_fix"] = (f"Opdel {filepath} i flere moduler efter SOLID. "
                            f"Analys\u00e9r ansvarsomr\u00e5der, opret moduler, flyt metoder, opdat\u00e9r imports.")
    issue["related"] = related_issues or []

    data["issues"].append(issue)
    data["meta"]["total"] = len(data["issues"])
    _save_issues(data)
    agent._log("WARNING", f"REFAC-issue oprettet: {issue_id}", f"{filepath} ({line_count} linjer)")
    return {"success": True, "issue": issue, "existing": False}


def create_issue(agent: Any, title: str, type: str = "bug", severity: str = "medium", description: str = "", location: str = "", impact: str = "", proposed_fix: str = "", acceptance_criteria: str = "") -> dict[str, Any]:
    """create issue.
    
    Args:
        agent:
        title:
        type:
        severity:
        description:
        location:
        impact:
        proposed_fix:
        acceptance_criteria:"""
    data = _load_issues()
    if location:
        parts = re.split(r'\s*[,;]\s*', location)
        resolved_parts = []
        for part in parts:
            m = re.match(r'([\w./\\-]+\.\w+):(\d+)(?:\s*-\s*\d+)?$', part.strip())
            if m:
                fname = m.group(1)
                linenum = int(m.group(2))
                for candidate in [fname, os.path.join(os.path.dirname(os.path.abspath(__file__)), fname)]:
                    if os.path.exists(candidate):
                        result = agent_files.locate_code(filepath=candidate, line_no=linenum)
                        if result.get("success") and result.get("name"):
                            resolved_parts.append(f"{fname}:{result['name']}")
                            break
                        elif result.get("success") and result.get("name") is None:
                            resolved_parts.append(f"{fname}:{linenum}")
                            break
                else:
                    resolved_parts.append(part)
            else:
                m2 = re.match(r'([\w./\\-]+\.\w+):([\w.]+)$', part.strip())
                if m2:
                    fname = m2.group(1)
                    sym_name = m2.group(2)
                    for candidate in [fname, os.path.join(os.path.dirname(os.path.abspath(__file__)), fname)]:
                        if os.path.exists(candidate):
                            vr = agent_files.locate_code(filepath=candidate, name=sym_name)
                            if vr.get("success"):
                                resolved_parts.append(part)
                            else:
                                agent._log("WARNING", f"Location '{sym_name}' not found in {fname}", part)
                                resolved_parts.append(part)
                            break
                    else:
                        resolved_parts.append(part)
                else:
                    resolved_parts.append(part)
        location = ", ".join(resolved_parts)

    title_lower = title.lower()
    title_keywords = {w for w in title_lower.split() if len(w) > 3}
    for i in _get_issues(data):
        if i.get("title") == title:
            agent._log("INFO", f"Issue findes allerede", i["id"])
            return {"success": True, "issue": i, "existing": True}
        loc = i.get("location", "")
        if location and loc and (location in loc or loc in location):
            agent._log("INFO", f"Issue med samme lokation allerede oprettet", i["id"])
            return {"success": True, "issue": i, "existing": True}
        existing_keywords = {w for w in i.get("title", "").lower().split() if len(w) > 3}
        overlap = title_keywords & existing_keywords
        if len(overlap) >= 3:
            agent._log("INFO", f"Issue med samme emne allerede oprettet", i["id"])
            return {"success": True, "issue": i, "existing": True}

    issue_id = _next_issue_id(data, type)
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    issue = {
        "id": issue_id,
        "title": title,
        "type": type,
        "severity": severity,
        "description": description,
        "location": location,
        "impact": impact,
        "proposed_fix": proposed_fix,
        "acceptance_criteria": acceptance_criteria,
        "status": "open",
        "created_at": now,
        "updated_at": now,
    }
    data["issues"].append(issue)
    data["meta"]["total"] = len(data["issues"])
    _save_issues(data)
    agent._log("INFO", f"Issue oprettet: {issue_id}", title[:100])
    return {"success": True, "issue": issue, "existing": False}


def detect_oversize_file(agent: Any, filename: str, content: str, related_bugs: list | None = None) -> dict[str, Any] | None:
    """detect oversize file.
    
    Args:
        agent:
        filename:
        content:
        related_bugs:"""
    # Skip oversize detection if a session is already running — creating new
    # REFAC issues mid-execution distracts the LLM and pollutes the issue list.
    # MagicMock auto-creates attributes, so we must check the actual type.
    tt = getattr(agent, 'task_tree', None)
    if tt is not None and 'mock' not in type(tt).__module__.lower():
        return None
    cp = getattr(agent, 'current_phase', None)
    if cp is not None and 'mock' not in type(cp).__module__.lower():
        return None
        return None
    line_count = content.count("\n")
    if line_count < OVERSIZE_LINE_LIMIT:
        agent._pending_refactor = None
        return None
    # Do NOT use truncated content — count lines directly from disk if content looks truncated
    resolved = agent_files._resolve_path(filename) if hasattr(agent_files, '_resolve_path') else filename
    try:
        with open(resolved, 'r', encoding='utf-8') as f:
            real_lines = sum(1 for _ in f)
        if real_lines > line_count:
            line_count = real_lines
    except Exception:
        pass
    result = {"file": filename, "lines": line_count, "related": related_bugs or []}
    agent._log("WARNING", f"Fil overskrider {OVERSIZE_LINE_LIMIT} linjer: {filename}", f"{line_count} linjer")
    agent._pending_refactor = result
    return result
