"""Test _check_required_tools and related auto-completion logic.

Regression: session a07633cc Test phase auto-completed via run_tests (tests
passed) but was then marked as failed because edit_file wasn't called.
The fix: when agent.issue_resolved is True (set by run_tests auto-complete),
_check_required_tools should not require edit_file/write_file.

Also tests _get_max_iterations for per-template/per-phase iteration budgets.
"""

import os
import sys
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


class FakeToolRegistry:
    def __init__(self, active):
        self.active_tools = active


class FakeAgent:
    def __init__(self, active_tools, issue_resolved=False, template=""):
        self.tool_registry = FakeToolRegistry(active_tools)
        self.lang = "da"
        self.issue_resolved = issue_resolved
        self.active_template = template
        self._tool_log = []


from agent_tasks import _check_required_tools, _get_max_iterations  # noqa: E402
import config  # noqa: E402


class TestCheckRequiredToolsRespectsIssueResolved(unittest.TestCase):
    """Bug regression: Test phase with passing run_tests should not require edit_file."""

    def test_test_phase_with_resolved_issue(self):
        """run_tests passed, agent.issue_resolved=True → no missing tools."""
        agent = FakeAgent(
            active_tools=["run_tests", "edit_file", "update_issue_status"],
            issue_resolved=True,
        )
        called = {"run_tests{test_path:''}": 1}
        self.assertIsNone(_check_required_tools(agent, called, "Test"))

    def test_test_phase_without_resolved_issue(self):
        """run_tests passed but issue_resolved=False → edit_file still required (old behaviour)."""
        agent = FakeAgent(
            active_tools=["run_tests", "edit_file", "update_issue_status"],
            issue_resolved=False,
        )
        called = {"run_tests{test_path:''}": 1}
        result = _check_required_tools(agent, called, "Test")
        self.assertIsNotNone(result)
        self.assertIn("edit_file", result)


class TestCheckRequiredToolsOtherPhases(unittest.TestCase):
    """Other phases should still enforce required tools."""

    def test_ekstraher_requires_write_file(self):
        agent = FakeAgent(
            active_tools=["read_location", "locate", "write_file"],
            issue_resolved=False,
        )
        called = {"read_location{filepath:'api_server.py'}": 1}
        result = _check_required_tools(agent, called, "Ekstraher")
        self.assertIsNotNone(result)
        self.assertIn("write_file", result)

    def test_ekstraher_passes_with_write_file(self):
        agent = FakeAgent(
            active_tools=["read_location", "locate", "write_file"],
            issue_resolved=False,
        )
        called = {"write_file{path:'routes.py'}": 1}
        self.assertIsNone(_check_required_tools(agent, called, "Ekstraher"))

    def test_update_issue_status_clears_edit_requirement(self):
        """If LLM called update_issue_status, edit_file is no longer required."""
        agent = FakeAgent(
            active_tools=["run_tests", "edit_file", "update_issue_status"],
            issue_resolved=False,
        )
        called = {
            "run_tests{test_path:''}": 1,
            "update_issue_status{issue_id:'REFAC-001'}": 1,
        }
        self.assertIsNone(_check_required_tools(agent, called, "Test"))

    def test_verifikation_phase_optional_edits(self):
        """Verifikation phase — edit_file is optional (only for test-fix cycles)."""
        agent = FakeAgent(
            active_tools=["run_tests", "edit_file"],
            issue_resolved=False,
        )
        called = {"run_tests{test_path:''}": 1}
        self.assertIsNone(_check_required_tools(agent, called, "Verifikation (Green)"))

    def test_opdatering_phase_requires_update_issue(self):
        """Opdatering phase — update_issue_status is required."""
        agent = FakeAgent(
            active_tools=["edit_file", "update_issue_status"],
            issue_resolved=False,
        )
        called = {"edit_file{path:'api_server.py'}": 1}
        result = _check_required_tools(agent, called, "Opdatér")
        self.assertIsNotNone(result)
        self.assertIn("update_issue_status", result)


class TestGetMaxIterations(unittest.TestCase):
    """Per-template/per-phase iteration budgets."""

    def test_refactor_analyse(self):
        agent = FakeAgent([], template="refactor")
        self.assertEqual(_get_max_iterations(agent, "Analyse"), 12)

    def test_refactor_plan(self):
        agent = FakeAgent([], template="refactor")
        self.assertEqual(_get_max_iterations(agent, "Plan"), 8)

    def test_refactor_ekstraher_higher_budget(self):
        """Ekstraher needs 20 turns (34+ symbols, multiple modules)."""
        agent = FakeAgent([], template="refactor")
        self.assertEqual(_get_max_iterations(agent, "Ekstraher"), 20)

    def test_refactor_opdater_higher_budget(self):
        agent = FakeAgent([], template="refactor")
        self.assertEqual(_get_max_iterations(agent, "Opdatér"), 20)

    def test_refactor_test(self):
        agent = FakeAgent([], template="refactor")
        self.assertEqual(_get_max_iterations(agent, "Test"), 8)

    def test_bugfix_implementering(self):
        agent = FakeAgent([], template="bugfix")
        self.assertEqual(_get_max_iterations(agent, "Implementering"), 12)

    def test_case_insensitive_phase(self):
        agent = FakeAgent([], template="refactor")
        for variant in ("Ekstraher", "ekstraher", "EKSTRAHER"):
            self.assertEqual(_get_max_iterations(agent, variant), 20)

    def test_unknown_template_falls_back(self):
        agent = FakeAgent([], template="nonexistent_template")
        self.assertEqual(_get_max_iterations(agent, "Plan"), config.MAX_TASK_ITERATIONS)

    def test_unknown_phase_falls_back(self):
        """Phase not in template limits → use config default."""
        agent = FakeAgent([], template="refactor")
        self.assertEqual(
            _get_max_iterations(agent, "NonExistentPhase"),
            config.MAX_TASK_ITERATIONS,
        )


if __name__ == "__main__":
    unittest.main()

