import os
import subprocess
import shlex

def _run_git(args, cwd=None):
    if cwd is None:
        cwd = os.getcwd()

    safe_path = os.path.realpath(cwd)
    cmd = ["git"] + args

    result = subprocess.run(cmd, cwd=safe_path, capture_output=True, text=True, timeout=30)
    if result.returncode == 0:
        return {"success": True, "output": result.stdout.strip(), "error": result.stderr.strip()}
    return {"success": False, "output": result.stdout.strip(), "error": result.stderr.strip() or f"exit code {result.returncode}"}


def git_status(path="."):
    return _run_git(["status", "--short"], cwd=path)


def git_remote_exists(path="."):
    r = _run_git(["remote", "get-url", "origin"], cwd=path)
    if r["success"]:
        return {"success": True, "url": r["output"]}
    return {"success": False, "error": "Intet remote 'origin' konfigureret"}


def git_add_all(path="."):
    return _run_git(["add", "-A"], cwd=path)


def git_commit(message, path="."):
    safe_msg = shlex.quote(message)
    return _run_git(["commit", "-m", message], cwd=path)


def git_push(branch="master", path="."):
    remote = git_remote_exists(path)
    if not remote["success"]:
        return remote
    return _run_git(["push", "-u", "origin", branch], cwd=path)


def git_set_remote(url, path="."):
    existing = git_remote_exists(path)
    if existing["success"]:
        _run_git(["remote", "remove", "origin"], cwd=path)
    return _run_git(["remote", "add", "origin", url], cwd=path)


def git_diff(older="HEAD~1", newer="HEAD", max_chars=8000):
    r = _run_git(["diff", "--unified=3", older, newer])
    if r["success"] and len(r["output"]) > max_chars:
        files = _run_git(["diff", "--name-only", older, newer])
        header = "AEndrede filer:\n" + files.get("output", "") if files["success"] else ""
        r["output"] = header + "\n" + r["output"][:max_chars] + "\n... (trunkeret)"
    return r


def git_log(count=10):
    return _run_git(["log", f"-{count}", "--oneline", "--decorate"])
