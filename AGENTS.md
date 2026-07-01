# Agenten — Agent Knowledge Base

Key facts, fixes, and debugging patterns learned through development.
Read this before making changes.

## Project Structure

| File | Purpose |
|------|---------|
| `api_server.py` | Flask REST API + all endpoints |
| `agent_core.py` | Agent facade (504 lines) — `__init__`, tool registration, `decompose_prompt`, `execute_tree`, thin delegation methods |
| `agent_issues.py` | Issue tools (`read_issue`, `update_issue_status`, `create_refactor_issue`) + oversize file detection |
| `agent_files.py` | File/chunk operations (`_read_file_content`, `_get_folder_context`, `chunk_text`, `list_chunks`, `read_chunk`) |
| `agent_tree.py` | Tree operations (`parse_tree_from_llm`, `create_fallback_tree`, `count_tasks`, `record_outcome`, `evolve_if_needed`) |
| `agent_skills.py` | Skills matching, template/config constants (`TEMPLATE_TOOLS`, `TEMPLATE_TASK_TOOLS`, `SECTION_INSTRUCTIONS`, `get_templates`) |
| `agent_git.py` | Git/PR workflow helpers (`is_pr_workflow`, `extract_branch_name`, `verify_pr_step`) + PR constants |
| `agent_tasks.py` | Task execution engine (`solve_task_stream`, `solve_task`, `handle_tool_call`, `_auto_populate_llm_todos`, `_reconcile_llm_todos`) |
| `agent_tools_todo.py` | LLM-driven todo management tools (`plan_phase`, `create_todo`, `update_todo`, `delete_todo`, `list_todos`) |
| `llm_wrapper.py` | LM Studio API client, image encoding, vision support |
| `tools.py` | ToolRegistry, tool dispatch (`execute()`), `parse_response()` |
| `lang.py` | Danish/English/Spanish/Chinese translations |
| `i18n.py` | Translation key enum (K.KEY) |
| `phase_engine.py` | Phase auto-advance logic (`check_phase_done`, `TEMPLATE_PHASE_CHECKS`) |
| `static/index.html` | Complete frontend SPA |
| `skills/*.md` | Agent skill definitions |
| `instructions/*.json` | Per-template section instructions (JSON, override `agent_skills.py`) |
| `sessions/*.json` | Persisted session data |

## Version Check

Server startup prints:
```
🕐 Startet: 2026-05-19 15:21:30
📦 api_server=15:21:30 | agent_core=12:23:16 | llm=15:15:05
```

`/api/version` returns JSON with all file timestamps + startup time.
Use this to verify the running code version — never assume code is deployed.

## Common Bugs & Fixes

### 1. Tool dispatch crashes with zero-param tools (`tools.py:125`)

**Symptom:** `TypeError: lambda() got an unexpected keyword argument 'X'`  
**Root cause:** `execute()` unpacks all args as `**kwargs`. Zero-param tools reject unknown kwargs.  
**Fix:** Use `inspect.signature(fn).parameters` to filter args. Also check for missing required params.

### 2. `_validate_template_prompt` KeyError (`api_server.py:603`)

**Symptom:** 500 error on decompose endpoint.  
**Root cause:** `_validate_template_prompt()` returned `{"warning":""}` without `matches`/`total` keys.  
**Fix:** All return paths include `"matches"` and `"total"`.

### 3. `_extract_filenames` failed on parenthetical locations (`agent_core.py:26-38`)

**Symptom:** Issue location `"llm_wrapper.py (entire file)"` returned `[]` — LLM couldn't auto-load the file.  
**Root cause:** `endswith(".py")` fails when `.py` is not at the very end of the string.  
**Fix:** Replaced with `re.finditer(r'([\w./-]+\.\w+)', location)` — regex extracts any `.py` filename from arbitrary parenthetical/coloned text.

### 4. Folder scanning missed file paths (`agent_files.py:68`)

**Symptom:** Prompt has `C:\dev\DEX\run.py` but `file_chunks` is empty.  
**Root cause:** `_get_folder_context()` only checked if `os.path.isdir(path)` — file paths failed.  
**Fix:** Also check `os.path.isdir(os.path.dirname(path))` when path is a file.

### 4. `_read_file_content` crashes on binary files

**Symptom:** UnicodeDecodeError on PNG/WEBP files in scanned folders.  
**Fix:** Skip binary extensions (`.png`, `.jpg`, `.webp`, etc.) early in `_read_file_content()`.

### 5. Image format: Gemma models = raw base64 ONLY

**Symptom:** `{"error":"'url' field must be a base64 encoded image"}` — HTTP 400  
**Verified:** gemma-4-26b-a4b, gemma-4-e4b — both reject `data:image/webp;base64,...`  
**Fix:** `llm_wrapper.py` `IMAGE_FORMATS` maps `"gemma"` → `"raw_b64"`. Qwen/GPT/Llava use `"data_url"`.  
**Never change the gemma entry to data_url** — verified via error logs 2026-05-19.

**Additionally:** Gemma uses `"type": "image"` (not `"image_url"`) — JSON structure is:
```json
{"type": "image", "url": "<base64>"}
```
Other models use OpenAI format:
```json
{"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}
```
This is handled by `_image_part()` in `llm_wrapper.py`.

**CORRECTION 2026-05-19:** LM Studio uses OpenAI-compatible API — `"type": "image_url"` is REQUIRED for all models. The difference is ONLY the `url` value:
- Gemma: **HTTP URL** (`"http://localhost:5000/uploads/filename.webp"`) — NOT base64, per vLLM/SGLang docs
- Qwen/GPT/Llava: data URL (`"data:image/png;base64,AAAA..."`)

Flask serves uploads at `/uploads/<filename>` so gemma can fetch the image locally.

**CRITICAL FINDING 2026-05-19:** Gemma 4 via LM Studio DOES accept `data:image/...;base64,...` format. The issue was:
1. `image/webp` MIME type is REJECTED — must use `image/png` or `image/jpeg`
2. URL-safe base64 works fine
3. `_image_url()` now maps `webp` → `png` automatically

**Verified:** Session 70a4713a — 0 HTTP 400 errors after fix (was 48). All 5 billedanalyse tasks completed on both gemma-4-26b-a4b AND gemma-4-e4b.

### 6. Image ordering: Gemma 4 requires images BEFORE text

**Symptom:** HTTP 400 when images come after text in content array.  
**Fix:** `_to_messages()` puts `image_url` entries BEFORE `text` entries. Documented in Google's model card.

### 7. VISION_KEYWORDS prevents text models from receiving images

**Symptom:** Text-only models crash with HTTP 400 when images are sent.  
**Fix:** `_supports_vision(model)` checks model name against `VISION_KEYWORDS`. Only vision models get images.

### 8. Session-scoped images

**Symptom:** Images from old session appear in new session.  
**Fix:** `create_session()` clears `agent.images`. `loadSelectedSession()` saves current session first. Auto-save after upload/clear/remove.

### 9. "Kunne ikke fuldføre opgaven automatisk"

**Symptom:** Task completes but no useful output.  
**Common causes:** HTTP 400 on all iterations (check `flask_output.log`), wrong model, no image uploaded.

### 10. `.env` files leaked into `file_chunks` via folder scan (`agent_files.py`)

**Symptom:** GITHUB_TOKEN and other secrets visible in `file_chunks` context sent to LLM.  
**Root cause:** `FOLDER_SCAN_EXTENSIONS` and a bypass condition `and not f.startswith('.env')` caused `.env` files to bypass extension filtering.  
**Fix:** Added `FOLDER_SCAN_EXCLUDE_FILES = {'.env'}`, explicit check before extension filter, and belt-and-suspenders check in `read_file_content()`. Renamed `FOLDER_SCAN_EXCLUDE` → `FOLDER_SCAN_EXCLUDE_DIRS` for clarity.

### 11. Missing Agent delegate methods after refactor (`agent_core.py`)

**Symptom:** `'Agent' object has no attribute '_evolve_if_needed'` at runtime.  
**Root cause:** Module function `evolve_if_needed()` was extracted to `agent_tree.py` but the thin delegation method `_evolve_if_needed` was never added to the Agent class.  
**Fix:** Add delegation method: `def _evolve_if_needed(self): agent_tree.evolve_if_needed(self)`  
**Rule:** Every `agent._*()` call in module code must have a corresponding delegation method in `agent_core.py`.

### 12. ARC-004: `_clean_task_name` had 50+ hardcoded regex patterns (`agent_tree.py:8-69`)

**Symptom:** New LLM models produce novel meta-instruction formats not covered by the pattern list — whack-a-mole maintenance.  
**Root cause:** ~50 hardcoded regex patterns trying to catch every possible LLM output-instruction phrasing.  
**Fix:** Replaced all 50+ patterns with a two-tier approach: (1) general structural cleaning (think tags, channel markers, markdown, numbering, bullets), (2) a single compiled regex with ~25 prefix alternatives grouped by semantic category. All 26 existing tests pass.  
**File:** `agent_tree.py:8-34`

### 13. Delegation-aware file resolution + hash verification

**Symptom:** Agent loads `agent_core.py` from an issue location, but the target function is just a 1-line stub delegating to another file (e.g. `agent_tree.py`). The LLM can't find the real implementation, loops on `list_chunks`/`read_chunk` forever.  
**Root cause:** Issue locations are just filenames — no delegation chain resolution, no file-integrity check.  
**Fix (4 files):**
- `agent_files.py`: `detect_delegations(content)` — regex that finds all `def X(self): return module.X(...)` stubs; `file_hash(filepath)` — SHA-256 digest for integrity
- `agent_core.py`: `_ensure_delegation_index()` — lazy-builds `func_name → (abspath, filename)` map by recursively following chains (handles circular refs); `_resolve_delegations_for_context()` — auto-loads delegation target files into `file_chunks` with context notes; `edit_file` lambda passes `expected_hash` from `_file_hash_registry`
- `agent_tasks.py`: `solve_task_stream()` scans `file_chunks` for stubs, appends `## DELEGERINGER` guidance block mapping each stub → real file
- `git_ops.py`: `edit_file()` accepts `expected_hash` parameter — hard block if file changed since load
**Performance:** Delegation index built once per Agent instance (lazy), O(1) lookups. Hash check only on `edit_file` calls.
**Files:** `agent_files.py:12-37`, `agent_core.py:326-389`, `agent_tasks.py:124-142`, `git_ops.py:296-312`

### 14. `solve_task()` called non-existent functions (`agent_tasks.py`)
**Symptom:** `NameError` when calling `/api/execute-without-stream`.  
**Root cause:** `solve_task()` called 4 module functions that were never defined (`build_tool_guidance`, `ask_ai`, `handle_tool_call`, `truncate_conversation`).  
**Fix:** Rewrote `solve_task()` to delegate to `solve_task_stream()` generator, collecting the final result. Also removed 4 dead delegate methods in `agent_core.py` pointing to the same non-existent functions.

### 15. Windows `split(":")` destroys drive letters (`agent_issues.py:162-163`)
**Symptom:** Location `C:\Dev\file.py:42` becomes just `C`.  
**Root cause:** `location.split(":")[0]` on a Windows path with drive letter.  
**Fix:** Use `os.path.splitdrive()` to detect and preserve Windows drive letters before colon-splitting.

