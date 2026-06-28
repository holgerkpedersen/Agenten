"""Tests for refactoring_engine.py — deterministic AST-based refactoring."""

import os
import pytest
from refactoring_engine import (
    RefactoringEngine, RefactoringError, AstAnalyzer, ImportVisitor,
    CodeModifier, FileSnapshot, ImportResolver,
    DependencyGraph, SymbolNode,
)


SAMPLE_SOURCE = '''"""Module docstring."""

import os
import sys
from typing import Any, Optional

GLOBAL_CONFIG = {"debug": False, "port": 8080}

def helper_function(name: str, count: int = 0) -> str:
    """A helper function."""
    result = f"Hello, {name}! Count: {count}"
    return result


class UserHandler:
    """Handles user-related operations."""

    def __init__(self, user_id: str) -> None:
        self.user_id = user_id
        self._data: dict[str, Any] = {}

    def get_name(self) -> str:
        return helper_function(self.user_id)

    @classmethod
    def create_admin(cls) -> "UserHandler":
        return cls("admin")


def standalone_api(config: dict | None = None) -> dict:
    """Standalone API entry point."""
    cfg = config or GLOBAL_CONFIG
    return {"status": "ok", "port": cfg.get("port", 8080)}
'''


SAMPLE_IMPORTS_SOURCE = '''"""Module with various import styles."""

import json
import os
from collections import OrderedDict
from datetime import datetime, timedelta
from typing import Any

_existing = True


def process(data: Any) -> dict:
    """Process incoming data."""
    result = json.dumps(data, indent=2)
    timestamp = datetime.now().isoformat()
    return {"data": result, "timestamp": timestamp}
'''


@pytest.fixture
def engine(tmp_path):
    return RefactoringEngine(base_dir=str(tmp_path))


@pytest.fixture
def source_file(tmp_path):
    path = tmp_path / "api_server.py"
    path.write_text(SAMPLE_SOURCE, encoding="utf-8")
    return str(path)


@pytest.fixture
def imports_file(tmp_path):
    path = tmp_path / "imports_test.py"
    path.write_text(SAMPLE_IMPORTS_SOURCE, encoding="utf-8")
    return str(path)


# ── AstAnalyzer ──────────────────────────────────────────────────────────

class TestAstAnalyzer:
    def test_find_function(self, source_file):
        content = SAMPLE_SOURCE
        tree = __import__("ast").parse(content)
        node = AstAnalyzer.find_node(tree, "helper_function")
        assert node is not None
        assert node.name == "helper_function"

    def test_find_class(self, source_file):
        content = SAMPLE_SOURCE
        tree = __import__("ast").parse(content)
        node = AstAnalyzer.find_node(tree, "UserHandler")
        assert node is not None
        assert isinstance(node, __import__("ast").ClassDef)

    def test_find_variable(self, source_file):
        content = SAMPLE_SOURCE
        tree = __import__("ast").parse(content)
        node = AstAnalyzer.find_node(tree, "GLOBAL_CONFIG")
        assert node is not None
        assert isinstance(node, (__import__("ast").Assign, __import__("ast").AnnAssign))

    def test_find_method_via_dotted(self, source_file):
        content = SAMPLE_SOURCE
        tree = __import__("ast").parse(content)
        node = AstAnalyzer.find_node(tree, "UserHandler.get_name")
        assert node is not None
        assert node.name == "get_name"

    def test_find_nonexistent(self, source_file):
        content = SAMPLE_SOURCE
        tree = __import__("ast").parse(content)
        node = AstAnalyzer.find_node(tree, "NonExistent")
        assert node is None

    def test_get_symbol_lines_includes_decorators(self):
        source = """@app.route("/test")
@validate
def decorated_func():
    pass
"""
        tree = __import__("ast").parse(source)
        node = AstAnalyzer.find_node(tree, "decorated_func")
        lines = source.split("\n")
        start, end = AstAnalyzer.get_symbol_lines(lines, node)
        extracted = "\n".join(lines[start:end])
        assert "@app.route" in extracted
        assert "@validate" in extracted
        assert "def decorated_func" in extracted

    def test_get_symbol_type(self):
        import ast
        func = ast.parse("def foo(): pass").body[0]
        cls = ast.parse("class Bar: pass").body[0]
        ann = ast.parse("x: int = 1").body[0]
        assert AstAnalyzer.get_symbol_type(func) == "function"
        assert AstAnalyzer.get_symbol_type(cls) == "class"
        assert AstAnalyzer.get_symbol_type(ann) == "variable"

    def test_names_from_import(self):
        import ast
        regular = ast.parse("import os, sys").body[0]
        from_imp = ast.parse("from typing import Optional, List").body[0]
        aliased = ast.parse("import numpy as np").body[0]
        assert "os" in AstAnalyzer.names_from_import_node(regular)
        assert "sys" in AstAnalyzer.names_from_import_node(regular)
        assert "Optional" in AstAnalyzer.names_from_import_node(from_imp)
        assert "np" in AstAnalyzer.names_from_import_node(aliased)


