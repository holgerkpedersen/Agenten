"""CoreAnalytics — tracks failure/edit patterns in the agent's own source code.

Enables the agent to identify hotspots in its own codebase
by recording tool-call outcomes mapped to handler files,
test results, and modification frequency.
"""

import json
import os
import time
from typing import Any

_CORE_DEFAULT_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    ".agent_storage", "core_outcomes.json"
)

TOOL_HANDLER_MAP: dict[str, str] = {
    "edit_file": "git_ops.py",
    "write_file": "agent_files.py",
    "read_chunk": "agent_files.py",
    "list_chunks": "agent_files.py",
    "locate": "agent_files.py",
    "read_location": "agent_files.py",
    "list_symbols": "agent_files.py",
    "list_files": "agent_files.py",
    "read_issue": "agent_issues.py",
    "update_issue_status": "agent_issues.py",
    "create_issue": "agent_issues.py",
    "create_refactor_issue": "agent_issues.py",
    "run_tests": "git_ops.py",
    "list_dir": "git_ops.py",
    "git_status": "agent_git.py",
    "git_diff": "git_ops.py",
    "git_commit": "git_ops.py",
    "git_push": "git_ops.py",
    "git_create_branch": "agent_git.py",
    "git_checkout": "agent_git.py",
    "add_image": "agent_tasks.py",
    "extract_symbol": "refactoring_engine.py",
    "remove_symbol": "refactoring_engine.py",
    "add_import": "refactoring_engine.py",
    "ddg_search": "ddg_search.py",
}

TOOL_HANDLER_MAP_INVERSE: dict[str, list[str]] = {}
for t, h in TOOL_HANDLER_MAP.items():
    TOOL_HANDLER_MAP_INVERSE.setdefault(h, []).append(t)


class CoreAnalytics:
    """Tracks outcomes mapped to source files for self-diagnosis.

    Schema (stored as JSON)::

        {
          "tools": {
            "edit_file": {
              "handler": "git_ops.py",
              "calls": 50,
              "failures": 5,
              "last_error": "old_string not found",
              "errors": {"old_string not found": 3},
              "last_recorded": 1717000000.0
            }
          },
          "tests": {
            "tests/test_api.py": {
              "runs": 12, "passes": 10, "failures": 2,
              "last_failure": "AssertionError: ...",
              "last_recorded": 1717000000.0
            }
          },
          "edits": {
            "git_ops.py": {
              "count": 7,
              "last_recorded": 1717000000.0
            }
          }
        }
    """

    def __init__(self, path: str = "") -> None:
        self.path = path or _CORE_DEFAULT_PATH
        self.data: dict[str, Any] = {
            "tools": {},
            "tests": {},
            "edits": {},
            "sessions": {"total": 0, "failed": 0, "recent_errors": []},
        }

    def load(self) -> None:
        try:
            with open(self.path, encoding="utf-8") as f:
                stored = json.load(f)
                for key in ("tools", "tests", "edits", "sessions"):
                    if key in stored:
                        self.data[key] = stored[key]
        except (FileNotFoundError, json.JSONDecodeError):
            pass

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2, ensure_ascii=False)

    # ── tool outcomes ──────────────────────────────────────────────

    def record_tool_outcome(
        self,
        tool_name: str,
        success: bool,
        error: str = "",
        handler_file: str | None = None,
    ) -> None:
        handler = handler_file or TOOL_HANDLER_MAP.get(tool_name, "unknown.py")
        entry = self.data["tools"].setdefault(tool_name, {
            "handler": handler,
            "calls": 0,
            "failures": 0,
            "last_error": "",
            "errors": {},
            "last_recorded": 0.0,
        })
        entry["calls"] += 1
        if not success:
            entry["failures"] += 1
            trimmed = error[:200] if error else "unknown"
            entry["last_error"] = trimmed
            err_map: dict[str, int] = entry["errors"]
            err_map[trimmed] = err_map.get(trimmed, 0) + 1
        entry["last_recorded"] = time.time()

    # ── test outcomes ──────────────────────────────────────────────

    def record_test_outcome(
        self,
        test_file: str,
        passed: bool,
        summary: str = "",
        duration: float = 0,
    ) -> None:
        entry = self.data["tests"].setdefault(test_file, {
            "runs": 0,
            "passes": 0,
            "failures": 0,
            "last_failure": "",
            "last_recorded": 0.0,
        })
        entry["runs"] += 1
        if passed:
            entry["passes"] += 1
        else:
            entry["failures"] += 1
            entry["last_failure"] = summary[:300] if summary else "unknown"
        entry["last_recorded"] = time.time()

    # ── edit tracking ──────────────────────────────────────────────

    def record_edit(self, filepath: str) -> None:
        entry = self.data["edits"].setdefault(filepath, {
            "count": 0,
            "last_recorded": 0.0,
        })
        entry["count"] += 1
        entry["last_recorded"] = time.time()

    # ── session outcomes ───────────────────────────────────────────

    def record_session(self, success: bool, error: str = "") -> None:
        self.data["sessions"]["total"] += 1
        if not success:
            self.data["sessions"]["failed"] += 1
            if error:
                recent = self.data["sessions"]["recent_errors"]
                recent.append(error[:200])
                if len(recent) > 20:
                    recent[:] = recent[-20:]

    # ── queries ────────────────────────────────────────────────────

    def get_hotspots(self, min_failures: int = 2) -> list[dict[str, Any]]:
        """Return handler files sorted by total tool failures (desc)."""
        file_failures: dict[str, int] = {}
        for info in self.data["tools"].values():
            h = info.get("handler", "unknown.py")
            f = info.get("failures", 0)
            file_failures[h] = file_failures.get(h, 0) + f

        spots = [
            {"file": h, "tool_failures": f, "edits": 0}
            for h, f in file_failures.items()
            if f >= min_failures
        ]
        for spot in spots:
            edit_info = self.data["edits"].get(spot["file"])
            if edit_info:
                spot["edits"] = edit_info["count"]
        spots.sort(key=lambda x: -x["tool_failures"])
        return spots

    def get_summary(self, min_failures: int = 2) -> str:
        """Return a compact markdown summary for prompt injection."""
        lines: list[str] = []
        hotspots = self.get_hotspots(min_failures=min_failures)
        if hotspots:
            lines.append("### Egne fejlmønstre")
            for s in hotspots:
                edit_note = f", {s['edits']} ændringer" if s["edits"] else ""
                lines.append(
                    f"- `{s['file']}`: {s['tool_failures']} værktøjsfejl{edit_note}"
                )

        test_info = self.data["tests"]
        failing = [k for k, v in test_info.items() if v.get("failures", 0) > 0]
        if failing:
            lines.append("### Tests med fejl")
            for t in failing[:5]:
                v = test_info[t]
                lines.append(f"- `{t}`: {v['failures']}/{v['runs']} fejlede")

        edits_total = sum(v["count"] for v in self.data["edits"].values())
        sess = self.data["sessions"]
        if sess["total"] > 0:
            pct = round(100 * sess["failed"] / sess["total"], 1) if sess["total"] else 0
            lines.append(f"### Sessioner: {sess['total']} i alt, {sess['failed']} fejlede ({pct}%)")

        return "\n".join(lines)
