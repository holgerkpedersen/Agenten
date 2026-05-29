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
| `agent_tasks.py` | Task execution engine (`solve_task_stream`, `solve_task`, `handle_tool_call`, `build_tool_guidance`, `add_image`) |
| `llm_wrapper.py` | LM Studio API client, image encoding, vision support |
| `tools.py` | ToolRegistry, tool dispatch (`execute()`), `parse_response()` |
| `lang.py` | Danish/English/Spanish/Chinese translations |
| `i18n.py` | Translation key enum (K.KEY) |
| `static/index.html` | Complete frontend SPA |
| `skills/*.md` | Agent skill definitions |
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

### 37. Test suite: `current_session_id` leaks between test files (`tests/test_sse_streaming.py:12-17`, `tests/test_api.py:11-16`)

**Symptom:** Full test suite hangs for 3+ minutes at `test_sse_streaming.py`. SSE tests take 6s+ each instead of <0.1s.  
**Root cause:** `test_api.py` creates real sessions via `POST /api/sessions/create`, setting `api_server.current_session_id` to a real session ID. When `test_sse_streaming.py` runs next, SSE tests without session mocking pick up the leaked `current_session_id`, load real session data with a real LLM model, and try to execute the task tree with the REAL LLM (not mocked). The `_ensure_model_loaded("test-model")` call takes 2-4s to fail, and `solve_task_stream` runs 4+ real LLM iterations.  
**Fix:** Reset `api_server.current_session_id = None` in the `client` fixture of both `test_api.py` and `test_sse_streaming.py`. Combined with `_ensure_model_loaded` TESTING guard, SSE tests now complete in <0.05s.

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

### 43. Auto-resolution missed already-fixed bugs when LLM hit tool call limit (`agent_tasks.py:472-491`)

**Symptom:** Session `88a11e66` — all 5 phases executed (all failed/done) despite the bug already being fixed. Analysis phase correctly identified "already fixed" but wasted all 6 tool calls re-reading the same code, hit tool call limit, and got the generic "Gennemførte 6 værktøjskald" message as `full_response` — which doesn't match `AUTO_RESOLVE_PATTERNS`.

**Root cause:** `_finalize_task_stream` only checked `full_response` text for auto-resolve patterns. When the tool call limit was reached, `full_response` was overwritten with the generic auto-done message, losing the LLM's actual analysis conclusion. The auto-resolution was in `elif task_node.status == "done":` block — but `_check_required_tools` (before phase-aware fix) ran first and set status to "failed".

**Fix (2 parts):**
1. Phase-aware `_check_required_tools()` — `update_issue_status` removed from required tools for Analyse/Læs/Afklar phases (already done in prior fix).
2. `_finalize_task_stream` now also scans ALL assistant messages' text content for `AUTO_RESOLVE_PATTERNS`, not just `full_response`. If a match is found in assistant messages, auto-resolution triggers.

**Files:** `agent_tasks.py:472-491`

## Model Knowledge

See `skills/vision_models.md` for full vision model compatibility matrix.  
Key takeaway: Gemma requires raw_b64 + images-before-text. Qwen/GPT use data_url.
