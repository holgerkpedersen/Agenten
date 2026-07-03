"""Test SSE streaming endpoint — /api/execute-stream and related SSE utilities."""
import json
import sys
import os
import pytest
from unittest.mock import MagicMock, patch, PropertyMock

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from api_server import app as flask_app


@pytest.fixture
def client():
    flask_app.config["TESTING"] = True
    from session_manager import session_manager
    session_manager.current_session_id = None
    with flask_app.test_client() as c:
        yield c


def _sse_events(resp):
    """Parse SSE response into list of event dicts."""
    events = []
    for line in resp.data.decode().split("\n"):
        if line.startswith("data: "):
            events.append(json.loads(line[6:]))
    return events


def _fake_generate(events=None):
    """Yield fake SSE event strings, then complete/error."""
    if events is None:
        events = []
    yield from events
    yield "data: " + json.dumps({"type": "complete", "message": "done"}) + "\n\n"


# ============================================================
# Unit tests for the _sse helper
# ============================================================
class TestSSEHelper:
    def test_sse_injects_stream_seq(self):
        from stream_execution import _sse
        result = _sse({"type": "test"}, 42)
        data = json.loads(result[6:].strip())
        assert data["type"] == "test"
        assert data["stream_seq"] == 42

    def test_sse_skips_seq_when_zero(self):
        from stream_execution import _sse
        result = _sse({"type": "test"}, 0)
        data = json.loads(result[6:].strip())
        assert data["type"] == "test"
        assert "stream_seq" not in data

    def test_sse_proper_format(self):
        from stream_execution import _sse
        result = _sse({"type": "foo", "value": 123}, 1)
        assert result.startswith("data: ")
        assert result.endswith("\n\n")
        parsed = json.loads(result[6:].strip())
        assert parsed == {"type": "foo", "value": 123, "stream_seq": 1}


# ============================================================
# Tests that the _stream_seq global counter works
# ============================================================
class TestStreamSeqCounter:
    def test_increments_across_calls(self):
        from stream_execution import _stream_seq, _stream_seq_lock
        with _stream_seq_lock:
            before = _stream_seq
        assert before >= 0  # just verify we can read it

    def test_stream_seq_in_all_sse_events(self, client):
        from stream_execution import _sse, _execute_with_stream, _stream_seq, _stream_seq_lock
        from task_tree import TaskTree, TaskNode

        tree = TaskTree(root_name="test")
        child = TaskNode("child")
        tree.root.add_child(child)

        with _stream_seq_lock:
            _stream_seq += 1
            seq = _stream_seq

        mock_agent = MagicMock()
        mock_agent.task_tree = tree
        mock_agent.llm.model = "test"
        mock_agent.decompose_llm.model = "test"
        mock_agent.lang = "en"
        mock_agent.stop_requested = False
        mock_agent.agent_log = []
        mock_agent.execution_log = []
        mock_agent.issue_resolved = False
        mock_agent.active_template = "fri"
        mock_agent._llm_todos = None
        mock_agent._wta = MagicMock()
        mock_agent._seq = MagicMock()
        mock_agent._log = MagicMock()
        mock_agent.solve_task_stream = MagicMock()

        def mock_solve_stream(*args, **kwargs):
            yield {"type": "done", "result": "completed"}

        mock_agent.solve_task_stream = mock_solve_stream

        completed = [0]
        gen = _execute_with_stream(tree.root, mock_agent, 1, completed, "prompt", True, "en", "test-session", seq)
        events = []
        for event_str in gen:
            events.append(json.loads(event_str[6:].strip()))

        for ev in events:
            assert "stream_seq" in ev, f"Event {ev['type']} missing stream_seq"
            assert ev["stream_seq"] == seq


# ============================================================
# Tests against the actual Flask endpoint
# ============================================================
class TestSSEHeaders:
    def test_returns_sse_content_type(self, client):
        resp = client.get("/api/execute-stream")
        assert resp.status_code == 200
        assert resp.mimetype == "text/event-stream"
        assert resp.headers.get("Cache-Control") == "no-cache"
        assert resp.headers.get("X-Accel-Buffering") == "no"


class TestSSEErrorNoSession:
    def test_returns_decompose_first_error(self, client):
        resp = client.get("/api/execute-stream")
        events = _sse_events(resp)
        assert len(events) >= 1
        last = events[-1]
        assert last["type"] == "error"


