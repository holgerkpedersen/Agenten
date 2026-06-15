# Refaktor Plan — agent_core.py (REFAC-016)

## Mål
Reducer `agent_core.py` fra ~1384 linjer til < 1000 linjer ved at flytte funktioner til eksisterende moduler.

## Nuværende Struktur i agent_core.py

### Top-level funktioner (uden for Agent-klassen):
| Funktion | Linje | Ansvarsområde |
|---|---|---|
| `log` | 28 | Logger-variabel |
| `_auto_load_location_file` | 45 | Fil-kontekst / location parsing |
| `_validate_prompt_against_code` | 75 | Prompt-validering |
| `_add_file_entry` | 148 | Fil-kontekst bygning |
| `_build_file_context` | 182 | Fil-kontekst bygning |
| `_build_fallback_tree` | 215 | Dekomposition fallback |
| `_decompose_via_llm` | 243 | LLM-baseret dekomposition |

### Agent-klasse (linje 295+):
~800+ linjer med metoder til:
- Tool-registrering (`_register_tools`, `_register_github_tools`, `_register_git_tools`, `_register_file_tools`, `_register_agent_tools`)
- Chunk/læsning (`_list_chunks`, `_read_chunk`, `_add_image`)
- Skills (`_refresh_skills`, `_match_skills`, `_format_skills_for_prompt`)
- Templates/evolution (`_get_templates`, `_record_outcome`, `_evolve_if_needed`)
- Logging (`_log`, `_record_tool_call`)
- Kontekst-håndtering (`_clean_task_name`, `_ensure_delegation_index`, `_resolve_delegations_for_context`, `_read_file_content`, `_get_single_file_context`, `_get_folder_context`, `_create_fallback_tree`, `_parse_tree_from_llm`, `_sanitize_prompt`)
- Dekomposition/eksekvering (`decompose_prompt`, `reset_execution`, `_count_tasks`, `task_tree_to_dict`, `task_tree_from_dict`, `_set_task_tools`, `solve_task`, `solve_task_stream`, `execute_tree`, `get_agent_status`, `suggest_new_module`)

## Eksisterende Moduler (skal udnyttes)
- `agent_helpers.py` — hjælpefunktioner (_resolve_t_keys_in_result, _safe_int, _extract_filenames, osv.)
- `agent_context.py` — kontekst-relaterede funktioner
- `agent_decomposition.py` — dekompositionshjælpere
- `agent_file_context.py` — fil-kontekst (_auto_load_issue_files)
- `agent_tools_github.py`, `agent_tools_git.py`, `agent_tools_file.py`, `agent_tools_agent.py` — tool-registrering

## Plan for Modulopdeling

### Trin 1: Flyt fil-kontekst funktioner til `agent_context.py`
**Funktioner der flyttes:**
- `_add_file_entry` (linje 148) → `agent_context.py`
- `_build_file_context` (linje 182) → `agent_context.py`

**Begrundelse:** Disse funktioner bygger fil-kontekst og hører naturligt sammen med kontekst-håndtering.

### Trin 2: Flyt dekomposition-funktioner til `agent_decomposition.py`
**Funktioner der flyttes:**
- `_build_fallback_tree` (linje 215) → `agent_decomposition.py`
- `_decompose_via_llm` (linje 243) → `agent_decomposition.py`

**Begrundelse:** Disse håndterer prompt-dekomposition og fallback-logik.

### Trin 3: Flyt location/prompt-validering til `agent_file_context.py`
**Funktioner der flyttes:**
- `_auto_load_location_file` (linje 45) → `agent_file_context.py`
- `_validate_prompt_against_code` (linje 75) → `agent_file_context.py`

**Begrundelse:** Disse funktioner håndterer location-fil loading og prompt-validering mod kode — relateret til fil-kontekst.

### Trin 4: Opdater `agent_core.py`
- Fjern de flyttede funktioner
- Tilføj imports fra de nye placeringer
- Agent-klassen forbliver i agent_core.py (den er stadig stor, men de frie funktioner reducerer filen)

## Forventet resultat
Efter refaktoren:
- `agent_context.py`: + `_add_file_entry`, `_build_file_context`
- `agent_decomposition.py`: + `_build_fallback_tree`, `_decompose_via_llm`
- `agent_file_context.py`: + `_auto_load_location_file`, `_validate_prompt_against_code`
- `agent_core.py`: Kun `log` variabel og `Agent` klasse → forventes < 1000 linjer

## Rækkefølge for eksekvering
1. Opret/opdater `agent_context.py` med de flyttede funktioner
2. Opret/opdater `agent_decomposition.py` med de flyttede funktioner
3. Opret/opdater `agent_file_context.py` med de flyttede funktioner
4. Opdater `agent_core.py` — fjern funktioner, tilføj imports
5. Kør testsuiten
