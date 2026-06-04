"""Test for fix to session 175d41a5: prevent false auto-resolve in refactor template."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from agent_tasks import _refactor_actually_moved_code
from agent_files import list_symbols, locate_code, read_location


class TestRefactorActuallyMoved(unittest.TestCase):
    """Verify _refactor_actually_moved_code correctly detects real refactor work."""

    def setUp(self):
        self._orig_cwd = os.getcwd()

    def tearDown(self):
        os.chdir(self._orig_cwd)

    def _run_in_tmp(self, files: dict[str, str]) -> bool:
        """Helper: write files to a temp dir, run check, restore cwd."""
        orig = os.getcwd()
        with tempfile.TemporaryDirectory() as tmp:
            try:
                os.chdir(tmp)
                for name, content in files.items():
                    with open(name, "w", encoding="utf-8") as f:
                        f.write(content)

                class FakeAgent:
                    active_template = "refactor"
                    lang = "da"
                return _refactor_actually_moved_code(FakeAgent())
            finally:
                os.chdir(orig)

    def test_bugfix_template_always_returns_true(self):
        class FakeAgent:
            active_template = "bugfix"
            lang = "da"
        self.assertTrue(_refactor_actually_moved_code(FakeAgent()))

    def test_kodeanalyse_template_always_returns_true(self):
        class FakeAgent:
            active_template = "kodeanalyse"
            lang = "da"
        self.assertTrue(_refactor_actually_moved_code(FakeAgent()))

    def test_refactor_no_modules_returns_false(self):
        result = self._run_in_tmp({
            "refactor_plan.md": "# Plan\n### 1. security.py\n### 2. file_handler.py\n",
        })
        self.assertFalse(result)

    def test_refactor_stub_only_returns_false(self):
        result = self._run_in_tmp({
            "refactor_plan.md": "# Plan\n### 1. security.py\n### 2. file_handler.py\n",
            "security.py": "from flask import request\n",
        })
        self.assertFalse(result)

    def test_refactor_real_code_returns_true(self):
        result = self._run_in_tmp({
            "refactor_plan.md": "# Plan\n### 1. security.py\n### 2. file_handler.py\n",
            "security.py": "def check_api_key():\n    return True\n" * 30,
            "file_handler.py": "def serve_upload():\n    return True\n" * 30,
        })
        self.assertTrue(result)

    def test_refactor_missing_one_module_returns_false(self):
        """Stærkere check: kræver ALLE plan-moduler, ikke kun ét."""
        result = self._run_in_tmp({
            "refactor_plan.md": "# Plan\n### 1. security.py\n### 2. file_handler.py\n",
            "security.py": "def check_api_key():\n    return True\n" * 30,
            # file_handler.py bevidst manglende
        })
        self.assertFalse(result)

    def test_refactor_oversize_api_server_returns_false(self):
        """Stærkere check: api_server.py skal være < 1000 linjer."""
        import tempfile as _tf
        with _tf.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "refactor_plan.md"), "w", encoding="utf-8") as f:
                f.write("# Plan\n### 1. security.py\n")
            with open(os.path.join(tmp, "security.py"), "w", encoding="utf-8") as f:
                f.write("def f():\n    pass\n" * 30)
            with open(os.path.join(tmp, "api_server.py"), "w", encoding="utf-8") as f:
                f.write("# " + "x" * 50 + "\n" * 1500)
            old_cwd = os.getcwd()
            os.chdir(tmp)
            try:
                class FakeAgent:
                    active_template = "refactor"
                    lang = "da"
                result = _refactor_actually_moved_code(FakeAgent())
            finally:
                os.chdir(old_cwd)
            self.assertFalse(result)


class TestReadLocationRejectsNonPython(unittest.TestCase):
    """Verify read_location/locate_code/list_symbols give a clear error for non-.py files."""

    def test_locate_code_on_markdown_returns_clear_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            md_path = os.path.join(tmp, "plan.md")
            with open(md_path, "w", encoding="utf-8") as f:
                f.write("# Some plan with ← unicode character\n")
            r = locate_code(filepath=md_path, name="refactor_plan")
            self.assertFalse(r["success"])
            self.assertIn(".py", r["error"])
            self.assertIn("read_chunk", r["error"].lower())

    def test_read_location_on_markdown_returns_clear_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            md_path = os.path.join(tmp, "plan.md")
            with open(md_path, "w", encoding="utf-8") as f:
                f.write("# Some plan\n")
            r = read_location(filepath=md_path, name="refactor_plan")
            self.assertFalse(r["success"])
            self.assertIn(".py", r["error"])
            self.assertIn("read_chunk", r["error"].lower())

    def test_list_symbols_on_markdown_returns_clear_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            md_path = os.path.join(tmp, "plan.md")
            with open(md_path, "w", encoding="utf-8") as f:
                f.write("# Some plan\n")
            r = list_symbols(filepath=md_path)
            self.assertFalse(r["success"])
            self.assertIn(".py", r["error"])
            self.assertIn("read_chunk", r["error"].lower())

    def test_locate_code_on_text_file_returns_clear_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            txt_path = os.path.join(tmp, "notes.txt")
            with open(txt_path, "w", encoding="utf-8") as f:
                f.write("Some notes\n")
            r = locate_code(filepath=txt_path, name="anything")
            self.assertFalse(r["success"])
            self.assertIn(".py", r["error"])

    def test_locate_code_on_python_file_still_works(self):
        with tempfile.TemporaryDirectory() as tmp:
            py_path = os.path.join(tmp, "test.py")
            with open(py_path, "w", encoding="utf-8") as f:
                f.write("def my_function():\n    return 42\n")
            r = locate_code(filepath=py_path, name="my_function")
            self.assertTrue(r["success"])
            self.assertEqual(r["name"], "my_function")
            self.assertIn("def my_function", r["body"])


class TestPlanPhaseCheckIsFilesFromPlan(unittest.TestCase):
    """Verify Plan phase uses files_from_plan, not file_exists."""

    def test_plan_uses_files_from_plan_with_min_5(self):
        from agent_phase_checks import TEMPLATE_PHASE_CHECKS
        plan_spec = TEMPLATE_PHASE_CHECKS["refactor"]["Plan"]
        self.assertEqual(plan_spec["type"], "files_from_plan")
        self.assertEqual(plan_spec["plan_path"], "refactor_plan.md")
        self.assertEqual(plan_spec["min_files"], 5)


if __name__ == "__main__":
    unittest.main()
