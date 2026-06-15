from __future__ import annotations
import re
import os
import json
from typing import Any
from agent_helpers import _extract_filenames



def _auto_load_issue_files(agent: Agent, prompt: str, template: str | None, files: list[dict[str, Any]]) -> None:
    """Automatically load issue-related files when a bug/issue ID is present in the prompt."""
    if template not in ("bugfix", "issue_handler") or files:
        return
    issue_match = re.search(r'(BUG-\d+|SEC-\d+|TST-\d+|ARC-\d+|PRF-\d+|MNT-\d+|REFAC-\d+)', prompt)
    if not issue_match:
        return
    issue_id = issue_match.group(1)
    try:
        base_dir = os.path.dirname(os.path.abspath(__file__))
        issues_path = os.path.join(base_dir, "docs", "issues", "observed", "issues.json")
        if not os.path.exists(issues_path):
            return
        with open(issues_path, encoding="utf-8") as f:
            issues_data = json.load(f)
        for issue in issues_data.get("issues", []):
            if issue.get("id", "").lower() != issue_id.lower():
                continue
            location = issue.get("location", "")
            filenames = _extract_filenames(location)
            for filename in filenames:
                if not filename.endswith('.py'):
                    continue
                workdir = os.environ.get('AGENT_WORKDIR', '')
                probe_paths = [filename, os.path.join(base_dir, filename), os.path.join(os.getcwd(), filename)]
                if workdir:
                    probe_paths.append(os.path.join(workdir, filename))
                for path in probe_paths:
                    if os.path.exists(path):
                        content = agent._read_file_content(path)
                        if content:
                            files.append({"filename": filename, "content": content, "path": path})
                            agent._log("INFO", f"Auto-loaded fil fra {issue_id}", path)
                        break
    except Exception as e:
        agent._log("WARNING", "Kunne ikke auto-loade issue-fil", str(e))
