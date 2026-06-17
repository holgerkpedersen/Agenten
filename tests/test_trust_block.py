"""Test trust block injection and list_symbols removal for refactor Ekstraher phase.

The trust block is injected when:
  - agent.active_template == "refactor"
  - task_node.name.lower() == "ekstraher"
  - plan_block is non-empty (refactor_plan.md loaded)
  - _symbols_block is non-empty (symbols auto-loaded)

When the trust block is active, list_symbols is REMOVED from active tools
so the LLM literally cannot call it — it must use batch_extract_symbols.
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


class FakeTaskNode:
    def __init__(self, name, parent=None):
        self.name = name
        self.children = []
        self.status = "pending"
        self.result = None
        self.parent = parent
        self.success_criteria = []
        self._is_root = not parent


class FakeToolRegistry:
    TOOL_MARKER = "<<<TOOL>>>"
    DONE_MARKER = "<<<DONE>>>"
    END_MARKER = "<<<END>>>"

    def __init__(self, active_tools=None):
        self.active_tools = active_tools or []
        self.tools = {}

    def build_system_prompt(self, task_prompt):
        return task_prompt

    def get_openai_tools_for_active(self):
        return []


class FakeSeq:
    def generate_tool_tip(self, template, phase):
        return ""


class FakeAgent:
    def __init__(self, template="refactor", phase="ekstraher",
                 plan_block=True, symbols_block=True,
                 tools=None):
        self.active_template = template
        self.tool_registry = FakeToolRegistry(
            tools or ["list_symbols", "batch_extract_symbols", "extract_symbol",
                       "verify_refactor", "list_chunks", "read_chunk",
                       "locate", "read_location", "write_file"]
        )
        self.lang = "da"
        self.prompt = "REFAC-004: api_server.py er 2196 linjer"
        self._file_context_str = ""
        self.file_chunks = {}
        self.images = []
        self._phase_todos = None
        self._skills = []
        self._active_skills = []
        self._seq = FakeSeq()
        self._agent_log = []
        self._tool_log = []
        self._list_symbols_cache = {}
        # Simulate loaded plan_block and _symbols_block
        self._refactor_plan_path = "refactor_plan.md"
        self._plan_loaded = plan_block
        self._symbols_loaded = symbols_block

    def _refresh_skills(self):
        pass

    def _match_skills(self, prompt):
        pass

    def _format_skills_for_prompt(self):
        return ""

    def _log(self, level, message, detail):
        self._agent_log.append({"level": level, "message": message, "detail": detail})


from agent_tasks import _build_initial_messages


class TestTrustBlockInjection(unittest.TestCase):
    """Verify trust block is injected and list_symbols is removed."""

    def setUp(self):
        self.src_path = "api_server.py"
        # Create a minimal refactor_plan.md for testing
        self.plan_content = "### middleware.py\n### session_manager.py\n"

    def test_trust_block_removes_list_symbols(self):
        """list_symbols should be removed from active tools when trust block active."""
        agent = FakeAgent(template="refactor", phase="ekstraher",
                          plan_block=True, symbols_block=True)
        node = FakeTaskNode("Ekstraher")
        orig_tools = list(agent.tool_registry.active_tools)

        with patch("os.path.exists", return_value=True), \
             patch("builtins.open", new_callable=MagicMock) as mock_open, \
             patch("agent_tasks._build_refactor_phase_context", return_value=""), \
             patch("agent_files.list_symbols") as mock_ls:
            mock_open.return_value.__enter__.return_value.read.return_value = self.plan_content
            mock_ls.return_value = {
                "success": True,
                "filepath": "api_server.py",
                "symbols": [
                    {"name": "_file_mtime", "type": "function", "line": 114, "signature": "(path)"},
                ],
                "count": 1,
            }
            _, _, _ = _build_initial_messages(agent, node, agent.prompt, "")

        self.assertNotIn("list_symbols", agent.tool_registry.active_tools,
                         "list_symbols should be removed when trust block is active")
        self.assertIn("batch_extract_symbols", agent.tool_registry.active_tools,
                      "batch_extract_symbols should remain")

    def test_trust_block_not_injected_for_non_refactor(self):
        """list_symbols should stay when template is not refactor."""
        agent = FakeAgent(template="bugfix", phase="ekstraher",
                          plan_block=True, symbols_block=True)
        node = FakeTaskNode("Ekstraher")
        orig_tools = list(agent.tool_registry.active_tools)

        with patch("os.path.exists", return_value=False):
            with patch("agent_files.list_symbols") as mock_ls:
                mock_ls.return_value = {"success": True, "symbols": [], "count": 0}
                _, _, _ = _build_initial_messages(agent, node, agent.prompt, "")

        self.assertIn("list_symbols", agent.tool_registry.active_tools,
                      "list_symbols should remain for non-refactor templates")

    def test_trust_block_not_injected_for_non_ekstraher(self):
        """list_symbols should stay when phase is not Ekstraher."""
        agent = FakeAgent(template="refactor", phase="opdat\u00e9r",
                          plan_block=True, symbols_block=True)
        node = FakeTaskNode("Opdat\u00e9r")

        with patch("os.path.exists", return_value=True), \
             patch("builtins.open", new_callable=MagicMock) as mock_open, \
             patch("agent_tasks._build_refactor_phase_context", return_value=""), \
             patch("agent_files.list_symbols") as mock_ls:
            mock_open.return_value.__enter__.return_value.read.return_value = self.plan_content
            mock_ls.return_value = {"success": True, "symbols": [], "count": 0}
            _, _, _ = _build_initial_messages(agent, node, agent.prompt, "")

        self.assertIn("list_symbols", agent.tool_registry.active_tools,
                      "list_symbols should remain for non-Ekstraher phases")

    def test_trust_block_not_injected_without_plan(self):
        """list_symbols should stay when no refactor_plan.md is loaded."""
        agent = FakeAgent(template="refactor", phase="ekstraher",
                          plan_block=False, symbols_block=True)
        node = FakeTaskNode("Ekstraher")
        agent._refactor_plan_path = ""  # No plan loaded

        with patch("os.path.exists", return_value=False), \
             patch("agent_files.list_symbols") as mock_ls:
            mock_ls.return_value = {"success": True, "symbols": [], "count": 0}
            _, _, _ = _build_initial_messages(agent, node, agent.prompt, "")

        self.assertIn("list_symbols", agent.tool_registry.active_tools,
                      "list_symbols should remain when no plan is loaded")


class TestCheckRefactorProgressCount(unittest.TestCase):
    """Verify _check_refactor_progress returns correct symbol count."""

    def _count_via_check_refactor(self, mock_symbols):
        """Helper to call _check_refactor_progress with mocked list_symbols."""
        from agent_tasks import _check_refactor_progress
        with patch("os.path.exists", return_value=False), \
             patch("agent_files.list_symbols") as mock_ls:
            mock_ls.return_value = {
                "success": True,
                "filepath": "api_server.py",
                "symbols": mock_symbols,
                "count": len(mock_symbols),
            }
            return _check_refactor_progress()

    def test_count_from_list_symbols(self):
        """Should return correct count from symbols list."""
        symbols = [
            {"name": f"func_{i}", "type": "function", "line": i * 10}
            for i in range(27)
        ]
        result = self._count_via_check_refactor(symbols)
        self.assertIn("api_server.py: 27 symbols tilbage", result,
                      msg=f"Expected 27 symbols in: {result}")

    def test_count_zero_when_no_symbols(self):
        """Should show 0 when symbols list is empty."""
        result = self._count_via_check_refactor([])
        self.assertIn("api_server.py: 0 symbols tilbage", result)

    def test_count_large_number(self):
        """Should correctly count 100+ symbols."""
        symbols = [
            {"name": f"func_{i}", "type": "function", "line": i}
            for i in range(108)
        ]
        result = self._count_via_check_refactor(symbols)
        self.assertIn("api_server.py: 108 symbols tilbage", result,
                      msg=f"Expected 108 symbols in: {result}")


if __name__ == "__main__":
    unittest.main()
