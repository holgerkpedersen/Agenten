---
name: refactor
keywords: [refactor, refaktorer, opdel, module, solid, import facade, extract, flyt, oprydning]
template: refactor
action_types: [analyze, plan, extract, update, test]
description: "Refactoring strategies: Extract Symbol for self-contained functions, Import Facade for 800+ line files with clear function groups, Delegation for large classes, Module Split for independent utilities."
---

# REFACTOR — SOLID OP DELING

Store filer opdeles i mindre moduler efter SOLID principper.

## VÆRKTØJER

| Strategi | Brug | Når |
|----------|------|-----|
| extract_symbol() | Flyt funktion/klasse til ny fil | Selvstændig kode, få afhængigheder |
| batch_extract_symbols() | Flyt FLERE symboler i ét kald | Mange symboler til samme modul |
| write_file() | Opret ny modulfil | Modul findes ikke endnu |
| remove_symbol() | Fjern symbol fra gammel fil | Symbolet er allerede ekstraheret |
| add_import() | Tilføj import i gammel fil | Efter remove_symbol |
| write_file(overwrite=force) | Omskriv hel fil | Import facade strategi |
| verify_refactor() | Tjek syntaks | Efter hver ændring |
| run_tests() | Verificér tests | Før og efter refactor |

## STRATEGIER

### 1. Import Facade (store filer > 800 linjer)
1. Opret sub-moduler med write_file()
2. Flyt funktioner med batch_extract_symbols()
3. Tilføj ``from typing import Any`` i ALLE nye filer
4. Originalfilen bliver facade — imports og re-export (aldrig slet!)
5. Bevar ``__all__`` og TEMPLATE_PHASE_CHECKS i facaden
6. Kør run_tests() og verify_refactor()

### 2. Extract Symbol (enkelte funktioner)
- Brug ``extract_symbol(source='fil.py', symbol_name='Funktion', target='ny.py')``
- Eller ``batch_extract_symbols()`` for flere på én gang

### 3. Delegation (store klasser)
- Bevar original klasse som facade
- Flyt metoder til delegate-klasser

### 4. Module Split (peer moduler)
- Opdel uafhængige funktioner i separate moduler

## REGLER

- **Originalfilen må ALDRIG slettes** — tests importerer fra den
- **from typing import Any** i ALLE nye .py filer
- Bevar ``__all__`` og TYPO/TEMPLATE konstanter
- ``verify_refactor()`` efter hver batch
- ``run_tests()`` efter hvert modul
- ``write_file(overwrite=force)`` er alternativ til remove_symbol
