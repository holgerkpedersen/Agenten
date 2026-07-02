import re
from typing import Any, Dict, List, Tuple, Optional
from ast_index import _indexed_dirs, _GLOBAL_SYMBOL_INDEX, _format_params, build_ast_index, _find_enclosing_symbol, _list_top_level_vars, _list_top_level_symbols, _scan_dir_into_index, _build_global_symbol_index, _ensure_workdir_indexed, list_symbols, locate_code
import os

def read_location(filepath: str, name: str | None = None, line_no: int | None = None) -> dict[str, Any]:
    """Read ONLY the function/class/method at a specific location via AST.
    Returns just the relevant code body, not the entire file.
    Use this instead of read_chunk when you need to see specific code.
    """
    # Handle LLM quirks: passing "None" as string instead of None
    if isinstance(name, str) and name.strip().lower() in ("none", "null", "undefined"):
        name = None
    # Handle LLM quirks: passing line_no as range string "1-50"
    if isinstance(line_no, str):
        line_no = line_no.strip()
        if "-" in line_no:
            line_no = int(line_no.split("-")[0].strip())
        else:
            try:
                line_no = int(line_no)
            except (ValueError, TypeError):
                line_no = None
    # Handle LLM quirks: passing line_no as list/tuple
    if isinstance(line_no, (list, tuple)) and len(line_no) > 0:
        line_no = int(line_no[0])
    result = locate_code(filepath=filepath, name=name, line_no=line_no)
    if not result.get("success"):
        return result

    content = result["body"]

    # Resolve translation keys found in the returned code.
    # LLM'en ser f.eks. t(K.TP_FRI, agent.lang) men ved ikke hvad K.TP_FRI
    # eller t() returnerer. Vi tilføjer de faktiske værdier som kommentarer.
    _t_pattern = re.compile(r't\(K\.(\w+)')
    t_keys = _t_pattern.findall(content)
    if t_keys:
        try:
            from i18n import K as _K
            from lang import t as _t
            resolved = []
            for key_name in sorted(set(t_keys)):
                key = getattr(_K, key_name, None)
                if key:
                    value = _t(key, 'da')
                    resolved.append(f"# {key_name} = \"{value[:200]}\"")
            if resolved:
                content += "\n\n## Oversættelser fundet i koden:\n" + "\n".join(resolved)
        except Exception:
            pass

    return {
        "success": True,
        "file": result["file"],
        "name": result["name"],
        "type": result["type"],
        "line": result["line"],
        "end_line": result["end_line"],
        "content": content,
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
        # Fallback: try reading the file directly from disk for non-Python files
        # not pre-loaded into file_chunks (e.g. .md, .json, .txt, .html)
        filepath = original
        if not os.path.isabs(filepath):
            workdir = getattr(agent, '_workdir', None) or os.getcwd()
            filepath = os.path.join(workdir, filepath)
        if os.path.isfile(filepath) and not filepath.endswith('.py'):
            try:
                with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
                    content = f.read()
                # Store in file_chunks for future reads
                agent.file_chunks[chunk] = [content]
                agent._log("READ", f"Læst fil direkte: {original}", f"{len(content)} tegn")
                return {"success": True, "chunk": chunk, "index": 1, "total": 1, "content": content}
            except (OSError, IOError) as e:
                return {"success": False, "error": f"Kunne ikke læse filen '{original}': {e}"}
        available = [k.replace("file_", "", 1) for k in agent.file_chunks.keys()] or ["ingen"]
        return {"success": False, "error": f"Ukendt chunk: '{original}'. Tilg\u00e6ngelige filer: {available}. Brug 'list_chunks' for at se alle."}
    if index < 1 or index > len(chunks):
        return {"success": False, "error": f"Chunk {index} findes ikke (1..{len(chunks)})"}
    agent._log("READ", f"L\u00e6st chunk {index}/{len(chunks)}: {original}", f"{len(chunks[index - 1])} tegn")
    return {"success": True, "chunk": chunk, "index": index, "total": len(chunks), "content": chunks[index - 1]}
