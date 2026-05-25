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
agent_core.py         # Agent-facade: init, tool-registrering, decompose, execute, tynde delegat-metoder
agent_tasks.py        # Opgaveudførelse: solve_task_stream, solve_task, handle_tool_call
agent_tree.py         # Træoperationer: parse, create_fallback_tree, record_outcome, evolve_if_needed
agent_files.py        # Fil/chunk operationer: read/write/chunk, folder-scanning (.env ekskluderet)
agent_skills.py       # Skills-matching, skabelon-konstanter (TEMPLATE_TOOLS, TEMPLATE_TASK_TOOLS)
agent_issues.py       # Issue-værktøjer: read_issue, update_issue_status, create_issue, oversize-detektion
agent_git.py          # Git/PR workflow: is_pr_workflow, extract_branch_name, verify_pr_step
api_server.py         # Flask API: SSE streaming, sessions, billed-upload, version, issues endpoint
llm_wrapper.py        # LM Studio HTTP wrapper (chat + streaming + vision/image encoding)
tools.py              # Tool/ToolRegistry — værktøjs-ramme (parse_response, build_system_prompt)
task_tree.py          # TaskTree / TaskNode datastruktur
config.py             # Centrale konstanter (CHUNK_SIZE, timeout, max_tokens)
git_ops.py            # Git + fil operationer (write_file, edit_file med validering)
github_wrapper.py     # GitHub API: repos, issues, PRs
session_manager.py    # Session persistence (JSON), threading lock
web_searcher.py       # DuckDuckGo web scraping
ddg_search.py         # DuckDuckGo search (fallback)
flow_builder.py       # Prompt flow builder
module_builder.py     # Dynamisk modulbygger (eksperimentel)
model_manager.py      # OpenAI + LM Link REST API model-lister
skill_loader.py       # Skills-system — frontmatter, keyword-scoring, template-matching
skill_evolution.py    # SkillFlow — outcome tracking, evolution analysis (Retain/Refine/Prune/Generate)
skill_tracker.py      # Per-skill outcome recording med success_rate
lang.py               # Oversættelser (da/en/es/zh)
i18n.py               # Internacionaliserings-nøgler (K enum)
AGENTS.md             # Knowledge base — bugs, fixes, debugging workflow
BRUGERVEJLEDNING.md   # Brugervejledning
static/index.html     # Browser-UI med drag/resize paneler, template dropdown, billed-preview, issues viewer
tests/                # 384 tests (pytest)
sessions/             # Sessioner i JSON-format (gem/indlæs/slet)
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
| 🖼️ **Billedanalyse** | Beskrivelse → Kontekst → Detaljer → Vurdering → Eksport (.md) |
| 🔧 **Refaktorering** | Analyse → Plan → Ekstraher → Opdatér → Test (SOLID refactoring) |
| 🧪 **Testgenerering** | Analyse → Test (Red) → Implementering → Verifikation (Green) |
| 🐛 **Bugfix (TDD)** | Analyse → Test (Red) → Implementering → Verifikation (Green) → Opdatering |
| 📋 **Issue Handler** | Læs → Analysér → Fix → Verificér → Opdatér status |

**Billedanalyse forudsætter:** Upload et billede via 🖼 knappen **før** du klikker Nedbryd. WebP billeder konverteres automatisk til `image/png` MIME for gemma-kompatibilitet.

## 🔧 Værktøjer

Agenten kan udføre systemoperationer via `<<<TOOL>>>` markører (28 værktøjer):

| Værktøj | Handling |
|---------|----------|
| `list_chunks` | List alle indlæste filer |
| `read_chunk` | Læs en chunk af en stor fil |
| `write_file` | Opret NY fil (afviser eksisterende .py filer — brug edit_file) |
| `edit_file` | Search-and-replace i eksisterende filer (med syntax-tjek) |
| `list_files` | List filer i en mappe (med filter på filtype og max dybde) |
| `create_issue` | Opret nyt issue |
| `create_refactor_issue` | Opret refactor-issue ved oversize filer |
| `read_issue` | Læs issue |
| `update_issue_status` | Opdater status på issue |
| `run_tests` | Kør pytest og returner resultater |
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

`write_file` og `edit_file` udfører automatisk 3 valideringer på skrevne filer:

| Validering | Hvornår | Beskrivelse |
|------------|---------|-------------|
| **Syntax check** | `.py` filer | `ast.parse()` — forhindrer skrivning af filer med syntax-fejl |
| **Dependency check** | `.py` filer | Scanner imports mod `requirements.txt` — auto-opdaterer |
| **Route mismatch** | `.py/.html/.js` | Sammenligner frontend/backend URL'er — returnerer `route_warnings` |
| **Overwrite guard** | `.py` filer | `write_file` afviser at overskrive eksisterende filer — brug `edit_file` |

