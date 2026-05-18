"""
SkillFlow Integration Test - fully automated, no LLM calls needed.

Usage:
    python test_skillflow.py          # Quick test (all mechanisms)
    python test_skillflow.py --live   # Also runs 1 real prompt (requires LLM)
"""

import sys
import os
import json
import time
import argparse
import inspect

# Ensure UTF-8 output in console (avoids crashes on emoji/log chars)
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from skill_tracker import SkillTracker
from skill_loader import SkillLoader

PASS = 0
FAIL = 0
TOTAL_TESTS = 0
SECTION_RESULTS = []


def section(title):
    SECTION_RESULTS.append({"title": title, "pass": 0, "fail": 0})
    print()
    print("=" * 55)
    print("  " + title)
    print("=" * 55)


def check(description, condition):
    global PASS, FAIL, TOTAL_TESTS
    TOTAL_TESTS += 1
    if condition:
        print("  [OK] " + description)
        PASS += 1
        if SECTION_RESULTS:
            SECTION_RESULTS[-1]["pass"] += 1
    else:
        print("  [FAIL] " + description)
        FAIL += 1
        if SECTION_RESULTS:
            SECTION_RESULTS[-1]["fail"] += 1


# =====================================================================
#  SETUP - isolated tracker, monkey-patched into both modules
# =====================================================================
class TestTracker(SkillTracker):
    DATA_FILE = "skill_outcomes_test.json"


_test_tracker = TestTracker()
_test_tracker.clear()

# Patch both skill_evolution and skill_loader to use test tracker
import skill_evolution
skill_evolution.tracker = _test_tracker


def _patched_get_success_rate(skill_name):
    stats = _test_tracker.get_stats(skill_name, recent=50)
    return stats.get("success_rate", 0)


SkillLoader._get_success_rate = staticmethod(_patched_get_success_rate)

print()
print("  " + "=" * 50)
print("     SKILLFLOW INTEGRATION TEST SUITE")
print("  " + "=" * 50)

# =====================================================================
#  TEST 1: Action-Type Deduction
# =====================================================================
section("1. Action-Type Deduction (evolution._deduce_action_type)")

check("'analyser koden' -> contains 'analyze'",
      'analyze' in skill_evolution._deduce_action_type("analyser koden i agent_core.py"))

check("'read the file' -> contains 'read'",
      'read' in skill_evolution._deduce_action_type("read the file and show content"))

check("'opret commit push' -> 'write' + 'git'",
      'write' in skill_evolution._deduce_action_type("opret et commit og push") and
      'git' in skill_evolution._deduce_action_type("opret et commit og push"))

check("'search the web' -> contains 'search'",
      'search' in skill_evolution._deduce_action_type("search the web for documentation"))

check("'Hvad er hovedstaden' -> ['general']",
      skill_evolution._deduce_action_type("Hvad er hovedstaden i Danmark") == ["general"])

check("'create pull request on GitHub' -> 'write' + 'github'",
      'write' in skill_evolution._deduce_action_type("create a pull request on GitHub") and
      'github' in skill_evolution._deduce_action_type("create a pull request on GitHub"))

# =====================================================================
#  TEST 2: Scoring Engine
# =====================================================================
section("2. Scoring Engine (keyword + action_type + success_rate)")

skills = SkillLoader.load_all()
check("At least 1 skill loaded", len(skills) >= 1)

kodeanalyse = next((s for s in skills if s['name'] == 'kodeanalyse'), None)
check("kodeanalyse skill found", kodeanalyse is not None)

if kodeanalyse:
    score_match = SkillLoader._score("analyser koden i agent_core.py", kodeanalyse)
    score_no_match = SkillLoader._score("Hvad er vejret i dag?", kodeanalyse)
    check("Matching task scores higher than non-matching",
          score_match > score_no_match)
    print("       score_match=" + str(score_match) + ", score_no_match=" + str(score_no_match))

# action_type bonus test
test_skill = {
    "name": "test_analyze",
    "keywords": ["kode", "analyse"],
    "action_types": ["analyze"],
    "min_score": 1,
    "base": False,
    "description": "",
    "body": "",
}
score_with_at = SkillLoader._score("analyser koden", test_skill)
test_skill_no_at = dict(test_skill, action_types=[])
score_without_at = SkillLoader._score("analyser koden", test_skill_no_at)
check("action_types bonus increases score",
      score_with_at > score_without_at)
print("       with action_types=" + str(score_with_at) + ", without=" + str(score_without_at))

# Seed success_rate data with matching skill name
_test_tracker.record("test_analyze", "unit test", True, duration_ms=10)
_test_tracker.record("test_analyze", "unit test", True, duration_ms=10)
_test_tracker.record("test_analyze", "unit test", False, duration_ms=10)

score_with_sr = SkillLoader._score("analyser koden", test_skill)
check("Score with success_rate > 0 gets boost multiplier",
      score_with_sr > score_with_at)
print("       base score=" + str(score_with_at) + ", with success_rate=" + str(score_with_sr))

# =====================================================================
#  TEST 3: Evolution Engine
# =====================================================================
section("3. Evolution Engine (analyze -> actions)")

