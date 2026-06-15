"""Git operations wrapper for Agent."""

import ast
import os
import re
import subprocess
import textwrap

import threading
from typing import Any
from i18n import K
from lang import t
from agent_files import is_safe_location, locate_code


def _resolve_path(path: str) -> str:
    """Resolve a path relative to AGENT_WORKDIR when set, otherwise relative to CWD."""
    if os.path.isabs(path):
        return os.path.abspath(path)
    workdir = os.environ.get('AGENT_WORKDIR', '')
    if workdir:
        return os.path.abspath(os.path.join(workdir, path))
    return os.path.abspath(path)


_file_lock = threading.RLock()
_BASE_DIR = os.path.realpath(os.path.dirname(os.path.abspath(__file__)))

_STDLIB_MODULES = {
    'abc', 'aifc', 'argparse', 'array', 'ast', 'asynchat', 'asyncio', 'asyncore',
    'atexit', 'audioop', 'base64', 'bdb', 'binascii', 'binhex', 'bisect', 'builtins',
    'bz2', 'calendar', 'cgi', 'cgitb', 'chunk', 'cmath', 'cmd', 'code', 'codecs',
    'codeop', 'collections', 'colorsys', 'compileall', 'concurrent', 'configparser',
    'contextlib', 'contextvars', 'copy', 'copyreg', 'cProfile', 'crypt', 'csv',
    'ctypes', 'curses', 'dataclasses', 'datetime', 'dbm', 'decimal', 'difflib',
    'dis', 'distutils', 'doctest', 'email', 'encodings', 'enum', 'errno', 'faulthandler',
    'fcntl', 'filecmp', 'fileinput', 'fnmatch', 'fractions', 'ftplib', 'functools',
    'gc', 'getopt', 'getpass', 'gettext', 'glob', 'graphlib', 'grp', 'gzip',
    'hashlib', 'heapq', 'hmac', 'html', 'http', 'idlelib', 'imaplib', 'imghdr',
    'imp', 'importlib', 'inspect', 'io', 'ipaddress', 'itertools', 'json', 'keyword',
    'lib2to3', 'linecache', 'locale', 'logging', 'lzma', 'mailbox', 'mailcap',
    'marshal', 'math', 'mimetypes', 'mmap', 'modulefinder', 'multiprocessing',
    'netrc', 'nis', 'nntplib', 'numbers', 'operator', 'optparse', 'os', 'ossaudiodev',
    'pathlib', 'pdb', 'pickle', 'pickletools', 'pipes', 'pkgutil', 'platform',
    'plistlib', 'poplib', 'posix', 'posixpath', 'pprint', 'profile', 'pstats',
    'pty', 'pwd', 'py_compile', 'pyclbr', 'pydoc', 'queue', 'quopri', 'random',
    're', 'readline', 'reprlib', 'resource', 'rlcompleter', 'runpy', 'sched',
    'secrets', 'select', 'selectors', 'shelve', 'shlex', 'shutil', 'signal',
    'site', 'smtpd', 'smtplib', 'sndhdr', 'socket', 'socketserver', 'sqlite3',
    'ssl', 'stat', 'statistics', 'string', 'stringprep', 'struct', 'subprocess',
    'sunau', 'symtable', 'sys', 'sysconfig', 'syslog', 'tabnanny', 'tarfile',
    'telnetlib', 'tempfile', 'termios', 'test', 'textwrap', 'threading', 'time',
    'timeit', 'tkinter', 'token', 'tokenize', 'tomllib', 'trace', 'traceback',
    'tracemalloc', 'tty', 'turtle', 'turtledemo', 'types', 'typing', 'unicodedata',
    'unittest', 'urllib', 'uu', 'uuid', 'venv', 'warnings', 'wave', 'weakref',
    'webbrowser', 'winreg', 'winsound', 'wsgiref', 'xdrlib', 'xml', 'xmlrpc',
    'zipapp', 'zipfile', 'zipimport', 'zlib',
}


def _check_missing_deps(py_content: str, req_path: str) -> list[str]:
    """check missing deps.
    
    Args:
        py_content:
        req_path:"""
    try:
        tree = ast.parse(py_content)
    except SyntaxError:
        return []
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split('.')[0]
                if top not in _STDLIB_MODULES:
                    imports.add(top)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                top = node.module.split('.')[0]
                if top not in _STDLIB_MODULES:
                    imports.add(top)
    if not imports or not os.path.exists(req_path):
        return []
    with open(req_path, 'r', encoding='utf-8') as f:
        req_pkgs = set()
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if line.startswith(('-', '#', '--')):
                continue
            if '#' in line:
                line = line[:line.index('#')].strip()
            pkg = re.split(r'[=<>~!]', line, maxsplit=1)[0].strip().lower()
            if pkg:
                req_pkgs.add(pkg)
    return sorted(name for name in imports if name.lower() not in req_pkgs)


