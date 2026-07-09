"""Shared plan parser for refactor plans.

Loads LLM-specific regex configs from ``llm_plans/<name>.json``.
Every caller that parses ``refactor_plan.md`` must use this module —
never inline regex.

Usage::

    from plan_parser import parse_refactor_plan, parse_modules_from_plan

    # Full parse: symbols per module
    plan = parse_refactor_plan(plan_content, agent.llm.model)
    # Module-only: filenames only
    modules = parse_modules_from_plan(plan_content, agent.llm.model)
"""

import json
import os
import re
from functools import lru_cache
from typing import Any

_PLANS_DIR = os.path.join(os.path.dirname(__file__), "llm_plans")


# --------------------------------------------------------------------------
# Config loading
# --------------------------------------------------------------------------

def load_plan_config(model_name: str | None = None) -> dict[str, Any]:
    """Return the best matching plan parser config for *model_name*.

    Scans ``llm_plans/*.json``, scores each by how many ``model_patterns``
    regexes match *model_name* (case-insensitive).  The config with the
    highest match count wins.  If no model-specific config matches, returns
    ``default.json`` (which must exist).
    """
    if not model_name:
        model_name = _resolve_default_model()
    configs = _load_all_configs()
    if not configs:
        return {}

    best = configs[0] if configs else {}
    best_score = 0

    for cfg in configs:
        name = cfg.get("name", "")
        if name == "default":
            continue
        score = 0
        for pat in cfg.get("model_patterns", []):
            try:
                if re.search(pat, model_name, re.IGNORECASE):
                    score += 1
            except re.error:
                continue
        if score > best_score:
            best_score = score
            best = cfg

    if best_score == 0:
        for cfg in configs:
            if cfg.get("name") == "default":
                best = cfg
                break

    return best


def _resolve_default_model() -> str:
    try:
        from config import LLM_MODEL
        return LLM_MODEL
    except (ImportError, AttributeError):
        return "default"


@lru_cache(maxsize=1)
def _load_all_configs() -> list[dict[str, Any]]:
    """Load every ``*.json`` from the plans directory, sorted by name."""
    configs: list[dict[str, Any]] = []
    if not os.path.isdir(_PLANS_DIR):
        return configs
    for fname in sorted(os.listdir(_PLANS_DIR)):
        if not fname.endswith(".json"):
            continue
        fpath = os.path.join(_PLANS_DIR, fname)
        try:
            with open(fpath, encoding="utf-8") as f:
                configs.append(json.load(f))
        except (OSError, json.JSONDecodeError):
            continue
    return configs


def clear_config_cache() -> None:
    """Discard cached config list (call after adding a new ``*.json``)."""
    _load_all_configs.cache_clear()


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------

def parse_refactor_plan(
    content: str,
    model_name: str | None = None,
) -> dict[str, list[str]]:
    """Parse *content* (plan markdown) into ``{module_filename: [symbol_names]}``.

    Returns empty dict when nothing is found.
    """
    if not content:
        return {}
    config = load_plan_config(model_name)
    return _parse_plan(content, config)


