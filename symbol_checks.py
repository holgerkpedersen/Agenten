import re
from typing import Any

from file_checks import _extract_modules_from_plan



_DEFAULT_DUNDER = re.compile(r"^__[A-Za-z0-9_]+__$")

import os


def _parse_plan_symbol_mapping(plan_content: str) -> dict[str, list[str]]:
    """Parse refactor plan into {module_filename: [symbol_names]}.

    Handles: ## Module:, ### N. filename.py, | **file.py** | table,
    ## Modul N: file.py (Danish), **Symboler (N):** inline lists, and
    label-grouped bullets (- Variabler: sym1, sym2).
    """
    mapping: dict[str, list[str]] = {}
    current_mod: str | None = None

    for line in plan_content.splitlines():
        # Reset on non-module headings
        heading_m = re.match(r'^#{2,6}\s+\S', line)
        if heading_m and '.py' not in line:
            current_mod = None

        # Format 1: ## Module: file_utils.py
        m = re.match(r'^##\s+Module:\s*(\S+\.\w+)', line)
        if m:
            current_mod = m.group(1)
            mapping.setdefault(current_mod, [])
            # Check for inline symbols on the same line
            sym_inline = re.search(r'\*{0,2}[Ss]ymbol(er|s)?\s*\(\d+\):\*{0,2}\s*(.+)', line)
            if sym_inline:
                for part in sym_inline.group(2).split(','):
                    part = part.strip()
                    sym_m = re.match(r'`?([a-zA-Z_]\w*)', part)
                    if sym_m:
                        name = sym_m.group(1)
                        if name and not name.startswith('__') and name not in mapping[current_mod]:
                            mapping[current_mod].append(name)
            continue

        # Format 1b: ## Modul N: filename.py (Danish heading with optional number)
        m = re.match(r'^#{2,6}\s+[Mm]odul[er]*\s*\d*:?\s*([\w./-]+\.\w+)', line)
        if m:
            current_mod = m.group(1)
            mapping.setdefault(current_mod, [])
            # Check for inline symbols on the same line
            sym_inline = re.search(r'\*{0,2}[Ss]ymbol(er|s)?\s*\(\d+\):\*{0,2}\s*(.+)', line)
            if sym_inline:
                for part in sym_inline.group(2).split(','):
                    part = part.strip()
                    sym_m = re.match(r'`?([a-zA-Z_]\w*)', part)
                    if sym_m:
                        name = sym_m.group(1)
                        if name and not name.startswith('__') and name not in mapping[current_mod]:
                            mapping[current_mod].append(name)
            continue

        # Format 1c: **Symboler (N):** sym1, sym2,... or **Symbols (N):** sym1, sym2,...
        # Inline symbol listing after a module heading
        m = re.match(r'^\*{1,2}[Ss]ymbol(er|s)?\s*\(\d+\):\*{0,2}\s*(.+)', line)
        if m and current_mod:
            syms_text = m.group(2)
            for part in syms_text.split(','):
                part = part.strip()
                sym_m = re.match(r'`?([a-zA-Z_]\w*)', part)
                if sym_m:
                    name = sym_m.group(1)
                    if name and not name.startswith('__') and name not in mapping[current_mod]:
                        mapping[current_mod].append(name)
            continue

        # Format 2: ### N. filename.py
        m = re.match(r'^#{2,6}\s+[\d\.\)]*\s*([\w./-]+\.\w+)', line)
        if m:
            current_mod = m.group(1)
            mapping.setdefault(current_mod, [])
            # Check for inline symbols on the same line
            sym_inline = re.search(r'\*{0,2}[Ss]ymbol(er|s)?\s*\(\d+\):\*{0,2}\s*(.+)', line)
            if sym_inline:
                for part in sym_inline.group(2).split(','):
                    part = part.strip()
                    sym_m = re.match(r'`?([a-zA-Z_]\w*)', part)
                    if sym_m:
                        name = sym_m.group(1)
                        if name and not name.startswith('__') and name not in mapping[current_mod]:
                            mapping[current_mod].append(name)
            continue

        # Format 3: markdown table
        m = re.match(r'^\|\s*\*{1,2}([\w./-]+\.\w+)\*{1,2}\s*\|(.+)', line)
        if m:
            mod = m.group(1)
            mapping.setdefault(mod, [])
            syms = re.findall(r'`([a-zA-Z_]\w*)`', m.group(2))
            for s in syms:
                if s not in mapping[mod]:
                    mapping[mod].append(s)
            current_mod = None
            continue

        if not line.strip():
            continue

        # Bullet items under module heading
        stripped = line.strip()
        prefix = '- ' if stripped.startswith('- ') else ('* ' if stripped.startswith('* ') and not stripped.startswith('**') else None)
        if current_mod and prefix:
            text = stripped[len(prefix):].strip()
            text_clean = re.sub(r'\([^)]*\)', '', text)
            label_m = re.match(r'^[a-zA-Z_]\w*:\s*', text_clean)
            if label_m:
                text_clean = text_clean[label_m.end():]
            for part in text_clean.split(','):
                part = part.strip()
                sym_m = re.match(r'`?([a-zA-Z_]\w*)', part)
                if sym_m:
                    name = sym_m.group(1)
                    if name and not name.startswith('__') and name not in mapping[current_mod]:
                        mapping[current_mod].append(name)

    return {k: v for k, v in mapping.items() if v}



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
    base = base_dir or os.environ.get('AGENT_WORKDIR') or os.getcwd()
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