### 16. SSE `current_session_id` race condition (`api_server.py:1111`)
**Symptom:** Concurrent requests save session data to the wrong session file.  
**Root cause:** `generate()` closure in `execute_stream()` referenced the global `current_session_id`, which can be mutated by concurrent request handlers.  
**Fix:** Capture `session_id = current_session_id` in a local variable before the closure is created.

### 17. `github_wrapper.py` network error handling
**Symptom:** Unhandled exception on network failure or non-JSON GitHub response.  
**Root cause:** No try/except around `requests.*()` calls, and `resp.json()` called on error paths without fallback.  
**Fix:** Added `_request()` wrapper with `RequestException` handling and `_safe_json()` helper for error paths. Default branch changed from `"master"` to `"main"`.

### 18. `flow_builder.py` URL encoding (`flow_builder.py:61-62`)
**Symptom:** Topics with `&` or `#` produce broken search URLs.  
**Root cause:** `topic.replace(' ', '+')` instead of `urllib.parse.quote()`.  
**Fix:** Replaced with `urllib.parse.quote(topic, safe='')`.

### 19. `agent_git.py` locale-dependent regex (`agent_git.py:55`)
**Symptom:** Branch name verification fails on systems with non-English git.  
**Root cause:** Regex `r"Switched to a new branch '...'"` only matches English locale.  
**Fix:** Extract branch name from `result.args.name` first (tool arguments), fall back to locale-independent quote regex on git output.

### 20. Session file atomic writes (`session_manager.py`)
**Symptom:** Corrupted session files after power loss.  
**Root cause:** `save_session` wrote directly to target file — crash during json.dump leaves partial file.  
**Fix:** Atomic write pattern: `.tmp` file → `os.replace(tmp, target)`. Also fixed TOCTOU in `load_session` (removed `os.path.exists()` check, catch `FileNotFoundError`).

### 21. `skill_evolution.py` `os.rename` Windows failure
**Symptom:** Skill prune fails on Windows when `.pruned` backup already exists.  
**Root cause:** `os.rename()` raises `PermissionError` on Windows if target exists.  
**Fix:** Replaced with `os.replace()` (cross-platform atomic rename).

### 22. `get_folder_context` path traversal (`agent_files.py:94-145`)
**Symptom:** Agent could scan `C:\Windows` or other sensitive directories.  
**Root cause:** No path safety check before `os.walk()`.  
**Fix:** Added `_is_safe_scan_path()` restricting folder scanning to project directory + system temp directories.

### 23. Remaining medium-priority issues
- `agent_skills.py:166`: Changed `s['name']` to `s.get('name', 'unknown')` — prevents crash on malformed skill dict
- `task_tree.py:9-12`: `add_child` now removes from old parent before re-parenting — prevents stale references and infinite loops
- `module_builder.py:17`: Added path traversal protection — rejects `..`, leading `/` or `\\`

### 24. `execution_log` dead code — never populated (`api_server.py`, `agent_tasks.py`)

**Symptom:** Session files always show `"execution_log": []` regardless of what tasks ran.  
**Root cause:** `execution_log` was initialized, reset, read, and serialized in 3 save paths, but **never appended to**.  
**Fix:** Add `agent.execution_log.append()` in `_execute_with_stream()` (`api_server.py:1085`) — one entry per completed task with timestamp, task name, and status.

### 25. `agent_log` overwritten by global agent stale data (`api_server.py:520,1097`)

**Symptom:** Session `agent_log` only shows decomposition entries — execution entries from SSE streaming are lost.  
**Root cause:** Two save paths conflict: (1) `_save_session_data()` saves `stream_agent.agent_log` (execution entries), then (2) frontend calls `/api/sessions/save` which saves global `agent.agent_log` (decomposition-only entries), overwriting the stream data.  
**Fix:** Both save paths now **merge** `agent_log` with existing entries using timestamp-based dedup instead of replacing:
  - `existing["agent_log"] = existing_agent_log + [e for e in new_log if e["timestamp"] not in existing_timestamps]`
  - Applied in `_save_session_data()` (`api_server.py:1093-1100`) and `/api/sessions/save` (`api_server.py:517-525`)

### 26. ADVARSEL false positive on successful tool-call tasks (`agent_tasks.py:232`)

**Symptom:** "⚠️ ADVARSEL: Dette resultat ser ufuldstændigt ud" appended to auto-generated success messages like "Gennemførte 6 værktøjskald. Opgave fuldført automatisk." (~60 chars).  
**Root cause:** The 100-char `is_short` threshold catches legitimate short success messages when the LLM returned `"ERROR"` after successful tool calls.  
**Fix:** Skip warning when `called_tools` is non-empty (`if (is_short or asks_for_files) and not called_tools:`). If tools were called, work was done — no warning needed.

### 27. `\bfinal\b` regex corrupts legitimate text (`tools.py:74`)

**Symptom:** LLM responses containing the word "final" in code or prose are silently stripped. Java `final` keyword, TypeScript code, normal English all corrupted.  
**Root cause:** `re.sub(r'\bfinal\s*', '', response)` universally removes the word "final" from ALL LLM responses, not just before tool/done markers.  
**Fix:** Changed to `re.sub(r'\bfinal\s*(?=<<<)', '', response)` — uses lookahead assertion to only strip "final" when immediately followed by `<<<` markers.

### 28. `edit_file` CRLF corruption on Windows (`git_ops.py:347-383`)

**Symptom:** `edit_file()` produces wrong byte positions when editing files with `\r\n` line endings. Normalized search text uses `\n` (1 byte) but content has `\r\n` (2 bytes) — byte offsets mismatch.  
**Root cause:** Content was read with `\r\n` preserved, then search normalized to `\n`-only on a separate `norm` variable. Byte positions from `norm` applied to original content produce wrong slices.  
**Fix:** Normalize content to `\n`-only at read time (`content.replace('\r\n', '\n').replace('\r', '\n')`), then search directly on content. Removed the separate `norm` variable entirely.

### 29. `VISION_KEYWORDS` prevents images sent to text-only models (`llm_wrapper.py`)

**Symptom:** HTTP 400 when images are sent to text-only models like `deepseek-chat`.  
**Root cause:** `_to_messages()` unconditionally injects images regardless of model capabilities.  
**Fix:** Added `VISION_KEYWORDS` class variable listing vision-capable model prefixes (qwen, gemma, gpt, llava, claude, gemini, vision, vl). Added `_supports_vision(model)` method. `_to_messages()` now checks `self._supports_vision(model)` before injecting images — if the model isn't vision-capable, images are silently dropped from the request (`images = None`).

### 30. `encode_image` file size limit prevents OOM (`llm_wrapper.py`)

**Symptom:** 100MB image upload becomes 133MB base64 string in memory.  
**Root cause:** `encode_image()` reads entire file without size check.  
**Fix:** Added `config.MAX_IMAGE_SIZE = 10 * 1024 * 1024` (10 MB). `encode_image()` checks `os.path.getsize(path)` before reading and raises `ValueError` if exceeded.

### 31. CORS + file upload hardening (`api_server.py:24,258-315`)

**Symptom:** Production CORS allows `*` origins. File uploads accept .html/.exe files. Spaces in filenames cause URL issues.  
**Fix:** CORS restricted to `CORS_ORIGINS` env var (defaults to `http://localhost:*`). File upload extension whitelist (`SAFE_UPLOAD_EXTS`). Return sanitized filename (not original) in upload response. `sanitize_filename()` replaces spaces with underscores. `MAX_CONTENT_LENGTH = 50 MB` Flask config.

### 32. Thread safety: globals locked (`api_server.py:74-91`)

**Symptom:** `active_streams`, `export_folder` mutated without locks — concurrent requests could corrupt or lose data.  
**Fix:** Added `active_streams_lock`, `export_folder_lock`, `current_session_lock`. All mutations wrapped in `with lock:` context managers. Added `_guard_json_body()` before_request handler — returns 400 on missing JSON body for POST endpoints, preventing `NoneType` crashes.

### 33. `list_files` and `get_single_file_context` path traversal (`git_ops.py:417`, `agent_files.py:81`)

**Symptom:** LLM can call `list_files("C:\\Windows")` to enumerate arbitrary directories. `get_single_file_context` attempts `os.path.join(base_dir, '..', filename)` without validation.  
**Fix:** Added `_is_safe_path()` check at top of `list_files()`. Added `os.path.realpath()` check in `get_single_file_context()` loop — rejects paths that resolve outside `base_dir`.  

### 34. Stale line numbers in issue locations → resolved by AST (`agent_files.py:locate_code`)

**Symptom:** Issues reference line numbers in `location` fields (e.g. `tools.py:95-96`). After edits to those files, line numbers shift and point to wrong/irrelevant code. The LLM reads wrong code, wastes iterations.  
**Root cause:** 145 of 161 issues used line-number-based locations. No mechanism to resolve current location from a stable identifier.  
**Fix (3 parts):**
- **`locate_code(filepath, name=None, line_no=None)`** in `agent_files.py` — new AST-based function finder. Given a line number, finds the innermost enclosing function/class and returns its name + current line number + full body. Given a function name (or `Class.method`), returns its current location. Handles nested functions, async functions, decorators.
- **Tool registration** — `locate` tool in `agent_core.py` lets the LLM call `locate(filepath='tools.py', name='parse_response')` to find the current line of any function. This means issues never need to store current line numbers — the agent resolves them at runtime.
- **Issue location migration** — `scripts/migrate_issue_locations.py` converts all `file.py:LL` locations to `file.py:function_name` (145 issues migrated). Adds `code_context` field with function signature (e.g. `def parse_response(self, response):`) as a secondary search anchor.
- **`create_issue()` auto-resolution** — `agent_issues.py` now auto-detects line-number locations at issue creation time and resolves them to function names via AST, adding a warning if function not found.
- **Pattern:** Use `locate` tool instead of hardcoded line numbers. Functions survive edits; line numbers don't.
- **Files:** `agent_files.py:198-299` (locate_code), `agent_core.py:310-314` (tool reg), `agent_issues.py:164-200` (auto-resolve), `scripts/migrate_issue_locations.py` (migration)

### 35. Native function calling: `generate_stream` crash on `tool_calls: null` (`llm_wrapper.py:255-257`)

**Symptom:** Every task iteration returns `[ERROR: 'NoneType' object is not iterable]` with models like `glm-5.1`.  
**Root cause:** Some OpenAI-compatible APIs include `"tool_calls": null` in streaming deltas (not omitting the key). `if "tool_calls" in delta: for tc in delta["tool_calls"]:` iterates over `None`.  
**Fix:** Replace with `tool_calls_list = delta.get("tool_calls"); if tool_calls_list: for tc in tool_calls_list:`. Uses `.get()` which returns `None` when key absent or value is null → falsy → skip.

### 36. `_resolve_base_url` priority: `OPENCODE_BASE_URL` moved to config (`llm_wrapper.py:32-41`, `config.py:59`)

**Symptom:** When `OPENCODE_BASE_URL` env var is set, all `LMStudioWrapper` instances connect to OpenCode Go — even when an explicit `base_url` is passed (e.g. `base_url=config.LLM_BASE_URL`). This breaks fallback to LM Studio and makes tests that need localhost unreachable.  
**Root cause:** `_resolve_base_url` checked `OPENCODE_BASE_URL` env BEFORE the explicit `base_url` parameter. Since `Agent()` always passes `config.LLM_BASE_URL` (always set from LM_HOST/LM_PORT defaults), `OPENCODE_BASE_URL` always won.  
**Fix (2 parts):**
1. `config.py`: `LLM_BASE_URL` now checks `OPENCODE_BASE_URL` env first — the OpenCode URL is baked into config at import time.
2. `llm_wrapper.py` `_resolve_base_url`: removed `OPENCODE_BASE_URL` env check entirely. Priority is now: explicit `base_url` param → `LM_BASE_URL` env → auto-construct from `LM_HOST`/`LM_PORT`. This makes tests pass clean base_url values without interference.

