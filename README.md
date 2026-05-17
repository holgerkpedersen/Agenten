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
tools.py              # Tool/ToolRegistry — generisk værktøjs-ramme (parse_response, build_system_prompt)
github_wrapper.py     # GitHub API: repos, issues, PRs
git_ops.py            # Git + fil operationer (write_file med syntax/dependency/route-validering)
task_tree.py          # TaskTree / TaskNode datastruktur
session_manager.py    # Session persistence (JSON), threading lock, corrupt session cleanup
web_searcher.py       # DuckDuckGo web scraping
skill_loader.py       # Skills-system — frontmatter, keyword-scoring, sprog-override
module_builder.py     # Dynamisk modulbygger (eksperimentel)
model_manager.py      # OpenAI + LM Link REST API model-lister
lang.py               # Oversættelser (da/en/es/zh)
i18n.py               # Internacionaliserings-nøgler (K enum)
static/index.html     # Browser-UI med drag/resize paneler, template dropdown, sprogvælger
skills/               # Skills i markdown med frontmatter (da/en sprog-overrides)
skills/base.md        # Base-skill: altid aktiv
skills/da/            # Dansk-sprogede skills
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
| 🐍 **Programmeringsopgave** | 5 faser: Kravanalyse → Arkitekturdesign → Implementeringsplan → Sikkerhedsanalyse → Kodeimplementering (`read_chunk` + `write_file`) |
| 🏗️ **Python Arkitektur** | 1 task: Arkitekturplanlægning med `write_file` output til `./docs/arkitektur.md` |
| 👤 **Agenten (3 tasks)** | 3 faser: Forstå formål → Udforsk/læs filer → Planlæg/beslut (generisk agent-workflow) |

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
| `write_file` | Skriv fil til disk (validerer syntax, dependencies og routes) |

## ✅ Validering

`write_file` udfører automatisk 3 valideringer på skrevne filer:

| Validering | Hvornår | Beskrivelse |
|------------|---------|-------------|
| **Syntax check** | `.py` filer | `ast.parse()` — returnerer `syntax_error` ved fejl (linje + besked) |
| **Dependency check** | `.py` filer | Scanner imports mod `requirements.txt` — returnerer `missing_deps` og **opdaterer automatisk** `requirements.txt` |
| **Route mismatch** | `.py` / `.html` / `.js` | Sammenligner `fetch()`/`action` URLs i frontend med `@app.route()` i backend — returnerer `route_warnings` for URL'er uden matchende route |

Valideringsresultater sendes tilbage til LLM som tool-output, så modellen kan **selvrette** fejl i næste iteration.

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

**LM Link**: Agenten understøtter også eksterne OpenAI-kompatible API'er (f.eks. nemotron på remote LM Studio) via `model_manager.py` — `get_all_rest_models()` merger OpenAI + REST API endpoints. Konfigurer i `.env` med `REST_API_BASE_URL` og `REST_API_KEY`.

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

**Tool-loop**: `solve_task_stream` → LLM (`/v1/chat/completions` med system/user/assistant messages) → parse response (JSON via `raw_decode`) → hvis TOOL: executér → feed resultat som user-message (med `TOOL_CONTINUATION` reminder) → LLM → ... → `<<<DONE>>>` (auto-tvinges efter 8 tool-calls)

**PR Workflow**: PR Agenten-skabelonen aktiverer checkpoint-validering — LLM'en tvinges til at branch → commit → push → PR i korrekt rækkefølge. Hvis et trin mangler, afvises `<<<DONE>>>` med en checkpoint-fejl.

**Skills**: `skill_loader.py` indlæser `skills/*.md` med frontmatter (keywords, template, sprog). `_match_skills()` scorer brugerens prompt og aktiverer relevante skills + deres sprog-overrides. Skills vises som "Retningslinjer (ikke værktøjer)" i prompten.

## 📝 Features

- **Sessions**: Gem/indlæs/omdøb — persistent JSON storage med atomisk write og threading lock
- **Filanalyse**: Upload filer — LLM får fuld filkontekst (store filer deles automatisk i chunks)
- **Streaming**: Real-time SSE output med thinking-toggle og chat-formatering (`/v1/chat/completions`)
- **Resultatkaskade**: Forældre-noder modtager børns resultater
- **Drag/resize paneler**: Frit layout med maximize/minimize/close + Ctrl+C i output-områder
- **Markdown eksport**: Preview + download af session rapporter
- **Skabeloner**: Fastlagte sektioner med per-template `SECTION_INSTRUCTIONS` — LLM bestemmer IKKE layout
- **PR Workflow**: Automatiseret branch → commit → push → PR med checkpoint-validering
- **Multi-sprog**: UI og LLM-instruktioner på dansk, engelsk, spansk og kinesisk
- **Skills-system**: Markdown-baserede skills med frontmatter, keyword-scoring og template-suggestion; sprog-overrides via `skills/{lang}/` struktur
- **Validering**: `write_file` tjekker syntax (`ast.parse`), dependencies (mod `requirements.txt`) og frontend/backend route-match
- **Auto-DONE**: Forhindrer uendelig tool-loop — `<<<DONE>>>` tvinges efter 8 tool-calls og reminders efter hvert tool-resultat
- **Robust JSON-parsing**: `json.JSONDecoder().raw_decode()` håndterer AI-output med ekstra `}}}` uden crash
- **LM Link support**: OpenAI-kompatible REST API modeller (f.eks. nemotron på remote LM Studio)

## 📋 Requirements

```
flask>=3.1.3
flask-cors>=6.0.2
requests>=2.33.1
beautifulsoup4>=4.14.3
anytree>=2.13.0
python-dotenv>=1.2.2
openai>=1.0.0          # LM Link understøttelse
```

## 🔄 Git workflow

Brug **🔀 PR Agenten** skabelonen til automatiseret PR workflow, eller **🐍 Programmeringsopgave** og **🏗️ Python Arkitektur** til kodegenerering med `write_file`:

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
