"""Deterministic phase-completion checks for template-driven workflows.

Each phase in a template can declare a success criterion that is checked
after every tool call. When the criterion is met, the phase is auto-completed
and the agent advances to the next sibling phase — the LLM does not get to
make additional (often redundant) tool calls.

Supported check types:

  - ``file_exists`` — one or more files must exist on disk
  - ``files_from_plan`` — parse a markdown plan file, extract module names
    (anything matching ``*.py`` or another extension), and require all of
    them to exist as actual files
  - ``min_text_length`` — accumulated LLM text output must meet a char
    minimum (avoids ending Analyse on a one-line "looks fine" reply)
  - ``code_contains`` — a source file must contain at least N of the given
    regex patterns (used to confirm refactor Opdatér added the import lines
    to the consumer file)
  - ``tool_called`` — the LLM must have invoked one of the listed tools
    (e.g. ``update_issue_status`` in the bugfix Opdatering phase)
  - ``tests_pass`` — the LLM must have called ``run_tests`` and the last
    run must have succeeded. This is the only way the Test phase auto-
    completes — declaring ``<<<DONE>>>`` without running tests does not
    pass.

All checks are read-only and side-effect free. If a check raises or the
required signal is missing, the check returns ``False`` and the LLM
continues to drive the phase (existing behaviour).
"""

from __future__ import annotations

import os
import re
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
    base = base_dir or os.getcwd()
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


def _extract_modules_from_plan(plan_content: str, ext: str = ".py") -> list[str]:
    """Extract module filenames from a refactor plan markdown.

    The plan format from the LLM typically looks like:
        ### 1. routes.py
        **Ansvar:** Endpoint definitioner
        ### 2. session_manager.py
        **Ansvar:** Session CRUD

    We also pick up bare filenames like ``routes.py`` anywhere in the text
    (in case the LLM didn't use a heading).
    """
    if not plan_content:
        return []
    seen: set[str] = set()
    ext_pattern = re.escape(ext)
    heading_pat = re.compile(
        rf"^\s*#{1,6}\s+[\d\.\)]*\s*([\w./-]+{ext_pattern})\b",
        re.MULTILINE,
    )
    inline_pat = re.compile(rf"\b([\w./-]+{ext_pattern})\b")
    for pat in (heading_pat, inline_pat):
        for m in pat.finditer(plan_content):
            name = m.group(1).strip()
            if not name or "/" in name or "\\" in name:
                continue
            seen.add(name)
    return sorted(seen)


def check_files_from_plan(spec: dict[str, Any], base_dir: str | None = None) -> tuple[bool, str]:
    """Return ``(passed, message)`` for a files_from_plan check.

    Spec keys:
      - ``plan_path`` (required) — path to the plan file (relative to base_dir)
      - ``ext`` (default ".py") — file extension to look for
      - ``min_files`` (default 1) — minimum number of files that must be listed
      - ``base_dir`` (optional) — override the cwd for both the plan and the
        module files. If not provided, uses ``os.getcwd()``.
    """
    base = base_dir or os.getcwd()
    plan_rel = spec.get("plan_path", "refactor_plan.md")
    plan_path = os.path.join(base, plan_rel) if not os.path.isabs(plan_rel) else plan_rel
    ext = spec.get("ext", ".py")
    min_files = int(spec.get("min_files", 1))
    if not os.path.exists(plan_path):
        return False, f"files_from_plan: planfil {plan_path} findes ikke"
    try:
        with open(plan_path, encoding="utf-8") as f:
            plan_content = f.read()
    except OSError as e:
        return False, f"files_from_plan: kunne ikke læse {plan_path}: {e}"
    modules = _extract_modules_from_plan(plan_content, ext=ext)
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


def check_text_contains(spec: dict[str, Any], full_response: str = "") -> tuple[bool, str]:
    """Check that the LLM's output mentions certain keywords.

    Spec keys:
      - ``keywords`` — list of required keywords/substrings (case-insensitive)
      - ``min_match`` — minimum number of keywords that must be present (default: all)
    """
    keywords = spec.get("keywords", [])
    if not keywords:
        return False, "text_contains: ingen keywords specificeret"
    lower = full_response.lower()
    found = [kw for kw in keywords if kw.lower() in lower]
    min_match = spec.get("min_match", len(keywords))
    if len(found) >= min_match:
        return True, f"text_contains: {len(found)}/{len(keywords)} keywords fundet"
    return False, f"text_contains: kun {len(found)}/{len(keywords)} keywords fundet — mangler: {', '.join(k for k in keywords if k.lower() not in lower)}"


