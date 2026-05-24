"""Test session_manager.py — persistence and knowledge extraction."""
import pytest
import json
import os
from session_manager import SessionManager


class TestSessionManagerBasics:
    def test_create_session(self, session_manager):
        sid, data = session_manager.create_session("Test Session")
        assert sid is not None
        assert data["name"] == "Test Session"
        assert "prompt_history" in data
        assert data["prompt_history"] == []

    def test_save_and_load(self, session_manager):
        sid, _ = session_manager.create_session("My Session")
        loaded = session_manager.load_session(sid)
        assert loaded is not None
        assert loaded["name"] == "My Session"

    def test_load_nonexistent(self, session_manager):
        result = session_manager.load_session("doesnotexist")
        assert result is None

    def test_list_sessions(self, session_manager):
        session_manager.create_session("Session 1")
        session_manager.create_session("Session 2")
        sessions = session_manager.list_sessions()
        assert len(sessions) >= 2
        names = [s["name"] for s in sessions]
        assert "Session 1" in names
        assert "Session 2" in names

    def test_rename_session(self, session_manager):
        sid, _ = session_manager.create_session("Old Name")
        result = session_manager.rename_session(sid, "New Name")
        assert result is True
        loaded = session_manager.load_session(sid)
        assert loaded["name"] == "New Name"

    def test_rename_nonexistent(self, session_manager):
        result = session_manager.rename_session("doesnotexist", "New Name")
        assert result is False

    def test_add_prompt_result(self, session_manager):
        sid, _ = session_manager.create_session("Test")
        result = session_manager.add_prompt_result(sid, "Test prompt", "Test result")
        assert result is True
        loaded = session_manager.load_session(sid)
        assert len(loaded["prompt_history"]) == 1
        assert loaded["prompt_history"][0]["prompt"] == "Test prompt"

    def test_get_prompt_history(self, session_manager):
        sid, _ = session_manager.create_session("Test")
        session_manager.add_prompt_result(sid, "Prompt 1", "Result 1")
        session_manager.add_prompt_result(sid, "Prompt 2", "Result 2")
        history = session_manager.get_prompt_history(sid)
        assert len(history) == 2

    def test_prompt_history_truncation(self, session_manager):
        sid, _ = session_manager.create_session("Test")
        for i in range(55):
            session_manager.add_prompt_result(sid, f"Prompt {i}", f"Result {i}")
        loaded = session_manager.load_session(sid)
        assert len(loaded["prompt_history"]) <= 50


class TestSessionKnowledgeExtraction:
    def test_extract_task_outcome(self, session_manager):
        sid, _ = session_manager.create_session("Test")
        session_manager.add_prompt_result(sid, "What is 2 + 2?", "4")
        loaded = session_manager.load_session(sid)
        knowledge = loaded["learned_knowledge"]
        items = [k for k in knowledge if k.get("type") == "task_outcome"]
        assert len(items) >= 1
        assert "2 + 2" in items[0]["content"]

    def test_extract_multiple_knowledge(self, session_manager):
        sid, _ = session_manager.create_session("Test")
        session_manager.add_prompt_result(sid, "How to optimize token usage?", "Use caching")
        session_manager.add_prompt_result(sid, "Fix bug in api_server", "Fixed route handler")
        loaded = session_manager.load_session(sid)
        knowledge = loaded["learned_knowledge"]
        assert len(knowledge) >= 1
        items = [k for k in knowledge if k.get("type") == "task_outcome"]
        assert len(items) >= 2

    def test_knowledge_context_da(self, session_manager):
        sid, _ = session_manager.create_session("Test")
        session_manager.add_prompt_result(sid, "Analyzer kode i agent_core.py", "Kode analyseret")
        context = session_manager.get_knowledge_for_context(sid, "kode analyse agent_core", lang="da")
        assert context is not None
        assert isinstance(context, str)
        assert len(context) > 0

    def test_knowledge_context_en(self, session_manager):
        sid, _ = session_manager.create_session("Test")
        session_manager.add_prompt_result(sid, "token optimization for llm", "Use caching")
        context = session_manager.get_knowledge_for_context(sid, "token optimization", lang="en")
        assert context is not None
        assert isinstance(context, str)

    def test_knowledge_context_empty_for_new_session(self, session_manager):
        sid, _ = session_manager.create_session("Test")
        context = session_manager.get_knowledge_for_context(sid, "Any prompt", lang="da")
        assert context == ""

    def test_knowledge_context_respects_lang(self, session_manager):
        sid, _ = session_manager.create_session("Test")
        session_manager.add_prompt_result(sid, "How to fix bugs in code?", "Fixed with tests")
        ctx_da = session_manager.get_knowledge_for_context(sid, "fix bugs code", lang="da")
        ctx_en = session_manager.get_knowledge_for_context(sid, "fix bugs code", lang="en")
        assert isinstance(ctx_da, str), f"da context not string: {type(ctx_da)}"
        assert isinstance(ctx_en, str), f"en context not string: {type(ctx_en)}"


class TestSessionStorage:
    def test_sessions_persisted_to_disk(self, session_manager):
        sid, _ = session_manager.create_session("Persistent")
        filepath = os.path.join(session_manager.storage_dir, f"{sid}.json")
        assert os.path.exists(filepath)
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
            assert data["name"] == "Persistent"

    def test_last_modified_updated(self, session_manager):
        sid, _ = session_manager.create_session("Test")
        loaded1 = session_manager.load_session(sid)
        old_modified = loaded1["last_modified"]
        session_manager.add_prompt_result(sid, "New prompt", "Result")
        loaded2 = session_manager.load_session(sid)
        assert loaded2["last_modified"] >= old_modified