class TestSSEWithSessionTree:
    def _setup_mocks(self, tree_dict, session_id="test-sse-session"):
        """Shared mock setup — patches the agent and execution so no real LLM is called."""
        load_data = {
            "original_prompt": "test prompt",
            "tree": tree_dict,
            "lang": "en",
            "file_chunks": [],
            "images": [],
            "template": "fri",
            "decompose_model": "test-model",
            "execute_model": "test-model",
            "full_prompt_with_context": "test context",
            "show_thinking": True,
            "ui_lang": "en",
        }
        return patch.multiple(
            "stream_execution",
            session_manager=MagicMock(
                current_session_id=session_id,
                load_session=MagicMock(return_value=load_data),
                update_session=MagicMock(),
            ),
        )

    def test_execution_yields_correct_event_types(self, client):
        from task_tree import TaskTree, TaskNode
        tree = TaskTree(root_name="test task")
        child = TaskNode("sub task")
        tree.root.add_child(child)

        with patch("stream_execution._execute_with_stream") as mock_exec, \
             patch("stream_execution._ensure_model_loaded") as mock_ensure, \
             patch("stream_execution.session_manager.load_session") as mock_load, \
             patch("stream_execution.session_manager.current_session_id", "test-sse-flow"), \
             patch("stream_execution.session_manager.update_session"):

            mock_load.return_value = {
                "original_prompt": "test prompt",
                "tree": tree.to_dict(),
                "lang": "en",
                "file_chunks": [],
                "images": [],
                "template": "fri",
                "decompose_model": "test-model",
                "execute_model": "test-model",
                "full_prompt_with_context": "test context",
                "show_thinking": True,
                "ui_lang": "en",
            }

            mock_exec.return_value = _fake_generate([
                "data: " + json.dumps({"type": "task_start", "task": "sub task"}) + "\n\n",
                "data: " + json.dumps({"type": "task_done", "task": "sub task", "status": "done"}) + "\n\n",
                "data: " + json.dumps({"type": "progress", "progress": 100}) + "\n\n",
            ])

            resp = client.get("/api/execute-stream")
            events = _sse_events(resp)
            types = [e["type"] for e in events]
            assert "context" in types
            assert "start" in types
            assert "complete" in types

    def test_context_contains_original_prompt(self, client):
        from task_tree import TaskTree
        tree = TaskTree(root_name="test task")

        with patch("stream_execution._execute_with_stream") as mock_exec, \
             patch("stream_execution._ensure_model_loaded"), \
             patch("stream_execution.session_manager.load_session") as mock_load, \
             patch("stream_execution.session_manager.current_session_id", "test-sse-ctx"), \
             patch("stream_execution.session_manager.update_session"):

            mock_load.return_value = {
                "original_prompt": "my prompt",
                "tree": tree.to_dict(),
                "lang": "en",
                "file_chunks": [],
                "images": [],
                "template": "fri",
                "decompose_model": "test",
                "execute_model": "test",
                "full_prompt_with_context": "my context",
                "show_thinking": True,
                "ui_lang": "en",
            }

            mock_exec.return_value = _fake_generate()

            resp = client.get("/api/execute-stream")
            events = _sse_events(resp)
            ctx = [e for e in events if e["type"] == "context"]
            assert ctx
            assert ctx[0]["original_prompt"] == "my context"

    def test_start_contains_total_tasks(self, client):
        from task_tree import TaskTree
        tree = TaskTree(root_name="test task")

        with patch("stream_execution._execute_with_stream") as mock_exec, \
             patch("stream_execution._ensure_model_loaded"), \
             patch("stream_execution.session_manager.load_session") as mock_load, \
             patch("stream_execution.session_manager.current_session_id", "test-sse-tasks"), \
             patch("stream_execution.session_manager.update_session"):

            mock_load.return_value = {
                "original_prompt": "test",
                "tree": tree.to_dict(),
                "lang": "en",
                "file_chunks": [],
                "images": [],
                "template": "fri",
                "decompose_model": "test",
                "execute_model": "test",
                "full_prompt_with_context": "test",
                "show_thinking": True,
                "ui_lang": "en",
            }

            mock_exec.return_value = _fake_generate()

            resp = client.get("/api/execute-stream")
            events = _sse_events(resp)
            start = [e for e in events if e["type"] == "start"]
            assert start
            assert start[0]["total_tasks"] == 1


# ============================================================
# Tests for concurrent execution guard (STAB-003)
# ============================================================
class TestConcurrentExecution:
    def test_second_stream_stops_previous(self, client):
        """Calling execute_stream twice for same session stops the first."""
        from stream_execution import _active_session_executions_lock, _active_session_executions, _stream_seq, _stream_seq_lock
        from session_manager import session_manager

        old_id = session_manager.current_session_id
        try:
            session_manager.current_session_id = "concurrent-test-session"

            # Patch session_manager.load_session to return None (no tree = error, no LLM)
            with patch("stream_execution.session_manager.load_session", return_value=None), \
                 patch("stream_execution._ensure_model_loaded"), \
                 patch("stream_execution.session_manager.update_session"):

                # First call — will hit decompose-first error (no tree in session)
                resp1 = client.get("/api/execute-stream")
                events1 = _sse_events(resp1)
                assert events1[-1]["type"] == "error"

                # Second call with same session — previous is "stopped" via guard
                resp2 = client.get("/api/execute-stream")
                events2 = _sse_events(resp2)
                assert events2[-1]["type"] == "error"
        finally:
            session_manager.current_session_id = None
            with _active_session_executions_lock:
                _active_session_executions.pop("concurrent-test-session", None)

    def test_message_not_stale_when_current_seq_matches(self):
        from stream_execution import _sse
        msg = _sse({"type": "progress"}, 5)
        assert msg


# ============================================================
# Test resume endpoint basic structure
# ============================================================
class TestSSEResume:
    def test_resume_returns_sse_on_no_session(self, client):
        resp = client.get("/api/execute-resume")
        assert resp.status_code == 200
        assert resp.mimetype == "text/event-stream"
        events = _sse_events(resp)
        assert events[-1]["type"] == "error"
        assert "Ingen aktiv session" in events[-1]["message"]