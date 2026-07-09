"""Helper script to run batch_extract_symbols for each module from the plan."""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from refactoring_engine import RefactoringEngine

engine = RefactoringEngine()

# Modul 1: refactor_utils.py — Utility-funktioner og konstanter
print("=" * 60)
print("Modul 1: refactor_utils.py")
print("=" * 60)
result = engine.batch_extract_symbols(
    source='refactoring_engine.py',
    symbols=[
        'log',
        '_BUILTINS',
        '_BUILTINS_TYPING',
        '_KNOWN_SYMBOL_IMPORTS',
        '_KNOWN_MODULE_SYMBOLS',
        '_extracted_registry',
        '_atomic_replace',
        '_parse_symbols_list',
        '_auto_add_known_imports',
        '_list_top_level_symbol_names',
        '_find_unresolved_local_deps',
        '_detect_import_cycle_risk',
        '_split_imports_from_code',
        '_registry_key',
        '_is_nested_function',
        'clear_extracted_registry',
        '_mark_extracted',
        '_is_already_extracted',
        '_extract_module_from_import',
        '_has_back_import'
    ],
    target='refactor_utils.py'
)
print(f"Resultat: {result.get('succeeded')}/{result.get('total')} succesfuld")
for r in result.get('results', []):
    status = "OK" if r.get('success') else f"FAIL: {r.get('error', '')}"
    print(f"  {r['symbol']}: {status}")

# Modul 2: refactor_error.py — Error og Backup
print("\n" + "=" * 60)
print("Modul 2: refactor_error.py")
print("=" * 60)
result = engine.batch_extract_symbols(
    source='refactoring_engine.py',
    symbols=[
        'RefactoringError',
        'FileSnapshot'
    ],
    target='refactor_error.py'
)
print(f"Resultat: {result.get('succeeded')}/{result.get('total')} succesfuld")
for r in result.get('results', []):
    status = "OK" if r.get('success') else f"FAIL: {r.get('error', '')}"
    print(f"  {r['symbol']}: {status}")

# Modul 3: ast_analyzer.py — AST-analyse og Visitor-pattern
print("\n" + "=" * 60)
print("Modul 3: ast_analyzer.py")
print("=" * 60)
result = engine.batch_extract_symbols(
    source='refactoring_engine.py',
    symbols=[
        'ImportVisitor',
        'SymbolNode',
        'DependencyGraph',
        'AstAnalyzer',
        'ImportResolver'
    ],
    target='ast_analyzer.py'
)
print(f"Resultat: {result.get('succeeded')}/{result.get('total')} succesfuld")
for r in result.get('results', []):
    status = "OK" if r.get('success') else f"FAIL: {r.get('error', '')}"
    print(f"  {r['symbol']}: {status}")

# Modul 4: code_modifier.py — Kode-modifikation
print("\n" + "=" * 60)
print("Modul 4: code_modifier.py")
print("=" * 60)
result = engine.batch_extract_symbols(
    source='refactoring_engine.py',
    symbols=[
        'CodeModifier'
    ],
    target='code_modifier.py'
)
print(f"Resultat: {result.get('succeeded')}/{result.get('total')} succesfuld")
for r in result.get('results', []):
    status = "OK" if r.get('success') else f"FAIL: {r.get('error', '')}"
    print(f"  {r['symbol']}: {status}")

print("\n" + "=" * 60)
print("Alle moduler behandlet!")
print("=" * 60)
