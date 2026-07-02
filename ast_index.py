import ast
import os
from typing import Any, Dict, List, Tuple, Optional
from lang import t
from path_utils import _resolve_path, _resolve_workdir
_indexed_dirs: set = set()






def _format_params(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """format params.

    Args:
        node:"""
    args = node.args
    parts = []
    # positional args
    total = len(args.args)
    defaults = [None] * (total - len(args.defaults)) + list(args.defaults) if args.defaults else [None] * total
    for i, a in enumerate(args.args):
        prefix = "self, " if i == 0 and a.arg == "self" else ""
        if i == 0 and a.arg == "self":
            continue
        p = a.arg
        if defaults[i] is not None:
            try:
                p += f"={ast.unparse(defaults[i])}"
            except Exception:
                p += "=..."
        parts.append(p)
    if args.vararg:
        parts.append(f"*{args.vararg.arg}")
    for a in args.kwonlyargs:
        parts.append(f"{a.arg}=...")
    if args.kwarg:
        parts.append(f"**{args.kwarg.arg}")
    return ", ".join(parts)



def build_ast_index(code: str, filename: str) -> str | None:
    """build ast index.

    Args:
        code:
        filename:"""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return None
    index_lines = [f"### {filename}"]
    def _sig(n):
        """sig.

        Args:
            n:"""
        return _format_params(n)

    def _doc(n):
        """doc.

        Args:
            n:"""
        d = ast.get_docstring(n) or ""
        return (" — " + d.splitlines()[0][:80]) if d else ""

    class _Builder(ast.NodeVisitor):
        """builder.

        Extends: ast.NodeVisitor"""
        def __init__(self) -> None:
            """Initialize the instance."""
            self.in_class = False
        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            """visit class def.

            Args:
                node:"""
            old = self.in_class
            self.in_class = True
            methods = []
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    methods.append(f"    {item.name}({_sig(item)}) [{item.lineno}]{_doc(item)}")
            index_lines.append(f"  class {node.name} [{node.lineno}]{_doc(node)}")
            index_lines.extend(methods)
            self.generic_visit(node)
            self.in_class = old
        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            """visit function def.

            Args:
                node:"""
            if not self.in_class:
                index_lines.append(f"  {node.name}({_sig(node)}) [{node.lineno}]{_doc(node)}")
            self.generic_visit(node)
        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            """visit async function def.

            Args:
                node:"""
            if not self.in_class:
                index_lines.append(f"  {node.name}({_sig(node)}) [{node.lineno}]{_doc(node)}")
            self.generic_visit(node)

    _Builder().visit(tree)
    return "\n".join(index_lines) if len(index_lines) > 1 else None



def _find_enclosing_symbol(tree: ast.Module, target_line: int) -> ast.AST | None:
    """find enclosing symbol.

    Args:
        tree:
        target_line:"""
    best = None
    class NodeVisitor(ast.NodeVisitor):
        """node visitor.

        Extends: ast.NodeVisitor"""
        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            """visit function def.

            Args:
                node:"""
            nonlocal best
            if node.lineno <= target_line <= (getattr(node, 'end_lineno', node.lineno) or node.lineno):
                best = node
            self.generic_visit(node)

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            """visit async function def.

            Args:
                node:"""
            nonlocal best
            if node.lineno <= target_line <= (getattr(node, 'end_lineno', node.lineno) or node.lineno):
                best = node
            self.generic_visit(node)

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            """visit class def.

            Args:
                node:"""
            nonlocal best
            if node.lineno <= target_line <= (getattr(node, 'end_lineno', node.lineno) or node.lineno):
                best = node
            self.generic_visit(node)

    NodeVisitor().visit(tree)
    return best



def _list_top_level_vars(tree: ast.Module) -> list[tuple[str, str, int]]:
    """list top level vars.

    Args:
        tree:"""
    vars = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    vars.append((target.id, "variable", node.lineno))
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name):
                vars.append((node.target.id, "variable", node.lineno))
    return vars



