"""Tests for agent_tasks._build_module_progress_msg — Ekstraher dedup progress."""

import os
import pytest
from unittest.mock import MagicMock


def _make_agent(planned_symbols, source_file="refac_test.py"):
    """Create a mock agent with _planned_symbols_per_target."""
    agent = MagicMock()
    agent._planned_symbols_per_target = planned_symbols
    agent._source_file = source_file
    agent.original_prompt = f"Refactor {source_file}"
    agent.lang = "da"
    return agent


class TestBuildModuleProgressMsg:
    """Tests for _build_module_progress_msg."""

    def test_empty_plan(self):
        from agent_tasks import _build_module_progress_msg
        agent = _make_agent({})
        result = _build_module_progress_msg(agent)
        assert result == ""

    def test_none_plan(self):
        from agent_tasks import _build_module_progress_msg
        agent = MagicMock()
        agent._planned_symbols_per_target = None
        result = _build_module_progress_msg(agent)
        assert result == ""

    def test_all_modules_complete(self, tmp_path):
        from agent_tasks import _build_module_progress_msg
        # Create complete module files
        mod1 = tmp_path / "config.py"
        mod1.write_text("DATABASE_URL = 'x'\ndef get_config(): pass\n", encoding="utf-8")
        mod2 = tmp_path / "utils.py"
        mod2.write_text("def helper(): pass\ndef other(): pass\n", encoding="utf-8")
        planned = {
            str(mod1): ["DATABASE_URL", "get_config"],
            str(mod2): ["helper", "other"],
        }
        agent = _make_agent(planned)
        result = _build_module_progress_msg(agent)
        assert "✅" in result
        assert "config.py" in result
        assert "utils.py" in result
        assert "2/2" in result
        # No "næste" when all done
        assert "Næste" not in result

    def test_one_module_incomplete(self, tmp_path):
        from agent_tasks import _build_module_progress_msg
        mod1 = tmp_path / "config.py"
        mod1.write_text("DATABASE_URL = 'x'\ndef get_config(): pass\n", encoding="utf-8")
        planned = {
            str(mod1): ["DATABASE_URL", "get_config"],
            "utils.py": ["helper", "other"],
        }
        agent = _make_agent(planned)
        result = _build_module_progress_msg(agent)
        assert "✅" in result
        assert "config.py" in result
        assert "⏳" in result
        assert "utils.py" in result
        assert "Næste" in result
        assert "batch_extract_symbols" in result

    def test_module_not_created(self, tmp_path):
        from agent_tasks import _build_module_progress_msg
        planned = {
            "nonexistent.py": ["foo", "bar"],
        }
        agent = _make_agent(planned)
        result = _build_module_progress_msg(agent)
        assert "⏳" in result
        assert "nonexistent.py" in result
        assert "0/2" in result
        assert "endnu ikke oprettet" in result

    def test_partial_symbols(self, tmp_path):
        from agent_tasks import _build_module_progress_msg
        mod1 = tmp_path / "handler.py"
        mod1.write_text("class User: pass\ndef create_user(): pass\n", encoding="utf-8")
        planned = {
            str(mod1): ["User", "create_user", "find_user", "delete_user"],
        }
        agent = _make_agent(planned)
        result = _build_module_progress_msg(agent)
        assert "2/4" in result
        assert "mangler" in result
        assert "find_user" in result or "delete_user" in result

    def test_batch_call_includes_missing_symbols(self, tmp_path):
        from agent_tasks import _build_module_progress_msg
        planned = {
            "handler.py": ["User", "create_user", "find_user"],
        }
        agent = _make_agent(planned)
        result = _build_module_progress_msg(agent)
        assert "batch_extract_symbols" in result
        assert "target='handler.py'" in result
        # Missing symbols should be listed
        assert "User" in result or "create_user" in result or "find_user" in result
