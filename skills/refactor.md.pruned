---
name: refactor
keywords: [refactor, refaktorer, opdel, module, solid, import facade, extract, flyt, oprydning]
template: refactor
action_types: [analyze, plan, extract, update, test]
description: "Refactoring strategies: Extract Symbol for self-contained functions, Import Facade for 800+ line files with clear function groups, Delegation for large classes, Module Split for independent utilities. Includes decision guide for choosing the right strategy."
---

## Refactor (SOLID-opdeling)

Refactor large files into smaller modules following SOLID principles. This skill describes multiple strategies so you can choose the best approach for each situation.

### Refactoring Strategies

#### 1. Extract Symbol (standard)

Use `extract_symbol(filepath='src.py', name='funktionsnavn', target_module='ny_modul.py')` to move a single function or class to a new module.

**Best when:** The symbol is self-contained with few dependencies.

#### 2. Import Facade

Rewrite the source file to become an **import facade** — a thin file that only imports and re-exports symbols from sub-modules.

**Workflow:**
1. Create sub-modules grouping related functions
2. Add `from typing import Any` to each sub-module
3. In the original file, replace all function bodies with imports and re-export
4. Preserve the original file's `__all__` and constants

**CRITICAL — ALDRI slet originalfilen:** Testfiler importerer fra originalfilen. Hvis du sletter den, knækker ALLE tests.

**Best when:** The file has 800+ lines with multiple clear responsibility areas.

#### 3. Delegation (class split)

Extract methods from a large class into separate classes, keeping the original class as a facade.

#### 4. Module split (peer modules)

Split one module into several independent peer modules.

### Strategy Decision Guide

| Condition | Strategy |
|-----------|----------|
| Function/class is self-contained, few deps | Extract Symbol |
| File > 800 lines, clear function groups | Import Facade |
| Large class with shared state | Delegation |
| Independent utility functions | Module Split |

### Test File Handling

**Eksisterende testfiler skal IKKE ændres.** De importerer fra originalfilen, som efter refactoring stadig eksisterer (som import facade).

### Important Rules

- **ALDRI slet originalfilen.** Testfiler importerer fra den.
- **Preserve `__all__` og konstanter.**
- **`from typing import Any`** i hver ny `.py` fil.
- **Test efter hvert modul** med `run_tests()`.
- **`verify_refactor()`** efter hver batch.
- **`write_file(overwrite="force")** kan bruges som alternativ til `remove_symbol`.
