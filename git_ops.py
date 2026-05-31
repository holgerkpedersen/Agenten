"""Git operations wrapper for Agent."""

import ast
import os
import re
import subprocess

import threading
from i18n import K
from lang import t
from agent_files import is_safe_location, locate_code

_file_lock = threading.Lock()
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


def _check_missing_deps(py_content, req_path):
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


def _extract_urls(content, source_path):
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


def _find_partner_files(path, other_ext):
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


def _check_route_mismatch(path, other_ext):
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


def _run_git(args, cwd=None):
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


def git_status(path="."):
    """git status.
    
    Args:
        path:"""
    return _run_git(["status", "--short"], cwd=path)


def git_remote_exists(path="."):
    """git remote exists.
    
    Args:
        path:"""
    r = _run_git(["remote", "get-url", "origin"], cwd=path)
    if r["success"]:
        return {"success": True, "url": r["output"]}
    return {"success": False, "error": "Intet remote 'origin' konfigureret"}


def git_add_all(path="."):
    """git add all.
    
    Args:
        path:"""
    return _run_git(["add", "-A"], cwd=path)


def git_commit(message, path="."):
    """git commit.
    
    Args:
        message:
        path:"""
    return _run_git(["commit", "-m", message], cwd=path)


def git_push(branch="main", path="."):
    """git push.
    
    Args:
        branch:
        path:"""
    remote = git_remote_exists(path)
    if not remote["success"]:
        return remote
    return _run_git(["push", "-u", "origin", branch], cwd=path)


def git_set_remote(url, path="."):
    """git set remote.
    
    Args:
        url:
        path:"""
    existing = git_remote_exists(path)
    if existing["success"]:
        _run_git(["remote", "remove", "origin"], cwd=path)
    return _run_git(["remote", "add", "origin", url], cwd=path)


def git_diff(older="HEAD~1", newer="HEAD", max_chars=8000):
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


def git_log(count=10):
    """git log.
    
    Args:
        count:"""
    return _run_git(["log", f"-{count}", "--oneline", "--decorate"])


def git_create_branch(name, path="."):
    """git create branch.
    
    Args:
        name:
        path:"""
    return _run_git(["checkout", "-b", name], cwd=path)


def git_current_branch(path="."):
    """git current branch.
    
    Args:
        path:"""
    return _run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=path)


def git_branch_list(path="."):
    """git branch list.
    
    Args:
        path:"""
    return _run_git(["branch"], cwd=path)


def git_pull(remote="origin", branch="main", path="."):
    """git pull.
    
    Args:
        remote:
        branch:
        path:"""
    return _run_git(["pull", remote, branch], cwd=path)


def git_checkout(branch, path="."):
    """git checkout.
    
    Args:
        branch:
        path:"""
    return _run_git(["checkout", branch], cwd=path)


def _check_post_write(path, content, result):
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


def write_file(path, content):
    """write file.
    
    Args:
        path:
        content:"""
    if not os.path.isabs(path):
        path = os.path.abspath(path)
    if not is_safe_location(path):
        return {"success": False, "error": f"Adgang nægtet: stien er uden for projektmappen: {path}"}
    dirname = os.path.dirname(path)
    if dirname:
        os.makedirs(dirname, exist_ok=True)
    try:
        if path.endswith('.py') and os.path.exists(path):
            return {
                "success": False,
                "error": f"Filen findes allerede: {path}. Brug edit_file til at redigere eksisterende filer."
            }
        if path.endswith('.py'):
            try:
                ast.parse(content)
            except SyntaxError as e:
                return {
                    "success": False,
                    "error": f"Syntaksfejl på linje {e.lineno}: {e.msg}",
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


def _build_flexible_pattern(text):
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


def _build_fuzzy_pattern(text):
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


def _has_placeholders(text):
    """has placeholders.
    
    Args:
        text:"""
    for pat in _PLACEHOLDER_PATTERNS:
        if pat.search(text):
            return True
    return False


def _detect_base_indentation(text):
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


def _normalize_indentation(new_text, search_text):
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


def edit_file(path, old_text="", new_text="", expected_hash=None, symbol=None):
    """edit file.
    
    Args:
        path:
        old_text:
        new_text:
        expected_hash:
        symbol:"""
    if not os.path.isabs(path):
        path = os.path.abspath(path)
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
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()

        # Normalize to \n-only for consistent byte positions (BUG-076 fix)
        content = content.replace('\r\n', '\n').replace('\r', '\n')

        if symbol:
            if not new_text:
                return {"success": False, "error": "new_text is required when using symbol."}
            loc = locate_code(filepath=path, name=symbol)
            if not loc.get("success"):
                return {"success": False, "error": f"Symbol '{symbol}' not found in {path}. Use old_text/new_text instead."}
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
        else:
            return {"success": False, "error": "Provide either symbol (AST-based) or old_text+new_text (search-and-replace)."}

        exact_old = content[idx:idx + search_len]
        if exact_old.count('\n') != search.count('\n'):
            lines = search.split('\n')
            pos = idx
            parts = []
            for line in lines:
                next_newline = content.find('\n', pos)
                if next_newline == -1:
                    # Ved filens ende uden newline
                    parts.append(content[pos:])
                    break
                parts.append(content[pos : next_newline + 1])
                pos = next_newline + 1
            exact_old = ''.join(parts)
        new_content = content.replace(exact_old, new_text, 1)
        if path.endswith('.py'):
            try:
                ast.parse(new_content)
            except SyntaxError as e:
                if symbol:
                    normalized_new = _normalize_indentation(new_text, search)
                    if normalized_new != new_text:
                        normalized_content = content.replace(exact_old, normalized_new, 1)
                        try:
                            ast.parse(normalized_content)
                            new_text = normalized_new
                            new_content = normalized_content
                        except SyntaxError as e2:
                            return {
                                "success": False,
                                "error": f"Syntaksfejl på linje {e2.lineno}: {e2.msg}",
                                "line": e2.lineno,
                                "msg": e2.msg
                            }
                    else:
                        return {
                            "success": False,
                            "error": f"Syntaksfejl på linje {e.lineno}: {e.msg}",
                            "line": e.lineno,
                            "msg": e.msg
                        }
                else:
                    return {
                        "success": False,
                        "error": f"Syntaksfejl på linje {e.lineno}: {e.msg}",
                        "line": e.lineno,
                        "msg": e.msg
                    }

        with _file_lock:
            with open(path, 'w', encoding='utf-8') as f:
                f.write(new_content)

        result = {
            "success": True,
            "path": os.path.abspath(path),
            "chars_before": len(content),
            "chars_after": len(new_content),
            "lines_changed": content.count('\n') - new_content.count('\n')
        }

        _check_post_write(path, new_content, result)

        return result
    except (IOError, OSError) as e:
        return {"success": False, "error": str(e)}


EXCLUDE_LIST_FILES = {'.env', '.env.example', 'credentials.json', 'secrets.json', '.gitconfig', 'id_rsa', 'id_rsa.pub', 'known_hosts', 'config.json'}

def list_files(path=".", pattern=None, max_depth=2):
    """list files.
    
    Args:
        path:
        pattern:
        max_depth:"""
    try:
        if not os.path.isabs(path):
            path = os.path.abspath(path)
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
