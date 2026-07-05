import json



def _extract_tool_call_content(messages: list[dict] | None) -> str:
    """Extract text content from OpenAI-style tool_calls in assistant messages.

    Native function calling places write_file/edit_file content (often 2000+ chars)
    inside ``tool_calls[*].function.arguments`` as a JSON string, NOT in the
    streaming ``content`` field. Phase checks like ``min_text_length`` need this
    content to determine if the LLM produced enough output — without extraction
    the check always sees 0-50 chars and fails, causing 5 retries.

    Returns a newline-joined string of all argument values >50 chars.
    """
    if not messages:
        return ""
    parts: list[str] = []
    for m in messages:
        if not isinstance(m, dict) or m.get("role") != "assistant":
            continue
        for tc in (m.get("tool_calls") or []):
            if not isinstance(tc, dict):
                continue
            fn = tc.get("function", tc)
            if not isinstance(fn, dict):
                continue
            args_str = fn.get("arguments", "")
            if not args_str or not isinstance(args_str, str):
                continue
            try:
                args = json.loads(args_str)
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(args, dict):
                for v in args.values():
                    if isinstance(v, str) and len(v) > 50:
                        parts.append(v)
    return "\n".join(parts)
