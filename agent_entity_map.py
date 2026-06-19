"""Entity Map — structured cross-file analysis for LLM context.

Extracts all entities (functions, classes, imports, variables) from loaded
Python files and detects anomalies: circular imports, missing typing.Any,
stale __all__ entries, orphaned symbols. Injected into the LLM prompt so the
model understands the codebase structure without reading every function.
"""

import ast
import os
import re
from typing import Any


def build_entity_map(filepaths: list[str]) -> dict[str, Any]:
    """Build a complete entity map for the given file paths.

    Returns:
        dict with keys: ``files`` (per-file entity lists),
        ``cross_file`` (dependencies, circular imports),
        ``anomalies`` (detected issues).
    """
    files: dict[str, Any] = {}
    all_symbols: dict[str, list[tuple[str, str]]] = {}  # symbol -> [(file, type)]

    for fp in filepaths:
        if not fp or not fp.endswith(".py") or not os.path.exists(fp):
            continue
        info = _analyze_file(fp)
        if info:
            basename = os.path.basename(fp)
            files[basename] = info
            for func in info.get("functions", []):
                all_symbols.setdefault(func["name"], []).append((basename, "function"))
            for cls in info.get("classes", []):
                all_symbols.setdefault(cls["name"], []).append((basename, "class"))
                for m in cls.get("methods", []):
                    all_symbols.setdefault(f"{cls['name']}.{m['name']}", []).append((basename, "method"))
            for var in info.get("variables", []):
                all_symbols.setdefault(var["name"], []).append((basename, "variable"))

    cross_file = _find_cross_references(files)
    anomalies = _detect_anomalies(files, cross_file)

    return {
        "files": files,
        "cross_file": cross_file,
        "anomalies": anomalies,
    }


def _analyze_file(filepath: str) -> dict[str, Any] | None:
    """Extract all entities from a single .py file."""
    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except OSError:
        return None

    try:
        tree = ast.parse(content)
    except SyntaxError:
        return None

    lines = content.split("\n")
    imports: list[dict[str, Any]] = []
    functions: list[dict[str, Any]] = []
    classes: list[dict[str, Any]] = []
    variables: list[dict[str, Any]] = []
    constants: list[dict[str, Any]] = []
    all_list: list[str] = []
    used_names: set[str] = set()

    for node in ast.iter_child_nodes(tree):
        # Imports
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append({
                    "module": alias.name if not alias.asname else f"{alias.name} as {alias.asname}",
                    "symbol": None,
                    "line": node.lineno,
                    "type": "import",
                })
                used_names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            symbol_list = [a.name if not a.asname else f"{a.name} as {a.asname}" for a in node.names]
            imports.append({
                "module": node.module or "",
                "symbols": symbol_list,
                "line": node.lineno,
                "type": "import_from",
            })
            for s in node.names:
                used_names.add(s.name.split(".")[0])

        # Functions
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            name = node.name
            args = [a.arg for a in node.args.args]
            decorators = [_decorator_name(d) for d in node.decorator_list if _decorator_name(d)]
            returns = _unparse(node.returns) if node.returns else ""
            doc = ast.get_docstring(node) or ""
            functions.append({
                "name": name,
                "line": node.lineno,
                "end_line": getattr(node, "end_lineno", node.lineno),
                "args": args,
                "returns": returns,
                "decorators": decorators,
                "docstring": doc[:200] if doc else "",
                "private": name.startswith("_"),
                "async": isinstance(node, ast.AsyncFunctionDef),
            })

        # Classes
        if isinstance(node, ast.ClassDef):
            methods: list[dict[str, Any]] = []
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    m_args = [a.arg for a in item.args.args]
                    methods.append({
                        "name": item.name,
                        "line": item.lineno,
                        "args": m_args,
                        "private": item.name.startswith("_"),
                    })
            bases = [_unparse(b) for b in node.bases if _unparse(b)]
            classes.append({
                "name": node.name,
                "line": node.lineno,
                "end_line": getattr(node, "end_lineno", node.lineno),
                "bases": bases,
                "methods": methods,
                "docstring": (ast.get_docstring(node) or "")[:200],
                "private": node.name.startswith("_"),
            })

        # Variables (module-level)
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    name = target.id
                    is_constant = name.isupper() and len(name) > 1
                    entry = {
                        "name": name,
                        "line": node.lineno,
                        "private": name.startswith("_"),
                        "constant": is_constant,
                    }
                    if name == "__all__":
                        _extract_all(node, all_list)
                    elif is_constant:
                        constants.append(entry)
                    else:
                        variables.append(entry)
        if isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name):
                name = node.target.id
                is_constant = name.isupper() and len(name) > 1
                entry = {
                    "name": name,
                    "line": node.lineno,
                    "private": name.startswith("_"),
                    "constant": is_constant,
                }
                if is_constant:
                    constants.append(entry)
                else:
                    variables.append(entry)

    imported_symbols: set[str] = set()
    for imp in imports:
        if imp["type"] == "import_from" and imp.get("symbols"):
            for s in imp["symbols"]:
                imported_symbols.add(s)

    return {
        "imports": imports,
        "functions": functions,
        "classes": classes,
        "variables": variables,
        "constants": constants,
        "all_list": all_list,
        "imported_symbols": sorted(imported_symbols),
        "symbol_count": len(functions) + len(classes) + len(variables),
        "total_lines": len(lines),
    }