def _extract_urls(content: str, source_path: str) -> set[str]:
    """extract urls.
    
    Args:
        content:
        source_path:"""
    urls = set()
    if source_path.endswith('.html'):
        for m in re.finditer(r"fetch\s*\(\s*['\"]([^'\"]+)['\"]", content):
            urls.add(m.group(1))
        for m in re.finditer(r'action\s*=\s*["\']([^"\']+)["\']', content):
            urls.add(m.group(1))
    elif source_path.endswith('.js'):
        for m in re.finditer(r"(?:fetch|axios\.(?:get|post|put|delete))\s*\(\s*['\"]([^'\"]+)['\"]", content):
            urls.add(m.group(1))
    return urls


def _find_partner_files(path: str, other_ext: str) -> str | None:
    """find partner files.
    
    Args:
        path:
        other_ext:"""
    dirname = os.path.dirname(path)
    basename = os.path.splitext(os.path.basename(path))[0]
    candidates = []

    if other_ext == '.py':
        candidates.append(os.path.join(dirname, 'app.py'))
        candidates.append(os.path.join(dirname, 'main.py'))
        candidates.append(os.path.join(dirname, 'server.py'))
        candidates.append(os.path.join(dirname, 'api.py'))
        candidates.append(os.path.join(dirname, 'routes.py'))
        candidates.append(os.path.join(dirname, basename + '.py'))
        pardir = os.path.dirname(dirname)
        if pardir:
            candidates.append(os.path.join(pardir, 'app.py'))
            candidates.append(os.path.join(pardir, 'main.py'))
            candidates.append(os.path.join(pardir, 'server.py'))
    else:
        candidates.append(os.path.join(dirname, basename + other_ext))
        candidates.append(os.path.join(dirname, 'index' + other_ext))
        templates_dir = os.path.join(dirname, 'templates')
        if os.path.isdir(templates_dir):
            candidates.append(os.path.join(templates_dir, 'index.html'))
            candidates.append(os.path.join(templates_dir, basename + other_ext))
        static_dir = os.path.join(dirname, 'static')
        if os.path.isdir(static_dir):
            for fname in os.listdir(static_dir):
                full = os.path.join(static_dir, fname)
                if full.endswith(other_ext):
                    candidates.append(full)

    for c in candidates:
        if os.path.exists(c):
            return c
    return None


def _check_route_mismatch(path: str, other_ext: str) -> list[str]:
    """check route mismatch.
    
    Args:
        path:
        other_ext:"""
    other = _find_partner_files(path, other_ext)
    if not other:
        return []
    with open(path, 'r', encoding='utf-8') as f:
        content = f.read()
    with open(other, 'r', encoding='utf-8') as f:
        other_content = f.read()

    if path.endswith('.py'):
        routes = set(re.findall(r"@\w+\.route\s*\(\s*['\"]([^'\"]+)['\"]", content))
        urls = _extract_urls(other_content, other)
    elif path.endswith('.html'):
        urls = _extract_urls(content, path)
        routes = set(re.findall(r"@\w+\.route\s*\(\s*['\"]([^'\"]+)['\"]", other_content))
    elif path.endswith('.js'):
        urls = _extract_urls(content, path)
        routes = set(re.findall(r"@\w+\.route\s*\(\s*['\"]([^'\"]+)['\"]", other_content))
    else:
        return []

    mismatched = []
    for url in sorted(urls):
        if url.startswith('./') or url.startswith('..'):
            base_dir = os.path.dirname(os.path.abspath(path))
            resolved = os.path.normpath(os.path.join(base_dir, url))
            url = '/' + os.path.relpath(resolved, base_dir).replace('\\', '/')
        if not url.startswith('/'):
            continue
        if url in routes:
            continue
        matched = any(
            re.fullmatch(re.sub(r'<[^>]+>', r'[^/]+', r), url)
            for r in routes
        )
        if not matched:
            mismatched.append(url)
    return mismatched


def _run_git(args: list[str], cwd: str | None = None) -> dict[str, Any]:
    """run git.
    
    Args:
        args:
        cwd:"""
    if cwd is None:
        cwd = os.getcwd()

    safe_path = os.path.realpath(cwd)
    cmd = ["git"] + args

    result = subprocess.run(cmd, cwd=safe_path, capture_output=True, text=True, timeout=30, encoding='utf-8', errors='replace')
    if result.returncode == 0:
        return {"success": True, "output": result.stdout.strip(), "error": result.stderr.strip()}
    return {"success": False, "output": result.stdout.strip(), "error": result.stderr.strip() or f"exit code {result.returncode}"}


def git_status(path: str = ".") -> dict[str, Any]:
    """git status.
    
    Args:
        path:"""
    return _run_git(["status", "--short"], cwd=path)