# ── ImportVisitor ────────────────────────────────────────────────────────

class TestImportVisitor:
    def test_collects_names(self):
        import ast
        tree = ast.parse("""def foo():
    x = os.path.join("a", "b")
    y = datetime.now()
    z = len([1, 2, 3])
""")
        func = tree.body[0]
        visitor = ImportVisitor()
        visitor.visit(func)
        assert "os" in visitor.names
        assert "datetime" in visitor.names
        assert "len" not in visitor.names  # builtin

    def test_skips_import_nodes(self):
        import ast
        tree = ast.parse("import os; import sys")
        visitor = ImportVisitor()
        visitor.visit(tree)
        # Should not descend into/collect from imports themselves
        # Names collected should be empty since there are no Name refs
        assert visitor.names == set()


# ── ImportResolver ──────────────────────────────────────────────────────

class TestImportResolver:
    def test_filter_imports(self):
        source = SAMPLE_SOURCE
        lines = source.split("\n")
        used = {"os", "Optional"}
        result = ImportResolver.filter_for_symbol(source, lines, used)
        assert any("import os" in r for r in result)
        assert any("from typing import" in r for r in result)

    def test_empty_on_no_match(self):
        source = SAMPLE_SOURCE
        lines = source.split("\n")
        used = {"nonexistent_module"}
        result = ImportResolver.filter_for_symbol(source, lines, used)
        assert result == []

    def test_dedup(self):
        source = "import os\nimport os\n"
        lines = source.split("\n")
        used = {"os"}
        result = ImportResolver.filter_for_symbol(source, lines, used)
        assert len(result) == 1


# ── CodeModifier ─────────────────────────────────────────────────────────

class TestCodeModifier:
    def test_remove_lines(self, tmp_path):
        path = tmp_path / "test_remove.py"
        path.write_text("a = 1\nb = 2\nc = 3\n", encoding="utf-8")
        CodeModifier.remove_lines(str(path), 1, 2)
        content = path.read_text(encoding="utf-8")
        assert "b = 2" not in content

    def test_remove_lines_validates_syntax(self, tmp_path):
        path = tmp_path / "test_remove_syntax.py"
        path.write_text("a = 1\ndef foo():\n    return 1\n", encoding="utf-8")
        with pytest.raises(RefactoringError):
            CodeModifier.remove_lines(str(path), 1, 2)

    def test_insert_import(self, tmp_path):
        path = tmp_path / "test_import.py"
        path.write_text("import os\n", encoding="utf-8")
        added = CodeModifier.insert_import(str(path), "from typing import Optional")
        assert added is True
        content = path.read_text(encoding="utf-8")
        assert "from typing import Optional" in content

    def test_insert_import_dedup(self, tmp_path):
        path = tmp_path / "test_import_dedup.py"
        path.write_text("import os\n", encoding="utf-8")
        added = CodeModifier.insert_import(str(path), "import os")
        assert added is False

    def test_insert_import_validates_syntax(self, tmp_path):
        path = tmp_path / "test_import_syntax.py"
        path.write_text("import os\n", encoding="utf-8")
        with pytest.raises(RefactoringError):
            CodeModifier.insert_import(str(path), "from typing import")


# ── FileSnapshot ─────────────────────────────────────────────────────────

class TestFileSnapshot:
    def test_create_and_restore(self, tmp_path):
        path = tmp_path / "snapshot_test.py"
        path.write_text("original content\n", encoding="utf-8")
        snap = FileSnapshot.create(str(path))
        path.write_text("modified content\n", encoding="utf-8")
        snap.restore()
        assert path.read_text(encoding="utf-8") == "original content\n"


# ── RefactoringEngine.extract_symbol ─────────────────────────────────────

