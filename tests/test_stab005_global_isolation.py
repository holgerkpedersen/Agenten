"""STAB-005: Verifies that _reset_shared_globals autouse fixture clears shared
global state between tests. Without it, earlier tests leaking state would affect
later tests."""
import pytest


class TestDirtyGlobals:
    """First test class dirties all globals. The autouse fixture in conftest.py
    must reset them before TestCleanGlobals runs."""

    @pytest.fixture(autouse=True)
    def _dirty_globals(self):
        from session_manager import session_manager, agent
        from stream_execution import _active_session_executions, _active_session_executions_lock

        session_manager.current_session_id = "dirty-session-id"
        agent.task_tree = "dirty-tree"
        agent.agent_log = [{"msg": "dirty"}]
        agent.execution_log = [{"msg": "dirty"}]
        agent.file_chunks = {"dirty": "data"}
        agent.issue_resolved = True
        agent.active_template = "dirty-template"
        agent.current_phase = "dirty-phase"

        with _active_session_executions_lock:
            _active_session_executions["dirty-key"] = True

    def test_dirtied(self):
        from session_manager import session_manager, agent
        from stream_execution import _active_session_executions, _active_session_executions_lock

        assert session_manager.current_session_id == "dirty-session-id"
        assert agent.task_tree == "dirty-tree"
        assert agent.agent_log == [{"msg": "dirty"}]
        assert agent.execution_log == [{"msg": "dirty"}]
        assert agent.file_chunks == {"dirty": "data"}
        assert agent.issue_resolved is True
        assert agent.active_template == "dirty-template"
        assert agent.current_phase == "dirty-phase"

        with _active_session_executions_lock:
            assert _active_session_executions.get("dirty-key") is True


class TestCleanGlobals:
    """Second test class: autouse fixture must have reset everything."""

    def test_session_id_is_none(self):
        from session_manager import session_manager
        assert session_manager.current_session_id is None

    def test_agent_tree_is_none(self):
        from session_manager import agent
        assert agent.task_tree is None

    def test_agent_log_is_empty(self):
        from session_manager import agent
        assert agent.agent_log == []

    def test_execution_log_is_empty(self):
        from session_manager import agent
        assert agent.execution_log == []

    def test_file_chunks_is_empty(self):
        from session_manager import agent
        assert agent.file_chunks == {}

    def test_issue_resolved_is_false(self):
        from session_manager import agent
        assert agent.issue_resolved is False

    def test_active_template_is_none(self):
        from session_manager import agent
        assert agent.active_template is None

    def test_current_phase_is_none(self):
        from session_manager import agent
        assert agent.current_phase is None

    def test_active_executions_is_empty(self):
        from stream_execution import _active_session_executions, _active_session_executions_lock
        with _active_session_executions_lock:
            assert _active_session_executions == {}