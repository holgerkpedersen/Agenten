"""Cross-module import verification — catches the #1 refactoring bug.

After symbols are moved between modules, callers in other modules may still
reference the symbol without importing it from its new location. This scanner
builds a project-wide symbol map and flags every call to a known symbol that
isn't imported or defined in the calling module.

Called at server startup (api_server.py). Logs warnings; does NOT block start.
"""

import ast
import os
import builtins
from typing import Any

_BUILTIN_NAMES: set[str] = set(dir(builtins))

# Directories / files to skip during scanning
_SKIP_DIRS: set[str] = {
    "__pycache__", ".git", "venv", "env", "node_modules",
    "sessions", "logs", "uploads", "tests", ".agent_storage",
    "migrations", ".pytest_cache", "css", "js",
}
_SKIP_FILES: set[str] = {"setup.py", "conftest.py"}


def _is_project_py_file(filepath: str) -> bool:
    """Return True if filepath is a project .py file worth scanning."""
    if not filepath.endswith(".py"):
        return False
    basename = os.path.basename(filepath)
    if basename in _SKIP_FILES:
        return False
    # Skip files deep in excluded directories
    for part in filepath.replace("\\", "/").split("/"):
        if part in _SKIP_DIRS:
            return False
    return True


def _find_project_py_files(root_dir: str) -> list[str]:
    """Walk root_dir and return all .py file paths that pass _is_project_py_file."""
    result: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root_dir):
        # Filter directories in-place
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for f in filenames:
            full = os.path.join(dirpath, f)
            if _is_project_py_file(full):
                result.append(full)
    return result


def _get_top_level_symbols(filepath: str) -> set[str]:
    """Extract all top-level defined names from a .py file using AST.

    Includes FunctionDef, AsyncFunctionDef, ClassDef, and module-level
    assignments (Assign, AnnAssign, NamedExpr).
    """
    try:
        with open(filepath, encoding="utf-8") as f:
            tree = ast.parse(f.read())
    except (SyntaxError, UnicodeDecodeError, RecursionError):
        return set()

    symbols: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            symbols.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    symbols.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            symbols.add(node.target.id)

    # Also collect nested function/class defs (they're visible as attributes
    # of their parent, not as bare module-level names — but they CAN be
    # referenced from outside the module via 'from X import inner_func').
    # We include all FunctionDef/ClassDef at ANY depth.
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            symbols.add(node.name)

    return symbols


def build_project_symbol_map(root_dir: str) -> dict[str, str]:
    """Build a {symbol_name: source_filepath} map for the entire project.

    If the same symbol name appears in multiple files, the last file wins
    (unique names are the norm; duplicates are marginal).

    Returns:
        dict mapping each symbol name to the filepath where it's defined.
    """
    symbol_map: dict[str, str] = {}
    for filepath in _find_project_py_files(root_dir):
        for sym in _get_top_level_symbols(filepath):
            symbol_map[sym] = filepath
    return symbol_map


def check_file_for_missing_imports(
    filepath: str,
    symbol_map: dict[str, str],
) -> list[tuple[str, int, str]]:
    """Scan a single .py file for calls to known symbols without a matching import.

    Args:
        filepath: Path to the .py file to check.
        symbol_map: The project-wide symbol map from build_project_symbol_map().

    Returns:
        List of (symbol_name, line_number, defined_in_filepath) tuples for
        each call to a project symbol that is neither imported nor defined
        in *filepath*.
    """
    try:
        with open(filepath, encoding="utf-8") as f:
            tree = ast.parse(f.read())
    except (SyntaxError, UnicodeDecodeError, RecursionError):
        return []

    # --- Collect names that this file has access to ---

    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                imported.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imported.add((alias.asname or alias.name).split(".")[0])

    # All locally defined symbols (any scope)
    locally_defined: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            locally_defined.add(node.name)
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    locally_defined.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            locally_defined.add(node.target.id)

    # --- Find calls to project symbols that aren't imported/defined here ---

    missing: list[tuple[str, int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            name = node.func.id

            # Skip builtins and dunder names
            if name in _BUILTIN_NAMES:
                continue
            if name.startswith("__") and name.endswith("__"):
                continue

            # Must be a KNOWN project symbol defined in ANOTHER file
            defined_in = symbol_map.get(name)
            if not defined_in or defined_in == filepath:
                continue

            # Must NOT be imported or locally defined in this file
            if name in imported or name in locally_defined:
                continue

            missing.append((name, node.func.lineno, defined_in))

    return missing


def verify_all_imports(
    root_dir: str | None = None,
) -> list[tuple[str, list[tuple[str, int, str]]]]:
    """Main entry point — scan entire project for missing cross-module imports.

    Args:
        root_dir: Project root. Defaults to the directory containing this file.

    Returns:
        List of (filepath, [ (symbol, lineno, defined_in_file), ... ]) for
        every file with at least one unimported symbol call.
        Empty list = no issues found.
    """
    if root_dir is None:
        root_dir = os.path.dirname(os.path.abspath(__file__))

    symbol_map = build_project_symbol_map(root_dir)
    issues: list[tuple[str, list[tuple[str, int, str]]]] = []

    for filepath in _find_project_py_files(root_dir):
        missing = check_file_for_missing_imports(filepath, symbol_map)
        if missing:
            issues.append((filepath, missing))

    return issues


def log_import_issues(
    issues: list[tuple[str, list[tuple[str, int, str]]]],
    logger: Any | None = None,
) -> None:
    """Pretty-print import verification results to a logger.

    Normally called at server startup. Uses print() if no logger provided.
    """
    if not issues:
        return

    log_fn = logger.warning if logger else print
    header = logger.info if logger else print

    header("[IMPORT-VERIFY] %d fil(er) med manglende imports fundet:", len(issues))
    for filepath, missing in sorted(issues):
        rel = os.path.relpath(filepath, start=os.path.dirname(__file__))
        log_fn("  %s:", rel)
        for name, lineno, defined_in in missing:
            def_rel = os.path.relpath(defined_in, start=os.path.dirname(__file__))
            log_fn(
                "    linje %d: %s() — findes i %s (ikke importeret her)",
                lineno, name, def_rel,
            )
