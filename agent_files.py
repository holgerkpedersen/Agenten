import ast
import os
import re
import hashlib
import tempfile
from typing import Any, Dict, List, Tuple, Optional
from lang import t
from i18n import K
import config

# Cache for file content to prevent repeated reading of the same file
_FILE_CONTENT_CACHE: Dict[str, Tuple[str, float]] = {}
_CONTENT_CACHE_MAX_SIZE = 100  # Maximum number of files to cache
_CONTENT_CACHE_TTL = 60  # Cache time-to-live in seconds

_indexed_dirs: set = set()


def _is_file_cached(filepath: str) -> bool:
    """Check if file is in cache and not expired."""
    if filepath not in _FILE_CONTENT_CACHE:
        return False
    
    content, timestamp = _FILE_CONTENT_CACHE[filepath]
    try:
        # Check if file has been modified
        if os.path.getmtime(filepath) != timestamp:
            # File has been modified, remove from cache
            del _FILE_CONTENT_CACHE[filepath]
            return False
        return True
    except OSError:
        # File no longer exists, remove from cache
        if filepath in _FILE_CONTENT_CACHE:
            del _FILE_CONTENT_CACHE[filepath]
        return False


def _cache_file_content(filepath: str, content: str) -> None:
    """Cache file content with LRU eviction when cache is too large."""
    global _FILE_CONTENT_CACHE
    
    # Remove if already exists
    if filepath in _FILE_CONTENT_CACHE:
        del _FILE_CONTENT_CACHE[filepath]
    
    # Add to cache
    try:
        _FILE_CONTENT_CACHE[filepath] = (content, os.path.getmtime(filepath))
    except OSError:
        # If we can't get the file's mtime, don't cache it
        return
    
    # Enforce maximum size by removing oldest entries
    if len(_FILE_CONTENT_CACHE) > _CONTENT_CACHE_MAX_SIZE:
        # Sort by timestamp (oldest first) and remove excess
        sorted_items = sorted(_FILE_CONTENT_CACHE.items(), key=lambda x: x[1][1])
        for key, _ in sorted_items[:-_CONTENT_CACHE_MAX_SIZE]:
            del _FILE_CONTENT_CACHE[key]


def _get_cached_file_content(filepath: str) -> Optional[str]:
    """Get cached file content if available and not expired."""
    if _is_file_cached(filepath):
        return _FILE_CONTENT_CACHE[filepath][0]
    return None


def _clear_file_content_cache() -> None:
    """Clear the file content cache."""
    global _FILE_CONTENT_CACHE
    _FILE_CONTENT_CACHE.clear()


CHUNK_SIZE = config.CHUNK_SIZE
FOLDER_SCAN_MAX_FILES = config.FOLDER_SCAN_MAX_FILES
FOLDER_SCAN_MAX_DEPTH = config.FOLDER_SCAN_MAX_DEPTH
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_SAFE_DIRS = {os.path.realpath(p) for p in [_BASE_DIR] if p}
for _td in (os.environ.get(k) for k in ('TMPDIR', 'TEMP', 'TMP')):
    if _td:
        _SAFE_DIRS.add(os.path.realpath(_td))
_SAFE_DIRS.add(os.path.realpath(tempfile.gettempdir()))
for _sub in ('exports', 'uploads'):
    _p = os.path.realpath(os.path.join(_BASE_DIR, _sub))
    _SAFE_DIRS.add(_p)


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

    # Don't override an explicitly set --workdir
    if os.environ.get('AGENT_WORKDIR_LOCKED'):
        return os.environ.get('AGENT_WORKDIR') or min(detected, key=len)

    workdir = min(detected, key=len)
    os.environ['AGENT_WORKDIR'] = workdir
    _ensure_workdir_indexed()
    return workdir


def _resolve_path(path: str) -> str:
    """Resolve a path relative to the workdir when outside Agenten cwd, otherwise relative to cwd."""
    if os.path.isabs(path):
        return os.path.abspath(path)
    workdir = _resolve_workdir()
    cwd = os.path.abspath('.')
    if os.path.normcase(workdir) != os.path.normcase(cwd):
        joined = os.path.abspath(os.path.join(workdir, path))
        if os.path.exists(joined):
            return joined
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





