import os
import re
import hashlib
import tempfile
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


def _is_safe_scan_path(target_path):
    try:
        real = os.path.realpath(target_path) if os.path.exists(target_path) else os.path.abspath(target_path)
        for safe in _SAFE_DIRS:
            if real.startswith(safe + os.sep) or real == safe:
                return True
        return False
    except Exception:
        return False


def chunk_text(text, size=CHUNK_SIZE):
    chunks = []
    for i in range(0, len(text), size):
        chunks.append(text[i:i + size])
    return chunks


STUB_PATTERN = re.compile(
    r'def\s+(\w+)\(self[^)]*\):\s*\n\s+return\s+(\w+)\.\1\b'
)


def detect_delegations(content):
    stubs = []
    for m in STUB_PATTERN.finditer(content):
        stubs.append((m.group(1), m.group(2)))
    return stubs


def file_hash(filepath):
    h = hashlib.sha256()
    with open(filepath, 'rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()


def read_file_content(agent, filepath):
    basename = os.path.basename(filepath)
    if basename in {'.env'}:
        return None
    ext = os.path.splitext(filepath)[1].lower()
    if ext in {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.ico', '.svg', '.pdf', '.zip', '.exe', '.dll'}:
        return None
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
        if len(content) > CHUNK_SIZE:
            content = content[:CHUNK_SIZE] + "\n" + t(K.FILE_TRUNCATED, agent.lang)
        return content
    except (UnicodeDecodeError, Exception) as e:
        agent._log("WARNING", f"Kan ikke læse {os.path.basename(filepath)} som tekst", str(e))
        return None


FOLDER_SCAN_EXCLUDE_DIRS = {'node_modules', '.git', 'venv', '.venv', '__pycache__', '.opencode', '.agent_storage'}
FOLDER_SCAN_EXCLUDE_FILES = {'.env'}
FOLDER_SCAN_EXTENSIONS = {'.py', '.js', '.json', '.html', '.css', '.yml', '.yaml', '.toml', '.md', '.txt', '.bat', '.cfg', '.ini', '.sh', '.jsx', '.ts', '.tsx', '.vue', '.svelte'}


def get_single_file_context(agent, prompt):
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
            content = read_file_content(agent, path)
            if content:
                agent._log("INFO", t(K.LOG_FILE_FOUND, agent.lang), path)
                return path, content

    agent._log("WARNING", t(K.LOG_FILE_NOT_FOUND, agent.lang), filename)
    return None, None


def get_folder_context(agent, prompt):
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

    folders = {f for f in folders if _is_safe_scan_path(f)}
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
        agent._log("INFO", "Fundet fil", item["path"])
    return found_files


def list_chunks(agent):
    if not agent.file_chunks:
        return {"success": True, "chunks": [], "message": "Ingen filer indl\u00e6st. Brug 'list_chunks' igen efter at have specificeret filer eller en mappe i din prompt."}
    result = []
    for key, chunks in agent.file_chunks.items():
        display = key.replace("file_", "", 1)
        result.append({"file": display, "chunks": len(chunks)})
    return {"success": True, "chunks": result, "count": len(result)}


def read_chunk(agent, chunk, index):
    original = chunk
    if not chunk.startswith("file_"):
        chunk = "file_" + chunk
    chunks = agent.file_chunks.get(chunk)
    if not chunks:
        available = [k.replace("file_", "", 1) for k in agent.file_chunks.keys()] or ["ingen"]
        return {"success": False, "error": f"Ukendt chunk: '{original}'. Tilg\u00e6ngelige filer: {available}. Brug 'list_chunks' for at se alle."}
    if index < 1 or index > len(chunks):
        return {"success": False, "error": f"Chunk {index} findes ikke (1..{len(chunks)})"}
    return {"success": True, "chunk": chunk, "index": index, "total": len(chunks), "content": chunks[index - 1]}