### 37. Test suite: `current_session_id` leaks between test files via module/instance attribute confusion (`tests/test_sse_streaming.py:12-17`, `tests/test_api.py:11-16`)

**Symptom:** Full test suite hangs for 3+ minutes at `test_sse_streaming.py`. SSE tests take 6s+ each instead of <0.1s.  
**Root cause (2 layers):**

**Layer 1 — Attribute confusion:** `session_manager.py:290` sets `current_session_id` as an **instance attribute** on the `SessionManager` singleton:
```python
session_manager.current_session_id = None  # instance attribute
```
When fixtures use `import session_manager; session_manager.current_session_id = None`, this creates a **module-level attribute** (separate from the instance attribute). `stream_execution.py` does `from session_manager import session_manager` (gets the INSTANCE), so `session_manager.current_session_id` reads the **instance** attribute — which was NEVER reset by the fixture. The fixture's module-level `current_session_id` shadows nothing because the instance attribute is never accessed via the module.

**Layer 2 — No cleanup:** `test_api.py`'s decompose tests set `current_session_id` (instance) to a real session ID and `agent.task_tree` to a real tree, but the fixture only sets `current_session_id = None` on the wrong target (module attr). State leaks into subsequent SSE tests.

**Fix (2 parts):**
1. `import session_manager; session_manager.current_session_id = None` → `from session_manager import session_manager; session_manager.current_session_id = None` (imports the INSTANCE, correctly resets the **instance** attribute).
2. Same fix in `test_api.py`'s fixture. Never use `import module; module.attr = value` when `attr` is an instance attribute, not a module-level variable.

**Rule:** Always check whether an attribute lives on the module or on an instance inside the module with `hasattr()`. `session_manager.current_session_id` is an instance attribute on `session_manager.session_manager` — access it via `from session_manager import session_manager`.

### 38. `model_manager` and `_ensure_model_loaded` guard for non-LM-Studio backends (`api_server.py:824-829,646`, `model_manager.py:34-56,78-84,87-116`)

**Symptom:** Tests and server try to call LM Studio model APIs (load/unload/list) when using OpenCode Go or other external LLM providers. Requests to wrong endpoints (e.g. `https://opencode.ai/zen/go/api/v1/models`) hang or fail.  
**Root cause:** `model_manager` functions (`get_loaded_models`, `get_available_models`, `get_all_rest_models`) and `_ensure_model_loaded` unconditionally connect to LM Studio. No check for whether the backend is actually LM Studio.  
**Fix (3 parts):**
1. `_ensure_model_loaded()`: returns early if `app.config["TESTING"]` is True OR if `config.LLM_BASE_URL` contains "opencode" or doesn't point to localhost.
2. `/api/models` endpoint: skips `model_manager` calls when `app.config["TESTING"]` is True OR OpenCode URL detected.
3. `model_manager.py`: `get_loaded_models()`, `get_available_models()`, `get_all_rest_models()` each return early (`None`/`[]`) when `os.environ.get('OPENCODE_BASE_URL')` is set. Belt-and-suspenders guard in case any code path calls them directly.

## Refactoring Convention

When extracting methods from `agent_core.py` into module files:
- Module-level functions: **no `_` prefix** (public module API), except for true internal helpers used only within the module
- Agent delegate methods: **keep `_` prefix** (private to the class)
- Example: `_record_outcome` on Agent → `record_outcome` in `agent_tree.py` + delegate in `agent_core.py`
- Always verify ALL `agent._*()` calls in the module have corresponding delegates before considering the refactor done

## Debugging Workflow

1. **Check server version:** `🕐 Startet:` in `flask_output.log` — confirms latest code
2. **Find error body:** `✗ HTTP 400: {"error":"..."}` — exact LM Studio complaint
3. **Check file_chunks:** `[DEBUG] file_chunks keys:` — are files loaded?
4. **Check images:** `/api/image/list` — are images in `agent.images`?
5. **Session analysis:** Read `.json` from `sessions/` folder

## Templates

| Template | Tools | Purpose |
|----------|-------|---------|
| `kodeanalyse` | list_chunks, read_chunk | Code analysis (read-only) |
| `programmering` | list_chunks, read_chunk, write_file, add_image | Design + implement code |
| `billedanalyse` | add_image, write_file, list_chunks, read_chunk | Image analysis → .md export |
| `agenten` | git tools + read_chunk | Git/GitHub PR workflow |

### 39. `locate_code` missed global variables (`agent_files.py:348-368,396-458`)
**Symptom:** LLM calls `locate(name='current_session_id')` — returns `"Symbol not found"`. LLM can't find the variable, gets stuck.
**Root cause:** `_build_global_symbol_index`, `_list_top_level_symbols`, and the name-search loop in `locate_code` only indexed/searched `FunctionDef`, `AsyncFunctionDef`, and `ClassDef` nodes. `ast.Assign` and `ast.AnnAssign` at module level were ignored.
**Fix:** Added `_list_top_level_vars(tree)` helper and integrated it into all three locations. `locate_code` now returns `"type": "variable"` with the full assignment line as body.

### 40. `_finalize_task_stream` auto-completes phases without required tools (`agent_tasks.py:306-345`)
**Symptom:** Session `3caff59c` — all 5 phases marked "done" but LLM never called `edit_file`/`write_file`/`update_issue_status`. Implementation phase "completed 6 tool calls (auto-completed)" with zero code written.
**Root cause:** No enforcement that required action tools were actually called. The `WRITE_REQUIRED` advisory in the prompt was ignored by the LLM. Three escape paths: (1) explicit `<<<DONE>>>` without checks, (2) `MAX_TOOL_CALLS` reached with only read tools, (3) text fallback on first iteration.
**Fix:** Added `_check_required_tools(agent, called_tools)` — checks if `{edit_file, write_file, update_issue_status}` are in `active_tools` but never called. Applied in both `_finalize_task_stream` (overrides status → failed) and the `<<<DONE>>>` handler (rejects with error message + retry). Added `K.LOG_REQUIRED_TOOLS_MISSING` i18n key in all 4 languages.

### 41. `create_issue` didn't validate function-name locations (`agent_issues.py:190-191`)
**Symptom:** Issue `BUG-063` had `location: api_server.py:_normalize_images` — but `_normalize_images` is a base64 converter that has nothing to do with `current_session_id`. The wrong location confused the LLM for 50%+ of its tokens.
**Root cause:** The location validation regex only matched `file.py:123` (line numbers). `file.py:FunctionName` format fell through to `resolved_parts.append(part)` without calling `locate_code` to verify the function exists.
**Fix:** Added a second regex match for `file.py:FunctionName` format, validates via `locate_code(filepath, name=sym_name)`, logs a warning if the function doesn't exist.

### 42. deepseek-v4-pro refuses to write/edit files — model training bias (`agent_tasks.py:132-134`)

**Symptom:** Session f502154d — 5 phases (Analysis, Plan, Extract, Update, Test). LLM called `list_files`, `read_chunk`, `list_chunks` extensively but NEVER called `write_file` or `edit_file`. Even with "⛔ YOU MUST write/edit code" warning + `write_file` in active tools, model reads and says Done.  
**Root cause:** `deepseek-v4-pro` is trained for analysis/reasoning, not code editing. It produces text analysis (in Done markers) but refuses to call write/edit tools. This is a model capability limitation, not a prompt issue.  
**Native tools verified:** DeepSeek API docs confirm function calling support. Agenten now properly sends `role:"tool"` messages with `tool_call_id` matching assistant `tool_calls`. No HTTP 400 errors. Model uses native tool calls for read tools but still refuses write_file/edit_file.  
**Fix:** Use `minimax-m2.5` for code-editing tasks (issue_handler, programming, refactoring). `deepseek-v4-pro` is fine for read-only analysis (kodeanalyse, resume). Other tested models: `minimax-m2.5` ✓ (edits code), `glm-5.1` ✓ (native tools).  
**Warning hardcoded fix:** Line 134 was hardcoded Danish "⚠️ DU SKAL redigere kode" — replaced with i18n key `K.WRITE_REQUIRED` in all 4 languages.

### 43. LLM task decomposition thinking leakage (`agent_tree.py:11-60`, `agent_tree.py:76-130`, `lang/*.json`)

**Symptom:** Session `8bb7f917` — task tree has 257 flat tasks that are the LLM's internal chain-of-thought ("Okay, the user wants me to...", "First, I need to...", "I should identify..."). Model `nvidia/nemotron-3-super` outputs thinking despite "Return ONLY the tree structure" instruction.

**Root cause:** `_clean_task_name()` meta-prefix regex and `parse_tree_from_llm()` skip-words didn't catch self-referential thinking patterns (I, let me, we, my, but, perhaps, first, so, now, looking at, another way, final decision, etc.).

**Fix (4 parts):**
1. **Prompts** (`lang/da.json`, `en.json`, `es.json`, `zh.json`): "fri" template now says "INGEN forklaringer, INGEN tankeproces, INGEN forberedelse — KUN selve træet" (stronger anti-thinking emphasis)
2. **Language instruction** (`agent_skills.py:203-270`, `lang/*.json`): Added `{lang_instruction}` to ALL 12 template prompts (was only in 4). Added `.replace("{lang_instruction}", lang_instr)` for all templates in `get_templates()`. LLM now always told "Svar på dansk" / "Answer in English" etc.
3. **`_clean_task_name()`** (`agent_tree.py:27-57`): Added ~40 new meta-prefix patterns organized by category (self-referential, meta-commentary, hedging, evaluations, ordinals, transitions). Added single-word-colon filter (`^\w+\s*:\s*$`).
4. **`parse_tree_from_llm()`** (`agent_tree.py:100-130`): Added flat-task heuristic — if >20 level-1 tasks with no children, LLM leaked thinking → use `create_fallback_tree()`. Added `"i'll", "i'm", "i've", "i'd"` to skip_words.

### 44. Auto-resolution missed already-fixed bugs when LLM hit tool call limit (`agent_tasks.py:472-491`)

**Symptom:** Session `88a11e66` — all 5 phases executed (all failed/done) despite the bug already being fixed. Analysis phase correctly identified "already fixed" but wasted all 6 tool calls re-reading the same code, hit tool call limit, and got the generic "Gennemførte 6 værktøjskald" message as `full_response` — which doesn't match `AUTO_RESOLVE_PATTERNS`.

**Root cause:** `_finalize_task_stream` only checked `full_response` text for auto-resolve patterns. When the tool call limit was reached, `full_response` was overwritten with the generic auto-done message, losing the LLM's actual analysis conclusion. The auto-resolution was in `elif task_node.status == "done":` block — but `_check_required_tools` (before phase-aware fix) ran first and set status to "failed".

**Fix (2 parts):**
1. Phase-aware `_check_required_tools()` — `update_issue_status` removed from required tools for Analyse/Læs/Afklar phases (already done in prior fix).
2. `_finalize_task_stream` now also scans ALL assistant messages' text content for `AUTO_RESOLVE_PATTERNS`, not just `full_response`. If a match is found in assistant messages, auto-resolution triggers.

