"""Test edit_file symbol-not-found → append behavior."""
import json
import os
import pytest
import tempfile
from tools import ToolRegistry, Tool


def _make_py_with_func(tmp_path: str, name: str = "existing_func", body: str = "    pass") -> str:
    """Create a .py file containing one function."""
    path = os.path.join(tmp_path, "test_mod.py")
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"def {name}():\n{body}\n")
    return path


class TestEditFileSymbolCreate:
    """edit_file with symbol not found → append new symbol at end of file."""

    def test_symbol_not_found_appends(self, tmp_path):
        from git_ops import edit_file
        path = _make_py_with_func(str(tmp_path))
        result = edit_file(path, symbol="ny_funktion", new_text="def ny_funktion():\n    return 42")
        assert result["success"] is True
        assert result.get("action") == "created"
        content = open(path, encoding="utf-8").read()
        assert "def ny_funktion():" in content
        assert "return 42" in content
        # Original function preserved
        assert "def existing_func():" in content

    def test_dedent_normalizes_indentation(self, tmp_path):
        from git_ops import edit_file
        path = _make_py_with_func(str(tmp_path))
        # new_text with 8 spaces leading indent (simulates LLM over-indenting)
        indented = "        def indented_func():\n            return 99"
        result = edit_file(path, symbol="indented_func", new_text=indented)
        assert result["success"] is True
        content = open(path, encoding="utf-8").read()
        # Should be dedented to module level
        assert "def indented_func():" in content
        assert "    return 99" in content
        assert "        def indented_func():" not in content

    def test_append_non_py_file(self, tmp_path):
        from git_ops import edit_file
        path = os.path.join(str(tmp_path), "data.json")
        with open(path, "w", encoding="utf-8") as f:
            f.write('{"a": 1}\n')
        # dedent should NOT be applied to non-.py files
        result = edit_file(path, symbol="new_entry", new_text='  {"b": 2}')
        assert result["success"] is True
        content = open(path, encoding="utf-8").read()
        assert '  {"b": 2}' in content  # indentation preserved


class TestEditFileSymbolReplace:
    """edit_file with symbol that exists → replace (regression)."""

    def test_symbol_exists_replaces(self, tmp_path):
        from git_ops import edit_file
        path = _make_py_with_func(str(tmp_path), name="hello", body="    return 'old'")
        result = edit_file(path, symbol="hello", new_text="def hello():\n    return 'new'")
        assert result["success"] is True
        content = open(path, encoding="utf-8").read()
        assert "return 'new'" in content
        assert "return 'old'" not in content


class TestEditFileRequirements:
    """edit_file with requirements alone (without symbol/old_text) → error."""

    def test_requirements_alone_errors(self, tmp_path):
        from git_ops import edit_file
        path = _make_py_with_func(str(tmp_path))
        result = edit_file(path, requirements="fix this function")
        assert result["success"] is False
        assert "Provide either symbol" in result.get("error", "")


class TestEditFileSymbolNoNewText:
    """edit_file with symbol but no new_text → error."""

    def test_symbol_without_new_text_errors(self, tmp_path):
        from git_ops import edit_file
        path = _make_py_with_func(str(tmp_path))
        result = edit_file(path, symbol="existing_func")
        assert result["success"] is False
        assert "new_text is required" in result.get("error", "")


class TestToolRegistration:
    """requirements should NOT be in optional_params for edit_file."""

    def test_requirements_not_in_optional_params(self):
        from agent_core import Agent
        agent = Agent()
        tool = agent.tool_registry.tools.get("edit_file")
        assert tool is not None, "edit_file tool not found"
        assert "requirements" not in tool.optional_params
        assert "symbol" in tool.optional_params
        assert "test_path" in tool.optional_params


class TestToolDescriptions:
    """Tool descriptions should not mention 'requirements'."""

    @pytest.mark.parametrize("lang_file", [
        "da.json", "en.json", "es.json", "zh.json"
    ])
    def test_description_no_requirements(self, lang_file):
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(base, "lang", lang_file)
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        desc = data.get("tools", {}).get("edit_file", "")
        assert "requirements" not in desc, f"{lang_file} still mentions 'requirements'"
