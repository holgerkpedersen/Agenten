"""Agent File tools registration module."""
from __future__ import annotations

import os
from typing import Any

import git_ops
import agent_files
from tools import Tool, _strip_llm_tags
from refactoring_engine import RefactoringEngine
from lang import t
from i18n import K

from agent_helpers import _safe_int, _resolve_t_keys_in_result


def _list_symbols_clean(agent: Any, filepath: str) -> dict[str, Any]:
    """list_symbols filtered to only actually-defined symbols + suggested groups."""
    import ast as _ast
    result = agent_files.list_symbols(filepath=filepath)
    if not result.get("success") or not result.get("symbols"):
        return result

    # Filter out imported-only symbols via AST
    try:
        with open(filepath, "r", encoding="utf-8") as _f:
            _tree = _ast.parse(_f.read())
        _defined = set()
        for _node in _ast.iter_child_nodes(_tree):
            if isinstance(_node, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
                _defined.add(_node.name)
            elif isinstance(_node, _ast.ClassDef):
                _defined.add(_node.name)
            elif isinstance(_node, _ast.Assign):
                for _t in _node.targets:
                    if isinstance(_t, _ast.Name):
                        _defined.add(_t.id)
            elif isinstance(_node, _ast.AnnAssign):
                if isinstance(_node.target, _ast.Name):
                    _defined.add(_node.target.id)
        result["symbols"] = [s for s in result["symbols"] if s.get("name") in _defined]
        result["count"] = len(result["symbols"])
    except Exception:
        pass

    groups = getattr(agent, '_module_groups', None)
    if groups:
        result["suggested_groups"] = groups
    return result


def register_file_tools(agent: Any) -> None:
    """Register file-related tools on the agent's tool registry.

    Args:
        agent: Agent instance with tool_registry attribute.
    """
    refactoring_engine = RefactoringEngine()

    agent.tool_registry.register(Tool(
        "read_location",
        t(K.TOOL_READ_LOCATION, agent.lang),
        ["filepath", "name"],
        lambda filepath, name=None, line_no=None: agent_files.read_location(filepath=filepath, name=name, line_no=line_no),
        optional_params=["line_no"]
    ))

    agent.tool_registry.register(Tool(
        "read_chunk",
        t(K.TOOL_READ_CHUNK, agent.lang),
        ["file_key", "index"],
        lambda file_key, index=1: agent._read_chunk(file_key, int(index))
    ))

    agent.tool_registry.register(Tool(
        "list_chunks",
        t(K.TOOL_LIST_CHUNKS, agent.lang),
        [],
        lambda: agent._list_chunks()
    ))

    agent.tool_registry.register(Tool(
        "list_symbols",
        t(K.TOOL_LIST_SYMBOLS, agent.lang),
        ["filepath"],
        lambda filepath: _list_symbols_clean(agent, filepath)
    ))

    agent.tool_registry.register(Tool(
        "locate",
        t(K.TOOL_LOCATE, agent.lang),
        ["name", "filepath"],
        lambda name=None, filepath=None, line_no=None: _resolve_t_keys_in_result(
            agent_files.locate_code(filepath=filepath, name=name, line_no=line_no)),
        optional_params=["filepath"]
    ))

    agent.tool_registry.register(Tool(
        "edit_file",
        t(K.TOOL_EDIT_FILE, agent.lang),
        ["path", "old_text", "new_text"],
        lambda path, old_text="", new_text="", symbol=None, test_path="": git_ops.edit_file(
            path=path,
            old_text=old_text,
            new_text=new_text,
            expected_hash=agent._file_hash_registry.get(os.path.normcase(os.path.abspath(path))),
            symbol=symbol,
            test_path=test_path,
            llm=agent.llm,
        ),
        optional_params=["symbol", "test_path"]
    ))

    agent.tool_registry.register(Tool(
        "write_file",
        t(K.TOOL_WRITE_FILE, agent.lang),
        ["path", "content"],
        lambda path, content, overwrite=False: git_ops.write_file(path=path, content=content, overwrite=overwrite)
    ))

    agent.tool_registry.register(Tool(
        "list_files",
        t(K.TOOL_LIST_FILES, agent.lang),
        ["path"],
        lambda path=".", pattern="", max_depth=2: git_ops.list_files(path=path, pattern=pattern or None, max_depth=_safe_int(max_depth, 2))
    ))

    agent.tool_registry.register(Tool(
        "delete_file",
        t(K.TOOL_DELETE_FILE, agent.lang),
        ["filepath"],
        lambda filepath: git_ops.delete_file(filepath=filepath)
    ))

    agent.tool_registry.register(Tool(
        "add_image",
        t(K.TOOL_ADD_IMAGE, agent.lang),
        ["path"],
        lambda path: agent._add_image(path)
    ))

    agent.tool_registry.register(Tool(
        "extract_symbol",
        t(K.TOOL_EXTRACT_SYMBOL, agent.lang),
        ["source", "symbol_name", "target"],
        lambda source, symbol_name, target: (
            refactoring_engine.batch_extract_symbols(
                source=source, symbols=symbol_name, target=target
            )
            if "," in str(symbol_name)
            else refactoring_engine.move_symbol(
                source=source, symbol_name=symbol_name, target=target
            )
        )
    ))

    agent.tool_registry.register(Tool(
        "batch_extract_symbols",
        "Flyt flere symboler til et modul i ét kald",
        ["source", "symbols", "target"],
        lambda source, symbols, target: refactoring_engine.batch_extract_symbols(
            source=source, symbols=symbols, target=target
        )
    ))

    agent.tool_registry.register(Tool(
        "remove_symbol",
        t(K.TOOL_REMOVE_SYMBOL, agent.lang),
        ["source", "symbol_name"],
        lambda source, symbol_name: refactoring_engine.remove_symbol(
            source=source, symbol_name=symbol_name
        )
    ))

    agent.tool_registry.register(Tool(
        "add_method",
        t(K.TOOL_ADD_METHOD, agent.lang),
        ["filepath", "class_name", "method_code"],
        lambda filepath, class_name, method_code: git_ops.add_method(
            filepath=filepath, class_name=class_name, method_code=method_code
        )
    ))

    agent.tool_registry.register(Tool(
        "add_function",
        t(K.TOOL_ADD_FUNCTION, agent.lang),
        ["filepath", "function_code"],
        lambda filepath, function_code, after_symbol="": git_ops.add_function(
            filepath=filepath, function_code=function_code, after_symbol=after_symbol
        )
    ))

    agent.tool_registry.register(Tool(
        "add_import",
        t(K.TOOL_ADD_IMPORT, agent.lang),
        ["source", "module", "symbol"],
        lambda source, module, symbol: refactoring_engine.add_import(
            source=source, module=module, symbol=symbol
        )
    ))

    agent.tool_registry.register(Tool(
        "remove_unused_imports",
        t(K.TOOL_REMOVE_UNUSED_IMPORTS, agent.lang),
        ["filepath"],
        lambda filepath: git_ops.remove_unused_imports(filepath=filepath)
    ))

    def _verify_refactor_wrapper(source: str, source_for_deps: str | None = None) -> dict:
        return refactoring_engine.verify_refactor(source=source, source_for_deps=source_for_deps)

    agent.tool_registry.register(Tool(
        "verify_refactor",
        t(K.TOOL_VERIFY_REFACTOR, agent.lang),
        ["source"],
        _verify_refactor_wrapper
    ))

    agent.tool_registry.register(Tool(
        "analyze_dependencies",
        t(K.TOOL_ANALYZE_DEPENDENCIES, agent.lang),
        ["source"],
        lambda source: refactoring_engine.analyze_dependencies(source=source)
    ))

    agent.tool_registry.register(Tool(
        "suggest_module_groups",
        t(K.TOOL_SUGGEST_MODULE_GROUPS, agent.lang),
        ["source"],
        lambda source, max_group_size=5: refactoring_engine.suggest_module_groups(
            source=source, max_group_size=_safe_int(max_group_size, 5)
        )
    ))

    agent.tool_registry.register(Tool(
        "run_extraction_plan",
        t(K.TOOL_RUN_EXTRACTION_PLAN, agent.lang),
        ["source"],
        lambda source, plan_path="refactor_plan.md": refactoring_engine.run_extraction_plan(
            source=source, plan_path=plan_path,
        )
    ))
