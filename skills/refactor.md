---
name: refactor
keywords: [refactor, refaktorer, opdel, ekstraher, solid, modul, oprydning]
template: refactor
action_types: [analyze, plan, extract, update, test]
---

## Refactor — SOLID-opdeling

Opdel store filer i mindre moduler ved at flytte symboler. Systemet auto-indlæser `refactor_plan.md` og symbol-listen — stol på dem.

### Workflow

 1. **Analyse**: Læs `refactor_plan.md` (auto-indlæst i din prompt). Den indeholder ALLE modulopdelinger og symbol-grupper. Kald IKKE `list_symbols` — du har allerede alle data.

 2. **Plan**: Brug `write_file()` til at skrive `refactor_plan.md` med modulnavne og hvilke symboler der flyttes til hvert modul. Systemet auto-afslutter fasen når filen findes med mindst 5 moduler.

 3. **Ekstraher**: Brug `batch_extract_symbols()` for at flytte symbol-grupper fra planen til nye .py filer. Ét kald per modul-gruppe. Systemet auto-afslutter når alle moduler fra planen er oprettet på disk.

 4. **Opdatér**: Brug `remove_symbol()` og `add_import()` til at rydde op i kildefilen.

 5. **Test**: Kør `run_tests()` for at verificere at intet er gået i stykker.

### Regler

- **Stol på planen.** `refactor_plan.md` + symbol-listen er auto-indlæst i din prompt. Du behøver IKKE `list_symbols`.
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
