from __future__ import annotations
import re
import os
import json
from typing import Any
from agent_helpers import _extract_filenames



def _auto_load_issue_files(agent: Agent, prompt: str, template: str | None, files: list[dict[str, Any]]) -> None:
    """Automatically load issue-related files when a bug/issue ID is present in the prompt."""
    if template not in ("bugfix", "issue_handler") or files:
        return
    issue_match = re.search(r'(BUG-\d+|SEC-\d+|TST-\d+|ARC-\d+|PRF-\d+|MNT-\d+|REFAC-\d+)', prompt)
    if not issue_match:
        return
    issue_id = issue_match.group(1)
    try:
        base_dir = os.path.dirname(os.path.abspath(__file__))
        issues_path = os.path.join(base_dir, "docs", "issues", "observed", "issues.json")
        if not os.path.exists(issues_path):
            return
        with open(issues_path, encoding="utf-8") as f:
            issues_data = json.load(f)
        for issue in issues_data.get("issues", []):
            if issue.get("id", "").lower() != issue_id.lower():
                continue
            location = issue.get("location", "")
            filenames = _extract_filenames(location)
            for filename in filenames:
                if not filename.endswith('.py'):
                    continue
                workdir = os.environ.get('AGENT_WORKDIR', '')
                probe_paths = [filename, os.path.join(base_dir, filename), os.path.join(os.getcwd(), filename)]
                if workdir:
                    probe_paths.append(os.path.join(workdir, filename))
                for path in probe_paths:
                    if os.path.exists(path):
                        content = agent._read_file_content(path)
                        if content:
                            files.append({"filename": filename, "content": content, "path": path})
                            agent._log("INFO", f"Auto-loaded fil fra {issue_id}", path)
                        break
    except Exception as e:
        agent._log("WARNING", "Kunne ikke auto-loade issue-fil", str(e))

import agent_files



def _auto_load_location_file(agent: Agent, prompt: str) -> None:
    """Load file(s) from a Location: field in the prompt."""
    if agent.file_chunks:
        return
    location_match = re.search(r'Location:\s*([^\n]+)', prompt, re.IGNORECASE)
    if not location_match:
        return
    location = location_match.group(1).strip()
    filenames = _extract_filenames(location)
    base_dir = os.path.dirname(os.path.abspath(__file__))
    for filename in filenames:
        if not filename.endswith('.py'):
            continue
        workdir = os.environ.get('AGENT_WORKDIR', '')
        probe_paths = [filename, os.path.join(base_dir, filename), os.path.join(os.getcwd(), filename)]
        if workdir:
            probe_paths.append(os.path.join(workdir, filename))
        for path in probe_paths:
            if os.path.exists(path):
                try:
                    content = agent._read_file_content(path)
                    if content:
                        chunk_key = f"file_{os.path.basename(filename)}"
                        agent.file_chunks[chunk_key] = agent_files.chunk_text(content)
                        agent._log("INFO", f"Auto-loaded location-fil", f"{filename} ({len(agent.file_chunks[chunk_key])} chunks)")
                except Exception as e:
                    agent._log("WARNING", f"Kunne ikke auto-loade {filename}", str(e))
                return



def _validate_prompt_against_code(agent: Agent, prompt: str) -> str:
    """Extract symbol names from prompt, check against global symbol index, log findings.
    
    Returns a context note to inject into the decomposition prompt.
    """
    import agent_files as _af
    extracted = set()
    # Backtick patterns: `function_name` or `Class.method`
    for m in re.finditer(r'`([a-zA-Z_]\w*(?:\.[a-zA-Z_]\w*)?)`', prompt):
        extracted.add(m.group(1))
    # function_name( or Class.method( patterns
    for m in re.finditer(r'(?<![a-zA-Z._])([a-z_][a-zA-Z_]\w*(?:\.[a-zA-Z_]\w*)?)\s*\(', prompt):
        extracted.add(m.group(1))
    # Location: file.py:symbol — extract the :symbol part
    loc_match = re.search(r'Location:\s*[\w./\\-]+\.\w+\s*:\s*([a-zA-Z_]\w*)', prompt)
    if loc_match:
        extracted.add(loc_match.group(1))
    # Issue ID references in prompt — extract keywords after "for" or "af"
    # (e.g., "missing implementation of validate_done_completion")
    for m in re.finditer(r'\b(?:implementer|ret|fix|løs|lav|brug|kald|kør)\s+([a-z_]\w+)', prompt, re.IGNORECASE):
        extracted.add(m.group(1))

    index = _af._GLOBAL_SYMBOL_INDEX if hasattr(_af, '_GLOBAL_SYMBOL_INDEX') else {}
    found = []
    for sym in sorted(extracted):
        matches = index.get(sym, [])
        if matches:
            locations = []
            for m in matches[:3]:
                if isinstance(m, tuple):
                    fp, ln = m[0], m[1]
                else:
                    fp, ln = str(m), '?'
                locations.append(f"{fp}:{ln}")
            found.append((sym, locations))

    # Also check for Location: file.py (without :symbol) — list file's symbols for context
    loc_file_match = re.search(r'Location:\s*([\w./\\-]+\.\w+)', prompt, re.IGNORECASE)
    loc_file_symbols = []
    if loc_file_match and not loc_match:
        loc_fn = os.path.basename(loc_file_match.group(1))
        if loc_fn.endswith('.py'):
            for sym_name, sym_matches in index.items():
                for sm in sym_matches:
                    if isinstance(sm, tuple) and sm[0] == loc_fn:
                        loc_file_symbols.append((sym_name, sm[1]))
                        break

    log_detail_lines = [f"Scanner prompt for symbol-match: {len(extracted)} kandidater"]
    for sym in sorted(extracted):
        log_detail_lines.append(f"  Symbol: {sym} — {'FUNDET' if sym in index else 'ikke fundet'}")
    if found:
        log_detail_lines.append("")
        for sym, locs in found:
            log_detail_lines.append(f"  ✓ {sym} på {', '.join(locs[:3])}")
    if loc_file_symbols:
        log_detail_lines.append(f"\n  Location-fil '{loc_fn}' har {len(loc_file_symbols)} symboler i indekset")
        for sym_name, ln in sorted(loc_file_symbols):
            log_detail_lines.append(f"    {sym_name} [{ln}]")
    agent._log("VALIDERING", f"Prompt-validering: {len(found)}/{len(extracted)} symboler matcher kode",
               "\n".join(log_detail_lines))

    if not found:
        return ""

    note = "\n\n## ⚠️ Prompt-validering\n"
    note += "Følgende symboler fra prompten findes ALLEREDE i koden:\n"
    for sym, locs in found:
        note += f"- `{sym}` på {', '.join(locs[:3])}\n"
    note += "\nOvervej om din opgave allerede er løst — du kan evt. nøjes med en 'Verificér'-fase.\n"
    return note

def _load_location_from_prompt(agent, prompt):
    """Load location file referenced in prompt if present."""
    import re
    match = re.search(r'location:\s*(.+)', prompt, re.IGNORECASE)
    if not match:
        return None
    filepath = match.group(1).strip()
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
        agent._log("INFO", f"Loaded location file: {filepath}", len(content))
        return content
    except FileNotFoundError:
        agent._log("WARN", f"Location file not found: {filepath}", "")
        return None
