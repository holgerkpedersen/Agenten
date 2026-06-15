"""Basic phase checks: file_exists, text_contains, min_text_length, tool_called."""
from __future__ import annotations

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

    The check is satisfied once the LLM has produced at least ``min_chars`` characters of accumulated output.
    Used by Analyse phases to make sure the LLM cannot terminate with a one-liner.

    Spec keys:
        - ``min_chars`` (required) — minimum accumulated character count.
        - ``include_messages`` (default True) — also count text inside ``agent.messages`` (assistant turns).
          Set to False if you only want to count the streaming ``full_response`` text.
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
    "Just called" is enough — we do not require a successful result, since most phase check contexts are already running after a successful tool call (e.g. ``edit_file`` returning ``success: True``).

    Spec keys:
        - ``tools`` (required) — list of tool names. Pass if any of them has been called.
          If ``tool_name`` is one of them, we pass immediately without scanning ``called_tools``.
        - ``require_all`` (default False) — when True, all listed tools must appear in ``called_tools``.
          Requires a non-empty ``called_tools``.
        - ``min_count`` (default 1) — minimum total calls across all listed tools.
          Counts sum of values for matching tool keys.
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
