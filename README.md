# Agenten

Dansk AI-opgaveplanlægger med værktøjsbrug — nedbryder prompts i opgavetræer, analyserer filer og billeder, og udfører operationer autonomt via LLM.

## 🚀 Hurtig start

```bash
pip install -r requirements.txt
cp .env.example .env   # Rediger .env med din GitHub token
python api_server.py   # Åbn http://localhost:5000
```

**Krav:** [LM Studio](https://lmstudio.ai) kørende på `localhost:1234` med en kompatibel model.  
**Vision:** Brug en vision-kompatibel model (Gemma 4, Qwen-VL, Llava).  
**Start output:** `🕐 Startet: 2026-05-19 15:21:30 | api_server=15:21:30 | llm=15:15:05` — verificér version.

## 📁 Projektstruktur

```
agent_core.py         # Agent-kerne: LLM-interaktion, opgavetræ, værktøjer, folder scanning
api_server.py         # Flask API: SSE streaming, sessions, billed-upload, version endpoint
llm_wrapper.py        # LM Studio HTTP wrapper (chat + streaming + vision/image encoding)
tools.py              # Tool/ToolRegistry — værktøjs-ramme (parse_response, build_system_prompt)
github_wrapper.py     # GitHub API: repos, issues, PRs
git_ops.py            # Git + fil operationer (write_file med syntax/dependency/route-validering)
task_tree.py          # TaskTree / TaskNode datastruktur
session_manager.py    # Session persistence (JSON), threading lock
web_searcher.py       # DuckDuckGo web scraping
skill_loader.py       # Skills-system — frontmatter, keyword-scoring, template-matching
skill_evolution.py    # SkillFlow — outcome tracking, evolution analysis (Retain/Refine/Prune/Generate)
skill_tracker.py      # Per-skill outcome recording med success_rate
module_builder.py     # Dynamisk modulbygger (eksperimentel)
model_manager.py      # OpenAI + LM Link REST API model-lister
lang.py               # Oversættelser (da/en/es/zh)
i18n.py               # Internacionaliserings-nøgler (K enum)
AGENTS.md             # Knowledge base — bugs, fixes, debugging workflow
static/index.html     # Browser-UI med drag/resize paneler, template dropdown, billed-preview
skills/               # Skills i markdown med frontmatter
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
| 💻 **Programmeringsopgave** | Kravanalyse → Arkitekturdesign → Implementeringsplan → Sikkerhedsanalyse → Kodeimplementering |
| 🏗️ **Python Arkitektur** | Arkitekturplanlægning med `write_file` output til `./docs/arkitektur.md` |
| 🖼️ **Billedanalyse** | Beskrivelse → Kontekst → Detaljer → Vurdering → Eksportér (.md) |

**Billedanalyse forudsætter:** Upload et billede via 🖼 knappen **før** du klikker Nedbryd. WebP billeder konverteres automatisk til `image/png` MIME for gemma-kompatibilitet.

## 🔧 Værktøjer

Agenten kan udføre systemoperationer via `<<<TOOL>>>` markører:

| Værktøj | Handling |
|---------|----------|
| `list_chunks` | List alle indlæste filer |
| `read_chunk` | Læs en chunk af en stor fil |
| `write_file` | Skriv fil til disk (validerer syntax, dependencies og routes) |
| `add_image` | Tilføj billede til kontekst (base64-encodes) |
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

## 🖼️ Vision / Billed-analyse

Upload billeder via 🖼 knappen eller "Gennemse" + "Læs fil". Understøtter `.png`, `.jpg`, `.webp`, `.gif`, `.bmp`.

**Billeder er session-scoped** — gemmes med sessionen, indlæses ved session-skift, ryddes ved ny session.

### Model-kompatibilitet

| Model | Format | JSON-type |
|-------|--------|-----------|
| **Gemma 4** (26b/e4b) | `data:image/png;base64,...` | `image_url` |
| Qwen / GPT / Llava | `data:image/png;base64,...` | `image_url` |

> **Vigtigt:** `image/webp` MIME afvises af Gemma 4 via LM Studio — mappes automatisk til `image/png`. Billeder placeres **før** tekst i content array (Gemma-krav).

Se `skills/vision_models.md` for fuld kompatibilitetsmatrix og `AGENTS.md` for debugging workflow.

## ✅ Validering

`write_file` udfører automatisk 3 valideringer på skrevne filer:

| Validering | Hvornår | Beskrivelse |
|------------|---------|-------------|
| **Syntax check** | `.py` filer | `ast.parse()` — returnerer `syntax_error` ved fejl |
| **Dependency check** | `.py` filer | Scanner imports mod `requirements.txt` — auto-opdaterer |
| **Route mismatch** | `.py/.html/.js` | Sammenligner frontend/backend URL'er — returnerer `route_warnings` |

## 🔐 Sikkerhed

- **Token i `.env`**: GitHub token KUN i `.env` (ikke i kode, ikke i git)
- **Prompt injection**: `<<<TOOL>>>` og `<<<DONE>>>` markører strips fra bruger-input
- **Kun registrerede værktøjer**: `ToolRegistry.execute()` nægter ukendte værktøjsnavne
- **Subprocess safety**: Git-kommandoer bruger liste-args (ingen shell)
- **LM Studio**: Kører lokalt — ingen data sendes eksternt (undtagen GitHub API)

## 🤖 LM Studio opsætning

1. Download [LM Studio](https://lmstudio.ai)
2. Download en model:
   - **Vision**: `google/gemma-4-26b-a4b` eller `gemma-4-e4b`
   - **Text**: `qwen/qwen3.6-35b-a3b` eller `qwen3-30b-a3b`
3. Start server på `http://localhost:1234`
4. Sæt context length til mindst 8192

## 🏗️ Arkitektur

```
Browser (index.html)
    │ SSE (EventSource)
    ▼
Flask API (api_server.py)
    │
    ├── decompose() → agent_core.decompose_prompt() → LLM
    ├── execute_stream() → agent_core.solve_task_stream() → LLM + Tools
    ├── /api/image/* — upload/list/clear/remove
    ├── /api/version — server version + file timestamps
    └── sessions/ (JSON persistence)
```

**Tool-loop**: `solve_task_stream` → LLM → parse response → TOOL: executér → feed resultat → LLM → ... → `<<<DONE>>>`

**PR Workflow**: Checkpoint-validering tvinger branch → commit → push → PR i korrekt rækkefølge.

**Skills**: `skill_loader.py` indlæser `skills/*.md` med frontmatter. `_match_skills()` scorer prompt og aktiverer relevante skills. `skill_evolution.py` analyserer outcomes og foreslår Retain/Refine/Prune/Generate.

**SkillFlow**: `skill_tracker.py` registrerer per-skill outcomes. Efter 15+ outcomes trigger `_evolve_if_needed()` automatisk evolution-analyse.

**Version tracking**: Server startup viser `🕐 Startet:` + `📦 llm=HH:MM:SS`. `/api/version` returnerer alle fil-timestamps.

## 📝 Features

- **Billedanalyse**: Upload → Decompose → 5-trins struktureret analyse → .md eksport
- **Vision support**: Automatisk model-detektion (VISION_KEYWORDS), format-tilpasning (raw_b64/data_url)
- **Sessions**: Gem/indlæs/omdøb — persistent JSON storage med atomic write
- **Filanalyse**: Upload filer, folder-scanning, automatisk chunk-deling af store filer
- **Streaming**: Real-time SSE output med thinking-toggle
- **Resultatkaskade**: Foregående task-resultater fødes ind i næste opgave
- **Drag/resize paneler**: Frit layout med maximize/minimize
- **Markdown eksport**: Preview + download af session rapporter
- **Multi-sprog**: UI og LLM-instruktioner på dansk, engelsk, spansk, kinesisk
- **Auto-DONE**: Forhindrer uendelig tool-loop efter 10-15 iterationer
- **Robust JSON-parsing**: `json.JSONDecoder().raw_decode()` håndterer AI-output
- **LM Link support**: OpenAI-kompatible REST API modeller

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
git commit -m "beskrivelse"
git push
```

Brug **🔀 PR Agenten** skabelonen til automatiseret PR workflow, **💻 Programmeringsopgave** til kodegenerering, og **🖼️ Billedanalyse** til vision-opgaver.
