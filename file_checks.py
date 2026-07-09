import os
from typing import Any



def check_file_exists(paths: list[str], spec: dict[str, Any] | None = None, base_dir: str | None = None) -> tuple[bool, str]:
    """Return ``(passed, message)`` for a file_exists check.

    Args:
        paths: List of file paths to check. All must exist for the check to pass.
        spec: Optional spec dict. Recognised keys:
              - ``require_all`` (default True) — if False, only one path must exist
        base_dir: Optional base directory to resolve relative paths against.
    """
    if not paths:
        return False, "file_exists: no paths provided"
    require_all = True
    if spec is not None:
        require_all = bool(spec.get("require_all", True))
    base = base_dir or os.environ.get('AGENT_WORKDIR') or os.getcwd()
    def _resolve(p: str) -> str:
        return p if os.path.isabs(p) else os.path.join(base, p)
    resolved = [_resolve(p) for p in paths]
    missing = [paths[i] for i, p in enumerate(resolved) if not os.path.exists(p)]
    if require_all:
        if not missing:
            return True, f"file_exists: alle filer findes: {', '.join(paths)}"
        return False, f"file_exists: mangler {', '.join(missing)}"
    found = [paths[i] for i, p in enumerate(resolved) if os.path.exists(p)]
    if found:
        return True, f"file_exists: mindst én fil findes: {', '.join(found)}"
    return False, f"file_exists: ingen af {', '.join(paths)} findes"



def _parse_refactor_plan_modules(plan_path: str) -> list[str]:
    """Parse alle *.py moduler nævnt i en refactor_plan.md fil.

    Bruges af _refactor_actually_moved_code til at verificere at ALLE moduler
    fra planen er oprettet under en refactor-session.

    Args:
        plan_path: Sti til refactor_plan.md (relativ eller absolut).

    Returns:
        Sorteret liste af modulnavne (f.eks. ['routes.py', 'security.py']).
        Returnerer [] hvis filen ikke findes eller er tom.
    """
    if not plan_path or not os.path.exists(plan_path):
        return []
    try:
        with open(plan_path, encoding="utf-8") as f:
            content = f.read()
    except (OSError, UnicodeDecodeError):
        return []
    return _extract_modules_from_plan(content, ext=".py")



def _has_real_code(filepath: str, min_lines: int = 20) -> bool:
    """Tjek om en Python-fil indeholder reel kode (def/class med >= min_lines).

    Skelner mellem reel flyttet kode og tomme import-stub. Bruges af
    _refactor_actually_moved_code til at afgøre om en refactor reelt er sket.

    Args:
        filepath: Sti til den fil der skal tjekkes.
        min_lines: Minimum antal linjer for at tælle som reel kode.

    Returns:
        True hvis filen eksisterer OG har def/class OG >= min_lines linjer.
    """
    if not filepath or not os.path.exists(filepath):
        return False
    try:
        with open(filepath, encoding="utf-8") as f:
            content = f.read()
    except (OSError, UnicodeDecodeError):
        return False
    has_definition = "def " in content or "class " in content
    if not has_definition:
        return False
    return content.count("\n") >= min_lines

import re



def _extract_modules_from_plan(plan_content: str, ext: str = ".py", allow_nested: bool = False, source_file: str = "") -> list[str]:
    """Extract module filenames from a refactor plan markdown.

    The plan format from the LLM typically looks like:
        ### 1. routes.py
        **Ansvar:** Endpoint definitioner
        ### 2. session_manager.py
        **Ansvar:** Session CRUD

    We also pick up bare filenames like ``routes.py`` anywhere in the text
    (in case the LLM didn't use a heading).

    Args:
        plan_content: The markdown content of the plan file.
        ext: File extension to look for (default ".py").
        allow_nested: If True, keep paths with ``/`` or ``\\`` (e.g.
            ``gui/browser_window.py``). Used by greenfield projects where
            modules are organized in subdirectories.
    """
    if not plan_content:
        return []
    seen: set[str] = set()
    _src_base = os.path.basename(source_file).lower() if source_file else ""
    ext_pattern = re.escape(ext)
    heading_pat = re.compile(
        rf"^\s*#{1,6}\s+[\d\.\)]*\s*(\S+{ext_pattern})\b",
        re.MULTILINE,
    )
    # Also match "## Module: file.py" or "## Module: `file.py`" (colon-separated heading)
    module_heading_pat = re.compile(
        rf"^\s*#{1,6}\s+[Mm]odul[er]*\s*\d*:?\s*(\S+{ext_pattern})\b",
        re.MULTILINE,
    )
    inline_pat = re.compile(rf"(?<![a-zA-Z])`?([\w./-]+{ext_pattern})`?(?![a-zA-Z])")
    for pat in (heading_pat, module_heading_pat, inline_pat):
        for m in pat.finditer(plan_content):
            name = m.group(1).strip().strip('`')
            if not name:
                continue
            if not allow_nested and ("/" in name or "\\" in name):
                continue
            seen.add(name)
    return sorted(seen)



