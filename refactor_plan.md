# Refaktor Plan for agent_core.py

## Baggrund
agent_core.py er 1384 linjer og bryder Single Responsibility Principle. Filen indeholder både helper-funktioner, file context håndtering, task tree operationer, tool registrering, og Agent klassen.

## Modulopdeling

### 1. `agent_helpers.py` — Utility/helper funktioner
**Ansvar:** Generelle hjælpefunktioner der ikke er knyttet til Agent klassen direkte.

**Flyt disse funktioner:**
- `_LOOKUP_CACHE` (global variabel)
- `_resolve_t_keys_in_result()` — Resolve t(K.XXX) translations
- `_safe_int()` — Konverter til int sikkert
- `_extract_filenames()` — Ekstraher filnavne fra location string
- `_auto_load_issue_files()` — Auto-load issue-relaterede filer
- `_auto_load_location_file()` — Load filer fra Location: field
- `_validate_prompt_against_code()` — Valider prompt mod symbol index
- `_run_doc_refinement()` — Kør iterativ doc-refinement

**Dependencies:** agent_files, agent_issues, config, re, os, json, subprocess, sys

---

### 2. `agent_context.py` — File context bygning
**Ansvar:** Bygning af file context til LLM prompts.

**Flyt disse funktioner:**
- `_add_file_entry()` — Tilføj fil entry til context
- `_build_file_context()` — Byg komplet file context
- `_build_fallback_tree()` — Byg fallback task tree fra template sektioner

**Dependencies:** agent_files, agent_issues, config, TaskTree, TaskNode, re, os

---

### 3. `agent_decomposition.py` — Prompt decomposition
**Ansvar:** LLM-baseret prompt decomposition og task tree generation.

**Flyt disse funktioner:**
- `_decompose_via_llm()` — Decompose prompt via LLM

**Dependencies:** agent_files, config, TaskTree, TaskNode, _build_fallback_tree (fra agent_context)

---

### 4. `agent_tools_github.py` — GitHub tool registrering
**Ansvar:** Registrering af GitHub-relaterede værktøjer.

**Flyt disse metoder fra Agent klassen:**
- `_register_github_tools()` 

**Dependencies:** GithubAPI, Tool, ToolRegistry

---

### 5. `agent_tools_git.py` — Git tool registrering
**Ansvar:** Registrering af git-relaterede værktøjer.

**Flyt disse metoder fra Agent klassen:**
- `_register_git_tools()`

**Dependencies:** git_ops, Tool, ToolRegistry, _safe_int (fra agent_helpers)

---

### 6. `agent_tools_file.py` — File tool registrering
**Ansvar:** Registrering af fil-relaterede værktøjer.

**Flyt disse metoder fra Agent klassen:**
- `_register_file_tools()`

**Dependencies:** git_ops, agent_files, Tool, ToolRegistry, _safe_int (fra agent_helpers), RefactoringEngine

---

### 7. `agent_tools_agent.py` — Agent tool registrering
**Ansvar:** Registrering af agent-relaterede værktøjer.

**Flyt disse metoder fra Agent klassen:**
- `_register_agent_tools()`

**Dependencies:** agent_issues, agent_pdf, agent_logs, Tool, ToolRegistry, _safe_int (fra agent_helpers), WebSearcher

---

### 8. `agent_delegation.py` — Delegation index og resolution
**Ansvar:** Håndtering af delegation index og resolution af delegerede funktioner.

**Flyt disse metoder fra Agent klassen:**
- `_ensure_delegation_index()`
- `_resolve_delegations_for_context()`

**Dependencies:** agent_files, os, log

---

## Rækkefølge for ekstraktion

1. **agent_helpers.py** — Ingen dependencies på andre nye moduler
2. **agent_context.py** — Dependencies: agent_helpers (indirekte via agent_files)
3. **agent_decomposition.py** — Dependencies: agent_context (_build_fallback_tree)
4. **agent_tools_github.py** — Dependencies: ingen andre nye moduler
5. **agent_tools_git.py** — Dependencies: agent_helpers (_safe_int)
6. **agent_tools_file.py** — Dependencies: agent_helpers (_safe_int)
7. **agent_tools_agent.py** — Dependencies: agent_helpers (_safe_int)
8. **agent_delegation.py** — Dependencies: ingen andre nye moduler

## Agent klassen efter refaktor

Agent klassen vil kun indeholde:
- `__init__()` — Initialisering
- `_register_tools()` — Kaller de 4 _register_*_tools metoder (flyttet til moduler)
- `_add_image()`, `_list_chunks()`, `_read_chunk()` — Kort proxy-metoder
- `_refresh_skills()`, `_match_skills()`, `_format_skills_for_prompt()`, `_get_templates()` — Skill proxy-metoder
- `_record_outcome()`, `_evolve_if_needed()` — Tree proxy-metoder
- `_log()`, `_record_tool_call()` — Logging metoder
- `_clean_task_name()` — Kort proxy
- `_read_file_content()`, `_get_single_file_context()`, `_get_folder_context()` — File context proxy-metoder
- `_create_fallback_tree()`, `_parse_tree_from_llm()` — Tree proxy-metoder
- `_sanitize_prompt()` — Prompt sanitization
- `decompose_prompt()` — Hoved decomposition metode (bliver i Agent)
- `reset_execution()` — Reset execution state
- `_count_tasks()`, `task_tree_to_dict()`, `task_tree_from_dict()` — Tree serialisering
- `_set_task_tools()` — Task tool setup
- `solve_task()`, `solve_task_stream()` — Task løsning
- `execute_tree()` — Tree eksekvering
- `get_agent_status()` — Status query
- `suggest_new_module()` — Modul suggestion

## Estimeret resultat
- agent_core.py: ~400 linjer (ned fra 1384)
- 8 nye moduler med tilsammen ~984 linjer
- Hvert modul har et klart ansvarsområde
