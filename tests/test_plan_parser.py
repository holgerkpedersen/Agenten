"""Tests for plan_parser.py — shared plan parser with LLM-specific JSON configs."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from plan_parser import (
    parse_refactor_plan,
    parse_modules_from_plan,
    clear_config_cache,
    load_plan_config,
)


def _plan(mod_heading: str, symbols_line: str = "", extra: str = "") -> str:
    """Build a minimal plan markdown string."""
    lines = [mod_heading]
    if symbols_line:
        lines.append(symbols_line)
    if extra:
        lines.append(extra)
    return "\n".join(lines)


class TestDefaultConfig(unittest.TestCase):
    """Test that default.json loads and handles all supported formats."""

    maxDiff = None

    @classmethod
    def setUpClass(cls):
        clear_config_cache()

    # ---- module heading formats ----

    def test_bare_module_heading(self):
        plan = _plan("## refactor_utils.py", "**Symboler (3):** fn1, fn2, fn3")
        result = parse_refactor_plan(plan)
        self.assertEqual(result, {"refactor_utils.py": ["fn1", "fn2", "fn3"]})

    def test_module_prefix_heading(self):
        plan = _plan("## Module: config.py", "**Symboler (3):** sym1, sym2, sym3")
        result = parse_refactor_plan(plan)
        self.assertEqual(result, {"config.py": ["sym1", "sym2", "sym3"]})

    def test_module_prefix_backtick(self):
        plan = _plan("## Module: `config.py`", "**Symboler (3):** sym1, sym2, sym3")
        result = parse_refactor_plan(plan)
        self.assertEqual(result, {"config.py": ["sym1", "sym2", "sym3"]})

    def test_danish_modul_heading(self):
        plan = _plan("## Modul: config.py", "**Symboler (3):** sym1, sym2, sym3")
        result = parse_refactor_plan(plan)
        self.assertEqual(result, {"config.py": ["sym1", "sym2", "sym3"]})

    def test_danish_modul_numbered(self):
        plan = _plan("## Modul 1: config.py", "**Symboler (3):** sym1, sym2, sym3")
        result = parse_refactor_plan(plan)
        self.assertEqual(result, {"config.py": ["sym1", "sym2", "sym3"]})

    def test_numbered_heading(self):
        plan = _plan("### 1. config.py", "**Symboler (3):** sym1, sym2, sym3")
        result = parse_refactor_plan(plan)
        self.assertEqual(result, {"config.py": ["sym1", "sym2", "sym3"]})

    def test_numbered_heading_paren(self):
        plan = _plan("### 1) config.py", "**Symboler (3):** sym1, sym2, sym3")
        result = parse_refactor_plan(plan)
        self.assertEqual(result, {"config.py": ["sym1", "sym2", "sym3"]})

    def test_no_symbols(self):
        plan = _plan("## refactor_utils.py")
        result = parse_refactor_plan(plan)
        self.assertEqual(result, {})

    # ---- symbol formats ----

    def test_symboler_der_flyttes(self):
        plan = _plan("## refactor_utils.py", "**Symboler der flyttes (3):** fn1, fn2, fn3")
        result = parse_refactor_plan(plan)
        self.assertEqual(result, {"refactor_utils.py": ["fn1", "fn2", "fn3"]})

    def test_symbols_to_move(self):
        plan = _plan("## refactor_utils.py", "**Symbols to move:** fn1, fn2, fn3")
        result = parse_refactor_plan(plan)
        self.assertEqual(result, {"refactor_utils.py": ["fn1", "fn2", "fn3"]})

    def test_symbols_no_count(self):
        plan = _plan("## refactor_utils.py", "**Symboler:** fn1, fn2")
        result = parse_refactor_plan(plan)
        self.assertEqual(result, {"refactor_utils.py": ["fn1", "fn2"]})

    def test_symbol_line_backticks(self):
        plan = _plan("## refactor_utils.py", "**Symboler (2):** `fn1`, `fn2`")
        result = parse_refactor_plan(plan)
        self.assertEqual(result, {"refactor_utils.py": ["fn1", "fn2"]})

    def test_inline_symbol_on_heading(self):
        plan = "## Module: config.py **Symboler (2):** sym1, sym2\nSome description"
        result = parse_refactor_plan(plan)
        self.assertEqual(result, {"config.py": ["sym1", "sym2"]})

    # ---- exclude headings ----

    def test_exclude_forbliver_heading(self):
        plan = (
            "## refactor_utils.py\n"
            "**Symboler (3):** fn1, fn2, fn3\n"
            "\n"
            "## Forbliver i `refactoring_engine.py` — Facade\n"
            "**Symbol der forbliver (1):** RefactoringEngine\n"
        )
        result = parse_refactor_plan(plan)
        self.assertEqual(result, {"refactor_utils.py": ["fn1", "fn2", "fn3"]})
        self.assertNotIn("refactoring_engine.py", result)

    def test_exclude_remains_heading(self):
        plan = (
            "## refactor_utils.py\n"
            "**Symboler (1):** fn1\n"
            "\n"
            "## Remains in main.py\n"
            "**Symboler (1):** MainClass\n"
        )
        result = parse_refactor_plan(plan)
        self.assertEqual(result, {"refactor_utils.py": ["fn1"]})
        self.assertNotIn("main.py", result)

    # ---- multi-module plans ----

    def test_multi_module_plan(self):
        plan = (
            "## refactor_utils.py\n"
            "**Symboler (3):** fn1, fn2, fn3\n"
            "\n"
            "## refactor_error.py\n"
            "**Symboler (2):** Err1, Err2\n"
        )
        result = parse_refactor_plan(plan)
        self.assertEqual(result, {
            "refactor_utils.py": ["fn1", "fn2", "fn3"],
            "refactor_error.py": ["Err1", "Err2"],
        })

    def test_forbliver_does_not_leak_symbols(self):
        """Regression: "Forbliver i" section must not map symbols to previous module."""
        plan = (
            "## code_modifier.py\n"
            "**Symboler (1):** CodeModifier\n"
            "\n"
            "## Forbliver i `refactoring_engine.py` — Facade\n"
            "**Symbol der forbliver (1):** RefactoringEngine\n"
        )
        result = parse_refactor_plan(plan)
        self.assertEqual(result, {"code_modifier.py": ["CodeModifier"]})
        # RefactoringEngine must NOT appear under code_modifier.py
        self.assertNotIn("RefactoringEngine", result.get("code_modifier.py", []))
        self.assertNotIn("refactoring_engine.py", result)

    # ---- edge cases ----

    def test_empty_plan(self):
        self.assertEqual(parse_refactor_plan(""), {})
        self.assertEqual(parse_refactor_plan(None), {})

    def test_no_modules(self):
        plan = "Just some text with no .py references"
        self.assertEqual(parse_refactor_plan(plan), {})

    def test_bullet_fallback(self):
        plan = (
            "## refactor_utils.py\n"
            "**Ansvar:** Helpers\n"
            "- fn1\n"
            "- fn2\n"
        )
        result = parse_refactor_plan(plan)
        self.assertEqual(result, {"refactor_utils.py": ["fn1", "fn2"]})

    def test_single_line_plan(self):
        """Mid-line ## headings should still be parsed (normalization)."""
        plan = "Some text ## Module: config.py **Symboler (1):** sym1 more text"
        result = parse_refactor_plan(plan)
        self.assertEqual(result, {"config.py": ["sym1"]})


