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

## Model Knowledge

See `skills/vision_models.md` for full vision model compatibility matrix.  
Key takeaway: Gemma requires raw_b64 + images-before-text. Qwen/GPT use data_url.