def git_remote_exists(path: str = ".") -> dict[str, Any]:
    """git remote exists.
    
    Args:
        path:"""
    r = _run_git(["remote", "get-url", "origin"], cwd=path)
    if r["success"]:
        return {"success": True, "url": r["output"]}
    return {"success": False, "error": "Intet remote 'origin' konfigureret"}


def git_add_all(path: str = ".") -> dict[str, Any]:
    """git add all.
    
    Args:
        path:"""
    return _run_git(["add", "-A"], cwd=path)


def git_commit(message: str, path: str = ".") -> dict[str, Any]:
    """git commit.
    
    Args:
        message:
        path:"""
    return _run_git(["commit", "-m", message], cwd=path)


def git_push(branch: str = "main", path: str = ".") -> dict[str, Any]:
    """git push.
    
    Args:
        branch:
        path:"""
    remote = git_remote_exists(path)
    if not remote["success"]:
        return remote
    return _run_git(["push", "-u", "origin", branch], cwd=path)


def git_set_remote(url: str, path: str = ".") -> dict[str, Any]:
    """git set remote.
    
    Args:
        url:
        path:"""
    existing = git_remote_exists(path)
    if existing["success"]:
        _run_git(["remote", "remove", "origin"], cwd=path)
    return _run_git(["remote", "add", "origin", url], cwd=path)


def git_diff(older: str = "HEAD~1", newer: str = "HEAD", max_chars: int = 8000) -> dict[str, Any]:
    """git diff.
    
    Args:
        older:
        newer:
        max_chars:"""
    r = _run_git(["diff", "--unified=3", older, newer])
    if r["success"] and len(r["output"]) > max_chars:
        files = _run_git(["diff", "--name-only", older, newer])
        header = "AEndrede filer:\n" + files.get("output", "") if files["success"] else ""
        r["output"] = header + "\n" + r["output"][:max_chars] + "\n... (trunkeret)"
    return r


def git_log(count: int = 10) -> dict[str, Any]:
    """git log.
    
    Args:
        count:"""
    return _run_git(["log", f"-{count}", "--oneline", "--decorate"])


def git_create_branch(name: str, path: str = ".") -> dict[str, Any]:
    """git create branch.
    
    Args:
        name:
        path:"""
    return _run_git(["checkout", "-b", name], cwd=path)


def git_current_branch(path: str = ".") -> dict[str, Any]:
    """git current branch.
    
    Args:
        path:"""
    return _run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=path)


def git_branch_list(path: str = ".") -> dict[str, Any]:
    """git branch list.
    
    Args:
        path:"""
    return _run_git(["branch"], cwd=path)


def git_pull(remote: str = "origin", branch: str = "main", path: str = ".") -> dict[str, Any]:
    """git pull.
    
    Args:
        remote:
        branch:
        path:"""
    return _run_git(["pull", remote, branch], cwd=path)


def git_checkout(branch: str, path: str = ".") -> dict[str, Any]:
    """git checkout.
    
    Args:
        branch:
        path:"""
    return _run_git(["checkout", branch], cwd=path)


def _check_post_write(path: str, content: str, result: dict[str, Any]) -> dict[str, Any]:
    """check post write.
    
    Args:
        path:
        content:
        result:"""
    dirname = os.path.dirname(path)
    if path.endswith('.py'):
        req_path = os.path.join(dirname or os.getcwd(), 'requirements.txt')
        missing = _check_missing_deps(content, req_path)
        if missing:
            result["missing_deps"] = missing
            if os.path.exists(req_path):
                with _file_lock:
                    with open(req_path, 'a', encoding='utf-8') as f:
                        f.write('\n' + '\n'.join(missing) + '\n')
                result["req_updated"] = missing
        for ext in ('.html', '.js'):
            mismatched = _check_route_mismatch(path, ext)
            if mismatched:
                result.setdefault('route_warnings', {})[ext] = mismatched
    elif path.endswith(('.html', '.js')):
        for ext in ('.py',):
            mismatched = _check_route_mismatch(path, ext)
            if mismatched:
                result.setdefault('route_warnings', {})[ext] = mismatched
    return result
