import json as _json
import os
import re
import sys
import subprocess
from lang import t
from i18n import K

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


def _get_issues_path():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs", "issues", "observed", "issues.json")


def _load_issues():
    path = _get_issues_path()
    if not os.path.exists(path):
        return {"meta": {"generated": "2026-05-20", "source": "Agenten", "total": 0}, "issues": []}
    with open(path, encoding="utf-8") as f:
        return _json.load(f)


def _save_issues(data):
    path = _get_issues_path()
    with open(path, "w", encoding="utf-8") as f:
        _json.dump(data, f, ensure_ascii=False, indent=2)


def _next_refac_id(data):
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
}


def _next_issue_id(data, issue_type):
    prefix = ISSUE_TYPE_PREFIXES.get(issue_type, "BUG")
    existing = [i["id"] for i in data.get("issues", []) if i["id"].startswith(f"{prefix}-")]
    nums = [int(i.split("-")[1]) for i in existing if i.split("-")[1].isdigit()]
    return f"{prefix}-{max(nums) + 1:03d}" if nums else f"{prefix}-001"


def run_pytest(test_path=""):
    try:
        cmd = [sys.executable, "-m", "pytest", "-v"]
        if test_path:
            cmd.append(test_path)
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        return {"success": result.returncode == 0, "stdout": result.stdout, "stderr": result.stderr, "exit_code": result.returncode}
    except subprocess.TimeoutExpired:
        return {"success": False, "stdout": "", "stderr": "Timeout (120s)", "exit_code": -1}
    except FileNotFoundError:
        return {"success": False, "stdout": "", "stderr": "python -m pytest not found", "exit_code": -1}


def read_issue(issue_id):
    data = _load_issues()
    for issue in data.get("issues", []):
        if issue.get("id", "").lower() == issue_id.lower():
            result = {"success": True, "issue": dict(issue)}
            # Ensure acceptance_criteria is shown even if empty
            result["issue"].setdefault("acceptance_criteria", "")
            return result
    available = [i["id"] for i in data.get("issues", [])]
    return {"success": False, "error": f"Issue '{issue_id}' not found. Available: {available}"}


def _resolve_referenced_issues(agent, data, issue, status, resolution_note):
    if status != "resolved":
        return
    ref_pattern = re.compile(r'(BUG-\d+|SEC-\d+|TST-\d+|ARC-\d+|PRF-\d+|MNT-\d+|REFAC-\d+)')
    fields = [issue.get("title", ""), issue.get("description", ""),
              issue.get("location", ""), issue.get("impact", ""), issue.get("proposed_fix", ""),
              resolution_note]
    refs = set()
    for field in fields:
        refs.update(ref_pattern.findall(field))
    refs.discard(issue.get("id", ""))
    for ref in refs:
        for ref_issue in data.get("issues", []):
            if ref_issue.get("id", "").upper() == ref.upper() and ref_issue.get("status") in ("open", "in_progress"):
                ref_issue["status"] = "resolved"
                ref_issue["resolution_note"] = f"Lukket automatisk da {issue['id']} blev resolved: {resolution_note[:100]}"
                agent._log("INFO", f"Med-reference {ref} \u2192 resolved via {issue['id']}", "")


def update_issue_status(agent, issue_id, status, resolution_note=""):
    phase = getattr(agent, 'current_phase', None)
    if status == "resolved" and phase == "afklar":
        return {"success": False, "error": "Afklar-fasen kan ikke sætte status til 'resolved'. Brug update_issue_status til at tilføje detaljer, og fortsæt til Verificer-fasen for at bekræfte om fejlen er løst."}
    data = _load_issues()
    for issue in data.get("issues", []):
        if issue.get("id", "").lower() == issue_id.lower():
            issue["status"] = status
            if resolution_note:
                issue["resolution_note"] = resolution_note
            _resolve_referenced_issues(agent, data, issue, status, resolution_note)
            _save_issues(data)
            agent._log("INFO", f"Issue {issue_id} \u2192 {status}", resolution_note[:200])
            if status == "resolved":
                agent.issue_resolved = True
            return {"success": True, "issue": issue, "status": status}
    return {"success": False, "error": f"Issue '{issue_id}' not found."}


def create_refactor_issue(agent, filepath, line_count, related_issues=None):
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


def create_issue(agent, title, type="bug", severity="medium", description="", location="", impact="", proposed_fix="", acceptance_criteria=""):
    data = _load_issues()
    # Normalize location to filename format
    if location:
        if ":" in location:
            location = location.split(":")[0].strip()
        else:
            location = location.split()[0].strip("`")
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


def detect_oversize_file(agent, filename, content, related_bugs=None):
    line_count = content.count("\n")
    if line_count < OVERSIZE_LINE_LIMIT:
        return None
    result = {"file": filename, "lines": line_count, "related": related_bugs or []}
    agent._log("WARNING", f"Fil overskrider {OVERSIZE_LINE_LIMIT} linjer: {filename}", f"{line_count} linjer")
    agent._pending_refactor = result
    return result
