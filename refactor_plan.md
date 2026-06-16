# Refactor Plan for agent_phase_checks.py

## Baggrund
`agent_phase_checks.py` er ~1000 linjer og bryder Single Responsibility Principle. Filen indeholder flere forskellige ansvarsområder der bør adskilles.

## Modulstruktur

### 1. `file_checks.py` — Fil-relaterede checks (linjer 36-153)
**Ansvar:** Tjek om filer findes, parser refactor plans for modulnavne, valider reel kode i filer.

| Funktion | Linje | Beskrivelse |
|---|---|---|
| `check_file_exists` | 38 | Return (passed, message) for file_exists check |
| `_parse_refactor_plan_modules` | 67 | Parse alle *.py moduler nævnt i refactor_plan.md |
| `_has_real_code` | 90 | Tjek om en Python-fil indeholder reel kode |
| `_extract_modules_from_plan` | 116 | Extract module filenames fra refactor plan markdown |
| `check_files_from_plan` | 155 | Return (passed, message) for files_from_plan check |

**Dependencies:** Ingen eksterne afhængigheder af andre moduler i denne fil.

---

### 2. `text_tool_checks.py` — Tekst-, tool- og code-checks (linjer 200-353)
**Ansvar:** Tjek LLM output, værktøjskald, kode-indehold og test-resultater.

| Funktion | Linje | Beskrivelse |
|---|---|---|
| `check_text_contains` | 202 | Check at LLM's output nævner visse keywords |
| `check_min_text_length` | 220 | Return (passed, message) for min_text_length check |
| `check_tool_called` | 266 | Return (passed, message) for tool_called check |
| `check_code_contains` | 305 | Return (passed, message) for code_contains check |
| `check_tests_pass` | 355 | Return (passed, message) for tests_pass check |

**Dependencies:** Ingen afhængigheder af andre moduler.

---

### 3. `symbol_checks.py` — Symbol-dækning og compound checks (linjer 394-617)
**Ansvar:** Tjek at symboler er dækket af moduler, compound check-logik, phase aliases.

| Funktion | Linje | Beskrivelse |
|---|---|---|
| `_parse_module_symbols` | 396 | Parse a .py file and return top-level symbols |
| `check_symbols_covered_by_modules` | 419 | Return (passed, message) for symbols_covered check |
| `check_all_of` | 543 | Return (passed, message) for all_of compound check |
| `PHASE_ALIASES` | 599 | Variabel med fase-navne aliases |
| `_resolve_phase_key` | 619 | Find canonical key i template_checks matching phase_name |

**Dependencies:**
- `check_all_of` afhænger af: `check_code_contains`, `check_file_exists`, `check_files_from_plan`, `check_min_text_length`, `check_symbols_covered_by_modules`, `check_tests_pass`, `check_text_contains`, `check_tool_called`
- `_resolve_phase_key` afhænger af: `PHASE_ALIASES`

---

### 4. `phase_engine.py` — Template checks og hoved-funktion (linjer 637-939)
**Ansvar:** Template definitioner for fase-checks, hovedfunktion til at tjekke om en fase er færdig.

| Funktion | Linje | Beskrivelse |
|---|---|---|
| `TEMPLATE_PHASE_CHECKS` | 637 | Stor dict med template checks per fase |
| `check_phase_done` | 941 | Check whether the current phase should auto-complete |

**Dependencies:**
- `check_phase_done` afhænger af: `TEMPLATE_PHASE_CHECKS`, `_resolve_phase_key`, `check_all_of` og alle individuelle check-funktioner

---

## Rækkefølge for ekstraktion

1. **`file_checks.py`** — ingen dependencies på andre moduler (kan laves først)
2. **`text_tool_checks.py`** — ingen dependencies på andre moduler
3. **`symbol_checks.py`** — afhænger af checks fra modul 1 og 2 (via imports)
4. **`phase_engine.py`** — afhænger af alle ovenstående (laves sidst)

## Efter refactor

`agent_phase_checks.py` reduceres til et tyndt facade-modul der kun indeholder:
```python
from file_checks import check_file_exists, check_files_from_plan
from text_tool_checks import check_text_contains, check_min_text_length, check_tool_called, check_code_contains, check_tests_pass
from symbol_checks import check_symbols_covered_by_modules, check_all_of
from phase_engine import check_phase_done
```
