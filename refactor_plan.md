# Refactor Plan for agent_phase_checks.py (REFAC-023)

Mål: Opdel agent_phase_checks.py (~1000 linjer) i mindre moduler på maks 300 linjer.

## Struktur

```
phase_checks/
    __init__.py          # Re-exports alle offentlige symboler
    file_checks.py       # check_file_exists, check_files_from_plan + helpers
    text_checks.py       # check_min_text_length, check_text_contains
    tool_checks.py       # check_tool_called, check_tests_pass
    code_checks.py       # check_code_contains, _parse_module_symbols, check_symbols_covered_by_modules
    compound_checks.py   # check_all_of
    phase_config.py      # PHASE_ALIASES, TEMPLATE_PHASE_CHECKS, _resolve_phase_key
agent_phase_checks.py    # Slank facade (~100 linjer) med imports + check_phase_done
```

### 1. phase_checks/__init__.py
**Ansvar:** Re-export alle offentlige symboler så eksisterende imports fortsat virker.

### 2. phase_checks/file_checks.py
**Ansvar:** Fil-relaterede checks.
- `check_file_exists`
- `_parse_refactor_plan_modules`
- `_has_real_code`
- `_extract_modules_from_plan`
- `check_files_from_plan`

### 3. phase_checks/text_checks.py
**Ansvar:** Tekst-baserede checks.
- `check_min_text_length`
- `check_text_contains`

### 4. phase_checks/tool_checks.py
**Ansvar:** Værktøjskald-relaterede checks.
- `check_tool_called`
- `check_tests_pass`

### 5. phase_checks/code_checks.py
**Ansvar:** Kode-indholds og symbol-relaterede checks.
- `_DEFAULT_DUNDER`
- `_parse_module_symbols`
- `check_code_contains`
- `check_symbols_covered_by_modules`

### 6. phase_checks/compound_checks.py
**Ansvar:** Sammensatte checks.
- `check_all_of`

### 7. phase_checks/phase_config.py
**Ansvar:** Konfiguration og fase-navn mapping.
- `PHASE_ALIASES`
- `_resolve_phase_key`
- `TEMPLATE_PHASE_CHECKS`