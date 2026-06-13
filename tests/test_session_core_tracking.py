"""End-to-end tests for CORE-issue session tracking.

Tests the full two-way loop:
  trigger_if_needed → _save_core_reference → start_research_for_issue
  update_issue_status(resolved) → _update_sessions_for_core_resolution

All file I/O targets temp directories; no real sessions touched.
"""

import json
import os
import tempfile
import time
import uuid

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def mock_agent():
    agent = MagicMock()
    agent.active_template = "issue_handler"
    agent._session_id = "test-session-123"
    agent._tool_log = []
    agent.tool_registry.active_tools = ["update_issue_status", "read_issue", "done"]
    return agent


@pytest.fixture
def mock_task_node():
    node = MagicMock()
    node.name = "Luk Issue"
    node.status = "failed"
    return node


# ── Helpers ──────────────────────────────────────────────────────────

def _make_session_file(session_dir: str, session_id: str,
                       core_issues: list | None = None) -> str:
    """Create a minimal session JSON file and return its path.

    The session file is placed inside a ``sessions/`` subdirectory
    to match how ``_save_core_reference`` resolves its path
    (``os.path.dirname(__file__) + "/sessions/"``).
    """
    sess_dir = os.path.join(session_dir, "sessions")
    os.makedirs(sess_dir, exist_ok=True)
    path = os.path.join(sess_dir, f"{session_id}.json")
    data = {
        "id": session_id,
        "name": "test-session",
        "agent_log": [],
        "prompt": "test",
    }
    if core_issues:
        data["core_issues"] = core_issues
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path


def _read_session(session_dir: str, session_id: str) -> dict:
    path = os.path.join(session_dir, "sessions", f"{session_id}.json")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _patch_module_abspath(tmpdir):
    """Return context-managers that redirect ``agent_autoresearch.os.path.*``
    to point at *tmpdir* so session files land in ``tmpdir/sessions/``."""
    return (
        patch("agent_autoresearch.os.path.abspath", return_value=os.path.join(tmpdir, "agent_autoresearch.py")),
        patch("agent_autoresearch.os.path.dirname", return_value=tmpdir),
    )


# ── Test: _save_core_reference ──────────────────────────────────────

class TestSaveCoreReference:
    def test_writes_to_session_file(self):
        """_save_core_reference appends core_issues + agent_log entry."""
        from agent_autoresearch import _save_core_reference
        with tempfile.TemporaryDirectory() as tmpdir:
            sid = f"test-{uuid.uuid4().hex[:8]}"
            _make_session_file(tmpdir, sid)
            abspath_patch, dirname_patch = _patch_module_abspath(tmpdir)
            with abspath_patch, dirname_patch:
                _save_core_reference(sid, "CORE-999", "issue_handler", "Fix", "missing_tool")

            data = _read_session(tmpdir, sid)
            assert "core_issues" in data
            assert len(data["core_issues"]) == 1
            assert data["core_issues"][0]["id"] == "CORE-999"
            assert data["core_issues"][0]["template"] == "issue_handler"
            assert data["core_issues"][0]["phase"] == "Fix"
            assert data["core_issues"][0]["failure_type"] == "missing_tool"
            assert "created" in data["core_issues"][0]

            logs = [e for e in data["agent_log"] if e.get("level") == "CORE"]
            assert len(logs) == 1
            assert "CORE-999" in logs[0]["message"]

    def test_skips_unknown_session(self):
        """No crash when session_id is unknown."""
        from agent_autoresearch import _save_core_reference
        _save_core_reference("unknown", "CORE-001", "t", "p", "f")
        _save_core_reference("", "CORE-001", "t", "p", "f")

    def test_skips_missing_file(self):
        """No crash when session file doesn't exist."""
        from agent_autoresearch import _save_core_reference
        _save_core_reference("nonexistent-session", "CORE-001", "t", "p", "f")

    def test_accumulates_multiple_issues(self):
        """Multiple core_issues entries accumulate."""
        from agent_autoresearch import _save_core_reference
        with tempfile.TemporaryDirectory() as tmpdir:
            sid = f"test-{uuid.uuid4().hex[:8]}"
            _make_session_file(tmpdir, sid)
            abspath_patch, dirname_patch = _patch_module_abspath(tmpdir)
            with abspath_patch, dirname_patch:
                _save_core_reference(sid, "CORE-001", "t1", "p1", "f1")
                _save_core_reference(sid, "CORE-002", "t2", "p2", "f2")

            data = _read_session(tmpdir, sid)
            assert len(data["core_issues"]) == 2
            assert data["core_issues"][0]["id"] == "CORE-001"
            assert data["core_issues"][1]["id"] == "CORE-002"


