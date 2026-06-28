from flask import Flask, request, jsonify, send_from_directory, Response, stream_with_context
from config import app
from typing import Any, Generator
import agent_issues
import json
import os
from session_manager import SessionManager, _guard_json_body, agent, session_manager, current_session_id, execution_status, execution_status_lock, export_folder, export_folder_lock

@app.route("/api/issues", methods=["GET"])
def list_issues() -> Any:
    """list issues — merged from framework (Agenten) and workdir."""
    issues_path = agent_issues._get_issues_path()
    data = agent_issues._load_all_issues()
    meta = data.setdefault("meta", {})
    all_issue_count = len(data.get("issues", [])) + len(data.get("active_risks", []))
    meta["total_all_issues"] = all_issue_count

    def issue_values_to_string(values):
        if isinstance(values, (list, tuple, set)):
            return ", ".join(str(value) for value in values)
        return str(values or "")

    all_issues = [dict(issue) for issue in data.get("issues", [])]
    for risk in data.get("active_risks", []):
        normalized = dict(risk)
        if "description" not in normalized and normalized.get("context"):
            normalized["description"] = normalized["context"]
        if "location" not in normalized:
            normalized["location"] = issue_values_to_string(normalized.get("affected_files") or [])
        if "impact" not in normalized and normalized.get("type") == "stability":
            normalized["impact"] = "Active stability risk that needs regression coverage before marking stable."
        if "proposed_fix" not in normalized and normalized.get("action"):
            normalized["proposed_fix"] = normalized["action"]
        if "acceptance_criteria" not in normalized and normalized.get("prevention_test"):
            normalized["acceptance_criteria"] = normalized["prevention_test"]
        all_issues.append(normalized)
    data["all_issues"] = all_issues
    return jsonify({"success": True, **data})


@app.route("/api/issues/<issue_id>", methods=["DELETE"])
def delete_issue(issue_id: str) -> Any:
    """delete issue.

    Args:
        issue_id:"""
    issues_path = agent_issues._get_issues_path()
    if not os.path.exists(issues_path):
        return jsonify({"success": False, "error": "Issues-fil findes ikke"}), 404
    with open(issues_path, encoding="utf-8") as f:
        data = json.load(f)
    before = len(data.get("issues", []))
    data["issues"] = [i for i in data.get("issues", []) if i.get("id", "").lower() != issue_id.lower()]
    if len(data["issues"]) == before:
        return jsonify({"success": False, "error": f"Issue '{issue_id}' findes ikke"}), 404
    data["meta"]["total"] = len(data["issues"])
    with open(issues_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return jsonify({"success": True, "deleted": issue_id})


@app.route("/api/issues", methods=["POST"])
def create_issue_from_ui() -> Any:
    """create issue from UI."""
    data = request.json
    if not data or not data.get("title"):
        return jsonify({"success": False, "error": "Title er påkrævet"}), 400
    result = agent_issues.create_issue(
        agent=agent,
        title=data["title"],
        type=data.get("type", "bug"),
        severity=data.get("severity", "medium"),
        description=data.get("description", ""),
        location=data.get("location", ""),
        impact=data.get("impact", ""),
        proposed_fix=data.get("proposed_fix", ""),
        acceptance_criteria=data.get("acceptance_criteria", ""),
    )
    return jsonify(result)