def check_plan_symbols_per_module(spec: dict[str, Any], base_dir: str | None = None) -> tuple[bool, str]:
    """Return ``(passed, message)`` for a plan_symbols_per_module check.

    Parses the refactor plan to get the planned symbols for each module,
    then verifies each module file exists AND contains **all** of its
    planned symbols. A symbol is considered present if its name appears
    among the module's top-level definitions (AST-parsed).

    This is the strictest Ekstraher check: it guarantees correct
    placement, not just that symbols exist somewhere.

    Spec keys:
      - ``plan_path`` (default ``refactor_plan.md``) — path to plan
      - ``ext`` (default ``.py``) — module file extension
      - ``exclude_patterns`` (default dunder regex) — symbol names to ignore
    """
    base = base_dir or os.environ.get('AGENT_WORKDIR') or os.getcwd()
    plan_rel = spec.get("plan_path", "refactor_plan.md")
    plan_path = plan_rel if os.path.isabs(plan_rel) else os.path.join(base, plan_rel)
    if not os.path.exists(plan_path):
        return False, f"plan_symbols_per_module: plan {plan_path} findes ikke"
    try:
        with open(plan_path, encoding="utf-8") as f:
            plan_content = f.read()
    except OSError as e:
        return False, f"plan_symbols_per_module: kunne ikke læse {plan_path}: {e}"
    planning = _parse_plan_symbol_mapping(plan_content)
    if not planning:
        return True, "plan_symbols_per_module: planen indeholder ingen symbol-mappings (springer over)"

    exclude_patterns = spec.get("exclude_patterns")
    if exclude_patterns is None:
        exclude_patterns = [r"^__[A-Za-z0-9_]+__$"]
    exclude_res = [re.compile(p) for p in exclude_patterns]

    missing_modules: list[str] = []
    missing_symbols_by_module: dict[str, list[str]] = {}

    for mod, planned_names in planning.items():
        mod_path = mod if os.path.isabs(mod) else os.path.join(base, mod)
        if not os.path.exists(mod_path):
            missing_modules.append(mod)
            missing_symbols_by_module[mod] = list(planned_names)
            continue
        syms = _parse_module_symbols(mod_path)
        actual_names = {
            s.get("name", "") for s in syms
            if s.get("name", "") and not any(p.match(s.get("name", "")) for p in exclude_res)
        }
        missing = [n for n in planned_names if n not in actual_names]
        if missing:
            missing_symbols_by_module[mod] = missing

    if not missing_modules and not missing_symbols_by_module:
        sample = ", ".join(list(planning.keys())[:3])
        return True, (
            f"plan_symbols_per_module: alle {len(planning)} moduler har "
            f"sine planlagte symboler (f.eks. {sample})"
        )

    parts: list[str] = []
    if missing_modules:
        parts.append(f"modul(er) mangler: {', '.join(missing_modules)}")
    for mod, syms in sorted(missing_symbols_by_module.items()):
        if syms:
            parts.append(f"{mod} mangler: {', '.join(syms[:5])}"
                         f"{' (+%d flere)' % (len(syms) - 5) if len(syms) > 5 else ''}")
    return False, f"plan_symbols_per_module: {' | '.join(parts)}"


