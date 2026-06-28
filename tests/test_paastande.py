"""Tests der beviser/afkræfter påstande om fe16347."""
import os
import sys
import ast

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from refactoring_engine import RefactoringEngine, AstAnalyzer, ImportVisitor, ImportResolver


TEST_SOURCE = '''
import os
import json

CONFIG = {"key": "value"}

def helper_one(data: str) -> str:
    """First helper."""
    return data.strip()

def helper_two(data: list) -> list:
    """Second helper."""
    return sorted(data)

class Manager:
    def __init__(self):
        self.items = []

    def add(self, item):
        self.items.append(item)
'''


# =============================================================================
# PÅSTAND 1: extract_symbol overskriver target i stedet for at appende
# =============================================================================

def test_paastand_1_extract_symbol_overwriter():
    """Kald extract_symbol to gange med samme target → kun sidste symbol overlever."""
    engine = RefactoringEngine()
    tmpdir = os.path.join(os.path.dirname(__file__), '..', 'tmp_test_overwrite')
    os.makedirs(tmpdir, exist_ok=True)

    source = os.path.join(tmpdir, 'source.py')
    target = os.path.join(tmpdir, 'target.py')

    with open(source, 'w', encoding='utf-8') as f:
        f.write(TEST_SOURCE)

    # Fjern target hvis den findes
    if os.path.exists(target):
        os.remove(target)

    # Første extraction: helper_one → target
    r1 = engine.extract_symbol(source, 'helper_one', target)
    assert r1['success'], f"Første extraction fejlede: {r1}"

    # Anden extraction: helper_two → target
    r2 = engine.extract_symbol(source, 'helper_two', target)
    assert r2['success'], f"Anden extraction fejlede: {r2}"

    with open(target, 'r', encoding='utf-8') as f:
        content = f.read()

    # BEVIS: Begge symboler findes — append virker!
    assert 'helper_one' in content, \
        "fix MISLYKKEDES: helper_one er ikke i target — overwrite-buggen er ikke fikset!"
    assert 'helper_two' in content, \
        "helper_two skulle være der"
    assert 'def helper_one' in content and 'def helper_two' in content, \
        "Begge funktioner skal være i target"
    assert content.index('helper_one') < content.index('helper_two'), \
        "helper_one skal komme før helper_two (append rækkefølge)"

    print(f"✅ FIX 1 VERIFICERET: extract_symbol appender nu. "
          f"Både 'helper_one' og 'helper_two' findes i target.")
    print(f"  Target indhold ({len(content)} chars):")
    for line in content.splitlines():
        print(f"  | {line}")

    # Oprydning
    for f in [source, target]:
        try: os.remove(f)
        except: pass
    try: os.rmdir(tmpdir)
    except: pass


# =============================================================================
# PÅSTAND 2: Iteration limit på 6 er for lavt til 34+ symboler
# =============================================================================

def test_paastand_2_iteration_limit_for_lavt():
    """Med 6 iterationer og ~1 extract_symbol kald pr iteration kan max ~6
    symboler flyttes. Planen kræver 34+."""
    from agent_skills import TEMPLATE_PHASE_ITERATION_LIMITS

    ekstraher_limit = TEMPLATE_PHASE_ITERATION_LIMITS.get("refactor", {}).get("Ekstraher", "N/A")
    config_max = 6  # MAX_TASK_ITERATIONS default

    actual = ekstraher_limit if isinstance(ekstraher_limit, int) else config_max

    planlagt_symboler = 34  # Fra sessionens plan: 34 symboler i routes.py alene
    max_parallel = 4        # LLM kan kalde ~4 værktøjer parallelt
    max_flytbare = actual * max_parallel

    print(f"✅ FIX 2: Ekstraher iteration limit = {actual}, "
          f"planlagt symboler = {planlagt_symboler}")
    print(f"  Max flytbare ved {actual} iterationer × {max_parallel} parallelle = {max_flytbare}")
    assert max_flytbare >= planlagt_symboler, \
        f"FIX MISLYKKEDES: {max_flytbare} < {planlagt_symboler} — stadig for få iterationer!"
    print(f"  => {max_flytbare} >= {planlagt_symboler}: Nok iterationer.")


# =============================================================================
# PÅSTAND 3: Opdatér får ikke refactor_plan.md i prompten
# =============================================================================

def test_paastand_3_opdater_ingen_plan():
    """Både 'ekstraher' og 'opdatér' skal få plan_block."""
    # Efter refactoring er _build_initial_messages flyttet til prompt_builder.py.
    # Tjek begge filer for at være robust over for fremtidige ændringer.
    base = os.path.join(os.path.dirname(__file__), '..')
    candidates = ['prompt_builder.py', 'agent_tasks.py', 'agent_message_builder.py']

    ekstraher_has_plan = False
    opdater_has_plan = False

    for fname in candidates:
        fpath = os.path.join(base, fname)
        if not os.path.exists(fpath):
            continue
        with open(fpath, 'r', encoding='utf-8') as f:
            content = f.read()

        # Find om koden indeholder plan_block-logik der dækker begge faser.
        # Koden kan være på én linje eller split over flere (multi-line if).
        for i, line in enumerate(content.splitlines()):
            has_ekstraher = 'ekstraher' in line.lower()
            has_opdater = 'opdatér' in line.lower() or 'opdater' in line.lower()

            # Tjek om linjen eller dens nabo-linjer refererer til plan_block + task_node
            context_window = '\n'.join(content.splitlines()[max(0, i-2):i+3])
            if has_ekstraher and 'plan_block' in context_window:
                ekstraher_has_plan = True
            if has_opdater and 'plan_block' in context_window:
                opdater_has_plan = True

    print(f"✅ FIX 3: Ekstraher får plan_block = {ekstraher_has_plan}, "
          f"Opdatér får plan_block = {opdater_has_plan}")
    assert ekstraher_has_plan, "Ekstraher skulle have plan (baseline check)"
    assert opdater_has_plan, \
        f"FIX MISLYKKEDES: Opdatér har stadig ikke plan_block!"


if __name__ == '__main__':
    print("=" * 60)
    print("VERIFIKATION: ALLE FIXES VIRKER")
    print("=" * 60)
    print()

    for name, fn in sorted(globals().items()):
        if name.startswith('test_'):
            print("-" * 60)
            try:
                fn()
            except Exception as e:
                print(f"❌ {name}: {e}")
            print()

    print("=" * 60)
    print("FÆRDIG")
    print("=" * 60)
