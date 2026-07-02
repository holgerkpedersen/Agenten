---
name: bugfix
keywords: [bug, fix, fejl, issue, defekt, test, tdd, rettelse, patch, crash, error, defekt]
template: bugfix
action_types: [analyze, test, write, verify]
---

## Bugfix (TDD)

Fix bugs using Test-Driven Development. This skill guides the agent through a strict Red-Green-Refactor cycle.

### Workflow

 1. **Analyse**: Read the issue with `read_issue()`. Understand the bug, its root cause, and the affected code. For Python files, use `locate()`/`read_location()` to read specific functions — NOT `read_chunk()` (which is only for non-Python files).

 2. **Test (Red)**: Write a pytest that asserts the CORRECT behavior in `tests/temp/test_BUG-XXX.py`. Call the function with the same inputs the bug describes, but ASSERT the expected correct output. The test MUST FAIL on the first run (`run_tests()`). If the test passes instead of failing, it means the test asserts the BUGGY behavior — rewrite it. If it still passes after rewriting and the code genuinely already works correctly, call `update_issue_status("resolved")`.

 3. **Implementering**: Apply the minimal code change needed. For existing files use `edit_file()` (with `old_text`/`new_text` or `symbol`). To add a new method to a class use `add_method()`. To add a module-level function use `add_function()`. Never use `write_file()` on files that already exist.

 4. **Verifikation (Green)**: Run the specific test — it MUST PASS. Then run the full suite with `run_tests()` to verify no regressions.

 5. **Opdatering**: Update the issue status to `"resolved"` with `update_issue_status()`. Include a brief resolution note.

### Rules

- **Test FIRST. Always.** Do not write any implementation code before the test exists and fails.
- **Minimal change.** Fix the bug with the fewest lines possible. Avoid unrelated refactoring unless the issue specifically requires it.
- **Run full suite.** After the fix, always run ALL tests with `run_tests()` (no args).
- **Update status.** Always mark the issue resolved after successful verification.
- **One issue at a time.** Do not attempt to fix multiple issues in one session.

### Example

For a NoneType crash at agent_core.py:985:
```
1. read_issue("BUG-003")
2. write_file("tests/temp/test_BUG-003.py") — test that calls solve_task_stream with active_tools=None
3. run_tests("tests/temp/test_BUG-003.py")  → RED (fails)
4. edit_file(agent_core.py, symbol="set_task_tools", requirements="Add None-guard before list comprehension")
5. run_tests("tests/temp/test_BUG-003.py")  → GREEN (passes)
6. run_tests() → all pass
7. update_issue_status("BUG-003", "resolved", "Added None-guard before list comprehension")
```

<!-- SkillFlow Refinement: 2026-05-30 -->
<!-- Failure patterns to address:
     - 
-->