**Files:** `agent_tasks.py:472-491`

### 44. Stale/placeholder test files from failed sessions must be cleaned up

**Symptom:** Session `ab48039c` — BUG-095 task `create_test_file` phase wrote `tests/test_execution_status_sse.py` with empty `pytest.raises` stubs (no real assertions). After the session failed, the file remained and caused 4 test failures (`pytest.raises(None)` — no actual exception raised).

**Root cause:** The LLM wrote placeholder test stubs because it couldn't complete the real implementation (wrong issue location, empty `old_text`, missing tools in phase). The file was never validated as a real test — just accepted if it looked like a test file.

**Fix (2 parts):**
1. Temp tests go in `tests/temp/` — automatically excluded from the default `run_tests()` suite via `--ignore=tests/temp`. Run them explicitly with `run_tests(test_path='tests/temp/test_...')`.
2. After any failed session, scan `tests/` (not `tests/temp/`) for leftover placeholder files.

**Rule:** Do not keep test files written by the LLM unless verified they pass correctly. Placeholder tests are worse than no tests. Temp tests in `tests/temp/` are for experimentation — once validated, they can be moved to `tests/`.

### 45. Auto-load location-file into file_chunks (`agent_core.py`)

**Symptom:** Session `feba9e32` — `file_chunks` is empty when prompt contains `Location: git_ops.py:edit_file`. LLM wastes 6+ iterations calling `list_chunks`/`list_files` trying to find the file, eventually fails with "Manglende påkrævede værktøjer: edit_file".

**Root cause:** No mechanism to auto-load the file specified in `Location:` field. `_auto_load_issue_files()` only handles issue IDs (BUG-xxx), not general location references. `_build_file_context()` scans folders but doesn't parse `Location:` from prompt.

**Fix:** Added `_auto_load_location_file(agent, prompt)` in `agent_core.py`:
- Parses `Location: filename.py:symbol` from prompt using regex
- Extracts filename using `_extract_filenames()`
- Loads file content via `agent._read_file_content()`
- Stores in `agent.file_chunks` as `file_{filename}` with chunks
- Called after `_auto_load_issue_files()` in `decompose()` method
- Early exit if `file_chunks` already populated (e.g., from session restore)

**Pattern:** Always include `Location: file.py:function` in prompts — agent now auto-loads it.

**Files:** `agent_core.py:82-103` (new function), `agent_core.py:632` (call site)

### 46. Full LLM response logging to separate files (`agent_tasks.py`)

**Symptom:** Session JSON `agent_log` entries have truncated `detail` fields (500 chars for tool calls, 400 for done, 600 for errors). Full LLM output is lost.

**Root cause:** `solve_task_stream()` truncated LLM responses before logging to save memory/session file size.

**Fix (4 files):**
1. `agent_tasks.py`: Added `_save_llm_log_file(agent, task_name, iteration, content)` — saves full LLM output to `logs/llm_responses/{session_id}/{task}_iter{N}.txt`
2. `agent_tasks.py`: Removed all `[:500]`, `[:400]`, `[:600]` truncations in `solve_task_stream()` logging. Added `log_file` parameter to `agent._log()` calls.
3. `agent_core.py`: Updated `_log()` to accept optional `log_file` parameter — includes it in `agent_log` entry when provided.
4. `api_server.py`: Set `stream_agent._session_id = current_session_id` so log files are organized by session.

**Result:** `agent_log.detail` contains full untruncated text. `agent_log.log_file` points to the separate text file with complete content including newlines.

**Files:** `agent_tasks.py:29-43` (helper), `agent_tasks.py:768-787` (logging), `agent_core.py:470-485` (_log), `api_server.py:1200` (_session_id)

### 47. Refactor template: LLM re-does earlier phases because of cross-phase reasoning (`agent_tasks.py:273-301`, `agent_skills.py:124`)

**Symptom:** Session `a07633cc` — Ekstraher phase wrote to `refactor_plan.md` 4 times instead of creating `.py` files. LLM reasoning: *"Planen findes allerede! Det er godt - det betyder at Analyse-fasen er gennemført og planen er lavet. Nu skal jeg gå videre til **Ekstraher-fasen**."* — the LLM was IN Ekstraher but thought it still needed to do Plan.

**Root cause:** The section instruction contains the full workflow description ("Plan → Ekstraher → Opdatér → Test") and the LLM treats it as a sequential guide rather than a per-phase rule. Plus: Ekstraher's section said "Læs INGENTING før du skriver" which prevented it from reading `refactor_plan.md` (where the actual module list lives).

**Fix (3 parts):**
1. **Phase anchor** (`agent_tasks.py:292-296`): Every phase's system prompt now starts with `🎯 NUVÆRENDE FASE: {phase_name}` + `⛔ Du er KUN i denne fase. Udfør IKKE andre faser`. LLM can no longer reason about "what should I do next phase" because it's told other phases are not its concern.
2. **Auto-load `refactor_plan.md`** (`agent_tasks.py:273-290`): For `refactor` template's `Ekstraher` phase, reads `refactor_plan.md` from disk and injects it as `plan_block` (capped at 3000 chars) using `K.REFACTOR_PLAN_LOADED` i18n key. LLM now sees the actual module list (routes.py, session_manager.py, etc.) instead of guessing.
3. **Fixed Ekstraher section instruction** (`agent_skills.py:124`): Removed "Læs INGENTING før du skriver" — replaced with "FØRSTE handling SKAL være write_file med et NYT filnavn (aldrig et filnavn der allerede findes)". LLM can now read context but must still create new files first.

**New i18n keys:** `K.PHASE_CURRENT`, `K.PHASE_ONLY`, `K.REFACTOR_PLAN_LOADED` (all 4 languages).

**Files:** `agent_tasks.py:273-301`, `agent_skills.py:124`, `i18n.py:343-345`, `lang/{da,en,es,zh}.json`

### 48. Deterministic phase auto-advance: `agent_phase_checks.py` (new), `agent_tasks.py:629-680`, `agent_skills.py:122-127`

**Symptom:** Session `a07633cc` (round 2) — LLM made 6+ redundant `write_file(refactor_plan.md)` calls in Ekstraher phase. The phase should have ended as soon as Plan wrote the plan, and as soon as Ekstraher created all the modules the plan listed. But the LLM kept looping because nothing told it "you're done".

**Root cause:** Phases are LLM-driven — the LLM has to declare `<<<DONE>>>` when it thinks the phase is complete. There's no system-level check that says "Plan is complete because refactor_plan.md exists" or "Ekstraher is complete because all .py files from the plan exist on disk".

**Fix (4 parts):**

1. **New module `agent_phase_checks.py`** with two check types:
   - `file_exists` — one or more files must exist on disk (with `require_all` option)
   - `files_from_plan` — parses a markdown plan, extracts module names (heading or inline patterns), requires all of them to exist. Handles Windows paths, ignores paths with separators, supports custom extensions.
2. **Template config `TEMPLATE_PHASE_CHECKS`** (`agent_phase_checks.py:128-141`): Maps `(template, phase_name) → check spec`. Currently only `refactor` template is configured:
   - `Plan` → `file_exists(refactor_plan.md)`
   - `Ekstraher` → `files_from_plan(refactor_plan.md, ext=.py)`
3. **Integration in `_get_phase_auto_complete_msg`** (`agent_tasks.py:629-680`): After the existing `run_tests` and `update_issue_status` checks, runs the template's deterministic phase check. The check fires after any productive tool call (`write_file`, `edit_file`, `run_tests`, `update_issue_status`). On pass, returns `t(K.PHASE_AUTO_ADVANCED, lang).format(reason=...)` which causes `solve_task_stream` to break out of its loop and move to the next sibling phase.
4. **Section instruction updates** (`agent_skills.py:122-127`): Plan and Ekstraher section instructions now say "Systemet auto-afslutter denne fase så snart [kriterium] er opfyldt — du behøver IKKE lave yderligere kald bagefter". LLM is told explicitly that extra calls are wasted.

**New i18n key:** `K.PHASE_AUTO_ADVANCED` (all 4 languages) — shown in agent log when phase auto-completes.

**Tests:** `tests/test_phase_checks.py` — 23 tests covering: file_exists (require_all True/False), module extraction (heading, inline, paths ignored, custom ext), files_from_plan (all/missing/empty/min_files), check_phase_done (template lookup, phase name case-insensitivity, no-template fallback), TEMPLATE_PHASE_CHECKS config validation.

**Verified via smoke test** (`C:\Dev\Agenten`): With actual `refactor_plan.md` listing 10 modules, creating the 7 missing .py files triggered Ekstraher auto-complete: "✅ Fase auto-afsluttet: files_from_plan: alle 10 moduler fra C:\Dev\Agenten\refactor_plan.md findes". Plan phase auto-completes as soon as `refactor_plan.md` is written.

**Files:** `agent_phase_checks.py` (new, 175 lines), `agent_tasks.py:629-680` (extended `_get_phase_auto_complete_msg` with deterministic checks), `agent_skills.py:122-127` (section instructions mention auto-advance), `i18n.py:347`, `lang/{da,en,es,zh}.json`, `tests/test_phase_checks.py` (new, 23 tests)

### 49. Phase checks visible in UI under each phase (`api_server.py:1549-1606`, `static/index.html:1083-1101,1170-1202,2230-2262,2300-2303`)

**Symptom:** Even though phases have deterministic success criteria, the user can't see them in the tree view. The LLM has them in its prompt but the human operator has no visibility into "what triggers auto-advance?".

**Fix (3 parts):**
1. **New `/api/phase-checks` endpoint** (`api_server.py:1549-1606`): Returns the `TEMPLATE_PHASE_CHECKS` config for a template, with each phase augmented by a human-readable Danish `description` (e.g. *"Fasen afsluttes automatisk når filen `refactor_plan.md` findes"*). Supports both `?template=refactor` and bare `/api/phase-checks` (all templates).
2. **Frontend caching + render** (`static/index.html:1083-1101,1170-1202`): New `loadPhaseChecks(template)` async function caches the response. `renderTree` looks up each node's name (case-insensitive) in the cache and renders `✓ <description>` in green below the existing `success_criteria` text.
3. **Template tracking** (`static/index.html:1095-1101,2096-2110,2300-2303`): New `currentTemplate` global. Updated on `switchTemplate()`, session load, and successful decompose. `loadPhaseChecks` is awaited before `updateTreeDisplay` so the cache is populated when the tree renders.

**Visual example (refactor template, Plan phase):**
```
📌 Plan [○]
  • Modulstørrelse ≤ 300 linjer
  ✓ Fasen afsluttes automatisk når filen `refactor_plan.md` findes.
```

**Tests:** `tests/test_api.py:148-191` — 5 new tests for `/api/phase-checks` (all templates, specific template, unknown template, Plan check format, Ekstraher check format).

**Files:** `api_server.py:1549-1606` (endpoint + `_format_phase_check_description` helper), `static/index.html:1083-1101,1170-1202,2230-2262,2300-2303` (caching + render + tracking), `tests/test_api.py:148-191` (5 tests)

### 50. `_check_required_tools` overrides Test-phase auto-completion from `run_tests` (`agent_tasks.py:600-603`)