import os
import ast
from typing import Any
def write_file(path: str, content: str, overwrite: bool = False) -> dict[str, Any]:
    """write file.
    
    Args:
        path: File path to write (relative to project root or absolute).
        content: File content as string.
        overwrite: ``False`` = reject if exists. ``True`` = allow but warn if replacing meaningful content. 
                   ``"force"`` or ``"replace"`` = unconditional.
    """
    path = _resolve_path(path)
    if not is_safe_location(path):
        return {"success": False, "error": f"Adgang n\u00e6gtet: stien er uden for projektmappen: {path}"}
    
    dirname = os.path.dirname(path)
    if dirname:
        os.makedirs(dirname, exist_ok=True)
    
    # Check for secret files
    secret_files = ['.env', '.secret', '.credentials', '.key', '.token']
    filename = os.path.basename(path)
    if filename in secret_files or any(filename.startswith(prefix) for prefix in ['._', '.git']):
        return {"success": False, "error": f"Adgang n\u00e6gtet: kan ikke skrive til hemmelige filer: {path}"}
    
    # Check for binary files
    if path.endswith(('.bin', '.exe', '.dll', '.so', '.dylib', '.zip', '.tar', '.gz', '.pdf', '.jpg', '.jpeg', '.png', '.gif', '.bmp')):
        return {"success": False, "error": f"Adgang n\u00e6gtet: kan ikke skrive til bin\u00e6re filer: {path}"}
    
    try:
        # tests/temp/ is a scratch directory — always allow overwrite
        is_scratch = "\\tests\\temp\\" in path or "/tests/temp/" in path
        if os.path.exists(path) and not overwrite and not is_scratch:
            return {
                "success": False,
                "error": f"Filen findes allerede: {path}. Brug edit_file til at redigere eksisterende filer, eller brug overwrite=true for at erstatte den."
            }
        
        if os.path.exists(path) and overwrite == True:
            existing_size = os.path.getsize(path)
            new_size = len(content)
            
            if existing_size > 200 and new_size < 50:
                return {
                    "success": False,
                    "error": (
                        f"Filen indeholder eksisterende indhold ({existing_size} bytes). "
                        f"Nyt indhold er kun {new_size} bytes — risiko for at slette meningsfuldt indhold. "
                        f"Brug edit_file til at redigere, eller overwrite=\"force\" for at tvinge overskrivning."
                    )
                }
            
            if existing_size > 500 and new_size < existing_size // 10:
                return {
                    "success": False,
                    "error": (
                        f"Filen indeholder {existing_size} bytes meningsfuldt indhold. "
                        f"Nyt indhold er kun {new_size} bytes ({existing_size // new_size}x mindre). "
                        f"Brug edit_file for at bevare indhold, eller overwrite=\"force\" for at tvinge."
                    )
                }
        
        # Handle markdown and text files safely
        if path.endswith(('.md', '.markdown', '.txt', '.rst')):
            if not content.strip():
                return {"success": False, "error": f"Kan ikke skrive tomme filer: {path}"}
        
        if path.endswith('.py'):
            try:
                ast.parse(content)
            except SyntaxError as e:
                return {
                    "success": False,
                    "error": f"Syntaksfejl p\u00e5 linje {e.lineno}: {e.msg}",
                    "line": e.lineno,
                    "msg": e.msg
                }
        
        with _file_lock:
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            
            result = {"success": True, "path": os.path.abspath(path), "chars": len(content)}
            _check_post_write(path, content, result)
            return result
            
    except (IOError, OSError) as e:
        return {"success": False, "error": str(e)}


def _build_flexible_pattern(text: str) -> tuple[str, int]:
    """build flexible pattern.
    
    Args:
        text:"""
    lines = text.split('\n')
    parts = []
    for line in lines:
        stripped = line.strip()
        if stripped:
            parts.append(r'[ \t]*' + re.escape(stripped))
        else:
            parts.append(r'[ \t]*')
    return r'\n'.join(parts), len(lines)


def _build_fuzzy_pattern(text: str) -> tuple[str, int]:
    """build fuzzy pattern.
    
    Args:
        text:"""
    lines = text.split('\n')
    parts = []
    for line in lines:
        stripped = line.strip()
        if stripped:
            parts.append(r'[ \t]*' + re.escape(stripped))
    if len(parts) < 2:
        return _build_flexible_pattern(text)
    return r'[ \t]*\n(?:[ \t]*\n)*[ \t]*'.join(parts), len(parts)


_PLACEHOLDER_PATTERNS = [
    re.compile(r'^\s*\.\.\.+\s*$', re.MULTILINE),
    re.compile(r"'?\s*\.\.\.\s*(?:full\s+.*?)?(?:body|code|implementation|function)?\s*\.\.\.\s*'?", re.IGNORECASE),
    re.compile(r'#\s*\.\.\.\s*(?:full\s+.*?)?(?:body|code|implementation|function)?\s*\.\.\.', re.IGNORECASE),
    re.compile(r'/\*\s*\.\.\.\s*(?:full\s+.*?)?(?:body|code|implementation|function)?\s*\.\.\.\s*\*/', re.IGNORECASE),
    re.compile(r'<!--\s*\.\.\.\s*(?:full\s+.*?)?(?:body|code|implementation|function)?\s*\.\.\.\s*-->', re.IGNORECASE),
]


