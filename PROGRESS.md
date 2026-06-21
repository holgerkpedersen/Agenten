# Refactor Eval Progress

## Endelige resultater (commit `bfb0a06`)

| Model | Score | Moduler | Symbol-acc | Tests | Tid |
|-------|-------|---------|-----------|-------|-----|
| minimax-m2.5 | 62/100 | 100% | 37% | 100% | 24min |
| qwen3.5-122b-a10b | 82/100 | 100% | 68% | 100% | 15min |
| **qwen3.6-27b-mtp** 🏆 | **92.7/100** | **100%** | **88%** | **100%** | 27min |

## Ny commit: `ef851e2` — fixes #3 + #4

### Fix #3: AI App Improver — detect variables in `parse_top_level_symbols`
- `ast.Assign`/`ast.AnnAssign` detection added
- Fixes false "missing symbol" for top-level config variables
- Commit: AIAppImprover `2ffd4e5`

### Fix #4: Agenten — auto-inject batch calls + remove plan_phase from Ekstraher
1. `_auto_populate_llm_todos` injects concrete `batch_extract_symbols()` calls with exact symbols from plan (via `_parse_plan_symbol_mapping`)
2. `plan_phase` + `create_todo` removed from Ekstraher active tools
3. `_llm_has_planned = True` when plan mapped → budget nudge won't suggest plan_phase
4. Fixed latent `UnboundLocalError` in keyword-match path
- Commit: Agenten `ef851e2` on branch `fix/refactor-eval-baseline`

## Expected improvement
- qwen3.6 should go from 92.7 → ~98/100 (5 config variables now detected)
- qwen3.5/minimax should improve by following concrete todos instead of position-based groupings

## Next: Run evaluation
```bash
cd C:\Dev\AIAppImprover
python -m ai_app_improver.main --models qwen3.6-27b-mtp
```
