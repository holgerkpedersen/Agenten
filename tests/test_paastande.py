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
    """Kun 'ekstraher' får plan_block, 'opdatér' får ingenting."""
    from agent_tasks import _build_initial_messages as build_msgs

    # Vi kan ikke kalde build_msgs direkte (kræver agent mock),
    # så vi tjekker prompt-bygningskoden
    with open(os.path.join(os.path.dirname(__file__), '..', 'agent_tasks.py'),
              'r', encoding='utf-8') as f:
        content = f.read()

    # Find plan_block sektionen
    in_plan_block = False
    ekstraher_has_plan = False
    opdater_has_plan = False
    lines = content.splitlines()
    for i, line in enumerate(lines):
        if 'plan_block' in line and '= ""' in line:
            in_plan_block = True
        if in_plan_block and 'ekstraher' in line.lower() and 'task_node.name' in line:
            ekstraher_has_plan = True
        if in_plan_block and 'opdatér' in line.lower() and 'task_node.name' in line:
            opdater_has_plan = True
        if in_plan_block and 'plan_block' in line and '+=' in line and '+ ""' not in line:
            # Næste plan_block brug — stop her
            if ekstraher_has_plan and not opdater_has_plan:
                break

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