seed_data = [
    ("resume", True), ("resume", True), ("resume", True),
    ("resume", True), ("resume", True), ("resume", False),
    ("kodeanalyse", True), ("kodeanalyse", True), ("kodeanalyse", False),
    ("kodeanalyse", False), ("kodeanalyse", False),
    ("git_pr", False), ("git_pr", False), ("git_pr", True),
    ("git_pr", False), ("git_pr", False),
    ("__none__", True), ("__none__", True), ("__none__", True),
]
task_map = {
    "resume": "Lav et resume af filen",
    "kodeanalyse": "Analyser koden i modulet",
    "git_pr": "Opret branch og commit og push",
    "__none__": "Hvad er hovedstaden i Danmark?",
}
for skill, success in seed_data:
    _test_tracker.record(skill, task_map.get(skill, "test"), success, duration_ms=500)

check("Seeded >= 15 outcomes", _test_tracker.total_outcomes >= 15)

result = skill_evolution.analyze()
check("Evolution analysis returns 'ok'",
      result.get("status") == "ok")
check("At least 1 action produced",
      len(result.get("actions", [])) >= 1)

action_types_found = set(a["action"] for a in result.get("actions", []))
print("       Actions: " + str(action_types_found))
for a in result.get("actions", []):
    print("         -> " + a['action'].ljust(8) + " " + a['skill'][:20].ljust(20) + " " + a.get('reason', '')[:60])

check("Retain action found (>=80% success skills exist)",
      "retain" in action_types_found)
check("Refine or Prune action found (mixed/poor skills exist)",
      bool(action_types_found & {"refine", "prune"}))

dry_results = skill_evolution.apply_evolution_actions(result["actions"], dry_run=True)
check("Dry-run apply returns all results",
      len(dry_results) == len(result["actions"]))
for r in dry_results:
    check("  Dry-run: " + r['action'] + " on '" + r['skill'] + "'",
          r.get("dry_run", False))

# =====================================================================
#  TEST 4: Skill Matching
# =====================================================================
section("4. Skill Matching (find_for_task)")

matched = SkillLoader.find_for_task("Analyser koden i agent_core.py", skills)
check("find_for_task returns a skill for code analysis",
      matched is not None)

all_matched = SkillLoader.find_all_for_task("Opret et commit og push til GitHub", skills, top=3)
check("find_all_for_task returns top skills",
      len(all_matched) >= 1)
if all_matched:
    names = [s["name"] for s in all_matched]
    print("       Top matches: " + str(names))

check("base skill is always included",
      any(s.get("base") for s in all_matched))

# =====================================================================
#  TEST 5: End-to-End Hook Verification (only with --live)
# =====================================================================
if "--live" in sys.argv:
    section("5. End-to-End Hook (real prompt -> tracker)")

    from agent_core import Agent

    agent = Agent()
    agent.show_thinking = False

    sig = inspect.signature(agent._record_outcome)
    check("agent._record_outcome() accepts (self, task_node)",
          len(sig.parameters) == 1)
    check("agent._evolve_if_needed() exists and callable",
          callable(agent._evolve_if_needed))
    check("agent._task_start_time attribute exists",
          hasattr(agent, '_task_start_time'))

    prompt = "Lav et resume af skill_loader.py"
    print("\n  Decomposing: " + prompt[:50] + "...")
    tree = agent.decompose_prompt(prompt)
    check("Decomposition succeeded", tree is not None)

    if tree:
        print("  Executing root node...")
        for event in agent.solve_task_stream(agent.task_tree.root, agent.original_prompt):
            if event["type"] == "done":
                print("  Task done: " + event['result'][:80])
                break

        skillflow_logs = [
            e for e in agent.agent_log
            if e.get("level") == "SKILLFLOW"
        ]
        print("       Agent log entries: " + str(len(agent.agent_log)))
        if skillflow_logs:
            for log in skillflow_logs:
                print("       [SKILLFLOW] " + log.get('message', '') + ": " + str(log.get('detail', ''))[:60])
else:
    print()
    print("  [SKIP] Live prompt test. Use --live to include (needs LLM server).")

# =====================================================================
#  SUMMARY
# =====================================================================
print()
print("=" * 55)
total = PASS + FAIL
pct = (PASS / total * 100) if total else 0
if FAIL == 0:
    print("  ALL " + str(PASS) + "/" + str(total) + " TESTS PASSED (" + str(int(pct)) + "%)")
else:
    print("  " + str(PASS) + "/" + str(total) + " passed, " + str(FAIL) + " failed (" + str(int(pct)) + "%)")
print("=" * 55)

for sr in SECTION_RESULTS:
    st = sr["pass"] + sr["fail"]
    mark = "[OK]" if sr["fail"] == 0 else "[FAIL]"
    print("  " + mark + " " + sr['title'].ljust(40) + " " + str(sr['pass']) + "/" + str(st))
print()

# Cleanup
_test_tracker.clear()
if os.path.exists(_test_tracker._path()):
    os.remove(_test_tracker._path())
print("  (test data cleaned up)")
