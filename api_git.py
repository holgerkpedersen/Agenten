import subprocess as _subprocess
import uuid as _uuid
import os as _os
from typing import Any
from flask import jsonify

_BASE_DIR = _os.path.dirname(_os.path.abspath(__file__))


def create_execution_backup() -> dict:
    """Stash all uncommitted changes before execution."""
    ts = _uuid.uuid4().hex[:8]
    tag = f"agent-backup-{ts}"
    r = _subprocess.run(
        ["git", "stash", "push", "-u", "-m", tag],
        capture_output=True, text=True, cwd=_BASE_DIR
    )
    return {"success": r.returncode == 0, "message": r.stdout or r.stderr, "tag": tag}


def restore_execution_backup() -> dict:
    """Pop the most recent agent-backup stash, restoring pre-execution state."""
    r = _subprocess.run(
        ["git", "stash", "list"],
        capture_output=True, text=True, cwd=_BASE_DIR
    )
    for line in r.stdout.strip().split("\n"):
        if "agent-backup-" in line:
            stash_ref = line.split(":")[0]
            _subprocess.run(["git", "checkout", "--", "."], capture_output=True, text=True, cwd=_BASE_DIR)
            _subprocess.run(["git", "clean", "-fd"], capture_output=True, text=True, cwd=_BASE_DIR)
            pop = _subprocess.run(
                ["git", "stash", "pop", stash_ref],
                capture_output=True, text=True, cwd=_BASE_DIR
            )
            return {"success": pop.returncode == 0, "message": pop.stdout or pop.stderr}
    return {"success": False, "message": "Ingen agent-backup fundet"}


def git_backup() -> Any:
    """Create execution backup (git stash)."""
    try:
        result = create_execution_backup()
        return jsonify(result)
    except Exception as e:
        import logging
        logging.getLogger(__name__).error("Git backup failed: %s", e)
        return jsonify({"success": False, "error": str(e)}), 500


def git_reset() -> Any:
    """Restore execution backup (git stash pop)."""
    result = restore_execution_backup()
    return jsonify(result)
