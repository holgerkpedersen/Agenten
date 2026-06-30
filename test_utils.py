from typing import Any, Generator
import agent_files
import subprocess

def _parse_test_summary(result: dict) -> str:
    """Parse test output for summary line."""
    ud = result.get("stdout", "") or ""
    if not ud:
        return ""
    last_short = ""
    for line in ud.splitlines():
        if "==" in line and ("passed" in line or "failed" in line or "error" in line):
            if "short test summary" not in line.lower():
                last_short = line.strip().lstrip("=").rstrip("=").strip()
    if last_short:
        return last_short
    return ""



def _run_full_test_suite(agent: Any) -> bool:
    """Run the full pytest suite to verify no regression."""
    try:
        import subprocess
        result = subprocess.run(
            ["python", "-m", "pytest", "tests/", "-q"],
            capture_output=True, text=True, timeout=120, cwd=agent_files._resolve_workdir()
        )
        if result.returncode == 0:
            return True
        agent._log("WARNING", f"Auto-research: tests fejlede ({result.returncode} failures)",
                   result.stdout[-500:] + result.stderr[-500:])
        return False
    except subprocess.TimeoutExpired:
        agent._log("WARNING", "Auto-research: tests timed out after 120s", "")
        return False
    except FileNotFoundError:
        return False
    except Exception as exc:
        agent._log("WARNING", f"Auto-research: test exception", str(exc)[:200])
        return False
