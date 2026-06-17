import subprocess as _subprocess
import uuid as _uuid
from typing import Any
from flask import jsonify


def create_execution_backup() -> dict:
    """Stash all uncommitted changes before execution."""
    from api_server import BASE_DIR
    ts = _uuid.uuid4().hex[:8]
    tag = f"agent-backup-{ts}"
    # -u includes untracked files, -m adds a message
    r = _subprocess.run(
        ["git", "stash", "push", "-u", "-m", tag],
        capture_output=True, text=True, cwd=BASE_DIR
    )
    return {"success": r.returncode == 0, "message": r.stdout or r.stderr, "tag": tag}


def restore_execution_backup() -> dict:
    """Pop the most recent agent-backup stash, restoring pre-execution state."""
    from api_server import BASE_DIR
    r = _subprocess.run(
        ["git", "stash", "list"],
        capture_output=True, text=True, cwd=BASE_DIR
    )
    for line in r.stdout.strip().split("\n"):
        if "agent-backup-" in line:
            stash_ref = line.split(":")[0]
            # Restore tracked files
            _subprocess.run(["git", "checkout", "--", "."], capture_output=True, text=True, cwd=BASE_DIR)
            # Delete untracked files created during the session
            _subprocess.run(["git", "clean", "-fd"], capture_output=True, text=True, cwd=BASE_DIR)
            # Restore the stash
            pop = _subprocess.run(
                ["git", "stash", "pop", stash_ref],
                capture_output=True, text=True, cwd=BASE_DIR
            )
            return {"success": pop.returncode == 0, "message": pop.stdout or pop.stderr}
    return {"success": False, "message": "Ingen agent-backup fundet"}


def git_backup() -> Any:
    """Create execution backup (git stash)."""
    result = create_execution_backup()
    return jsonify(result)


def git_reset() -> Any:
    """Restore execution backup (git stash pop)."""
    result = restore_execution_backup()
    return jsonify(result)
