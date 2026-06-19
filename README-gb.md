# Agenten

Danish AI task planner with tool usage — breaks down prompts into task trees, analyzes files, refactors code, and performs operations autonomously via LLM.

## 🚀 Quick start

```bash
pip install -r requirements.txt
cp .env.example .env   # Edit .env with your GitHub token
python api_server.py   # Open http://localhost:5000
```

**Requirements:** [LM Studio](https://lmstudio.ai) running on `localhost:1234` with a compatible model — or [OpenCode Go](https://opencode.ai) with `OPENCODE_API_KEY`.  
**Vision:** Use a vision-capable model (Gemma 4, Qwen-VL, Llava).  
**Start output:** `🕐 Startet: 2026-05-19 15:21:30 | api_server=15:21:30 | llm=15:15:05` — verify version.

## 📁 Project structure

```
agent_core.py         # Agent facade: init, tool registration, decompose, execute, thin delegates
agent_tasks.py        # Task execution: solve_task_stream, solve_task, handle_tool_call
agent_tree.py         # Tree operations: parse, create_fallback_tree, record_outcome, evolve_if_needed
agent_files.py        # File/chunk operations: read/write/chunk, folder scanning (.env excluded)
agent_skills.py       # Skill matching, template constants (TEMPLATE_TOOLS, TEMPLATE_TASK_TOOLS)
agent_autoresearch.py # Auto-research: classify failures, build proposed_fix, create CORE-issues, retry
agent_phase_checks.py # Deterministic phase checks: file_exists, files_from_plan, tool_called, tests_pass
agent_wta.py          # Weighted Tool Arbitration: rank_tool_calls, Laplace scoring, sequence analysis
agent_issues.py       # Issue tools: read_issue, update_issue_status, create_issue, oversize detection
agent_git.py          # Git/PR workflow: is_pr_workflow, extract_branch_name, verify_pr_step
api_server.py         # Flask API: SSE streaming, sessions, image upload, version, issues endpoint
llm_wrapper.py        # LM Studio HTTP wrapper (chat + streaming + vision/image encoding)
tools.py              # Tool/ToolRegistry — tool framework (parse_response, build_system_prompt)
task_tree.py          # TaskTree / TaskNode data structures
config.py             # Central constants (CHUNK_SIZE, timeout, max_tokens)
git_ops.py            # Git + file operations (write_file, edit_file with validation)
github_wrapper.py     # GitHub API: repos, issues, PRs
session_manager.py    # Session persistence (JSON), threading lock
web_searcher.py       # DuckDuckGo web scraping
ddg_search.py         # DuckDuckGo search (fallback)
flow_builder.py       # Prompt flow builder
module_builder.py     # Dynamic module builder (experimental)
model_manager.py      # OpenAI + LM Link REST API model lister
skill_loader.py       # Skill system — frontmatter, keyword-scoring, template-matching
skill_evolution.py    # SkillFlow — outcome tracking, evolution analysis (Retain/Refine/Prune/Generate)
skill_tracker.py      # Per-skill outcome recording with success_rate
lang.py               # Translations (da/en/es/zh)
i18n.py               # Internationalization keys (K enum)
AGENTS.md             # Knowledge base — bugs, fixes, debugging workflow
BRUGERVEJLEDNING.md   # User guide (Danish)
static/index.html     # Browser UI with drag/resize panels, template dropdown, image preview, issues viewer
core_analytics.py    # Tool/test outcome tracking, hotspots, summaries
instructions/        # Section instructions per template (JSON, 12 templates)
tests/                # 739 tests (pytest, all pass in 11s)
sessions/             # JSON session persistence (save/load/delete)
skills/               # Skills in markdown with frontmatter
```

## 🎯 Templates

Select a template from the dropdown before decomposition — the LLM receives fixed sections:

| Template | Description |
|----------|-------------|
| 🌳 **Free decomposition** | LLM determines the task tree dynamically (3-6 main tasks, max 2 levels) |
| 📄 **Summary** | Overview → Key points → Conclusion → Recommendations |
| 🔍 **Code analysis** | Purpose → Imports → Architecture → Code quality → Security |
| 📊 **Diff analysis** | Git log + diff → Risk assessment → Recommendations |
| 🔀 **PR Agent** | Branch → Commit → Push → Pull Request (automated PR workflow) |
| 💻 **Programming task** | Requirements → Architecture → Implementation plan → Security → Code |
| 🏗️ **Python Architecture** | Architecture planning with `write_file` output to `./docs/arkitektur.md` |
| 🖼️ **Image analysis** | Description → Context → Details → Assessment → Export (.md) |
| 🔧 **Refactoring** | Analysis → Plan → Extract → Update → Test (SOLID refactoring) |
| 🧪 **Test generation** | Analysis → Test (Red) → Implementation → Verification (Green) |
| 🐛 **Bugfix (TDD)** | Analysis → Test (Red) → Implementation → Verification (Green) → Update |
| 📋 **Issue Handler** | Read → Analyze → Fix → Verify → Update status |

**Image analysis requires:** Upload an image via 🖼 button **before** clicking Decompose. WebP images are automatically converted to `image/png` MIME for gemma compatibility.

## 🔧 Tools

The agent can perform system operations via `<<<TOOL>>>` markers (35 tools):

| Tool | Action |
|------|--------|
| `plan_phase` | Create detailed task plan with tool calls and steps |
| `create_todo` | Add a new todo to the LLM's personal plan |
| `update_todo` | Mark todo done or update text |
| `delete_todo` | Remove a todo from the plan |
| `list_todos` | Show both Agent's success criteria and LLM's action plan |
| `list_chunks` | List all loaded files |
| `read_chunk` | Read a chunk of a large file |
| `locate` | Find current line of PYTHON function/class/variable via AST — NOT tool name |
| `write_file` | Create NEW file (rejects existing .py files — use edit_file) |
| `edit_file` | Search-and-replace in existing files (with syntax check) |
| `list_files` | List files in a directory (with file type filter and max depth) |
| `create_issue` | Create new issue |
| `create_refactor_issue` | Create refactor issue for oversized files |
| `read_issue` | Read issue (include_hints=false default — problem-only) |
| `update_issue_status` | Update issue status |
| `run_tests` | Run pytest and return results |
| `add_image` | Add image to context (base64-encoded) |
| `github_create_repo` | Create a GitHub repository |
| `github_list_repos` | List your repositories |
| `github_create_issue` | Create a GitHub issue |
| `github_create_pr` | Create a pull request |
| `git_status` | Show changed files |
| `git_add_all` | Stage all changes |
| `git_commit` | Commit with a message |
| `git_push` | Push to remote |
| `git_set_remote` | Set the remote origin URL |
| `git_remote_status` | Check remote configuration |
| `git_diff` | Show differences between commits |
| `git_log` | Show recent commits |
| `git_create_branch` | Create a new branch |
| `git_current_branch` | Show the current branch |
| `git_branch_list` | List all branches |
| `git_pull` | Pull changes from remote |
| `git_checkout` | Switch to a branch |

## 🖼️ Vision / Image analysis

Upload images via 🖼 button or "Browse" + "Read file". Supports `.png`, `.jpg`, `.webp`, `.gif`, `.bmp`.

**Images are session-scoped** — saved with the session, loaded on session switch, cleared on new session.

### Model compatibility

| Model | Format | JSON type |
|-------|--------|-----------|
| **Gemma 4** (26b/e4b) | `data:image/png;base64,...` | `image_url` |
| Qwen / GPT / Llava | `data:image/png;base64,...` | `image_url` |

> **Important:** `image/webp` MIME is rejected by Gemma 4 via LM Studio — automatically mapped to `image/png`. Images placed **before** text in content array (Gemma requirement).

See `skills/vision_models.md` for full compatibility matrix and `AGENTS.md` for debugging workflow.

## ✅ Validation

`write_file` and `edit_file` perform automatic validations on written files:

| Validation | When | Description |
|------------|------|-------------|
| **Syntax check** | `.py` files | `ast.parse()` — prevents writing files with syntax errors |
| **Dependency check** | `.py` files | Scans imports against `requirements.txt` — auto-updates |
| **Route mismatch** | `.py/.html/.js` | Compares frontend/backend URLs — returns `route_warnings` |
| **Overwrite guard** | `.py` files | `write_file` refuses to overwrite existing files — use `edit_file` |

## 🔐 Security

- **Token in `.env`**: GitHub token ONLY in `.env` (not in code, not in git)
- **`.env` never scanned**: `.env` files excluded from folder scanning and `read_file_content`
- **Prompt injection**: `<<<TOOL>>>` and `<<<DONE>>>` markers stripped from user input. Additional sanitization via `_sanitize_prompt()`
- **Syntax check before write**: `write_file` and `edit_file` validate Python syntax BEFORE writing
- **Only registered tools**: `ToolRegistry.execute()` refuses unknown tool names
- **Per-phase tool restrictions**: Each phase in a template only has access to relevant tools
- **API key auth**: Optional API-key protection on `/api/*` endpoints (set `AGENT_API_KEY`)
- **Magic byte validation**: Image upload validates magic bytes (not just file extension)
- **Subprocess safety**: Git commands use list arguments (no shell)
- **LM Studio**: Runs locally — no data sent externally (except GitHub API)

## 🤖 LM Studio setup

1. Download [LM Studio](https://lmstudio.ai)
2. Download a model:
   - **Vision**: `google/gemma-4-26b-a4b` or `gemma-4-e4b`
   - **Text**: `qwen/qwen3.6-35b-a3b` or `qwen3-30b-a3b`
3. Start server at `http://localhost:1234`
4. Set context length to at least 8192

## 🏗️ Architecture

```
Browser (index.html)
    │ SSE (EventSource)
    ▼
Flask API (api_server.py)
    │
    ├── decompose() → agent_core.decompose_prompt()
    │       ├── agent_skills.get_templates() / match_skills()
    │       ├── agent_files.get_folder_context() / get_single_file_context()
    │       ├── agent_tree.parse_tree_from_llm() / create_fallback_tree()
    │       └── LLM (decomposition)
    │
    ├── execute_stream() → agent_core.solve_task_stream()
    │       ├── agent_tasks.solve_task_stream() → LLM + Tools loop
    │       ├── agent_tasks.handle_tool_call()
    │       ├── agent_git.verify_pr_step()
    │       ├── agent_tree.record_outcome()
    │       └── Skip root when children exist (no redundant re-execution)
    │
    ├── /api/image/* — upload/list/clear/remove
    ├── /api/issues — list all tracked issues
    ├── /api/version — server version + file timestamps
    └── sessions/ (JSON persistence)
```

**Tool loop**: `solve_task_stream` → LLM → parse response → TOOL: execute → feed result → LLM → ... → `<<<DONE>>>`

**PR Workflow**: `agent_git.verify_pr_step()` enforces branch → commit → push → PR in correct order.

**Skills**: `skill_loader.py` loads `skills/*.md` with frontmatter. `agent_skills.match_skills()` scores prompts and activates relevant skills. `skill_evolution.py` analyzes outcomes and suggests Retain/Refine/Prune/Generate.

**SkillFlow**: `skill_tracker.py` records per-skill outcomes. After 15+ outcomes, `agent_tree.evolve_if_needed()` triggers automatic evolution analysis.

**Version tracking**: Server startup shows `🕐 Startet:` + `📦 llm=HH:MM:SS`. `/api/version` returns all file timestamps.

## 📝 Features

- **LLM-driven todos**: LLM creates and manages its own task plan via `plan_phase`, `create_todo`, `update_todo`, `delete_todo`, `list_todos`. Shown in the task plan panel and inline in LLM output.
- **Auto-populated LLM todos**: Refactor template generates per-module todos automatically from `refactor_plan.md`.
- **refactor_analyse.md**: Analysis phase saves output to `refactor_analyse.md`, auto-loaded in Plan phase — saves 3-5 iterations by avoiding re-reading symbols/functions.
- **Agnostic instructions**: Section instructions use `{source_file}` instead of hardcoded `api_server.py`.
- **Opdatér phase auto-generates patterns**: `code_contains` regex patterns are dynamically generated from `refactor_plan.md` module names.
- **Undo clears execution state**: Session files are cleaned (agent_log, execution_log, llm_todos) before git reset.
- **Autonomous bugfix**: 🐛 Bugfix (TDD) template → Analysis → Test → Implementation → Verification → Update
- **Autonomous refactoring**: 🔧 Refactor template → Analysis → Plan → Extract → Update → Test
- **Test generation**: 🧪 Generate tests for untested classes/functions/methods
- **Image analysis**: Upload → Decompose → Structured 5-phase analysis → .md export
- **Vision support**: Automatic model detection, format adaptation (Gemma requires raw_b64 + images-before-text)
- **Issues viewer**: 🐛 Issues button shows all tracked issues with details and "Use as task" action
- **Issue Handler**: 📋 Automated issue fix workflow (read → analyze → fix → verify)
- **Precise file editing**: `edit_file` search-and-replace instead of full-file rewrites
- **Auto-discovery**: `create_issue` tool reports new bugs/issues during analysis
- **Sessions**: Save/load/rename/delete — persistent JSON storage with atomic write
- **File analysis**: Upload files, folder scanning (.env excluded), automatic chunking
- **Streaming**: Real-time SSE output with thinking toggle and stop button
- **Result cascade**: Previous task results fed into next task
- **Drag/resize panels**: Free layout with maximize/minimize
- **Markdown export**: Preview + download of session reports
- **Multi-language**: UI and LLM instructions in Danish, English, Spanish, Chinese
- **Per-phase tool restrictions**: Each phase gets only relevant tools (e.g. Analysis = read-only)
- **Timeout**: Task execution aborted after 30 minutes (EXECUTION_TIMEOUT)
- **Auto-DONE**: Prevents infinite tool loops after 10-15 iterations
- **Robust JSON parsing**: `json.JSONDecoder().raw_decode()` handles AI output
- **LM Link support**: OpenAI-compatible REST API models
- **Context-CoT integration**: Extract-first guidance (LLM must summarize context before tools), anti-leakage read_issue (problem-only default, hints on request), rubric-based validation per skill (binary checks → retry)
- **OpenCode Go support**: Set `OPENCODE_BASE_URL` + `OPENCODE_API_KEY` to use OpenCode Go instead of LM Studio
- **Native function calling**: OpenAI native `tools` parameter sent with chat completions — model returns structured tool_calls instead of marker parsing
- **Session persistence fix**: `current_session_id` leak between test files fixed, `_save_session_data` debounce removed (always saves at SSE end), tree serialization now includes `result` field
- **Phase checks & auto-advance**: Deterministic success criteria for phases. System auto-completes when all modules exist or plan is written.
- **Refactor template iteration limits**: Higher budget (15-12 iterations) to handle large refactor workloads.
- **739 tests**: pytest suite covering all modules (all pass in 11s)
- **AGENTS.md**: Knowledge base updated with entries 34-57+ (LLM todos, AGENT_WORKDIR, refactor_analyse, etc.)
- **Cache-Control: no-cache**: Static routes avoid browser caching of index.html

## 📋 Requirements

```
flask>=3.1.3
flask-cors>=6.0.2
requests>=2.33.1
beautifulsoup4>=4.14.3
python-dotenv>=1.2.2
openai>=1.0.0
```

## 🔄 Git workflow

```bash
git add -A
git commit -m "description"
git push
```

Use **🔀 PR Agent** template for automated PR workflow, **💻 Programming task** for code generation, **🖼️ Image analysis** for vision tasks, **🔧 Refactoring** for code restructuring, and **🐛 Bugfix (TDD)** for bug fixing.