class TestParseModulesFromPlan(unittest.TestCase):
    """Test module-only extraction (no symbols)."""

    def test_heading_extraction(self):
        plan = (
            "## refactor_utils.py\n"
            "text\n"
            "## refactor_error.py\n"
            "text\n"
        )
        result = parse_modules_from_plan(plan)
        self.assertEqual(result, ["refactor_error.py", "refactor_utils.py"])

    def test_inline_extraction(self):
        plan = "Use `routes.py` and security.py for these tasks"
        result = parse_modules_from_plan(plan)
        self.assertEqual(result, ["routes.py", "security.py"])

    def test_exclude_forbliver(self):
        plan = (
            "## refactor_utils.py\n"
            "text\n"
            "## Forbliver i `refactoring_engine.py`\n"
            "text\n"
        )
        result = parse_modules_from_plan(plan)
        self.assertEqual(result, ["refactor_utils.py"])
        self.assertNotIn("refactoring_engine.py", result)

    def test_empty(self):
        self.assertEqual(parse_modules_from_plan(""), [])
        self.assertEqual(parse_modules_from_plan(None), [])

    def test_allow_nested(self):
        plan = "## gui/main_window.py\n## utils/helpers.py\n"
        result = parse_modules_from_plan(plan, allow_nested=True)
        self.assertEqual(sorted(result), ["gui/main_window.py", "utils/helpers.py"])

    def test_reject_nested(self):
        plan = "## gui/main_window.py\n## helpers.py\n"
        result = parse_modules_from_plan(plan, allow_nested=False)
        self.assertEqual(result, ["helpers.py"])


class TestModelConfigMatching(unittest.TestCase):
    """Test that model-specific configs are loaded correctly."""

    def setUp(self):
        clear_config_cache()

    def test_default_fallback(self):
        """Unknown model should get default.json."""
        cfg = load_plan_config("unknown-model-v42")
        self.assertEqual(cfg.get("name"), "default")

    def test_minimax_matches(self):
        """Model name containing 'minimax' should load minimax config."""
        cfg = load_plan_config("minimax-m2.5-fp16")
        self.assertEqual(cfg.get("name"), "minimax-m2.5")

    def test_minimax_case_insensitive(self):
        cfg = load_plan_config("MiniMax-M2.5-123b")
        self.assertEqual(cfg.get("name"), "minimax-m2.5")

    def test_none_model_uses_default(self):
        cfg = load_plan_config(None)
        self.assertEqual(cfg.get("name"), "default")

    def test_empty_string_uses_default(self):
        cfg = load_plan_config("")
        self.assertEqual(cfg.get("name"), "default")


class TestConfigSchema(unittest.TestCase):
    """Validate that all JSON configs have required keys."""

    def test_default_has_all_keys(self):
        cfg = load_plan_config("test-unknown")
        self.assertIn("name", cfg)
        self.assertIn("module", cfg)
        self.assertIn("symbols", cfg)
        self.assertIn("module_only", cfg)
        self.assertIn("heading", cfg.get("module", {}))
        self.assertIn("exclude_headings", cfg.get("module", {}))
        self.assertIn("inline", cfg.get("symbols", {}))

    def test_minimax_has_all_keys(self):
        cfg = load_plan_config("minimax-test")
        self.assertEqual(cfg.get("name"), "minimax-m2.5")
        self.assertIn("module", cfg)
        self.assertIn("symbols", cfg)
        self.assertIn("model_patterns", cfg)

    def test_json_files_are_parseable(self):
        import json
        import glob
        for fpath in glob.glob(os.path.join(os.path.dirname(__file__), "..", "llm_plans", "*.json")):
            with open(fpath, encoding="utf-8") as f:
                data = json.load(f)
            self.assertIn("name", data, f"{fpath} missing 'name'")
            self.assertIn("module", data, f"{fpath} missing 'module'")


if __name__ == "__main__":
    unittest.main()
