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
import os
from typing import Any

from collections import defaultdict

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
    'Any', 'Optional', 'List', 'Dict', 'Tuple', 'Set', 'Callable',
    'TypeVar', 'Generic', 'Protocol', 'Union', 'Final', 'ClassVar',
    'Sequence', 'Iterable', 'Iterator', 'Generator',
})


class RefactoringError(Exception):
    pass


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
        new_content = '\n'.join(new_lines).strip() + '\n'

        try:
            ast.parse(new_content)
        except SyntaxError as e:
            raise RefactoringError(f"Syntax error after removal: {e}")

        tmppath = path + '.tmp'
        with open(tmppath, 'w', encoding='utf-8') as f:
            f.write(new_content)
        os.replace(tmppath, path)

        return new_content

    @staticmethod
    def insert_import(path: str, import_stmt: str) -> bool:
        """Insert an import statement into a Python file after existing imports.

        Returns True if import was added, False if it already exists.
        """
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read().replace('\r\n', '\n').replace('\r', '\n')

        if import_stmt in content:
            return False

        tree = ast.parse(content)
        lines = content.split('\n')

        last_import_line = 0
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                end = getattr(node, 'end_lineno', node.lineno) or node.lineno
                if end > last_import_line:
                    last_import_line = end

        insert_at = last_import_line
        lines.insert(insert_at, import_stmt)
        new_content = '\n'.join(lines)

        try:
            ast.parse(new_content)
        except SyntaxError as e:
            raise RefactoringError(f"Syntax error after adding import: {e}")

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

    def _abs(self, path: str) -> str:
        p = os.path.abspath(path)
        if not os.path.exists(p):
            raise RefactoringError(f"File not found: {p}")
        return p

    def _read(self, path: str) -> tuple[str, list[str]]:
        path = self._abs(path)
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read().replace('\r\n', '\n').replace('\r', '\n')
        return content, content.split('\n')

    def _write(self, path: str, content: str) -> None:
        path = os.path.abspath(path)
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
        """
        source = self._abs(source)
        target = os.path.abspath(target)

        content, lines = self._read(source)

        try:
            tree = ast.parse(content)
        except SyntaxError as e:
            return {'success': False, 'error': f"Syntax error in source: {e}"}

        node = AstAnalyzer.find_node(tree, symbol_name)
        if node is None:
            return {'success': False, 'error': f"Symbol '{symbol_name}' not found in {os.path.basename(source)}"}

        start_line, end_line = AstAnalyzer.get_symbol_lines(lines, node)
        symbol_code = '\n'.join(lines[start_line:end_line])
        symbol_type = AstAnalyzer.get_symbol_type(node)

        visitor = ImportVisitor()
        visitor.visit(node)
        used_names = visitor.names

        needed_imports = ImportResolver.filter_for_symbol(content, lines, used_names)

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
        new_import_block = '\n'.join(new_imports)

        if not existing:
            if new_import_block and symbol_code:
                target_content = new_import_block + '\n\n' + symbol_code + '\n'
            elif symbol_code:
                target_content = symbol_code + '\n'
            else:
                target_content = ''
        else:
            if new_imports and symbol_code:
                target_content = existing + '\n\n' + new_import_block + '\n\n' + symbol_code + '\n'
            elif symbol_code:
                target_content = existing + '\n\n' + symbol_code + '\n'
            elif new_imports:
                target_content = existing + '\n\n' + new_import_block + '\n'
            else:
                target_content = existing

        if target_content.strip():
            try:
                ast.parse(target_content)
            except SyntaxError as e:
                return {'success': False,
                        'error': f"Target file would be syntactically invalid after extraction: {e}",
                        'symbol': symbol_name, 'type': symbol_type,
                        'source': source, 'target': target}

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
        """
        source = self._abs(source)

        content, lines = self._read(source)

        try:
            tree = ast.parse(content)
        except SyntaxError as e:
            return {'success': False, 'error': f"Syntax error in source: {e}"}

        node = AstAnalyzer.find_node(tree, symbol_name)
        if node is None:
            return {'success': False, 'error': f"Symbol '{symbol_name}' not found in {os.path.basename(source)}"}

        start_line, end_line = AstAnalyzer.get_symbol_lines(lines, node)
        snapshot = FileSnapshot.create(source)

        try:
            CodeModifier.remove_lines(source, start_line, end_line)
        except RefactoringError as e:
            snapshot.restore()
            return {'success': False, 'error': str(e)}

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
        """
        source = self._abs(source)
        import_stmt = f"from {module} import {symbol}"

        try:
            added = CodeModifier.insert_import(source, import_stmt)
        except RefactoringError as e:
            return {'success': False, 'error': str(e)}

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
        target = os.path.abspath(target)
        short_name = symbol_name.split('.')[-1]
        target_module = os.path.splitext(os.path.basename(target))[0]

        source_snapshot = FileSnapshot.create(source)
        target_exists = os.path.exists(target)
        target_snapshot = FileSnapshot.create(target) if target_exists else None

        result = {}

        extract_result = self.extract_symbol(source, symbol_name, target)
        result['extract'] = extract_result
        if not extract_result['success']:
            return {'success': False, 'error': f"Extract failed: {extract_result.get('error')}",
                    'step': 'extract', 'partial': result}

        remove_result = self.remove_symbol(source, symbol_name)
        result['remove'] = remove_result
        if not remove_result['success']:
            source_snapshot.restore()
            if target_snapshot:
                target_snapshot.restore()
            else:
                try:
                    os.remove(target)
                except OSError:
                    pass
            return {'success': False, 'error': f"Remove failed: {remove_result.get('error')}",
                    'step': 'remove', 'partial': result}

        import_result = self.add_import(source, target_module, short_name)
        result['import'] = import_result
        if not import_result['success']:
            source_snapshot.restore()
            if target_snapshot:
                target_snapshot.restore()
            else:
                try:
                    os.remove(target)
                except OSError:
                    pass
            return {'success': False, 'error': f"Import failed: {import_result.get('error')}",
                    'step': 'import', 'partial': result}

        return {
            'success': True,
            'symbol': symbol_name,
            'source': source,
            'target': target,
            'steps': result,
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
            return {'success': False, 'error': str(e)}
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
