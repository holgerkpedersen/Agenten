# Agenten — Agent Knowledge Base

Key facts, fixes, and debugging patterns learned through development.
Read this before making changes.

## Project Structure

| File | Purpose |
|------|---------|
| `api_server.py` | Flask REST API + all endpoints |
| `agent_core.py` | Agent class, tools, task execution, folder scanning |
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

### 3. Folder scanning missed file paths (`agent_core.py:520`)

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
