"""Test SSE streaming endpoint — /api/execute-stream."""
import json
import sys
import os
import pytest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from api_server import app as flask_app


@pytest.fixture
def client():
    flask_app.config["TESTING"] = True
    import api_server
    api_server.current_session_id = None
    with flask_app.test_client() as c:
        yield c


def _sse_events(resp):
    """Parse SSE response into list of event dicts."""
    events = []
    for line in resp.data.decode().split("\n"):
        if line.startswith("data: "):
            events.append(json.loads(line[6:]))
    return events


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
    def test_full_execution_flow(self, client):
        from task_tree import TaskTree, TaskNode

        tree = TaskTree(root_name="test task")
        child = TaskNode("sub task")
        tree.root.add_child(child)

        with patch("api_server.current_session_id", "test-sse-session"), \
             patch("api_server.session_manager.load_session") as mock_load, \
             patch("api_server.agent") as mock_agent:

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

            mock_agent.llm = MagicMock()
            mock_agent.llm.model = "test-model"
            mock_agent.decompose_llm = MagicMock()
            mock_agent.searcher = None

            mock_llm = MagicMock()
            mock_llm.model = "test-model"

            def fake_stream(messages, temperature=0.3, max_tokens=4096, images=None, tools=None):
                yield '<<<DONE>>>{"result":"completed"}<<<END>>>'

            mock_llm.generate_stream = fake_stream
            mock_agent.llm = mock_llm

            resp = client.get("/api/execute-stream")
            events = _sse_events(resp)

            types = [e["type"] for e in events]
            assert "context" in types
            assert "start" in types
            assert "complete" in types

            ctx = [e for e in events if e["type"] == "context"]
            assert ctx[0]["original_prompt"] == "test context"

            start = [e for e in events if e["type"] == "start"]
            assert start[0]["total_tasks"] == 2

    def test_execution_with_stop(self, client):
        from task_tree import TaskTree, TaskNode

        tree = TaskTree(root_name="test task")

        with patch("api_server.current_session_id", "test-stop-session"), \
             patch("api_server.session_manager.load_session") as mock_load, \
             patch("api_server.agent") as mock_agent:

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

            mock_agent.llm = MagicMock()
            mock_agent.llm.model = "test"
            mock_agent.decompose_llm = MagicMock()
            mock_agent.searcher = None

            mock_llm = MagicMock()
            mock_llm.model = "test"
            call_count = 0

            def fake_stream(messages, temperature=0.3, max_tokens=4096, images=None, tools=None):
                nonlocal call_count
                call_count += 1
                if call_count > 1:
                    yield '<<<DONE>>>{"result":"done"}<<<END>>>'
                else:
                    yield '<<<DONE>>>{"result":"completed"}<<<END>>>'

            mock_llm.generate_stream = fake_stream
            mock_agent.llm = mock_llm

            resp = client.get("/api/execute-stream")
            events = _sse_events(resp)
            types = [e["type"] for e in events]
            assert "complete" in types or "error" in types
