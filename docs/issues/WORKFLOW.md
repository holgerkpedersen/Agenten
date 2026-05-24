# Issue Resolution Workflow

A structured process for taking an issue from discovery through analysis, implementation, and verification.

---

## Phase 1: Triage

**Goal:** Understand the issue and decide whether to act.

| Step | Action | Output |
|------|--------|--------|
| 1.1 | Read the issue thoroughly. Identify type (bug, security, feature, refactor). | Classification |
| 1.2 | Check `docs/issues/observed/issues.json` — is it already tracked? | Dedup confirmation |
| 1.3 | Reproduce the issue. Write down exact steps. | Reproduction steps |
| 1.4 | Assess severity: critical / high / medium / low. | Severity label |
| 1.5 | Assign priority: P0 (immediate) / P1 (this sprint) / P2 (next sprint) / P3 (backlog). | Priority label |

---

## Phase 2: Analysis

**Goal:** Understand root cause and identify affected code.

```
1. Read the file(s) at the reported location.
2. Trace the full call path:
   - What calls this code?
   - What data flows through it?
   - What are the edge cases?
3. Search for similar patterns elsewhere in the codebase.
4. Check AGENTS.md for related known issues.
5. Check git log for related commits.
```

### Analysis Checklist
- [ ] Read the relevant source file(s)
- [ ] Understand the input/output contract
- [ ] Identify all callers and callees
- [ ] List edge cases (empty input, large input, malformed input, concurrent access)
- [ ] Check if existing tests cover this path
- [ ] Check if this issue is related to any open issue in issues.json

### Output
- Root cause description (2-3 sentences)
- List of affected files
- List of related issues
- Recommended fix approach

---

## Phase 3: Design

**Goal:** Decide how to fix the issue.

### For Bug Fixes
- Minimal change principle: change as little as possible
- Prefer local fixes over refactoring
- Add assertions or early returns for defensive programming

### For Security Fixes
- Least privilege: add validation closest to the input boundary
- Use established libraries (never roll your own crypto/validation)
- Document the security boundary

### For Refactoring
- Extract, don't rewrite (small refactors over big bang)
- Keep the public API unchanged unless explicitly required
- Add tests before refactoring, then verify they still pass

### Design Document (for complex issues)
```
## Approach
[1-2 paragraph description of the fix]

## Changes Required
| File | Change |
|------|--------|
| foo.py:42 | Add validation for X |
| bar.py:88 | Handle None case |

## Risk Assessment
- What could break?
- How to verify?

## Test Plan
- New unit tests:
- Modified existing tests:
- Manual verification steps:
```

---

## Phase 4: Implementation

**Goal:** Write clean, tested code.

### Code Standards
- Follow existing code style (PEP 8 for Python, standard JS for frontend)
- No emoji or comments unless the code's purpose is non-obvious
- Use `K.KEY` enum for all translatable strings
- Add type hints for all new functions (Python)
- Use `_log()` for logging, not `print()` (except server startup)

### Implementation Order
1. Write the fix in the smallest possible scope
2. Add defensive checks at the input boundary
3. If changing a function signature, update all callers
4. Add/update tests BEFORE or AFTER the fix (test-first preferred for bugs)

### Verification
- Run `pytest` — all existing tests must pass
- Run `python api_server.py` — server must start without errors
- Manually test the affected endpoint(s)

---

## Phase 5: Testing

**Goal:** Ensure the fix works and nothing is broken.

### Test Types to Consider

| Type | Tool | When |
|------|------|------|
| Unit test | pytest | For isolated logic (tools, parsing, formatting) |
| API test | pytest + test client | For endpoint behavior |
| Integration test | pytest + mocked LLM | For multi-step workflows |
| Regression test | pytest | After fixing a bug, add a test that would have caught it |

### Testing Checklist
- [ ] Unit test covers the fix's logic
- [ ] Edge cases tested: empty input, None, invalid types, boundary values
- [ ] Existing tests still pass: `pytest`
- [ ] If SSE endpoint changed: manuall verify streaming output

### Example: Testing a bug fix
```python
# Before the fix, this would raise TypeError
def test_read_chunk_with_none_active_tools():
    agent = Agent()
    agent.tool_registry.set_active_tools(None)
    agent.file_chunks = {"file_test.py": ["content"]}
    # Should not crash
    result = agent.solve_task_stream(...)
```

---

## Phase 6: Review

**Goal:** Catch issues before they reach production.

### Self-Review Checklist
- [ ] Code compiles/starts without error
- [ ] All existing tests pass (`pytest`)
- [ ] New tests added for the fix
- [ ] No debug code, print statements, or TODO comments left
- [ ] Error messages use the i18n system (`K.KEY` + `t()`)
- [ ] No hardcoded secrets, paths, or URLs
- [ ] Type hints added for new functions
- [ ] Lint clean (`ruff` or `flake8` if available)
- [ ] Changes are minimal — no unrelated refactoring

### Update Issue Status
- [ ] Mark issue as resolved in `docs/issues/observed/issues.json`
- [ ] Add resolution notes (what was done, why)
- [ ] If the fix uncovered follow-up issues, create new issues

---

## Quick Reference

### Commands
```bash
# Run tests
pytest

# Run specific test
pytest tests/test_tools.py::TestToolExecution::test_execute_valid_tool -v

# Start server
python api_server.py

# Check for Python syntax errors
python -m py_compile <file>.py

# Check imports (from project root)
python -c "import api_server; print('OK')"
```

### Common Fix Patterns

| Issue Type | Pattern |
|-----------|---------|
| NoneType error | Add `if x is not None` guard |
| Missing validation | Add at the API boundary (before database/LLM call) |
| Race condition | Use threading.Lock (see SessionManager._lock) |
| Forgotten error handling | Wrap external calls in try/except |
| Hardcoded value | Move to config.py or env var |
| Duplicate code | Extract to shared function |
| Dead code | Remove and verify no tests fail |

---

## Example: Full Issue Run-Through

**Issue:** BUG-003 — read_chunk removal crashes when active_tools is None

**Phase 2 Analysis:**
- `agent_core.py:984`: `if single_file and not is_chunked and self.tool_registry.active_tools and 'read_chunk' in self.tool_registry.active_tools:`
- Bug: `'read_chunk' in None` raises TypeError
- Root cause: Python's `and` short-circuits, but the `in` operator on the right side still evaluates when `active_tools` is truthy-like (empty list `[]` passes the truthy check)

**Phase 3 Design:**
- Change to `if single_file and not is_chunked and self.tool_registry.active_tools is not None and 'read_chunk' in self.tool_registry.active_tools:`
- Minimal change, one line

**Phase 4 Implementation:**
- Applied the one-line fix
- Verified with pytest

**Phase 5 Testing:**
- Added `test_read_chunk_with_none_active_tools`
- Ran `pytest` — all pass

**Phase 6 Review:**
- Code is minimal and correct
- Issue status → resolved in issues.json