def parse_modules_from_plan(
    content: str,
    model_name: str | None = None,
    *,
    source_file: str = "",
    allow_nested: bool = False,
    ext: str = ".py",
) -> list[str]:
    """Extract only module filenames from plan markdown (no symbols).

    Uses the ``module_only`` config section, falling back to heading-based
    detection.  Returns sorted unique list.

    Args:
        ext: File extension to look for (default ``.py``). When non-``.py``,
            the heading pattern's extension is dynamically replaced.
    """
    if not content:
        return []
    config = load_plan_config(model_name)
    modules: set[str] = set()

    # Normalize: insert newline before ## headings appearing mid-line
    content = re.sub(r'(?<=\S)\s+(?=##\s)', '\n', content)

    mo = config.get("module_only", {})
    methods: list[str] = mo.get("methods", ["heading"])
    heading_cfg = config.get("module", {}).get("heading", {})
    heading_pat = heading_cfg.get("pattern", "")
    heading_flags = heading_cfg.get("flags", "")
    exclude = config.get("module", {}).get("exclude_headings", [])

    # For non-.py extensions, dynamically replace the extension in patterns
    ext_pattern = re.escape(ext)
    if ext != ".py" and heading_pat:
        heading_pat = re.sub(r'\\\.py', ext_pattern, heading_pat)

    if "heading" in methods and heading_pat:
        heading_re = _compile_re(heading_pat, heading_flags)
        for m in heading_re.finditer(content):
            name = m.group(1).strip().strip("`")
            if not name:
                continue
            hl = _get_line_at(content, m.start())
            if _matches_any(hl, exclude):
                continue
            if not allow_nested and ("/" in name or "\\" in name):
                continue
            modules.add(name)

    if "inline" in methods:
        inline_pat = mo.get("inline_pattern", "")
        if inline_pat:
            # For non-.py extensions, replace in inline pattern too
            if ext != ".py":
                inline_pat = re.sub(r'\\\.py', ext_pattern, inline_pat)

            # Build excluded ranges: text under excluded headings should not
            # contribute inline filenames (e.g. "## Forbliver i x.py").
            # Some excluded headings are NOT matched by heading_re (because
            # the heading style is different), so we detect them separately.
            excluded_ranges: list[tuple[int, int]] = []
            if exclude:
                excluded_ranges = _find_excluded_ranges(content, exclude)

            inline_re = _compile_re(inline_pat)
            for m in inline_re.finditer(content):
                pos = m.start()
                if any(start <= pos < end for start, end in excluded_ranges):
                    continue
                name = m.group(1).strip().strip("`")
                if not name:
                    continue
                if not allow_nested and ("/" in name or "\\" in name):
                    continue
                modules.add(name)

    return sorted(modules)


# --------------------------------------------------------------------------
# Internal helpers
# --------------------------------------------------------------------------

def _parse_plan(content: str, config: dict[str, Any]) -> dict[str, list[str]]:
    """Core parse logic driven by *config*."""
    result: dict[str, list[str]] = {}

    # Normalize: insert newline before ## headings appearing mid-line
    # (handles single-line plans like "... Symboler (10): ... ## Module: file.py ...")
    content = re.sub(r'(?<=\S)\s+(?=##\s)', '\n', content)

    heading_cfg = config.get("module", {}).get("heading", {})
    heading_pat = heading_cfg.get("pattern", "")
    heading_flags = heading_cfg.get("flags", "")
    exclude_headings = config.get("module", {}).get("exclude_headings", [])
    exclude_labels = config.get("module", {}).get("exclude_symbol_labels", [])

    if not heading_pat:
        return result

    heading_re = _compile_re(heading_pat, heading_flags)

    matches = list(heading_re.finditer(content))
    if not matches:
        return result

    for i, m in enumerate(matches):
        mod_name = m.group(1).strip().strip("`")
        if not mod_name:
            continue

        # Check if this heading should be excluded (e.g. "Forbliver i")
        heading_line = _get_line_at(content, m.start())
        if _matches_any(heading_line, exclude_headings):
            continue

        # Section = text between this heading's end and the next heading
        end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
        section = content[m.end():end]

        symbols = _extract_symbols(section, config.get("symbols", {}), heading_line, exclude_labels)
        if symbols:
            result[mod_name] = symbols

    return result