def chunk_text(text: str, size: int = CHUNK_SIZE) -> list[str]:
    """chunk text.
    
    Args:
        text:
        size:"""
    chunks = []
    for i in range(0, len(text), size):
        chunks.append(text[i:i + size])
    return chunks


STUB_PATTERN = re.compile(
    r'def\s+(\w+)\(self[^)]*\):\s*\n\s+return\s+(\w+)\.\1\b'
)


def detect_delegations(content: str) -> list[tuple[str, str]]:
    """detect delegations.
    
    Args:
        content:"""
    stubs = []
    for m in STUB_PATTERN.finditer(content):
        stubs.append((m.group(1), m.group(2)))
    return stubs


def _format_params(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """format params.
    
    Args:
        node:"""
    args = node.args
    parts = []
    # positional args
    total = len(args.args)
    defaults = [None] * (total - len(args.defaults)) + list(args.defaults) if args.defaults else [None] * total
    for i, a in enumerate(args.args):
        prefix = "self, " if i == 0 and a.arg == "self" else ""
        if i == 0 and a.arg == "self":
            continue
        p = a.arg
        if defaults[i] is not None:
            try:
                p += f"={ast.unparse(defaults[i])}"
            except Exception:
                p += "=..."
        parts.append(p)
    if args.vararg:
        parts.append(f"*{args.vararg.arg}")
    for a in args.kwonlyargs:
        parts.append(f"{a.arg}=...")
    if args.kwarg:
        parts.append(f"**{args.kwarg.arg}")
    return ", ".join(parts)


def build_ast_index(code: str, filename: str) -> str | None:
    """build ast index.
    
    Args:
        code:
        filename:"""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return None
    index_lines = [f"### {filename}"]
    def _sig(n):
        """sig.
        
        Args:
            n:"""
        return _format_params(n)

    def _doc(n):
        """doc.
        
        Args:
            n:"""
        d = ast.get_docstring(n) or ""
        return (" — " + d.splitlines()[0][:80]) if d else ""

    class _Builder(ast.NodeVisitor):
        """builder.
        
        Extends: ast.NodeVisitor"""
        def __init__(self) -> None:
            """Initialize the instance."""
            self.in_class = False
        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            """visit class def.
            
            Args:
                node:"""
            old = self.in_class
            self.in_class = True
            methods = []
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    methods.append(f"    {item.name}({_sig(item)}) [{item.lineno}]{_doc(item)}")
            index_lines.append(f"  class {node.name} [{node.lineno}]{_doc(node)}")
            index_lines.extend(methods)
            self.generic_visit(node)
            self.in_class = old
        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            """visit function def.
            
            Args:
                node:"""
            if not self.in_class:
                index_lines.append(f"  {node.name}({_sig(node)}) [{node.lineno}]{_doc(node)}")
            self.generic_visit(node)
        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            """visit async function def.
            
            Args:
                node:"""
            if not self.in_class:
                index_lines.append(f"  {node.name}({_sig(node)}) [{node.lineno}]{_doc(node)}")
            self.generic_visit(node)

    _Builder().visit(tree)
    return "\n".join(index_lines) if len(index_lines) > 1 else None


def file_hash(filepath: str) -> str | None:
    """file hash.
    
    Args:
        filepath:"""
    try:
        size = os.path.getsize(filepath)
        if size > config.MAX_IMAGE_SIZE * 2:
            return None
        h = hashlib.sha256()
        with open(filepath, 'rb') as f:
            for chunk in iter(lambda: f.read(65536), b''):
                h.update(chunk)
        return h.hexdigest()
    except (IOError, OSError):
        return None


def read_file_content(agent: Any, filepath: str) -> str | None:
    """read file content.
    
    Args:
        agent:
        filepath:"""
    # Check cache first
    cached_content = _get_cached_file_content(filepath)
    if cached_content is not None:
        agent._log("DEBUG", f"Using cached content for", os.path.basename(filepath))
        return cached_content
    
    basename = os.path.basename(filepath)
    if basename in {'.env'}:
        return None
    ext = os.path.splitext(filepath)[1].lower()
    if ext in {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.ico', '.zip', '.exe', '.dll'}:
        return None
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
        if '\x00' in content:
            return None
        if len(content) > CHUNK_SIZE:
            content = content[:CHUNK_SIZE] + "\n" + t(K.FILE_TRUNCATED, agent.lang)
        # Cache the content
        _cache_file_content(filepath, content)
        return content
    except (UnicodeDecodeError, Exception) as e:
        agent._log("WARNING", f"Kan ikke læse {os.path.basename(filepath)} som tekst", str(e))
        return None


FOLDER_SCAN_EXCLUDE_DIRS = {'node_modules', '.git', 'venv', '.venv', '__pycache__', '.opencode', '.agent_storage'}
FOLDER_SCAN_EXCLUDE_FILES = {'.env'}
FOLDER_SCAN_EXTENSIONS = {'.py', '.js', '.json', '.html', '.css', '.yml', '.yaml', '.toml', '.md', '.txt', '.bat', '.cfg', '.ini', '.sh', '.jsx', '.ts', '.tsx', '.vue', '.svelte'}


def get_single_file_context(agent: Any, prompt: str) -> tuple[str | None, str | None]:
    """get single file context.
    
    Args:
        agent:
        prompt:"""
    file_match = re.search(r'analyser\s+([^\s]+\.py)', prompt, re.IGNORECASE)
    if not file_match:
        return None, None

    filename = file_match.group(1)
    agent._log("INFO", t(K.LOG_READING_FILE, agent.lang), filename)

    base_dir = os.path.dirname(os.path.abspath(__file__))
    possible_paths = [
        filename,
        os.path.join(base_dir, filename),
        os.path.join(base_dir, 'static', filename),
        os.path.join(base_dir, 'sessions', filename),
        os.path.join(os.getcwd(), filename),
        os.path.join(base_dir, '..', filename),
    ]

    for path in possible_paths:
        if os.path.exists(path):
            resolved = os.path.realpath(path)
            if not (os.path.normcase(resolved).startswith(os.path.normcase(base_dir) + os.sep) or os.path.normcase(resolved) == os.path.normcase(base_dir)):
                continue
            content = read_file_content(agent, path)
            if content:
                agent._log("INFO", t(K.LOG_FILE_FOUND, agent.lang), path)
                return path, content

    agent._log("WARNING", t(K.LOG_FILE_NOT_FOUND, agent.lang), filename)
    return None, None


def get_folder_context(agent: Any, prompt: str) -> list[dict[str, str]] | None:
    """get folder context.
    
    Args:
        agent:
        prompt:"""
    folder_pattern = re.compile(r'(?:[A-Za-z]:[\\/][^\s,;"\']+|/[^\s,;"\']+)')
    folders = set()
    for match in folder_pattern.finditer(prompt):
        raw = match.group(0)
        path = os.path.normpath(raw)
        if os.path.isdir(path):
            folders.add(path)
        elif os.path.isfile(path):
            parent = os.path.dirname(path)
            if os.path.isdir(parent):
                folders.add(parent)

    if not folders:
        return None

    folders = {f for f in folders if is_safe_location(f)}
    if not folders:
        agent._log("WARNING", "Ingen tilladte mapper at scanne", "Alle fundne stier var udenfor projektet")
        return None

    agent._log("INFO", "Automatisk scanning af mapper", ", ".join(sorted(folders)))

    found_files = []
    for folder in sorted(folders):
        for dirpath, dirnames, filenames in os.walk(folder):
            rel = os.path.relpath(dirpath, folder)
            depth = 0 if rel == '.' else rel.count(os.sep) + 1
            if depth > FOLDER_SCAN_MAX_DEPTH:
                dirnames.clear()
                continue
            dirnames[:] = [d for d in dirnames if d not in FOLDER_SCAN_EXCLUDE_DIRS]
            for f in sorted(filenames):
                if f in FOLDER_SCAN_EXCLUDE_FILES:
                    continue
                ext = os.path.splitext(f)[1].lower()
                if ext not in FOLDER_SCAN_EXTENSIONS:
                    continue
                if len(found_files) >= FOLDER_SCAN_MAX_FILES:
                    break
                filepath = os.path.join(dirpath, f)
                content = read_file_content(agent, filepath)
                if content:
                    relpath = os.path.relpath(filepath, folder)
                    found_files.append({"filename": relpath, "content": content, "path": filepath})
            if len(found_files) >= FOLDER_SCAN_MAX_FILES:
                break
        if len(found_files) >= FOLDER_SCAN_MAX_FILES:
            break

    if not found_files:
        agent._log("WARNING", "Ingen relevante filer fundet i mapper", ", ".join(sorted(folders)))
        return None

    for item in found_files:
        agent._log("DEBUG", "Scanned", item["path"])
    return found_files


def read_location(filepath: str, name: str | None = None, line_no: int | None = None) -> dict[str, Any]:
    """Read ONLY the function/class/method at a specific location via AST.
    Returns just the relevant code body, not the entire file.
    Use this instead of read_chunk when you need to see specific code.
    """
    result = locate_code(filepath=filepath, name=name, line_no=line_no)
    if not result.get("success"):
        return result
    return {
        "success": True,
        "file": result["file"],
        "name": result["name"],
        "type": result["type"],
        "line": result["line"],
        "end_line": result["end_line"],
        "content": result["body"],
        "also_in_file": result.get("also_in_file", ""),
    }


def list_chunks(agent: Any) -> dict[str, Any]:
    """list chunks.
    
    Args:
        agent:"""
    if not agent.file_chunks:
        return {"success": True, "chunks": [], "message": "Ingen filer indl\u00e6st. Brug 'list_chunks' igen efter at have specificeret filer eller en mappe i din prompt."}
    result = []
    for key, chunks in agent.file_chunks.items():
        display = key.replace("file_", "", 1)
        result.append({"file": display, "chunks": len(chunks)})
    return {"success": True, "chunks": result, "count": len(result)}


def read_chunk(agent: Any, chunk: str, index: int) -> dict[str, Any]:
    """read chunk.
    
    Args:
        agent:
        chunk:
        index:"""
    original = chunk
    if not chunk.startswith("file_"):
        chunk = "file_" + chunk
    chunks = agent.file_chunks.get(chunk)
    if not chunks:
        available = [k.replace("file_", "", 1) for k in agent.file_chunks.keys()] or ["ingen"]
        return {"success": False, "error": f"Ukendt chunk: '{original}'. Tilg\u00e6ngelige filer: {available}. Brug 'list_chunks' for at se alle."}
    if index < 1 or index > len(chunks):
        return {"success": False, "error": f"Chunk {index} findes ikke (1..{len(chunks)})"}
    agent._log("READ", f"L\u00e6st chunk {index}/{len(chunks)}: {original}", f"{len(chunks[index - 1])} tegn")
    return {"success": True, "chunk": chunk, "index": index, "total": len(chunks), "content": chunks[index - 1]}


def _find_enclosing_symbol(tree: ast.Module, target_line: int) -> ast.AST | None:
    """find enclosing symbol.
    
    Args:
        tree:
        target_line:"""
    best = None
    class NodeVisitor(ast.NodeVisitor):
        """node visitor.
        
        Extends: ast.NodeVisitor"""
        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            """visit function def.
            
            Args:
                node:"""
            nonlocal best
            if node.lineno <= target_line <= (getattr(node, 'end_lineno', node.lineno) or node.lineno):
                best = node
            self.generic_visit(node)

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            """visit async function def.
            
            Args:
                node:"""
            nonlocal best
            if node.lineno <= target_line <= (getattr(node, 'end_lineno', node.lineno) or node.lineno):
                best = node
            self.generic_visit(node)

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            """visit class def.
            
            Args:
                node:"""
            nonlocal best
            if node.lineno <= target_line <= (getattr(node, 'end_lineno', node.lineno) or node.lineno):
                best = node
            self.generic_visit(node)

    NodeVisitor().visit(tree)
    return best


def _list_top_level_vars(tree: ast.Module) -> list[tuple[str, str, int]]:
    """list top level vars.
    
    Args:
        tree:"""
    vars = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    vars.append((target.id, "variable", node.lineno))
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name):
                vars.append((node.target.id, "variable", node.lineno))
    return vars


def _list_top_level_symbols(tree: ast.Module) -> list[tuple[str, str, int]]:
    """list top level symbols.
    
    Args:
        tree:"""
    symbols = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.FunctionDef):
            symbols.append((node.name, "function", node.lineno))
        elif isinstance(node, ast.AsyncFunctionDef):
            symbols.append((node.name, "async_function", node.lineno))
        elif isinstance(node, ast.ClassDef):
            symbols.append((node.name, "class", node.lineno))
    symbols.extend(_list_top_level_vars(tree))
    return symbols


