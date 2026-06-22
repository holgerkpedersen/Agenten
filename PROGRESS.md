# Refactor Eval Progress

## Alle fixes (branch `fix/refactor-eval-baseline`)

| # | Commit | Beskrivelse |
|---|--------|-------------|
| 1 | `2ffd4e5` (AIAppImp) | `parse_top_level_symbols` detekterer `ast.Assign`/`ast.AnnAssign` |
| 2 | `ef851e2` | Auto-inject konkrete `batch_extract_symbols()` kald som todos fra plan |
| 3 | `ef851e2` | Fjern `plan_phase`/`create_todo` fra Ekstraher tools |
| 4 | `e103be4` | Symbol-completeness check i todo-population |
| 5 | `67ceb8e` (AIAppImp) | Robust `reset_testrefac`: `git restore` + `git clean -fd` + retry |
| 6 | `7a00ba6` | Plan/Ekstraher prerequisite checks + Analyse 6→8 iterations |

## Seneste commit: `7a00ba6` — prerequisite checks

### Problem
`refactor_plan.md` blev oprettet af Plan-fasen, men `refactor_analyse.md` fandtes IKKE — Analyse-fasen havde fejlet (LLM'en brugte alle 6 iterationer på at læse symboler og nåede aldrig at kalde `write_file`).

### Fix
1. **Plan prerequisite**: Hvis `refactor_analyse.md` mangler → Plan fejler med det samme og beder om restart.
2. **Ekstraher prerequisite**: Hvis `refactor_plan.md` mangler → Ekstraher fejler med det samme.
3. **Analyse budget**: 6→8 iterationer (LLM skal nå: plan_phase + list_symbols + read_location x5 + write_file).

### Kør evaluering
```bash
cd C:\Dev\AIAppImprover
python -m ai_app_improver.main --models qwen3.6-27b-mtp
```
