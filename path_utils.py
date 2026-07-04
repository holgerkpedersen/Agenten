import os
import re
# Lazy imports to avoid circular dependency with ast_index
# These are imported inside functions that need them

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))

_SAFE_DIRS = {os.path.realpath(p) for p in [_BASE_DIR] if p}



def _is_safe_path(base_dir: str, target_path: str) -> bool:
    """Ensures that target_path resolves within base_dir to prevent path traversal."""
    try:
        real_base = os.path.realpath(base_dir)
        real_target = os.path.realpath(target_path) if os.path.exists(target_path) else os.path.abspath(target_path)
        return real_target.startswith(real_base + os.sep) or real_target == real_base
    except Exception:
        return False



def _resolve_workdir() -> str:
    """Return the effective workdir: AGENT_WORKDIR env, or auto-detect from cwd."""
    env_workdir = os.environ.get('AGENT_WORKDIR', '')
    if env_workdir:
        return os.path.abspath(env_workdir)
    return os.path.abspath('.')



def auto_detect_workdir(file_chunks: dict | None = None, prompt: str = "") -> str | None:
    """Detect workdir from prompt tracebacks or file_chunks content.

    Scans prompt for file paths like ``C:\\Dev\\StarBrowser\\starbrowser\\main.py``
    and file_chunks content for absolute paths.  If found, sets
    ``AGENT_WORKDIR`` env var, re-indexes, and returns the directory.
    """
    cwd = os.path.abspath('.')
    cwd_norm = os.path.normcase(cwd)
    agenten_dir = os.path.normcase(os.path.dirname(os.path.abspath(__file__)))
    detected: set[str] = set()

    def _check_path(path: str) -> None:
        try:
            path = path.strip().strip('"\'')
            abspath = os.path.abspath(path)
            if os.path.exists(abspath):
                parent = os.path.dirname(abspath)
            elif os.path.exists(os.path.dirname(path)):
                parent = os.path.dirname(path)
            else:
                return
            parent_norm = os.path.normcase(parent)
            if parent_norm != cwd_norm and parent_norm != agenten_dir:
                detected.add(parent)
        except Exception:
            pass

    if prompt:
        for match in re.finditer(r'(C:[/\\][^\s\r\n]{10,}\.py)', prompt):
            _check_path(match.group(1))

    if file_chunks:
        for chunks in file_chunks.values():
            if not isinstance(chunks, list):
                continue
            for chunk in (chunks or []):
                if not isinstance(chunk, str):
                    continue
                for match in re.finditer(r'(C:[/\\][^\s\r\n]{10,}\.py)', chunk):
                    _check_path(match.group(1))

    if not detected:
        return None

    # Only set AGENT_WORKDIR if it was explicitly provided at startup
    # (--workdir flag). Otherwise, keep workdir as CWD (Agenten).
    if not os.environ.get('AGENT_WORKDIR'):
        return None

    workdir = min(detected, key=len)
    os.environ['AGENT_WORKDIR'] = workdir
    from ast_index import _ensure_workdir_indexed
    _ensure_workdir_indexed()
    return workdir



def _resolve_path(path: str) -> str:
    """Resolve a path relative to the workdir when AGENT_WORKDIR is set, otherwise relative to cwd.

    When AGENT_WORKDIR is set, ALWAYS prefer the workdir — even if the file
    doesn't exist there yet (it will be created). Never fall through to CWD
    for non-existent paths, as that causes Agenten framework files (e.g.
    ``config.py``) to be returned when the user intends to create a new
    module in their project.
    """
    if os.path.isabs(path):
        return os.path.abspath(path)
    workdir = _resolve_workdir()
    cwd = os.path.abspath('.')
    if os.path.normcase(workdir) != os.path.normcase(cwd):
        return os.path.abspath(os.path.join(workdir, path))
    return os.path.abspath(path)



def is_safe_location(target_path: str) -> bool:
    """Checks if target_path is within any known-safe directory (project root, cwd, --workdir, exports, uploads, temp)."""
    try:
        real = os.path.realpath(target_path) if os.path.exists(target_path) else os.path.abspath(target_path)
        real = os.path.normcase(real)
        # Check AGENT_WORKDIR if set (used by --workdir CLI arg)
        workdir = os.environ.get('AGENT_WORKDIR', '')
        if workdir:
            workdir_real = os.path.realpath(workdir)
            workdir_norm = os.path.normcase(workdir_real)
            if real.startswith(workdir_norm + os.sep) or real == workdir_norm:
                return True
        # Also check current working directory as fallback
        cwd = os.path.normcase(os.path.realpath(os.getcwd()))
        if real.startswith(cwd + os.sep) or real == cwd:
            return True
        for safe in _SAFE_DIRS:
            safe_norm = os.path.normcase(safe)
            if real.startswith(safe_norm + os.sep) or real == safe_norm:
                return True
        return False
    except Exception:
        return False