def check_plan_symbols_covered(spec: dict[str, Any], base_dir: str | None = None) -> tuple[bool, str]:
    """Return ``(passed, message)`` for a plan_symbols_covered check.

    Verifies that EVERY top-level symbol from the source file is mentioned
    in the refactor plan — i.e. the plan is comprehensive and no symbol
    was forgotten. Unlike ``symbols_covered`` (Ekstraher) which checks
    modules on disk, this checks the plan CONTENT itself.

    Spec keys:
      - ``source_file`` (required) — original .py file
      - ``plan_path`` (default ``refactor_plan.md``) — plan to validate
      - ``ext`` (default ``.py``) — module file extension
      - ``exclude_patterns`` (default dunder regex) — symbol names to skip
      - ``ignore_unlisted`` (default False) — if True, symbols not found in
        the plan are silently ignored rather than failing the check
    """
    source_rel = spec.get("source_file", "")
    if not source_rel:
        return False, "plan_symbols_covered: source_file mangler"
    base = base_dir or os.environ.get('AGENT_WORKDIR') or os.getcwd()
    source_path = source_rel if os.path.isabs(source_rel) else os.path.join(base, source_rel)
    if not os.path.exists(source_path):
        return False, f"plan_symbols_covered: kildefil {source_path} findes ikke"
    plan_rel = spec.get("plan_path", "refactor_plan.md")
    plan_path = plan_rel if os.path.isabs(plan_rel) else os.path.join(base, plan_rel)
    if not os.path.exists(plan_path):
        return False, f"plan_symbols_covered: plan {plan_path} findes ikke"
    try:
        with open(plan_path, encoding="utf-8") as f:
            plan_content = f.read()
    except OSError as e:
        return False, f"plan_symbols_covered: kunne ikke læse {plan_path}: {e}"

    exclude_patterns = spec.get("exclude_patterns")
    if exclude_patterns is None:
        exclude_patterns = [r"^__[A-Za-z0-9_]+__$"]
    exclude_res = [re.compile(p) for p in exclude_patterns]

    source_symbols = _parse_module_symbols(source_path)
    source_names: set[str] = {
        s.get("name", "") for s in source_symbols
        if s.get("name", "") and not any(p.match(s.get("name", "")) for p in exclude_res)
    }
    if not source_names:
        return True, "plan_symbols_covered: ingen symbols at spore i kildefilen"

    planning = _parse_plan_symbol_mapping(plan_content)
    # Flatten all planned symbols from all modules into one set
    plan_symbols: set[str] = set()
    for syms in planning.values():
        plan_symbols.update(syms)

    if not plan_symbols:
        ignore_unlisted = bool(spec.get("ignore_unlisted", False))
        if ignore_unlisted:
            return True, "plan_symbols_covered: plan indeholder ingen symbol-mappings (ignoreret)"
        return False, "plan_symbols_covered: planen indeholder ingen symbol-mappings"

    missing = sorted(source_names - plan_symbols)
    if not missing:
        return True, (
            f"plan_symbols_covered: alle {len(source_names)} kildesymboler "
            f"er nævnt i planen ({len(plan_symbols)} planlagte)"
        )

    sample = ", ".join(missing[:8])
    more = f" (+{len(missing) - 8} flere)" if len(missing) > 8 else ""
    return False, (
        f"plan_symbols_covered: {len(missing)} af {len(source_names)} kildesymboler "
        f"mangler i planen: {sample}{more}"
    )


# Phase name aliases for multi-language support.
# Maps canonical (Danish) phase key → list of alias phase names in other languages.
# Used by both backend check_phase_done() and the frontend /api/phase-checks endpoint.
PHASE_ALIASES: dict[str, list[str]] = {
    "analyse": ["analysis", "análisis", "分析"],
    "ekstraher": ["extract", "extraer", "提取"],
    "opdatér": ["update", "actualizar", "更新"],
    "test": ["probar", "测试"],
    "test (red)": ["prueba (red)", "测试 (red)"],
    "implementering": ["implementation", "implementación", "实施"],
    "verifikation (green)": ["verification (green)", "verificación (green)", "验证 (green)"],
    "opdatering": ["update", "actualización", "更新"],
    "læs": ["read"],
    "afklar": ["clarify"],
    "luk": ["close"],
}

