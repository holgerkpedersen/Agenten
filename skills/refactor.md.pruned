---
name: refactor
keywords: [refactor, refaktorer, opdel, module, solid, import facade, extract, flyt, oprydning]
template: refactor
action_types: [analyze, plan, extract, update, test]
description: "Refactoring strategies: Extract Symbol for self-contained functions, Import Facade for 800+ line files with clear function groups, Delegation for large classes, Module Split for independent utilities."
---

## Refactor — SOLID-opdeling

Refactor large files into smaller modules following SOLID principles.

### 1. Extract Symbol
Move one function/class to a new file. Use `extract_symbol(source='fil.py', symbol_name='Funktion', target='ny.py')`.
**Når:** Funktionen er selvstændig med få afhængigheder.

### 2. Import Facade
Omskriv kildefilen til en tynd import-facade (~50 linjer) med sub-moduler.
**Når:** Filen > 800 linjer med tydelige funktionsgrupper.
**⚠️ ALDRI slet originalfilen** — tests importerer fra den.

### 3. Delegation
Store klasser med delt tilstand → facade + delegater.

### 4. Module Split
Uafhængige funktioner → peer-moduler.

### Regler
- `from typing import Any` i ALLE nye `.py` filer
- Bevar `__all__` og konstanter i facaden
- `run_tests()` efter hvert modul
- `write_file(overwrite="force")` er alternativ til `remove_symbol`
- `verify_refactor()` efter hver batch