def check_files_from_plan(spec: dict[str, Any], base_dir: str | None = None) -> tuple[bool, str]:
    source_file = spec.get("source_file", "")
    """Return ``(passed, message)`` for a files_from_plan check.

    Spec keys:
      - ``plan_path`` (required) — path to the plan file (relative to base_dir)
      - ``ext`` (default ".py") — file extension to look for
      - ``min_files`` (default 1) — minimum number of files that must be listed
      - ``allow_nested`` (default False) — if True, keep paths with ``/`` or
        ``\\`` (e.g. ``gui/browser_window.py``). Used by greenfield projects.
      - ``base_dir`` (optional) — override the cwd for both the plan and the
        module files. If not provided, uses ``os.getcwd()``.
    """
    base = base_dir or os.environ.get('AGENT_WORKDIR') or os.getcwd()
    plan_rel = spec.get("plan_path", "refactor_plan.md")
    plan_path = os.path.join(base, plan_rel) if not os.path.isabs(plan_rel) else plan_rel
    ext = spec.get("ext", ".py")
    min_files = int(spec.get("min_files", 1))
    allow_nested = bool(spec.get("allow_nested", False))
    if not os.path.exists(plan_path):
        return False, f"files_from_plan: planfil {plan_path} findes ikke"
    try:
        with open(plan_path, encoding="utf-8") as f:
            plan_content = f.read()
    except OSError as e:
        return False, f"files_from_plan: kunne ikke læse {plan_path}: {e}"
    modules = _extract_modules_from_plan(plan_content, ext=ext, allow_nested=allow_nested, source_file=source_file)
    if len(modules) < min_files:
        return False, (
            f"files_from_plan: fandt kun {len(modules)} modulnavne i {plan_path} "
            f"(kræver mindst {min_files})"
        )
    missing = []
    for m in modules:
        full = os.path.join(base, m) if not os.path.isabs(m) else m
        if not os.path.exists(full):
            missing.append(m)
    if not missing:
        return True, (
            f"files_from_plan: alle {len(modules)} moduler fra {plan_path} "
            f"findes: {', '.join(modules)}"
        )
    return False, (
        f"files_from_plan: mangler {len(missing)} af {len(modules)} moduler: "
        f"{', '.join(missing)}"
    )


