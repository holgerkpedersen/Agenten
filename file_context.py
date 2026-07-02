import config
import os
from typing import Any, Dict, List, Tuple, Optional
from lang import t
from i18n import K
from cache import _FILE_CONTENT_CACHE, _CONTENT_CACHE_MAX_SIZE, _is_file_cached, _cache_file_content, _get_cached_file_content, _clear_file_content_cache
from text_utils import CHUNK_SIZE

import re
from path_utils import is_safe_location

FOLDER_SCAN_MAX_FILES = config.FOLDER_SCAN_MAX_FILES

FOLDER_SCAN_MAX_DEPTH = config.FOLDER_SCAN_MAX_DEPTH



FOLDER_SCAN_EXCLUDE_DIRS = {'node_modules', '.git', 'venv', '.venv', '__pycache__', '.opencode', '.agent_storage'}

FOLDER_SCAN_EXCLUDE_FILES = {'.env'}

FOLDER_SCAN_EXTENSIONS = {'.py', '.js', '.json', '.html', '.css', '.yml', '.yaml', '.toml', '.md', '.txt', '.bat', '.cfg', '.ini', '.sh', '.jsx', '.ts', '.tsx', '.vue', '.svelte'}

def read_file_content(agent: Any, filepath: str) -> str | None:
    """read file content.

    Args:
        agent: The agent instance
        filepath: Path to the file to read
    """
    # Check cache first
    cached_content = _get_cached_file_content(filepath)
    if cached_content is not None:
        agent._log("DEBUG", f"Using cached content for {os.path.basename(filepath)}")
        return cached_content

    basename = os.path.basename(filepath)
    if basename in {'.env', '.secret', '.key', '.token'}:
        return None

    ext = os.path.splitext(filepath)[1].lower()
    if ext in {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.ico', '.zip', '.exe', '.dll', '.pdf', '.doc', '.docx'}:
        return None

    # Handle markdown and text files safely
    if ext in {'.md', '.markdown', '.txt', '.log', '.csv', '.json', '.yaml', '.yml'}:
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

    # For other file types, attempt to read as text
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
