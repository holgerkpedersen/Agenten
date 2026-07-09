"""
refactoring_engine — Deterministic AST-based Python refactoring.

All operations are 100% deterministic — zero LLM involvement.
Design patterns used:
  - Strategy: ExtractionStrategy for functions vs classes vs methods
  - Visitor: ImportVisitor walks symbol AST to find name references
  - Command: MoveSymbolCommand is reversible
  - Memento: FileSnapshot for backup/rollback
  - Template Method: move_symbol() defines the skeleton
  - Facade: RefactoringEngine provides a simple public API
"""

import ast
import json
import os
import re
import hashlib
import textwrap
import time as _time
from typing import Any
from collections import defaultdict

from config import get_logger
log = get_logger(__name__)


def _atomic_replace(src: str, dst: str, max_retries: int = 8) -> None:
    """Replace dst with src atomically, retrying on Windows file locks."""
    for attempt in range(max_retries):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt < max_retries - 1:
                _time.sleep(0.15 * (attempt + 1))
            else:
                raise


def _parse_symbols_list(symbols: str | list[str]) -> list[str]:
    """Parse the ``symbols`` parameter into a clean list of symbol names.

    Handles all formats commonly sent by LLMs:
    - ``["sym1", "sym2"]`` — JSON array string
    - ``['sym1', 'sym2']`` — Python list string
    - ``sym1, sym2, sym3`` — comma-separated string
    - ``["sym1", "sym2"]`` as actual list (from JSON API)
    """
    if isinstance(symbols, list):
        return [str(s).strip() for s in symbols if str(s).strip()]

    s = str(symbols).strip()

    # Try JSON array: ["sym1", "sym2"]
    if s.startswith("[") and s.endswith("]"):
        try:
            parsed = json.loads(s)
            if isinstance(parsed, list):
                return [str(item).strip(" \t\"'") for item in parsed if item]
        except (json.JSONDecodeError, TypeError):
            pass

    # Try Python list: ['sym1', 'sym2']
    if s.startswith("[") and s.endswith("]"):
        try:
            parsed = ast.literal_eval(s)
            if isinstance(parsed, list):
                return [str(item).strip(" \t\"'") for item in parsed if item]
        except (ValueError, SyntaxError, TypeError):
            pass

    # Comma-separated
    if "," in s:
        return [p.strip(" \t\"'[]") for p in s.split(",") if p.strip(" \t\"'[]")]

    # Space-separated (no commas found)
    parts = [p.strip(" \t\"'[]") for p in s.split() if p.strip(" \t\"'[]")]
    if parts:
        return parts

    return []


_BUILTINS: frozenset[str] = frozenset({
    'abs', 'all', 'any', 'bool', 'bytes', 'callable', 'chr', 'classmethod',
    'compile', 'complex', 'delattr', 'dict', 'dir', 'divmod', 'enumerate',
    'eval', 'exec', 'filter', 'float', 'format', 'frozenset', 'getattr',
    'globals', 'hasattr', 'hash', 'hex', 'id', 'input', 'int', 'isinstance',
    'issubclass', 'iter', 'len', 'list', 'locals', 'map', 'max', 'min',
    'next', 'object', 'oct', 'open', 'ord', 'pow', 'print', 'property',
    'range', 'repr', 'reversed', 'round', 'set', 'setattr', 'slice',
    'sorted', 'staticmethod', 'str', 'sum', 'super', 'tuple', 'type',
    'vars', 'zip',
    'True', 'False', 'None', 'self', 'cls',
    'Exception', 'ValueError', 'TypeError', 'KeyError', 'IndexError',
    'AttributeError', 'ImportError', 'ModuleNotFoundError', 'StopIteration',
    'RuntimeError', 'OSError', 'IOError', 'FileNotFoundError', 'NotImplementedError',
})

_BUILTINS_TYPING: frozenset[str] = frozenset({
    'Any', 'Optional', 'List', 'Dict', 'Tuple', 'Set', 'Callable',
    'TypeVar', 'Generic', 'Protocol', 'Union', 'Final', 'ClassVar',
    'Sequence', 'Iterable', 'Iterator', 'Generator',
})

# Static mapping of known framework symbols → their import paths.
# batch_extract_symbols auto-adds these to target modules so extracted
# functions actually compile without manual import fixing.
_KNOWN_SYMBOL_IMPORTS: dict[str, str] = {
    # Flask — bare symbols (typically from flask import X)
    "Flask": "flask",
    "request": "flask",
    "jsonify": "flask",
    "Response": "flask",
    "send_from_directory": "flask",
    "stream_with_context": "flask",
    "url_for": "flask",
    "redirect": "flask",
    "abort": "flask",
    "make_response": "flask",
    "send_file": "flask",
    "session": "flask",
    "g": "flask",
    "current_app": "flask",
    "copy_current_request_context": "flask",
    "has_request_context": "flask",
    # Flask extension
    "CORS": "flask_cors",
    # Config
    "app": "config",
    "BASE_DIR": "config",
    "STATIC_DIR": "config",
    "VERSION_FILES": "config",
    "BUILD_INFO": "config",
    "get_logger": "config",
    "log": "config",
    "_is_development_mode": "config",
    "_file_mtime": "config",
    "active_streams": "config",
    "active_streams_lock": "config",
    "current_session_lock": "config",
    # Session manager
    "SessionManager": "session_manager",
    "session_manager": "session_manager",
    "agent": "session_manager",
    "current_session_id": "session_manager",
    "_guard_json_body": "session_manager",
    "execution_status": "session_manager",
    "execution_status_lock": "session_manager",
    "export_folder": "session_manager",
    "export_folder_lock": "session_manager",
    # Agent core
    "Agent": "agent_core",
    # Framework modules
    "TEMPLATE_PHASE_CHECKS": "agent_phase_checks",
    "check_phase_done": "agent_phase_checks",
    "clear_extracted_registry": "refactoring_engine",
    "LMStudioWrapper": "llm_wrapper",
    "K": "i18n",
    "t": "lang",
    "get_ui_translations": "lang",
}

# Reverse map: module → list of (symbol, alias) pairs for from X import Y
_KNOWN_MODULE_SYMBOLS: dict[str, list[tuple[str, str | None]]] = {}
for _sym, _mod in _KNOWN_SYMBOL_IMPORTS.items():
    _KNOWN_MODULE_SYMBOLS.setdefault(_mod, []).append((_sym, None))


