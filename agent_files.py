import ast
import os
import re
import hashlib
import tempfile
from typing import Any
from lang import t
from i18n import K
import config

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


def is_safe_location(target_path: str) -> bool:
    """Checks if target_path is within any known-safe directory (project root, exports, uploads, temp)."""
    try:
        real = os.path.realpath(target_path) if os.path.exists(target_path) else os.path.abspath(target_path)
        real = os.path.normcase(real)
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
            if not (resolved.startswith(base_dir + os.sep) or resolved == base_dir):
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


def _build_global_symbol_index() -> dict[str, list[Any]]:
    """build global symbol index."""
    index = {}
    for fname in os.listdir('.'):
        if not fname.endswith('.py'):
            continue
        try:
            with open(fname, 'r', encoding='utf-8') as f:
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
    return index

_GLOBAL_SYMBOL_INDEX = _build_global_symbol_index()


def locate_code(filepath: str | None = None, name: str | None = None, line_no: int | None = None) -> dict[str, Any]:
    """locate code.
    
    Args:
        filepath:
        name:
        line_no:"""
    if not filepath and name:
        matches = _GLOBAL_SYMBOL_INDEX.get(name, [])
        if not matches:
            return {"success": False, "error": f"Symbol '{name}' not found in any file"}
        if len(matches) > 1:
            files = ", ".join(m[0] for m in matches)
            return {"success": False, "error": f"Symbol '{name}' found in multiple files: {files}. Specify filepath='fil.py' to disambiguate."}
        filepath = matches[0][0]
    if not filepath or not os.path.exists(filepath):
        return {"success": False, "error": f"File not found: {filepath}"}
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
                                "body": "\n".join(code.splitlines()[start - 1:end]),
                                "also_in_file": siblings,
                            }
                    return {"success": False, "error": f"Method '{func_name}' not found in class '{class_name}' in {os.path.basename(filepath)}"}
            else:
                if isinstance(node, ast.FunctionDef) and node.name == func_name:
                    start = node.lineno
                    end = getattr(node, 'end_lineno', start) or start
                    return {
                        "success": True,
                        "file": filepath,
                        "name": name,
                        "type": "function",
                        "line": start,
                        "end_line": end,
                        "body": "\n".join(code.splitlines()[start - 1:end]),
                        "also_in_file": siblings,
                    }
                elif isinstance(node, ast.ClassDef) and node.name == func_name:
                    start = node.lineno
                    end = getattr(node, 'end_lineno', start) or start
                    return {
                        "success": True,
                        "file": filepath,
                        "name": name,
                        "type": "class",
                        "line": start,
                        "end_line": end,
                        "body": "\n".join(code.splitlines()[start - 1:end]),
                        "also_in_file": siblings,
                    }
                elif isinstance(node, ast.AsyncFunctionDef) and node.name == func_name:
                    start = node.lineno
                    end = getattr(node, 'end_lineno', start) or start
                    return {
                        "success": True,
                        "file": filepath,
                        "name": name,
                        "type": "async_function",
                        "line": start,
                        "end_line": end,
                        "body": "\n".join(code.splitlines()[start - 1:end]),
                        "also_in_file": siblings,
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
                        "body": "\n".join(code.splitlines()[start - 1:end]),
                        "also_in_file": siblings,
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
                        "body": "\n".join(code.splitlines()[start - 1:end]),
                        "also_in_file": siblings,
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
                "body": code.splitlines()[line_no - 1] if 1 <= line_no <= len(code.splitlines()) else "",
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
            "body": "\n".join(code.splitlines()[start - 1:end]),
            "also_in_file": siblings,
        }

    return {"success": False, "error": "Specify either 'name' or 'line_no'"}