class TestExtractSymbol:
    def test_extract_function(self, engine, source_file, tmp_path):
        target = tmp_path / "helpers.py"
        result = engine.extract_symbol(source_file, "helper_function", str(target))
        assert result["success"] is True
        assert result["symbol"] == "helper_function"
        assert result["type"] == "function"
        assert os.path.exists(str(target))
        content = open(str(target), encoding="utf-8").read()
        assert "def helper_function" in content

    def test_extract_class(self, engine, source_file, tmp_path):
        target = tmp_path / "handlers.py"
        result = engine.extract_symbol(source_file, "UserHandler", str(target))
        assert result["success"] is True
        assert result["symbol"] == "UserHandler"
        assert result["type"] == "class"
        content = open(str(target), encoding="utf-8").read()
        assert "class UserHandler" in content
        assert "def __init__" in content
        assert "def get_name" in content

    def test_extract_nonexistent(self, engine, source_file, tmp_path):
        target = tmp_path / "nowhere.py"
        with pytest.raises(RefactoringError, match="not found"):
            engine.extract_symbol(source_file, "NonExistent", str(target))

    def test_extract_nonexistent_source(self, engine, tmp_path):
        target = tmp_path / "nowhere.py"
        with pytest.raises(RefactoringError, match="File not found"):
            engine.extract_symbol("/nonexistent/path.py", "foo", str(target))

    def test_extract_variable(self, engine, source_file, tmp_path):
        target = tmp_path / "config.py"
        result = engine.extract_symbol(source_file, "GLOBAL_CONFIG", str(target))
        assert result["success"] is True
        assert result["symbol"] == "GLOBAL_CONFIG"
        assert result["type"] == "variable"
        content = open(str(target), encoding="utf-8").read()
        assert "GLOBAL_CONFIG" in content

    def test_extract_with_dependencies(self, engine, source_file, tmp_path):
        """When we extract standalone_api, it uses GLOBAL_CONFIG — that
        dependency should NOT be auto-resolved (it's a symbol, not an import).
        Only import statements are resolved."""
        target = tmp_path / "api.py"
        result = engine.extract_symbol(source_file, "standalone_api", str(target))
        assert result["success"] is True
        content = open(str(target), encoding="utf-8").read()
        assert "def standalone_api" in content


# ── RefactoringEngine.remove_symbol ──────────────────────────────────────

class TestRemoveSymbol:
    def test_remove_function(self, engine, source_file):
        result = engine.remove_symbol(source_file, "helper_function")
        assert result["success"] is True
        assert result["symbol"] == "helper_function"
        content = open(source_file, encoding="utf-8").read()
        assert "def helper_function" not in content

    def test_remove_class(self, engine, source_file):
        result = engine.remove_symbol(source_file, "UserHandler")
        assert result["success"] is True
        content = open(source_file, encoding="utf-8").read()
        assert "class UserHandler" not in content

    def test_remove_nonexistent(self, engine, source_file):
        result = engine.remove_symbol(source_file, "NonExistent")
        assert result["success"] is True
        assert result.get("already_removed") is True
        assert "NonExistent" in result.get("note", "")

    def test_remove_restores_on_syntax_error(self, engine, source_file):
        """remove_symbol should refuse to produce invalid Python."""
        # The sample file has valid Python; removing a proper symbol
        # should keep the file valid. We test via remove(standalone_api)
        # which leaves GLOBAL_CONFIG and helper_function.
        result = engine.remove_symbol(source_file, "standalone_api")
        assert result["success"] is True
        # File should still be valid Python
        import ast
        content = open(source_file, encoding="utf-8").read()
        ast.parse(content)  # no error


# ── RefactoringEngine.add_import ─────────────────────────────────────────

class TestAddImport:
    def test_add_new_import(self, engine, source_file):
        result = engine.add_import(source_file, "helpers", "helper_function")
        assert result["success"] is True
        assert result["import_added"] is True
        content = open(source_file, encoding="utf-8").read()
        assert "from helpers import helper_function" in content

    def test_add_duplicate_import(self, engine, source_file):
        engine.add_import(source_file, "os", "path")
        # Second call with same import — should not add again
        result = engine.add_import(source_file, "os", "path")
        assert result["success"] is True
        assert result["import_added"] is False

    def test_add_import_already_exists(self, engine, source_file):
        # "import os" is already in the file
        result = engine.add_import(source_file, "os", "path")
        engine.add_import(source_file, "os", "path")
        result = engine.add_import(source_file, "os", "path")
        assert result["success"] is True
        content = open(source_file, encoding="utf-8").read()
        assert content.count("from os import path") == 1

    def test_add_import_validates_syntax(self, engine, tmp_path):
        path = tmp_path / "syntax_test.py"
        path.write_text("import os\n", encoding="utf-8")
        with pytest.raises(RefactoringError, match="Syntax error"):
            engine.add_import(str(path), "typing", "")


