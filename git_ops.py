import ast
import os
import re
import subprocess
import shlex
from i18n import K
from lang import t

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
        req_pkgs = {line.split('==')[0].split('>=')[0].split('~=')[0].strip().lower()
                    for line in f if line.strip() and not line.startswith(('#', '-i', '--'))}
    return sorted(name for name in imports if name.lower() not in req_pkgs)


def _extract_urls(content, source_path):
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
    if cwd is None:
        cwd = os.getcwd()

    safe_path = os.path.realpath(cwd)
    cmd = ["git"] + args

    result = subprocess.run(cmd, cwd=safe_path, capture_output=True, text=True, timeout=30, encoding='utf-8', errors='replace')
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


def git_create_branch(name, path="."):
    return _run_git(["checkout", "-b", name], cwd=path)


def git_current_branch(path="."):
    return _run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=path)


def git_branch_list(path="."):
    return _run_git(["branch"], cwd=path)


def git_pull(remote="origin", branch="master", path="."):
    return _run_git(["pull", remote, branch], cwd=path)


def git_checkout(branch, path="."):
    return _run_git(["checkout", branch], cwd=path)


def write_file(path, content):
    dirname = os.path.dirname(path)
    if dirname:
        os.makedirs(dirname, exist_ok=True)
    try:
        content = content.replace('\\r\\n', '\r\n').replace('\\n', '\n')
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        result = {"success": True, "path": os.path.abspath(path), "chars": len(content)}
        if path.endswith('.py'):
            try:
                ast.parse(content)
            except SyntaxError as e:
                result["syntax_error"] = f"Linje {e.lineno}: {e.msg}"
            req_path = os.path.join(dirname or os.getcwd(), 'requirements.txt')
            missing = _check_missing_deps(content, req_path)
            if missing:
                result["missing_deps"] = missing
                if os.path.exists(req_path):
                    with open(req_path, 'a', encoding='utf-8') as f:
                        f.write('\n' + '\n'.join(missing) + '\n')
                    result["req_updated"] = missing
        if path.endswith('.py'):
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
    except (IOError, OSError) as e:
        return {"success": False, "error": str(e)}
