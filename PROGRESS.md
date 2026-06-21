# Refactor Eval Progress

## Baseline
| Commit | Branch | Fixes |
|--------|--------|-------|
| `026f28d` | `fix/refactor-eval-baseline` | `plan_content` UnboundLocalError + 500 error handler |

**Baseline score:** 62/100 (100% moduler, 37% symboler, 100% tests)

---

## Forbedring #1: Fjern plan_phase fra Ekstraher tools
**Commit:** `037128b`
**Ændring:** `set_task_tools` fjerner `plan_phase`, `create_todo`, `delete_todo`, `list_todos` fra refactor Ekstraher.
**Resultat:** **62/100** — uændret. Forbedring alene hjælper ikke.

---

## Forbedring #2: STATUS-blok + skip auto-grupper
**Commit:** `10ae4de`
**Ændringer:**
1. `symbol_checks.py` — ny `_parse_plan_symbol_mapping()` håndterer alle plan-formater (### N., ## Module:, tabel, label-grupper)
2. `agent_tasks.py` `_build_refactor_phase_context` — bruger `_parse_plan_symbol_mapping` i stedet for gammel parser (var tom)
3. `agent_tasks.py` `_build_initial_messages` — skipper `suggest_module_groups` når plan allerede har detaljer

**Forventet effekt:** LLM ser STATUS-blok med "config.py: DATABASE_URL, get_config, ...". Auto-genererede 8-symbol grupper vises ikke. LLM kalder én `batch_extract_symbols` per modul.

**Status:** ⏳ Afventer måling

**Kommando:** `python -m ai_app_improver.main --models minimax-m2.5`

---

## Næste steps (ikke implementeret)
- **Forbedring #3:** Block `<<<DONE>>>` ved forkert placering