def _scan_dir_into_index(dir_path: str, index: dict) -> None:
    """Scan a directory for .py files and add symbols to the index.
    
    Skips directories already in _indexed_dirs to avoid re-scanning.
    """
    abs_path = os.path.abspath(dir_path)
    if abs_path in _indexed_dirs:
        return
    _indexed_dirs.add(abs_path)

    try:
        entries = os.listdir(abs_path)
    except Exception:
        return

    for fname in entries:
        if not fname.endswith('.py'):
            continue
        fpath = os.path.join(abs_path, fname)
        try:
            with open(fpath, 'r', encoding='utf-8') as f:
                tree = ast.parse(f.read())
        except Exception:
            continue
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                index.setdefault(node.name, []).append((fname, node.lineno))
            elif isinstance(node, ast.ClassDef):
                index.setdefault(node.name, []).append((fname, node.lineno, "class"))
                for child in node.body:
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        dotted = f"{node.name}.{child.name}"
                        index.setdefault(dotted, []).append((fname, child.lineno))
                        index.setdefault(child.name, []).append((fname, child.lineno))
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        index.setdefault(target.id, []).append((fname, node.lineno))
            elif isinstance(node, ast.AnnAssign):
                if isinstance(node.target, ast.Name):
                    index.setdefault(node.target.id, []).append((fname, node.lineno))