**Symptom:** Session `a07633cc` — Test phase ran `run_tests()` → all 461 tests passed → log says *"✅ Rød test bestået — fejlen er allerede rettet. Afslutter Test (Red) fasen."* → then immediately the phase is marked **failed** with *"Manglende påkrævede værktøjer: edit_file"*. UI shows: `[02-06-2026 12:16:46] ERROR: task_failed_label Test`.

**Root cause:** Two bugs colluding:
1. `_check_required_tools` ignores `agent.issue_resolved` — even though the run_tests auto-complete handler at `_get_phase_auto_complete_msg:634-638` sets `agent.issue_resolved = True`, the finalizer at line 737-740 unconditionally calls `_check_required_tools` and overrides `status = "failed"`.
2. `CLOSE_PHASE_ALIASES = {"opdatering", "luk", "close"}` did NOT include the Danish verb form `"opdatér"` used by the refactor template — so `update_issue_status` was wrongly removed from required for the refactor's Opdatér phase.

**Fix (2 parts):**
1. `_check_required_tools` (`agent_tasks.py:601-603`): If `agent.issue_resolved` is True, drop `edit_file`/`write_file` from required (the bug is auto-resolved by passing tests).
2. `CLOSE_PHASE_ALIASES` (`agent_tasks.py:569`): Added `"opdatér"` so the refactor template's close phase correctly requires `update_issue_status`.

**Bonus fixes:**
- Added missing i18n key `task_failed_label` to all 4 lang files (was previously displayed as the raw key in browser console).
- Reset REFAC-001 issue status from `resolved` to `open` — was marked resolved with a misleading "Test phase confirmed bug is already fixed" message from a prior failed session, but the refactor was never actually done (7 of 10 modules still missing).

**Tests:** `tests/test_check_required_tools.py` — 7 new tests covering: Test phase respects issue_resolved, Ekstraher still requires write_file, Opdatér still requires update_issue_status, Verifikation phase allows optional edit_file, update_issue_status call clears edit_file requirement.

**Files:** `agent_tasks.py:568-602`, `lang/{da,en,es,zh}.json`, `tests/test_check_required_tools.py`

### 51. Refactor template needs higher iteration budget (91 symbols → 7 modules) (`agent_skills.py:42-66`, `agent_tasks.py:96-114`)

**Symptom:** Session `a07633cc` Ekstraher phase hit `MAX_TASK_ITERATIONS = 6` after only reading the file structure, before creating ANY module. Opdatér hit the same cap before making any meaningful edits. With 91 symbols to move into 7 modules, the LLM needs ~15-25 turns to do the work.

**Root cause:** Hard-coded `MAX_TASK_ITERATIONS = 6` in `config.py` is the bottleneck — `solve_task_stream` loops at most 6 times. This is fine for simple bugfix tasks (read issue + edit + test) but way too low for refactor workflows that need to create many new files.

