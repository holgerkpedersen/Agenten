# Refactor Eval Progress

## Resultater

| Commit | qwen3.6-27b-mtp | qwen3.5-122b | minimax-m2.5 | Note |
|--------|-----------------|--------------|--------------|------|
| `bfb0a06` (baseline) | 92.7/100 | 82/100 | 62/100 | STATUS-blok + skip auto-grupper |
| `1ce7f08` (fixes #3+#4) | 97.1/100 | — | — | variable detection + concrete batch todos |
| `e103be4` (fix #4b) | afventer | — | — | symbol-completeness check |

## Fixes implementeret

| # | Commit Agenten | Commit AIAppImprover | Beskrivelse |
|---|---------------|---------------------|-------------|
| 1 | — | `2ffd4e5` | `parse_top_level_symbols` detekterer `ast.Assign`/`ast.AnnAssign` variable |
| 2 | `ef851e2` | — | Auto-inject konkrete `batch_extract_symbols()` kald som todos fra plan |
| 3 | `ef851e2` | — | Fjern `plan_phase`/`create_todo` fra Ekstraher tools |
| 4 | `e103be4` | — | Symbol-completeness check: `_done = True` kun hvis ALLE planlagte symboler findes i filen |
| 5 | — | `67ceb8e` | Robust `reset_testrefac`: `git restore` + `git clean -fd` + retry ved PermissionError |

## Dybdeanalyse: Hvorfor modellerne fejler

### Qwen3.6-27b-mtp (97.1/100) — 2 manglende funktioner
`config_to_dict` og `get_allowed_extensions` mangler i config.py fordi:
1. `config.py` eksisterede fra tidligere session (stale) — `reset_testrefac` ryddede ikke op
2. Auto-populated todo markeret som `"done": True` pga. fil-eksistens
3. LLM'en sprang config-ekstraktion over → kun 8/10 symboler flyttet

**Fix #4b** (`e103be4`) løser dette ved at tjekke symbol-completeness, ikke bare fil-eksistens.

### Minimax-m2.5 (62/100) — 37% symbol-accuracy
batch_extract kald:
```
user_handler.py: get_allowed_extensions, config_to_dict, User, create_user... (config-funktioner blandet med user!)
processor.py:    get_active_users, bulk_create_users, parse_csv, filter_records... (user-funktioner blandet med processor!)
```
Grupperer efter **position i kildefilen** i stedet for semantisk kategori.

### Qwen3.5-122b (82/100) — 68% symbol-accuracy
Samme position-baserede gruppering som minimax, men via `plan_phase`:
```
Step 2: User, create_user, find_user... → user_handler.py ✅
Step 3: get_active_users, bulk_create_users, parse_csv, filter_records... → processor.py ❌
```
De 4 user-funktioner endte i processor.py fordi de stod tæt på processor-funktionerne i kildefilen.

## Næste: Kør evaluering
```bash
cd C:\Dev\AIAppImprover
python -m ai_app_improver.main --models qwen3.6-27b-mtp
```

Forventet: ~100/100 (alle 41 symboler korrekt placeret, variabler detekteret, stale filer ryddet korrekt).