def check_min_text_length(spec: dict[str, Any], full_response: str = "", agent: Any | None = None) -> tuple[bool, str]:
    """Return ``(passed, message)`` for a min_text_length check.

    The check is satisfied once the LLM has produced at least ``min_chars``
    characters of accumulated output. Used by Analyse phases to make sure
    the LLM cannot terminate with a one-liner.

    Spec keys:
      - ``min_chars`` (required) — minimum accumulated character count.
      - ``include_messages`` (default True) — also count text inside
        ``agent.messages`` (assistant turns). Set to False if you only
        want to count the streaming ``full_response`` text.
    """
    try:
        min_chars = int(spec.get("min_chars", 100))
    except (TypeError, ValueError):
        return False, "min_text_length: invalid min_chars"
    if min_chars <= 0:
        return True, f"min_text_length: min_chars={min_chars}, ingen krav"
    text = full_response or ""
    include_msgs = bool(spec.get("include_messages", True))
    if include_msgs and agent is not None:
        messages = getattr(agent, "messages", None)
        if not messages:
            for attr in ("_messages", "conversation", "history"):
                messages = getattr(agent, attr, None)
                if messages:
                    break
        if messages:
            for m in messages:
                if not isinstance(m, dict):
                    continue
                if m.get("role") != "assistant":
                    continue
                content = m.get("content", "")
                if isinstance(content, str):
                    text += "\n" + content
                elif isinstance(content, list):
                    for part in content:
                        if isinstance(part, dict) and part.get("type") == "text":
                            text += "\n" + part.get("text", "")
    if len(text) >= min_chars:
        return True, f"min_text_length: {len(text)} tegn (>= {min_chars})"
    return False, f"min_text_length: kun {len(text)} tegn (kræver {min_chars})"


def check_tool_called(spec: dict[str, Any], tool_name: str = "", called_tools: dict | None = None) -> tuple[bool, str]:
    """Return ``(passed, message)`` for a tool_called check.

    The check passes as soon as the LLM invokes one of the required tools.
    "Just called" is enough — we do not require a successful result, since
    most phase check contexts are already running after a successful tool
    call (e.g. ``edit_file`` returning ``success: True``).

    Spec keys:
      - ``tools`` (required) — list of tool names. Pass if any of them has
        been called. If ``tool_name`` is one of them, we pass immediately
        without scanning ``called_tools``.
      - ``require_all`` (default False) — when True, all listed tools must
        appear in ``called_tools``. Requires a non-empty ``called_tools``.
      - ``min_count`` (default 1) — minimum total calls across all listed
        tools. Counts sum of values for matching tool keys.
    """
    required = spec.get("tools", []) or []
    if not required:
        return False, "tool_called: ingen tools specificeret"
    if tool_name and tool_name in required:
        return True, f"tool_called: {tool_name} blev kaldt"
    if not called_tools:
        return False, f"tool_called: {tool_name or '?'} ikke i {required}"
    require_all = bool(spec.get("require_all", False))
    min_count = int(spec.get("min_count", 1))
    seen = {k.split("{")[0] for k in called_tools}
    matched = [t for t in required if t in seen]
    count = sum(called_tools.get(k, 0) for k in called_tools if k.split("{")[0] in required)
    if require_all:
        if len(matched) == len(required) and count >= min_count:
            return True, f"tool_called: alle {required} kaldt ({count} gange)"
        missing = [t for t in required if t not in seen]
        return False, f"tool_called: mangler {missing} (kaldt: {matched}, count={count})"
    if matched and count >= min_count:
        return True, f"tool_called: {matched[0]} kaldt (count={count})"
    return False, f"tool_called: ingen af {required} kaldt endnu"