def _extract_all(node: ast.Assign, all_list: list[str]) -> None:
    """Extract string literals from __all__ assignment."""
    if isinstance(node.value, (ast.List, ast.Tuple)):
        for elt in node.value.elts:
            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                all_list.append(elt.value)


def _decorator_name(d: ast.AST) -> str:
    """Return the decorator's name as a string, or empty."""
    if isinstance(d, ast.Name):
        return d.id
    if isinstance(d, ast.Attribute):
        return f"{_unparse(d.value)}.{d.attr}" if _unparse(d.value) else d.attr
    if isinstance(d, ast.Call):
        return _decorator_name(d.func)
    return ""


def _unparse(node: ast.AST | None) -> str:
    """Safely unparse an AST node to a string."""
    if node is None:
        return ""
    try:
        return ast.unparse(node)
    except Exception:
        return ""


def _find_cross_references(files: dict[str, Any]) -> dict[str, Any]:
    """Find cross-file dependencies and circular imports."""
    file_imports: dict[str, list[str]] = {}  # file -> list of imported module names
    import_map: dict[str, str] = {}  # module_name -> file where it's defined

    # Build map of which files define which modules
    for basename, info in files.items():
        mod_name = os.path.splitext(basename)[0]
        import_map[mod_name] = basename

    # Build per-file import lists and detect circulars
    circular_imports: list[list[str]] = []
    dependencies: list[dict[str, Any]] = []

    for basename, info in files.items():
        mod_name = os.path.splitext(basename)[0]
        imported_modules: set[str] = set()
        for imp in info.get("imports", []):
            if imp["type"] == "import":
                imported_modules.add(imp["module"].split(".")[0])
            elif imp["type"] == "import_from":
                imported_modules.add(imp["module"].split(".")[0])
        file_imports[basename] = sorted(imported_modules)

        for imported in imported_modules:
            if imported in import_map and import_map[imported] != basename:
                dependencies.append({
                    "from_file": basename,
                    "to_file": import_map[imported],
                    "module": imported,
                })

    # Simple circular detection: A imports B and B imports A
    for dep in dependencies:
        reverse = [d for d in dependencies
                   if d["from_file"] == dep["to_file"] and d["to_file"] == dep["from_file"]]
        if reverse:
            pair = sorted([dep["from_file"], dep["to_file"]])
            if pair not in circular_imports:
                circular_imports.append(pair)

    # Find missing typing.Any imports
    missing_any: list[dict[str, str]] = []
    for basename, info in files.items():
        has_any = any(
            imp.get("type") == "import_from" and imp["module"] == "typing"
            and "Any" in (imp.get("symbols") or [])
            for imp in info.get("imports", [])
        )
        if not has_any:
            # Check if Any is actually used
            _wd = os.environ.get('AGENT_WORKDIR') or os.getcwd()
            fp = os.path.join(_wd, basename) if not basename.startswith("/") else basename
            if os.path.exists(fp):
                try:
                    with open(fp, "r", encoding="utf-8") as f:
                        content = f.read()
                    if "Any" in content and "from typing import" not in content:
                        missing_any.append({"file": basename, "detail": "Any used but from typing import Any mangler"})
                except OSError:
                    pass

    return {
        "dependencies": dependencies,
        "circular_imports": circular_imports,
        "file_imports": file_imports,
        "missing_any": missing_any,
    }