def _auto_add_known_imports(target: str) -> list[str]:
    """Scan a Python file for used-but-not-imported names and add known imports.

    Checks each Name reference in the target file against
    ``_KNOWN_SYMBOL_IMPORTS``. If an unimported name matches, adds the
    corresponding ``from <module> import <name>`` at the top of the file.

    Args:
        target: Path to the target .py file.

    Returns:
        List of import strings that were added (empty if none needed).
    """
    if not os.path.exists(target):
        return []
    try:
        with open(target, encoding="utf-8") as f:
            content = f.read()
    except (OSError, UnicodeDecodeError):
        return []
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return []

    # Collect names already defined in the file
    local_defs: set[str] = set()
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            local_defs.add(node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    local_defs.add(t.id)
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name):
                local_defs.add(node.target.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                imports.add(alias.asname or alias.name)
            if isinstance(node, ast.ImportFrom) and node.module:
                imports.update(alias.asname or alias.name for alias in node.names)

    # Collect parameter names from all functions
    param_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for arg in node.args.args:
                param_names.add(arg.arg)
            if node.args.vararg:
                param_names.add(node.args.vararg.arg)
            if node.args.kwarg:
                param_names.add(node.args.kwarg.arg)

    # Collect all Name references (Load context only)
    used: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            if node.id not in local_defs and node.id not in imports \
               and node.id not in param_names and node.id not in _BUILTINS \
               and node.id not in _BUILTINS_TYPING:
                used.add(node.id)

    # For remaining names, check if they're in KNOWN_SYMBOL_IMPORTS
    needed_imports: dict[str, set[str]] = {}
    for name in used:
        mod = _KNOWN_SYMBOL_IMPORTS.get(name)
        if mod:
            needed_imports.setdefault(mod, set()).add(name)

    if not needed_imports:
        return []

    # Read lines and insert imports after existing imports
    lines = content.split("\n")
    insert_pos = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith(("import ", "from ")):
            insert_pos = i + 1
        elif stripped.startswith(('"""', "'''", "#")) and insert_pos == 0:
            # Docstring or comment at start — keep going
            continue
        elif stripped == "" and insert_pos == 0:
            continue
        elif insert_pos > 0:
            break
        else:
            break

    new_imports: list[str] = []
    for mod in sorted(needed_imports):
        symbols = sorted(needed_imports[mod])
        # Check if these exact imports already exist
        existing_imports_str = "\n".join(lines)
        already_there = True
        for sym in symbols:
            pat = f"from {mod} import "
            if pat in existing_imports_str:
                # Already importing from this module
                continue
            already_there = False
        if already_there:
            # All symbols already imported — skip
            continue
        new_imports.append(f"from {mod} import {', '.join(symbols)}")

    if not new_imports:
        return []

    # Insert after the last import line
    for imp in new_imports:
        lines.insert(insert_pos, imp)
        insert_pos += 1

    try:
        with open(target, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        log.info("_auto_add_known_imports: added %d imports to %s: %s",
                 len(new_imports), os.path.basename(target), "; ".join(new_imports))
    except OSError as e:
        log.warning("_auto_add_known_imports: write failed: %s", e)
        return []

    return new_imports


def _list_top_level_symbol_names(content: str) -> set[str]:
    """Return set of all top-level function/class/variable names in content."""
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return set()
    names: set[str] = set()
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    names.add(t.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


def _find_unresolved_local_deps(source_content: str, target_content: str) -> list[str]:
    """Find names used in target_content that are local symbols in source_content.

    These are names that a target file references but that are only defined
    as top-level symbols in the source file (not builtins, not imported,
    not defined in the target itself). This detects the case where extracting
    a function like ``create_user() -> User`` leaves ``User`` undefined in
    the target because ``User`` is a class in the source — not an import.
    """
    try:
        source_tree = ast.parse(source_content)
    except SyntaxError:
        return []
    try:
        target_tree = ast.parse(target_content)
    except SyntaxError:
        return []

    source_symbols = _list_top_level_symbol_names(source_content)

    # Collect names defined/imported in target
    target_defined = _list_top_level_symbol_names(target_content)
    target_imported: set[str] = set()
    for node in ast.walk(target_tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            target_imported |= AstAnalyzer.names_from_import_node(node)

    all_known = target_defined | target_imported | _BUILTINS | _BUILTINS_TYPING

    # Collect ALL Name nodes in target (except imports and definitions)
    used_in_target: set[str] = set()
    for node in ast.walk(target_tree):
        if isinstance(node, ast.Name):
            name = node.id
            # Skip names that are being defined (target of assignment, function def, etc.)
            # We only care about references, not definitions
            if name not in all_known and name not in source_symbols:
                continue
            # Check if this is a reference (not a definition context)
            if name not in target_defined:
                used_in_target.add(name)

    unresolved = sorted(used_in_target & source_symbols)
    return [u for u in unresolved if u not in all_known]


def _detect_import_cycle_risk(
    source_content: str,
    source_path: str,
    target_path: str,
    symbol_code: str,
) -> list[str]:
    """Detect if extracted symbol code references names from source that would
    create a circular import if imported into target.

    A circular import risk exists when:
    1. The extracted code references names defined at module level in source
       (not imports, not builtins, not defined in the symbol itself)
    2. The source file already imports from the target module

    Returns list of risky name references (names that need importing from
    source into target, but source already imports from target → cycle).
    """
    if not os.path.exists(target_path):
        return []

    try:
        source_tree = ast.parse(source_content)
    except SyntaxError:
        return []
    try:
        symbol_tree = ast.parse(textwrap.dedent(symbol_code))
    except SyntaxError:
        return []

    # Names defined locally in the symbol itself
    local_names: set[str] = set()
    for node in ast.walk(symbol_tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            local_names.add(node.name)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            local_names.add(node.id)
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    local_names.add(t.id)

    source_symbols = _list_top_level_symbol_names(source_content)

    # Names referenced in the symbol (Load) that are defined in source
    referenced_from_source: set[str] = set()
    for node in ast.walk(symbol_tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            if node.id in source_symbols and node.id not in local_names:
                referenced_from_source.add(node.id)

    if not referenced_from_source:
        return []

    # Check if source already imports from target → would create a cycle
    target_module_name = os.path.splitext(os.path.basename(target_path))[0]
    for node in ast.walk(source_tree):
        if isinstance(node, ast.ImportFrom):
            if node.module and (node.module == target_module_name or node.module.split('.')[0] == target_module_name):
                return sorted(referenced_from_source)
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == target_module_name or alias.name.split('.')[0] == target_module_name:
                    return sorted(referenced_from_source)

    return []


def _split_imports_from_code(content: str) -> tuple[str, str]:
    """Split file content into (imports_block, code_block).

    All consecutive top-level import statements at the start of the file
    are collected into the imports_block. Everything else is code_block.
    """
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return "", content

    lines = content.split('\n')
    last_import_end = 0
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            end = getattr(node, 'end_lineno', node.lineno) or node.lineno
            if last_import_end == 0 or end < last_import_end + 3:
                if end > last_import_end:
                    last_import_end = end
            else:
                break

    if last_import_end == 0:
        return "", content

    import_lines = lines[:last_import_end]
    code_lines = lines[last_import_end:]
    return '\n'.join(import_lines), '\n'.join(code_lines).strip('\n')


# Globalt register over allerede ekstraherede symboler pr. source-fil.
# Nøgle: absolut sti til source-filen.
# Værdi: sæt af symbolnavne der allerede er flyttet til en target.
# Nulstilles eksplicit vha. clear_extracted_registry() ved sessionsstart.
_extracted_registry: dict[str, set[str]] = {}


def _registry_key(source: str) -> str:
    """Generer en nøgle for registret: absolut sti til source-filen.

    Tidligere brugte denne funktion et hash til at detektere source-reverts,
    men det ødelagde registret efter hver extraction (fordi filen ændrer
    sig når et symbol fjernes). Nu bruges kun absolut sti som nøgle.
    """
    return os.path.abspath(source)


def _is_nested_function(tree: ast.AST, node: ast.AST) -> bool:
    """Check if a FunctionDef/AsyncFunctionDef is nested inside another function.

    Returns True if the node is a function defined INSIDE another function
    (not a class method, not module-level). Uses AST parent-walking by
    scanning all FunctionDef/AsyncFunctionDef bodies for the node reference.
    """
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return False
    for candidate in ast.walk(tree):
        if candidate is tree or candidate is node:
            continue
        if isinstance(candidate, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for child in ast.iter_child_nodes(candidate):
                if child is node:
                    return True
    return False


def clear_extracted_registry(source: str | None = None) -> None:
    """Nulstil registret for én eller alle source-filer.

    Args:
        source: Hvis angivet, nulstilles kun for denne fil.
                Hvis None, nulstilles hele registret.
    """
    if source:
        _extracted_registry.pop(os.path.abspath(source), None)
    else:
        _extracted_registry.clear()


def _mark_extracted(source: str, symbol: str) -> None:
    """Registrér at et symbol er blevet ekstraheret fra source."""
    key = _registry_key(source)
    _extracted_registry.setdefault(key, set()).add(symbol)


def _is_already_extracted(source: str, symbol: str) -> bool:
    """Tjek om et symbol allerede er ekstraheret fra denne source."""
    key = _registry_key(source)
    return symbol in _extracted_registry.get(key, set())


def _extract_module_from_import(import_stmt: str) -> str | None:
    """Extract module name from an import statement string.

    'from flask import request' → 'flask'
    'import os' → 'os'
    Returns None if it can't parse.
    """
    try:
        tree = ast.parse(import_stmt)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                return node.module
            if isinstance(node, ast.Import) and node.names:
                return node.names[0].name
    except SyntaxError:
        pass
    return None


def _has_back_import(imp_module: str, target_module: str) -> bool:
    """Tjek om imp_module allerede importerer fra target_module.

    Brugt til at forhindre circular imports: hvis target importerer
    fra imp_module, men imp_module allerede har 'from target import X'.
    """
    if not os.path.exists(imp_module):
        # Prøv med .py tilføjelse
        imp_module_py = imp_module + '.py' if not imp_module.endswith('.py') else imp_module
        if not os.path.exists(imp_module_py):
            return False
        imp_module = imp_module_py
    target_base = os.path.splitext(os.path.basename(target_module))[0]
    try:
        with open(imp_module, 'r', encoding='utf-8') as f:
            tree = ast.parse(f.read())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                mod_name = os.path.splitext(os.path.basename(node.module))[0]
                if mod_name == target_base:
                    return True
        return False
    except (OSError, SyntaxError):
        return False


class RefactoringError(Exception):
    """Structured error for refactoring operations with rollback support.

    Attributes:
        category: Error category for logging and retry logic.
        filepath: The affected file path.
        snapshot: Optional FileSnapshot for automatic rollback.
        details: Dict with line numbers, content excerpts, etc.
    """
    # Error categories
    SYNTAX = "syntax"
    FILE_NOT_FOUND = "file_not_found"
    SYMBOL_NOT_FOUND = "symbol_not_found"
    IMPORT_FAILED = "import_failed"
    EXTRACTION_FAILED = "extraction_failed"
    REMOVAL_FAILED = "removal_failed"
    TARGET_SYNTAX = "target_syntax"
    CIRCULAR_IMPORT = "circular_import"
    SYMBOL_NESTED = "symbol_nested"
    SYMBOL_NESTED_STATEFUL = "symbol_nested_stateful"

    def __init__(self, message: str, category: str = "unknown",
                 filepath: str = "", snapshot: Any = None,
                 details: dict | None = None):
        self.category = category
        self.filepath = filepath
        self.snapshot = snapshot
        self.details = details or {}
        super().__init__(message)


class FileSnapshot:
    """Memento: stores file content for rollback."""

    def __init__(self, path: str, content: str) -> None:
        self.path = path
        self.content = content

    @classmethod
    def create(cls, path: str) -> 'FileSnapshot':
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read().replace('\r\n', '\n').replace('\r', '\n')
        return cls(path, content)

    def restore(self) -> None:
        tmppath = self.path + '.tmp'
        with open(tmppath, 'w', encoding='utf-8') as f:
            f.write(self.content)
        _atomic_replace(tmppath, self.path)


class ImportVisitor(ast.NodeVisitor):
    """Visitor: collects all name references from a symbol's AST subtree.

    Only collects names that are NOT Python builtins.
    Does NOT descend into import statements (they won't be inside a symbol).
    """

    def __init__(self) -> None:
        self.names: set[str] = set()

    def visit_Name(self, node: ast.Name) -> None:
        if node.id not in _BUILTINS:
            self.names.add(node.id)

    def visit_Import(self, node: ast.Import) -> None:
        pass

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        pass


class SymbolNode:
    """A symbol node in the dependency graph."""

    def __init__(self, name: str, sym_type: str, line: int):
        self.name = name
        self.type = sym_type
        self.line = line
        self.dependencies: set[str] = set()
        self.imported_names: set[str] = set()
        self.decorators: list[str] = []

    def to_dict(self) -> dict[str, Any]:
        return {
            'name': self.name,
            'type': self.type,
            'line': self.line,
            'dependencies': sorted(self.dependencies),
            'imported_names': sorted(self.imported_names),
            'decorators': self.decorators,
        }


class DependencyGraph:
    """Full dependency graph of all top-level symbols in a file."""

    def __init__(self) -> None:
        self.nodes: dict[str, SymbolNode] = {}
        self.external_imports: dict[str, list[str]] = defaultdict(list)

    def to_dict(self) -> dict[str, Any]:
        return {
            'symbols': {k: v.to_dict() for k, v in self.nodes.items()},
            'external_imports': dict(self.external_imports),
        }


class AstAnalyzer:
    """Strategy-based analysis: finds symbols and their dependencies."""

    @staticmethod
    def find_node(tree: ast.AST, name: str) -> ast.AST | None:
        """Find a top-level FunctionDef/AsyncFunctionDef/ClassDef/Assign by name.

        Supports 'Class.method' dotted notation for methods.
        """
        parts = name.split('.', 1)
        func_name = parts[-1]
        class_name = parts[0] if len(parts) == 2 else None

        for node in ast.walk(tree):
            if class_name:
                if isinstance(node, ast.ClassDef) and node.name == class_name:
                    for child in node.body:
                        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child.name == func_name:
                            return child
                    return None
            else:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
                    return node
                if isinstance(node, ast.ClassDef) and node.name == func_name:
                    return node
                if isinstance(node, ast.Assign):
                    for t in node.targets:
                        if isinstance(t, ast.Name) and t.id == func_name:
                            return node
                if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == func_name:
                    return node
        return None

    @staticmethod
    def get_captured_variables(tree: ast.AST, node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
        """Find names captured from enclosing scope by a nested function.

        Scans the function body for all Name references, then filters out:
        - Parameters of the function itself
        - Names defined locally inside the function (inner defs/assigns)
        - Builtins and imports
        - 'self', 'cls', 'super'

        Returns sorted list of captured variable names.
        """
        param_names: set[str] = {a.arg for a in node.args.args}
        if node.args.vararg:
            param_names.add(node.args.vararg.arg)
        if node.args.kwarg:
            param_names.add(node.args.kwarg.arg)
        for a in node.args.kwonlyargs:
            param_names.add(a.arg)
        for a in node.args.posonlyargs:
            param_names.add(a.arg)

        local_names: set[str] = set()
        # Collect nonlocal names first — AugAssign targets with nonlocal
        # are rebinding enclosing scope, not creating local vars.
        nonlocal_names: set[str] = set()
        for child in ast.walk(node):
            if isinstance(child, ast.Nonlocal):
                nonlocal_names.update(child.names)
        for child in ast.walk(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                local_names.add(child.name)
            if isinstance(child, ast.Assign):
                for t in child.targets:
                    if isinstance(t, ast.Name):
                        local_names.add(t.id)
            if isinstance(child, ast.AnnAssign) and isinstance(child.target, ast.Name):
                local_names.add(child.target.id)
            if isinstance(child, ast.NamedExpr) and isinstance(child.target, ast.Name):
                local_names.add(child.target.id)
            if isinstance(child, ast.AugAssign) and isinstance(child.target, ast.Name) and child.target.id not in nonlocal_names:
                local_names.add(child.target.id)
            if isinstance(child, (ast.For, ast.AsyncFor)):
                if isinstance(child.target, ast.Name):
                    local_names.add(child.target.id)
                elif isinstance(child.target, ast.Tuple):
                    for elt in child.target.elts:
                        if isinstance(elt, ast.Name):
                            local_names.add(elt.id)
            if isinstance(child, (ast.With, ast.AsyncWith)):
                for item in child.items:
                    if item.optional_vars is not None:
                        if isinstance(item.optional_vars, ast.Name):
                            local_names.add(item.optional_vars.id)
                        elif isinstance(item.optional_vars, ast.Tuple):
                            for elt in item.optional_vars.elts:
                                if isinstance(elt, ast.Name):
                                    local_names.add(elt.id)
            if isinstance(child, ast.ExceptHandler) and child.name is not None:
                local_names.add(child.name)
            if isinstance(child, (ast.ListComp, ast.SetComp, ast.GeneratorExp)):
                for gen in child.generators:
                    if isinstance(gen.target, ast.Name):
                        local_names.add(gen.target.id)
                    elif isinstance(gen.target, ast.Tuple):
                        for elt in gen.target.elts:
                            if isinstance(elt, ast.Name):
                                local_names.add(elt.id)
            if isinstance(child, ast.DictComp):
                for gen in child.generators:
                    if isinstance(gen.target, ast.Name):
                        local_names.add(gen.target.id)
                    elif isinstance(gen.target, ast.Tuple):
                        for elt in gen.target.elts:
                            if isinstance(elt, ast.Name):
                                local_names.add(elt.id)

        # Collect all Name loads in the function body
        refs: set[str] = set()
        for child in ast.walk(node):
            if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load):
                refs.add(child.id)

        # Collect imports visible in the source file
        import_names: set[str] = set()
        for child in ast.iter_child_nodes(tree):
            if isinstance(child, (ast.Import, ast.ImportFrom)):
                import_names |= AstAnalyzer.names_from_import_node(child)

        captured = refs - param_names - local_names - _BUILTINS - _BUILTINS_TYPING - import_names
        return sorted(captured)

    @staticmethod
    def get_symbol_lines(lines: list[str], node: ast.AST) -> tuple[int, int]:
        """Get 0-indexed start, 1-indexed end line range for a symbol.

        Includes decorators, leading comments, and trailing blank lines
        that belong to the symbol.
        """
        start = node.lineno - 1

        if hasattr(node, 'decorator_list') and node.decorator_list:
            for d in node.decorator_list:
                if hasattr(d, 'lineno') and (d.lineno - 1) < start:
                    start = d.lineno - 1

        while start > 0:
            stripped = lines[start - 1].strip()
            if stripped == '' or stripped.startswith('#'):
                start -= 1
            else:
                break

        end = getattr(node, 'end_lineno', node.lineno) or node.lineno

        return start, end

    @staticmethod
    def names_from_import_node(node: ast.AST) -> set[str]:
        """Get the set of names an import statement provides to the namespace."""
        names = set()
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.asname or alias.name.split('.')[0]
                names.add(name)
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                name = alias.asname or alias.name
                names.add(name)
        return names

    @staticmethod
    def get_symbol_type(node: ast.AST) -> str:
        if isinstance(node, ast.ClassDef):
            return 'class'
        if isinstance(node, ast.AsyncFunctionDef):
            return 'async_function'
        if isinstance(node, ast.FunctionDef):
            return 'function'
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            return 'variable'
        return 'unknown'

    @staticmethod
    def node_name(node: ast.AST) -> str | None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            return node.name
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    return t.id
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            return node.target.id
        return None

    @staticmethod
    def decorator_name(d: ast.AST) -> str | None:
        if isinstance(d, ast.Name):
            return d.id
        if isinstance(d, ast.Attribute):
            inner = AstAnalyzer.decorator_name(d.value)
            return f"{inner}.{d.attr}" if inner else d.attr
        if isinstance(d, ast.Call):
            return AstAnalyzer.decorator_name(d.func)
        return None


class ImportResolver:
    """Resolves which imports a symbol needs by matching used names."""

    @staticmethod
    def filter_for_symbol(source_content: str, source_lines: list[str],
                           used_names: set[str]) -> list[str]:
        """Filter source imports to only those providing names in used_names.

        Returns list of import statement strings (e.g. ['import os',
        'from flask import Flask']).
        """
        try:
            tree = ast.parse(source_content)
        except SyntaxError:
            return []

        needed: list[str] = []
        seen: set[str] = set()

        for node in ast.iter_child_nodes(tree):
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue

            provided = AstAnalyzer.names_from_import_node(node)
            if provided & used_names:
                start = node.lineno - 1
                end = getattr(node, 'end_lineno', node.lineno) or node.lineno
                stmt = '\n'.join(source_lines[start:end])
                if stmt not in seen:
                    seen.add(stmt)
                    needed.append(stmt)

        return needed


class CodeModifier:
    """Deterministic code modification operations."""

    @staticmethod
    def remove_lines(path: str, start: int, end: int) -> str:
        """Remove lines from a file. start/end are 0-indexed, end is exclusive.

        Returns the new content. Validates syntax after removal.
        """
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read().replace('\r\n', '\n').replace('\r', '\n')
        lines = content.split('\n')

        new_lines = lines[:start] + lines[end:]

        # Compress runs of 3+ consecutive blank lines to at most 2
        compressed = []
        blank_run = 0
        for line in new_lines:
            if line.strip() == '':
                blank_run += 1
                if blank_run <= 2:
                    compressed.append(line)
            else:
                blank_run = 0
                compressed.append(line)
        new_content = '\n'.join(compressed).strip() + '\n'

        try:
            ast.parse(new_content)
        except SyntaxError as e:
            raise RefactoringError(
                f"Syntax error after removal: {e}",
                category=RefactoringError.SYNTAX,
                filepath=path,
                details={"line": e.lineno, "msg": e.msg}
            )

        tmppath = path + '.tmp'
        with open(tmppath, 'w', encoding='utf-8') as f:
            f.write(new_content)
        _atomic_replace(tmppath, path)

        return new_content

    @staticmethod
    def insert_import(path: str, import_stmt: str) -> bool:
        """Insert an import statement into a Python file after existing imports.

        Returns True if import was added, False if it already exists.
        Merges with existing same-module imports (e.g. 'from os import path'
        becomes 'from os import path, walk' instead of adding a separate line).
        """
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read().replace('\r\n', '\n').replace('\r', '\n')

        if import_stmt in content:
            return False

        tree = ast.parse(content)
        lines = content.split('\n')

        # Try to merge with existing same-module ImportFrom
        import ast as _ast
        try:
            parsed_import = _ast.parse(import_stmt)
            merge_candidate = (parsed_import and parsed_import.body and
                               isinstance(parsed_import.body[0], _ast.ImportFrom))
        except SyntaxError:
            merge_candidate = False
        if merge_candidate:
            new_node = parsed_import.body[0]
            new_module = new_node.module
            new_name = new_node.names[0].name if new_node.names else ""

            for node in _ast.iter_child_nodes(tree):
                if isinstance(node, _ast.ImportFrom) and node.module == new_module:
                    # Same module — check if symbol already exists in this import
                    existing_names = {alias.name for alias in node.names}
                    if new_name in existing_names:
                        return False  # Already imported
                    # Merge: add symbol to existing import line
                    old_line_num = node.lineno - 1  # 0-indexed
                    old_line = lines[old_line_num]
                    # Find the last name in the existing import
                    last_name = node.names[-1].name
                    # Replace the closing paren or add to existing paren
                    if old_line.rstrip().endswith(')'):
                        # Multi-line import or parenthesized — insert before closing )
                        new_line = old_line.rstrip()
                        insert_pos = new_line.rfind(')')
                        new_line = new_line[:insert_pos] + ', ' + new_name + new_line[insert_pos:]
                        lines[old_line_num] = new_line
                    else:
                        # Single-line import: from X import a → from X import a, b
                        lines[old_line_num] = old_line.rstrip() + ', ' + new_name
                    new_content = '\n'.join(lines)
                    try:
                        _ast.parse(new_content)
                    except SyntaxError:
                        # Fall through to normal insert below
                        lines = content.split('\n')
                        break
                    # Write merged result
                    tmppath = path + '.tmp'
                    with open(tmppath, 'w', encoding='utf-8') as f:
                        f.write(new_content)
                    _atomic_replace(tmppath, path)
                    return True

        # Normal insert: insert right after the first consecutive import block
        # (standard Python convention — imports at the top of the file).
        # Using the LAST import line breaks files like api_server.py which
        # have a `from routes import ...` at line 2150.
        first_import_end = 0
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                end = getattr(node, 'end_lineno', node.lineno) or node.lineno
                if first_import_end == 0 or end < first_import_end + 3:
                    # First import or consecutive (within 2 lines)
                    if end > first_import_end:
                        first_import_end = end
                else:
                    break  # Stop at gap before scattered import

        insert_at = first_import_end if first_import_end > 0 else 0
        lines.insert(insert_at, import_stmt)
        new_content = '\n'.join(lines)

        try:
            ast.parse(new_content)
        except SyntaxError as e:
            raise RefactoringError(
                f"Syntax error after adding import: {e}",
                category=RefactoringError.IMPORT_FAILED,
                filepath=path,
                details={"line": e.lineno, "msg": e.msg}
            )

        tmppath = path + '.tmp'
        with open(tmppath, 'w', encoding='utf-8') as f:
            f.write(new_content)
        _atomic_replace(tmppath, path)

        return True


class RefactoringEngine:
    """Facade: public API for deterministic AST-based refactoring.

    Usage:
        engine = RefactoringEngine()
        engine.extract_symbol('api_server.py', 'UserHandler', 'routes.py')
        engine.remove_symbol('api_server.py', 'UserHandler')
        engine.add_import('api_server.py', 'routes', 'UserHandler')
        engine.verify_refactor('api_server.py')
    """

    def __init__(self, base_dir: str | None = None):
        self.base_dir = base_dir or os.getcwd()

    def _resolve(self, path: str) -> str:
        """Resolve a path relative to AGENT_WORKDIR if set, else CWD."""
        if not os.path.isabs(path):
            wd = os.environ.get("AGENT_WORKDIR", "")
            if wd:
                return os.path.normpath(os.path.join(wd, path))
        return os.path.abspath(path)

    def _abs(self, path: str) -> str:
        p = self._resolve(path)
        if not os.path.exists(p):
            raise RefactoringError(
                f"File not found: {p}",
                category=RefactoringError.FILE_NOT_FOUND,
                filepath=p
            )
        # Na r AGENT_WORKDIR er sat, accept e r kun stier inden for workdir
        # eller Agenten frameworket (sidstna vnte er altid tilladt).
        _wd = os.environ.get("AGENT_WORKDIR", "")
        if _wd:
            _norm_p = os.path.normcase(os.path.realpath(p))
            _norm_wd = os.path.normcase(os.path.realpath(_wd))
            _norm_agent = os.path.normcase(os.path.realpath(os.path.dirname(os.path.abspath(__file__))))
            _in_wd = _norm_p.startswith(_norm_wd + os.sep) or _norm_p == _norm_wd
            _in_agent = _norm_p.startswith(_norm_agent + os.sep) or _norm_p == _norm_agent
            if not _in_wd and not _in_agent:
                raise RefactoringError(
                    f"Adgang n -gtet: '{p}' er uden for arbejdsmappen '{_wd}'",
                    category=RefactoringError.FILE_NOT_FOUND,
                    filepath=p
                )
        return p

    def _read(self, path: str) -> tuple[str, list[str]]:
        path = self._abs(path)
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read().replace('\r\n', '\n').replace('\r', '\n')
        return content, content.split('\n')

    def _write(self, path: str, content: str) -> None:
        path = self._resolve(path)
        log.debug("_write: %s (%d bytes)", path, len(content))
        tmppath = path + '.tmp'
        with open(tmppath, 'w', encoding='utf-8') as f:
            f.write(content)
        _atomic_replace(tmppath, path)

    def extract_symbol(self, source: str, symbol_name: str, target: str) -> dict[str, Any]:
        """Strategy-based extraction: copy a symbol + its imports to a new file.

        Steps:
        1. Parse source file → AST
        2. Find the AST node for the symbol
        3. Get its full line range (including decorators/comments)
        4. Run ImportVisitor on the symbol to find used names
        5. Resolve which imports the symbol needs
        6. Construct target file: imports + symbol code
        7. Write target file atomically

        Returns dict with success, symbol, source, target, lines, etc.
        Raises RefactoringError if extraction cannot be performed.
        """
        source = self._abs(source)
        target = self._resolve(target)
        log.info("extract_symbol start: source=%s target=%s symbol=%s",
                 os.path.basename(source), os.path.basename(target), symbol_name)

        content, lines = self._read(source)

        try:
            tree = ast.parse(content)
        except SyntaxError as e:
            log.warning("extract_symbol: syntax error in source %s: %s", source, e)
            raise RefactoringError(
                f"Syntax error in source: {e}",
                category=RefactoringError.SYNTAX,
                filepath=source,
                details={"line": e.lineno, "msg": e.msg}
            )

        node = AstAnalyzer.find_node(tree, symbol_name)
        if node is None:
            log.warning("extract_symbol: symbol '%s' not found in %s", symbol_name, source)
            raise RefactoringError(
                f"Symbol '{symbol_name}' not found in {os.path.basename(source)}",
                category=RefactoringError.SYMBOL_NOT_FOUND,
                filepath=source,
                details={"symbol": symbol_name}
            )

        # Check if symbol is a nested function (closure) — requires special handling
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and _is_nested_function(tree, node):
            captured = AstAnalyzer.get_captured_variables(tree, node)
            has_nonlocal = any(
                isinstance(n, ast.Nonlocal) for n in ast.walk(node)
            )
            log.warning("extract_symbol: %s is nested (captures=%s, nonlocal=%s)",
                        symbol_name, captured, has_nonlocal)
            if has_nonlocal:
                try:
                    return self._convert_stateful_closure(source, symbol_name, target, tree, node, lines)
                except RefactoringError:
                    log.warning("extract_symbol: stateful conversion failed for %s, raising NESTED_STATEFUL",
                                symbol_name)
                    raise RefactoringError(
                        f"Symbol '{symbol_name}' er en stateful nested funktion "
                        f"(nonlocal). Forsøg på automatisk konvertering slog fejl.",
                        category=RefactoringError.SYMBOL_NESTED_STATEFUL,
                        filepath=source,
                        details={"symbol": symbol_name, "captured": captured}
                    )
            if not captured:
                # No captured variables — can extract as-is (just dedented)
                # Fall through to normal extraction below
                log.info("extract_symbol: %s is nested but has no captures — extracting as-is",
                         symbol_name)
                pass
            else:
                # Simple captures — convert to top-level with explicit params
                try:
                    return self._convert_nested_to_top_level(source, symbol_name, target, tree, node, lines)
                except RefactoringError:
                    raise
                except Exception as e:
                    raise RefactoringError(
                        f"Nested function '{symbol_name}' kunne ikke konverteres: {e}",
                        category=RefactoringError.SYMBOL_NESTED,
                        filepath=source,
                        details={"symbol": symbol_name, "captured": captured, "error": str(e)}
                    )

        start_line, end_line = AstAnalyzer.get_symbol_lines(lines, node)
        symbol_code = textwrap.dedent('\n'.join(lines[start_line:end_line]))
        symbol_type = AstAnalyzer.get_symbol_type(node)
        log.debug("extract_symbol: source_lines=%d-%d, type=%s, code=%s",
                  start_line + 1, end_line, symbol_type,
                  symbol_code.strip()[:200])

        visitor = ImportVisitor()
        visitor.visit(node)
        used_names = visitor.names

        # Find lokale symbol-afhængigheder: navne brugt af symbolet som er
        # top-level symboler i source-filen (f.eks. en klasse der refereres
        # i type hints). Disse skal også ekstraheres for at target virker.
        _source_symbols = _list_top_level_symbol_names(content)
        missing_deps = sorted(used_names & _source_symbols - {symbol_name})

        needed_imports = ImportResolver.filter_for_symbol(content, lines, used_names)

        # Tjek for circular imports: hvis nogen af de nødvendige imports
        # allerede har en `from target import ...` eller `import target`.
        _tgt_module = os.path.splitext(os.path.basename(target))[0]
        for imp in needed_imports:
            _imp_mod = _extract_module_from_import(imp)
            if _imp_mod and _has_back_import(_imp_mod, target):
                log.warning("extract_symbol: circular import detected: %s imports from %s",
                            _imp_mod, _tgt_module)
                raise RefactoringError(
                    f"Cirkulær import: '{_imp_mod}' importerer allerede fra '{_tgt_module}'. "
                    f"Symbol '{symbol_name}' kan ikke flyttes til {os.path.basename(target)} "
                    f"da det ville skabe en cirkulær afhængighed.",
                    category=RefactoringError.CIRCULAR_IMPORT,
                    filepath=source,
                    details={"symbol": symbol_name, "target": target, "circular_with": _imp_mod}
                )

        os.makedirs(os.path.dirname(target) or '.', exist_ok=True)

        if os.path.exists(target):
            with open(target, 'r', encoding='utf-8') as f:
                existing = f.read().strip()
            log.debug("extract_symbol: target %s exists (%d bytes), existing imports parsed",
                      os.path.basename(target), len(existing))
        else:
            existing = ''

        existing_imports: set[str] = set()
        if existing:
            try:
                existing_tree = ast.parse(existing)
                for node in ast.walk(existing_tree):
                    if isinstance(node, (ast.Import, ast.ImportFrom)):
                        existing_imports.add(ast.unparse(node))
            except SyntaxError:
                existing_imports = set()

        new_imports = [i for i in needed_imports if i not in existing_imports]
        # Filter out self-imports (importing from the target module itself)
        _target_module = os.path.splitext(os.path.basename(target))[0]
        new_imports = [i for i in new_imports if f"from {_target_module}" not in i]
        new_import_block = '\n'.join(new_imports)

        if not existing:
            if new_import_block and symbol_code:
                target_content = new_import_block + '\n\n' + symbol_code + '\n'
            elif symbol_code:
                target_content = symbol_code + '\n'
            else:
                target_content = ''
        else:
            # Strip existing imports from content, combine with new imports at top
            _existing_imports, _existing_code = _split_imports_from_code(existing)
            _all_imports = _existing_imports
            if new_import_block:
                if _existing_imports:
                    _all_imports = _existing_imports + '\n' + new_import_block
                else:
                    _all_imports = new_import_block
            if _all_imports and (_existing_code or symbol_code):
                target_content = _all_imports + '\n\n' + _existing_code
                if symbol_code:
                    target_content += '\n\n' + symbol_code + '\n'
            elif _existing_code and symbol_code:
                target_content = _existing_code + '\n\n' + symbol_code + '\n'
            elif symbol_code:
                target_content = symbol_code + '\n'
            elif _existing_code:
                target_content = _existing_code
            elif _all_imports:
                target_content = _all_imports
            else:
                target_content = existing

        if target_content.strip():
            try:
                ast.parse(target_content)
            except SyntaxError as e:
                log.warning("extract_symbol: target syntax error after extraction: %s", e)
                raise RefactoringError(
                    f"Target file would be syntactically invalid after extraction: {e}",
                    category=RefactoringError.TARGET_SYNTAX,
                    filepath=target,
                    details={"line": e.lineno, "msg": e.msg, "symbol": symbol_name}
                )

        # Kildeadvalidering: sammenlind ekstraheret kode med kilde
        _src_check = ast.parse(content)
        _src_node = AstAnalyzer.find_node(_src_check, symbol_name)
        if _src_node:
            _src_code = '\n'.join(lines[_src_node.lineno - 1:_src_node.end_lineno])
            if _src_code.strip() != symbol_code.strip():
                log.warning(
                    "extract_symbol: KILDEAFVIKLING for '%s' — target (%s...) "
                    "adskiller sig fra kilde (%s...)",
                    symbol_name,
                    symbol_code.strip()[:120],
                    _src_code.strip()[:120],
                )

        self._write(target, target_content)

        log.info("extract_symbol OK: %s → %s (%d imports, %d lines, preview=%.80s)",
                 symbol_name, os.path.basename(target), len(new_imports),
                 end_line - start_line, symbol_code.strip()[:80])

        return {
            'success': True,
            'symbol': symbol_name,
            'type': symbol_type,
            'source': source,
            'target': target,
            'source_lines': f"{start_line + 1}-{end_line}",
            'imports_count': len(needed_imports),
            'imports_added': len(new_imports),
            'imports_deduped': len(needed_imports) - len(new_imports),
            'used_names': sorted(used_names),
            'missing_dependencies': missing_deps,
        }

    def _convert_nested_to_top_level(
        self,
        source: str,
        symbol_name: str,
        target: str,
        tree: ast.AST,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        lines: list[str],
    ) -> dict[str, Any]:
        """Convert a nested function to a top-level function in target.

        Handles:
        - Simple captures (variables from enclosing scope) → adds as parameters
        - Direct call sites → appends captured args
        - Callback pass sites → wraps with functools.partial

        Does NOT handle stateful closures (with nonlocal).
        """
        target = self._resolve(target)
        captured = AstAnalyzer.get_captured_variables(tree, node)
        start_line, end_line = AstAnalyzer.get_symbol_lines(lines, node)
        symbol_lines = list(lines[start_line:end_line])
        log.info("_convert_nested_to_top_level: %s captures=%s", symbol_name, captured)

        # 1. Build new signature with captured vars at the end
        def_line_idx = node.lineno - 1 - start_line  # offset from start to def line
        old_sig_line = symbol_lines[def_line_idx] if 0 <= def_line_idx < len(symbol_lines) else lines[node.lineno - 1]
        if captured:
            new_params_str = ', '.join(captured)
            if '(' in old_sig_line and ')' in old_sig_line:
                paren_idx = old_sig_line.rindex(')')
                before = old_sig_line[:paren_idx]
                after = old_sig_line[paren_idx:]
                if before.rstrip().endswith('('):
                    # def func( → def func(captured1, captured2)
                    new_sig_line = before + new_params_str + after
                else:
                    # def func(a, b) → def func(a, b, captured1, captured2)
                    new_sig_line = before + ', ' + new_params_str + after
            else:
                new_sig_line = old_sig_line
            symbol_lines[def_line_idx] = new_sig_line
        # else: no captured → keep original signature

        # 2. Get symbol code with modified signature
        symbol_code = textwrap.dedent('\n'.join(symbol_lines))

        # 3. Resolve and write target
        visitor = ImportVisitor()
        visitor.visit(node)
        used_names = visitor.names | set(captured)
        content = '\n'.join(lines)
        needed_imports = ImportResolver.filter_for_symbol(content, lines, used_names)
        os.makedirs(os.path.dirname(target) or '.', exist_ok=True)
        existing_target = ''
        if os.path.exists(target):
            existing_target = open(target, 'r', encoding='utf-8').read().strip()
        existing_imports: set[str] = set()
        if existing_target:
            try:
                for imp_node in ast.walk(ast.parse(existing_target)):
                    if isinstance(imp_node, (ast.Import, ast.ImportFrom)):
                        seg = ast.get_source_segment(existing_target, imp_node)
                        if seg:
                            existing_imports.add(seg.strip())
            except SyntaxError:
                pass
        new_imports = [i for i in needed_imports if i not in existing_imports]
        target_imports_block = '\n'.join(new_imports)
        target_parts = [p for p in [target_imports_block, symbol_code] if p.strip()]
        target_content = '\n\n'.join(target_parts) + '\n'
        try:
            ast.parse(target_content)
        except SyntaxError as e:
            raise RefactoringError(
                f"Target syntax error after nested conversion: {e}",
                category=RefactoringError.TARGET_SYNTAX,
                filepath=target,
                details={"symbol": symbol_name, "captured": captured, "error": str(e)}
            )
        self._write(target, target_content)
        log.info("_convert_nested_to_top_level: wrote %s to %s (%d bytes)",
                 symbol_name, os.path.basename(target), len(target_content))

        # 4. Update source in-memory
        source_path = self._abs(source)
        source_content = '\n'.join(lines)
        source_tree = ast.parse(source_content)

        # Find references to the symbol outside its own definition
        # Collect all (line, col_offset) of Name nodes with this id
        source_lines = list(lines)
        ref_locations: set[int] = set()  # line numbers (0-based)
        for ref_node in ast.walk(source_tree):
            if not isinstance(ref_node, ast.Name) or ref_node.id != symbol_name:
                continue
            ref_lineno = ref_node.lineno - 1
            if start_line <= ref_lineno < end_line:
                continue
            # Determine if direct call or callback pass
            # Check if parent node is a Call where this Name is the function
            is_call = False
            for p_node in ast.walk(source_tree):
                if isinstance(p_node, ast.Call):
                    if isinstance(p_node.func, ast.Name) and p_node.func.id == symbol_name:
                        if p_node.func.lineno - 1 == ref_lineno:
                            is_call = True
                            break
            ref_locations.add(ref_lineno)

            if captured:
                if is_call:
                    # Direct call: append captured args to existing call
                    call_line = source_lines[ref_lineno]
                    open_paren = call_line.find('(')
                    close_paren = call_line.rfind(')')
                    if close_paren >= 0 and open_paren >= 0:
                        args_section = call_line[open_paren + 1:close_paren]
                        has_kwargs = '=' in args_section
                        if has_kwargs:
                            append = ', ' + ', '.join(f'{v}={v}' for v in captured)
                        else:
                            append = ', ' + ', '.join(captured)
                        source_lines[ref_lineno] = call_line[:close_paren] + append + call_line[close_paren:]
                    else:
                        # Multi-line call — append to call via wrapping as partial
                        source_lines[ref_lineno] = source_lines[ref_lineno].replace(
                            f'{symbol_name}(', f'functools.partial({symbol_name}, ', 1
                        )
                        # Fix: we changed the semantics — partial with all args
                else:
                    # Callback pass: wrap with partial
                    captured_kwargs = ', '.join(f'{v}={v}' for v in captured)
                    old_ref = symbol_name
                    new_ref = f"functools.partial({symbol_name}, {captured_kwargs})"
                    source_lines[ref_lineno] = source_lines[ref_lineno].replace(old_ref, new_ref, 1)

        # Handle non-call references (like `return _merge_session`)
        # These are the same as "callback pass" — already handled above by the else branch

        # Write updated source (with updated references but still containing the function)
        source_path = self._abs(source)
        updated_source = '\n'.join(source_lines)
        with open(source_path + '.tmp', 'w', encoding='utf-8') as f:
            f.write(updated_source)
        _atomic_replace(source_path + '.tmp', source_path)

        # 5. Remove the original nested function lines
        try:
            new_source = CodeModifier.remove_lines(source, start_line, end_line)
        except RefactoringError as e:
            log.warning("_convert_nested_to_top_level: remove_lines FAILED: %s", e)
            raise RefactoringError(
                f"Kunne ikke fjerne nested function '{symbol_name}' efter konvertering: {e}",
                category=RefactoringError.REMOVAL_FAILED,
                filepath=source,
                details={"symbol": symbol_name, "captured": captured}
            )

        # 6. Add import in source
        target_module = os.path.splitext(os.path.basename(target))[0]
        try:
            CodeModifier.insert_import(source, f"from {target_module} import {symbol_name}")
        except RefactoringError as e:
            log.warning("_convert_nested_to_top_level: insert_import FAILED: %s", e)
            raise

        # 7. Add functools import if partial wrappers were used
        used_partial = any('functools.partial' in l for l in source_lines)
        if used_partial:
            try:
                CodeModifier.insert_import(source, "import functools")
            except RefactoringError:
                pass

        log.info("_convert_nested_to_top_level OK: %s → %s (captured=%s, refs=%d)",
                 symbol_name, os.path.basename(target), captured, len(ref_locations))
        return {
            "success": True,
            "symbol": symbol_name,
            "source": source,
            "target": target,
            "converted": True,
            "captured_vars": captured,
            "references_updated": len(ref_locations),
        }

    def _convert_stateful_closure(
        self,
        source: str,
        symbol_name: str,
        target: str,
        tree: ast.AST,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        lines: list[str],
    ) -> dict[str, Any]:
        """Convert a stateful nested closure to a class-based wrapper in target.

        Handles closures with `nonlocal` declarations by creating a class
        with `__call__` in the target module.

        For example:
            def outer():
                completed = 0
                def inner(node):
                    nonlocal completed
                    completed += 1
                    ...
            →
            class InnerWrapper:
                def __init__(self):
                    self._completed = 0
                def __call__(self, node):
                    self._completed += 1
                    ...
        """
        target = self._resolve(target)
        captured = AstAnalyzer.get_captured_variables(tree, node)
        # Find nonlocal variables
        nonlocal_vars: set[str] = set()
        for child in ast.walk(node):
            if isinstance(child, ast.Nonlocal):
                nonlocal_vars.update(child.names)
        # Captured variables minus nonlocal = just-read captures
        read_captures = [v for v in captured if v not in nonlocal_vars]
        log.info("_convert_stateful_closure: %s nonlocal=%s read_captures=%s",
                 symbol_name, sorted(nonlocal_vars), read_captures)

        start_line, end_line = AstAnalyzer.get_symbol_lines(lines, node)
        symbol_lines = list(lines[start_line:end_line])
        def_line_idx = node.lineno - 1 - start_line

        # Build the class wrapper
        class_name = symbol_name[0].upper() + symbol_name[1:] + "Wrapper" if symbol_name[0].islower() else symbol_name + "Wrapper"

        # Build __init__
        init_kwargs = ', '.join(read_captures)
        class_code = f"class {class_name}:\n"
        if read_captures or nonlocal_vars:
            class_code += f"    def __init__(self{', ' + init_kwargs if init_kwargs else ''}):\n"
            for v in read_captures:
                class_code += f"        self.{v} = {v}\n"
            for v in sorted(nonlocal_vars):
                class_code += f"        self._{v} = 0\n"
        else:
            class_code += "    def __init__(self):\n"
            class_code += "        pass\n"

        # Build __call__ from the original code
        dedent_level = len(symbol_lines[def_line_idx]) - len(symbol_lines[def_line_idx].lstrip())
        call_body_lines = []
        for idx, orig_line in enumerate(symbol_lines):
            if idx == def_line_idx:
                continue  # skip the def line
            stripped = orig_line.strip()
            if not stripped:
                call_body_lines.append('')
                continue
            if stripped.startswith('nonlocal '):
                call_body_lines.append("        # converted nonlocal")
                continue
            # Determine indentation: body is at 8 spaces (4 class + 4 method)
            # plus any extra nesting beyond the direct function body
            orig_indent = len(orig_line) - len(orig_line.lstrip())
            extra_nesting = orig_indent - dedent_level - 4
            if extra_nesting < 0:
                extra_nesting = 0
            body_indent = '        ' + '    ' * (extra_nesting // 4)
            modified = orig_line.strip()
            # Fix C: recursive call → self() in __call__
            modified = modified.replace(f"{symbol_name}(", "self(")
            # Fix B: nonlocal vars → self._
            for nv in sorted(nonlocal_vars, key=len, reverse=True):
                if nv in modified and not modified.startswith('#'):
                    modified = modified.replace(nv, f"self._{nv}")
            # Fix B: read_captures → self.{v}
            for rc in sorted(read_captures, key=len, reverse=True):
                if rc in modified and not modified.startswith('#'):
                    modified = modified.replace(rc, f"self.{rc}")
            call_body_lines.append(f"{body_indent}{modified}")

        # Find original params for __call__
        orig_params_list = [a.arg for a in node.args.args]
        orig_params = ', '.join(orig_params_list)
        call_sig = f"def __call__(self{', ' + orig_params if orig_params else ''}):"

        class_code += f"    {call_sig}\n"
        class_code += '\n'.join(call_body_lines) + '\n'

        # Write target
        os.makedirs(os.path.dirname(target) or '.', exist_ok=True)
        visitor = ImportVisitor()
        visitor.visit(node)
        used_names = visitor.names | set(captured)
        content = '\n'.join(lines)
        needed_imports = ImportResolver.filter_for_symbol(content, lines, used_names)
        existing_target = ''
        if os.path.exists(target):
            existing_target = open(target, 'r', encoding='utf-8').read().strip()
        existing_imports: set[str] = set()
        if existing_target:
            try:
                for imp_node in ast.walk(ast.parse(existing_target)):
                    if isinstance(imp_node, (ast.Import, ast.ImportFrom)):
                        seg = ast.get_source_segment(existing_target, imp_node)
                        if seg:
                            existing_imports.add(seg.strip())
            except SyntaxError:
                pass
        new_imports = [i for i in needed_imports if i not in existing_imports]
        imports_block = '\n'.join(new_imports)
        target_parts = [p for p in [imports_block, class_code] if p.strip()]
        target_content = '\n\n'.join(target_parts) + '\n'
        try:
            ast.parse(target_content)
        except SyntaxError as e:
            raise RefactoringError(
                f"Target syntax error after stateful conversion: {e}",
                category=RefactoringError.TARGET_SYNTAX,
                filepath=target,
                details={"symbol": symbol_name, "error": str(e)}
            )
        self._write(target, target_content)

        # Remove the original nested function and update the call site
        source_path = self._abs(source)
        source_lines = list(lines)

        # Find reference to the function in parent scope and replace with instance creation
        source_tree = ast.parse('\n'.join(source_lines))
        ref_line_nos: set[int] = set()
        for ref_node in ast.walk(source_tree):
            if isinstance(ref_node, ast.Name) and ref_node.id == symbol_name:
                ref_lineno = ref_node.lineno - 1
                if not (start_line <= ref_lineno < end_line):
                    ref_line_nos.add(ref_lineno)

        init_kwargs = ', '.join(f'{v}={v}' for v in read_captures)
        init_call = f"{class_name}({init_kwargs})" if init_kwargs else f"{class_name}()"
        for ref_line in ref_line_nos:
            source_lines[ref_line] = source_lines[ref_line].replace(symbol_name, init_call, 1)

        # Write updated source
        updated_source = '\n'.join(source_lines)
        with open(source_path + '.tmp', 'w', encoding='utf-8') as f:
            f.write(updated_source)
        _atomic_replace(source_path + '.tmp', source_path)

        # Remove original function
        try:
            CodeModifier.remove_lines(source, start_line, end_line)
        except RefactoringError as e:
            raise RefactoringError(
                f"Kunne ikke fjerne stateful closure '{symbol_name}': {e}",
                category=RefactoringError.REMOVAL_FAILED,
                filepath=source,
                details={"symbol": symbol_name}
            )

        # Add import
        target_module = os.path.splitext(os.path.basename(target))[0]
        try:
            CodeModifier.insert_import(source, f"from {target_module} import {class_name}")
        except RefactoringError:
            pass

        log.info("_convert_stateful_closure OK: %s → %s.%s (nonlocal=%s, refs=%d)",
                 symbol_name, os.path.basename(target), class_name,
                 sorted(nonlocal_vars), len(ref_line_nos))
        return {
            "success": True,
            "symbol": symbol_name,
            "source": source,
            "target": target,
            "converted": True,
            "class_name": class_name,
            "nonlocal_vars": sorted(nonlocal_vars),
            "references_updated": len(ref_line_nos),
        }

    def remove_symbol(self, source: str, symbol_name: str) -> dict[str, Any]:
        """Remove a symbol from source file deterministically via AST.

        Returns dict with success, symbol, source, lines_removed, etc.
        Raises RefactoringError if removal cannot be performed.
        """
        source = self._abs(source)

        content, lines = self._read(source)

        try:
            tree = ast.parse(content)
        except SyntaxError as e:
            log.warning("remove_symbol: syntax error in %s: %s", source, e)
            raise RefactoringError(
                f"Syntax error in source: {e}",
                category=RefactoringError.SYNTAX,
                filepath=source,
                details={"line": e.lineno, "msg": e.msg}
            )

        node = AstAnalyzer.find_node(tree, symbol_name)
        if node is None:
            # Symbol not found — likely already extracted in a prior phase.
            # Return soft success instead of raising, so the LLM can continue.
            log.debug("remove_symbol: %s not found in %s (already removed?)",
                      symbol_name, os.path.basename(source))
            return {
                'success': True,
                'symbol': symbol_name,
                'source': source,
                'already_removed': True,
                'note': f"Symbol '{symbol_name}' findes ikke i {os.path.basename(source)} — allerede fjernet?"
            }

        start_line, end_line = AstAnalyzer.get_symbol_lines(lines, node)
        snapshot = FileSnapshot.create(source)

        try:
            CodeModifier.remove_lines(source, start_line, end_line)
        except RefactoringError as e:
            log.warning("remove_symbol: remove_lines FAILED for %s: %s — restoring snapshot",
                        symbol_name, e)
            snapshot.restore()
            raise

        remaining = self._list_symbols(source)
        log.info("remove_symbol OK: %s removed from %s (lines %d-%d, %d symbols remain)",
                 symbol_name, os.path.basename(source), start_line + 1, end_line,
                 len(remaining))

        return {
            'success': True,
            'symbol': symbol_name,
            'source': source,
            'lines_removed': f"{start_line + 1}-{end_line}",
            'remaining_symbols': remaining,
        }

    def add_import(self, source: str, module: str, symbol: str) -> dict[str, Any]:
        """Add 'from module import symbol' to source if not already present.

        Returns dict with success, import_added, import_line.
        Raises RefactoringError if import cannot be added.
        """
        source = self._abs(source)
        import_stmt = f"from {module} import {symbol}"

        try:
            added = CodeModifier.insert_import(source, import_stmt)
        except RefactoringError as e:
            log.warning("add_import: FAILED %s in %s: %s", import_stmt, source, e)
            raise

        log.debug("add_import: %s in %s (added=%s)", import_stmt, os.path.basename(source), added)
        return {
            'success': True,
            'import_added': added,
            'source': source,
            'import_line': import_stmt,
        }

    def verify_refactor(self, source: str, source_for_deps: str | None = None) -> dict[str, Any]:
        """Verify a source file is syntactically valid Python.

        When ``source_for_deps`` is provided (the original source file from
        which symbols were extracted), also checks the target file for
        unresolved name references that are only defined in the source.
        This catches ``NameError`` scenarios (e.g. a function using a class
        that was left behind in the source file).

        Returns dict with success, lines, symbols.
        """
        source = self._abs(source)

        try:
            content, lines = self._read(source)
            ast.parse(content)
        except SyntaxError as e:
            log.warning("verify_refactor: SYNTAX ERROR in %s: %s", source, e)
            return {'success': False, 'error': f"Syntax error: {e}"}
        except Exception as e:
            log.warning("verify_refactor: FAILED to read/parse %s: %s", source, e)
            return {'success': False, 'error': f"Failed to read/parse: {e}"}

        symbols = self._list_symbols(source)

        result: dict[str, Any] = {
            'success': True,
            'source': source,
            'lines': len(lines),
            'symbols': symbols,
        }

        # Cross-file dependency check
        if source_for_deps:
            try:
                source_content, _ = self._read(source_for_deps)
                local_deps = _find_unresolved_local_deps(source_content, content)
                if local_deps:
                    log.warning("verify_refactor: unresolved deps in %s from %s: %s",
                                os.path.basename(source), os.path.basename(source_for_deps),
                                ', '.join(local_deps))
                    result['warning'] = (
                        f"Filen refererer symboler der kun er defineret i "
                        f"{os.path.basename(source_for_deps)}: "
                        f"{', '.join(local_deps)}. "
                        f"Ekstraher disse symboler eller tilføj imports for at "
                        f"undgå NameError ved import."
                    )
                    result['missing_dependencies'] = local_deps
                    result['source_for_deps'] = source_for_deps
            except (OSError, RefactoringError, SyntaxError):
                pass

        log.debug("verify_refactor: %s OK (%d lines, %d symbols)",
                  os.path.basename(source), len(lines), len(symbols))
        return result

    @staticmethod
    def _verify_importable(target: str) -> dict[str, Any]:
        """Verify that target module can be imported without circular errors.

        Tries to import the target module using importlib. If the module
        has unresolved circular imports (e.g. it imports from the source
        file while the source also imports from it), this will raise
        ImportError.

        Returns {"success": True} or {"success": False, "error": "..."}.
        The module is removed from sys.modules afterward to avoid side
        effects.
        """
        import importlib
        import sys as _sys

        target_path = target
        if not os.path.isabs(target_path):
            wd = os.environ.get("AGENT_WORKDIR", "")
            target_path = os.path.normpath(os.path.join(wd, target_path)) if wd else os.path.abspath(target_path)

        if not os.path.exists(target_path):
            return {"success": False, "error": f"Target file not found: {target_path}"}

        module_name = os.path.splitext(os.path.basename(target_path))[0]
        target_dir = os.path.dirname(target_path)
        added = False

        if target_dir not in _sys.path:
            _sys.path.insert(0, target_dir)
            added = True

        try:
            importlib.import_module(module_name)
            return {"success": True}
        except ImportError as e:
            return {"success": False, "error": str(e)}
        except Exception as e:
            return {"success": False, "error": f"{type(e).__name__}: {e}"}
        finally:
            if module_name in _sys.modules:
                del _sys.modules[module_name]
            if added and target_dir in _sys.path:
                _sys.path.remove(target_dir)

    def batch_extract_symbols(self, source: str, symbols: list[str] | str, target: str) -> dict[str, Any]:
        """Extract multiple symbols to a target module in a single call.

        Each symbol is moved via move_symbol (extract + remove + add_import).
        Results include per-symbol outcomes so the LLM can see which succeeded
        and which failed without wasting iterations.

        Accepts ``symbols`` as a list of strings (preferred), a
        comma-separated string, or a JSON array string (common LLM
        hallucination: ``'["sym1", "sym2"]'``).
        """
        symbols = _parse_symbols_list(symbols)
        log.info("batch_extract_symbols start: %d symbols %s → %s: %s",
                 len(symbols), os.path.basename(source), os.path.basename(target),
                 ', '.join(symbols))
        results = []

        # Pre-extraction check: import cycle risk analysis
        import_cycle_risks: dict[str, list[str]] = {}
        try:
            source_content_cycle, source_lines_cycle = self._read(source)
            source_tree_cycle = ast.parse(source_content_cycle)
            for sym in symbols:
                node = AstAnalyzer.find_node(source_tree_cycle, sym)
                if node is not None:
                    _sl, _el = AstAnalyzer.get_symbol_lines(source_lines_cycle, node)
                    sym_code = '\n'.join(source_lines_cycle[_sl:_el])
                    risks = _detect_import_cycle_risk(
                        source_content_cycle, source, target, sym_code
                    )
                    if risks:
                        import_cycle_risks[sym] = risks
        except Exception as e:
            log.debug("import cycle risk check error: %s", e)

        if import_cycle_risks:
            log.warning("batch_extract_symbols: import cycle risk detected: %s",
                        import_cycle_risks)

        for i, sym in enumerate(symbols):
            if i > 0:
                import time
                time.sleep(0.1)
            # Tjek om symbolet allerede er ekstraheret i en tidligere session
            if _is_already_extracted(source, sym):
                log.debug("batch_extract_symbols: %s already extracted (skipped)", sym)
                results.append({
                    "success": True,
                    "symbol": sym,
                    "already_extracted": True,
                    "error": "",
                })
                continue
            try:
                r = self.move_symbol(source, sym, target)
                if r.get("success"):
                    _mark_extracted(source, sym)
                results.append(r)
            except RefactoringError as e:
                log.warning("batch_extract_symbols: %s FAILED: %s (category=%s)",
                            sym, e, e.category)
                results.append({
                    "success": False,
                    "symbol": sym,
                    "error": str(e),
                    "category": e.category,
                })
        # Post-check: find navne i target der kun er defineret i source
        # (f.eks. en klasse der bruges i type hints men ikke blev ekstraheret)
        missing_deps: list[str] = []
        try:
            with open(self._resolve(target), 'r', encoding='utf-8') as _tf:
                _target_content = _tf.read().replace('\r\n', '\n').replace('\r', '\n')
            _source_content, _ = self._read(source)
            missing_deps = _find_unresolved_local_deps(_source_content, _target_content)
        except (OSError, RefactoringError):
            pass

        _succeeded = sum(1 for r in results if r.get("success"))
        _failed = sum(1 for r in results if not r.get("success"))
        _skipped = sum(1 for r in results if r.get("already_extracted"))

        # Auto-add known framework imports (app, BASE_DIR, request, etc.)
        auto_added: list[str] = []
        if _succeeded > 0:
            auto_added = _auto_add_known_imports(target)

        if missing_deps:
            log.warning("batch_extract_symbols: missing deps in target: %s",
                        ', '.join(missing_deps))

        log.info("batch_extract_symbols done: %s → %s (%d/%d succeeded, %d failed, %d skipped, deps=%d, auto_imports=%d)",
                 os.path.basename(source), os.path.basename(target),
                 _succeeded, len(symbols), _failed, _skipped, len(missing_deps), len(auto_added))
        for r in results:
            status = "OK" if r.get("success") else "FAIL"
            extra = " (already_extracted)" if r.get("already_extracted") else ""
            err = f" — {r['error']}" if r.get("error") else ""
            log.debug("  %s %s%s%s", status, r["symbol"], extra, err)

        # Post-extraction check: is target actually importable?
        import_check: dict[str, Any] = {"success": True}
        if _succeeded > 0:
            try:
                import_check = self._verify_importable(target)
                if not import_check["success"]:
                    log.warning("batch_extract_symbols: target NOT importable: %s",
                                import_check.get("error", ""))
            except Exception as e:
                log.warning("batch_extract_symbols: import check crashed: %s", e)
                import_check = {"success": False, "error": str(e)}

        result = {
            "success": True,
            "source": source,
            "target": target,
            "total": len(symbols),
            "succeeded": _succeeded,
            "failed": _failed,
            "results": results,
            "nested_converted": sum(1 for r in results if r.get("nested_function")),
            "stateful_converted": sum(1 for r in results if r.get("stateful_closure")),
            "missing_dependencies": missing_deps,
            "import_cycle_risks": import_cycle_risks,
            "import_check": import_check,
            "auto_added_imports": auto_added,
        }

        log.info("batch_extract_symbols done: succeeded=%d failed=%d import_ok=%s",
                 _succeeded, _failed, import_check.get("success", "?"))
        return result

    def run_extraction_plan(
        self,
        source: str,
        plan_path: str = "refactor_plan.md",
    ) -> dict[str, Any]:
        """Read ``refactor_plan.md`` and extract ALL planned symbols deterministically.

        This is the core "one button" extraction tool — no LLM orchestration
        needed. It parses the plan, runs ``batch_extract_symbols`` for each
        module, and reports comprehensive results.

        Args:
            source: Path to the source file being refactored (e.g. ``api_server.py``).
            plan_path: Path to the plan file (default ``refactor_plan.md``).

        Returns:
            Dict with keys:
            - success (bool): True if ALL modules were fully extracted
            - total_modules (int): Number of modules in the plan
            - succeeded (int): Modules fully extracted
            - failed (int): Modules with errors
            - skipped (int): Modules that already existed with all symbols
            - progress (list[dict]): Per-module results
            - summary (str): Human-readable summary
        """
        # Resolve paths
        base = os.environ.get("AGENT_WORKDIR", "")
        if base:
            plan_path = os.path.join(base, plan_path) if not os.path.isabs(plan_path) else plan_path
            source = os.path.join(base, source) if not os.path.isabs(source) else source

        if not os.path.exists(plan_path):
            return {
                "success": False,
                "error": f"Plan file not found: {plan_path}",
                "total_modules": 0,
                "succeeded": 0,
                "failed": 0,
                "progress": [],
                "summary": f"❌ Planfil {plan_path} findes ikke",
            }

        try:
            with open(plan_path, encoding="utf-8") as f:
                plan_content = f.read()
        except (OSError, UnicodeDecodeError) as e:
            return {
                "success": False,
                "error": f"Cannot read plan: {e}",
                "total_modules": 0,
                "succeeded": 0,
                "failed": 0,
                "progress": [],
                "summary": f"❌ Kan ikke læse plan: {e}",
            }

        # Parse modules from plan — supports multiple formats:
        #   ## Module: config.py
        #   ## Module: `config.py`        (backtick-wrapped names)
        #   ## Modul 1: config.py          (Danish with optional number)
        #   ### 1. config.py               (numbered heading)
        # Symbols can be listed as:
        #   **Symboler (3):** sym1, sym2, sym3
        #   **Symbols to move**: followed by bullet items:
        #     - `sym1`
        #     - sym2
        module_pattern = re.compile(
            r"^#{1,6}\s+(?:\d+[\.\)]\s*|(?:[Mm]odul[er]*\s*\d*:?\s*)?)([\w./`-]+\.py)\b",
            re.MULTILINE,
        )
        symbols_pattern = re.compile(
            r"\*\*Symbol(?:er|s)?\s*(?:to move)?\s*(?:\(\d+\))?:\*\*\s*(.+)",
        )

        modules: list[dict[str, Any]] = []
        for mod_match in module_pattern.finditer(plan_content):
            mod_name = mod_match.group(1).strip().strip('`')
            # Find the Symboler line within the module section
            start = mod_match.end()
            # Find end of this module section (next ## heading or end of file)
            next_heading = re.search(r"^#{1,6}\s+", plan_content[start:], re.MULTILINE)
            end = start + next_heading.start() if next_heading else len(plan_content)
            section = plan_content[start:end]
            sym_match = symbols_pattern.search(section)
            symbols: list[str] = []
            if sym_match:
                raw = sym_match.group(1).strip()
                symbols = [s.strip().strip('`').strip() for s in raw.split(",") if s.strip().strip('`').strip()]
            if not symbols:
                # Fallback: extract symbols from bullet/indented lines
                bullet_pat = re.compile(r'^\s*[-*]\s+`?([a-zA-Z_]\w*)`?\s*$', re.MULTILINE)
                bullet_matches = bullet_pat.findall(section)
                symbols = [s for s in bullet_matches if s]
            modules.append({"name": mod_name, "symbols": symbols})

        if not modules:
            return {
                "success": False,
                "error": f"No modules found in {plan_path}",
                "total_modules": 0,
                "succeeded": 0,
                "failed": 0,
                "progress": [],
                "summary": f"❌ Ingen moduler fundet i {plan_path}",
            }

        from file_checks import _parse_refactor_plan_modules
        all_planned_modules = set(_parse_refactor_plan_modules(plan_path))

        progress: list[dict[str, Any]] = []
        succeeded_count = 0
        failed_count = 0
        skipped_count = 0

        for mod in modules:
            target = mod["name"]
            symbols = mod["symbols"]
            target_path = os.path.join(
                os.path.dirname(plan_path) if not os.path.isabs(target) else "",
                target
            )
            if not os.path.isabs(target_path):
                target_path = os.path.join(os.getcwd(), target)

            mod_result: dict[str, Any] = {
                "module": target,
                "symbols": symbols,
                "status": "pending",
            }

            # Check if target already exists with all symbols
            if os.path.exists(target_path):
                existing_symbols = self._list_symbols(target_path)
                planned_set = set(symbols)
                missing = planned_set - set(existing_symbols)
                if not missing and planned_set:
                    mod_result["status"] = "skipped"
                    mod_result["message"] = f"All {len(symbols)} symbols already in {target}"
                    skipped_count += 1
                    progress.append(mod_result)
                    continue

            if not symbols:
                # Module is just a container (e.g. __init__.py) — skip
                mod_result["status"] = "skipped"
                mod_result["message"] = "No symbols to extract"
                skipped_count += 1
                progress.append(mod_result)
                continue

            # Run batch extraction
            try:
                result = self.batch_extract_symbols(
                    source=source,
                    symbols=symbols,
                    target=target,
                )
                result_ok = result.get("success", False) and result.get("succeeded", 0) > 0
                if result_ok:
                    mod_result["status"] = "succeeded"
                    mod_result["succeeded"] = result.get("succeeded", 0)
                    mod_result["failed"] = result.get("failed", 0)
                    mod_result["auto_added_imports"] = result.get("auto_added_imports", [])
                    mod_result["import_check"] = result.get("import_check", {})
                    succeeded_count += 1
                else:
                    mod_result["status"] = "failed"
                    mod_result["error"] = result.get("results", [{}])[0].get("error", "Unknown error") if result.get("results") else "No results"
                    failed_count += 1
            except Exception as e:
                mod_result["status"] = "failed"
                mod_result["error"] = f"{type(e).__name__}: {e}"
                failed_count += 1

            progress.append(mod_result)

        total = len(modules)
        summary_parts = [
            f"📊 Plan: {total} moduler, {succeeded_count} succes, {failed_count} fejl, {skipped_count} sprunget over"
        ]
        for m in progress:
            if m["status"] == "succeeded":
                sym_count = m.get("succeeded", 0)
                imports = m.get("auto_added_imports", [])
                imp_str = f" (+{len(imports)} imports)" if imports else ""
                summary_parts.append(f"  ✅ {m['module']}: {sym_count}/{len(m['symbols'])} symboler{imp_str}")
            elif m["status"] == "failed":
                summary_parts.append(f"  ❌ {m['module']}: {m.get('error', '?')}")
            else:
                summary_parts.append(f"  ⏭️  {m['module']}: {m.get('message', '?')}")

        all_ok = failed_count == 0
        if all_ok:
            summary_parts.append(f"\n✅ ALLE {total} moduler behandlet — ekstrahering fuldført!")
        elif succeeded_count > 0:
            summary_parts.append(f"\n⚠️ {failed_count} modul(er) fejlede — se detaljer ovenfor")
        else:
            summary_parts.append(f"\n❌ Alle {total} moduler fejlede!")

        return {
            "success": all_ok,
            "total_modules": total,
            "succeeded": succeeded_count,
            "failed": failed_count,
            "skipped": skipped_count,
            "progress": progress,
            "summary": "\n".join(summary_parts),
        }

    @staticmethod
    def _symbol_exists_in_target(target: str, symbol_name: str) -> bool:
        """Tjek om et symbol allerede findes i target-filen via AST.

        Forhindrer duplikatextractioner na r batch_extract_symbols kaldes
        flere gange for samme symbol (f.eks. efter session-reset).
        """
        if not os.path.exists(target):
            return False
        try:
            with open(target, 'r', encoding='utf-8') as f:
                content = f.read()
            tree = ast.parse(content)
            return AstAnalyzer.find_node(tree, symbol_name) is not None
        except (SyntaxError, OSError):
            return False

    def move_symbol(self, source: str, symbol_name: str, target: str) -> dict[str, Any]:
        """Full move: extract + remove + add_import.

        Template Method pattern: defines the skeleton of a move operation.
        Steps:
        0. Check if symbol already exists in target (idempotent guard)
        1. Extract symbol to target (imports + code) — skip if already exists
        2. Remove symbol from source
        3. Add import in source pointing to target module
        4. Verify source syntax

        If any step fails, all prior steps are rolled back.
        """
        source = self._abs(source)
        target = self._resolve(target)
        short_name = symbol_name.split('.')[-1]
        target_module = os.path.splitext(os.path.basename(target))[0]

        source_snapshot = FileSnapshot.create(source)
        target_exists = os.path.exists(target)
        target_snapshot = FileSnapshot.create(target) if target_exists else None

        def _rollback(error_step: str) -> dict[str, Any]:
            log.warning("move_symbol: ROLLBACK after %s failure for %s", error_step, symbol_name)
            source_snapshot.restore()
            if target_snapshot:
                target_snapshot.restore()
            else:
                try:
                    os.remove(target)
                except OSError:
                    pass

        _t0 = _time.monotonic()

        # Step 0: Idempotent guard — check if symbol already exists in target
        symbol_already_in_target = self._symbol_exists_in_target(target, symbol_name)

        if symbol_already_in_target:
            log.debug("move_symbol: %s already exists in %s — skipping extract",
                      symbol_name, os.path.basename(target))
            extract_result = {
                "success": True,
                "symbol": symbol_name,
                "already_exists": True,
                "note": f"Symbol '{symbol_name}' fandtes allerede i target — extract sprunget over",
            }
            _t_extract = _time.monotonic() - _t0
        else:
            # Step 1: Extract
            try:
                extract_result = self.extract_symbol(source, symbol_name, target)
                _t_extract = _time.monotonic() - _t0
            except RefactoringError as e:
                log.warning("move_symbol: extract FAILED for %s: %s", symbol_name, e)
                return {
                    'success': False,
                    'symbol': symbol_name,
                    'error': f"Extract failed: {e}",
                    'step': 'extract',
                    'category': e.category,
                }

        # Step 2: Remove
        # For nested function conversions, the conversion already removed
        # the original function and added the import — skip steps 2-3.
        if extract_result.get("converted"):
            log.info("move_symbol: %s was converted (nested) — skip remove & import", symbol_name)
            remove_result = {"success": True, "skipped": True, "note": "conversion already removed source"}
            import_result = {"success": True, "skipped": True, "note": "conversion already added import"}
            _t_remove = 0.0
            _t_import = 0.0
        else:
            _t1 = _time.monotonic()
            try:
                remove_result = self.remove_symbol(source, symbol_name)
                _t_remove = _time.monotonic() - _t1
            except RefactoringError as e:
                log.warning("move_symbol: remove FAILED for %s: %s — rolling back", symbol_name, e)
                _rollback('remove')
                return {
                    'success': False,
                    'symbol': symbol_name,
                    'error': f"Remove failed: {e}",
                    'step': 'remove',
                    'category': e.category,
                }

            # Step 3: Add import
            _t2 = _time.monotonic()
            try:
                import_result = self.add_import(source, target_module, short_name)
                _t_import = _time.monotonic() - _t2
            except RefactoringError as e:
                log.warning("move_symbol: import FAILED for %s: %s — rolling back", symbol_name, e)
                _rollback('import')
                return {
                    'success': False,
                    'symbol': symbol_name,
                    'error': f"Import failed: {e}",
                    'step': 'import',
                    'category': e.category,
                }

        _t_total = _time.monotonic() - _t0
        log.info("move_symbol OK: %s %s→%s (already_in_target=%s, extract=%.3fs, remove=%.3fs, import=%.3fs, total=%.3fs)",
                 symbol_name, os.path.basename(source), os.path.basename(target),
                 symbol_already_in_target, _t_extract, _t_remove, _t_import, _t_total)

        result = {
            'success': True,
            'symbol': symbol_name,
            'source': source,
            'target': target,
            'already_in_target': symbol_already_in_target,
            'steps': {
                'extract': extract_result,
                'remove': remove_result,
                'import': import_result,
            },
        }
        # Expose nested function info at top level for LLM visibility
        if extract_result.get("converted"):
            result["nested_function"] = True
            result["captured_vars"] = extract_result.get("captured_vars", [])
            result["references_updated"] = extract_result.get("references_updated", 0)
        if extract_result.get("class_name"):
            result["stateful_closure"] = True
            result["class_name"] = extract_result.get("class_name")
        return result

    def _list_symbols(self, path: str) -> list[dict[str, Any]]:
        """List all top-level symbols in a Python file."""
        try:
            from agent_files import list_symbols
            result = list_symbols(path)
            if result.get('success'):
                return result.get('symbols', [])
        except Exception:
            pass
        return []

    def _build_dependency_graph(self, source: str) -> dict[str, Any]:
        """Build a full dependency graph of all top-level symbols in source.

        For each top-level symbol, identifies:
        - Which other top-level symbols it references (internal dependencies)
        - Which imported names it uses (external dependencies)
        - Which decorators are applied to it

        Also maps which top-level imports provide which names.
        """
        try:
            source = self._abs(source)
        except RefactoringError as e:
            return {'success': False, 'error': str(e), 'category': e.category}
        content, lines = self._read(source)

        try:
            tree = ast.parse(content)
        except SyntaxError as e:
            return {'success': False, 'error': f"Syntax error: {e}"}

        graph = DependencyGraph()

        top_nodes: list[ast.AST] = []
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                top_nodes.append(node)
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                for t in (node.targets if isinstance(node, ast.Assign) else [node.target]):
                    if isinstance(t, ast.Name) and not t.id.startswith('_'):
                        top_nodes.append(node)
                        break

        symbol_index: dict[str, ast.AST] = {}
        for node in top_nodes:
            name = self._node_name(node)
            if name:
                symbol_index[name] = node

        all_symbol_names = set(symbol_index.keys())
        for node in top_nodes:
            name = self._node_name(node)
            if not name:
                continue

            start, end = AstAnalyzer.get_symbol_lines(lines, node)
            sym_type = AstAnalyzer.get_symbol_type(node)
            sn = SymbolNode(name, sym_type, start + 1)

            deco_names = []
            if hasattr(node, 'decorator_list'):
                for d in node.decorator_list:
                    dname = self._decorator_name(d)
                    if dname:
                        deco_names.append(dname)
            sn.decorators = deco_names

            visitor = ImportVisitor()
            visitor.visit(node)
            used = visitor.names

            dep_names = {n for n in used if n in all_symbol_names and n != name}
            sn.dependencies = dep_names

            sn.imported_names = used - dep_names - _BUILTINS

            graph.nodes[name] = sn

        for node in ast.iter_child_nodes(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                provided = AstAnalyzer.names_from_import_node(node)
                for p in provided:
                    graph.external_imports[p].append(lines[node.lineno - 1])

        return {
            'success': True,
            'source': source,
            'graph': graph.to_dict(),
        }

    def _node_name(self, node: ast.AST) -> str | None:
        return AstAnalyzer.node_name(node)

    def _decorator_name(self, d: ast.AST) -> str | None:
        return AstAnalyzer.decorator_name(d)

    def analyze_dependencies(self, source: str) -> dict[str, Any]:
        """Public API: build and return the dependency graph as a dict."""
        return self._build_dependency_graph(source)

    def suggest_module_groups(self, source: str, max_group_size: int = 5) -> dict[str, Any]:
        """Suggest module groupings based on the dependency graph.

        Uses Tarjan's algorithm to find strongly connected components (SCCs).
        Symbols in the same SCC MUST stay together to avoid circular imports.
        Loose (non-SCC) symbols are grouped by dependency proximity.

        Returns a suggested breakdown into module files.
        """
        graph_result = self._build_dependency_graph(source)
        if not graph_result['success']:
            return graph_result

        g = graph_result['graph']
        nodes = g['symbols']

        if not nodes:
            return {'success': True, 'source': source, 'groups': [], 'graph': g}

        name_list = list(nodes.keys())
        name_index = {n: i for i, n in enumerate(name_list)}
        n = len(name_list)

        adj: list[list[int]] = [[] for _ in range(n)]
        for sym_name, sym_data in nodes.items():
            if sym_name in name_index:
                for dep in sym_data.get('dependencies', []):
                    if dep in name_index:
                        adj[name_index[sym_name]].append(name_index[dep])

        index_counter = 0
        indices = [-1] * n
        lowlink = [0] * n
        on_stack = [False] * n
        stack: list[int] = []
        sccs: list[set[int]] = []

        def strongconnect(v: int) -> None:
            nonlocal index_counter
            indices[v] = index_counter
            lowlink[v] = index_counter
            index_counter += 1
            stack.append(v)
            on_stack[v] = True

            for w in adj[v]:
                if indices[w] == -1:
                    strongconnect(w)
                    lowlink[v] = min(lowlink[v], lowlink[w])
                elif on_stack[w]:
                    lowlink[v] = min(lowlink[v], indices[w])

            if lowlink[v] == indices[v]:
                scc: set[int] = set()
                while True:
                    w = stack.pop()
                    on_stack[w] = False
                    scc.add(w)
                    if w == v:
                        break
                sccs.append(scc)

        for v in range(n):
            if indices[v] == -1:
                strongconnect(v)

        single_syms = []
        multi_sccs = []
        for scc in sccs:
            if len(scc) == 1:
                v = next(iter(scc))
                single_syms.append(name_list[v])
            else:
                multi_sccs.append([name_list[v] for v in scc])

        grouped_singles: list[list[str]] = []
        remaining = set(single_syms)

        while remaining:
            group: list[str] = []
            candidates = sorted(remaining, key=lambda s: nodes[s]['line'])

            for c in candidates:
                if len(group) >= max_group_size:
                    break
                group.append(c)
                remaining.remove(c)

            if group:
                grouped_singles.append(group)

        all_groups = multi_sccs + grouped_singles

        decorator_map: dict[str, set[str]] = defaultdict(set)
        for sym_name, sym_data in nodes.items():
            for d in sym_data.get('decorators', []):
                decorator_map[d].add(sym_name)

        return {
            'success': True,
            'source': source,
            'groups': [
                {
                    'symbols': grp,
                    'size': len(grp),
                    'is_scc': grp in multi_sccs,
                    'lines': f"{min(nodes[s]['line'] for s in grp)}-{max(nodes[s]['line'] for s in grp)}",
                }
                for grp in all_groups
            ],
            'graph': g,
        }
