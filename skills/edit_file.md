---
name: edit_file
keywords: [edit, ret, rediger, fix, ændr, replace, erstat, rettelse, patch,
  søg og erstat, search and replace, edit_file, symbol, function edit, rett,
  replace_in_file]
action_types: [write]
---

## Edit File — Symbol-based Editing

Use `edit_file` with `symbol` to replace entire functions/classes/methods reliably.

### Preferred Workflow (Python files)

1. **Read the function** with `read_location(filepath="fil.py", name="funktionsnavn")` — returns ONLY the function body, not the entire file (brug `locate` hvis du ikke kender filnavnet)
2. **Edit with symbol**: `edit_file(path="fil.py", symbol="search_ddg", new_text="def search_ddg(...):\n    ...")`

The tool uses AST to find the function's exact line range, extracts the old body, and replaces it with `new_text`. No fragile `old_text` matching needed.

### Rules

- `new_text` must include the **ENTIRE** function/class — from `def`/`class` line through the end of the body
- One `edit_file` call replaces exactly one symbol
- For structural changes (e.g. adding a second `except` block), do sequential passes — each `edit_file` call replaces the whole function with an improved version
- Run `run_tests()` after each edit to verify

### Error Recovery

- If `edit_file` returns a syntax error, re-read the file with `read_chunk`, fix `new_text`, and retry
- **NEVER** advance with `<<<DONE>>>` after a failed edit — keep retrying until it succeeds
- If `symbol` is unavailable (non-Python file or symbol not found), fall back to `old_text`+`new_text` — copy `old_text` **EXACTLY** from `read_location` output (or `read_chunk` for non-Python files), preserving all whitespace and indentation
- **Never use `read_chunk` on `.py` files** — `read_location` is always preferred. `read_chunk` is only for non-Python files (`.json`, `.html`, `.txt`, etc.)

### Examples

**Good** (symbol-based, preferred):
```
locate(filepath="ddg_search.py", name="search_ddg")
# Returns: line 44-83, full function body

edit_file(
  path="ddg_search.py",
  symbol="search_ddg",
  new_text="""def search_ddg(query: str, max_results: int = 5) -> List[Dict]:
    params = urllib.parse.urlencode({'q': query, 'kl': 'en-us'})
    url = f'https://html.duckduckgo.com/html/?{params}'
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': _UA})
            with urllib.request.urlopen(req, timeout=10) as resp:
                html = resp.read().decode('utf-8', errors='replace')
            break
        except urllib.error.URLError as e:
            if attempt == 2:
                log.warning("ddg search failed for '%s': %s", query, e)
                return []
            time.sleep(0.5 * (attempt + 1))
    ..."""
)
```

**Bad** (manual old_text, fragile):
```
edit_file(
  path="ddg_search.py",
  old_text="except Exception as e:",  # ← fragile, indentation must match exactly
  new_text="except urllib.error.URLError as e:"  # ← often fails
)
```

### Multi-step structural changes

When you need to restructure code (e.g. replace one `except` clause with two), do sequential symbol replacements — each pass gives the full corrected function:

```
# Pass 1: narrow exception type
edit_file(path="ddg_search.py", symbol="search_ddg",
  new_text="def search_ddg(...):\n    ...\n        except urllib.error.URLError as e:\n            ...")

# Pass 2: add second except for unexpected errors  
edit_file(path="ddg_search.py", symbol="search_ddg",
  new_text="def search_ddg(...):\n    ...\n        except urllib.error.URLError as e:\n            ...\n        except Exception as e:\n            log.error(...)")
```
