"""Agent helper utilities."""
from __future__ import annotations

import os
import re
import json
import subprocess
import sys
from typing import Any

from config import get_logger

log = get_logger(__name__)


_LOOKUP_CACHE: dict[str, str | None] = {}


def _resolve_t_keys_in_result(result: dict) -> dict:
    """Resolve t(K.XXX) translations in locate/read_location results."""
    if result.get("success") and result.get("body"):
        body = result["body"]
        _t_pattern = re.compile(r't\(K\.(\w+)')
        t_keys = _t_pattern.findall(body)
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
                    result["body"] = body + "\n\n## Oversættelser:\n" + "\n".join(resolved)
            except Exception:
                pass
    return result


def _safe_int(val: Any, default: int = 0) -> int:
    """Convert a value to an integer safely, returning default on failure."""
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


def _extract_filenames(location: str) -> list[str]:
    """Extract file paths from a location string using regex."""
    filenames = []
    if not location:
        return filenames
    for m in re.finditer(r'([\w./\\-]+\.\w+)', location):
        fn = m.group(1)
        if fn not in filenames:
            filenames.append(fn)
    return filenames


def _auto_load_issue_files(agent: Any, prompt: str, template: str | None, files: list[dict[str, Any]]) -> None:
    """Automatically load issue-related files when a bug/issue ID is present in the prompt."""
    import agent_issues
    import agent_files
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


def _auto_load_location_file(agent: Any, prompt: str) -> None:
    """Load file(s) from a Location: field in the prompt."""
    import agent_files
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


def _validate_prompt_against_code(agent: Any, prompt: str) -> str:
    """Extract symbol names from prompt, check against global symbol index, log findings."""
    import agent_files as _af
    extracted = set()
    for m in re.finditer(r'`([a-zA-Z_]\w*(?:\.[a-zA-Z_]\w*)?)`', prompt):
        extracted.add(m.group(1))
    for m in re.finditer(r'(?<![a-zA-Z._])([a-z_][a-zA-Z_]\w*(?:\.[a-zA-Z_]\w*)?)\s*\(', prompt):
        extracted.add(m.group(1))
    loc_match = re.search(r'Location:\s*[\w./\\-]+\.\w+\s*:\s*([a-zA-Z_]\w*)', prompt)
    if loc_match:
        extracted.add(loc_match.group(1))
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
        log_detail_lines.append(f" Symbol: {sym} — {'FUNDET' if sym in index else 'ikke fundet'}")
    if found:
        log_detail_lines.append("")
        for sym, locs in found:
            log_detail_lines.append(f" ✓ {sym} på {', '.join(locs[:3])}")
    if loc_file_symbols:
        log_detail_lines.append(f"\n Location-fil '{loc_fn}' har {len(loc_file_symbols)} symboler i indekset")
        for sym_name, ln in sorted(loc_file_symbols):
            log_detail_lines.append(f" {sym_name} [{ln}]")
    agent._log("VALIDERING", f"Prompt-validering: {len(found)}/{len(extracted)} symboler matcher kode", "\n".join(log_detail_lines))
    if not found:
        return ""
    note = "\n\n## ⚠️ Prompt-validering\n"
    note += "Følgende symboler fra prompten findes ALLEREDE i koden:\n"
    for sym, locs in found:
        note += f"- `{sym}` på {', '.join(locs[:3])}\n"
    note += "\nOvervej om din opgave allerede er løst — du kan evt. nøjes med en 'Verificér'-fase.\n"
    return note


def _run_doc_refinement(workdir: str, rounds: int = 7, model: str = "") -> dict[str, Any]:
    """Kør iterativ doc-refinement via scripts/run_doc_refinement.py."""
    if not workdir:
        return {"success": False, "error": "workdir mangler"}
    project_root = os.path.dirname(os.path.abspath(__file__))
    script_path = os.path.join(project_root, "scripts", "run_doc_refinement.py")
    if not os.path.isfile(script_path):
        return {"success": False, "error": f"Script ikke fundet: {script_path}"}
    rounds = max(1, int(rounds))
    per_call_timeout = 900
    total_timeout = int(per_call_timeout * rounds * 1.5)
    cmd = [
        sys.executable, script_path, "--workdir", workdir,
        "--rounds", str(rounds), "--timeout", str(per_call_timeout),
    ]
    if model:
        cmd.extend(["--model", model])
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=total_timeout)
        output = (result.stdout or "") + (result.stderr or "")
        dialog_path = os.path.join(workdir, "docs", "uddybning_dialog.md")
        return {
            "success": result.returncode == 0,
            "exit_code": result.returncode,
            "rounds_run": rounds,
            "per_call_timeout": per_call_timeout,
            "total_timeout": total_timeout,
            "output": output[-2000:],
            "dialog_path": dialog_path if os.path.isfile(dialog_path) else None,
        }
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "error": f"Refinement script timeout (>{total_timeout}s = {total_timeout // 60} min for {rounds} rounds)",
            "rounds_run": rounds,
            "per_call_timeout": per_call_timeout,
            "total_timeout": total_timeout,
        }
    except Exception as e:
        return {"success": False, "error": f"Refinement script fejl: {e}"}