def _has_placeholders(text: str) -> bool:
    """has placeholders.
    
    Args:
        text:"""
    for pat in _PLACEHOLDER_PATTERNS:
        if pat.search(text):
            return True
    return False


def _detect_base_indentation(text: str) -> int:
    """detect base indentation.
    
    Args:
        text:"""
    lines = text.split('\n')
    indent_levels = []
    for line in lines:
        if line.strip() and not line.strip().startswith('#'):
            stripped = line.lstrip()
            if stripped:
                indent = len(line) - len(stripped)
                indent_levels.append(indent)
    if not indent_levels:
        return 0
    return min(indent_levels)


def _normalize_indentation(new_text: str, search_text: str) -> str:
    """normalize indentation.
    
    Args:
        new_text:
        search_text:"""
    target_indent = _detect_base_indentation(search_text)
    source_indent = _detect_base_indentation(new_text)
    if target_indent == source_indent:
        return new_text
    lines = new_text.split('\n')
    result = []
    for line in lines:
        stripped = line.lstrip()
        if stripped:
            leading = len(line) - len(stripped)
            if leading >= source_indent:
                new_leading = leading - source_indent + target_indent
                result.append(' ' * new_leading + stripped)
            else:
                result.append(line)
        else:
            result.append(line)
    return '\n'.join(result)


