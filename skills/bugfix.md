---
name: bugfix
keywords: [bug, fix, fejl, issue, defekt, test, tdd, rettelse, patch, crash, error, defekt]
template: bugfix
action_types: [analyze, test, write, verify]
---

## Bugfix (TDD)

Fix bugs using Test-Driven Development. This skill guides the agent through a strict Red-Green-Refactor cycle.

### Workflow

1. **Analyse**: Read the issue with `read_issue()`. Understand the bug, its root cause, and the affected code. Read relevant source files with `read_chunk()`.

2. **Test (Red)**: Write a pytest that reproduces the bug. The test MUST FAIL on the first run (`run_tests()`). If the test passes, it doesn't actually catch the bug — rewrite it.

3. **Implementering**: Apply the minimal code change needed. Use `write_file()` to update the file. Change ONLY what's necessary to fix the bug.

4. **Verifikation (Green)**: Run the specific test — it MUST PASS. Then run the full suite with `run_tests()` to verify no regressions.

5. **Opdatering**: Update the issue status to `"resolved"` with `update_issue_status()`. Include a brief resolution note.

### Rules

- **Test FIRST. Always.** Do not write any implementation code before the test exists and fails.
- **Minimal change.** Fix the bug with the fewest lines possible. No refactoring, no unrelated improvements.
- **Run full suite.** After the fix, always run ALL tests with `run_tests()` (no args).
- **Update status.** Always mark the issue resolved after successful verification.
- **One issue at a time.** Do not attempt to fix multiple issues in one session.

### Example

For a NoneType crash at agent_core.py:985:
```
1. read_issue("BUG-003")
2. Add test in test_agent_core.py that calls solve_task_stream with active_tools=None
3. run_tests("tests/test_agent_core.py::TestSetTaskTools")  → RED (fails)
4. write_file(agent_core.py) — add None-guard
5. run_tests("tests/test_agent_core.py::TestSetTaskTools")  → GREEN (passes)
6. run_tests() → all pass
7. update_issue_status("BUG-003", "resolved", "Added None-guard before list comprehension")
```

<!-- SkillFlow Refinement: 2026-05-30 -->
<!-- Failure patterns to address:
     - 
-->