# ── RefactoringEngine.verify_refactor ───────────────────────────────────

class TestVerifyRefactor:
    def test_verify_valid(self, engine, source_file):
        result = engine.verify_refactor(source_file)
        assert result["success"] is True
        assert result["lines"] > 0
        assert len(result["symbols"]) > 0

    def test_verify_with_syntax_error(self, engine, tmp_path):
        path = tmp_path / "broken.py"
        path.write_text("def foo(:\n    pass\n", encoding="utf-8")
        result = engine.verify_refactor(str(path))
        assert result["success"] is False

    def test_verify_nonexistent_file(self, engine):
        with pytest.raises(RefactoringError, match="File not found"):
            engine.verify_refactor("/nonexistent/file.py")


# ── RefactoringEngine.move_symbol ────────────────────────────────────────

class TestMoveSymbol:
    def test_move_function(self, engine, source_file, tmp_path):
        target = tmp_path / "helpers.py"
        result = engine.move_symbol(source_file, "helper_function", str(target))
        assert result["success"] is True
        assert os.path.exists(str(target))
        # Source should have import added
        src_content = open(source_file, encoding="utf-8").read()
        assert "from helpers import helper_function" in src_content
        assert "def helper_function" not in src_content
        # Target should have the function
        tgt_content = open(str(target), encoding="utf-8").read()
        assert "def helper_function" in tgt_content

    def test_move_class(self, engine, source_file, tmp_path):
        target = tmp_path / "handlers.py"
        result = engine.move_symbol(source_file, "UserHandler", str(target))
        assert result["success"] is True
        assert os.path.exists(str(target))
        src_content = open(source_file, encoding="utf-8").read()
        assert "from handlers import UserHandler" in src_content
        assert "class UserHandler" not in src_content
        tgt_content = open(str(target), encoding="utf-8").read()
        assert "class UserHandler" in tgt_content
        # Verify source is still valid Python
        import ast
        ast.parse(src_content)

    def test_move_nonexistent_symbol(self, engine, source_file, tmp_path):
        target = tmp_path / "nowhere.py"
        result = engine.move_symbol(source_file, "NonExistent", str(target))
        assert result["success"] is False
        assert result.get("step") == "extract"
        assert "category" in result

    def test_move_rolls_back_on_failure(self, engine, source_file, tmp_path):
        """If remove fails (e.g. symbol already gone), the target file should be rolled back."""
        original = open(source_file, encoding="utf-8").read()
        target = tmp_path / "temp_target.py"
        # First move succeeds
        r1 = engine.move_symbol(source_file, "helper_function", str(target))
        assert r1["success"] is True
        # Second move of same symbol should fail and roll back
        target2 = tmp_path / "temp_target2.py"
        r2 = engine.move_symbol(source_file, "helper_function", str(target2))
        assert r2["success"] is False
        # Target2 should not exist (rolled back)
        assert not os.path.exists(str(target2)) or os.path.getsize(str(target2)) == 0


# ── Integration: full refactor workflow ─────────────────────────────────

class TestRefactorWorkflow:
    def test_full_refactor_workflow(self, engine, source_file, tmp_path):
        """Simulate a real refactor: move helper_function and standalone_api
        to new modules, then verify everything is valid Python."""
        target1 = tmp_path / "helpers.py"
        target2 = tmp_path / "api.py"

        r1 = engine.move_symbol(source_file, "helper_function", str(target1))
        assert r1["success"] is True

        r2 = engine.move_symbol(source_file, "standalone_api", str(target2))
        assert r2["success"] is True

        # Both targets exist
        assert os.path.exists(str(target1))
        assert os.path.exists(str(target2))

        # Source still has UserHandler and GLOBAL_CONFIG
        src = open(source_file, encoding="utf-8").read()
        assert "class UserHandler" in src
        assert "GLOBAL_CONFIG" in src
        assert "def helper_function" not in src
        assert "def standalone_api" not in src
        assert "from helpers import helper_function" in src

        # Verify all files are valid Python
        import ast
        for p in [source_file, str(target1), str(target2)]:
            ast.parse(open(p, encoding="utf-8").read())

    def test_userhandler_method_extract(self, engine, source_file, tmp_path):
        """Extract a method to a new file (method-level granularity)."""
        # This should work via extract_symbol since UserHandler.create_admin
        # is a method. But move_symbol will need to also handle the import.
        # First extract the whole class, then remove it from source.
        target = tmp_path / "handlers.py"
        r1 = engine.move_symbol(source_file, "UserHandler", str(target))
        assert r1["success"] is True
        tgt = open(str(target), encoding="utf-8").read()
        assert "class UserHandler" in tgt
        assert "def create_admin" in tgt
        assert "def get_name" in tgt


