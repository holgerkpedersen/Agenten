"""Test at refactor af agent_phase_checks.py er korrekt."""
import sys
import os

def test_modules_exist():
    """Alle moduler skal eksistere."""
    modules = ['file_checks.py', 'text_tool_checks.py', 'symbol_checks.py', 'phase_engine.py']
    for m in modules:
        path = os.path.join(os.path.dirname(__file__), m)
        assert os.path.exists(path), f"Modul {m} findes ikke"
    print("✓ Alle moduler eksisterer")

def test_agent_phase_checks_is_small():
    """agent_phase_checks.py skal være reduceret til < 100 linjer."""
    path = os.path.join(os.path.dirname(__file__), 'agent_phase_checks.py')
    with open(path, encoding='utf-8') as f:
        lines = len(f.readlines())
    assert lines < 100, f"agent_phase_checks.py er {lines} linjer — forventet < 100"
    print(f"✓ agent_phase_checks.py er {lines} linjer")

def test_imports_work():
    """Import af alle moduler skal virke."""
    from file_checks import check_file_exists, check_files_from_plan
    from text_tool_checks import check_text_contains, check_min_text_length, check_tool_called, check_code_contains
    from symbol_checks import check_symbols_covered_by_modules
    from phase_engine import check_tests_pass, check_all_of, check_phase_done
    print("✓ Alle imports virker")

def test_facade_exports():
    """agent_phase_checks.py skal eksportere alle symboler."""
    import agent_phase_checks as apc
    expected = [
        'check_file_exists', 'check_files_from_plan',
        'check_text_contains', 'check_min_text_length',
        'check_tool_called', 'check_code_contains',
        'check_symbols_covered_by_modules',
        'check_tests_pass', 'check_all_of', 'check_phase_done'
    ]
    for name in expected:
        assert hasattr(apc, name), f"agent_phase_checks mangler {name}"
    print("✓ Facade eksporterer alle symboler")

if __name__ == '__main__':
    test_modules_exist()
    test_agent_phase_checks_is_small()
    test_imports_work()
    test_facade_exports()
    print("\n✅ Alle tests bestået!")
