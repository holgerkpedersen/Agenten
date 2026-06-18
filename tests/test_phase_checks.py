"""Test agent_phase_checks.py — all check types."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from agent_phase_checks import (
    TEMPLATE_PHASE_CHECKS,
    _extract_modules_from_plan,
    check_all_of,
    check_code_contains,
    check_file_exists,
    check_files_from_plan,
    check_min_text_length,
    check_phase_done,
    check_symbols_covered_by_modules,
    check_tests_pass,
    check_tool_called,
)


class FakeAgent:
    """Minimal Agent-like object exposing active_template and lang."""

    def __init__(self, template: str = "", lang: str = "da", tests_failed: bool = False, messages: list | None = None) -> None:
        self.active_template = template
        self.lang = lang
        self._tests_failed = tests_failed
        self.messages = messages or []


class FakeTask:
    def __init__(self, name: str) -> None:
        self.name = name


class TestFileExists(unittest.TestCase):
    def test_all_files_exist(self):
        with tempfile.TemporaryDirectory() as tmp:
            f1 = os.path.join(tmp, "a.txt")
            f2 = os.path.join(tmp, "b.txt")
            open(f1, "w").close()
            open(f2, "w").close()
            ok, msg = check_file_exists([f1, f2])
            self.assertTrue(ok)
            self.assertIn("a.txt", msg)
            self.assertIn("b.txt", msg)

    def test_missing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            f1 = os.path.join(tmp, "a.txt")
            f2 = os.path.join(tmp, "missing.txt")
            open(f1, "w").close()
            ok, msg = check_file_exists([f1, f2])
            self.assertFalse(ok)
            self.assertIn("missing.txt", msg)

    def test_empty_paths(self):
        ok, _ = check_file_exists([])
        self.assertFalse(ok)

    def test_require_all_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            f1 = os.path.join(tmp, "a.txt")
            f2 = os.path.join(tmp, "missing.txt")
            open(f1, "w").close()
            ok, msg = check_file_exists([f1, f2], {"require_all": False})
            self.assertTrue(ok)
            self.assertIn("a.txt", msg)

    def test_require_all_false_none_exist(self):
        ok, _ = check_file_exists(["/nonexistent/a", "/nonexistent/b"], {"require_all": False})
        self.assertFalse(ok)


class TestExtractModulesFromPlan(unittest.TestCase):
    def test_heading_format(self):
        plan = """# Refaktorplan

### 1. routes.py
**Ansvar:** Endpoint definitioner

### 2. session_manager.py
**Ansvar:** Session CRUD
"""
        mods = _extract_modules_from_plan(plan)
        self.assertEqual(mods, ["routes.py", "session_manager.py"])

    def test_inline_format(self):
        plan = """Vi opretter følgende moduler: routes.py, file_handler.py og model_manager.py."""
        mods = _extract_modules_from_plan(plan)
        self.assertIn("routes.py", mods)
        self.assertIn("file_handler.py", mods)
        self.assertIn("model_manager.py", mods)

    def test_ignores_paths(self):
        plan = "Se evt. ./docs/refactor_plan.md for detaljer. Fil: routes.py"
        mods = _extract_modules_from_plan(plan)
        self.assertIn("routes.py", mods)
        self.assertNotIn("./docs/refactor_plan.md", mods)

    def test_empty_plan(self):
        self.assertEqual(_extract_modules_from_plan(""), [])
        self.assertEqual(_extract_modules_from_plan(None), [])

    def test_custom_extension(self):
        plan = "Opret moduler: data.json og config.json"
        mods = _extract_modules_from_plan(plan, ext=".json")
        self.assertIn("data.json", mods)
        self.assertIn("config.json", mods)


class TestFilesFromPlan(unittest.TestCase):
    def test_all_modules_exist(self):
        with tempfile.TemporaryDirectory() as tmp:
            open(os.path.join(tmp, "refactor_plan.md"), "w").write("""# Plan

