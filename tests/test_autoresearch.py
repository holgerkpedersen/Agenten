"""Tests for agent_autoresearch.py — classification, dedup, and issue creation."""

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
def mock_agent_readonly():
    """Agent fixture with only read tools — Analyse/Laes phase."""
    agent = MagicMock()
    agent.active_template = "kodeanalyse"
    agent._session_id = "test-session-123"
    agent._tool_log = []
    agent.tool_registry.active_tools = [
        "read_location", "read_chunk", "list_chunks",
        "list_files", "list_symbols", "locate", "done",
    ]
    return agent


@pytest.fixture
def mock_agent_fix():
    """Agent fixture with read/write tools — Fix phase."""
    agent = MagicMock()
    agent.active_template = "issue_handler"
    agent._session_id = "test-session-123"
    agent._tool_log = []
    agent.tool_registry.active_tools = [
        "read_issue", "read_location", "locate",
        "edit_file", "write_file", "run_tests", "done",
    ]
    return agent


@pytest.fixture
def mock_task_node():
    node = MagicMock()
    node.name = "Luk Issue"
    node.status = "failed"
    return node


class TestClassifyFailure:
    def test_missing_tool(self, mock_agent, mock_task_node):
        from agent_autoresearch import classify_failure
        called_tools = {"read_issue{}": 1}
        ftype, evidence = classify_failure(
            mock_task_node, called_tools, [], "tekst", mock_agent)
        assert ftype == "missing_tool"
        assert "update_issue_status" in evidence.get("uncalled", [])

    def test_tool_failed(self, mock_agent, mock_task_node):
        from agent_autoresearch import classify_failure
        called_tools = {"update_issue_status{'issue_id': 'X'}": 1}
        tool_log = [
            {"tool": "update_issue_status", "success": False,
             "error": "Issue 'X' not found.", "args": {"issue_id": "X"}},
        ]
        ftype, evidence = classify_failure(
            mock_task_node, called_tools, tool_log, "", mock_agent)
        assert ftype == "tool_failed"
        assert evidence.get("tool") == "update_issue_status"

    def test_read_loop(self, mock_agent_readonly, mock_task_node):
        from agent_autoresearch import classify_failure
        called_tools = {"read_location{}": 1}
        tool_log = [{"tool": "read_location", "success": True}] * 6
        ftype, evidence = classify_failure(
            mock_task_node, called_tools, tool_log, "", mock_agent_readonly)
        assert ftype == "read_loop"
        assert evidence.get("consecutive_reads", 0) >= 5

    def test_short_output(self, mock_agent_readonly, mock_task_node):
        from agent_autoresearch import classify_failure
        ftype, evidence = classify_failure(
            mock_task_node, {}, [], "kort", mock_agent_readonly)
        assert ftype == "short_output"
        assert evidence.get("response_length") == 4

    def test_unknown_failure(self, mock_agent_readonly, mock_task_node):
        from agent_autoresearch import classify_failure
        called_tools = {"locate{}": 1}
        tool_log = [{"tool": "locate", "success": True}]
        ftype, evidence = classify_failure(
            mock_task_node, called_tools, tool_log,
            "lang tekst " * 20, mock_agent_readonly)
        assert ftype == "unknown"

    def test_status_not_checked(self, mock_agent_readonly, mock_task_node):
        from agent_autoresearch import classify_failure
        mock_task_node.status = "done"
        ftype, _ = classify_failure(mock_task_node, {}, [], "hej", mock_agent_readonly)
        assert ftype == "short_output"


class TestFindDuplicateIssue:
    def test_exact_match_same_template(self):
        from agent_autoresearch import _find_duplicate_issue
        issues = [
            {"id": "CORE-001", "status": "open",
             "title": "Manglende vaerktoej i issue_handler/Luk Issue: update_issue_status",
             "description": "**Template:** issue_handler\n**Fase:** Luk Issue"},
        ]
        dup = _find_duplicate_issue(
            "missing_tool", "issue_handler", "Luk Issue",
            {"uncalled": ["update_issue_status"]}, issues)
        assert dup == "CORE-001"

    def test_no_match_different_phase(self):
        from agent_autoresearch import _find_duplicate_issue
        issues = [
            {"id": "CORE-001", "status": "open",
             "title": "Manglende vaerktoej i issue_handler/Afklar",
             "description": "**Template:** issue_handler\n**Fase:** Afklar"},
        ]
        dup = _find_duplicate_issue(
            "missing_tool", "issue_handler", "Fix",
            {"uncalled": ["update_issue_status"]}, issues)
        assert dup is None

    def test_resolved_issue_ignored(self):
        from agent_autoresearch import _find_duplicate_issue
        issues = [
            {"id": "CORE-001", "status": "resolved",
             "title": "Manglende vaerktoej i issue_handler/Luk Issue: update_issue_status",
             "description": "**Template:** issue_handler\n**Fase:** Luk Issue"},
        ]
        dup = _find_duplicate_issue(
            "missing_tool", "issue_handler", "Luk Issue",
            {"uncalled": ["update_issue_status"]}, issues)
        assert dup is None

    def test_empty_issues_list(self):
        from agent_autoresearch import _find_duplicate_issue
        dup = _find_duplicate_issue(
            "missing_tool", "issue_handler", "Luk Issue", {}, [])
        assert dup is None


