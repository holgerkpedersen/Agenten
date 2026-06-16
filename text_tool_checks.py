from typing import Any


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

import os
import re



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