def _detect_anomalies(files: dict[str, Any], cross_file: dict[str, Any]) -> list[dict[str, str]]:
    """Detect issues across the loaded files."""
    anomalies: list[dict[str, str]] = []

    # Stale __all__ entries: listed but not defined or imported in file
    for basename, info in files.items():
        defined = set()
        for func in info.get("functions", []):
            defined.add(func["name"])
        for cls in info.get("classes", []):
            defined.add(cls["name"])
        for var in info.get("variables", []):
            defined.add(var["name"])
        for const in info.get("constants", []):
            defined.add(const["name"])
        imported = set(info.get("imported_symbols", []))
        for entry in info.get("all_list", []):
            if entry not in defined and entry not in imported and not entry.startswith("_"):
                anomalies.append({
                    "type": "stale_all_entry",
                    "file": basename,
                    "detail": f"'{entry}' i __all__ men ikke defineret i filen",
                })

    # Missing typing.Any
    for entry in cross_file.get("missing_any", []):
        anomalies.append({
            "type": "missing_import",
            "file": entry["file"],
            "detail": entry["detail"],
        })

    # Circular imports
    for pair in cross_file.get("circular_imports", []):
        anomalies.append({
            "type": "circular_import",
            "file": " <-> ".join(pair),
            "detail": f"Cirkul\u00e6r import mellem {pair[0]} og {pair[1]}",
        })

    return anomalies


def format_entity_map_prompt(entity_map: dict[str, Any], max_anomalies: int = 5) -> str:
    """Format the entity map as a human-readable prompt section for the LLM."""
    parts: list[str] = ["## Entity Map"]

    files = entity_map.get("files", {})
    for basename, info in files.items():
        func_count = len(info.get("functions", []))
        cls_count = len(info.get("classes", []))
        var_count = len(info.get("variables", []))
        imp_count = len(info.get("imports", []))
        private = sum(1 for f in info.get("functions", []) if f.get("private"))
        parts.append(
            f"\n### {basename}  ({info.get('total_lines', '?')} linjer, "
            f"{func_count} funktioner, {cls_count} classes, "
            f"{var_count} variable, {imp_count} imports)"
        )
        if private:
            parts.append(f"  Private: {private} ({', '.join(f['name'] for f in info.get('functions', []) if f.get('private'))})")
        if info.get("all_list"):
            parts.append(f"  __all__ ({len(info['all_list'])}): {', '.join(info['all_list'][:8])}")

    cross = entity_map.get("cross_file", {})
    if cross.get("circular_imports"):
        parts.append(f"\n### Cirkul\u00e6re imports ({len(cross['circular_imports'])})")
        for pair in cross["circular_imports"]:
            parts.append(f"  - {pair[0]} <-> {pair[1]}")

    anomalies = entity_map.get("anomalies", [])
    if anomalies:
        parts.append(f"\n### Kendte problemer (viser {min(len(anomalies), max_anomalies)}/{len(anomalies)})")
        for a in anomalies[:max_anomalies]:
            parts.append(f"  - [{a['type']}] {a['file']}: {a['detail']}")

    return "\n".join(parts)
