"""File-based phase checks: file_exists, files_from_plan, code_contains.

Checks that verify files exist on disk, plan modules are created, or source
files contain expected patterns.
"""
from __future__ import annotations

import os
import re
from typing import Any


def check_file_exists(
    paths: list[str], spec: dict[str, Any] | None = None, base_dir: str | None = None
) -> tuple[bool, str]:
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


def _extract_modules_from_plan(
    plan_content: str, ext: str = ".py", allow_nested: bool = False
) -> list[str]:
    """Extract module filenames from a refactor plan markdown.

    The plan format from the LLM typically looks like:
        ### 1. routes.py **Ansvar:** Endpoint definitioner
        ### 2. session_manager.py **Ansvar:** Session CRUD

    We also pick up bare filenames like ``routes.py`` anywhere in the text
    (in case the LLM didn't use a heading).

    Args:
        plan_content: The markdown content of the plan file.
        ext: File extension to look for (default ".py").
        allow_nested: If True, keep paths with ``/`` or ``\\``
            (e.g. ``gui/browser_window.py``). Used by greenfield projects where
            modules are organized in subdirectories.
    """
    if not plan_content:
        return []

    seen: set[str] = set()
    ext_pattern = re.escape(ext)
    heading_pat = re.compile(
        rf"^\s*#{1,6}\s+[\d\.\)]*\s*(\S+{ext_pattern})\b", re.MULTILINE
    )
    # Also match "## Module: file.py" or "## Module: `file.py`" (colon-separated heading)
    module_heading_pat = re.compile(
        rf"^\s*#{1,6}\s+[Mm]odul[er]*\s*\d*:?\s*(\S+{ext_pattern})\b", re.MULTILINE
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


def check_files_from_plan(
    spec: dict[str, Any], base_dir: str | None = None
) -> tuple[bool, str]:
    """Return ``(passed, message)`` for a files_from_plan check.

    Spec keys:
        - ``plan_path`` (required) — path to the plan file (relative to base_dir)
        - ``ext`` (default ".py") — file extension to look for
        - ``min_files`` (default 1) — minimum number of files that must be listed
        - ``allow_nested`` (default False) — if True, keep paths with ``/`` or ``\\``
            (e.g. ``gui/browser_window.py``). Used by greenfield projects.
        - ``base_dir`` (optional) — override the cwd for both the plan and the
            module files. If not provided, uses ``os.getcwd()``.
    """
    base = base_dir or os.environ.get("AGENT_WORKDIR") or os.getcwd()
    plan_rel = spec.get("plan_path", "refactor_plan.md")
    plan_path = (
        os.path.join(base, plan_rel) if not os.path.isabs(plan_rel) else plan_rel
    )
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

    modules = _extract_modules_from_plan(
        plan_content, ext=ext, allow_nested=allow_nested
    )
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


def check_code_contains(
    spec: dict[str, Any], base_dir: str | None = None
) -> tuple[bool, str]:
    """Return ``(passed, message)`` for a code_contains check.

    The check reads ``spec["path"]`` (relative to ``base_dir``) and counts regex
    matches for each pattern in ``spec["patterns"]``. Used to confirm that refactor
    Opdatér actually added ``from routes import ...`` etc. to ``api_server.py``.

    Spec keys:
        - ``path`` (required) — file to scan.
        - ``patterns`` (required) — list of regex strings.
        - ``require_all`` (default False) — if True, every pattern must match at least once.
            If False, only ``min_matches`` patterns need to match.
        - ``min_matches`` (default 1) — minimum number of distinct patterns that must match
            (used with ``require_all=False``).
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
