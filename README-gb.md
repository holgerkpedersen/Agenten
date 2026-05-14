# Agenten

Danish AI task planner with tool usage — breaks down prompts into task trees, analyzes files, and performs operations autonomously via LLM.

## 🚀 Quick start

```bash
pip install -r requirements.txt
cp .env.example .env   # Edit .env with your GitHub token
python api_server.py   # Open http://localhost:5000
```

**Requirements:** [LM Studio](https://lmstudio.ai) running on `localhost:1234` with a compatible model (Qwen3, Llama, DeepSeek).  
**Note:** The agent uses `/v1/chat/completions` (chat format) — the model must support this endpoint.

## 📁 Project structure

```
agent_core.py         # Core agent: LLM interaction, task tree, tools
api_server.py         # Flask API: SSE streaming, sessions, file handling
llm_wrapper.py        # LM Studio HTTP wrapper (completions + streaming)
tools.py              # Tool/ToolRegistry — generic tool framework
github_wrapper.py     # GitHub API: repos, issues, PRs
git_ops.py            # Git operations via subprocess
task_tree.py          # TaskTree / TaskNode data structures
session_manager.py    # Session persistence (JSON)
web_searcher.py       # DuckDuckGo web scraping
module_builder.py     # Dynamic module builder (experimental)
static/index.html     # Browser UI with drag/resize panels
```

## 🎯 Templates

Select a template from the dropdown before decomposition — the LLM receives fixed sections:

| Template | Description |
|----------|-------------|
| 🌳 **Free decomposition** | LLM determines the task tree dynamically |
| 📄 **Summary** | Overview → Key points → Conclusion → Recommendations |
| 🔍 **Code analysis** | Purpose → Imports → Architecture → Code quality → Security |
| 📊 **Diff analysis** | Git log + diff → Risk assessment → Recommendations |
| 🔀 **PR Agent** | Branch → Commit → Push → Pull Request (automated PR workflow) |

## 🔧 Tools

The agent can perform system operations via `<<<TOOL>>>` markers:

| Tool | Action |
|------|--------|
| `github_create_repo` | Create a GitHub repository |
| `github_list_repos` | List your repositories |
| `github_create_issue` | Create an issue |
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
| `read_chunk` | Read a chunk of a large file |

## 🔐 Security — Best Practices

- **Token in `.env`**: GitHub token is stored ONLY in `.env` (never in code, never committed)
- **`.env` in `.gitignore`**: Prevents accidental committing of credentials
- **Rotate tokens**: Use fine‑grained tokens with minimal scopes. Rotate if you suspect a leak
- **Prompt injection**: `<<<TOOL>>>` and `<<<DONE>>>` markers are stripped from all user input before LLM calls
- **Only registered tools**: `ToolRegistry.execute()` refuses to run unknown tool names
- **Subprocess safety**: All git commands use list arguments (no shell), parameters are validated
- **LM Studio**: Runs locally — no data is sent to external services (except GitHub API when a tool uses it)

> **⚠️ Important**: If your GitHub token has been exposed (e.g., in shell output), **rotate it immediately** at https://github.com/settings/tokens

## 🤖 LM Studio setup

1. Download [LM Studio](https://lmstudio.ai)  
2. Download a model (recommended: `qwen/qwen3.6-35b-a3b` or `qwen3-30b-a3b`)  
3. Start the server at `http://localhost:1234`  
4. Set the context length to at least 8192  

**Note:** The agent uses the `/v1/chat/completions` endpoint (chat format with a `messages` array). Make sure LM Studio is running with the OpenAI‑compatible API enabled.

**Model choice:** Qwen3 is optimized for agentic tasks and tool calling. Smaller models also work for simple tasks.

## 🏗️ Architecture

```
Browser (index.html)
    │ SSE (EventSource)
    ▼
Flask API (api_server.py)
    │
    ├── decompose() → agent_core.decompose_prompt() → LLM
    ├── execute_stream() → agent_core.solve_task_stream() → LLM + Tools
    └── sessions/ (JSON persistence)
```

**Tool loop:** `solve_task_stream` → LLM (`/v1/chat/completions` with system/user/assistant messages) → parse response → if TOOL: execute → feed result as a user message → LLM → … → `<<<DONE>>>`

**PR workflow:** The PR Agent template activates checkpoint validation — the LLM is forced to branch → commit → push → create a PR in the correct order. If any step is missing, `<<<DONE>>>` is rejected with a checkpoint error.

## 📝 Features

- **Sessions**: Save/load/rename — persistent JSON storage  
- **File analysis**: Upload files — LLM receives full file context (large files are automatically chunked)  
- **Streaming**: Real‑time SSE output with thinking toggle and chat formatting (`/v1/chat/completions`)  
- **Result cascade**: Parent nodes receive child results  
- **Drag/resize panels**: Free layout with maximize/minimize/close  
- **Markdown export**: Preview + download of session reports  
- **Templates**: Fixed sections — LLM does NOT decide the layout  
- **PR workflow**: Automated branch → commit → push → PR with checkpoint validation  
- **Multi‑language**: UI and LLM instructions in Danish, English, Spanish, and Chinese  

## 📋 Requirements

```
flask==3.1.3
flask-cors==6.0.2
requests==2.33.1
beautifulsoup4==4.14.3
anytree==2.13.0
python-dotenv==1.2.2
```

## 🔄 Git workflow

Use the **🔀 PR Agent** template for an automated PR flow:

1. Choose "🔀 PR Agent" in the dropdown  
2. Enter something like “Create branch 'new-feature', commit changes, push and open a PR to master”  
3. The agent automatically performs: branch → commit → push → PR  
4. The checkpoint system ensures the correct order — the LLM must complete each step before moving on

Manual git commands are also supported via the tools:

```bash
git add -A
git commit -m "description"
git push
```

Use templates for structured code analysis of your own files before committing.