# ── Test: _update_sessions_for_core_resolution ──────────────────────

class TestUpdateSessionsForCoreResolution:
    def test_updates_matching_session(self):
        """Resolution stamps resolved/resolved_by on matching core_issues ref."""
        from agent_autoresearch import _update_sessions_for_core_resolution
        with tempfile.TemporaryDirectory() as tmpdir:
            sid = f"test-{uuid.uuid4().hex[:8]}"
            _make_session_file(tmpdir, sid, core_issues=[
                {"id": "CORE-001", "template": "issue_handler", "phase": "Fix",
                 "failure_type": "missing_tool",
                 "created": "2026-06-12T12:00:00"},
            ])
            abspath_patch, dirname_patch = _patch_module_abspath(tmpdir)
            with abspath_patch, dirname_patch:
                _update_sessions_for_core_resolution("CORE-001", "resolving-session-abc")

            data = _read_session(tmpdir, sid)
            ref = data["core_issues"][0]
            assert ref["resolved"] is not None
            assert ref["resolved_by"] == "resolving-session-abc"

    def test_updates_multiple_sessions(self):
        """All sessions referencing the same core_id get updated."""
        from agent_autoresearch import _update_sessions_for_core_resolution
        with tempfile.TemporaryDirectory() as tmpdir:
            sids = []
            for i in range(3):
                sid = f"test-{uuid.uuid4().hex[:8]}"
                _make_session_file(tmpdir, sid, core_issues=[
                    {"id": "CORE-001", "template": "issue_handler", "phase": "Fix",
                     "failure_type": "missing_tool",
                     "created": "2026-06-12T12:00:00"},
                ])
                sids.append(sid)

            abspath_patch, dirname_patch = _patch_module_abspath(tmpdir)
            with abspath_patch, dirname_patch:
                _update_sessions_for_core_resolution("CORE-001", "resolver-xyz")

            for sid in sids:
                data = _read_session(tmpdir, sid)
                assert data["core_issues"][0]["resolved_by"] == "resolver-xyz"

    def test_skips_already_resolved(self):
        """Already-resolved core_issues are not overwritten."""
        from agent_autoresearch import _update_sessions_for_core_resolution
        with tempfile.TemporaryDirectory() as tmpdir:
            sid = f"test-{uuid.uuid4().hex[:8]}"
            _make_session_file(tmpdir, sid, core_issues=[
                {"id": "CORE-001", "template": "issue_handler", "phase": "Fix",
                 "failure_type": "missing_tool",
                 "created": "2026-06-12T12:00:00",
                 "resolved": "2026-06-13T08:00:00",
                 "resolved_by": "earlier-session"},
            ])
            abspath_patch, dirname_patch = _patch_module_abspath(tmpdir)
            with abspath_patch, dirname_patch:
                _update_sessions_for_core_resolution("CORE-001", "new-session")

            data = _read_session(tmpdir, sid)
            assert data["core_issues"][0]["resolved_by"] == "earlier-session"

    def test_skips_session_without_core_issues(self):
        """Sessions without core_issues key are not modified."""
        from agent_autoresearch import _update_sessions_for_core_resolution
        with tempfile.TemporaryDirectory() as tmpdir:
            sid = f"test-{uuid.uuid4().hex[:8]}"
            _make_session_file(tmpdir, sid)  # no core_issues
            abspath_patch, dirname_patch = _patch_module_abspath(tmpdir)
            with abspath_patch, dirname_patch:
                _update_sessions_for_core_resolution("CORE-001", "resolver")
            # File should still be valid (no crash, no core_issues added)
            data = _read_session(tmpdir, sid)
            assert "core_issues" not in data

    def test_handles_empty_core_id(self):
        """No crash when core_id is empty."""
        from agent_autoresearch import _update_sessions_for_core_resolution
        _update_sessions_for_core_resolution("", "resolver")

    def test_handles_missing_session_dir(self):
        """No crash when session directory doesn't exist."""
        from agent_autoresearch import _update_sessions_for_core_resolution
        abspath_patch, dirname_patch = _patch_module_abspath("/nonexistent/path")
        with abspath_patch, dirname_patch:
            _update_sessions_for_core_resolution("CORE-001", "resolver")


# ── Test: trigger_if_needed → full flow ─────────────────────────────

