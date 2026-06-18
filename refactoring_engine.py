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
import hashlib
from typing import Any
from collections import defaultdict


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
# Nøgle: absolut sti til source-filen + hash af source-indhold.
# Værdi: sæt af symbolnavne der allerede er flyttet til en target.
# Nulstilles når source-filen ændres (ny mtime/hash).
_extracted_registry: dict[str, set[str]] = {}
_registry_source_hashes: dict[str, str] = {}


def _registry_key(source: str) -> str:
    """Generer en nøgle for registret: absolut sti + hash af source.

    Hashet sikrer at registret nulstilles når source-filen revertes
    (f.eks. mellem sessioner) så symboler kan ekstraheres igen.
    """
    abspath = os.path.abspath(source)
    try:
        with open(abspath, 'rb') as f:
            content = f.read()
        h = hashlib.sha256(content).hexdigest()[:16]
        old_h = _registry_source_hashes.get(abspath)
        if old_h and old_h != h:
            # Filen har ændret sig (revert/ny version) — nulstil registret
            _extracted_registry.pop(abspath, None)
        _registry_source_hashes[abspath] = h
        return abspath
    except (OSError, IOError):
        return abspath


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
        os.replace(tmppath, self.path)


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
        os.replace(tmppath, path)

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
                    os.replace(tmppath, path)
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
        os.replace(tmppath, path)

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
        return p

    def _read(self, path: str) -> tuple[str, list[str]]:
        path = self._abs(path)
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read().replace('\r\n', '\n').replace('\r', '\n')
        return content, content.split('\n')

    def _write(self, path: str, content: str) -> None:
        path = self._resolve(path)
        tmppath = path + '.tmp'
        with open(tmppath, 'w', encoding='utf-8') as f:
            f.write(content)
        os.replace(tmppath, path)

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

        content, lines = self._read(source)

        try:
            tree = ast.parse(content)
        except SyntaxError as e:
            raise RefactoringError(
                f"Syntax error in source: {e}",
                category=RefactoringError.SYNTAX,
                filepath=source,
                details={"line": e.lineno, "msg": e.msg}
            )

        node = AstAnalyzer.find_node(tree, symbol_name)
        if node is None:
            raise RefactoringError(
                f"Symbol '{symbol_name}' not found in {os.path.basename(source)}",
                category=RefactoringError.SYMBOL_NOT_FOUND,
                filepath=source,
                details={"symbol": symbol_name}
            )

        start_line, end_line = AstAnalyzer.get_symbol_lines(lines, node)
        symbol_code = '\n'.join(lines[start_line:end_line])
        symbol_type = AstAnalyzer.get_symbol_type(node)

        visitor = ImportVisitor()
        visitor.visit(node)
        used_names = visitor.names

        needed_imports = ImportResolver.filter_for_symbol(content, lines, used_names)

        # Tjek for circular imports: hvis nogen af de nødvendige imports
        # allerede har en `from target import ...` eller `import target`.
        _tgt_module = os.path.splitext(os.path.basename(target))[0]
        for imp in needed_imports:
            _imp_mod = _extract_module_from_import(imp)
            if _imp_mod and _has_back_import(_imp_mod, target):
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
                raise RefactoringError(
                    f"Target file would be syntactically invalid after extraction: {e}",
                    category=RefactoringError.TARGET_SYNTAX,
                    filepath=target,
                    details={"line": e.lineno, "msg": e.msg, "symbol": symbol_name}
                )

        self._write(target, target_content)

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
            snapshot.restore()
            raise

        remaining = self._list_symbols(source)

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
            raise

        return {
            'success': True,
            'import_added': added,
            'source': source,
            'import_line': import_stmt,
        }

    def verify_refactor(self, source: str) -> dict[str, Any]:
        """Verify a source file is syntactically valid Python.

        Returns dict with success, lines, symbols.
        """
        source = self._abs(source)

        try:
            content, lines = self._read(source)
            ast.parse(content)
        except SyntaxError as e:
            return {'success': False, 'error': f"Syntax error: {e}"}
        except Exception as e:
            return {'success': False, 'error': f"Failed to read/parse: {e}"}

        symbols = self._list_symbols(source)

        return {
            'success': True,
            'source': source,
            'lines': len(lines),
            'symbols': symbols,
        }

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
        results = []
        for i, sym in enumerate(symbols):
            if i > 0:
                import time
                time.sleep(0.1)
            # Tjek om symbolet allerede er ekstraheret i en tidligere session
            if _is_already_extracted(source, sym):
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
                results.append({
                    "success": False,
                    "symbol": sym,
                    "error": str(e),
                    "category": e.category,
                })
        return {
            "success": True,
            "source": source,
            "target": target,
            "total": len(symbols),
            "succeeded": sum(1 for r in results if r.get("success")),
            "failed": sum(1 for r in results if not r.get("success")),
            "results": results,
        }

    def move_symbol(self, source: str, symbol_name: str, target: str) -> dict[str, Any]:
        """Full move: extract + remove + add_import.

        Template Method pattern: defines the skeleton of a move operation.
        Steps:
        1. Extract symbol to target (imports + code)
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
            source_snapshot.restore()
            if target_snapshot:
                target_snapshot.restore()
            else:
                try:
                    os.remove(target)
                except OSError:
                    pass

        # Step 1: Extract
        try:
            extract_result = self.extract_symbol(source, symbol_name, target)
        except RefactoringError as e:
            return {
                'success': False,
                'error': f"Extract failed: {e}",
                'step': 'extract',
                'category': e.category,
            }

        # Step 2: Remove
        try:
            remove_result = self.remove_symbol(source, symbol_name)
        except RefactoringError as e:
            _rollback('remove')
            return {
                'success': False,
                'error': f"Remove failed: {e}",
                'step': 'remove',
                'category': e.category,
            }

        # Step 3: Add import
        try:
            import_result = self.add_import(source, target_module, short_name)
        except RefactoringError as e:
            _rollback('import')
            return {
                'success': False,
                'error': f"Import failed: {e}",
                'step': 'import',
                'category': e.category,
            }

        return {
            'success': True,
            'symbol': symbol_name,
            'source': source,
            'target': target,
            'steps': {
                'extract': extract_result,
                'remove': remove_result,
                'import': import_result,
            },
        }

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