def edit_file(path: str, old_text: str = "", new_text: str = "", expected_hash: str | None = None, symbol: str | None = None, requirements: str = "", test_path: str = "", llm: Any | None = None) -> dict[str, Any]:
    """edit file.
    
    Args:
        path:
        old_text:
        new_text:
        expected_hash:
        symbol:
        requirements:
        test_path:
        llm:"""
    path = _resolve_path(path)
    if not is_safe_location(path):
        return {"success": False, "error": f"Adgang nægtet: stien er uden for projektmappen: {path}"}
    try:
        if not os.path.exists(path):
            return {"success": False, "error": f"Filen findes ikke: {path}"}
        if expected_hash is not None:
            import agent_files
            current_hash = agent_files.file_hash(path)
            if current_hash != expected_hash:
                return {
                    "success": False,
                    "error": (
                        f"HARD BLOCK: Filen '{os.path.basename(path)}' er blevet "
                        f"ændret siden indlæsning (hash mismatch). Forventet: "
                        f"{expected_hash[:12]}..., aktuelt: {current_hash[:12]}... "
                        f"Genindlæs filen og prøv igen."
                    )
                }

        # Route to edit_file2 pipeline when symbol + requirements (smarter LLM mode for .py files)
        # Only for functions/classes — variables use the standard AST replacement below.
        if requirements and symbol and path.endswith('.py') and llm:
            loc_check = locate_code(filepath=path, name=symbol)
            if loc_check.get("success") and loc_check.get("type") != "variable":
                import edit_file2
                return edit_file2.edit_file2(
                    filepath=path, name=symbol, requirements=requirements,
                    llm=llm, test_path=test_path or None,
                )

        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()

        # Normalize to \n-only for consistent byte positions (BUG-076 fix)
        content = content.replace('\r\n', '\n').replace('\r', '\n')

        _symbol_created = False
        if symbol:
            if not new_text:
                return {"success": False, "error": "new_text is required when using symbol."}
            loc = locate_code(filepath=path, name=symbol)
            if loc.get("success"):
                start = loc["line"]
                end = loc["end_line"]
                lines_list = content.split('\n')
                if start < 1 or end > len(lines_list):
                    return {"success": False, "error": f"Symbol '{symbol}' line range {start}-{end} outside file"}
                search = '\n'.join(lines_list[start - 1:end])
                if content.count(search) != 1:
                    return {"success": False, "error": f"Symbol body for '{symbol}' matched {content.count(search)} times (expected 1)"}
                if _has_placeholders(new_text):
                    return {"success": False, "error": f"new_text for symbol '{symbol}' indeholder pladsholdere (..., 'full new function body', osv.). Erstat med den faktiske kode."}
                idx = content.index(search)
                search_len = len(search)
                exact_old = content[idx:idx + search_len]
                if exact_old.count('\n') != search.count('\n'):
                    lines = search.split('\n')
                    pos = idx
                    parts = []
                    for line in lines:
                        next_newline = content.find('\n', pos)
                        if next_newline == -1:
                            parts.append(content[pos:])
                            break
                        parts.append(content[pos:next_newline + 1])
                        pos = next_newline + 1
                    exact_old = ''.join(parts)
                new_content = content.replace(exact_old, new_text, 1)
            elif "." in symbol:
                class_name = symbol.rsplit(".", 1)[0]
                class_loc = locate_code(filepath=path, name=class_name)
                if not class_loc.get("success") or class_loc.get("type") not in ("class",):
                    return {"success": False, "error": f"Symbol '{symbol}' not found in {path}. Use old_text/new_text instead."}
                lines_list = content.split('\n')
                insert_at = class_loc["end_line"]
                while insert_at > class_loc["line"] and insert_at <= len(lines_list):
                    if lines_list[insert_at - 1].strip() == '':
                        insert_at -= 1
                    else:
                        break
                if insert_at < class_loc["end_line"]:
                    insert_at = class_loc["end_line"]
                trimmed = new_text.rstrip('\n')
                trimmed_lines = trimmed.split('\n')
                indent = "    "
                if trimmed_lines and not trimmed_lines[0].startswith(indent):
                    trimmed_lines = [indent + l if l.strip() else l for l in trimmed_lines]
                new_method_block = '\n' + '\n'.join(trimmed_lines)
                lines_list.insert(insert_at, new_method_block)
                new_content = '\n'.join(lines_list)
            else:
                # Symbol not found → create new symbol at end of file
                text_to_append = textwrap.dedent(new_text) if path.endswith('.py') else new_text
                new_content = content + '\n' + text_to_append + '\n'
                _symbol_created = True
        elif old_text:
            search = old_text.replace('\r\n', '\n').replace('\r', '\n')
            count = content.count(search)
            if count == 0:
                pattern, nlines = _build_flexible_pattern(search)
                m = re.search(pattern, content)
                if not m:
                    pattern, nlines = _build_fuzzy_pattern(search)
                    m = re.search(pattern, content)
                if not m:
                    return {"success": False, "error": f"Teksten blev ikke fundet i {path}"}
                matches = list(re.finditer(pattern, content))
                if len(matches) > 1:
                    return {"success": False, "error": f"Teksten fundet {len(matches)} gange — brug en mere specifik søgestreng"}
                idx = m.start()
                search_len = len(m.group())
            else:
                if count > 1:
                    return {"success": False, "error": f"Teksten fundet {count} gange — brug en mere specifik søgestreng"}
                idx = content.index(search)
                search_len = len(search)
            exact_old = content[idx:idx + search_len]
            if exact_old.count('\n') != search.count('\n'):
                lines = search.split('\n')
                pos = idx
                parts = []
                for line in lines:
                    next_newline = content.find('\n', pos)
                    if next_newline == -1:
                        parts.append(content[pos:])
                        break
                    parts.append(content[pos:next_newline + 1])
                    pos = next_newline + 1
                exact_old = ''.join(parts)
            new_content = content.replace(exact_old, new_text, 1)

            if path.endswith('.py'):
                normalized_new = _normalize_indentation(new_text, exact_old)
                if normalized_new != new_text:
                    new_content = content.replace(exact_old, normalized_new, 1)
                    new_text = normalized_new
                try:
                    ast.parse(new_content)
                except SyntaxError as e:
                    return {
                        "success": False,
                        "error": f"Syntaksfejl på linje {e.lineno}: {e.msg}",
                        "line": e.lineno,
                        "msg": e.msg
                    }
        else:
            return {"success": False, "error": "Provide either symbol (AST-based) or old_text+new_text (search-and-replace)."}

        # Syntax check for symbol-based edits (both replace and append at class end)
        if symbol and path.endswith('.py') and new_content and new_content != content:
            try:
                ast.parse(new_content)
            except SyntaxError as e:
                return {"success": False, "error": f"Syntaksfejl på linje {e.lineno}: {e.msg}", "line": e.lineno, "msg": e.msg}

        with _file_lock:
            with open(path, 'w', encoding='utf-8') as f:
                f.write(new_content)

        result = {
            "success": True,
            "path": os.path.abspath(path),
            "chars_before": len(content),
            "chars_after": len(new_content),
            "lines_changed": content.count('\n') - new_content.count('\n'),
        }
        if _symbol_created:
            result["action"] = "created"

        _check_post_write(path, new_content, result)

        return result
    except (IOError, OSError) as e:
        return {"success": False, "error": str(e)}


EXCLUDE_LIST_FILES = {'.env', '.env.example', 'credentials.json', 'secrets.json', '.gitconfig', 'id_rsa', 'id_rsa.pub', 'known_hosts', 'config.json'}

def list_files(path: str = ".", pattern: str | None = None, max_depth: int = 2) -> dict[str, Any]:
    """list files.
    
    Args:
        path:
        pattern:
        max_depth:"""
    try:
        path = _resolve_path(path)
        if not is_safe_location(path):
            return {"success": False, "error": f"Adgang nægtet: stien er uden for projektmappen: {path}"}
        if not os.path.isdir(path):
            return {"success": False, "error": f"Mappen findes ikke: {path}"}
        result = []
        for root, dirs, files in os.walk(path):
            rel = os.path.relpath(root, path)
            depth = 0 if rel == '.' else rel.count(os.sep) + 1
            if depth > max_depth:
                dirs.clear()
                continue
            for f in sorted(files):
                if f in EXCLUDE_LIST_FILES:
                    continue
                if pattern and not f.endswith(pattern):
                    continue
                fp = os.path.join(root, f)
                relpath = os.path.relpath(fp, path)
                try:
                    size = os.path.getsize(fp)
                except OSError:
                    size = 0
                result.append({"file": relpath, "size": size})
        return {"success": True, "files": result, "count": len(result), "path": os.path.abspath(path)}
    except Exception as e:
        return {"success": False, "error": str(e)}