def _build_global_symbol_index() -> dict[str, list[Any]]:
    """Build global symbol index from CWD + AGENT_WORKDIR if different."""
    index = {}
    cwd = os.path.abspath('.')
    _scan_dir_into_index(cwd, index)
    workdir = _resolve_workdir()
    if os.path.normcase(workdir) != os.path.normcase(cwd):
        _scan_dir_into_index(workdir, index)
    return index


def _ensure_workdir_indexed() -> None:
    """Ensure workdir .py files are included in the global symbol index."""
    workdir = _resolve_workdir()
    cwd = os.path.abspath('.')
    if os.path.normcase(workdir) != os.path.normcase(cwd):
        _scan_dir_into_index(workdir, _GLOBAL_SYMBOL_INDEX)

_GLOBAL_SYMBOL_INDEX = _build_global_symbol_index()


def list_symbols(filepath: str) -> dict[str, Any]:
    """List all top-level symbols (functions, classes, variables) in a Python file.
    
    Args:
        filepath: Path to the .py file

    Returns:
        dict with success flag, filepath, and symbols list"""
    filepath = _resolve_path(filepath)
    if not filepath or not os.path.exists(filepath):
        return {"success": False, "error": f"File not found: {filepath}"}
    if not filepath.lower().endswith('.py'):
        return {
            "success": False,
            "error": (
                f"list_symbols understøtter kun Python-filer (.py), fik '{os.path.basename(filepath)}'. "
                f"Brug read_chunk for andre filtyper."
            ),
        }
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            code = f.read()
        tree = ast.parse(code)
    except SyntaxError as e:
        return {"success": False, "error": f"Syntax error in {os.path.basename(filepath)}: {e}"}
    except Exception as e:
        return {"success": False, "error": f"Could not parse {os.path.basename(filepath)}: {e}"}

    symbols = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            sig = "(" + ", ".join(a.arg for a in node.args.args) + ")"
            symbols.append({"name": node.name, "type": "function", "line": node.lineno, "signature": sig})
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.ClassDef):
            methods = []
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    sig = "(" + ", ".join(a.arg for a in child.args.args) + ")"
                    methods.append({"name": child.name, "type": "method", "line": child.lineno, "signature": sig})
            symbols.append({"name": node.name, "type": "class", "line": node.lineno, "methods": methods})
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    symbols.append({"name": target.id, "type": "variable", "line": node.lineno})
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name):
                symbols.append({"name": node.target.id, "type": "variable", "line": node.lineno})

    symbols.sort(key=lambda s: ({"class": 0, "function": 1, "async_function": 1, "method": 2, "variable": 3}.get(s.get("type", "variable"), 9), s.get("line", 0)))

    return {
        "success": True,
        "filepath": filepath,
        "symbols": symbols,
        "count": len(symbols),
    }


