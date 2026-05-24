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

### 3. Folder scanning missed file paths (`agent_files.py:68`)

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
