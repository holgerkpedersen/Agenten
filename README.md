# Agenten

Dansk AI-opgaveplanlægger med værktøjsbrug — nedbryder prompts i opgavetræer, analyserer filer, og udfører operationer autonomt via LLM.

## 🚀 Hurtig start

```bash
pip install -r requirements.txt
cp .env.example .env   # Rediger .env med din GitHub token
python api_server.py   # Åbn http://localhost:5000
```

**Krav:** [LM Studio](https://lmstudio.ai) kørende på `localhost:1234` med en kompatibel model (Qwen3, Llama, DeepSeek).  
**Bemærk:** Agenten bruger `/v1/chat/completions` (chat-format) — modellen skal understøtte dette.

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
| 📊 **Diff-analyse** | Git log + diff → Risikovurdering → Anbefalinger |
| 🔀 **PR Agenten** | Branch → Commit → Push → Pull Request (automatiseret PR workflow) |

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
| `git_diff` | Vis ændringer mellem commits |
| `git_log` | Vis seneste commits |
| `git_create_branch` | Opret ny branch |
| `git_current_branch` | Vis aktiv branch |
| `git_branch_list` | List alle branches |
| `git_pull` | Hent ændringer fra remote |
| `git_checkout` | Skift til branch |
| `read_chunk` | Læs en chunk af en stor fil |

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

**Bemærk:** Agenten bruger `/v1/chat/completions` endpointet (chat-format med `messages` array).  
Sørg for at LM Studio serveren kører med OpenAI-kompatibel API aktiveret.

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

**Tool-loop**: `solve_task_stream` → LLM (`/v1/chat/completions` med system/user/assistant messages) → parse response → hvis TOOL: executér → feed resultat som user-message → LLM → ... → `<<<DONE>>>`

**PR Workflow**: PR Agenten-skabelonen aktiverer checkpoint-validering — LLM'en tvinges til at branch → commit → push → PR i korrekt rækkefølge. Hvis et trin mangler, afvises `<<<DONE>>>` med en checkpoint-fejl.

## 📝 Features

- **Sessions**: Gem/indlæs/omdøb — persistent JSON storage
- **Filanalyse**: Upload filer — LLM får fuld filkontekst (store filer deles automatisk i chunks)
- **Streaming**: Real-time SSE output med thinking-toggle og chat-formatering (`/v1/chat/completions`)
- **Resultatkaskade**: Forældre-noder modtager børns resultater
- **Drag/resize paneler**: Frit layout med maximize/minimize/close
- **Markdown eksport**: Preview + download af session rapporter
- **Skabeloner**: Fastlagte sektioner — LLM bestemmer IKKE layout
- **PR Workflow**: Automatiseret branch → commit → push → PR med checkpoint-validering
- **Multi-sprog**: UI og LLM-instruktioner på dansk, engelsk, spansk og kinesisk

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

Brug **🔀 PR Agenten** skabelonen til automatiseret PR-flow:

1. Vælg "🔀 PR Agenten" i dropdown
2. Indtast f.eks. "Opret branch 'ny-feature', commit ændringer, push og opret PR til master"
3. Agenten udfører automatisk: branch → commit → push → PR
4. Checkpoint-system sikrer korrekt rækkefølge — LLM tvinges til at fuldføre hvert trin

Manuelle git-kommandoer understøttes også via værktøjerne:

```bash
git add -A
git commit -m "beskrivelse"
git push
```

Brug skabeloner til struktureret kodeanalyse af egne filer før commit.
