# Agenten

Dansk AI-opgaveplanlægger med værktøjsbrug — nedbryder prompts i opgavetræer, analyserer filer, og udfører operationer autonomt via LLM.

## 🚀 Hurtig start

```bash
pip install -r requirements.txt
cp .env.example .env   # Rediger .env med din GitHub token
python api_server.py   # Åbn http://localhost:5000
```

**Krav:** [LM Studio](https://lmstudio.ai) kørende på `localhost:1234` med en kompatibel model (Qwen3, Llama, DeepSeek).

## 📁 Projektstruktur

```
agent_core.py         # Agent-kerne: LLM-interaktion, opgavetræ, værktøjer
api_server.py         # Flask API: SSE streaming, sessions, filhåndtering
llm_wrapper.py        # LM Studio HTTP wrapper (completions + streaming)
tools.py              # Tool/ToolRegistry — generisk værktøjs-ramme
github_wrapper.py     # GitHub API: repos, issues, PRs
git_ops.py            # Git operationer via subprocess
task_tree.py          # TaskTree / TaskNode datastruktur
session_manager.py    # Session persistence (JSON)
web_searcher.py       # DuckDuckGo web scraping
module_builder.py     # Dynamisk modulbygger (eksperimentel)
static/index.html     # Browser-UI med drag/resize paneler
```

## 🎯 Skabeloner

Vælg skabelon i dropdown før nedbrydning — LLM får fastlagte sektioner:

| Skabelon | Beskrivelse |
|----------|-------------|
| 🌳 **Fri nedbrydning** | LLM bestemmer opgavetræet dynamisk |
| 📄 **Resumé** | Overblik → Nøglepunkter → Konklusion → Anbefalinger |
| 🔍 **Kodeanalyse** | Formål → Imports → Arkitektur → Kodekvalitet → Sikkerhed |

## 🔧 Værktøjer

Agenten kan udføre systemoperationer via `<<<TOOL>>>` markører:

| Værktøj | Handling |
|---------|----------|
| `github_create_repo` | Opret GitHub repository |
| `github_list_repos` | List dine repos |
| `github_create_issue` | Opret issue |
| `github_create_pr` | Opret pull request |
| `git_status` | Vis ændrede filer |
| `git_add_all` | Stage alle ændringer |
| `git_commit` | Commit med besked |
| `git_push` | Push til remote |
| `git_set_remote` | Sæt remote origin URL |
| `git_remote_status` | Tjek remote konfiguration |

## 🔐 Sikkerhed — Best Practices

- **Token i `.env`**: GitHub token gemmes KUN i `.env` (ikke i kode, ikke i git)
- **`.env` i `.gitignore`**: Forhindrer utilsigtet commit af credentials
- **Rotér tokens**: Brug fine-grained tokens med minimale scopes. Rotér ved mistanke om læk
- **Prompt injection**: `<<<TOOL>>>` og `<<<DONE>>>` markører strips fra al bruger-input før LLM-kald
- **Kun registrerede værktøjer**: `ToolRegistry.execute()` nægter at køre ukendte værktøjsnavne
- **Subprocess safety**: Alle git-kommandoer bruger liste-args (ingen shell), parametre valideres
- **LM Studio**: Kør lokalt — ingen data sendes til eksterne tjenester (undtagen GitHub API ved tool-brug)

> **⚠️ Vigtigt**: Hvis din GitHub token er blevet eksponeret (f.eks. i shell output), skal den **straks roteres** på https://github.com/settings/tokens

## 🤖 LM Studio opsætning

1. Download [LM Studio](https://lmstudio.ai)
2. Download en model (anbefalet: `qwen/qwen3.6-35b-a3b` eller `qwen3-30b-a3b`)
3. Start server på `http://localhost:1234`
4. Sæt context length til mindst 8192

**Model-valg**: Qwen3 er optimeret til agentic tasks og tool-calling. Mindre modeller fungerer også til simple opgaver.

## 🏗️ Arkitektur

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

**Tool-loop**: `solve_task_stream` → LLM → parse response → hvis TOOL: executér → feed resultat → LLM → ... → DONE

## 📝 Features

- **Sessions**: Gem/indlæs/omdøb — persistent JSON storage
- **Filanalyse**: Upload filer — LLM får fuld filkontekst
- **Streaming**: Real-time SSE output med thinking-toggle
- **Resultatkaskade**: Forældre-noder modtager børns resultater
- **Drag/resize paneler**: Frit layout med maximize/minimize/close
- **Markdown eksport**: Preview + download af session rapporter
- **Skabeloner**: Fastlagte sektioner — LLM bestemmer IKKE layout

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

```bash
git add -A
git commit -m "beskrivelse"
git push
```

Brug skabeloner til struktureret kodeanalyse af egne filer før commit.