### routes.py
### session_manager.py
""")
            open(os.path.join(tmp, "routes.py"), "w").close()
            open(os.path.join(tmp, "session_manager.py"), "w").close()
            ok, msg = check_files_from_plan({"plan_path": "refactor_plan.md"}, base_dir=tmp)
            self.assertTrue(ok, msg)
            self.assertIn("routes.py", msg)
            self.assertIn("session_manager.py", msg)

    def test_missing_module(self):
        with tempfile.TemporaryDirectory() as tmp:
            open(os.path.join(tmp, "refactor_plan.md"), "w").write("### routes.py\n### session_manager.py\n")
            open(os.path.join(tmp, "routes.py"), "w").close()
            # session_manager.py missing
            ok, msg = check_files_from_plan({"plan_path": "refactor_plan.md"}, base_dir=tmp)
            self.assertFalse(ok)
            self.assertIn("session_manager.py", msg)
            self.assertNotIn("routes.py", msg)

    def test_plan_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            ok, _ = check_files_from_plan({"plan_path": "refactor_plan.md"}, base_dir=tmp)
            self.assertFalse(ok)

    def test_min_files_too_low(self):
        with tempfile.TemporaryDirectory() as tmp:
            open(os.path.join(tmp, "refactor_plan.md"), "w").write("Kort plan uden moduler.")
            ok, msg = check_files_from_plan(
                {"plan_path": "refactor_plan.md", "min_files": 3}, base_dir=tmp
            )
            self.assertFalse(ok)
            self.assertIn("3", msg)


class TestCheckPhaseDone(unittest.TestCase):
    def test_no_template(self):
        agent = FakeAgent(template="")
        task = FakeTask("Plan")
        ok, _ = check_phase_done(agent, task, None)
        self.assertFalse(ok)

    def test_unknown_template(self):
        agent = FakeAgent(template="nonexistent_template")
        task = FakeTask("Plan")
        ok, _ = check_phase_done(agent, task, None)
        self.assertFalse(ok)

    def test_unknown_phase(self):
        agent = FakeAgent(template="refactor")
        task = FakeTask("DoesNotExist")
        ok, _ = check_phase_done(agent, task, None)
        self.assertFalse(ok)

    def test_plan_phase_passes_when_file_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            # Plan must list at least 5 .py modules to pass.
            plan_content = (
                "# Plan\n"
                "### 1. security.py\n"
                "### 2. file_handler.py\n"
                "### 3. image_handler.py\n"
                "### 4. session_manager.py\n"
                "### 5. model_manager.py\n"
            )
            open(os.path.join(tmp, "refactor_plan.md"), "w", encoding="utf-8").write(plan_content)
            # Also create the module files (files_from_plan requires them to exist)
            for mod in ("security.py", "file_handler.py", "image_handler.py",
                        "session_manager.py", "model_manager.py"):
                open(os.path.join(tmp, mod), "w", encoding="utf-8").close()
            agent = FakeAgent(template="refactor")
            task = FakeTask("Plan")
            ok, msg = check_phase_done(agent, task, None, base_dir=tmp)
            self.assertTrue(ok, msg)

    def test_plan_phase_fails_when_file_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            agent = FakeAgent(template="refactor")
            task = FakeTask("Plan")
            ok, _ = check_phase_done(agent, task, None, base_dir=tmp)
            self.assertFalse(ok)

    def test_plan_phase_fails_when_too_few_modules(self):
        """Plan must list at least 5 .py modules (catches stale plans from prior runs)."""
        with tempfile.TemporaryDirectory() as tmp:
            # Only 2 modules listed — should fail with min_files=5
            plan_content = (
                "# Plan\n"
                "### 1. security.py\n"
                "### 2. file_handler.py\n"
            )
            open(os.path.join(tmp, "refactor_plan.md"), "w", encoding="utf-8").write(plan_content)
            agent = FakeAgent(template="refactor")
            task = FakeTask("Plan")
            ok, msg = check_phase_done(agent, task, None, base_dir=tmp)
            self.assertFalse(ok, msg)
            self.assertIn("mindst 5", msg)

    def test_case_insensitive_phase_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan_content = (
                "# Plan\n"
                "### 1. security.py\n"
                "### 2. file_handler.py\n"
                "### 3. image_handler.py\n"
                "### 4. session_manager.py\n"
                "### 5. model_manager.py\n"
            )
            open(os.path.join(tmp, "refactor_plan.md"), "w", encoding="utf-8").write(plan_content)
            for mod in ("security.py", "file_handler.py", "image_handler.py",
                        "session_manager.py", "model_manager.py"):
                open(os.path.join(tmp, mod), "w", encoding="utf-8").close()
            agent = FakeAgent(template="refactor")
            # Danish capital + lowercase
            for name in ("Plan", "plan", "PLAN"):
                task = FakeTask(name)
                ok, _ = check_phase_done(agent, task, None, base_dir=tmp)
                self.assertTrue(ok, f"Phase name '{name}' should match")

    def test_ekstraher_phase(self):
        with tempfile.TemporaryDirectory() as tmp:
            open(os.path.join(tmp, "refactor_plan.md"), "w").write("### routes.py\n### session_manager.py\n")
            # Create both modules but leave them empty (no symbols to cover
            # since source file is missing) — symbols_covered requires the
            # source file to exist, so we use a minimal api_server.py.
            open(os.path.join(tmp, "api_server.py"), "w").write("# empty\npass\n")
            open(os.path.join(tmp, "routes.py"), "w").close()
            agent = FakeAgent(template="refactor")
            task = FakeTask("Ekstraher")
            # First: missing module → fail
            ok, _ = check_phase_done(agent, task, None, base_dir=tmp)
            self.assertFalse(ok)
            # Add module → pass (all_of needs both files_from_plan + symbols_covered,
            # symbols_covered passes with 0 source symbols to track)
            open(os.path.join(tmp, "session_manager.py"), "w").close()
            ok, msg = check_phase_done(agent, task, None, base_dir=tmp)
            self.assertTrue(ok, msg)


class TestTemplatePhaseChecksConfig(unittest.TestCase):
    def test_refactor_plan_check(self):
        cfg = TEMPLATE_PHASE_CHECKS.get("refactor", {}).get("Plan")
        self.assertIsNotNone(cfg)
        self.assertEqual(cfg["type"], "files_from_plan")
        self.assertEqual(cfg["plan_path"], "refactor_plan.md")
        self.assertEqual(cfg["min_files"], 5)

    def test_refactor_ekstraher_check(self):
        cfg = TEMPLATE_PHASE_CHECKS.get("refactor", {}).get("Ekstraher")
        self.assertIsNotNone(cfg)
        self.assertEqual(cfg["type"], "all_of")
        sub_types = [c.get("type") for c in cfg.get("checks", [])]
        self.assertIn("files_from_plan", sub_types)
        self.assertIn("symbols_covered", sub_types)
        symbols_spec = next(c for c in cfg["checks"] if c["type"] == "symbols_covered")
        self.assertEqual(symbols_spec["source_file"], "api_server.py")
        self.assertEqual(symbols_spec["plan_path"], "refactor_plan.md")

    def test_refactor_analyse_check(self):
        cfg = TEMPLATE_PHASE_CHECKS.get("refactor", {}).get("Analyse")
        self.assertEqual(cfg["type"], "all_of")
        self.assertIn("min_text_length", [c["type"] for c in cfg["checks"]])
        self.assertIn("tool_called", [c["type"] for c in cfg["checks"]])

    def test_refactor_test_check(self):
        cfg = TEMPLATE_PHASE_CHECKS.get("refactor", {}).get("Test")
        self.assertEqual(cfg["type"], "tests_pass")

    def test_bugfix_implementering_check(self):
        cfg = TEMPLATE_PHASE_CHECKS.get("bugfix", {}).get("Implementering")
        self.assertEqual(cfg["type"], "tool_called")
        self.assertIn("edit_file", cfg["tools"])

    def test_bugfix_opdatering_check(self):
        cfg = TEMPLATE_PHASE_CHECKS.get("bugfix", {}).get("Opdatering")
        self.assertEqual(cfg["type"], "tool_called")
        self.assertEqual(cfg["tools"], ["update_issue_status"])

    def test_issue_handler_all_phases_defined(self):
        cfg = TEMPLATE_PHASE_CHECKS.get("issue_handler")
        self.assertIsNotNone(cfg)
        for phase in ("L\u00e6s", "Afklar", "Fix", "Luk Issue"):
            self.assertIn(phase, cfg, f"issue_handler.{phase} mangler check-spec")

    def test_testgenerering_all_phases_defined(self):
        cfg = TEMPLATE_PHASE_CHECKS.get("testgenerering")
        self.assertIsNotNone(cfg)
        for phase in ("Analyse", "Test (Red)", "Implementering", "Verifikation (Green)"):
            self.assertIn(phase, cfg, f"testgenerering.{phase} mangler check-spec")


class TestMinTextLength(unittest.TestCase):
    def test_full_response_satisfies(self):
        ok, msg = check_min_text_length({"min_chars": 10}, full_response="a" * 50)
        self.assertTrue(ok, msg)
        self.assertIn("50", msg)

    def test_full_response_too_short(self):
        ok, msg = check_min_text_length({"min_chars": 100}, full_response="kort")
        self.assertFalse(ok)
        self.assertIn("4", msg)

    def test_includes_assistant_messages(self):
        agent = FakeAgent(messages=[
            {"role": "user", "content": "kort user msg"},
            {"role": "assistant", "content": "x" * 200},
        ])
        ok, msg = check_min_text_length({"min_chars": 100}, full_response="", agent=agent)
        self.assertTrue(ok, msg)

    def test_includes_list_content(self):
        agent = FakeAgent(messages=[
            {"role": "assistant", "content": [{"type": "text", "text": "y" * 150}]},
        ])
        ok, _ = check_min_text_length({"min_chars": 100}, agent=agent)
        self.assertTrue(ok)

    def test_invalid_min_chars(self):
        ok, _ = check_min_text_length({"min_chars": "abc"})
        self.assertFalse(ok)

    def test_min_chars_zero_always_passes(self):
        ok, _ = check_min_text_length({"min_chars": 0})
        self.assertTrue(ok)


class TestToolCalled(unittest.TestCase):
    def test_current_tool_in_list(self):
        ok, msg = check_tool_called({"tools": ["edit_file"]}, tool_name="edit_file")
        self.assertTrue(ok, msg)
        self.assertIn("edit_file", msg)

    def test_current_tool_not_in_list(self):
        ok, msg = check_tool_called({"tools": ["edit_file"]}, tool_name="read_chunk")
        self.assertFalse(ok)

    def test_called_tools_history(self):
        called = {"edit_file{file:x.py,old:y}": 1}
        ok, _ = check_tool_called({"tools": ["edit_file"]}, tool_name="read_chunk", called_tools=called)
        self.assertTrue(ok)

    def test_require_all_passes(self):
        called = {"edit_file{}": 1, "write_file{}": 1}
        ok, _ = check_tool_called(
            {"tools": ["edit_file", "write_file"], "require_all": True},
            tool_name="read_chunk", called_tools=called,
        )
        self.assertTrue(ok)

    def test_require_all_fails_when_missing(self):
        called = {"edit_file{}": 1}
        ok, msg = check_tool_called(
            {"tools": ["edit_file", "update_issue_status"], "require_all": True},
            tool_name="read_chunk", called_tools=called,
        )
        self.assertFalse(ok)
        self.assertIn("update_issue_status", msg)

    def test_empty_tools_list(self):
        ok, _ = check_tool_called({"tools": []}, tool_name="edit_file")
        self.assertFalse(ok)

    def test_no_history_no_current_tool(self):
        ok, _ = check_tool_called({"tools": ["edit_file"]}, tool_name="", called_tools=None)
        self.assertFalse(ok)


class TestCodeContains(unittest.TestCase):
    def test_pattern_matches(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = os.path.join(tmp, "api_server.py")
            open(f, "w").write("from routes import bp\nfrom session_manager import save\n")
            ok, msg = check_code_contains(
                {"path": "api_server.py", "patterns": ["from routes"], "require_all": False, "min_matches": 1},
                base_dir=tmp,
            )
            self.assertTrue(ok, msg)

    def test_require_all_missing_pattern(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = os.path.join(tmp, "api_server.py")
            open(f, "w").write("from routes import bp\n")
            ok, msg = check_code_contains(
                {"path": "api_server.py", "patterns": ["from routes", "from security"], "require_all": True},
                base_dir=tmp,
            )
            self.assertFalse(ok)
            self.assertIn("security", msg)

    def test_min_matches_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = os.path.join(tmp, "api_server.py")
            open(f, "w").write("from routes import bp\n")
            ok, _ = check_code_contains(
                {"path": "api_server.py", "patterns": ["from routes", "from security"], "min_matches": 2},
                base_dir=tmp,
            )
            self.assertFalse(ok)

    def test_file_missing(self):
        ok, msg = check_code_contains(
            {"path": "missing.py", "patterns": ["x"]}, base_dir="/nonexistent"
        )
        self.assertFalse(ok)
        self.assertIn("findes ikke", msg)

    def test_empty_path(self):
        ok, _ = check_code_contains({"path": "", "patterns": ["x"]})
        self.assertFalse(ok)

    def test_invalid_regex(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = os.path.join(tmp, "x.py")
            open(f, "w").write("ok")
            ok, msg = check_code_contains(
                {"path": "x.py", "patterns": ["[invalid"]}, base_dir=tmp,
            )
            self.assertFalse(ok)
            self.assertIn("ugyldigt", msg)


class TestTestsPass(unittest.TestCase):
    def test_requires_run_tests_call(self):
        agent = FakeAgent()
        ok, msg = check_tests_pass({}, agent=agent, tool_name="edit_file")
        self.assertFalse(ok)
        self.assertIn("kaldte ikke run_tests", msg)

    def test_passes_when_run_tests_just_called(self):
        agent = FakeAgent(tests_failed=False)
        ok, _ = check_tests_pass({}, agent=agent, tool_name="run_tests")
        self.assertTrue(ok)

    def test_fails_when_tests_failed_flag(self):
        agent = FakeAgent(tests_failed=True)
        ok, _ = check_tests_pass({}, agent=agent, tool_name="run_tests")
        self.assertFalse(ok)

    def test_falls_back_to_called_tools(self):
        agent = FakeAgent()
        called = {"run_tests{test_path:'tests/'}": 1}
        ok, _ = check_tests_pass({}, agent=agent, tool_name="edit_file", called_tools=called)
        self.assertTrue(ok)

    def test_require_run_false(self):
        agent = FakeAgent()
        ok, _ = check_tests_pass({"require_run": False}, agent=agent, tool_name="edit_file")
        self.assertTrue(ok)


class TestCheckPhaseDoneExtended(unittest.TestCase):
    """Verify check_phase_done dispatches to the new check types."""

    def test_min_text_length_dispatch(self):
        agent = FakeAgent(template="refactor")
        task = FakeTask("Analyse")
        ok, msg = check_phase_done(agent, task, full_response="x" * 600, tool_name="read_location")
        self.assertTrue(ok, msg)
        self.assertIn("all_of", msg)
        self.assertIn("2 sub-checks bestod", msg)

    def test_min_text_length_fail(self):
        agent = FakeAgent(template="refactor")
        task = FakeTask("Analyse")
        ok, _ = check_phase_done(agent, task, full_response="kort")
        self.assertFalse(ok)

    def test_tool_called_dispatch(self):
        agent = FakeAgent(template="bugfix")
        task = FakeTask("Implementering")
        ok, _ = check_phase_done(agent, task, tool_name="edit_file")
        self.assertTrue(ok)

    def test_tool_called_dispatch_fails(self):
        agent = FakeAgent(template="bugfix")
        task = FakeTask("Implementering")
        ok, _ = check_phase_done(agent, task, tool_name="read_chunk")
        self.assertFalse(ok)

    def test_tests_pass_dispatch(self):
        agent = FakeAgent(template="refactor", tests_failed=False)
        task = FakeTask("Test")
        ok, _ = check_phase_done(agent, task, tool_name="run_tests")
        self.assertTrue(ok)

    def test_code_contains_dispatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            open(os.path.join(tmp, "api_server.py"), "w").write("from routes import bp\n")
            agent = FakeAgent(template="refactor")
            task = FakeTask("Opdat\u00e9r")
            ok, _ = check_phase_done(agent, task, base_dir=tmp)
            self.assertTrue(ok)

    def test_refactor_opdater_has_seven_patterns(self):
        cfg = TEMPLATE_PHASE_CHECKS.get("refactor", {}).get("Opdat\u00e9r")
        self.assertEqual(cfg["type"], "code_contains")
        patterns = cfg["patterns"]
        self.assertEqual(len(patterns), 7, f"expected 7 separate patterns, got {len(patterns)}: {patterns}")
        for mod in ("routes", "session_manager", "file_handler", "image_handler",
                    "model_manager", "security", "helpers"):
            self.assertTrue(
                any(mod in p for p in patterns),
                f"missing import for {mod} in Opdatér patterns: {patterns}",
            )

    def test_refactor_opdater_uses_explicit_description(self):
        cfg = TEMPLATE_PHASE_CHECKS.get("refactor", {}).get("Opdat\u00e9r")
        self.assertIn("description", cfg)
        self.assertIn("{source_file}", cfg["description"])


class TestSymbolsCoveredByModules(unittest.TestCase):
    """Verify check_symbols_covered_by_modules — the new refactor quality gate."""

    def _setup(self, tmp, source_content: str, plan_content: str, modules: dict[str, str] | None = None) -> str:
        """Write source file, plan, and module files. Return base_dir."""
        open(os.path.join(tmp, "api_server.py"), "w").write(source_content)
        open(os.path.join(tmp, "refactor_plan.md"), "w").write(plan_content)
        for name, content in (modules or {}).items():
            open(os.path.join(tmp, name), "w").write(content)
        return tmp

    def test_all_symbols_in_exactly_one_module(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._setup(
                tmp,
                source_content="def index():\n    pass\n\ndef save():\n    pass\n",
                plan_content="### routes.py\n### session_manager.py\n",
                modules={
                    "routes.py": "def index():\n    return 'hi'\n",
                    "session_manager.py": "def save():\n    return None\n",
                },
            )
            ok, msg = check_symbols_covered_by_modules(
                {"source_file": "api_server.py", "plan_path": "refactor_plan.md"},
                base_dir=tmp,
            )
            self.assertTrue(ok, msg)
            self.assertIn("præcis ét modul", msg)

    def test_missing_symbol_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._setup(
                tmp,
                source_content="def index():\n    pass\n\ndef save():\n    pass\n",
                plan_content="### routes.py\n### session_manager.py\n",
                modules={
                    "routes.py": "def index():\n    return 'hi'\n",
                    # session_manager.py missing — no `save` defined anywhere
                    "session_manager.py": "",
                },
            )
            ok, msg = check_symbols_covered_by_modules(
                {"source_file": "api_server.py", "plan_path": "refactor_plan.md"},
                base_dir=tmp,
            )
            self.assertFalse(ok)
            self.assertIn("save", msg)
            self.assertIn("mangler", msg)

    def test_duplicated_symbol_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._setup(
                tmp,
                source_content="def index():\n    pass\n",
                plan_content="### routes.py\n### helpers.py\n",
                modules={
                    "routes.py": "def index():\n    return 'a'\n",
                    "helpers.py": "def index():\n    return 'b'\n",  # duplicated
                },
            )
            ok, msg = check_symbols_covered_by_modules(
                {"source_file": "api_server.py", "plan_path": "refactor_plan.md"},
                base_dir=tmp,
            )
            self.assertFalse(ok)
            self.assertIn("duplikeret", msg)
            self.assertIn("index", msg)

    def test_dunder_names_excluded_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._setup(
                tmp,
                source_content="def __init__():\n    pass\n\ndef index():\n    pass\n",
                plan_content="### routes.py\n",
                modules={"routes.py": "def index():\n    return 1\n"},
            )
            ok, msg = check_symbols_covered_by_modules(
                {"source_file": "api_server.py", "plan_path": "refactor_plan.md"},
                base_dir=tmp,
            )
            self.assertTrue(ok, msg)

    def test_custom_exclude_pattern(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._setup(
                tmp,
                source_content="def LEGACY():\n    pass\ndef index():\n    pass\n",
                plan_content="### routes.py\n",
                modules={"routes.py": "def index():\n    return 1\n"},
            )
            ok, msg = check_symbols_covered_by_modules(
                {
                    "source_file": "api_server.py",
                    "plan_path": "refactor_plan.md",
                    "exclude_patterns": [r"^LEGACY$", r"^__[A-Za-z0-9_]+__$"],
                },
                base_dir=tmp,
            )
            self.assertTrue(ok, msg)

    def test_class_with_method_covered(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._setup(
                tmp,
                source_content="class App:\n    def run(self):\n        pass\n",
                plan_content="### app.py\n",
                modules={"app.py": "class App:\n    def run(self):\n        return 1\n"},
            )
            ok, _ = check_symbols_covered_by_modules(
                {"source_file": "api_server.py", "plan_path": "refactor_plan.md"},
                base_dir=tmp,
            )
            self.assertTrue(ok)

    def test_module_missing_blocks_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._setup(
                tmp,
                source_content="def index():\n    pass\n",
                plan_content="### routes.py\n### session_manager.py\n",
                modules={"routes.py": "def index():\n    return 1\n"},
                # session_manager.py missing
            )
            ok, msg = check_symbols_covered_by_modules(
                {"source_file": "api_server.py", "plan_path": "refactor_plan.md"},
                base_dir=tmp,
            )
            self.assertFalse(ok)
            self.assertIn("session_manager.py", msg)
            self.assertIn("venter", msg)

    def test_require_all_modules_false_runs_even_with_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._setup(
                tmp,
                source_content="def index():\n    pass\ndef save():\n    pass\n",
                plan_content="### routes.py\n### session_manager.py\n",
                modules={"routes.py": "def index():\n    return 1\n"},
            )
            ok, msg = check_symbols_covered_by_modules(
                {
                    "source_file": "api_server.py",
                    "plan_path": "refactor_plan.md",
                    "require_all_modules": False,
                },
                base_dir=tmp,
            )
            self.assertFalse(ok)
            # Should report `save` as missing across all existing modules
            self.assertIn("save", msg)

    def test_empty_source_file_passes_vacuously(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._setup(
                tmp,
                source_content="pass\n",
                plan_content="### routes.py\n",
                modules={"routes.py": "def index():\n    return 1\n"},
            )
            ok, msg = check_symbols_covered_by_modules(
                {"source_file": "api_server.py", "plan_path": "refactor_plan.md"},
                base_dir=tmp,
            )
            self.assertTrue(ok, msg)
            self.assertIn("ingen", msg)

    def test_syntax_error_in_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._setup(
                tmp,
                source_content="def broken(:\n",
                plan_content="### routes.py\n",
                modules={"routes.py": ""},
            )
            ok, msg = check_symbols_covered_by_modules(
                {"source_file": "api_server.py", "plan_path": "refactor_plan.md"},
                base_dir=tmp,
            )
            self.assertFalse(ok)
            self.assertIn("syntaksfejl", msg)

    def test_source_file_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            open(os.path.join(tmp, "refactor_plan.md"), "w").write("### routes.py\n")
            ok, msg = check_symbols_covered_by_modules(
                {"source_file": "api_server.py", "plan_path": "refactor_plan.md"},
                base_dir=tmp,
            )
            self.assertFalse(ok)
            self.assertIn("findes ikke", msg)

    def test_plan_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            open(os.path.join(tmp, "api_server.py"), "w").write("def x(): pass\n")
            ok, _ = check_symbols_covered_by_modules(
                {"source_file": "api_server.py", "plan_path": "refactor_plan.md"},
                base_dir=tmp,
            )
            self.assertFalse(ok)

    def test_module_parse_failure_counts_as_no_symbols(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._setup(
                tmp,
                source_content="def index():\n    pass\n",
                plan_content="### routes.py\n",
                modules={"routes.py": "def broken(:\n"},  # syntax error
            )
            ok, msg = check_symbols_covered_by_modules(
                {"source_file": "api_server.py", "plan_path": "refactor_plan.md"},
                base_dir=tmp,
            )
            self.assertFalse(ok)
            self.assertIn("index", msg)


class TestAllOf(unittest.TestCase):
    """Verify check_all_of — compound check used by refactor Ekstraher."""

    def test_all_pass(self):
        spec = {
            "checks": [
                {"type": "min_text_length", "min_chars": 5},
                {"type": "tool_called", "tools": ["edit_file"]},
            ]
        }
        ok, msg = check_all_of(
            spec, tool_name="edit_file", full_response="hello world",
        )
        self.assertTrue(ok, msg)
        self.assertIn("alle", msg)

    def test_first_fails_short_circuits(self):
        spec = {
            "checks": [
                {"type": "min_text_length", "min_chars": 100},
                {"type": "tool_called", "tools": ["edit_file"]},
            ]
        }
        ok, msg = check_all_of(spec, tool_name="edit_file", full_response="x")
        self.assertFalse(ok)
        self.assertIn("min_text_length", msg)
        self.assertIn("fejlede", msg)

    def test_second_fails(self):
        spec = {
            "checks": [
                {"type": "min_text_length", "min_chars": 5},
                {"type": "tool_called", "tools": ["edit_file"]},
            ]
        }
        ok, _ = check_all_of(spec, tool_name="read_chunk", full_response="hello world")
        self.assertFalse(ok)

    def test_no_sub_checks(self):
        ok, _ = check_all_of({"checks": []})
        self.assertFalse(ok)

    def test_unknown_sub_type(self):
        ok, msg = check_all_of({"checks": [{"type": "nonexistent"}]})
        self.assertFalse(ok)
        self.assertIn("unknown", msg)

    def test_nested_compound_with_filesymbols(self):
        with tempfile.TemporaryDirectory() as tmp:
            open(os.path.join(tmp, "api_server.py"), "w").write("def x(): pass\n")
            open(os.path.join(tmp, "refactor_plan.md"), "w").write("### routes.py\n")
            open(os.path.join(tmp, "routes.py"), "w").write("def x():\n    return 1\n")
            spec = {
                "checks": [
                    {"type": "files_from_plan", "plan_path": "refactor_plan.md", "min_files": 1},
                    {"type": "symbols_covered", "source_file": "api_server.py", "plan_path": "refactor_plan.md"},
                ]
            }
            ok, msg = check_all_of(spec, base_dir=tmp)
            self.assertTrue(ok, msg)

    def test_dispatch_from_check_phase_done(self):
        with tempfile.TemporaryDirectory() as tmp:
            open(os.path.join(tmp, "api_server.py"), "w").write("def x(): pass\n")
            open(os.path.join(tmp, "refactor_plan.md"), "w").write("### routes.py\n")
            open(os.path.join(tmp, "routes.py"), "w").write("def x():\n    return 1\n")
            agent = FakeAgent(template="refactor")
            task = FakeTask("Ekstraher")
            ok, msg = check_phase_done(agent, task, base_dir=tmp)
            self.assertTrue(ok, msg)
            self.assertIn("all_of", msg)

    def test_dispatch_fails_when_modules_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            open(os.path.join(tmp, "api_server.py"), "w").write("def x(): pass\n")
            open(os.path.join(tmp, "refactor_plan.md"), "w").write("### routes.py\n")
            # routes.py missing
            agent = FakeAgent(template="refactor")
            task = FakeTask("Ekstraher")
            ok, msg = check_phase_done(agent, task, base_dir=tmp)
            self.assertFalse(ok)
            self.assertIn("all_of", msg)


if __name__ == "__main__":
    unittest.main()
