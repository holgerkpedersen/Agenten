from typing import Any, Generator
import os
from agent_config import log, EXECUTION_TIMEOUT, _WRITE_TOOLS, PHASE_ALIASES, REQUIRED_ACTION_TOOLS, CLOSE_PHASE_ALIASES, ISSUE_ID_PATTERN, AUTO_RESOLVE_PATTERNS, FRAMEWORK_PY, _TODO_TOOL_MAP
import subprocess
import agent_issues
from test_utils import _parse_test_summary

def _track_produced_file(agent: Any, tool_result: dict) -> None:
    """Extract file path from a successful write/edit tool result and track it."""
    tool = tool_result.get("tool", "")
    if tool not in _WRITE_TOOLS:
        return
    result = tool_result.get("result", {})
    if not isinstance(result, dict) or not result.get("success"):
        return
    path = result.get("result") or tool_result.get("args", {}).get("path", "")
    if not path and isinstance(result, dict):
        path = result.get("path", "")
    if path:
        if not os.path.isabs(path):
            workdir = os.environ.get("AGENT_WORKDIR", "")
            base = os.path.abspath(workdir) if workdir else os.path.abspath(".")
            path = os.path.normpath(os.path.join(base, path))
        agent._produced_files.add(os.path.abspath(path))



def _get_modified_core_files(agent: Any) -> set[str]:
    """Return set of core framework file basenames modified during this task."""
    modified: set[str] = set()
    for entry in getattr(agent, '_tool_log', []):
        tool = entry.get("tool", "")
        if tool not in ("write_file", "edit_file", "delete_file", "extract_symbol", "remove_symbol"):
            continue
        if not entry.get("success", False):
            continue
        args = entry.get("args", {})
        if not args:
            continue
        filepath = args.get("filepath") or args.get("path") or args.get("source") or ""
        basename = os.path.basename(filepath)
        if basename in FRAMEWORK_PY:
            modified.add(basename)
    return modified



def _verify_self_modification(agent: Any) -> None:
    """After a task that modified core files, run tests and rollback if they fail.

    Only triggers when at least one core framework file was successfully
    modified during the task. If tests fail, each modified file is reverted
    via ``git checkout`` and the failure is recorded in CoreAnalytics.
    """
    modified = _get_modified_core_files(agent)
    if not modified:
        return

    agent._log("INFO", "Self-modification detected \u2014 running verification",
               ", ".join(sorted(modified)))

    result = agent_issues.run_pytest()
    passed = result.get("success", False) and result.get("exit_code", -1) == 0

    test_summary = _parse_test_summary(result) or ""
    if not passed:
        summary = test_summary or f"exit code {result.get('exit_code', '?')}"

        # Refactor template: skip rollback — extraction is intentional
        if getattr(agent, 'active_template', '') == "refactor":
            agent._log("WARNING",
                       f"Verification FAILED \u2014 REFACTOR template, SKIPPING rollback for {len(modified)} file(s)",
                       summary[:300])
        else:
            agent._log("WARNING", f"Verification FAILED \u2014 rolling back {len(modified)} file(s)",
                       summary[:300])

            for basename in sorted(modified):
                try:
                    subprocess.run(
                        ["git", "checkout", "--", basename],
                        capture_output=True, text=True, timeout=30,
                        cwd=os.path.dirname(os.path.abspath(__file__)),
                    )
                    agent._log("INFO", f"  Rolled back {basename}", "")
                except Exception as exc:
                    agent._log("ERROR", f"  Rollback failed for {basename}", str(exc))

        if hasattr(agent, '_core'):
            for basename in sorted(modified):
                agent._core.record_test_outcome(
                    test_file=f"self_mod:{basename}",
                    passed=passed,
                    summary=summary if not passed else "Verification passed",
                )
            if not passed:
                agent._core.save()

    if not passed:
        if hasattr(agent, '_core'):
            hotspots = agent._core.get_hotspots(min_failures=3)
            for basename in sorted(modified):
                matching = [h for h in hotspots if h["file"] == basename]
                if matching and matching[0]["tool_failures"] >= 3:
                    try:
                        agent_issues.create_issue(
                            agent,
                            title=f"{basename} har fejlet ved selvtests 3+ gange",
                            type="self",
                            severity="high",
                            description=(
                                f"{basename} har fejlet ved automatisk "
                                f"test-verifikation {matching[0]['tool_failures']} gange "
                                f"efter redigering af egen kode.\n\n"
                                f"Sidste test-output: {summary[:300]}"
                            ),
                            location=basename,
                        )
                        agent._log("INFO", f"Auto-created CORE issue for {basename}",
                                   f"{matching[0]['tool_failures']} failures")
                    except Exception as exc:
                        agent._log("ERROR", "Failed to create CORE issue", str(exc))
    else:
        agent._log("INFO", "Verification passed \u2014 all tests OK",
                   test_summary[:200] if test_summary else "")
