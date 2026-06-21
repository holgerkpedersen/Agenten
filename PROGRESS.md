# Refactor Eval Progress

## Baseline
| Commit | Score | Moduler | Symboler | Tests |
|--------|-------|---------|----------|-------|
| `026f28d` (baseline) | 62/100 | 100% | 37% | 100% |

---

## Forbedring #1: Fjern plan_phase fra Ekstraher tools
**Commit:** `037128b` | **Score:** 62/100 — uændret

---

## Forbedring #2: STATUS-blok + skip auto-grupper
**Commit:** `4e0fe27` | **Score:** 62/100 — uændret

---

## Konklusion
Minimax-m2.5 placerer konsekvent 37% af symboler korrekt — uanset prompt-forbedringer. LLM'en følger sin egen interne ræsonnering, ikke STATUS-blokken. Prompt-ændringer alene er utilstrækkelige.

## Næste skridt
- Prøv **anden model** (qwen3.5 eller qwen3.6) — måske bedre til at følge instruktioner
- Eller **check_symbols_placed_correctly** + **block `<<<DONE>>>`** (kræver mere kode)
- Alternativt: accepter 62/100 som minimax baseline og fokuser på at forbedre via feedback-loop (LLM får at vide "count_by_key er i file_utils.py, skal være i processor.py" og retter i næste iteration)
