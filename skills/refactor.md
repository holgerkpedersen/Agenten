---
name: refactor
keywords: [refactor, refaktorer, opdel, ekstraher, solid, modul, oprydning]
template: refactor
action_types: [analyze, plan, extract, update, test]
protected: true
---

## Refactor — SOLID-opdeling

Opdel store filer i mindre moduler ved at flytte symboler. Hver fase bygger på den forrige.

### Workflow

 1. **Analyse**: Brug `list_symbols()` og `read_location()` for at forstå strukturen. **SIDSTE HANDLING: Gem analysen i `refactor_analyse.md` med `write_file()`** — Plan-fasen læser denne fil.

 2. **Plan**: Læs `refactor_analyse.md` (auto-indlæst) for at få analysen. Brug `write_file()` til at skrive `refactor_plan.md` med modulnavne og hvilke symboler der flyttes til hvert modul. Systemet auto-afslutter fasen når filen findes.

 3. **Ekstraher**: Brug `batch_extract_symbols()` for at flytte symbol-grupper fra planen til nye .py filer. Ét kald per modul-gruppe. Systemet auto-afslutter når alle moduler fra planen er oprettet på disk.

 4. **Opdatér**: Brug `remove_symbol()` og `add_import()` til at rydde op i kildefilen.

 5. **Test**: Kør `run_tests()` for at verificere at intet er gået i stykker.

### Regler

- **Gem analysen.** `refactor_analyse.md` bruges af Plan-fasen — skriv den altid sidst i Analyse.
- **`batch_extract_symbols` > `extract_symbol`.** Brug batch til at flytte flere symboler ad gangen til samme modul.
- **`verify_refactor()`** efter hver batch for at tjekke syntaks.
- **`run_tests()`** til sidst — bekræft at alle tests stadig passer.
- **Originalfilen må ALDRIG slettes** — tests og imports reference den.
- **`from typing import Any`** i alle nye .py filer.

### Eksempel

```
# Iterér over planens modul-grupper:
batch_extract_symbols(source='api_server.py', symbols='_RateLimiter, _is_development_mode, _rate_limit', target='middleware.py')
batch_extract_symbols(source='api_server.py', symbols='set_folder, folder_status, save_to_folder, list_folder_contents', target='folder_manager.py')
verify_refactor(source='api_server.py')
# Gentag for hver gruppe i refactor_plan.md
```

<!-- SkillFlow Refinement: 2026-06-17 -->
