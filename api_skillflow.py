import json
import os
from typing import Any
from flask import jsonify, request
from config import BASE_DIR, _is_development_mode

def skillflow_report() -> Any:
    """skillflow report."""
    if not _is_development_mode():
        api_key = request.headers.get('X-API-Key') or request.args.get('api_key')
        expected_key = os.environ.get('AGENT_API_KEY', '')
        if expected_key and api_key != expected_key:
            return jsonify({"success": False, "error": "Unauthorized"}), 401
    import json as _json
    outcomes_path = os.path.join(BASE_DIR, ".agent_storage", "skill_outcomes.json")
    evolution_path = os.path.join(BASE_DIR, ".agent_storage", "evolution_actions.json")

    outcomes = []
    if os.path.exists(outcomes_path):
        with open(outcomes_path, encoding="utf-8") as f:
            outcomes = _json.load(f)

    evolution = {}
    if os.path.exists(evolution_path):
        with open(evolution_path, encoding="utf-8") as f:
            evolution = _json.load(f)

    # Build stats
    from collections import Counter
    skills_c = Counter()
    success_c = Counter()
    templates_c = Counter()
    for o in outcomes:
        s = o.get("skill", "?")
        skills_c[s] += 1
        if o.get("success"):
            success_c[s] += 1
        t = o.get("template", "")
        if t:
            templates_c[t] += 1

    md = f"""# 🧬 SkillFlow Analysis

**Total outcomes:** {len(outcomes)}
**Last analysis:** {evolution.get('analyzed_at', 'never')}

## Per-Skill Statistics

| Skill | Success | Total | Rate |
|-------|---------|-------|------|
"""
    for skill, count in skills_c.most_common():
        s = success_c.get(skill, 0)
        rate = 100 * s / count if count else 0
        md += f"| {skill} | {s} | {count} | {rate:.0f}% |\n"

    if templates_c:
        md += "\n## Template Usage\n\n| Template | Outcomes |\n|----------|----------|\n"
        for t, c in templates_c.most_common():
            md += f"| {t} | {c} |\n"

    actions = evolution.get("actions", [])
    if actions:
        md += f"\n## Evolution Actions ({len(actions)})\n\n"
        for a in actions:
            act = a.get("action", "?")
            skill = a.get("skill", "?")
            reason = a.get("reason", "")
            emoji = {"retain": "✅", "refine": "🔧", "prune": "🗑️", "generate": "🆕"}.get(act, "❓")
            md += f"### {emoji} {act.upper()}: `{skill}`\n\n"
            md += f"**Reason:** {reason}\n\n"
            if a.get("success_rate") is not None:
                md += f"- Success rate: {a['success_rate']:.0%}\n"
            if a.get("frequency"):
                md += f"- Frequency: {a['frequency']}× repeated\n"
            if a.get("example_task"):
                md += f"- Example task: *{a['example_task']}*\n"
            if a.get("suggested_action_types"):
                md += f"- Suggested types: {', '.join(a['suggested_action_types'])}\n"
            md += "\n"

    if not outcomes:
        md += "\n*No outcomes recorded yet. Run some tasks to accumulate data.*\n"

    # Applied changes log
    log_path = os.path.join(BASE_DIR, ".agent_storage", "evolution_log.json")
    if os.path.exists(log_path):
        with open(log_path, encoding="utf-8") as f:
            log_entries = _json.load(f)
        if log_entries:
            md += f"\n## Applied Changes ({len(log_entries)} entries)\n\n"
            for entry in log_entries[-10:]:
                md += f"### {entry['timestamp']}\n\n"
                md += "| Action | Skill | Details |\n|--------|-------|----------|\n"
                for act in entry.get("actions", []):
                    if isinstance(act, dict):
                        act_action = act.get('action', '?')
                        act_skill = f"`{act.get('skill','?')}`"
                        act_result = act.get('message', act.get('result', ''))[:120]
                    else:
                        act_action = str(act)
                        act_skill = "—"
                        act_result = ""
                    md += f"| {act_action} | {act_skill} | {act_result} |\n"
                md += "\n"

    md += "\n\n[Apply pending actions](/api/skillflow/apply)"

    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><title>SkillFlow Analysis</title>
<script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
<style>
    body {{ font-family: 'Segoe UI', system-ui; max-width: 1000px; margin: 40px auto; padding: 20px; background: #0f172a; color: #e2e8f0; }}
    h1 {{ border-bottom: 2px solid #334155; padding-bottom: 10px; }}
    h2 {{ border-bottom: 1px solid #334155; padding-bottom: 6px; margin-top: 28px; }}
    h3 {{ color: #93c5fd; margin-top: 20px; }}
    code {{ background: #1e293b; padding: 2px 6px; border-radius: 4px; }}
    pre {{ background: #1e293b; padding: 14px; border-radius: 8px; overflow-x: auto; }}
    table {{ border-collapse: collapse; width: 100%; margin: 12px 0; }}
    th, td {{ border: 1px solid #334155; padding: 8px 12px; text-align: left; }}
    th {{ background: #1e293b; }}
    a {{ color: #60a5fa; }}
</style></head>
<body><div id="content"></div>
<script>document.getElementById('content').innerHTML = marked.parse({json.dumps(md)});</script>
</body></html>"""

def skillflow_apply() -> Any:
    """skillflow apply."""
    from skill_evolution import analyze, apply_evolution_actions, _log_applied
    analysis = analyze()
    if analysis.get("status") != "ok":
        return jsonify({"success": False, "error": analysis})
    results = apply_evolution_actions(analysis["actions"], dry_run=False)
    if results:
        _log_applied(results)
    return jsonify({"success": True, "status": "applied", "actions": len(results), "results": results})

def skillflow_status() -> Any:
    """skillflow status."""
    import json as _json
    outcomes_path = os.path.join(BASE_DIR, ".agent_storage", "skill_outcomes.json")
    evolution_path = os.path.join(BASE_DIR, ".agent_storage", "evolution_actions.json")
    data = {"outcomes": [], "evolution": {}}
    if os.path.exists(outcomes_path):
        with open(outcomes_path, encoding="utf-8") as f:
            data["outcomes"] = _json.load(f)
    if os.path.exists(evolution_path):
        with open(evolution_path, encoding="utf-8") as f:
            data["evolution"] = _json.load(f)
    return jsonify({"success": True, "data": data})