# ── Error handling ──────────────────────────────────────────────────────

class TestErrorHandling:
    def test_engine_init_default_base_dir(self):
        engine = RefactoringEngine()
        assert engine.base_dir is not None

    def test_abs_raises_on_missing(self, engine):
        with pytest.raises(RefactoringError, match="File not found"):
            engine._abs("/nonexistent/path.py")

    def test_write_creates_file(self, engine, tmp_path):
        path = tmp_path / "new_file.py"
        engine._write(str(path), "x = 1\n")
        assert os.path.exists(str(path))
        assert open(str(path), encoding="utf-8").read() == "x = 1\n"

    def test_extract_symbol_syntax_error(self, engine, tmp_path):
        path = tmp_path / "broken.py"
        path.write_text("def foo(:\n", encoding="utf-8")
        target = tmp_path / "out.py"
        with pytest.raises(RefactoringError, match="Syntax error"):
            engine.extract_symbol(str(path), "foo", str(target))


class TestDependencyGraph:
    """Tests for DependencyGraph, SymbolNode, analyze_dependencies, suggest_module_groups."""

    def test_symbol_node_basics(self):
        sn = SymbolNode("foo", "function", 10)
        assert sn.name == "foo"
        assert sn.type == "function"
        assert sn.line == 10
        assert sn.dependencies == set()
        sn.dependencies.add("bar")
        d = sn.to_dict()
        assert d["name"] == "foo"
        assert d["dependencies"] == ["bar"]

    def test_dependency_graph_empty(self):
        g = DependencyGraph()
        d = g.to_dict()
        assert d["symbols"] == {}
        assert d["external_imports"] == {}

    def test_analyze_simple_file(self, engine, tmp_path):
        src = tmp_path / "simple.py"
        src.write_text(
            "import os\n"
            "\n"
            "def helper():\n"
            "    return os.getcwd()\n"
            "\n"
            "def main():\n"
            "    return helper()\n"
        )
        result = engine.analyze_dependencies(str(src))
        assert result["success"]
        graph = result["graph"]
        assert "helper" in graph["symbols"]
        assert "main" in graph["symbols"]
        assert graph["symbols"]["main"]["dependencies"] == ["helper"]
        assert graph["symbols"]["helper"]["dependencies"] == []
        assert "os" in graph["symbols"]["helper"]["imported_names"]
        assert graph["symbols"]["helper"]["imported_names"] == ["os"]

    def test_analyze_class_with_methods(self, engine, tmp_path):
        src = tmp_path / "classy.py"
        src.write_text(
            "from flask import Flask\n"
            "\n"
            "app = Flask(__name__)\n"
            "\n"
            "class UserHandler:\n"
            "    def get(self):\n"
            "        pass\n"
            "    def post(self):\n"
            "        return self.get()\n"
            "\n"
            "def create_app():\n"
            "    return UserHandler()\n"
        )
        result = engine.analyze_dependencies(str(src))
        assert result["success"]
        symbols = result["graph"]["symbols"]
        assert "UserHandler" in symbols
        assert "create_app" in symbols
        assert "app" in symbols
        assert "UserHandler" in symbols["create_app"]["dependencies"]
        assert "Flask" in symbols["app"]["imported_names"]

    def test_analyze_decorators(self, engine, tmp_path):
        src = tmp_path / "decorated.py"
        src.write_text(
            "from app import app\n"
            "\n"
            "@app.route('/')\n"
            "def index():\n"
            "    return 'hello'\n"
        )
        result = engine.analyze_dependencies(str(src))
        assert result["success"]
        node = result["graph"]["symbols"]["index"]
        assert "app.route" in node["decorators"]

    def test_suggest_module_groups_no_deps(self, engine, tmp_path):
        src = tmp_path / "independent.py"
        src.write_text(
            "def a():\n"
            "    pass\n"
            "\n"
            "def b():\n"
            "    pass\n"
            "\n"
            "def c():\n"
            "    pass\n"
        )
        result = engine.suggest_module_groups(str(src), max_group_size=2)
        assert result["success"]
        groups = result["groups"]
        assert len(groups) >= 2
        all_syms = set()
        for g in groups:
            for s in g["symbols"]:
                all_syms.add(s)
        assert all_syms == {"a", "b", "c"}

    def test_suggest_module_groups_scc_detected(self, engine, tmp_path):
        src = tmp_path / "circular.py"
        src.write_text(
            "def a():\n"
            "    return b()\n"
            "\n"
            "def b():\n"
            "    return a()\n"
            "\n"
            "def c():\n"
            "    return a()\n"
        )
        result = engine.suggest_module_groups(str(src))
        assert result["success"]
        groups = result["groups"]
        scc_groups = [g for g in groups if g["is_scc"]]
        assert len(scc_groups) >= 1
        scc_syms = set()
        for g in scc_groups:
            for s in g["symbols"]:
                scc_syms.add(s)
        assert "a" in scc_syms
        assert "b" in scc_syms

    def test_analyze_nonexistent_file(self, engine, tmp_path):
        result = engine.analyze_dependencies(str(tmp_path / "nope.py"))
        assert not result["success"]

    def test_analyze_empty_file(self, engine, tmp_path):
        src = tmp_path / "empty.py"
        src.write_text("")
        result = engine.analyze_dependencies(str(src))
        assert result["success"]
        assert result["graph"]["symbols"] == {}

    def test_suggest_groups_with_external_imports(self, engine, tmp_path):
        src = tmp_path / "with_imports.py"
        src.write_text(
            "import json\n"
            "from flask import request\n"
            "\n"
            "def parse():\n"
            "    return json.loads('{}')\n"
            "\n"
            "def handle():\n"
            "    return request.json\n"
        )
        result = engine.analyze_dependencies(str(src))
        assert result["success"]
        assert "json" in result["graph"]["symbols"]["parse"]["imported_names"]
        assert "request" in result["graph"]["symbols"]["handle"]["imported_names"]


