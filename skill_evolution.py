"""
SkillFlow — Evolution Engine
Analyses outcomes and suggests Retain / Refine / Prune / Generate actions.
"""

import os
import json
import re
from datetime import datetime
from collections import defaultdict, Counter

from skill_tracker import tracker


EVOLUTION_FILE = ".agent_storage/skill_evolution_log.json"
ACTIONS_LOG = ".agent_storage/evolution_actions.json"

# Thresholds
RETAIN_MIN_RATE = 0.80
REFINE_MIN_RATE = 0.50
PRUNE_MAX_RATE = 0.50
PRUNE_MIN_COUNT = 5
EVOLVE_EVERY_N = 15
GENERATE_MIN_REPEAT = 2


def _load_json(path, default):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return default
    return default


def _save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def _deduce_action_type(task: str) -> list:
    task_lower = task.lower()
    types = []
    if any(w in task_lower for w in ["read", "læs", "hent", "fetch", "get", "vis"]):
        types.append("read")
    if any(w in task_lower for w in ["write", "skriv", "create", "opret", "edit",
                                       "ret", "tilføj", "add", "update", "opdater"]):
        types.append("write")
    if any(w in task_lower for w in ["git", "commit", "push", "branch", "merge",
                                       "pull", "checkout"]):
        types.append("git")
    if any(w in task_lower for w in ["github", "pr", "issue", "pull request",
                                       "repository", "repo"]):
        types.append("github")
    if any(w in task_lower for w in ["search", "søg", "find", "led", "lookup",
                                       "slå op"]):
        types.append("search")
    if any(w in task_lower for w in ["analyze", "analyser", "review", "gennemgå",
                                       "kodegennemgang", "refactor", "omstrukturer"]):
        types.append("analyze")
    return types if types else ["general"]


def analyze():
    """
    Analyse all tracked outcomes and produce a list of evolution actions.
    Returns a dict with skill-level recommendations and potential new skill gaps.
    """
    outcomes = tracker.get_outcomes()
    total = len(outcomes)
    if total < 5:
        return {"status": "not_enough_data", "total": total, "actions": []}

    stats = tracker.get_all_skill_stats(recent=100)
    actions = []
    evolved = set()

    for skill_name, s in stats.items():
        if not skill_name or skill_name == "__none__":
            continue
        count = s["count"]
        rate = s["success_rate"]

        if rate >= RETAIN_MIN_RATE:
            actions.append({
                "action": "retain",
                "skill": skill_name,
                "reason": f"Success rate {rate:.0%} >= {RETAIN_MIN_RATE:.0%}",
                "success_rate": round(rate, 3),
                "count": count,
            })
            evolved.add(skill_name)

        elif rate >= REFINE_MIN_RATE:
            recent_failures = [
                o for o in tracker.get_outcomes(skill_name, 20)
                if not o.get("success")
            ]
            common_patterns = Counter(
                o.get("detail", "")[:80] for o in recent_failures
            ).most_common(3)
            actions.append({
                "action": "refine",
                "skill": skill_name,
                "reason": f"Success rate {rate:.0%} — needs improvement",
                "success_rate": round(rate, 3),
                "count": count,
                "failure_patterns": [p for p, _ in common_patterns],
            })
            evolved.add(skill_name)

        elif rate < PRUNE_MAX_RATE and count >= PRUNE_MIN_COUNT:
            actions.append({
                "action": "prune",
                "skill": skill_name,
                "reason": f"Success rate {rate:.0%} < {PRUNE_MAX_RATE:.0%} with {count} uses",
                "success_rate": round(rate, 3),
                "count": count,
            })
            evolved.add(skill_name)

    # Generate: detect repeated tasks that had no skill match
    unmatched = tracker.get_unmatched_tasks(50)
    task_counter = Counter(unmatched)
    for task_text, frequency in task_counter.most_common(5):
        if frequency >= GENERATE_MIN_REPEAT and frequency >= 1:
            suggested_name = _suggest_skill_name(task_text)
            action_types = _deduce_action_type(task_text)
            actions.append({
                "action": "generate",
                "skill": suggested_name,
                "reason": f"Task repeated {frequency}x with no matching skill",
                "frequency": frequency,
                "example_task": task_text[:150],
                "suggested_action_types": action_types,
            })

    _save_json(ACTIONS_LOG, {
        "analyzed_at": datetime.now().isoformat(),
        "total_outcomes": total,
        "actions": actions,
    })

    return {
        "status": "ok",
        "total": total,
        "skills_analyzed": len(stats),
        "actions": actions,
    }


def _suggest_skill_name(task: str) -> str:
    words = re.findall(r"[A-Za-z0-9]+", task)
    keywords = [w for w in words if len(w) > 3 and w.lower() not in
                {"the", "this", "that", "with", "from", "what", "which",
                 "hvad", "med", "fra", "den", "det", "til", "kan", "jeg",
                 "vil", "skal", "har", "ver", "ich", "und", "der", "die",
                 "das", "ist", "nicht"}]
    if not keywords:
        return "auto_generated_skill"
    base = "_".join(keywords[:3]).lower()
    return base[:60]


def _load_skill_file(name: str):
    skills_dir = "skills"
    for root, _dirs, files in os.walk(skills_dir):
        for f in files:
            if f.endswith(".md") and f.replace(".md", "").lower() == name.lower():
                path = os.path.join(root, f)
                with open(path, "r", encoding="utf-8") as fh:
                    return path, fh.read()
    return None, None