def fix_cross_module_imports(agent):
    import ast as _ast

    plan_path = getattr(agent, '_refactor_plan_path', '') or os.path.join(
        os.environ.get('AGENT_WORKDIR') or os.getcwd(), 'refactor_plan.md')
    if not os.path.exists(plan_path):
        return []

    base = os.path.dirname(plan_path) or os.getcwd()
    modules = _parse_refactor_plan_modules(plan_path)
    if not modules:
        return []

    mod_files = {m: p for m in modules if m.endswith('.py')
                 and os.path.exists(p := os.path.join(base, m))}
    if not mod_files:
        return []

    mod_info = {}
    for name, path in mod_files.items():
        try:
            with open(path, encoding='utf-8') as f:
                content = f.read()
            tree = _ast.parse(content)
        except Exception:
            continue
        defs, imports, names, calls = set(), set(), set(), {}
        for node in _ast.walk(tree):
            if isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef, _ast.ClassDef)):
                defs.add(node.name)
            elif isinstance(node, _ast.Import):
                imports.update(a.asname or a.name for a in node.names)
            elif isinstance(node, _ast.ImportFrom):
                imports.update(a.asname or a.name for a in node.names if a.name)
            elif isinstance(node, _ast.Name) and isinstance(node.ctx, _ast.Load):
                names.add(node.id)
        for node in _ast.walk(tree):
            if isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
                fc = set()
                for sub in _ast.walk(node):
                    if isinstance(sub, _ast.Call) and isinstance(sub.func, _ast.Name):
                        fc.add(sub.func.id)
                calls[node.name] = fc
        mod_info[name] = dict(defs=defs, imports=imports, names=names - defs - imports,
                              calls=calls, tree=tree, path=path, content=content)

    def _scan_defs(filepath):
        """Scan a .py file for top-level function/class/constant definitions."""
        import os as _os
        full = _os.path.join(_os.path.dirname(__file__), filepath)
        defs = {}
        try:
            with open(full, encoding='utf-8') as f:
                tree = _ast.parse(f.read())
            for node in _ast.walk(tree):
                if isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
                    defs[node.name] = node.lineno
            for node in _ast.walk(tree):
                if isinstance(node, _ast.Assign):
                    for target in node.targets:
                        if isinstance(target, _ast.Name) and target.id.isupper():
                            defs[target.id] = node.lineno
        except Exception:
            pass
        return defs, filepath

    at_defs = {}
    at_sources = {'agent_tasks.py': {}, 'agent_utils.py': {}}
    for src in at_sources:
        defs, fname = _scan_defs(src)
        at_sources[src] = defs
        at_defs.update(defs)

    from config import get_logger
    _log = get_logger(__name__)
    fixes = []

    for name, info in mod_info.items():
        unresolved = info['names'].copy()
        for other, oi in mod_info.items():
            if other == name or other == 'agent_tasks.py':
                continue
            missing = unresolved & oi['defs']
            if not missing:
                continue
            other_stem = other.replace('.py', '')
            imp = f"from {other_stem} import {', '.join(sorted(missing))}"
            with open(info['path'], encoding='utf-8') as f:
                lines = f.read().split('\n')
            last_idx = -1
            for i, l in enumerate(lines):
                stripped = l.strip()
                if stripped.startswith(('import ', 'from ')):
                    last_idx = i
                elif stripped.startswith(('def ', 'class ', '@')):
                    break
            lines.insert(last_idx + 1, imp)
            with open(info['path'], 'w', encoding='utf-8') as f:
                f.write('\n'.join(lines))
            fixes.append(f"{name}: {imp}")

    source_module_for_sym = {}
    for src, defs in at_sources.items():
        for sym in defs:
            source_module_for_sym[sym] = src.replace('.py', '')

    for name, info in mod_info.items():
        for func, fcalls in info['calls'].items():
            needed = fcalls & at_defs.keys() - info['defs'] - info['imports']
            if not needed:
                continue
            func_node = next((n for n in _ast.walk(info['tree'])
                              if isinstance(n, (_ast.FunctionDef, _ast.AsyncFunctionDef))
                              and n.name == func), None)
            if not func_node:
                continue
            body_line = func_node.lineno
            for stmt in func_node.body:
                if not isinstance(stmt, _ast.Expr):
                    body_line = stmt.lineno
                    break
            by_src = {}
            for sym in needed:
                src = source_module_for_sym.get(sym, 'agent_tasks')
                by_src.setdefault(src, []).append(sym)
            with open(info['path'], encoding='utf-8') as f:
                lines = f.read().split('\n')
            for src, syms in sorted(by_src.items()):
                imp = f"    from {src} import {', '.join(sorted(syms))}"
                if any(imp.strip() == l.strip() for l in lines):
                    continue
                lines.insert(body_line - 1, imp)
                fixes.append(f"{name}.{func}(): lazy {imp.strip()}")
            with open(info['path'], 'w', encoding='utf-8') as f:
                f.write('\n'.join(lines))

    if fixes:
        _log.info("Auto-fixed cross-module imports: %s", '; '.join(fixes))
    return fixes
