"""Session performance tracker — mål hvor effektive vores forbedringer er.

Kørsel:
    python scripts/session_stats.py [session_id] [--all]

Eksempler:
    python scripts/session_stats.py 61255108
    python scripts/session_stats.py --all --days 7
"""
import json
import os
import re
import sys
import glob
from datetime import datetime
from collections import defaultdict

_BASE = os.path.abspath(os.path.join(os.path.dirname(__file__ or "."), ".."))
SESSIONS_DIR = os.path.join(_BASE, "sessions")
LOG_FILE = os.path.join(_BASE, "logs", "agenten.log")


def _find_session_path(sid: str) -> str | None:
    """Find session file by ID. Supports partial ID matching."""
    # Try direct paths first
    _search_dirs = [SESSIONS_DIR]
    wd = os.environ.get("AGENT_WORKDIR", "")
    if wd:
        _search_dirs.append(os.path.join(wd, "sessions"))
    _search_dirs.extend([
        "C:\\Dev\\Agenten\\sessions",
        "C:\\Dev\\TestRefac\\sessions",
    ])
    
    # Direct match (full UUID)
    for _d in _search_dirs:
        if not os.path.isdir(_d):
            continue
        _full = os.path.join(_d, f"{sid}.json")
        if os.path.exists(_full):
            return _full
    
    # Partial match (search by prefix)
    for _d in _search_dirs:
        if not os.path.isdir(_d):
            continue
        for _f in os.listdir(_d):
            if _f.startswith(sid) and _f.endswith(".json"):
                return os.path.join(_d, _f)
    return None


def load_session(sid: str) -> dict | None:
    path = _find_session_path(sid)
    if not path:
        return None
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return json.load(f)


def analyze_session(sid: str) -> dict:
    data = load_session(sid)
    if not data:
        return {"error": f"Session {sid} not found"}

    template = data.get("template", "?")
    tree = data.get("tree", {})
    phases = []
    if tree and tree.get("children"):
        for c in tree["children"]:
            phases.append({
                "name": c.get("name", "?"),
                "status": c.get("status", "?"),
            })

    llm_todos = data.get("llm_todos", [])
    llm_done = sum(1 for t in (llm_todos or []) if t.get("done"))
    llm_total = len(llm_todos or [])

    log_entries = data.get("agent_log", [])
    log_count = len(log_entries)
    first_ts = log_entries[0].get("timestamp", 0) if log_entries else 0
    last_ts = log_entries[-1].get("timestamp", 0) if log_entries else 0
    duration = last_ts - first_ts if first_ts and last_ts else 0

    mod_time = data.get("last_modified", "")
    created = data.get("created", "")

    return {
        "id": sid[:20],
        "template": template,
        "created": created,
        "modified": mod_time,
        "duration_sec": duration,
        "duration_str": f"{duration:.0f}s" if duration < 120 else f"{duration/60:.1f}m",
        "phases": phases,
        "phases_done": sum(1 for p in phases if p["status"] == "done"),
        "phases_total": len(phases),
        "llm_todos": f"{llm_done}/{llm_total}",
        "log_entries": log_count,
        "success": all(p["status"] == "done" for p in phases) if phases else False,
    }


def analyze_log_for_session(sid: str) -> dict:
    """Parse agenten.log for timing and retry info."""
    if not os.path.exists(LOG_FILE):
        return {"error": "agenten.log not found"}
    
    result = defaultdict(list)
    current_phase = None
    iteration_times = []
    
    with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            if sid not in line:
                continue
            
            # Match timestamps
            m = re.match(r"(\d{2}:\d{2}:\d{2})", line)
            if not m:
                continue
            ts = m.group(1)
            
            # Phase start
            m2 = re.search(r"Påbegynder opgave: (\w+)", line)
            if m2:
                current_phase = m2.group(1)
                iteration_times = []
            
            # Iteration count
            m3 = re.search(r"\[(\w+) #(\d+)\]", line)
            if m3 and current_phase:
                iteration_times.append(int(m3.group(2)))
                result[f"{current_phase}_max_iter"].append(max(iteration_times))
            
            # Failure
            m4 = re.search(r"Kunne ikke fuldføre.*?: (\w+)", line)
            if m4:
                result[f"{m4.group(1)}_failures"].append(ts)
            
            # Retry
            m5 = re.search(r"Genforsøg (\d+)/\d+ for (\w+)", line)
            if m5:
                result[f"{m5.group(2)}_retries"] = int(m5.group(1))
    
    return dict(result)


def print_report(sid: str):
    s = analyze_session(sid)
    if "error" in s:
        print(f"!! {s['error']}")
        return
    
    print(f"\n{'='*50}")
    print(f"  Session {s['id']}")
    print(f"{'='*50}")
    print(f"  Template:   {s['template']}")
    print(f"  Created:    {s['created'][:19]}")
    print(f"  Modified:   {s['modified'][:19]}")
    print(f"  Duration:   {s['duration_str']}")
    print(f"  Log items:  {s['log_entries']}")
    
    print(f"\n  Phases:")
    for p in s['phases']:
        icon = "[OK]" if p['status'] == 'done' else "[..]" if p['status'] == 'running' else "[!!]"
        print(f"    {icon} {p['name']:30s} [{p['status']}]")
    
    print(f"\n  Done: {s['phases_done']}/{s['phases_total']} phases")
    print(f"  LLM todos: {s['llm_todos']} done")
    
    log_stats = analyze_log_for_session(sid)
    if log_stats:
        print(f"\n  Iterations:")
        for key, values in sorted(log_stats.items()):
            if values and not isinstance(values, list):
                print(f"    {key}: {values}")
            elif values:
                print(f"    {key}: max={max(values)}, calls={len(values)}")
    
    overall = "ALL PHASES DONE" if s['success'] else "NOT ALL PHASES DONE"
    print(f"\n  {overall}")
    print(f"{'='*50}\n")


def list_recent_sessions(count: int = 10):
    """Show recent sessions (simplified)."""
    print("Use: python scripts/session_stats.py <session_id_prefix>")
    print("Ex:  python scripts/session_stats.py 61255108")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] != "--help":
        print_report(sys.argv[1])
    else:
        list_recent_sessions()
    elif len(sys.argv) > 1:
        print_report(sys.argv[1])
    else:
        print(__doc__)