## 🔐 Sikkerhed

- **Token i `.env`**: GitHub token KUN i `.env` (ikke i kode, ikke i git)
- **`.env` aldrig scannet**: `.env` filer ekskluderes fra folder-scanning og `read_file_content`
- **Prompt injection**: `<<<TOOL>>>` og `<<<DONE>>>` markører strips fra bruger-input. Yderligere sanitization via `_sanitize_prompt()`
- **Syntax check før skrivning**: `write_file` og `edit_file` validerer Python syntaks FØR filen skrives
- **Kun registrerede værktøjer**: `ToolRegistry.execute()` nægter ukendte værktøjsnavne
- **Per-fase tool-restriktioner**: Hver fase i en skabelon har kun adgang til relevante værktøjer
- **API key auth**: Valgfri API-key beskyttelse på `/api/*` endpoints (sæt `AGENT_API_KEY`)
- **Magic byte validering**: Billed-upload validerer magic bytes (ikke kun filendelse)
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
    │       └── Skip root node when children exist (no redundant re-execution)
    │
    ├── /api/image/* — upload/list/clear/remove
    ├── /api/issues — list all tracked issues
    ├── /api/version — server version + file timestamps
    └── sessions/ (JSON persistence)
```

**Tool-loop**: `solve_task_stream` → LLM → parse response → TOOL: executér → feed resultat → LLM → ... → `<<<DONE>>>`

**PR Workflow**: `agent_git.verify_pr_step()` tvinger branch → commit → push → PR i korrekt rækkefølge.

**Skills**: `skill_loader.py` indlæser `skills/*.md` med frontmatter. `agent_skills.match_skills()` scorer prompt og aktiverer relevante skills. `skill_evolution.py` analyserer outcomes og foreslår Retain/Refine/Prune/Generate.

**SkillFlow**: `skill_tracker.py` registrerer per-skill outcomes. Efter 15+ outcomes trigger `agent_tree.evolve_if_needed()` automatisk evolution-analyse.

**Version tracking**: Server startup viser `🕐 Startet:` + `📦 llm=HH:MM:SS`. `/api/version` returnerer alle fil-timestamps.

## 📝 Features

- **Autonom bugfix**: 🐛 Bugfix (TDD) skabelon → Analyse → Test → Implementering → Verifikation → Opdatering
- **Autonom refactoring**: 🔧 Refactor-skabelon → Analyse → Plan → Ekstraher → Opdatér → Test
- **Testgenerering**: 🧪 Generer tests for utestede klasser/funktioner/metoder
- **Billedanalyse**: Upload → Decompose → 5-trins struktureret analyse → .md eksport
- **Vision support**: Automatisk model-detektion (VISION_KEYWORDS), format-tilpasning (raw_b64/data_url)
- **Issues viewer**: 🐛 Issues knap viser alle issues med detaljer og "Brug som opgave"
- **Issue Handler**: 📋 Automatisk issue fix workflow (læs → analysér → fix → verificér)
- **Sessions**: Gem/indlæs/omdøb/slet — persistent JSON storage med atomic write
- **Filanalyse**: Upload filer, folder-scanning (med .env exkludering), automatisk chunk-deling
- **Præcise filændringer**: `edit_file` search-and-replace i stedet for full-file rewrites
- **Auto-opdagelse**: `create_issue` værktøj rapporterer nye fejl/issues under analyse
- **Streaming**: Real-time SSE output med thinking-toggle og stop-knap
- **Resultatkaskade**: Foregående task-resultater fødes ind i næste opgave
- **Drag/resize paneler**: Frit layout med maximize/minimize
- **Markdown eksport**: Preview + download af session rapporter
- **Multi-sprog**: UI og LLM-instruktioner på dansk, engelsk, spansk, kinesisk
- **Per-fase tool-restriktioner**: Hver fase har kun relevante værktøjer (f.eks. Analyse = read-only)
- **Timeout**: Task execution afbrydes efter 30 minutter (EXECUTION_TIMEOUT)
- **Auto-DONE**: Forhindrer uendelig tool-loop efter 10-15 iterationer
- **Robust JSON-parsing**: `json.JSONDecoder().raw_decode()` håndterer AI-output
- **LM Link support**: OpenAI-kompatible REST API modeller
- **384 tests**: pytest suite med test af alle moduler

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

Brug **🔀 PR Agenten** skabelonen til automatiseret PR workflow, **💻 Programmeringsopgave** til kodegenerering, **🖼️ Billedanalyse** til vision-opgaver, og **🐛 Bugfix (TDD)** til fejlretning.