def check_code_contains(spec: dict[str, Any], base_dir: str | None = None) -> tuple[bool, str]:
    """Return ``(passed, message)`` for a code_contains check.

    The check reads ``spec["path"]`` (relative to ``base_dir``) and counts
    regex matches for each pattern in ``spec["patterns"]``. Used to confirm
    that refactor Opdatér actually added ``from routes import ...`` etc.
    to ``api_server.py``.

    Spec keys:
      - ``path`` (required) — file to scan.
      - ``patterns`` (required) — list of regex strings.
      - ``require_all`` (default False) — if True, every pattern must match
        at least once. If False, only ``min_matches`` patterns need to match.
      - ``min_matches`` (default 1) — minimum number of distinct patterns
        that must match (used with ``require_all=False``).
    """
    rel = spec.get("path", "")
    if not rel:
        return False, "code_contains: path mangler"
    base = base_dir or os.getcwd()
    full = rel if os.path.isabs(rel) else os.path.join(base, rel)
    patterns = spec.get("patterns", []) or []
    if not patterns:
        return False, "code_contains: patterns mangler"
    if not os.path.exists(full):
        return False, f"code_contains: fil {full} findes ikke"
    try:
        with open(full, encoding="utf-8") as f:
            content = f.read()
    except OSError as e:
        return False, f"code_contains: kunne ikke læse {full}: {e}"
    require_all = bool(spec.get("require_all", False))
    min_matches = int(spec.get("min_matches", 1))
    matched = []
    for pat in patterns:
        try:
            if re.search(pat, content):
                matched.append(pat)
        except re.error as e:
            return False, f"code_contains: ugyldigt regex {pat!r}: {e}"
    if require_all:
        if len(matched) == len(patterns):
            return True, f"code_contains: alle {len(patterns)} patterns matchet i {rel}"
        missing = [p for p in patterns if p not in matched]
        return False, f"code_contains: {len(matched)}/{len(patterns)} matchet, mangler {missing}"
    if len(matched) >= min_matches:
        return True, f"code_contains: {len(matched)} patterns matchet i {rel} (>= {min_matches})"
    return False, f"code_contains: kun {len(matched)}/{len(patterns)} matchet i {rel} (kræver {min_matches})"


def check_tests_pass(spec: dict[str, Any], agent: Any | None = None, tool_name: str = "", called_tools: dict | None = None) -> tuple[bool, str]:
    """Return ``(passed, message)`` for a tests_pass check.

    The check has three guards:

      1. ``require_run`` (default True) — fail unless the LLM actually
         called ``run_tests`` (we use ``tool_name`` for the latest call
         and fall back to scanning ``called_tools`` keys).
      2. ``agent._tests_failed`` must be ``False`` (or absent).
      3. ``agent._last_test_summary`` (if present) should not contain
         substrings like ``failed`` or ``error``.

    This is the only way a Test phase auto-completes — declaring
    ``<<<DONE>>>`` without invoking ``run_tests`` does not pass.

    Spec keys:
      - ``scope`` (default "all") — informational only; current
        implementation only supports the project-wide test suite.
      - ``require_run`` (default True) — gate on LLM having invoked
        ``run_tests``.
    """
    require_run = bool(spec.get("require_run", True))
    if require_run:
        ran = tool_name == "run_tests"
        if not ran and called_tools:
            ran = any(k.split("{")[0] == "run_tests" for k in called_tools)
        if not ran:
            return False, "tests_pass: LLM kaldte ikke run_tests i denne fase"
    if agent is not None and getattr(agent, "_tests_failed", False):
        return False, "tests_pass: seneste testkørsel fejlede"
    summary = ""
    if agent is not None:
        summary = getattr(agent, "_last_test_summary", "") or ""
    if summary and re.search(r"\b(failed|error)\b", summary, re.IGNORECASE):
        return False, f"tests_pass: test summary nævner fejl: {summary[:120]}"
    return True, "tests_pass: alle tests bestod"


_DEFAULT_DUNDER = re.compile(r"^__[A-Za-z0-9_]+__$")


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
            r = check_tests_pass(sub, agent=agent, tool_name=tool_name, called_tools=called_tools)
        elif sub_type == "symbols_covered":
            r = check_symbols_covered_by_modules(sub, base_dir=base_dir)
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