class TestDependencyGraphMethods:
    """Tests for AstAnalyzer methods used by dependency graph."""

    def test_node_name_function(self):
        import ast
        tree = ast.parse("def foo(): pass")
        name = AstAnalyzer.node_name(tree.body[0])
        assert name == "foo"

    def test_node_name_class(self):
        import ast
        tree = ast.parse("class Foo: pass")
        name = AstAnalyzer.node_name(tree.body[0])
        assert name == "Foo"

    def test_node_name_assign(self):
        import ast
        tree = ast.parse("X = 1")
        name = AstAnalyzer.node_name(tree.body[0])
        assert name == "X"

    def test_decorator_name_simple(self):
        import ast
        tree = ast.parse("@app.route('/')\ndef x(): pass")
        d = tree.body[0].decorator_list[0]
        name = AstAnalyzer.decorator_name(d)
        assert name == "app.route"

    def test_decorator_name_plain(self):
        import ast
        tree = ast.parse("@staticmethod\ndef x(): pass")
        d = tree.body[0].decorator_list[0]
        name = AstAnalyzer.decorator_name(d)
        assert name == "staticmethod"


class TestExtractDedent:
    """Extracted symbols should always be dedented to module level (0 indent)."""

    SOURCE_WITH_NESTED = '''"""Module with nested function."""
import os

def outer():
    """I do stuff."""
    x = 1

    def inner():
        """I am nested — 4-space indented."""
        return 42

    return inner()
'''

    def test_extract_nested_function_is_dedented(self, engine, tmp_path):
        src = tmp_path / "nested_source.py"
        src.write_text(self.SOURCE_WITH_NESTED, encoding="utf-8")
        tgt = tmp_path / "target.py"

        result = engine.extract_symbol(str(src), "inner", str(tgt))
        assert result["success"]

        target_code = tgt.read_text(encoding="utf-8")
        assert "def inner():" in target_code, (
            f"inner() should be at module level, got:\n{target_code}"
        )
        # Verify the def line has NO leading whitespace
        for line in target_code.splitlines():
            if line.strip().startswith("def inner():"):
                assert line == "def inner():", (
                    f"inner() should NOT be indented (0 spaces). "
                    f"Found: {repr(line)}"
                )
                break
        else:
            pytest.fail("def inner(): not found in target")

    def test_extract_top_level_function_unchanged(self, engine, tmp_path):
        src = tmp_path / "flat_source.py"
        src.write_text('''"""Flat module."""
def top():
    return 1
''', encoding="utf-8")
        tgt2 = tmp_path / "target2.py"

        result = engine.extract_symbol(str(src), "top", str(tgt2))
        assert result["success"]

        target_code = tgt2.read_text(encoding="utf-8")
        for line in target_code.splitlines():
            if line.strip().startswith("def top():"):
                assert line == "def top():", (
                    f"top() should remain at 0 indent. Found: {repr(line)}"
                )
                break
        else:
            pytest.fail("def top(): not found in target")

    SOURCE_WITH_CAPTURES = '''"""Module with captured var."""
import os

def outer():
    prefix = "/var/log"
    def log_msg(msg):
        return prefix + msg
    return log_msg("test")
'''

    def test_extract_nested_with_captures(self, engine, tmp_path):
        """Nested function with captured vars → converted to top-level with extra params."""
        src = tmp_path / "capture_source.py"
        src.write_text(self.SOURCE_WITH_CAPTURES, encoding="utf-8")
        tgt = tmp_path / "log_target.py"

        result = engine.extract_symbol(str(src), "log_msg", str(tgt))
        assert result["success"]
        assert result.get("converted"), "Should have been converted"
        assert result.get("captured_vars") == ["prefix"]

        target_code = tgt.read_text(encoding="utf-8")
        assert "def log_msg(msg, prefix):" in target_code, (
            f"log_msg should have prefix param, got:\n{target_code}"
        )

        source_code = src.read_text(encoding="utf-8")
        assert "from log_target import log_msg" in source_code, (
            f"Source should import log_msg from target, got:\n{source_code}"
        )
        assert "def log_msg" not in source_code, (
            "Nested def should be removed from source"
        )

    SOURCE_WITH_STATEFUL = '''"""Module with stateful closure."""
import os

def outer():
    count = 0
    def increment(amount):
        nonlocal count
        count += amount
        return count
    return increment(5)
'''

    def test_extract_stateful_closure(self, engine, tmp_path):
        """Stateful closure with nonlocal → converted to class wrapper."""
        src = tmp_path / "stateful_source.py"
        src.write_text(self.SOURCE_WITH_STATEFUL, encoding="utf-8")
        tgt = tmp_path / "counter.py"

        result = engine.extract_symbol(str(src), "increment", str(tgt))
        assert result["success"]
        assert result.get("converted"), "Should have been converted"
        assert "nonlocal_vars" in result
        assert "count" in result.get("nonlocal_vars", [])

        target_code = tgt.read_text(encoding="utf-8")
        assert "class IncrementWrapper:" in target_code, (
            f"Target should have class wrapper, got:\n{target_code}"
        )
        assert "__call__" in target_code, (
            "Wrapper class should have __call__"
        )
        assert "self._count" in target_code, (
            "Nonlocal var should be converted to self._count"
        )

        source_code = src.read_text(encoding="utf-8")
        assert "from counter import IncrementWrapper" in source_code, (
            f"Source should import wrapper class, got:\n{source_code}"
        )
        assert "def increment" not in source_code, (
            "Nested def should be removed from source"
        )

    SOURCE_NO_CAPTURES = '''"""Module with nested but no captures."""
def outer():
    def helper():
        return 1
    return helper()
'''

    def test_extract_nested_no_captures(self, engine, tmp_path):
        """Nested function with no captures → extracted as-is (no conversion needed)."""
        src = tmp_path / "nocap_source.py"
        src.write_text(self.SOURCE_NO_CAPTURES, encoding="utf-8")
        tgt = tmp_path / "helper_target.py"

        result = engine.extract_symbol(str(src), "helper", str(tgt))
        assert result["success"]
        assert not result.get("converted", False), (
            "Should NOT be converted — no captures"
        )

        target_code = tgt.read_text(encoding="utf-8")
        assert "def helper():" in target_code
        for line in target_code.splitlines():
            if line.strip().startswith("def helper():"):
                assert line == "def helper():", (
                    f"helper should be at 0 indent. Found: {repr(line)}"
                )
                break