from symbol_checks import check_symbols_covered_by_modules, PHASE_ALIASES



def _resolve_phase_key(phase_name: str, template_checks: dict[str, dict[str, Any]]) -> str | None:
    """Find the canonical key in template_checks matching *phase_name*.

    Checks direct (case-insensitive) match first, then alias lookups.
    Returns the actual key from *template_checks* or None.
    """
    lowered = phase_name.lower()
    for key in template_checks:
        if key.lower() == lowered:
            return key
    # Alias lookup: find the canonical alias key, then find the matching
    # template_checks entry (case-insensitive).
    for alias_key, aliases in PHASE_ALIASES.items():
        if lowered == alias_key or lowered in aliases:
            for key in template_checks:
                if key.lower() == alias_key:
                    return key
    return None

from symbol_checks import check_symbols_covered_by_modules, PHASE_ALIASES, _resolve_phase_key
from file_checks import check_file_exists
from file_checks import check_files_from_plan
from text_tool_checks import check_text_contains
from text_tool_checks import check_min_text_length
from text_tool_checks import check_tool_called
from text_tool_checks import check_code_contains



def check_all_of(
    spec: dict[str, Any],
    agent: Any | None = None,
    task_node: Any | None = None,
    called_tools: dict | None = None,
    base_dir: str | None = None,
    tool_name: str = "",
    full_response: str = "",
) -> tuple[bool, str]:
    """Return ``(passed, message)`` for an all_of compound check.

    Runs each sub-spec in ``spec["checks"]`` (list) and passes only if all
    pass. Useful when a phase needs to satisfy more than one criterion
    (e.g. refactor Ekstraher needs both ``files_from_plan`` AND
    ``symbols_covered``).

    Spec keys:
      - ``checks`` (required) — list of sub-specs. Each must have a
        ``"type"`` key matching one of the supported check types.
      - ``fail_fast`` (default True) — stop at the first failing sub-check
        (the message reports the failing type).
    """
    sub_specs = spec.get("checks", []) or []
    if not sub_specs:
        return False, "all_of: ingen sub-checks"
    fail_fast = bool(spec.get("fail_fast", True))
    passed_types: list[str] = []
    for sub in sub_specs:
        if not isinstance(sub, dict):
            continue
        sub_type = sub.get("type")
        if sub_type == "file_exists":
            r = check_file_exists(sub.get("paths", []), sub, base_dir=base_dir)
        elif sub_type == "files_from_plan":
            r = check_files_from_plan(sub, base_dir=base_dir)
        elif sub_type == "min_text_length":
            r = check_min_text_length(sub, full_response=full_response, agent=agent)
        elif sub_type == "code_contains":
            r = check_code_contains(sub, base_dir=base_dir)
        elif sub_type == "tool_called":
            r = check_tool_called(sub, tool_name=tool_name, called_tools=called_tools)
        elif sub_type == "tests_pass":
            from phase_engine import check_tests_pass as _check_tp
            r = _check_tp(sub, agent=agent, tool_name=tool_name, called_tools=called_tools)
        elif sub_type == "symbols_covered":
            r = check_symbols_covered_by_modules(sub, base_dir=base_dir)
        elif sub_type == "plan_symbols_per_module":
            r = check_plan_symbols_per_module(sub, base_dir=base_dir)
        elif sub_type == "plan_symbols_covered":
            r = check_plan_symbols_covered(sub, base_dir=base_dir)
        elif sub_type == "text_contains":
            r = check_text_contains(sub, full_response=full_response)
        else:
            r = (False, f"unknown sub-check: {sub_type}")
        if r[0]:
            passed_types.append(sub_type)
            continue
        if fail_fast:
            return False, f"all_of: {sub_type} fejlede — {r[1]}"
        return False, f"all_of: {sub_type} fejlede — {r[1]}"
    return True, f"all_of: alle {len(passed_types)} sub-checks bestod ({', '.join(passed_types)})"