class TestBuildFailureReport:
    def test_missing_tool_report(self):
        from agent_autoresearch import _build_failure_report, FAILURE_MISSING_TOOL
        agent = MagicMock(); agent.active_template = "issue_handler"
        task_node = MagicMock(); task_node.name = "Luk Issue"
        report = _build_failure_report(
            agent, task_node, FAILURE_MISSING_TOOL,
            {"required": ["update_issue_status"],
             "called": ["read_issue"], "uncalled": ["update_issue_status"]})
        assert "update_issue_status" in report["title"]
        assert "issue_handler" in report["location"]

    def test_tool_failed_report(self):
        from agent_autoresearch import _build_failure_report, FAILURE_TOOL_FAILED
        agent = MagicMock(); agent.active_template = "bugfix"
        task_node = MagicMock(); task_node.name = "Implementering"
        report = _build_failure_report(
            agent, task_node, FAILURE_TOOL_FAILED,
            {"tool": "edit_file", "attempts": 3,
             "last_error": "old_text not found",
             "last_args": "{'path': 'test.py'}"})
        assert "edit_file" in report["title"]
        assert "bugfix" in report["location"]

    def test_unknown_report(self):
        from agent_autoresearch import _build_failure_report, FAILURE_UNKNOWN
        agent = MagicMock(); agent.active_template = "refactor"
        task_node = MagicMock(); task_node.name = "Ekstraher"
        report = _build_failure_report(
            agent, task_node, FAILURE_UNKNOWN,
            {"called_tools": [], "response_length": 0})
        assert "Uforklaret" in report["title"]
        assert "manuel analyse" in report["proposed_fix"]


class TestTriggerIfNeeded:
    def test_skips_when_status_not_failed(self, mock_agent, mock_task_node):
        from agent_autoresearch import trigger_if_needed
        mock_task_node.status = "done"
        with patch("agent_autoresearch._rate_limit_ok") as mock_rate:
            trigger_if_needed(mock_agent, mock_task_node, {}, "ok", [])
            mock_rate.assert_not_called()

    def test_skips_on_rate_limit(self, mock_agent, mock_task_node):
        from agent_autoresearch import trigger_if_needed
        with patch("agent_autoresearch._rate_limit_ok", return_value=False), \
             patch("agent_autoresearch._find_duplicate_issue") as mock_dedup:
            trigger_if_needed(mock_agent, mock_task_node,
                              {"read_issue{}": 1}, "", [])
            mock_dedup.assert_not_called()

    def test_skips_on_duplicate(self, mock_agent, mock_task_node):
        from agent_autoresearch import trigger_if_needed
        with patch("agent_autoresearch._rate_limit_ok", return_value=True), \
             patch("agent_issues._load_issues") as mock_load:
            mock_load.return_value = {
                "issues": [
                    {"id": "CORE-001", "status": "open",
                     "title": "Manglende vaerktoej i issue_handler/Luk Issue: update_issue_status",
                     "description": "**Template:** issue_handler\n**Fase:** Luk Issue"},
                ]
            }
            with patch("agent_autoresearch._async_create_issue") as mock_create:
                trigger_if_needed(mock_agent, mock_task_node,
                                  {"read_issue{}": 1}, "", [])
                mock_create.assert_not_called()

    def test_creates_issue_on_novel_failure(self, mock_agent, mock_task_node):
        from agent_autoresearch import trigger_if_needed
        with patch("agent_autoresearch._rate_limit_ok", return_value=True), \
             patch("agent_issues._load_issues") as mock_load:
            mock_load.return_value = {"issues": []}
            with patch("agent_autoresearch._async_create_issue") as mock_create:
                trigger_if_needed(mock_agent, mock_task_node,
                                  {"read_issue{}": 1}, "", [])
                mock_create.assert_called_once()


class TestAsyncCreateIssue:
    def test_creates_issue_successfully(self, mock_agent):
        from agent_autoresearch import _async_create_issue
        report = {"title": "t", "type": "bug", "severity": "medium",
                  "description": "d", "location": "l", "impact": "i",
                  "proposed_fix": "f", "acceptance_criteria": "a"}
        with patch("agent_issues.create_issue") as mock_create:
            mock_create.return_value = {
                "success": True, "issue": {"id": "CORE-042"}, "existing": False}
            _async_create_issue(mock_agent, MagicMock(), "missing_tool",
                                 {"uncalled": ["edit_file"]}, report)
            mock_create.assert_called_once_with(
                mock_agent, title="t", type="bug", severity="medium",
                description="d", location="l", impact="i",
                proposed_fix="f", acceptance_criteria="a")

    def test_handles_create_failure(self, mock_agent):
        from agent_autoresearch import _async_create_issue
        report = {"title": "t", "type": "bug", "severity": "m",
                  "description": "d", "location": "l", "impact": "i",
                  "proposed_fix": "f", "acceptance_criteria": "a"}
        with patch("agent_issues.create_issue") as mock_create:
            mock_create.return_value = {"success": False, "error": "bang"}
            _async_create_issue(mock_agent, MagicMock(), "missing_tool",
                                 {"uncalled": ["x"]}, report)
            mock_create.assert_called_once()


class TestRateLimit:
    def test_rate_limit_ok_first_call(self):
        from agent_autoresearch import _rate_limit_ok, _last_analysis
        _last_analysis.clear()
        assert _rate_limit_ok("session-1") is True

    def test_rate_limit_blocks_within_window(self):
        from agent_autoresearch import _rate_limit_ok, _last_analysis
        _last_analysis.clear()
        _rate_limit_ok("session-1")
        assert _rate_limit_ok("session-1") is False

    def test_rate_limit_allows_different_sessions(self):
        from agent_autoresearch import _rate_limit_ok, _last_analysis
        _last_analysis.clear()
        _rate_limit_ok("session-1")
        assert _rate_limit_ok("session-2") is True
