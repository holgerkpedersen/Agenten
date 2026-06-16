---
name: refactor
keywords: [refactor, refaktorer, solid, opdel, split, module, ekstraher, restructure, reorganize, single responsibility]
template: refactor
action_types: [analyze, write, verify]
---

## Refaktorer efter SOLID

Opdel en monolitisk Python-fil i moduler efter SOLID-principperne. Følg faserne STRENGT — spring ikke over, læs ikke unødigt.

### Workflow

**1. Analyse**
- Brug `list_symbols(filepath)` for at se ALLE symboler i filen
- Brug `read_location(filepath, name)` til at læse specifikke funktioner/klasser
- Læs IKKE hele filen med `read_chunk`
- Identificér ansvarsområder: routes, session-håndtering, fil-operationer, model-loading, image-håndtering, osv.
- Notér hvilke funktioner der hører til hvilket ansvarsområde
- **Læs IKKE tool-implementering** — `agent_files.py:list_symbols` er et værktøj, du skal BRUGE det, ikke læse dets kildekode

**2. Plan**
- Beslut modulstruktur. Typiske moduler for en Flask-app:
  - `routes.py` — endpoint-definitioner
  - `session_manager.py` — session CRUD
  - `file_handler.py` — upload/download/folder
  - `model_manager.py` — model-loading/unloading
  - `image_handler.py` — image upload/list/clear
  - `security.py` — rate limiting, API key check
- Skriv planen med `write_file("refactor_plan.md", ...)`
- Planen skal indeholde: hvilket modul der oprettes, hvilke funktioner der flyttes dertil, i hvilken rækkefølge
- OUTPUT: `refactor_plan.md` — ELLERS har du ikke lavet planen

**3. Ekstraher**
- Din FØRSTE handling SKAL være `write_file` med et nyt .py filnavn som `routes.py`
- Skriv IKKE til `refactor_plan.md` — den er allerede oprettet i Plan-fasen
- Opret ÆGTE modulfiler: `routes.py`, `session_manager.py`, `file_handler.py`, osv.
- Kopiér koden FRA api_server.py ind i hver ny fil
- Læs INTET før du skriver. Al koden kender du allerede fra Analyse-fasen
- Opret ÉN fil ad gangen. <<<DONE>>> først når ALLE nye .py filer er oprettet

**4. Opdat\u00e9r**
- Brug `edit_file` til at \u00e6ndre api_server.py. L\u00e6s MAXIMALT \u00c9N funktion med read_location, KALD derefter edit_file
- Fjern den kode der blev flyttet til modulerne
- Tilf\u00f8j `from <modul> import <funktion>` i stedet
- Brug IKKE `write_file` — api_server.py findes allerede
- <<<DONE>>> f\u00f8rst n\u00e5r edit_file lykkes

**5. Test**
- Kør `run_tests()` — HELE testsuiten
- Hvis tests fejler: ret import-stier med `edit_file` og genkør
- Når ALLE tests består: `update_issue_status("REFAC-xxx", "resolved", "...")`
- OUTPUT: tests der består + issue sat til resolved

### Vigtige regler

- **Læs IKKE værktøjskode.** `list_symbols` er et registreret værktøj — kald det som `<<<TOULE>>>`, søg ikke efter dets kildekode
- **Læs IKKE før du skriver i Ekstraher-fasen.** Al nødvendig information er allerede indsamlet i Analyse
- **Én fil ad gangen.** Hvis api_server.py er 1500+ linjer, opret EN fase per modul (f.eks. "Ekstraher routes", "Ekstraher session")
- **edit_file kræver PRÆCIS tekst.** Brug `read_location` først for at se den nøjagtige kode, kopiér den direkte ind i `old_text`
- **Kør HELE testsuiten.** `run_tests()` uden args. Ikke kun en enkelt test.

### Eksempel på modulstruktur

For en Flask-app som `api_server.py`:
```
api_server.py
routes.py          → @app.route-dekorerede funktioner
session_manager.py → create_session, load_session, save_current_session, rename_session
file_handler.py    → upload_file, read_file, sanitize_filename, folder operations
model_manager.py   → get_models, set_model, load_model_route, unload_model_route
rate_limiter.py    → _RateLimiter klasse, _rate_limit helper
```

### Eksempel på Ekstraher-fase

```
<<<TOULE>>>{"tool":"write_file","args":{"path":"routes.py","content":"from flask import ...\n\ndef index(): ..."}}<<<END>>>
<<<TOULE>>>{"tool":"write_file","args":{"path":"session_manager.py","content":"..."}}<<<END>>>
<<<TOULE>>>{"tool":"write_file","args":{"path":"file_handler.py","content":"..."}}<<<END>>>
<<<DONE>>>{"result":"Oprettet 3 moduler: routes.py, session_manager.py, file_handler.py"}<<<END>>>
```