def add_method(filepath: str, class_name: str, method_code: str) -> dict[str, Any]:
    """Insert a method into an existing class using AST.

    Finds the class by name, inserts method_code at the end of the class body.
    method_code should contain the 'def' line and body (indentation optional —
    the function normalizes to 4-space indent). Returns success/error.
    """
    path = _resolve_path(filepath)
    if not is_safe_location(path):
        return {"success": False, "error": "Adgang nægtet: stien er uden for projektmappen"}
    if not os.path.exists(path):
        return {"success": False, "error": f"Filen findes ikke: {path}"}
    if not path.lower().endswith('.py'):
        return {"success": False, "error": "Kun Python-filer (.py) understøttes"}

    try:
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()
    except Exception as e:
        return {"success": False, "error": f"Kunne ikke læse filen: {e}"}

    try:
        tree = ast.parse(content)
    except SyntaxError as e:
        return {"success": False, "error": f"Syntaxfejl i filen: {e}"}

    class_node = None
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            class_node = node
            break

    if not class_node:
        return {"success": False, "error": f"Klasse '{class_name}' ikke fundet i {os.path.basename(path)}"}

    end_line = class_node.end_lineno
    if end_line is None:
        return {"success": False, "error": "Kunne ikke bestemme klassens slutning (kræver Python 3.8+)"}

    lines = content.split('\n')
    method_lines = method_code.split('\n')

    # Strip common leading whitespace from method_code
    method_lines_stripped = textwrap.dedent('\n'.join(method_lines)).split('\n')
    # Re-indent to 4 spaces
    indented = ['    ' + l if l.strip() else l for l in method_lines_stripped]
    # Ensure def line starts with 4 spaces
    if indented and indented[0].strip():
        if not indented[0].startswith('    '):
            indented[0] = '    ' + indented[0].lstrip()

    insert = [''] + indented  # blank line before method
    new_lines = lines[:end_line] + insert + lines[end_line:]
    new_content = '\n'.join(new_lines)

    try:
        ast.parse(new_content)
    except SyntaxError as e:
        return {
            "success": False,
            "error": f"Syntaksfejl på linje {e.lineno}: {e.msg}",
            "line": e.lineno,
            "msg": e.msg,
        }

    try:
        with open(path, 'w', encoding='utf-8') as f:
            f.write(new_content)
    except Exception as e:
        return {"success": False, "error": f"Kunne ikke skrive filen: {e}"}

    return {
        "success": True,
        "method": indented[0] if indented else "",
        "class": class_name,
        "file": os.path.abspath(path),
        "inserted_at": end_line + 1,
    }


def add_function(filepath: str, function_code: str, after_symbol: str = "") -> dict[str, Any]:
    """Insert a module-level function into a Python file using AST.

    Inserts function_code at the end of the file (or after after_symbol if provided).
    function_code should contain the 'def' line and body.
    """
    path = _resolve_path(filepath)
    if not is_safe_location(path):
        return {"success": False, "error": "Adgang nægtet: stien er uden for projektmappen"}
    if not os.path.exists(path):
        return {"success": False, "error": f"Filen findes ikke: {path}"}
    if not path.lower().endswith('.py'):
        return {"success": False, "error": "Kun Python-filer (.py) understøttes"}

    try:
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()
    except Exception as e:
        return {"success": False, "error": f"Kunne ikke læse filen: {e}"}

    try:
        tree = ast.parse(content)
    except SyntaxError as e:
        return {"success": False, "error": f"Syntaxfejl i filen: {e}"}

    lines = content.split('\n')
    func_lines = function_code.split('\n')
    func_lines_stripped = textwrap.dedent('\n'.join(func_lines)).split('\n')

    if after_symbol:
        insert_line = None
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == after_symbol:
                insert_line = node.end_lineno
        if insert_line is None:
            return {"success": False, "error": f"Symbol '{after_symbol}' ikke fundet i {os.path.basename(path)}"}
        insert = [''] + func_lines_stripped
        new_lines = lines[:insert_line] + insert + lines[insert_line:]
    else:
        # Insert before trailing blank lines / if __name__ guard
        cut = len(lines)
        for i in range(len(lines) - 1, -1, -1):
            stripped = lines[i].strip()
            if stripped and not stripped.startswith('if __name__'):
                cut = i + 1
                break
            if stripped.startswith('if __name__'):
                cut = i
        insert = ([''] if cut < len(lines) else ['', '']) + func_lines_stripped
        new_lines = lines[:cut] + insert + lines[cut:]

    new_content = '\n'.join(new_lines)

    try:
        ast.parse(new_content)
    except SyntaxError as e:
        return {
            "success": False,
            "error": f"Syntaksfejl på linje {e.lineno}: {e.msg}",
            "line": e.lineno,
            "msg": e.msg,
        }

    try:
        with open(path, 'w', encoding='utf-8') as f:
            f.write(new_content)
    except Exception as e:
        return {"success": False, "error": f"Kunne ikke skrive filen: {e}"}

    return {
        "success": True,
        "function": func_lines_stripped[0] if func_lines_stripped else "",
        "file": os.path.abspath(path),
        "inserted_after": after_symbol or "(end of file)",
    }


