"""Issue management, creation and status updates."""

import json as _json
import os
import re
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


def _get_issues_path() -> str:
    """get issues path."""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs", "issues", "observed", "issues.json")


def _load_issues() -> dict[str, Any]:
    """load issues."""
    path = _get_issues_path()
    if not os.path.exists(path):
        return {"meta": {"generated": "2026-05-20", "source": "Agenten", "total": 0}, "issues": []}
    try:
        with open(path, encoding="utf-8") as f:
            return _json.load(f)
    except (_json.JSONDecodeError, IOError, OSError) as e:
        log.warning("Failed to load issues.json: %s", e)
        return {"meta": {"generated": "2026-05-20", "source": "Agenten", "total": 0}, "issues": []}


def _save_issues(data: dict[str, Any]) -> None:
    """save issues.
    
    Args:
        data:"""
    path = _get_issues_path()
    with open(path, "w", encoding="utf-8") as f:
        _json.dump(data, f, ensure_ascii=False, indent=2)


def _next_refac_id(data: dict[str, Any]) -> str:
    """next refac id.
    
    Args:
        data:"""
    existing = [i["id"] for i in data.get("issues", []) if i["id"].startswith("REFAC-")]
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
}


def _next_issue_id(data: dict[str, Any], issue_type: str) -> str:
    """next issue id.
    
    Args:
        data:
        issue_type:"""
    prefix = ISSUE_TYPE_PREFIXES.get(issue_type, "BUG")
    existing = [i["id"] for i in data.get("issues", []) if i["id"].startswith(f"{prefix}-")]
    nums = [int(i.split("-")[1]) for i in existing if i.split("-")[1].isdigit()]
    return f"{prefix}-{max(nums) + 1:03d}" if nums else f"{prefix}-001"


def run_pytest(test_path: str = "") -> dict[str, Any]:
    """run pytest.
    
    Args:
        test_path:"""
    try:
        cmd = [sys.executable, "-m", "pytest", "-v"]
        if test_path:
            cmd.append(test_path)
        else:
            cmd.append("--ignore=tests/temp")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=config.SUBPROCESS_TIMEOUT)
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
    for issue in data.get("issues", []):
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
    available = [i["id"] for i in data.get("issues", [])]
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
        for ref_issue in data.get("issues", []):
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
    for issue in data.get("issues", []):
        if issue.get("id", "").lower() == issue_id.lower():
            issue["status"] = status
            if resolution_note:
                issue["resolution_note"] = resolution_note
            for ref_id, ref_note in _resolve_referenced_issues(agent, data, issue, status, resolution_note):
                for ref_issue in data.get("issues", []):
                    if ref_issue.get("id", "").upper() == ref_id.upper():
                        ref_issue["status"] = "resolved"
                        ref_issue["resolution_note"] = ref_note
            _save_issues(data)
            agent._log("INFO", f"Issue {issue_id} \u2192 {status}", resolution_note[:200])
            if status == "resolved":
                agent.issue_resolved = True
            return {"success": True, "issue": issue, "status": status}
    return {"success": False, "error": f"Issue '{issue_id}' not found."}


def create_refactor_issue(agent: Any, filepath: str, line_count: int, related_issues: list | None = None) -> dict[str, Any]:
    """create refactor issue.
    
    Args:
        agent:
        filepath:
        line_count:
        related_issues:"""
    data = _load_issues()
    existing = [i for i in data.get("issues", []) if i.get("location", "").startswith(filepath) and i.get("type") == "refactor"]
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
    for i in data.get("issues", []):
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
    line_count = content.count("\n")
    if line_count < OVERSIZE_LINE_LIMIT:
        return None
    result = {"file": filename, "lines": line_count, "related": related_bugs or []}
    agent._log("WARNING", f"Fil overskrider {OVERSIZE_LINE_LIMIT} linjer: {filename}", f"{line_count} linjer")
    agent._pending_refactor = result
    return result