def _list_top_level_symbols(tree: ast.Module) -> list[tuple[str, str, int]]:
    """list top level symbols.

    Args:
        tree:"""
    symbols = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.FunctionDef):
            symbols.append((node.name, "function", node.lineno))
        elif isinstance(node, ast.AsyncFunctionDef):
            symbols.append((node.name, "async_function", node.lineno))
        elif isinstance(node, ast.ClassDef):
            symbols.append((node.name, "class", node.lineno))
    symbols.extend(_list_top_level_vars(tree))
    return symbols



def _scan_dir_into_index(dir_path: str, index: dict) -> None:
    """Scan a directory for .py files and add symbols to the index.

    Skips directories already in _indexed_dirs to avoid re-scanning.
    """
    abs_path = os.path.abspath(dir_path)
    if abs_path in _indexed_dirs:
        return
    _indexed_dirs.add(abs_path)

    try:
        entries = os.listdir(abs_path)
    except Exception:
        return

    for fname in entries:
        if not fname.endswith('.py'):
            continue
        fpath = os.path.join(abs_path, fname)
        try:
            with open(fpath, 'r', encoding='utf-8') as f:
                tree = ast.parse(f.read())
        except Exception:
            continue
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                index.setdefault(node.name, []).append((fname, node.lineno))
            elif isinstance(node, ast.ClassDef):
                index.setdefault(node.name, []).append((fname, node.lineno, "class"))
                for child in node.body:
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        dotted = f"{node.name}.{child.name}"
                        index.setdefault(dotted, []).append((fname, child.lineno))
                        index.setdefault(child.name, []).append((fname, child.lineno))
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        index.setdefault(target.id, []).append((fname, node.lineno))
            elif isinstance(node, ast.AnnAssign):
                if isinstance(node.target, ast.Name):
                    index.setdefault(node.target.id, []).append((fname, node.lineno))



def _build_global_symbol_index() -> dict[str, list[Any]]:
    """Build global symbol index from CWD + AGENT_WORKDIR if different."""
    index = {}
    cwd = os.path.abspath('.')
    _scan_dir_into_index(cwd, index)
    workdir = _resolve_workdir()
    if os.path.normcase(workdir) != os.path.normcase(cwd):
        _scan_dir_into_index(workdir, index)
    return index

_GLOBAL_SYMBOL_INDEX = _build_global_symbol_index()


def _ensure_workdir_indexed() -> None:
    """Ensure workdir .py files are included in the global symbol index."""
    workdir = _resolve_workdir()
    cwd = os.path.abspath('.')
    if os.path.normcase(workdir) != os.path.normcase(cwd):
        _scan_dir_into_index(workdir, _GLOBAL_SYMBOL_INDEX)



def list_symbols(filepath: str) -> dict[str, Any]:
    """List all top-level symbols (functions, classes, variables) in a Python file.

    Args:
        filepath: Path to the .py file

    Returns:
        dict with success flag, filepath, and symbols list"""
    filepath = _resolve_path(filepath)
    if not filepath or not os.path.exists(filepath):
        return {"success": False, "error": f"File not found: {filepath}"}
    if not filepath.lower().endswith('.py'):
        preview = ""
        try:
            with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
                lines = [next(f) for _ in range(100)]
            preview = "\n" + "".join(lines)[:3000]
        except (OSError, StopIteration):
            pass
        return {
            "success": False,
            "error": (
                f"list_symbols understøtter kun Python-filer (.py), fik '{os.path.basename(filepath)}'. "
                f"Brug read_chunk for at læse hele filen."
            ),
            "content_preview": preview if preview else None,
        }
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            code = f.read()
        tree = ast.parse(code)
    except SyntaxError as e:
        return {"success": False, "error": f"Syntax error in {os.path.basename(filepath)}: {e}"}
    except Exception as e:
        return {"success": False, "error": f"Could not parse {os.path.basename(filepath)}: {e}"}

    symbols = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            sig = "(" + ", ".join(a.arg for a in node.args.args) + ")"
            symbols.append({"name": node.name, "type": "function", "line": node.lineno, "signature": sig})
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.ClassDef):
            methods = []
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    sig = "(" + ", ".join(a.arg for a in child.args.args) + ")"
                    methods.append({"name": child.name, "type": "method", "line": child.lineno, "signature": sig})
            symbols.append({"name": node.name, "type": "class", "line": node.lineno, "methods": methods})
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    symbols.append({"name": target.id, "type": "variable", "line": node.lineno})
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name):
                symbols.append({"name": node.target.id, "type": "variable", "line": node.lineno})

    symbols.sort(key=lambda s: ({"class": 0, "function": 1, "async_function": 1, "method": 2, "variable": 3}.get(s.get("type", "variable"), 9), s.get("line", 0)))

    return {
        "success": True,
        "filepath": filepath,
        "symbols": symbols,
        "count": len(symbols),
    }