def _extract_symbols(
    section: str,
    sym_config: dict[str, Any],
    heading_line: str,
    exclude_labels: list[str],
) -> list[str]:
    """Extract symbol names from a module section."""
    seen: set[str] = set()

    # 1. Check heading line for inline symbols
    on_hl = sym_config.get("on_heading_line", {})
    if on_hl.get("enabled", True):
        hl_pat = on_hl.get("pattern", "")
        if hl_pat:
            m = _compile_re(hl_pat).search(heading_line)
            if m:
                for part in m.group(1).split(","):
                    _add_symbol(part, seen, exclude_labels)

    # 2. Primary: inline symbol line (**Symboler (N):** sym1, sym2, ...)
    inline_cfg = sym_config.get("inline", {})
    inline_pat = inline_cfg.get("pattern", "")
    if inline_pat:
        m = _compile_re(inline_pat).search(section)
        if m:
            raw = m.group(1).strip()
            for part in raw.split(","):
                _add_symbol(part, seen, exclude_labels)

    # 3. Fallback: bullet items
    bullet_cfg = sym_config.get("bullet", {})
    if bullet_cfg.get("enabled", False) and not seen:
        bullet_pat = bullet_cfg.get("pattern", "")
        bullet_flags = bullet_cfg.get("flags", "")
        if bullet_pat:
            for m in _compile_re(bullet_pat, bullet_flags).finditer(section):
                name = m.group(1).strip().strip("`")
                if name and not _matches_any(name, exclude_labels):
                    seen.add(name)

    return sorted(seen)


def _add_symbol(raw: str, seen: set[str], exclude_labels: list[str]) -> None:
    """Parse and add a single symbol name from comma-separated text."""
    name = raw.strip().strip("`").strip()
    if not name:
        return
    m = re.match(r"([a-zA-Z_]\w*)", name)
    if not m:
        return
    sym = m.group(1)
    if sym.startswith("__"):
        return
    if _matches_any(sym, exclude_labels):
        return
    seen.add(sym)


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------

_REGEX_CACHE: dict[str, re.Pattern] = {}


def _compile_re(pattern: str, flags: str = "") -> re.Pattern:
    """Compile a regex with caching."""
    key = pattern + "|" + flags
    rv = _REGEX_CACHE.get(key)
    if rv is not None:
        return rv
    flag_int = 0
    for ch in flags:
        if ch == "m":
            flag_int |= re.MULTILINE
        elif ch == "i":
            flag_int |= re.IGNORECASE
        elif ch == "s":
            flag_int |= re.DOTALL
    rv = re.compile(pattern, flag_int)
    _REGEX_CACHE[key] = rv
    return rv


def _get_line_at(content: str, pos: int) -> str:
    """Return the full line containing position *pos*."""
    start = content.rfind("\n", 0, pos) + 1
    end = content.find("\n", pos)
    if end == -1:
        end = len(content)
    return content[start:end]


def _matches_any(text: str, patterns: list[str]) -> bool:
    """Return ``True`` if *text* matches any regex in *patterns*."""
    for pat in patterns:
        try:
            if re.search(pat, text):
                return True
        except re.error:
            continue
    return False


def _find_excluded_ranges(content: str, exclude_patterns: list[str]) -> list[tuple[int, int]]:
    """Find ranges of text under excluded headings.

    Scans for lines starting with ``##`` that match *exclude_patterns* AND
    contain ``.py``, then finds the range from that heading to the next
    heading (or end of file).  This catches both heading-re matched and
    non-matched excluded headings (e.g. a Forbliver heading with a .py path).
    """
    ranges: list[tuple[int, int]] = []
    lines = content.splitlines(keepends=True)
    # Build position map: line_index → start_position in content
    pos = 0
    line_starts: list[int] = []
    for line in lines:
        line_starts.append(pos)
        pos += len(line)

    # Find all excluded heading lines
    for i, line in enumerate(lines):
        stripped = line.lstrip()
        if stripped.startswith("##") and ".py" in stripped:
            if _matches_any(stripped, exclude_patterns):
                start_pos = line_starts[i]
                # End = next heading or EOF
                end_pos = len(content)
                for j in range(i + 1, len(lines)):
                    ns = lines[j].lstrip()
                    if ns.startswith("##") and not _matches_any(ns, exclude_patterns):
                        end_pos = line_starts[j]
                        break
                    if ns.startswith("##"):
                        continue
                ranges.append((start_pos, end_pos))
    return ranges