TEMPLATE_PHASE_CHECKS: dict[str, dict[str, dict[str, Any]]] = {
    "refactor": {
        "Analyse": {
            "type": "all_of",
            "description": "FORM\u00c5L: Forst\u00e5 den store fils struktur og ansvarsomr\u00e5der. Kr\u00e6ver: mindst 500 tegn analyse + 3 funktioner l\u00e6st med read_location.",
            "checks": [
                {"type": "min_text_length", "min_chars": 500},
                {"type": "tool_called", "tools": ["read_location"], "min_count": 3},
            ],
        },
        "Plan": {
            "type": "files_from_plan",
            "plan_path": "refactor_plan.md",
            "ext": ".py",
            "min_files": 5,
            "description": "FORM\u00c5L: Beslut modulopdeling og skriv plan. Kr\u00e6ver: refactor_plan.md med mindst 5 *.py-moduler.",
        },
        "Ekstraher": {
            "type": "all_of",
            "description": "FORM\u00c5L: Opret nye modulfiler med kode fra den originale fil. Kr\u00e6ver: alle planlagte *.py-moduler oprettet + alle symboler fordelt.",
            "checks": [
                {
                    "type": "files_from_plan",
                    "plan_path": "refactor_plan.md",
                    "ext": ".py",
                    "min_files": 1,
                },
                {
                    "type": "symbols_covered",
                    "source_file": "api_server.py",
                    "plan_path": "refactor_plan.md",
                    "ext": ".py",
                    "exclude_patterns": [r"^__[A-Za-z0-9_]+__$"],
                },
            ],
        },
        "Opdat\u00e9r": {
            "type": "code_contains",
            "path": "api_server.py",
            "patterns": [
                "from\\s+routes\\b",
                "from\\s+session_manager\\b",
                "from\\s+file_handler\\b",
                "from\\s+image_handler\\b",
                "from\\s+model_manager\\b",
                "from\\s+security\\b",
                "from\\s+helpers\\b",
            ],
            "require_all": False,
            "min_matches": 1,
            "description": "FORM\u00c5L: Fjern flyttet kode fra original fil, tilf\u00f8j imports til nye moduler. Kr\u00e6ver: api_server.py importerer fra mindst \u00e9t nyt modul.",
        },
        "Test": {
            "type": "tests_pass",
            "scope": "all",
            "description": "FORM\u00c5L: Bekr\u00e6ft at refactoring ikke har brudt noget. Kr\u00e6ver: alle tests best\u00e5r.",
        },
    },
    "bugfix": {
        "Analyse": {
            "type": "min_text_length",
            "min_chars": 300,
            "description": "FORM\u00c5L: Forst\u00e5 buggen og identific\u00e9r rod\u00e5rsag i koden. Kr\u00e6ver: mindst 300 tegn analyse.",
        },
        "Test (Red)": {
            "type": "file_exists",
            "paths": ["tests/temp/test_*.py"],
            "require_all": False,
            "min_files": 1,
            "description": "FORM\u00c5L: Skriv en pytest der reproducerer buggen. Kr\u00e6ver: test-fil i tests/temp/. Testen skal fejle (r\u00f8d fase).",
        },
        "Implementering": {
            "type": "tool_called",
            "tools": ["edit_file", "write_file"],
            "description": "FORM\u00c5L: Ret koden med minimal \u00e6ndring. Kr\u00e6ver: edit_file eller write_file kaldt.",
        },
        "Verifikation (Green)": {
            "type": "tests_pass",
            "scope": "all",
            "description": "FORM\u00c5L: Bekr\u00e6ft at fixet virker og ingen regressions. Kr\u00e6ver: alle tests best\u00e5r.",
        },
        "Opdatering": {
            "type": "tool_called",
            "tools": ["update_issue_status"],
            "description": "FORM\u00c5L: Luk issue med beskrivelse af hvad der blev rettet. Kr\u00e6ver: update_issue_status kaldt.",
        },
    },
    "issue_handler": {
        "L\u00e6s": {
            "type": "tool_called",
            "tools": ["read_issue"],
            "description": "FORM\u00c5L: L\u00e6s issue-beskrivelsen og forst\u00e5 problemet. Kr\u00e6ver: read_issue kaldt.",
        },
        "Afklar": {
            "type": "min_text_length",
            "min_chars": 200,
            "description": "FORM\u00c5L: Analys\u00e9r koden, afg\u00f8r om fejlen findes. Kr\u00e6ver: mindst 200 tegn analyse.",
        },
        "Fix": {
            "type": "tool_called",
            "tools": ["edit_file", "write_file"],
            "description": "FORM\u00c5L: Ret fejlen i koden. Kr\u00e6ver: edit_file eller write_file kaldt.",
        },
        "Luk Issue": {
            "type": "tool_called",
            "tools": ["update_issue_status"],
            "description": "FORM\u00c5L: Mark\u00e9r issue som resolved med rettelsesnote. Kr\u00e6ver: update_issue_status kaldt.",
        },
    },
    "testgenerering": {
        "Analyse": {
            "type": "min_text_length",
            "min_chars": 300,
            "description": "FORM\u00c5L: Forst\u00e5 hvilke funktioner der mangler testd\u00e6kning. Kr\u00e6ver: mindst 300 tegn analyse.",
        },
        "Test (Red)": {
            "type": "file_exists",
            "paths": ["tests/temp/test_*.py"],
            "require_all": False,
            "min_files": 1,
            "description": "FORM\u00c5L: Skriv pytest-tests for den manglende d\u00e6kning. Kr\u00e6ver: test-fil i tests/temp/.",
        },
        "Implementering": {
            "type": "tool_called",
            "tools": ["edit_file"],
            "optional": True,
            "description": "FORM\u00c5L: G\u00f8r koden testbar hvis n\u00f8dvendigt. Kr\u00e6ver: edit_file kaldt (kun hvis koden skal \u00e6ndres).",
        },
        "Verifikation (Green)": {
            "type": "tests_pass",
            "scope": "all",
            "description": "FORM\u00c5L: Bekr\u00e6ft at nye tests best\u00e5r og ingen regressions. Kr\u00e6ver: alle tests best\u00e5r.",
        },
    },
    "kodeanalyse": {
        "Form\u00e5l": {
            "type": "all_of",
            "description": "FORM\u00c5L: Forklar hvad filen g\u00f8r og dens rolle i projektet. Kr\u00e6ver: fil gemt i docs/formaal.md med analyse af form\u00e5l, ansvar, cohesion og single responsibility.",
            "checks": [
                {"type": "file_exists", "paths": ["docs/formaal.md"], "min_files": 1},
                {"type": "code_contains", "path": "docs/formaal.md", "patterns": [
                    "form\u00e5l", "ansvar", "rolle", "cohesion", "single responsibility"
                ], "min_matches": 3},
            ],
        },
        "Imports og afh\u00e6ngigheder": {
            "type": "all_of",
            "description": "FORM\u00c5L: Gennemg\u00e5 filens imports og eksterne afh\u00e6ngigheder. Kr\u00e6ver: fil gemt i docs/imports.md med gennemgang af imports, cirkul\u00e6re afh\u00e6ngigheder og ubrugte imports.",
            "checks": [
                {"type": "file_exists", "paths": ["docs/imports.md"], "min_files": 1},
                {"type": "code_contains", "path": "docs/imports.md", "patterns": [
                    "import", "afh\u00e6ngighed", "cirkul\u00e6r", "ubrugt", "ekstern"
                ], "min_matches": 3},
            ],
        },
        "Arkitektur": {
            "type": "all_of",
            "description": "FORM\u00c5L: Analys\u00e9r filens struktur, design patterns og dataflow. Kr\u00e6ver: fil gemt i docs/arkitektur.md med analyse af struktur, patterns, coupling og SOLID.",
            "checks": [
                {"type": "file_exists", "paths": ["docs/arkitektur.md"], "min_files": 1},
                {"type": "code_contains", "path": "docs/arkitektur.md", "patterns": [
                    "klasse", "funktion", "struktur", "design pattern", "kobling",
                    "cohesion", "SOLID", "single responsibility"
                ], "min_matches": 4},
            ],
        },
        "Kodekvalitet": {
            "type": "all_of",
            "description": "FORM\u00c5L: Vurder kodekvalitet (DRY, SOLID, PEP 8, complexity, naming, tests). Kr\u00e6ver: fil gemt i docs/kodekvalitet.md med kvalitetsvurdering.",
            "checks": [
                {"type": "file_exists", "paths": ["docs/kodekvalitet.md"], "min_files": 1},
                {"type": "code_contains", "path": "docs/kodekvalitet.md", "patterns": [
                    "DRY", "SOLID", "PEP 8", "navngivning", "complexity",
                    "type hint", "fejlh\u00e5ndtering", "test coverage"
                ], "min_matches": 4},
            ],
        },
        "Sikkerhed": {
            "type": "all_of",
            "description": "FORM\u00c5L: Identific\u00e9r s\u00e5rbarheder (OWASP top 10). Kr\u00e6ver: fil gemt i docs/sikkerhed.md med sikkerhedsanalyse.",
            "checks": [
                {"type": "file_exists", "paths": ["docs/sikkerhed.md"], "min_files": 1},
                {"type": "code_contains", "path": "docs/sikkerhed.md", "patterns": [
                    "inputvalidering", "autentifikation", "access control",
                    "kryptering", "fejlh\u00e5ndtering", "session",
                    "CSRF", "XSS", "SQL injection", "OWASP"
                ], "min_matches": 5},
            ],
        },
    },
    "programmering": {
        "Kravanalyse": {
            "type": "file_exists",
            "paths": ["docs/kravanalyse.md"],
            "min_files": 1,
            "description": "FORM\u00c5L: Afd\u00e6k og dokument\u00e9r alle krav (funktionelle og ikke-funktionelle). Kr\u00e6ver: docs/kravanalyse.md eksisterer.",
        },
        "Arkitekturdesign": {
            "type": "file_exists",
            "paths": ["docs/arkitektur.md"],
            "min_files": 1,
            "description": "FORM\u00c5L: Design systemarkitektur med komponenter, moduler og dataflow. Kr\u00e6ver: docs/arkitektur.md eksisterer.",
        },
        "Implementeringsplan": {
            "type": "file_exists",
            "paths": ["docs/implementeringsplan.md"],
            "min_files": 1,
            "description": "FORM\u00c5L: Planl\u00e6g filstruktur, r\u00e6kkef\u00f8lge og teststrategi. Kr\u00e6ver: docs/implementeringsplan.md eksisterer.",
        },
        "Sikkerhedsanalyse": {
            "type": "file_exists",
            "paths": ["docs/sikkerhedsanalyse.md"],
            "min_files": 1,
            "description": "FORM\u00c5L: Analys\u00e9r sikkerhedsaspekter (OWASP, inputvalidering, auth). Kr\u00e6ver: docs/sikkerhedsanalyse.md eksisterer.",
        },
        "Kodeimplementering": {
            "type": "tool_called",
            "tools": ["write_file", "edit_file"],
            "description": "FORM\u00c5L: Skriv koden baseret p\u00e5 design og plan. Kr\u00e6ver: write_file eller edit_file kaldt (greenfield).",
        },
    },
}