def locate_code(filepath: str | None = None, name: str | None = None, line_no: int | None = None) -> dict[str, Any]:
    """locate code.
    
    Args:
        filepath:
        name:
        line_no:"""
    _ensure_workdir_indexed()

    if not filepath and name:
        matches = _GLOBAL_SYMBOL_INDEX.get(name, [])
        if not matches:
            suggestions = sorted(_GLOBAL_SYMBOL_INDEX.keys())
            tip = ""
            if suggestions:
                fuzzy = [s for s in suggestions if name.lower() in s.lower()][:5]
                if fuzzy:
                    tip = f" Mente du: {', '.join(fuzzy)}?"
                else:
                    tip = f" Prøv list_symbols eller brug et af disse symboler: {', '.join(suggestions[:12])}"
            return {"success": False, "error": f"Symbol '{name}' not found in any file.{tip}"}
        if len(matches) > 1:
            files = ", ".join(m[0] for m in matches)
            return {"success": False, "error": f"Symbol '{name}' found in multiple files: {files}. Specify filepath='fil.py' to disambiguate."}
        filepath = matches[0][0]
    if filepath:
        filepath = _resolve_path(filepath)
    if not filepath or not os.path.exists(filepath):
        return {"success": False, "error": f"File not found: {filepath}"}
    if not filepath.lower().endswith('.py'):
        return {
            "success": False,
            "error": (
                f"locate_code understøtter kun Python-filer (.py), fik '{os.path.basename(filepath)}'. "
                f"Brug list_symbols eller read_chunk for andre filtyper."
            ),
        }
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            code = f.read()
        tree = ast.parse(code)
    except SyntaxError as e:
        return {"success": False, "error": f"Syntax error in {os.path.basename(filepath)}: {e}"}
    except Exception as e:
        return {"success": False, "error": f"Could not parse {os.path.basename(filepath)}: {e}"}

    symbols = _list_top_level_symbols(tree)
    siblings = "\n".join(f"  {s[0]} ({s[1]}, line {s[2]})" for s in symbols) if symbols else "  (none)"

    _lines = code.splitlines()
    def _ctx(s: int, e: int, n: int = 5) -> dict:
        """Build pre/post context dict from line numbers (1-indexed)."""
        pre = "\n".join(_lines[max(0, s - 1 - n):s - 1])
        post = "\n".join(_lines[e:min(len(_lines), e + n)])
        return {"pre_context": pre, "post_context": post}

    if name:
        parts = name.split(".", 1)
        func_name = parts[-1]
        class_name = parts[0] if len(parts) == 2 else None

        for node in ast.walk(tree):
            if class_name:
                if isinstance(node, ast.ClassDef) and node.name == class_name:
                    for child in node.body:
                        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child.name == func_name:
                            start = child.lineno
                            end = getattr(child, 'end_lineno', start) or start
                            return {
                                "success": True,
                                "file": filepath,
                                "name": name,
                                "type": "method",
                                "line": start,
                                "end_line": end,
                                "body": "\n".join(_lines[start - 1:end]),
                                "also_in_file": siblings,
                                **_ctx(start, end),
                            }
                    return {"success": False, "error": f"Method '{func_name}' not found in class '{class_name}' in {os.path.basename(filepath)}"}
            else:
                if isinstance(node, ast.FunctionDef) and node.name == func_name:
                    start = node.lineno
                    # Include decorators if present
                    if node.decorator_list:
                        decorator_lines = [d.lineno for d in node.decorator_list if hasattr(d, 'lineno')]
                        if decorator_lines:
                            start = min(decorator_lines)
                    end = getattr(node, 'end_lineno', start) or start
                    return {
                        "success": True,
                        "file": filepath,
                        "name": name,
                        "type": "function",
                        "line": start,
                        "end_line": end,
                        "body": "\n".join(_lines[start - 1:end]),
                        "also_in_file": siblings,
                        **_ctx(start, end),
                    }
                elif isinstance(node, ast.ClassDef) and node.name == func_name:
                    start = node.lineno
                    if node.decorator_list:
                        decorator_lines = [d.lineno for d in node.decorator_list if hasattr(d, 'lineno')]
                        if decorator_lines:
                            start = min(decorator_lines)
                    end = getattr(node, 'end_lineno', start) or start
                    return {
                        "success": True,
                        "file": filepath,
                        "name": name,
                        "type": "class",
                        "line": start,
                        "end_line": end,
                        "body": "\n".join(_lines[start - 1:end]),
                        "also_in_file": siblings,
                        **_ctx(start, end),
                    }
                elif isinstance(node, ast.AsyncFunctionDef) and node.name == func_name:
                    start = node.lineno
                    # Include decorators if present
                    if node.decorator_list:
                        decorator_lines = [d.lineno for d in node.decorator_list if hasattr(d, 'lineno')]
                        if decorator_lines:
                            start = min(decorator_lines)
                    end = getattr(node, 'end_lineno', start) or start
                    return {
                        "success": True,
                        "file": filepath,
                        "name": name,
                        "type": "async_function",
                        "line": start,
                        "end_line": end,
                        "body": "\n".join(_lines[start - 1:end]),
                        "also_in_file": siblings,
                        **_ctx(start, end),
                    }
                elif isinstance(node, ast.Assign) and func_name in (t.id for t in node.targets if isinstance(t, ast.Name)):
                    start = node.lineno
                    end = getattr(node, 'end_lineno', start) or start
                    return {
                        "success": True,
                        "file": filepath,
                        "name": name,
                        "type": "variable",
                        "line": start,
                        "end_line": end,
                        "body": "\n".join(_lines[start - 1:end]),
                        "also_in_file": siblings,
                        **_ctx(start, end),
                    }
                elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == func_name:
                    start = node.lineno
                    end = getattr(node, 'end_lineno', start) or start
                    return {
                        "success": True,
                        "file": filepath,
                        "name": name,
                        "type": "variable",
                        "line": start,
                        "end_line": end,
                        "body": "\n".join(_lines[start - 1:end]),
                        "also_in_file": siblings,
                        **_ctx(start, end),
                    }
        return {"success": False, "error": f"Symbol '{name}' not found in {os.path.basename(filepath)}"}

    if line_no is not None:
        symbol = _find_enclosing_symbol(tree, line_no)
        if symbol is None:
            return {
                "success": True,
                "file": filepath,
                "name": None,
                "type": "module",
                "line": line_no,
                "end_line": line_no,
                "body": _lines[line_no - 1] if 1 <= line_no <= len(_lines) else "",
                "pre_context": "\n".join(_lines[max(0, line_no - 1 - 5):line_no - 1]),
                "post_context": "\n".join(_lines[line_no:min(len(_lines), line_no + 5)]),
            }

        node_type = "class" if isinstance(symbol, ast.ClassDef) else "async_function" if isinstance(symbol, ast.AsyncFunctionDef) else "function"
        start = symbol.lineno
        end = getattr(symbol, 'end_lineno', start) or start

        class_name = None
        for parent in ast.walk(tree):
            if isinstance(parent, ast.ClassDef) and hasattr(parent, 'body'):
                if symbol in [child for child in ast.walk(parent) if child is symbol]:
                    class_name = parent.name
                    break

        full_name = f"{class_name}.{symbol.name}" if class_name else symbol.name
        node_type = "method" if class_name else node_type

        return {
            "success": True,
            "file": filepath,
            "name": full_name,
            "type": node_type,
            "line": start,
            "end_line": end,
            "body": "\n".join(_lines[start - 1:end]),
            "also_in_file": siblings,
            **_ctx(start, end),
        }

    return {"success": False, "error": "Specify either 'name' or 'line_no'"}