def apply_evolution_actions(actions: list, dry_run: bool = True) -> list:
    """
    Apply Retain/Refine/Prune/Generate actions to skill files.
    In dry_run mode, only return what would be done without touching files.
    """
    results = []
    log = _load_json(EVOLUTION_FILE, [])

    for action in actions:
        act = action["action"]
        skill_name = action["skill"]
        result = {"action": act, "skill": skill_name, "dry_run": dry_run}

        if act == "retain":
            result["message"] = f"Kept '{skill_name}' unchanged"
            if not dry_run:
                log.append({
                    "timestamp": datetime.now().isoformat(),
                    "action": "retain",
                    "skill": skill_name,
                })

        elif act == "refine":
            path, content = _load_skill_file(skill_name)
            if path and content:
                if not dry_run:
                    improved = _add_refinement_note(content, action)
                    with open(path, "w", encoding="utf-8") as f:
                        f.write(improved)
                    log.append({
                        "timestamp": datetime.now().isoformat(),
                        "action": "refine",
                        "skill": skill_name,
                        "failure_patterns": action.get("failure_patterns", []),
                    })
                result["message"] = f"Refined '{skill_name}' with failure analysis"
            else:
                result["message"] = f"Cannot refine '{skill_name}' — file not found"

        elif act == "prune":
            path, _content = _load_skill_file(skill_name)
            if path and not dry_run:
                backup = path + ".pruned"
                os.rename(path, backup)
                log.append({
                    "timestamp": datetime.now().isoformat(),
                    "action": "prune",
                    "skill": skill_name,
                    "backup": backup,
                })
                result["message"] = f"Pruned '{skill_name}' (backup: {backup})"
            elif path:
                result["message"] = f"Would prune '{skill_name}'"
            else:
                result["message"] = f"Cannot prune '{skill_name}' — file not found"

        elif act == "generate":
            if not dry_run:
                suggested_name = skill_name
                action_types = action.get("suggested_action_types", ["general"])
                example = action.get("example_task", "")
                frontmatter = (
                    f"---\n"
                    f"name: {suggested_name}\n"
                    f"keywords: [{', '.join(suggested_name.split('_')[:3])}]\n"
                    f"action_types: [{', '.join(action_types)}]\n"
                    f"description: Auto-generated from SkillFlow — {example[:80]}\n"
                    f"min_score: 1\n"
                    f"---\n"
                    f"\n"
                    f"## {suggested_name.replace('_', ' ').title()}\n"
                    f"\n"
                    f"Auto-generated skill based on repeated unmatched tasks.\n"
                    f"\n"
                    f"**Example task:** {example}\n"
                    f"\n"
                    f"### Instructions\n"
                    f"\n"
                    f"_(Add instructions here based on observed patterns)_\n"
                )
                gen_path = os.path.join("skills", f"{suggested_name}.md")
                with open(gen_path, "w", encoding="utf-8") as f:
                    f.write(frontmatter)
                log.append({
                    "timestamp": datetime.now().isoformat(),
                    "action": "generate",
                    "skill": suggested_name,
                    "path": gen_path,
                    "example_task": example[:200],
                })
                result["message"] = f"Generated new skill '{suggested_name}' at {gen_path}"
            else:
                result["message"] = (
                    f"Would generate skill '{skill_name}' "
                    f"(action_types: {action.get('suggested_action_types', ['general'])})"
                )

        results.append(result)

    if not dry_run:
        _save_json(EVOLUTION_FILE, log)

    return results


def _add_refinement_note(content: str, action: dict) -> str:
    patterns = action.get("failure_patterns", [])
    note = f"\n<!-- SkillFlow Refinement: {datetime.now().strftime('%Y-%m-%d')} -->\n"
    note += "<!-- Failure patterns to address:\n"
    for p in patterns:
        note += f"     - {p}\n"
    note += "-->\n"
    # Insert before last blank line or at end
    lines = content.rstrip().split("\n")
    for i in range(len(lines) - 1, -1, -1):
        if lines[i].strip():
            lines.insert(i + 1, note)
            break
    else:
        lines.append(note)
    return "\n".join(lines)


def should_evolve() -> bool:
    total = tracker.total_outcomes
    if total == 0:
        return False
    return total % EVOLVE_EVERY_N == 0


def evolve_if_needed(dry_run: bool = True) -> dict:
    if not should_evolve():
        return {"status": "skipped", "reason": "not_yet", "total": tracker.total_outcomes}
    analysis = analyze()
    if analysis.get("status") != "ok":
        return analysis
    results = apply_evolution_actions(analysis["actions"], dry_run=dry_run)
    if not dry_run and results:
        _log_applied(results)
    return {
        "status": "evolved",
        "analysis": analysis,
        "results": results,
    }


def _log_applied(results: list):
    import json, os
    from datetime import datetime
    log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".agent_storage", "evolution_log.json")
    try:
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        entries = []
        if os.path.exists(log_path):
            with open(log_path, encoding="utf-8") as f:
                entries = json.load(f)
        entries.append({
            "timestamp": datetime.now().isoformat(),
            "actions": results
        })
        with open(log_path, "w", encoding="utf-8") as f:
            json.dump(entries, f, indent=2, ensure_ascii=False)
    except Exception:
        pass