class TestTriggerIfNeededFlow:
    def test_full_flow_creates_issue_and_reference(self, mock_agent, mock_task_node):
        """trigger_if_needed → create_issue → _save_core_reference → research."""
        from agent_autoresearch import trigger_if_needed
        with tempfile.TemporaryDirectory() as tmpdir:
            sid = "test-flow-" + uuid.uuid4().hex[:8]
            mock_agent._session_id = sid
            _make_session_file(tmpdir, sid)
            abspath_patch, dirname_patch = _patch_module_abspath(tmpdir)

            with patch("agent_autoresearch._rate_limit_ok", return_value=True), \
                 patch("agent_issues._load_issues") as mock_load, \
                 patch("agent_issues.create_issue") as mock_create_issue, \
                 patch("agent_autoresearch.start_research_for_issue") as mock_start, \
                 abspath_patch, dirname_patch:

                mock_load.return_value = {"issues": [], "meta": {"total": 0}}
                mock_create_issue.return_value = {
                    "success": True, "issue": {"id": "CORE-777"}}

                trigger_if_needed(
                    mock_agent, mock_task_node,
                    {"read_issue{}": 1}, "kort output", []
                )

            # Verify issue was created
            mock_create_issue.assert_called_once()
            # Verify research was started
            mock_start.assert_called_once_with(mock_agent, "CORE-777")

            # Verify session file got the core_issues reference
            data = _read_session(tmpdir, sid)
            assert len(data["core_issues"]) == 1
            assert data["core_issues"][0]["id"] == "CORE-777"

    def test_skips_when_duplicate_found(self, mock_agent, mock_task_node):
        """Duplicate CORE issues suppress reference creation."""
        from agent_autoresearch import trigger_if_needed
        with tempfile.TemporaryDirectory() as tmpdir:
            sid = "test-dup-" + uuid.uuid4().hex[:8]
            mock_agent._session_id = sid
            _make_session_file(tmpdir, sid)

            with patch("agent_autoresearch._rate_limit_ok", return_value=True), \
                 patch("agent_issues._load_issues") as mock_load, \
                 patch("agent_autoresearch._save_core_reference") as mock_save, \
                 patch("agent_autoresearch.start_research_for_issue") as mock_start:

                mock_load.return_value = {
                    "issues": [
                        {"id": "CORE-001", "status": "open",
                         "title": "Manglende vaerktoej i issue_handler/Luk Issue",
                         "description": "**Template:** issue_handler\n**Fase:** Luk Issue"},
                    ]
                }
                trigger_if_needed(
                    mock_agent, mock_task_node,
                    {"read_issue{}": 1}, "kort", []
                )

            mock_save.assert_not_called()
            mock_start.assert_not_called()


# ── Test: update_issue_status → _update_sessions_for_core_resolution ─

class TestResolutionBacklink:
    def test_resolve_updates_referencing_sessions(self):
        """update_issue_status(resolved) calls _update_sessions_for_core_resolution."""
        from agent_issues import update_issue_status
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create issues file
            issues_path = os.path.join(tmpdir, "issues.json")
            issues_data = {
                "issues": [
                    {"id": "CORE-001", "status": "open",
                     "title": "Test issue",
                     "description": "**Template:** issue_handler\n**Fase:** Fix",
                     "type": "self", "severity": "medium",
                     "location": "agent_skills.py",
                     "impact": "test", "proposed_fix": "test"},
                ],
                "meta": {"total": 1},
            }
            with open(issues_path, "w", encoding="utf-8") as f:
                json.dump(issues_data, f, ensure_ascii=False, indent=2)

            # Create session file with core_issues reference
            sid = "test-resolve-" + uuid.uuid4().hex[:8]
            _make_session_file(tmpdir, sid, core_issues=[
                {"id": "CORE-001", "template": "issue_handler", "phase": "Fix",
                 "failure_type": "missing_tool",
                 "created": "2026-06-12T12:00:00"},
            ])

            agent = MagicMock()
            agent.active_template = "issue_handler"
            agent._session_id = sid
            agent._tool_log = []
            agent.issue_resolved = False
            agent.tool_registry.active_tools = ["update_issue_status"]

            abspath_patch, dirname_patch = _patch_module_abspath(tmpdir)
            with patch("agent_issues._get_issues_path", return_value=issues_path), \
                 abspath_patch, dirname_patch:

                result = update_issue_status(agent, "CORE-001", "resolved", "Fixed!")

            assert result["success"] is True
            assert agent.issue_resolved is True

            # Verify session got the backlink
            data = _read_session(tmpdir, sid)
            ref = data["core_issues"][0]
            assert ref["resolved"] is not None
            assert ref["resolved_by"] == sid
