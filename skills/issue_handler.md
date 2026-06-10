---
name: issue_handler
keywords: [issue, handler, håndtering, bug, fix, fejl, defekt, rettelse, patch, error, task, opgave]
template: issue_handler
action_types: [analyze, read, write, test, verify]
rubrics: [{"id":"issue_read","desc":"Læste issue med read_issue før handling","check":"tool_used:read_issue"}]
---

## Issue Håndtering

Håndter issues (bugs, security, performance, tests, etc.) gennem en struktureret 4-trins proces.

### Faser

1. **Læs**: Læs issue med `read_issue()`. Forstå beskrivelse, location, impact og proposed_fix. Du må IKKE læse kildekode eller redigere filer i dette trin.

2. **Afklar**: Analyser issue-teksten og verificér om fejlen stadig eksisterer. Læs den relevante kildekode med `read_chunk()`. Kør `run_tests()` for at bekræfte testsuiten. Hvis der mangler detaljer (præcis location, acceptance criteria, repro steps), opdater med `update_issue_status()`. Du må IKKE sætte status til 'resolved' i denne fase. Hvis fejlen ALLEREDE er løst: opdater status til 'resolved' og stop. Hvis fejlen findes: fortsæt til Fix.

3. **Fix**: Læs kildekoden med `read_chunk()` — **maks 2 læsekald, skriv så straks**. Ret fejlen med `edit_file()` — brug PRÆCIS tekst fra filen (samme indentering, samme quotes). Kør `run_tests()` for at bekræfte rettelsen OG at ingen tests er gået i stykker. Du må IKKE selv markere issue som resolved — det gøres i Luk Issue.

4. **Luk Issue**: Bekræft at fix'et er implementeret og alle tests består. Opdater issue-status til 'resolved' med `update_issue_status()` og tilføj en PRÆCIS resolution_note: beskriv HVAD der blev ændret, HVORFOR, og bekræft at ALLE tests består.

### Workflow

```
Issue → Læs → Afklar → Fix → Luk Issue
                  ↓ (hvis allerede løst)
               resolved
```

### Rules

- **Ét issue ad gangen.** Forsøg ikke at løse flere issues i én session.
- **Præcis tekst:** Når du bruger `edit_file`, kopiér tekst DIREKTE fra filen — samme indentering, samme tegn.
- **Skriv ELLER redigér:** Hvis issuet kræver ny funktionalitet, brug `write_file` til at oprette nye filer. Hvis issuet kræver ændring i eksisterende kode, brug `edit_file`.
- **Stop med at læse — begynd at skrive:** Når du har læst de relevante funktioner (maks 2 læsekald), SKAL du straks kalde `edit_file` eller `write_file`. Læs ikke mere — skriv din løsning.
- **Test før luk:** Kør ALTID `run_tests()` før du afslutter Luk Issue-fasen.
- **Præcis resolution_note:** Beskriv hvad der blev ændret og hvorfor. Sig ikke "implementeret" hvis intet blev ændret.
- **Ingen status-hopping:** Kun Afklar (hvis allerede løst) og Luk Issue må sætte status til 'resolved'.

### Example

For SEC-013 (Path Traversal):
```
1. Læs: read_issue("SEC-013")
2. Afklar:
   - read_chunk("file_api_server.py", 1) — læs koden
   - KONKLUSION: 5 endpoints mangler _is_safe_path — skal fikses
3. Fix:
   - edit_file(api_server.py) — tilføj _is_safe_path check i read_file
   - edit_file(api_server.py) — tilføj check i save_to_folder
   - edit_file(api_server.py) — tilføj check i list_folder_contents
   - edit_file(api_server.py) — tilføj check i list_python_files
   - run_tests() → all pass
4. Luk Issue:
   - update_issue_status("SEC-013", "resolved",
     "Added _is_safe_path() to read_file, save_to_folder,
      list_folder_contents, list_python_files. All 314 tests pass.")
```
































































































<!-- skillflow:known_failures -->
### Kendte Fejlmønstre

Opdateret: 2026-06-10 18:25

**Hyppige fejl ved brug af denne skill:**
- 
- Læste issue med read_issue før handling

{% end skillflow:known_failures %}















































