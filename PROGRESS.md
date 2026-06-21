# Refactor Eval Progress

## Baseline
| Commit | Branch | Fixes |
|--------|--------|-------|
| `026f28d` | `fix/refactor-eval-baseline` | `plan_content` UnboundLocalError + 500 error handler |

**Baseline score:** 62/100 (modeller: 100%, symboler: 37%, tests: 100%)

---

## Forbedring #1: Fjern plan_phase fra Ekstraher tools
**Commit:** `037128b`  
**Branch:** `fix/refactor-eval-baseline`  
**Ændring:** `agent_tasks.py` `set_task_tools` — fjerner `plan_phase`, `create_todo`, `delete_todo`, `list_todos` fra refactor Ekstraher. Beholder `update_todo` + `done`.

**Forventet effekt:** LLM kan ikke lave 8-symbols undergrupper via `create_todo`, må eksekvere planen direkte.

**Status:** ⏳ Afventer måling

**Kommando:** `cd C:/Dev/AIAppImprover && python -m ai_app_improver.main --models minimax-m2.5`

---

## Næste steps (ikke implementeret)
- **Forbedring #2:** STATUS-blok med `symbol → modul` mapping
- **Forbedring #3:** Block `<<<DONE>>>` ved forkert placering
