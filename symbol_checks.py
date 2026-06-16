import re
from typing import Any

from file_checks import _extract_modules_from_plan



_DEFAULT_DUNDER = re.compile(r"^__[A-Za-z0-9_]+__$")

import os



def _parse_module_symbols(path: str) -> list[dict[str, Any]]:
    """Parse a .py file and return its top-level symbols.

    Wraps :func:`agent_files.list_symbols` (which uses ``ast.parse``) and
    returns a list of ``{name, type, line, ...}`` dicts. Falls back to an
    empty list when the file is missing or unparseable so that callers see
    "symbol missing in this module" rather than an error.
    """
    try:
        from agent_files import list_symbols as _list_symbols
    except ImportError:
        return []
    if not os.path.exists(path):
        return []
    try:
        result = _list_symbols(path)
    except Exception:
        return []
    if not isinstance(result, dict) or not result.get("success"):
        return []
    return result.get("symbols", []) or []

from symbol_checks import _parse_module_symbols



def check_symbols_covered_by_modules(spec: dict[str, Any], base_dir: str | None = None) -> tuple[bool, str]:
    """Return ``(passed, message)`` for a symbols_covered check.

    The check guarantees that **every** top-level symbol from a source file
    (typically ``api_server.py``) is defined in **exactly one** of the new
    modules listed in a refactor plan. It fails when:

      * any source symbol is in 0 modules (forgotten)
      * any source symbol is in 2+ modules (duplicated)

    Symbols matching ``exclude_patterns`` (regex list) are skipped, with
    Python dunder names (``__name__``, ``__all__`` etc.) excluded by
    default. This lets callers keep trivial entry points in the source
    file without breaking the gate.

    Spec keys:
      - ``source_file`` (required) — original .py file (e.g. ``api_server.py``)
      - ``plan_path`` (default ``refactor_plan.md``) — plan to read modules from
      - ``ext`` (default ``.py``) — module file extension
      - ``exclude_patterns`` (default dunder regex) — list of regex; symbols
        whose names match any of these are ignored.
      - ``require_all_modules`` (default True) — when True, the check only
        runs once every module listed in the plan exists on disk. If some
        modules are still missing, the check returns False with "waiting
        for modules" rather than reporting all source symbols as missing.
    """
    source_rel = spec.get("source_file", "")
    if not source_rel:
        return False, "symbols_covered: source_file mangler"
    base = base_dir or os.getcwd()
    source_path = source_rel if os.path.isabs(source_rel) else os.path.join(base, source_rel)
    if not os.path.exists(source_path):
        return False, f"symbols_covered: kildefil {source_path} findes ikke"
    plan_rel = spec.get("plan_path", "refactor_plan.md")
    plan_path = plan_rel if os.path.isabs(plan_rel) else os.path.join(base, plan_rel)
    ext = spec.get("ext", ".py")
    if not os.path.exists(plan_path):
        return False, f"symbols_covered: plan {plan_path} findes ikke"
    try:
        with open(plan_path, encoding="utf-8") as f:
            plan_content = f.read()
    except OSError as e:
        return False, f"symbols_covered: kunne ikke læse {plan_path}: {e}"
    modules = _extract_modules_from_plan(plan_content, ext=ext)
    if not modules:
        return False, f"symbols_covered: ingen moduler fundet i {plan_path}"
    exclude_patterns = spec.get("exclude_patterns")
    if exclude_patterns is None:
        exclude_patterns = [r"^__[A-Za-z0-9_]+__$"]
    exclude_res = [re.compile(p) for p in exclude_patterns]

    source_symbols = _parse_module_symbols(source_path)
    if not source_symbols:
        try:
            import ast as _ast
            with open(source_path, encoding="utf-8") as f:
                _ast.parse(f.read())
        except (SyntaxError, ValueError) as e:
            return False, (
                f"symbols_covered: syntaksfejl i {source_path}: {e}"
            )
        except OSError as e:
            return False, f"symbols_covered: kunne ikke læse {source_path}: {e}"
        return True, (
            f"symbols_covered: ingen top-level symboler i {source_rel} "
            f"— intet at spore (tom eller trivial fil)"
        )
    names_to_track: list[tuple[str, str]] = []
    for s in source_symbols:
        name = s.get("name", "")
        if not name:
            continue
        if any(p.match(name) for p in exclude_res):
            continue
        names_to_track.append((name, s.get("type", "?")))
    if not names_to_track:
        return True, f"symbols_covered: ingen symbols at spore (alle ekskluderet) i {source_rel}"

    require_all_modules = bool(spec.get("require_all_modules", True))
    if require_all_modules:
        missing_modules = []
        for m in modules:
            mp = m if os.path.isabs(m) else os.path.join(base, m)
            if not os.path.exists(mp):
                missing_modules.append(m)
        if missing_modules:
            return False, (
                f"symbols_covered: venter på moduler: {', '.join(missing_modules)}"
            )

    placement: dict[str, list[str]] = {n: [] for n, _ in names_to_track}
    module_symbol_cache: dict[str, set[str]] = {}
    for m in modules:
        mp = m if os.path.isabs(m) else os.path.join(base, m)
        if not os.path.exists(mp):
            continue
        if m not in module_symbol_cache:
            syms = _parse_module_symbols(mp)
            module_symbol_cache[m] = {s.get("name", "") for s in syms}
        mod_names = module_symbol_cache[m]
        for n, _ in names_to_track:
            if n in mod_names:
                placement[n].append(m)

    missing = sorted(n for n, mods in placement.items() if len(mods) == 0)
    duplicated = sorted(n for n, mods in placement.items() if len(mods) > 1)
    if not missing and not duplicated:
        sample = ", ".join(f"{n}" for n, _ in names_to_track[:3])
        return True, (
            f"symbols_covered: alle {len(names_to_track)} symboler fra {source_rel} "
            f"er landet i præcis ét modul (f.eks. {sample})"
        )
    parts = []
    if missing:
        sample = ", ".join(missing[:5])
        more = f" (+{len(missing) - 5} flere)" if len(missing) > 5 else ""
        parts.append(f"mangler i alle moduler: {sample}{more}")
    if duplicated:
        dup_detail = "; ".join(f"{n} i {','.join(placement[n])}" for n in duplicated[:3])
        more = f" (+{len(duplicated) - 3} flere)" if len(duplicated) > 3 else ""
        parts.append(f"duplikeret på tværs: {dup_detail}{more}")
    return False, f"symbols_covered: {' | '.join(parts)}"