def check_phase_done(agent: Any, task_node: Any, called_tools: dict | None = None, base_dir: str | None = None, tool_name: str = "", full_response: str = "") -> tuple[bool, str]:
    """Check whether the current phase should auto-complete.

    Looks up the phase's check spec in ``TEMPLATE_PHASE_CHECKS`` and runs it.
    Returns ``(passed, message)``. If no spec is defined for the current
    template/phase, returns ``(False, "")`` so existing LLM-driven flow
    continues.

    Args:
        agent: Agent instance (for template/lang, ``_tests_failed`` flag,
            ``messages`` for ``min_text_length``, ``active_template``).
        task_node: Current task node (for phase name).
        called_tools: Optional dict of tool-key -> call count. Tool keys are
            ``"{tool_name}{args_dict_repr}"``; we extract the tool name by
            splitting on ``{``.
        base_dir: Optional base directory to resolve relative paths against.
        tool_name: Name of the most recent tool call. Used by
            ``tool_called`` and ``tests_pass`` checks to detect "the LLM
            just called this tool" without scanning the full history.
        full_response: Accumulated LLM text output (streaming). Used by
            ``min_text_length`` to count characters without scanning
            ``agent.messages`` (faster and works even when messages list
            is empty).
    """
    template = getattr(agent, "active_template", "") or ""
    if not template:
        return False, ""
    template_checks = TEMPLATE_PHASE_CHECKS.get(template)
    if not template_checks:
        return False, ""
    phase_name = (task_node.name or "").strip()
    canonical_key = _resolve_phase_key(phase_name, template_checks)
    if not canonical_key:
        return False, ""
    spec = template_checks.get(canonical_key)
    if not spec:
        return False, ""
    check_type = spec.get("type")
    if check_type == "file_exists":
        return check_file_exists(spec.get("paths", []), spec, base_dir=base_dir)
    if check_type == "files_from_plan":
        return check_files_from_plan(spec, base_dir=base_dir)
    if check_type == "min_text_length":
        return check_min_text_length(spec, full_response=full_response, agent=agent)
    if check_type == "code_contains":
        return check_code_contains(spec, base_dir=base_dir)
    if check_type == "tool_called":
        return check_tool_called(spec, tool_name=tool_name, called_tools=called_tools)
    if check_type == "tests_pass":
        return check_tests_pass(spec, agent=agent, tool_name=tool_name, called_tools=called_tools)
    if check_type == "symbols_covered":
        return check_symbols_covered_by_modules(spec, base_dir=base_dir)
    if check_type == "text_contains":
        return check_text_contains(spec, full_response=full_response)
    if check_type == "all_of":
        return check_all_of(
            spec, agent=agent, task_node=task_node, called_tools=called_tools,
            base_dir=base_dir, tool_name=tool_name, full_response=full_response,
        )
    return False, f"unknown check type: {check_type}"