def locate_code(filepath: str | None = None, name: str | None = None, line_no: int | None = None) -> dict[str, Any]:
    """locate code.

    Args:
        filepath:
        name:
        line_no:"""
    _ensure_workdir_indexed()

    if not filepath and name:
        matches = _GLOBAL_SYMBOL_INDEX.get(name, [])
        if not matches:
            suggestions = sorted(_GLOBAL_SYMBOL_INDEX.keys())
            tip = ""
            if suggestions:
                fuzzy = [s for s in suggestions if name.lower() in s.lower()][:5]
                if fuzzy:
                    tip = f" Mente du: {', '.join(fuzzy)}?"
                else:
                    tip = f" Prøv list_symbols eller brug et af disse symboler: {', '.join(suggestions[:12])}"
            return {"success": False, "error": f"Symbol '{name}' not found in any file.{tip}"}
        if len(matches) > 1:
            files = ", ".join(m[0] for m in matches)
            return {"success": False, "error": f"Symbol '{name}' found in multiple files: {files}. Specify filepath='fil.py' to disambiguate."}
        filepath = matches[0][0]
    if filepath:
        filepath = _resolve_path(filepath)
    if not filepath or not os.path.exists(filepath):
        return {"success": False, "error": f"File not found: {filepath}"}
    if not filepath.lower().endswith('.py'):
        # Auto-read first lines so the LLM has immediate context
        preview = ""
        try:
            with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
                lines = [next(f) for _ in range(100)]
            preview = "\n" + "".join(lines)[:3000]
        except (OSError, StopIteration):
            pass
        return {
            "success": False,
            "error": (
                f"locate_code understøtter kun Python-filer (.py), fik '{os.path.basename(filepath)}'. "
                f"Brug read_chunk for at læse hele filen."
            ),
            "content_preview": preview if preview else None,
        }
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            code = f.read()
        tree = ast.parse(code)
    except SyntaxError as e:
        return {"success": False, "error": f"Syntax error in {os.path.basename(filepath)}: {e}"}
    except Exception as e:
        return {"success": False, "error": f"Could not parse {os.path.basename(filepath)}: {e}"}

    symbols = _list_top_level_symbols(tree)
    siblings = "\n".join(f"  {s[0]} ({s[1]}, line {s[2]})" for s in symbols) if symbols else "  (none)"

    _lines = code.splitlines()
    def _ctx(s: int, e: int, n: int = 5) -> dict:
        """Build pre/post context dict from line numbers (1-indexed)."""
        pre = "\n".join(_lines[max(0, s - 1 - n):s - 1])
        post = "\n".join(_lines[e:min(len(_lines), e + n)])
        return {"pre_context": pre, "post_context": post}

    if name:
        parts = name.split(".", 1)
        func_name = parts[-1]
        class_name = parts[0] if len(parts) == 2 else None

        for node in ast.walk(tree):
            if class_name:
                if isinstance(node, ast.ClassDef) and node.name == class_name:
                    for child in node.body:
                        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child.name == func_name:
                            start = child.lineno
                            end = getattr(child, 'end_lineno', start) or start
                            return {
                                "success": True,
                                "file": filepath,
                                "name": name,
                                "type": "method",
                                "line": start,
                                "end_line": end,
                                "body": "\n".join(_lines[start - 1:end]),
                                "also_in_file": siblings,
                                **_ctx(start, end),
                            }
                    return {"success": False, "error": f"Method '{func_name}' not found in class '{class_name}' in {os.path.basename(filepath)}"}
            else:
                if isinstance(node, ast.FunctionDef) and node.name == func_name:
                    start = node.lineno
                    # Include decorators if present
                    if node.decorator_list:
                        decorator_lines = [d.lineno for d in node.decorator_list if hasattr(d, 'lineno')]
                        if decorator_lines:
                            start = min(decorator_lines)
                    end = getattr(node, 'end_lineno', start) or start
                    return {
                        "success": True,
                        "file": filepath,
                        "name": name,
                        "type": "function",
                        "line": start,
                        "end_line": end,
                        "body": "\n".join(_lines[start - 1:end]),
                        "also_in_file": siblings,
                        **_ctx(start, end),
                    }
                elif isinstance(node, ast.ClassDef) and node.name == func_name:
                    start = node.lineno
                    if node.decorator_list:
                        decorator_lines = [d.lineno for d in node.decorator_list if hasattr(d, 'lineno')]
                        if decorator_lines:
                            start = min(decorator_lines)
                    end = getattr(node, 'end_lineno', start) or start
                    return {
                        "success": True,
                        "file": filepath,
                        "name": name,
                        "type": "class",
                        "line": start,
                        "end_line": end,
                        "body": "\n".join(_lines[start - 1:end]),
                        "also_in_file": siblings,
                        **_ctx(start, end),
                    }
                elif isinstance(node, ast.AsyncFunctionDef) and node.name == func_name:
                    start = node.lineno
                    # Include decorators if present
                    if node.decorator_list:
                        decorator_lines = [d.lineno for d in node.decorator_list if hasattr(d, 'lineno')]
                        if decorator_lines:
                            start = min(decorator_lines)
                    end = getattr(node, 'end_lineno', start) or start
                    return {
                        "success": True,
                        "file": filepath,
                        "name": name,
                        "type": "async_function",
                        "line": start,
                        "end_line": end,
                        "body": "\n".join(_lines[start - 1:end]),
                        "also_in_file": siblings,
                        **_ctx(start, end),
                    }
                elif isinstance(node, ast.Assign) and func_name in (t.id for t in node.targets if isinstance(t, ast.Name)):
                    start = node.lineno
                    end = getattr(node, 'end_lineno', start) or start
                    return {
                        "success": True,
                        "file": filepath,
                        "name": name,
                        "type": "variable",
                        "line": start,
                        "end_line": end,
                        "body": "\n".join(_lines[start - 1:end]),
                        "also_in_file": siblings,
                        **_ctx(start, end),
                    }
                elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == func_name:
                    start = node.lineno
                    end = getattr(node, 'end_lineno', start) or start
                    return {
                        "success": True,
                        "file": filepath,
                        "name": name,
                        "type": "variable",
                        "line": start,
                        "end_line": end,
                        "body": "\n".join(_lines[start - 1:end]),
                        "also_in_file": siblings,
                        **_ctx(start, end),
                    }
        return {"success": False, "error": f"Symbol '{name}' not found in {os.path.basename(filepath)}"}

    if line_no is not None:
        symbol = _find_enclosing_symbol(tree, line_no)
        if symbol is None:
            return {
                "success": True,
                "file": filepath,
                "name": None,
                "type": "module",
                "line": line_no,
                "end_line": line_no,
                "body": _lines[line_no - 1] if 1 <= line_no <= len(_lines) else "",
                "pre_context": "\n".join(_lines[max(0, line_no - 1 - 5):line_no - 1]),
                "post_context": "\n".join(_lines[line_no:min(len(_lines), line_no + 5)]),
            }

        node_type = "class" if isinstance(symbol, ast.ClassDef) else "async_function" if isinstance(symbol, ast.AsyncFunctionDef) else "function"
        start = symbol.lineno
        end = getattr(symbol, 'end_lineno', start) or start

        class_name = None
        for parent in ast.walk(tree):
            if isinstance(parent, ast.ClassDef) and hasattr(parent, 'body'):
                if symbol in [child for child in ast.walk(parent) if child is symbol]:
                    class_name = parent.name
                    break

        full_name = f"{class_name}.{symbol.name}" if class_name else symbol.name
        node_type = "method" if class_name else node_type

        return {
            "success": True,
            "file": filepath,
            "name": full_name,
            "type": node_type,
            "line": start,
            "end_line": end,
            "body": "\n".join(_lines[start - 1:end]),
            "also_in_file": siblings,
            **_ctx(start, end),
        }

    return {"success": False, "error": "Specify either 'name' or 'line_no'"}
