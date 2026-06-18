import subprocess as _subprocess
import uuid as _uuid
import os as _os
from typing import Any
from flask import jsonify

# Projektrod — defineret lokalt for at undgå lazy import fra api_server
# som kan udløse AssertionError (CORS efter første request).
_BASE_DIR = _os.path.dirname(_os.path.abspath(__file__))


def create_execution_backup() -> dict:
    """Stash all uncommitted changes before execution."""
    _wd = _os.environ.get("AGENT_WORKDIR", "")
    _git_dir = _wd if _wd and _os.path.isdir(_os.path.join(_wd, ".git")) else _BASE_DIR
    ts = _uuid.uuid4().hex[:8]
    tag = f"agent-backup-{ts}"
    r = _subprocess.run(
        ["git", "stash", "push", "-u", "-m", tag],
        capture_output=True, text=True, cwd=_git_dir
    )
    return {"success": r.returncode == 0, "message": r.stdout or r.stderr, "tag": tag}


def restore_execution_backup() -> dict:
    """Pop the most recent agent-backup stash, restoring pre-execution state."""
    # Try workdir git first, then fall back to Agenten's git (for stashes created before _git_dir fix)
    _candidates = []
    _wd = _os.environ.get("AGENT_WORKDIR", "")
    if _wd and _os.path.isdir(_os.path.join(_wd, ".git")):
        _candidates.append(_wd)
    _candidates.append(_BASE_DIR)

    for _git_dir in _candidates:
        r = _subprocess.run(
            ["git", "stash", "list"],
            capture_output=True, text=True, cwd=_git_dir
        )
        for line in r.stdout.strip().split("\n"):
            if "agent-backup-" in line:
                stash_ref = line.split(":")[0]
                _subprocess.run(["git", "checkout", "--", "."], capture_output=True, text=True, cwd=_git_dir)
                _subprocess.run(["git", "clean", "-fd"], capture_output=True, text=True, cwd=_git_dir)
                pop = _subprocess.run(
                    ["git", "stash", "pop", stash_ref],
                    capture_output=True, text=True, cwd=_git_dir
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
        logging.getLogger(__name__).error("Git backup failed", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


def git_reset() -> Any:
    """Restore execution backup (git stash pop)."""
    result = restore_execution_backup()
    return jsonify(result)
