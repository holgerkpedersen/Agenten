"""Tests for auto-research sub-session execution (_execute_autoresearch_issue, _run_full_test_suite)."""

from unittest.mock import MagicMock, patch, call
from subprocess import TimeoutExpired
import pytest


@pytest.fixture
def mock_agent():
    agent = MagicMock()
    agent.active_template = "issue_handler"
    agent.original_prompt = "Original prompt med BUG-001"
    agent._session_id = "test-session-456"
    agent._tool_log = []
    agent._autoresearch_depth = 0
    agent.file_chunks = {"file_test.py": ["chunk1"]}
    agent.file_context = None
    agent.full_prompt_with_context = "full context"
    agent.task_tree = None
    agent.agent_log = []
    return agent


class TestRunFullTestSuite:
    def test_returns_true_on_success(self):
        from agent_tasks import _run_full_test_suite
        agent = MagicMock()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = "passed"
            result = _run_full_test_suite(agent)
            assert result is True

    def test_returns_false_on_failure(self):
        from agent_tasks import _run_full_test_suite
        agent = MagicMock()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 1
            mock_run.return_value.stdout = "FAILED"
            result = _run_full_test_suite(agent)
            assert result is False

    def test_returns_false_on_timeout(self):
        from agent_tasks import _run_full_test_suite
        agent = MagicMock()
        with patch("subprocess.run", side_effect=TimeoutExpired("cmd", 120)):
            result = _run_full_test_suite(agent)
            assert result is False

    def test_returns_false_on_exception(self):
        from agent_tasks import _run_full_test_suite
        agent = MagicMock()
        with patch("subprocess.run", side_effect=FileNotFoundError):
            result = _run_full_test_suite(agent)
            assert result is False


class TestExecuteAutoresearchIssue:
    def test_returns_false_when_issue_not_found(self, mock_agent):
        from agent_tasks import _execute_autoresearch_issue
        with patch("agent_issues._load_issues", return_value={"issues": []}):
            gen = _execute_autoresearch_issue(mock_agent, "CORE-999")
            events = list(gen)
            assert events == []
            assert mock_agent.active_template == "issue_handler"

    def test_yields_start_and_complete_events(self, mock_agent):
        from agent_tasks import _execute_autoresearch_issue
        issue = {
            "id": "CORE-042", "title": "Test issue",
            "description": "Beskrivelse", "location": "test.py",
            "impact": "Mellem", "proposed_fix": "Ret koden"
        }
        with patch("agent_issues._load_issues", return_value={"issues": [issue]}), \
             patch("agent_file_context._auto_load_issue_files"), \
             patch("agent_file_context._auto_load_location_file"), \
             patch.object(mock_agent, "solve_task_stream", return_value=[]):
            gen = _execute_autoresearch_issue(mock_agent, "CORE-042")
            events = list(gen)
        # Should yield at least start + complete events
        types = [e.get("type") for e in events]
        assert "autoresearch" in types
        start = [e for e in events if e.get("action") == "start"]
        complete = [e for e in events if e.get("action") == "complete"]
        assert len(start) >= 1
        assert len(complete) >= 1
        assert start[0]["issue_id"] == "CORE-042"

    def test_saves_and_restores_state(self, mock_agent):
        from agent_tasks import _execute_autoresearch_issue
        issue = {
            "id": "CORE-043", "title": "State test",
            "description": "", "location": "",
            "impact": "", "proposed_fix": ""
        }
        with patch("agent_issues._load_issues", return_value={"issues": [issue]}), \
             patch("agent_file_context._auto_load_issue_files"), \
             patch("agent_file_context._auto_load_location_file"), \
             patch.object(mock_agent, "solve_task_stream", return_value=[]):
            list(_execute_autoresearch_issue(mock_agent, "CORE-043"))
        # State should be restored
        assert mock_agent.active_template == "issue_handler"
        assert mock_agent.original_prompt == "Original prompt med BUG-001"
        assert mock_agent._autoresearch_depth == 0

    def test_sets_depth_during_execution(self, mock_agent):
        from agent_tasks import _execute_autoresearch_issue
        issue = {
            "id": "CORE-044", "title": "Depth test",
            "description": "", "location": "",
            "impact": "", "proposed_fix": ""
        }
        depths_seen = []
        def track_depth(*args, **kwargs):
            depths_seen.append(mock_agent._autoresearch_depth)
            return iter([])

        with patch("agent_issues._load_issues", return_value={"issues": [issue]}), \
             patch("agent_file_context._auto_load_issue_files"), \
             patch("agent_file_context._auto_load_location_file"), \
             patch.object(mock_agent, "solve_task_stream", track_depth):
            list(_execute_autoresearch_issue(mock_agent, "CORE-044"))
        # During execution, depth should be incremented
        assert depths_seen, "solve_task_stream should have been called"
        for d in depths_seen:
            assert d == 1, f"Expected depth=1 during execution, got {d}"

    def test_respects_depth_limit(self, mock_agent):
        from agent_tasks import _execute_autoresearch_issue
        mock_agent._autoresearch_depth = 2
        issue = {
            "id": "CORE-045", "title": "Depth limit",
            "description": "", "location": "",
            "impact": "", "proposed_fix": ""
        }
        with patch("agent_issues._load_issues", return_value={"issues": [issue]}):
            gen = _execute_autoresearch_issue(mock_agent, "CORE-045")
            result = list(gen)
        # Should return False immediately without calling solve_task_stream
        assert any(e.get("action") == "error" for e in result), \
            "Should yield an error event at depth limit"

    def test_phase_names_from_template_phase_checks(self, mock_agent):
        from agent_tasks import _execute_autoresearch_issue
        from agent_phase_checks import TEMPLATE_PHASE_CHECKS
        issue = {
            "id": "CORE-046", "title": "Phase names",
            "description": "", "location": "",
            "impact": "", "proposed_fix": ""
        }
        expected = list(TEMPLATE_PHASE_CHECKS.get("selvforbedring", {}).keys())
        assert expected, "selvforbedring should have phase checks defined"

        phases_seen = []
        class FakeNode:
            status = "pending"
            name = ""
        def track_phase(node, prompt):
            phases_seen.append(node.name)
            return iter([])

        with patch("agent_issues._load_issues", return_value={"issues": [issue]}), \
             patch("agent_file_context._auto_load_issue_files"), \
             patch("agent_file_context._auto_load_location_file"), \
             patch.object(mock_agent, "solve_task_stream", track_phase):
            list(_execute_autoresearch_issue(mock_agent, "CORE-046"))
        assert phases_seen == expected, \
            f"Expected phases {expected}, got {phases_seen}"


