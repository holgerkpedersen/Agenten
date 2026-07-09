import ast
from refactor_utils import log, _BUILTINS, _BUILTINS_TYPING, _KNOWN_SYMBOL_IMPORTS, _KNOWN_MODULE_SYMBOLS, _extracted_registry, _atomic_replace, _parse_symbols_list, _auto_add_known_imports, _list_top_level_symbol_names, _find_unresolved_local_deps, _detect_import_cycle_risk, _split_imports_from_code, _registry_key, _is_nested_function, clear_extracted_registry, _mark_extracted, _is_already_extracted, _extract_module_from_import, _has_back_import
from typing import Any
from collections import defaultdict
from lang import t

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