**Fix (3 parts):**
1. **New `TEMPLATE_PHASE_ITERATION_LIMITS` dict** (`agent_skills.py:42-66`): Per-template/per-phase overrides. Refactor: `Analyse=4, Plan=4, Ekstraher=15, Opdatér=12, Test=8`. Bugfix: `Analyse=6, Test (Red)=6, Implementering=12, Verifikation (Green)=8, Opdatering=4`.
2. **New `_get_max_iterations(agent, task_name)`** (`agent_tasks.py:96-114`): Looks up per-template override (case-insensitive phase match), falls back to `MAX_TASK_ITERATIONS` (or `MAX_PR_TASK_ITERATIONS` for PR workflows). Replaces the previous hardcoded config check at `agent_tasks.py:851`.
3. **Motivating prompts** (`agent_skills.py:124,126`): Ekstraher and Opdatér section instructions now start with 🔥 EFFEKTIVITETSGUIDE that tells the LLM:
   - "Du har KUN 15 iterations" (explicit budget)
   - "brug list_symbols FØRST for at se ALLE symboler" (batch reads)
   - "skriv HELE modulet på ÉN gang" (full modules, not piecemeal)
   - "skriv hellere et modul med stubs end at bruge alle iterations på research" (don't over-research)

**Why lower limits work despite the workload:** Auto-advance from `agent_phase_checks.py` (entry 48) ends the phase as soon as the criterion is met (all .py files exist). So the LLM just needs to grind through module creation — the system will stop it as soon as work is done. The tight budget + auto-advance combo rewards decisive action.

**Tests:** 9 new tests in `tests/test_check_required_tools.py::TestGetMaxIterations` covering all refactor/bugfix phases, case-insensitive lookup, unknown template/phase fallback, PR workflow override. Total 477 tests pass.

**Files:** `agent_skills.py:42-66` (TEMPLATE_PHASE_ITERATION_LIMITS), `agent_tasks.py:96-114,851` (_get_max_iterations + call site), `tests/test_check_required_tools.py:9-25,140-203`

### 52. Refactor template: false auto-resolve and `.md` read_location crash (`agent_files.py`, `agent_phase_checks.py`, `agent_tasks.py`, `i18n.py`)

**Symptom:** Session `175d41a5` — REFAC-001 reported as `resolved` without any code being moved. Three bugs:
1. **Plan-fasen auto-advance med stale fil**: `file_exists("refactor_plan.md")` var sandt fordi filen fandtes fra en tidligere session — LLM'ens `write_file` fejlede ("Filen findes allerede"), men auto-complete tjekkede kun eksistens, ikke om planen var opdateret.
2. **Opdatér-fasen låst i læse-loop**: LLM brugte 12 iterationer på `read_location("refactor_plan.md", "refactor_plan")` som fejlede med `Syntax error: invalid character '←' (U+2190)`. `read_location`/`locate_code` parser ALTID filen som Python AST, og markdown-filens `←`-tegn fra afhængighedsdiagrammet giver en syntaksfejl. LLM forsøgte aldrig `read_chunk` i stedet.
3. **Test-fasen auto-resolver falskt**: 536 tests bestod fordi `api_server.py` aldrig var ændret. Test-fasen satte `agent.issue_resolved = True` og markerede issuet som løst. Falsk positiv.

**Root cause:** Tre svage checks:
- `file_exists` for Plan: tjekker kun eksistens, ikke indhold
- `locate_code` parser ikke-Python filer som AST, fejler forvirrende
- `tests_pass` for Test-fasen kræver ikke at refactoren reelt er udført

**Fix (3 dele):**

1. **Stærkere Plan-fase check** (`agent_phase_checks.py:527-530`): Skiftet fra `file_exists(refactor_plan.md)` til `files_from_plan(plan_path="refactor_plan.md", min_files=5)`. Kræver nu at planen indeholder mindst 5 `*.py`-modulnavne OG at de findes. Forhindrer stale-plan godkendelse.

2. **Tydelig fejl for ikke-Python filer** (`agent_files.py:572-578, 630-637`): `locate_code`, `read_location`, og `list_symbols` tjekker nu `filepath.lower().endswith('.py')` før AST-parsing. Returnerer `"locate_code understøtter kun Python-filer (.py), fik 'plan.md'. Brug read_chunk eller list_chunks for andre filtyper."` i stedet for `Syntax error: invalid character '←'`.

3. **Forhindret falsk auto-resolve i Test-fase for refactor** (`agent_tasks.py:78-130, 718-728`): Ny helper `_refactor_actually_moved_code(agent)` der for refactor-template tjekker om mindst ét af modulerne nævnt i `refactor_plan.md` faktisk indeholder flyttet kode (def/class med >20 linjer). Hvis ikke, sætter den IKKE `issue_resolved = True` og returnerer advarsel `K.TEST_BUT_NO_REFACTOR` der fortæller LLM at redigere filerne manuelt. For ikke-refactor templates returnerer den `True` som før.

**Ny i18n key:** `K.TEST_BUT_NO_REFACTOR` i alle 4 sprog (da/en/es/zh).

**Tests:** 11 nye tests i `tests/test_session_175d41a5_fixes.py` dækker:
- `_refactor_actually_moved_code`: 5 tests (bugfix/kodeanalyse altid True, refactor ingen moduler → False, refactor kun stub → False, refactor real kode → True)
- `read_location`/`locate_code`/`list_symbols` på `.md`/`.txt` filer: 5 tests (alle giver klar fejl)
- Plan-fase bruger `files_from_plan` med `min_files=5`: 1 test

Opdaterede eksisterende tests i `tests/test_phase_checks.py` (3 tests: `test_plan_phase_passes_when_file_exists`, `test_case_insensitive_phase_match`, `test_refactor_plan_check`) til at matche nyt kriterium.

**Resultat:** 548 tests passerer (537 eksisterende + 11 nye).

**Files:** `agent_files.py:572-578, 630-637`, `agent_phase_checks.py:527-530`, `agent_tasks.py:78-130, 718-728`, `i18n.py:177`, `lang/{da,en,es,zh}.json:213`, `tests/test_session_175d41a5_fixes.py` (ny, 11 tests), `tests/test_phase_checks.py:175-200, 224-227` (opdateret 3 tests)

### 53. 175d41a5: Længere refactor-faser ignorerer write-tools (qwen3.5-122b) (`agent_tasks.py`, `agent_phase_checks.py`)

**Symptom:** Session `175d41a5` — Ekstraher og Opdatér faser begge failede med "Manglende påkrævede værktøjer: write_file" / "edit_file" SELVOM begge var i `active_tools`. LLM brugte 12-15 iterationer i dedup-loop uden at kalde et eneste skrive-værktøj. Test-fasen auto-resolvede REFAC-001 fordi 548 tests passed — men intet var reelt refaktoreret (api_server.py voksede 1521→1857 linjer).

**Root cause:** Tre svagheder i entry 52's fikser:
1. `_refactor_actually_moved_code` krævede kun ≥1 modul med reel kode — `security.py` havde reel `_RateLimiter` klasse → returnerede True → falsk auto-resolve
2. Ingen mekanisme til at bryde dedup-loop (LLM ignorerede "Du har allerede dette resultat")
3. `_check_required_tools` tjekkede kun EFTER hele iteration-budget var brugt

**Fix (3 dele):**

1. **Stærkere refactor-check** (`agent_tasks.py:80-130`, `agent_phase_checks.py:67-110`): Ny `_refactor_actually_moved_code` kræver:
   - ALLE moduler i `refactor_plan.md` eksisterer
   - Hver modul har reel kode (`def ` eller `class ` med ≥20 linjer) via ny `_has_real_code(min_lines=20)`
   - `api_server.py` er reduceret til < 1000 linjer
   - Nye helpers i `agent_phase_checks.py`: `_parse_refactor_plan_modules()` (regex-baseret), `_has_real_code(min_lines=20)`

2. **Dedup-loop escape** (`agent_tasks.py:915, 1006-1024`): Track `consecutive_dedups` tæller. Ved 3+ "Du har allerede dette resultat" i træk, inject system-reminder: "STOP med at læse. BRUG et værktøj der SKRIVER: write_file, edit_file". Resets efter reminder eller efter write-kald. Ny `agent._current_task_iteration` attribut spores parallelt.

3. **Tidlig write-check i refactor** (`agent_tasks.py:635-655`): `_check_required_tools` trigger tidlig abort hvis:
   - Template er `refactor`
   - Task er `Ekstraher`/`Opdatér` (matcher `ekstraher`/`opdat` substrings)
   - Ingen `write_file`/`edit_file` kaldt
   - `agent._current_task_iteration >= 3`
   - Reset counter når ny task starter eller write-tool kaldt

**Ny i18n key:** `K.REFACTOR_INCOMPLETE` (alle 4 sprog) — "Refaktoreringen er ufuldstændig. {missing_count} modul(er) mangler stadig, eller api_server.py er ikke reduceret til under 1000 linjer."

**Tests:** `tests/test_session_175d41a5_phase2.py` (22 nye tests) + 2 opdaterede tests i `test_session_175d41a5_fixes.py`:
- Parser: finder alle listede moduler, håndterer tom/ingen fil, dedup
- `_has_real_code`: skelner stub vs reel kode (def/class, min_lines, eksisterende fil)
- `_check_required_tools`: refactor fejler ved iter 3 uden write, passerer med write; iter 1-2 ikke påvirket; ikke-refactor templates urørt
- `_refactor_actually_moved_code` integration: alle moduler + lille api → True; manglende modul → False; kun stubs → False; api_server.py > 1000 linjer → False
- i18n key findes i da/en/es/zh

**Cleanup efter implementation:**
- Nulstil REFAC-001 i `issues.json`: `status: "resolved"` → `"open"`
- Slet `routes.py` (707 bytes stub, ingen reel kode)
- Behold `security.py` (3666 bytes, reel `_RateLimiter` klasse flyttet)
- Behold `session_manager.py` (10451 bytes, eksisterende kode)

**Verifikation:** Genkør session 175d41a5 → Ekstraher skal fejle med Fix C's besked efter 3 iterationer i stedet for 15. Test-fase skal IKKE auto-resolve (Fix A fanger manglende moduler).

**Resultat:** 572 tests passerer (548 baseline + 24 nye/opdaterede). 0 eksisterende tests brydes.

**Files:** `agent_phase_checks.py:67-110` (2 nye helpers), `agent_tasks.py:80-130` (rewritten check), `agent_tasks.py:635-655` (early-abort logik), `agent_tasks.py:915-924, 1006-1024` (dedup tracking), `i18n.py:179` (ny KEY), `lang/{da,en,es,zh}.json:215` (ny key), `tests/test_session_175d41a5_phase2.py` (ny, 22 tests), `tests/test_session_175d41a5_fixes.py:65-105` (2 opdaterede tests)

### 60. `_extracted_registry` hash-clearing makes `_is_already_extracted` always return False

**Symptom:** `batch_extract_symbols` creates duplicate symbol definitions in target files when called more than once for the same source. Symbols remain in the source file even though `move_symbol` reports success.

**Root cause (refactoring_engine.py:209-227):** `_registry_key()` used file SHA-256 hash to detect source-file reverts. Every `remove_symbol` call modifies the source file, changing its hash. The next `_registry_key()` call sees a new hash and **clears the entire registry** for that file. This means:
1. `_is_already_extracted()` always returns False after the first extraction
2. `batch_extract_symbols` re-extracts all symbols on every call → duplicates in target

**Fix (3 parts):**
1. **Remove hash-clearing from `_registry_key()`** — now just returns `os.path.abspath(source)`. Registry persists during the session without self-invalidation.
2. **Add `_symbol_exists_in_target()` check in `move_symbol()`** — before extracting, checks via AST if the symbol already exists in the target file. If it does, skips the extract step (preventing duplicates) but still runs `remove_symbol` and `add_import`. Makes `batch_extract_symbols` fully idempotent.
3. **Clear registry at session start** — `create_session()` in `api_server.py` calls `clear_extracted_registry()` to prevent stale entries from previous sessions.

Also removed unused `_registry_source_hashes` global dict. Added public `clear_extracted_registry(source=None)` function.

**Files:** `refactoring_engine.py:201-240,1149-1310`, `api_server.py:25,473`

## Model Knowledge

See `skills/vision_models.md` for full vision model compatibility matrix.  
Key takeaway: Gemma requires raw_b64 + images-before-text. Qwen/GPT use data_url.

### 5. Autoresearch: FAILURE_INCOMPLETE + auto-resolve af CORE-issues

**`agent_autoresearch.py`: `FAILURE_INCOMPLETE`** — ny failure-type for når
en refactor Ekstraher/Opdatér fase løber tør for iterationer før alle
moduler er oprettet.

**Detektion** i `classify_failure()`:
- Tjekker `active_template == "refactor"` og fase `ekstraher`/`opdatér`
- Parser `refactor_plan.md` for moduler via `_parse_refactor_plan_modules()`
- Sammenligner med hvad der findes på disk
- Returnerer `FAILURE_INCOMPLETE` med `modules_planned`, `modules_created`,
  `missing_modules`, `all_modules` i evidence

**Fix-proposal** i `_build_issue_fix()` inkluderer:
- Dynamisk iteration budget i `_get_max_iterations()`: `max(current_budget, 2 + num_modules * 2 + 5)`
- System-besked når todos auto-opdateres
- Fjern 'Brug update_todo' fra `instructions/refactor.json`

**Auto-resolve af CORE-issues**: `_check_issue_fix_applied()` verificerer
programmatisk om et issues fix allerede er implementeret:
- `FAILURE_INCOMPLETE`: AST-scan af `agent_tasks.py` efter `_get_max_iterations`
  med `_parse_refactor_plan_modules` + `refactor_plan.md` i koden
- Hvis fix er implementeret → `update_issue_status(issue, "resolved")`
- Hvis fix ikke er implementeret → ignorer duplicate (ingen ny research)

**`da_labels` i `_find_duplicate_issue()`**: `incomplete` genkendes på
danske keywords ("ufuldstændig", "manglende moduler", "ikke alle moduler").

### 5. Commit ALTID før test

**Regel:** `git add` + `git commit` ALLE ændringer **før** du beder brugeren om at starte serveren eller teste. Serveren auto-stasher uncommitted ændringer ved fejl — de går tabt. Sig aldrig "prøv nu" med ucommittede ændringer.

### 5. Brug `git restore` — aldrig `git checkout --`

**Hændelse (2026-06-17):** `git checkout -- config.py` virkede ikke pga. CRLF/LF forskelle på Windows. `config.py` forblev modificeret, serveren kunne ikke starte.

**Regel:**
- Brug ALTID `git restore <file>` i stedet for `git checkout -- <file>`
- `git checkout` kan ikke håndtere CRLF/LF normalisering på Windows — `git restore` kan
- Eksempel: `git restore api_server.py session_manager.py` i stedet for `git checkout -- api_server.py session_manager.py`

## Operational Principles

### 1. Verify before deploying — never assume project architecture

**Hændelse (2026-06-07):** Efter at have rettet `tools.py`, `agent_tasks.py` m.fl. i Agenten, kopierede agenten filerne med `cp` til OCRScanner fordi en session-fil lå i OCRScanners sessions-mappe. Agenten antog at OCRScanner kørte Agenten-frameworket — men OCRScanner var et standalone Flask-projekt (`app.py`) der slet ikke importerede Agenten.

**Regel:**
- Bekræft ALTID at et projekt reelt importerer/afhænger af en kodebase før du deployer filer derhen
- Tjek imports (`head -5 *.py`) — importerer `agent_core` eller `agent_tasks`? Hvis nej, hører filerne ikke til
- En session-fil i en mappe betyder IKKE at frameworket kører der
- Når du er i tvivl: spørg brugeren før du kopierer

### 2. Indrøm fejl med det samme — aldrig spin

**Hændelse (samme):** Da brugeren påpegede fejlen, prøvede agenten først at argumentere for at filerne "allerede fandtes" — baseret på et `[ -f ]` check der slog udslag fordi `cp` lige havde oprettet dem. Det var en dårlig analyse der forsinkede den ærlige indrømmelse.

**Regel:**
- Hvis du har lavet en fejl, sig det med det samme — uden forbehold
- Brug ikke tekniske checks til at "bevise" at fejlen ikke skete
- En hurtig og ærlig "det var en fejl, beklager" er altid bedre end en lang forklaring

### 3. Commit før du starter api_server.py

**Hændelse (2026-06-07):** Ændringer i agent_tasks.py, config.py, agent_skills.py m.fl. blev auto-stash'et til `stash@{0}` hver gang serveren fejlede. Det skete fordi serverens error-håndtering stasher ikke-committede ændringer før genstart. Flere forsøg gik tabt.

**Regel:**
- `git add` og `git commit` ALLE ændringer før du starter `python api_server.py`
- Tjek med `git status --short` at working tree er rent
- Hvis du glemmer det og ændringer forsvinder: `git stash pop stash@{0}` bringer dem tilbage

### 4. Non-deterministic LLM behaviour — Formål/importer faser fejler intermittently

**Symptom:** Samme kode, samme prompt — nogle gange lykkes Formål (skriver til docs/), andre gange fejler den med "Manglende påkrævede værktøjer: write_file".

**Root cause:** LLM'en vælger nogle gange at læse funktioner én ad gangen (1 iteration per read), andre gange i parallel (3-4 reads i samme iteration). Ved 1-per-iteration bruges alle 6-8 iterationer på læsning, og write_file når aldrig at blive kaldt.

**Løsning (flere lag):**
| Lag | Hvad | Status |
|-----|------|--------|
| Iteration limits | Øget 6→8 (agent_skills.py) | ✅ Implementeret |
| Tool call limits | MAX_TOOL_CALLS_ANALYSE 6→10 (config.py) | ✅ Implementeret |
| Batch-read instruktion | "Send FLERE read_location-kald på én gang" | ✅ Implementeret |
| Read-loop escape | System-tvang ved 5+ consecutive reads | ✅ Implementeret |
| Skill-flow kendte fejl | Dokumenteret i skills/kodeanalyse.md | ✅ Implementeret |
| Per-fase iteration tracking | SkillFlow sporer success/failure per skill | ✅ Aktiv (84% success for kodeanalyse) |

**Hvad mangler:** Hvis LLM ignorerer read-loop escape (ses i log som "STOP med at læse" + alligevel endnu et read), er der ingen mekanisme til at tvinge den til at skrive. En mulig forbedring: bryd loopet og inject write-påbud som system-role i stedet for user-role.

**Opdatering 2026-06-14:** Read-loop escape fjernet. Tidligere pruned systemet ALLE read-tools efter 3x samme værktøj (selv med forskellige funktionsnavne). Nu: advarsel ved 5x, men værktøjer forbliver tilgængelige. LLM kan altid læse mere hvis nødvendigt — advarsel er nok til at bryde loopet.

**Monitorering:** Brug SkillFlow `/skillflow` rapporten til at følge success rate over tid.

## WTA: select_winner fjernet, rank_tool_calls tilføjet

**Ændring 2026-06-08:** `select_winner` var dead code — aldrig kaldt i production. Designet til at vælge én tool-call blandt multiple kandidater, men native function calling returnerer ALLE kald LLM'en vil lave, ikke konkurrerende alternativer. `_exploration_roll` og `EXPLORATION_RATE` fulgte med.

**Erstattet af `rank_tool_calls(template, phase, tool_calls, max_calls)`** (`agent_wta.py:65-100`):
- Scorer hvert tool-kald med Laplace success rate per `(template, phase)`
- Sorterer højeste score først — de mest pålidelige værktøjer kører først
- `max_calls` kan begrænse antallet (endnu ikke brugt i production, men param findes)
- Integreret i `solve_task_stream()` (`agent_tasks.py:1325-1331`) — kører efter LLM returnerer `pending_tc`, før execution

**Hvorfor dette er bedre:**
- Data indsamles via `record()` (kaldt for alle tools) → reorderer dynamisk
- Logges kun når rækkefølgen faktisk ændrer sig (`WTA: Reordered: ...`)
- Ingen bypass-logik (write_file beats alt i gamle select_winner) — nu ren score-baseret

**Tests:** 5 nye `rank_tool_calls` tests, 6 gamle `select_winner` tests fjernet.

### 54. Test (Red) phase false positive: mocking tests hide bugs (`agent_tasks.py`, `agent_phase_checks.py`)

**Symptom:** Session `bc3b4f38` — StarBrowser BUG-001 (AttributeError on `AA_EnableHighDpiScaling`). Test (Red) phase wrote a test using `unittest.mock.patch('PyQt6.QtCore.Qt.ApplicationAttribute')` — the mock hid the missing attribute. The test **passed** (1 passed in 0.11s) but the bug was never fixed. System auto-resolved the issue and skipped all remaining phases.

**Root cause:** No detection of "cheating" tests. A test that mocks the error site will always pass regardless of whether the bug is fixed. The "Red test passed" heuristic assumes a legitimate fix, but mocking creates false positives.

**Fix (3 parts):**
1. **`write_file` overwrite protection** (`git_ops.py:380-430`): Added content-size guard — when overwriting existing files (>200 bytes) with very small content (<50 bytes), rejects with instruction to use `edit_file` or `overwrite="force"`. Also guards against replacing large files (>500 bytes) with content <10% of original size. This prevents the LLM from truncating existing documentation.
2. **`overwrite="force"` parameter**: New string option for `write_file` that bypasses all overwrite guards unconditionally.
3. **Phase check i18n** (`agent_phase_checks.py`, `lang/*.json`, `i18n.py`): All `TEMPLATE_PHASE_CHECKS` descriptions are now i18n-enabled via `description_key` field. Added ~50 phase_check keys across all 4 languages. The `/api/phase-checks` endpoint accepts `?lang=` parameter. Frontend caches by template+lang and invalidates on language switch.

**Note:** Full mocking-test detection (AST analysis for `unittest.mock.patch` targeting the bug site) is future work — requires understanding which attribute/function is being mocked and whether the mock hides the bug. For now, the overwrite guard prevents the LLM from writing trivially-sized test files and the i18n ensures correct language display.

**Files:** `git_ops.py:380-430`, `agent_core.py:555`, `agent_phase_checks.py:639-904`, `i18n.py:356-399`, `lang/{da,en,es,zh}.json:498-554`, `api_server.py:1550-1648`, `static/index.html:999-1030`

### 55. `locate` tool searches wrong project directory when session is in another project

**Symptom:** Session `bc3b4f38` — StarBrowser's BUG-001. Analysis phase LLM called `locate(name='create_application')` — returned Agenten symbols (`Agent.__init__`, `Agent._count_tasks`, etc.) instead of StarBrowser symbols. LLM wasted 2 iterations searching wrong project, hit 3 consecutive tool failures → dedup-escape triggered.

**Root cause:** `locate` and `list_symbols`'s global symbol index (`_GLOBAL_SYMBOL_INDEX`) was built at import time from `os.getcwd()` (Agenten root). When sessions from other projects are loaded, those projects' symbols were never indexed. `_ensure_workdir_indexed()` depended on `AGENT_WORKDIR` env var which wasn't set.

**Fix (3 parts):**
1. **`_resolve_workdir()`** (`agent_files.py:99-104`): New function — checks `AGENT_WORKDIR` env, falls back to CWD.
2. **`auto_detect_workdir(file_chunks, prompt)`** (`agent_files.py:107-150`): New function — scans session prompt for traceback paths like `C:\Dev\StarBrowser\starbrowser\main.py` and file_chunks content for absolute paths. Auto-sets `AGENT_WORKDIR` and re-indexes symbols.
3. **Session load calls** (`api_server.py:509,1421`): Both `load_session()` and stream session setup now call `auto_detect_workdir()` after restoring file_chunks.

**Result:** When a session contains tracebacks or code referencing files outside Agenten's directory, the system auto-detects the workdir and indexes that project's symbols. No manual `AGENT_WORKDIR` setup needed.

**Files:** `agent_files.py:99-150`, `api_server.py:509-510,1421-1422`

### 56. `add_method`/`add_function` — AST-based symbol insertion tools

**Symptom:** LLM must send the entire class TWICE in `edit_file` arguments (`old_text` + `new_text` = 1000+ lines with complex escaping). Escaping errors in the JSON arguments cause "unterminated string literal" SyntaxErrors. The LLM can't escape `"`, `"""`, `\\n` properly in 4500+ token tool calls.

**Fix (2 new tools + 1 code improvement):**
1. **`add_method(filepath, class_name, method_code)`** in `git_ops.py:812`: Uses `ast.parse` to find the class, inserts `method_code` at the class's `end_lineno`. Normalizes indentation to 4 spaces. Syntax-checks the result. LLM only generates the new method (10-30 lines), not the whole class.
2. **`add_function(filepath, function_code, [after_symbol])`** in `git_ops.py:870`: Same AST-based approach for module-level functions. Inserts at end of file or after a named symbol.
3. **`REQUIRED_ACTION_TOOLS`** (`agent_tasks.py:1005`): Extended to include `add_method`, `add_function`, `remove_symbol`, `add_import` so `_check_required_tools` enforces them.
4. **4-tier JSON recovery** (`llm_wrapper.py:17-94`): When the LLM's tool call arguments fail `json.loads`, the new chain tries: `raw_decode` (truncated JSON) → `_repair_json_control_chars` (fixes actual `\n` in strings — #1 LLM error) → `_salvage_json_args` (regex extraction for truly broken JSON) → `{}` (last resort).

**Usage for BUG-097:** Instead of `edit_file(path='tools.py', old_text='class ToolRegistry:\n...')`, use `add_method(filepath='tools.py', class_name='ToolRegistry', method_code='def _parse_json_robust(self, raw, default_error_message=None):\n    ...')`.

**Files:** `git_ops.py:812-940`, `agent_core.py:688-712`, `agent_tasks.py:1005`, `llm_wrapper.py:17-94,486-499`, `i18n.py:102-103`, `lang/*.json:tools.add_method`

### 57. `edit_file` indentation normalization for `old_text` path (`git_ops.py:710-742`)

**Symptom:** LLM copies `new_text` from `locate` result (0-space indent) but file has 4-space indent. `edit_file` with `old_text` finds a match via fuzzy pattern, replaces with `new_text` at wrong indent → "Syntaksfejl: unindent does not match any outer indentation level".

**Root cause:** `_normalize_indentation(new_text, search)` was only called when `symbol` was set (`git_ops.py:714`). The `old_text` path (no symbol) returned syntax error immediately without trying indentation normalization.

**Fix:** Removed `if symbol:` condition — `_normalize_indentation` now runs for ALL `.py` edits when `ast.parse` fails, regardless of whether `old_text` or `symbol` was used.

**Files:** `git_ops.py:710-742`

### 58. Nested function extraction: 3 runtime-crashes fixed (`refactoring_engine.py`)

**Symptom:** `batch_extract_symbols` succeeded (9/9) but stateful closure `execute_with_progress`
would crash at runtime with `NameError`.

**Root cause 3 separate bugs:**

**(A) `get_captured_variables` missing for/with/except/comprehension targets (`refactoring_engine.py:538-549`):**
Only checked `ast.Assign`/`ast.AnnAssign`/`ast.NamedExpr` for local variable definitions.
For-loop variables (`for child in ...`), with-as variables (`with x as y`), exception variables
(`except Exception as e`), and comprehension generators were NOT recognized as local → falsely
appeared as "captured" → `__init__` got extra parameters that don't exist in the call site's scope.
**Fix:** Added `ast.For.target`, `ast.AsyncFor.target`, `ast.AugAssign.target`, `ast.With.items[i].optional_vars`,
`ast.AsyncWith.items[i].optional_vars`, `ast.ExceptHandler.name`, `ast.ListComp`/`ast.SetComp`/`ast.GeneratorExp`/
`ast.DictComp` generators' `.target` to `local_names`.
**Nuance:** `ast.AugAssign` must exclude nonlocal names (`completed += 1` with `nonlocal completed` is
NOT creating a local, it's rebinding the enclosing scope). Added `nonlocal_names` pre-scan.
**Python 3.9+:** `ast.With` uses `items` (list of `withitem`), not direct `optional_vars`.

**(B) `read_captures` not replaced with `self.` in `__call__` body (`refactoring_engine.py:1376-1378`):**
Only `nonlocal` vars were replaced (`self._`). Read-captured vars (e.g. `total_tasks`) were stored in
`__init__` but the `__call__` body referenced them bare → `NameError`.
**Fix:** In the body-building loop, after nonlocal replacement, also replace each `read_capture` with
`self.{v}`.

**(C) Recursive calls not updated to `self()` (`refactoring_engine.py:1430-1435`):**
The call-site scanner skipped lines inside the function body (`if start_line <= ref_lineno < end_line:
continue`). Recursive calls like `execute_with_progress(child)` were never updated → `NameError`
in target module after extraction.
**Fix:** In the body-building loop, replace `{symbol_name}(` with `self(` before the capture replacements.

**Verification:** All 9 nested functions in `api_server.py` extracted successfully (1 stateful → class).
Generated `__call__` body is self-contained: recursive calls use `self()`, captured vars use `self.`,
nonlocal vars use `self._`.

**Files:** `refactoring_engine.py:538-586` (Fix A), `refactoring_engine.py:1375-1379` (Fix B+C)

### 59. `ast.With` in Python 3.9+ has `items` attribute

**Symptom:** `AttributeError: 'With' object has no attribute 'optional_vars'` when running
AST analysis on Python 3.12.

**Root cause:** Python 3.9 changed `ast.With` from direct attributes (`context_expr`, `optional_vars`)
to `items: list[withitem]` where each `withitem` has `context_expr` and `optional_vars`.
Same for `ast.AsyncWith`.

**Fix:** Use `for item in child.items:` before accessing `item.optional_vars`.

**Files:** `refactoring_engine.py:565-569`

### 66. `getattr(obj, attr, [])` returns None when attr exists but is None — one-shot streaming crash

**Symptom:** One-shot template immediately crashes with `TypeError: argument of type 'NoneType' is not iterable` after "Påbegynder opgave" log. No LLM interaction occurs.

**Root cause:** `getattr(agent.tool_registry, 'active_tools', [])` on `agent_message_builder.py:482` returns `None` instead of `[]` because `active_tools` EXISTS as an attribute on ToolRegistry (set to `None` in `__init__`). `getattr` only returns the default when the attribute DOES NOT EXIST at all. Then `"plan_phase" in None` crashes.

**Why one-shot only:** `set_task_tools()` takes an early-return path for templates not in `TEMPLATE_TASK_TOOLS` (one-shot isn't listed), so `active_tools` stays `None`. Templates like refactor/bugfix have phase entries in `TEMPLATE_TASK_TOOLS`, so `set_task_tools` calls `set_active_tools(tools)` → `active_tools` becomes a list → no crash.

**Fix:** Replace `getattr(obj, attr, [])` with `obj.attr if obj else None` + explicit `is None` check before `in`:
```python
active = agent.tool_registry.active_tools if agent.tool_registry else None
if active is None or "plan_phase" in active:
```

**Key insight:** `getattr(obj, attr, default)` is NOT a safe way to get `[]` when attr could be `None`. The default is only used when the attr is completely missing, not when it's falsy. Use `getattr(obj, attr, None) or []` for safe iteration, or check `is None` explicitly before `in`.

**Files:** `agent_message_builder.py:482`

### 60. `from session_manager import current_session_id` froze snapshots across modules — "Nedbryd en opgave først" after session load

**Symptom:** User loads an existing session in the UI (tree is displayed), clicks "Stream" →
immediate SSE error `Nedbryd en opgave først` ("Decompose a task first"). Meanwhile the tree IS
in the session file — just not visible to `execute_stream()`.

**Root cause (regression from refactoring):** After `api_server.py` was split into 27 modules,
each module did `from session_manager import current_session_id, export_folder`.
This imports the VALUE at import time (`None`). When `session_routes.py::load_session()` later
ran `global current_session_id; current_session_id = session_id`, it only updated the binding
in `session_routes.py`'s namespace — the 26 other modules kept their frozen `None` snapshot.
So `stream_execution.py::execute_stream()` read `current_session_id = None`, skipped the
session-restore branch, and `agent.task_tree` stayed `None` → "Decompose first" error.

Same bug affects `export_folder` (set via `folder_manager.py::set_folder()`, read elsewhere).

**Fix:** `current_session_id` and `export_folder` are now MUTABLE ATTRIBUTES on the singleton
`session_manager` instance (set in `session_manager.py`:
`session_manager.current_session_id = None; session_manager.export_folder = None`).
All 27 importing modules no longer import these names — they read/write
`session_manager.current_session_id` / `session_manager.export_folder`. Since the instance is a
shared mutable object, writes propagate across modules immediately.

Pattern rule for refactors: **never `from module import X` for a module-level mutable scalar
that other modules rebind via `global X`**. Attach to a shared singleton instead.

Updates touched: `session_manager.py`, `stream_execution.py`, `execution_core.py`,
`execution_control.py`, `decomposition.py`, `session_routes.py`, `session_api.py` (dead code,
also fixed for consistency), `folder_manager.py`, `routes.py`, `image_handler.py`,
`layout_prompts.py`, `layout_routes.py`, `model_api.py`, `model_routes.py`, `phase_checks.py`,
`phase_checks_routes.py`, `static_routes.py`, `api_server.py`, `autoresearch.py`,
`autoresearch_routes.py`, `error_handling.py`, `issues_api.py`, `issue_routes.py`,
`ui_routes.py`, `utility_routes.py`. Deleted dead `folder_handler.py`. Test fixtures in
`tests/test_sse_streaming.py` and `tests/test_api.py` now reset
`session_manager.current_session_id = None` and patch
`stream_execution.session_manager.current_session_id` instead of the removed module symbol.

**Files:** `session_manager.py:284-294` (singleton attributes), all 25 importer modules,
`tests/test_sse_streaming.py:13-18,56,107`, `tests/test_api.py:13-18`

### 61. `from api_server import X` inside a function re-executes `api_server.py` as a new module

**Symptom:** First HTTP request after server start crashes with
`AssertionError: The setup method 'after_request' can no longer be called on the application`
originating from `CORS(app, ...)` inside `api_server.py`.

**Root cause:** `api_server.py` is launched as `__main__`, so `sys.modules` stores it under
`__main__`, NOT under `api_server`. Any function-level `from api_server import agent` in another
module (e.g. `routes.py::upload_file`) cannot find `api_server` in `sys.modules`, so Python
re-imports it as a fresh module — re-executing `CORS(app, ...)` after the first request has
already been served → Flask raises.

**Fix:** Replace every lazy `from api_server import X` with top-level imports from the actual
source modules (`config`, `folder_manager`, `session_manager`, `stream_execution`,
`decomposition`). Applied to `routes.py`, `execution_core.py`, `api_skillflow.py`.

**Rule:** After refactoring `api_server.py`, NEVER leave `from api_server import X` inside a
function body — the `__main__` aliasing makes it re-trigger module-level setup code. Import from
the real source module instead.

**Files:** `routes.py`, `execution_core.py`, `api_skillflow.py`

### 62. `verify_imports.py` — cross-module import scanner

**Symptom:** After refactoring, `stream_execution.py` called `_count_source_symbols()` without
importing it — `NameError` at runtime when the SSE endpoint first triggers. These bugs are silent
until hit by a specific request flow.

**Root cause:** No automated check that every called symbol in a module can be resolved from
the module's imports or definitions. The refactoring engine moves symbols but doesn't update
callers automatically.

**Fix:** Created `verify_imports.py` — an AST-based scanner that runs at server startup:
1. Builds a project-wide symbol map (all FunctionDef/ClassDef/Assign targets across all .py files)
2. For each .py file, walks all `Call(Name(...))` nodes
3. If the called name is a known project symbol defined in ANOTHER file but NOT imported or
   defined in the calling file → flags it with file, line number, and source file
4. Logs warnings; raises `SystemExit` in production mode

Excludes: builtins, dunder names, `tests/`, `uploads/`, `sessions/`, `logs/`, `.agent_storage/`.

Called from `api_server.py` startup before `app.run()`. Added to `VERSION_FILES` for the
`/api/version` endpoint.

**Tests:** `tests/test_verify_imports.py` (8 tests) — verifies: missing import detection,
imported OK, local def OK, builtins OK, external imports OK, multi-missing, same-file def,
and directory exclusion.

**Files:** `verify_imports.py` (new), `api_server.py:131-141`, `config.py:273`,
`tests/test_verify_imports.py` (new)

### 63. `run_tests` excluded from `called_tools` → Test phase overrides done→failed

**Symptom:** Session `1de1a95b` — Test phase calls `run_tests()`, tests pass (832/832), phase
auto-advances (status=done), but `_finalize_task_stream` re-checks `check_phase_done` with
`tool_name=""` and `called_tools` (no `run_tests` there) → `tests_pass` returns False →
`task_node.status` overridden from `"done"` to `"failed"` → task retried 5 times.

**Root cause:** `stream_core.py:839-840` had a `pass` statement for `run_tests` to skip dedup
tracking (intended to prevent false dedup-blocking for a side-effect tool called multiple times
with same args). But `pass` silently dropped the tool from `called_tools` entirely — it was
neither tracked in the dict NOR checked for dedup. `_finalize_task_stream`'s `check_phase_done`
re-check with `tool_name=""` then couldn't find `run_tests`.

**Fix (stream_core.py:838-844):** Changed `pass` to increment `called_tools` but skip only the
dedup branch — `run_tests` is now tracked in the dict but never triggers dedup blocking:
```python
if tool_name in ("run_tests",):
    tool_key = tool_name + str(args_val)
    called_tools[tool_key] = called_tools.get(tool_key, 0) + 1
```
The `if tool_name not in ("run_tests", ...) and dup_count >= 1:` dedup check already skips
`run_tests`, so the tracking is side-effect-free for the loop's dedup logic.

**Tests:** 832 passed, 1 xfailed in 10.74s. Existing `test_falls_back_to_called_tools` at
`test_phase_checks.py:464` already covers `check_tests_pass` with `call_tools` dict containing
`run_tests` — it was the accumulation (not the check) that was buggy.

**Files:** `stream_core.py:838-844`

### 64. `renderTree` manglede `skipped` status-symbol i opgavetræet (`static/index.html:1526-1527,416`)

**Symptom:** Cascade-skipped faser (pga. `issue_resolved = True`) viste `○` (pending) i opgavetræets status-cirkel, selvom backend korrekt satte `remaining.status = "skipped"` og sendte SSE `task_done` med `status: "skipped"`.

**Root cause:** `renderTree()`'s ternære havde ingen case for `node.status === 'skipped'` — faldt igennem til default `'○'` + `'status-pending'` CSS.

**Fix (2 ændringer i `static/index.html`):**
1. `statusClass`: tilføj `(node.status === 'skipped' ? 'status-skipped' : 'status-pending')` — amber baggrund (`#f59e0b`)
2. `statusText`: tilføj `(node.status === 'skipped' ? '⏭' : '○')` — skip-symbol
3. CSS: `.status-skipped { background: #f59e0b; color: white; }`

**Files:** `static/index.html:416,1526-1527`

### 65. `"rejected"` vs `"resolved"` status-semantik for afviste bug-påstande (`agent_issues.py:266,285`, `instructions/bugfix.json`, `agent_skills.py:227`)

**Symptom:** BUG-103's analyse konkluderede at påstanden kunne afvises (koden var allerede korrekt), men status blev sat til `"resolved"` — hvilket betyder "buggen er rettet". BUG-103 var aldrig en ægte fejl.

**Root cause:** `update_issue_status()` havde kun `if status == "resolved": agent.issue_resolved = True`. `"rejected"` (brugt til afviste påstande) satte ikke flaget, så cascade-skip virkede ikke. Derudover instruerede Analyse-fasens prompt LLM'en til at bruge `'resolved'` for afviste påstande.

**Semantik (skal overholdes):**
- `"resolved"` = buggen var ægte og er blevet **rettet** (kodeændring udført) — bruges af Test/Opdatering faser
- `"rejected"` = bug-påstanden var **ugyldig** (koden var altid korrekt, fejlen kan ikke reproduceres) — bruges af Analyse fasen

**Fix (3 dele):**
1. `agent_issues.py:266,285`: `if status in ("resolved", "rejected"): agent.issue_resolved = True` — cascade-skip virker nu for begge
2. `instructions/bugfix.json`: Analyse TRIN 4 siger nu `'rejected'` i stedet for `'resolved'`
3. `agent_skills.py:227`: Samme ændring for den indbyggede sektionsinstruktion

**Files:** `agent_issues.py:266,285`, `instructions/bugfix.json:2`, `agent_skills.py:227`

### 67. `instructions/*.json` translations pattern — `en_`, `es_`, `zh_` prefixed keys

**Pattern:** Every Danish key in `instructions/*.json` gets corresponding `en_<lowercase_key>`, `es_<lowercase_key>`, `zh_<lowercase_key>` entries. The `_load_section_instructions()` in `agent_skills.py` resolves the correct key via `agent.lang + "_" + phase_name.lower()` lookup, falling back to the original Danish key.

**Files translated (2026-07-01):** `agenten.json`, `billedanalyse.json`, `diffanalyse.json`, `kodeanalyse.json`, `programmering.json`, `python-arkitektur.json`, `resume.json`, `selvforbedring.json`, `testgenerering.json`, `issue_handler.json` (missing `en_læs`/`es_læs`/`zh_læs` added).

### 68. `agent_autoresearch.py` — `_DESC_TEXTS` and `_FIX_TEXTS` dicts for 4-language description/fix prose

**Pattern:** Two new dicts mirror `_ISSUE_TEXTS`: `_DESC_TEXTS` (26 keys, `_build_issue_description()`) and `_FIX_TEXTS` (30 keys, `_build_issue_fix()`). Both use the same `_text()` helper pattern with `lang` parameter. `_build_issue_description()` and `_build_issue_fix()` now accept `lang` param — called from `_create_issue()` which gets `agent.lang`.

**Replacements:** ~250 lines of hardcoded Danish prose (section headings, analysis text, solution suggestions, root cause descriptions) replaced with `_desc_text()`/`_fix_text()` lookups. Code fragments and file paths remain language-independent.

**Tests:** `test_kodeanalyse_has_five` updated to check Danish-only key count (`'_' not in k`) instead of total section count.

**Commit:** `342e6bf` (instructions), `6c31fd1` (autoresearch)
