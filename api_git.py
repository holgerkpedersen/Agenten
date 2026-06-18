import subprocess as _subprocess
import uuid as _uuid
import os as _os
from typing import Any
from flask import jsonify

# Projektrod — defineret lokalt for at undgå lazy import fra api_server
# som kan udløse AssertionError (CORS efter første request).
_BASE_DIR = _os.path.dirname(_os.path.abspath(__file__))


def create_execution_backup() -> dict:
    """Stash all uncommitted changes before execution,
    including gitignored session files via --include-untracked."""
    _wd = _os.environ.get("AGENT_WORKDIR", "")
    _git_dir = _wd if _wd and _os.path.isdir(_os.path.join(_wd, ".git")) else _BASE_DIR
    ts = _uuid.uuid4().hex[:8]
    tag = f"agent-backup-{ts}"
    # Force-add gitignored session files so stash captures them
    _subprocess.run(["git", "add", "sessions/"], capture_output=True, text=True, cwd=_git_dir)
    r = _subprocess.run(
        ["git", "stash", "push", "-u", "-m", tag],
        capture_output=True, text=True, cwd=_git_dir
    )
    return {"success": r.returncode == 0, "message": r.stdout or r.stderr, "tag": tag}


def restore_execution_backup() -> dict:
    """Hard-reset to HEAD, then drop the backup stash (state is restored by reset)."""
    _candidates = []
    _wd = _os.environ.get("AGENT_WORKDIR", "")
    if _wd and _os.path.isdir(_os.path.join(_wd, ".git")):
        _candidates.append(_wd)
    _candidates.append(_BASE_DIR)

    for _git_dir in _candidates:
        # Hard reset — alle tracked filer tilbage til HEAD
        r1 = _subprocess.run(
            ["git", "reset", "--hard", "HEAD"],
            capture_output=True, text=True, cwd=_git_dir
        )
        # Remove untracked files (inkl. nye filer oprettet under execution)
        r2 = _subprocess.run(
            ["git", "clean", "-fd"],
            capture_output=True, text=True, cwd=_git_dir
        )
        # Slet det tilsvarende stash (behøves ikke efter hard reset)
        r3 = _subprocess.run(
            ["git", "stash", "list"],
            capture_output=True, text=True, cwd=_git_dir
        )
        for line in r3.stdout.strip().split("\n"):
            if "agent-backup-" in line:
                stash_ref = line.split(":")[0]
                _subprocess.run(
                    ["git", "stash", "drop", stash_ref],
                    capture_output=True, text=True, cwd=_git_dir
                )
        reset_ok = r1.returncode == 0
        msg = r1.stderr.strip() or "Git reset — working tree restored to HEAD"
        # Also try to restore session files (gitignored, so may not be in index)
        _subprocess.run(
            ["git", "checkout", "--", "sessions/"],
            capture_output=True, text=True, cwd=_git_dir
        )
        # Also try to restore AGENT_WORKDIR session files
        if _wd and _wd != _git_dir:
            _subprocess.run(
                ["git", "checkout", "--", "sessions/"],
                capture_output=True, text=True, cwd=_wd
            )
        return {"success": reset_ok, "message": msg}
    return {"success": False, "message": "Ingen agent-backup fundet"}


def git_backup() -> Any:
    """Create execution backup (git stash)."""
    try:
        result = create_execution_backup()
        return jsonify(result)
    except Exception as e:
        import traceback
        import logging
        log = logging.getLogger(__name__)
        log.error("Git backup failed: %s", traceback.format_exc())
        return jsonify({"success": False, "error": str(e)}), 500


def git_reset() -> Any:
    """Restore execution backup (git stash pop)."""
    result = restore_execution_backup()
    return jsonify(result)