def _find_file_references(filepath: str) -> list[str]:
    """Search project files for references to the given file.

    Scans ALL text-based source files for references:
    - .py files: checks import statements via regex
    - All text files: checks for filename (with extension) as a word-boundary match
    Returns list of (filename, line_number, line_text) tuples.
    """
    basename = os.path.basename(filepath)
    module_name = os.path.splitext(basename)[0]
    refs = []
    seen = set()
    workdir = os.environ.get('AGENT_WORKDIR') or os.getcwd()
    import re

    # For .py files: precise import pattern
    import_pattern = re.compile(
        rf'(?:^|\s)(?:import\s+(?:\w+\s*,\s*)*{re.escape(module_name)}(?:\s*,\s*\w+)*|from\s+{re.escape(module_name)}\s+import)'
    )
    # For all files: filename reference (word-boundary match with common delimiters)
    escaped = re.escape(basename)
    ref_pattern = re.compile(
        rf'(?:^|[\s"\'([{{<,]){escaped}(?:$|[\s"\')\]}}>,.:;!?])'
    )

    # Directories to skip entirely
    skip_dirs = {'.git', '__pycache__', 'node_modules', '.venv', 'venv', 'logs', 'sessions'}
    # Binary/file extensions to skip
    skip_ext = {'.pyc', '.pyo', '.exe', '.dll', '.so', '.dylib', '.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.ico', '.svg', '.woff', '.woff2', '.ttf', '.eot', '.pdf'}

    for root, dirs, files in os.walk(workdir):
        dirs[:] = [d for d in dirs if d not in skip_dirs]
        for f in files:
            if f == basename:
                continue  # skip the file itself
            ext = os.path.splitext(f)[1].lower()
            if ext in skip_ext:
                continue
            fpath = os.path.join(root, f)
            try:
                with open(fpath, 'r', encoding='utf-8', errors='replace') as fh:
                    for i, line in enumerate(fh, 1):
                        stripped = line.strip()
                        if stripped.startswith('#'):
                            continue
                        # .py files: check import pattern first
                        if f.endswith('.py'):
                            if import_pattern.search(stripped):
                                key = (f, i, 'import')
                                if key not in seen:
                                    seen.add(key)
                                    refs.append(f"{f}:{i}: {stripped}")
                                continue  # skip filename ref check to avoid dupes
                        # All text files: check filename reference
                        if ref_pattern.search(stripped):
                            key = (f, i, stripped[:80])
                            if key not in seen:
                                seen.add(key)
                                refs.append(f"{f}:{i}: {stripped}")
            except (OSError, UnicodeDecodeError, PermissionError):
                continue
    return refs


def delete_file(filepath: str) -> dict[str, Any]:
    """Delete a file from disk.

    Only works for files within the project directory (safe path check).
    Scans the entire repo for references to the file before allowing deletion.
    """
    path = _resolve_path(filepath)
    if not is_safe_location(path):
        return {"success": False, "error": "Adgang n\u00e6gtet: stien er uden for projektmappen"}
    if not os.path.exists(path):
        return {"success": False, "error": f"Filen findes ikke: {path}"}

    # Safety guard: check for references in the repo
    refs = _find_file_references(path)
    if refs:
        ref_list = "\n".join(refs[:10])
        if len(refs) > 10:
            ref_list += f"\n  ... og {len(refs) - 10} flere"
        return {
            "success": False,
            "error": (
                f"Filen '{os.path.basename(path)}' kan ikke slettes: den refereres stadig "
                f"i {len(refs)} fil(er). Fjern disse referencer f\u00f8rst:\n{ref_list}"
            ),
            "references": refs,
            "reference_count": len(refs),
        }

    try:
        os.remove(path)
    except Exception as e:
        return {"success": False, "error": f"Kunne ikke slette filen: {e}"}

    return {
        "success": True,
        "file": os.path.abspath(path),
    }