class TestTriggerDepthGuard:
    def test_trigger_if_needed_refuses_in_sub_session(self, mock_agent):
        """trigger_if_needed should return None when _autoresearch_depth > 0."""
        from agent_autoresearch import trigger_if_needed
        mock_agent._autoresearch_depth = 1
        mock_agent.autoresearch_enabled = True
        task_node = MagicMock()
        task_node.status = "failed"
        task_node.name = "Ret"
        with patch("agent_autoresearch._rate_limit_ok", return_value=True), \
             patch("agent_issues._load_issues", return_value={"issues": [], "meta": {"total": 0}}), \
             patch("agent_issues.create_issue") as mock_create:
            result = trigger_if_needed(mock_agent, task_node, {}, "", [])
            assert result is None, \
                "Should refuse at depth > 0"
            mock_create.assert_not_called()

    def test_trigger_if_needed_proceeds_at_depth_zero(self, mock_agent):
        """trigger_if_needed should proceed normally when _autoresearch_depth == 0."""
        from agent_autoresearch import trigger_if_needed
        mock_agent._autoresearch_depth = 0
        mock_agent.autoresearch_enabled = True
        task_node = MagicMock()
        task_node.status = "failed"
        task_node.name = "Ret"
        with patch("agent_autoresearch._rate_limit_ok", return_value=True), \
             patch("agent_issues._load_issues", return_value={"issues": [], "meta": {"total": 0}}), \
             patch("agent_issues.create_issue", return_value={"success": True, "issue": {"id": "CORE-777"}}):
            result = trigger_if_needed(mock_agent, task_node, {}, "", [])
            assert result == "CORE-777", \
                "Should create issue and return issue_id at depth 0"

    def test_trigger_if_needed_proceeds_with_mock_depth(self, mock_agent):
        """MagicMock _autoresearch_depth (not int) should be treated as depth 0."""
        from agent_autoresearch import trigger_if_needed
        mock_agent._autoresearch_depth = MagicMock()  # Simulate unset attribute on mock
        mock_agent.autoresearch_enabled = True
        task_node = MagicMock()
        task_node.status = "failed"
        task_node.name = "Ret"
        with patch("agent_autoresearch._rate_limit_ok", return_value=True), \
             patch("agent_issues._load_issues", return_value={"issues": [], "meta": {"total": 0}}), \
             patch("agent_issues.create_issue", return_value={"success": True, "issue": {"id": "CORE-888"}}):
            result = trigger_if_needed(mock_agent, task_node, {}, "", [])
            assert result == "CORE-888", \
                "MagicMock depth should not block — treated as unset"
