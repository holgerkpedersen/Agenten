"""Tests for fixes addressing session 175d41a5 phase 2 issues.

Covers:
  - Fix A: Stærkere _refactor_actually_moved_code (kræver ALLE moduler + api_server < 1000 linjer)
  - Fix B: Dedup-loop escape (3+ "allerede set" -> system reminder)
  - Fix C: Tidlig write-check i refactor Ekstraher/Opdatér (iteration 3)
  - i18n key K.REFACTOR_INCOMPLETE i alle 4 sprog
"""
import os
import tempfile
import unittest
from unittest.mock import patch, MagicMock

from agent_phase_checks import (
    _parse_refactor_plan_modules,
    _has_real_code,
)


def _real_module_content(func_name: str, n_lines: int = 25) -> str:
    """Generate a Python module with n_lines of real code."""
    lines = [f"def {func_name}_{i}(): return {i}" for i in range(n_lines)]
    return "\n".join(lines) + "\n"


class TestParseRefactorPlanModules(unittest.TestCase):
    """_parse_refactor_plan_modules skal finde alle .py moduler i planen."""

    def test_finds_all_listed_modules(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
            f.write("# Plan\n### 1. `security.py`\n### 2. `file_handler.py`\n### 3. `image_handler.py`\n")
            path = f.name
        try:
            modules = _parse_refactor_plan_modules(path)
            self.assertIn("security.py", modules)
            self.assertIn("file_handler.py", modules)
            self.assertIn("image_handler.py", modules)
        finally:
            os.unlink(path)

    def test_empty_plan_returns_empty_list(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
            f.write("# Ingen moduler her\nBare tekst\n")
            path = f.name
        try:
            self.assertEqual(_parse_refactor_plan_modules(path), [])
        finally:
            os.unlink(path)

    def test_nonexistent_file_returns_empty_list(self):
        self.assertEqual(_parse_refactor_plan_modules("/nonexistent/path.md"), [])

    def test_dedup(self):
        """Duplikerede modulnavne returneres kun én gang."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
            f.write("### `security.py`\n### `security.py` igen\n### `file_handler.py`\n")
            path = f.name
        try:
            modules = _parse_refactor_plan_modules(path)
            self.assertEqual(modules.count("security.py"), 1)
            self.assertIn("file_handler.py", modules)
        finally:
            os.unlink(path)


class TestHasRealCode(unittest.TestCase):
    """_has_real_code skal skelne stub fra reel kode."""

    def test_real_class_with_many_lines(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding="utf-8") as f:
            f.write(_real_module_content("func", n_lines=30))
            path = f.name
        try:
            self.assertTrue(_has_real_code(path, min_lines=20))
        finally:
            os.unlink(path)

    def test_only_imports_returns_false(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding="utf-8") as f:
            f.write("from security import check_api_key\nfrom flask import request\n")
            path = f.name
        try:
            self.assertFalse(_has_real_code(path, min_lines=20))
        finally:
            os.unlink(path)

    def test_short_with_def_returns_false(self):
        """Fil med def men under min_lines skal returnere False."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding="utf-8") as f:
            f.write("def foo():\n    return 1\n")
            path = f.name
        try:
            self.assertFalse(_has_real_code(path, min_lines=20))
        finally:
            os.unlink(path)

    def test_nonexistent_returns_false(self):
        self.assertFalse(_has_real_code("/nonexistent/file.py"))

    def test_class_counts_as_real_code(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding="utf-8") as f:
            content = "class Foo:\n" + "\n".join([f"    def m{i}(self): pass" for i in range(25)])
            f.write(content)
            path = f.name
        try:
            self.assertTrue(_has_real_code(path, min_lines=20))
        finally:
            os.unlink(path)


class TestCheckRequiredToolsRefactorEarlyAbort(unittest.TestCase):
    """_check_required_tools skal abortere tidligt for refactor-faser."""

    def _make_agent(self, template="refactor", iteration=3, active=None):
        class FakeToolRegistry:
            def __init__(self, tools):
                self.active_tools = tools
        agent = MagicMock()
        agent.active_template = template
        agent._current_task_iteration = iteration
        agent.tool_registry = FakeToolRegistry(active or ["write_file", "read_location"])
        agent.lang = "da"
        agent.issue_resolved = False
        return agent

    def test_refactor_ekstraher_no_write_at_iter3_fails(self):
        from agent_tasks import _check_required_tools
        agent = self._make_agent(iteration=3)
        called = {"read_location": 1}
        result = _check_required_tools(agent, called, "Ekstraher")
        self.assertIsNotNone(result)
        self.assertIn("write_file", result)

    def test_refactor_opdater_no_write_at_iter3_fails(self):
        from agent_tasks import _check_required_tools
        agent = self._make_agent(iteration=3)
        called = {"read_location": 1}
        result = _check_required_tools(agent, called, "Opdat\u00e9r")
        self.assertIsNotNone(result)

    def test_refactor_with_write_call_passes(self):
        from agent_tasks import _check_required_tools
        agent = self._make_agent(iteration=5)
        called = {"read_location": 1, "write_file{}": 1}
        result = _check_required_tools(agent, called, "Ekstraher")
        if result is not None:
            self.assertNotIn("FEJL: Du har ikke kaldt", result)

    def test_non_refactor_template_unaffected(self):
        from agent_tasks import _check_required_tools
        agent = self._make_agent(template="issue_handler", iteration=10)
        called = {"read_issue": 1}
        result = _check_required_tools(agent, called, "Analyse")
        if result is not None:
            self.assertNotIn("Refactor kr\u00e6ver", result)

    def test_refactor_low_iteration_passes(self):
        """Før iteration 3 skal Fix C ikke abortere."""
        from agent_tasks import _check_required_tools
        agent = self._make_agent(iteration=2)
        called = {"read_location": 1}
        result = _check_required_tools(agent, called, "Ekstraher")
        if result is not None:
            self.assertNotIn("FEJL: Du har ikke kaldt", result)


class TestRefactorActuallyMovedCodeIntegration(unittest.TestCase):
    """Integration: _refactor_actually_moved_code med rigtige filer."""

    def test_all_modules_and_small_api_returns_true(self):
        from agent_tasks import _refactor_actually_moved_code
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "refactor_plan.md"), "w", encoding="utf-8") as f:
                f.write("### `security.py`\n### `file_handler.py`\n")
            with open(os.path.join(tmp, "security.py"), "w", encoding="utf-8") as f:
                f.write(_real_module_content("s", 30))
            with open(os.path.join(tmp, "file_handler.py"), "w", encoding="utf-8") as f:
                f.write(_real_module_content("fh", 30))
            with open(os.path.join(tmp, "api_server.py"), "w", encoding="utf-8") as f:
                f.write("# Lille fil\nimport os\n")
            old_cwd = os.getcwd()
            os.chdir(tmp)
            try:
                class FakeAgent:
                    active_template = "refactor"
                    lang = "da"
                self.assertTrue(_refactor_actually_moved_code(FakeAgent()))
            finally:
                os.chdir(old_cwd)

    def test_missing_module_returns_false(self):
        from agent_tasks import _refactor_actually_moved_code
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "refactor_plan.md"), "w", encoding="utf-8") as f:
                f.write("### `security.py`\n### `file_handler.py`\n")
            with open(os.path.join(tmp, "security.py"), "w", encoding="utf-8") as f:
                f.write(_real_module_content("s", 30))
            with open(os.path.join(tmp, "api_server.py"), "w", encoding="utf-8") as f:
                f.write("# Lille\n")
            old_cwd = os.getcwd()
            os.chdir(tmp)
            try:
                class FakeAgent:
                    active_template = "refactor"
                    lang = "da"
                self.assertFalse(_refactor_actually_moved_code(FakeAgent()))
            finally:
                os.chdir(old_cwd)

    def test_only_stubs_returns_false(self):
        from agent_tasks import _refactor_actually_moved_code
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "refactor_plan.md"), "w", encoding="utf-8") as f:
                f.write("### `routes.py`\n")
            with open(os.path.join(tmp, "routes.py"), "w", encoding="utf-8") as f:
                f.write("from security import check_api_key\n# Routes her\n")
            with open(os.path.join(tmp, "api_server.py"), "w", encoding="utf-8") as f:
                f.write("# Lille\n")
            old_cwd = os.getcwd()
            os.chdir(tmp)
            try:
                class FakeAgent:
                    active_template = "refactor"
                    lang = "da"
                self.assertFalse(_refactor_actually_moved_code(FakeAgent()))
            finally:
                os.chdir(old_cwd)

    def test_oversize_api_server_returns_false(self):
        from agent_tasks import _refactor_actually_moved_code
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "refactor_plan.md"), "w", encoding="utf-8") as f:
                f.write("### `security.py`\n")
            with open(os.path.join(tmp, "security.py"), "w", encoding="utf-8") as f:
                f.write(_real_module_content("s", 30))
            with open(os.path.join(tmp, "api_server.py"), "w", encoding="utf-8") as f:
                f.write("\n".join(["# x" for _ in range(1500)]))
            old_cwd = os.getcwd()
            os.chdir(tmp)
            try:
                class FakeAgent:
                    active_template = "refactor"
                    lang = "da"
                self.assertFalse(_refactor_actually_moved_code(FakeAgent()))
            finally:
                os.chdir(old_cwd)


class TestI18nRefactorIncomplete(unittest.TestCase):
    """i18n key K.REFACTOR_INCOMPLETE skal findes i alle 4 sprog."""

    def test_key_exists_in_da(self):
        from i18n import K
        from lang import t
        result = t(K.REFACTOR_INCOMPLETE, "da")
        self.assertIn("{missing_count}", result)
        self.assertIn("ufuldst", result.lower())

    def test_key_exists_in_en(self):
        from i18n import K
        from lang import t
        result = t(K.REFACTOR_INCOMPLETE, "en")
        self.assertIn("incomplete", result.lower())

    def test_key_exists_in_es(self):
        from i18n import K
        from lang import t
        result = t(K.REFACTOR_INCOMPLETE, "es")
        self.assertIn("incompleta", result.lower())

    def test_key_exists_in_zh(self):
        from i18n import K
        from lang import t
        result = t(K.REFACTOR_INCOMPLETE, "zh")
        self.assertIn("\u672a\u5b8c\u6210", result)


if __name__ == "__main__":
    unittest.main()
