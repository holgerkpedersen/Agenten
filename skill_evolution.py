"""
SkillFlow — Evolution Engine
Analyses outcomes and suggests Retain / Refine / Prune / Generate actions.
"""

import os
import json
import re
from datetime import datetime
from collections import defaultdict, Counter
from typing import Any

from skill_tracker import tracker
from config import get_logger
log = get_logger(__name__)


EVOLUTION_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".agent_storage", "skill_evolution_log.json")
ACTIONS_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".agent_storage", "evolution_actions.json")

# Thresholds
RETAIN_MIN_RATE = 0.80
REFINE_MIN_RATE = 0.50
PRUNE_MAX_RATE = 0.50
PRUNE_MIN_COUNT = 10
EVOLVE_EVERY_N = 10
GENERATE_MIN_REPEAT = 8
GENERATE_MIN_TASK_LENGTH = 10

SIMILARITY_THRESHOLD = 0.35


def _load_json(path: str, default: Any) -> Any:
    """Load JSON file or return default value."""
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return default
    return default
    return default


def _save_json(path: str, data: Any) -> None:
    """save json.
    
    Args:
        path:
        data:"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def _deduce_action_type(task: str) -> list[str]:
    """deduce action type.
    
    Args:
        task (str):
    
    Returns:
        list"""
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


STOPWORDS = {
    "the", "this", "that", "with", "from", "what", "which", "and", "for",
    "not", "are", "was", "had", "has", "but", "can", "all", "will", "its",
    "also", "than", "then", "each", "could", "would", "should", "about",
    "into", "over", "such", "only", "other", "more", "very", "just",
    "hvad", "med", "fra", "den", "det", "til", "kan", "jeg",
    "vil", "skal", "har", "ver", "ich", "und", "der", "die",
    "das", "ist", "nicht", "eine", "auch", "mit", "auf", "aus",
}


def _normalize_task(text: str) -> set[str]:
    """normalize task.
    
    Args:
        text (str):
    
    Returns:
        set"""
    words = re.findall(r"[a-z0-9]+", text.lower())
    return {w for w in words if len(w) > 2 and w not in STOPWORDS}


def _task_similarity(t1: str, t2: str) -> float:
    """task similarity.
    
    Args:
        t1 (str):
        t2 (str):
    
    Returns:
        float"""
    a = _normalize_task(t1)
    b = _normalize_task(t2)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _cluster_unmatched(outcomes: list[dict[str, Any]]) -> list[list[str]]:
    """cluster unmatched.
    
    Args:
        outcomes (list):
    
    Returns:
        list"""
    clusters = []
    for o in outcomes:
        task = o.get("task", "")
        if not task or len(task.strip()) < GENERATE_MIN_TASK_LENGTH:
            continue
        best_idx = None
        best_score = 0
        for i, c in enumerate(clusters):
            rep = c[0]
            score = _task_similarity(task, rep)
            if score > best_score:
                best_score = score
                best_idx = i
        if best_idx is not None and best_score >= SIMILARITY_THRESHOLD:
            clusters[best_idx].append(task)
        else:
            clusters.append([task])
    return [c for c in clusters if len(c) >= GENERATE_MIN_REPEAT]


def _extract_cluster_name(tasks: list[str]) -> str:
    """extract cluster name.
    
    Args:
        tasks (list):
    
    Returns:
        str"""
    counter = Counter()
    for t in tasks:
        counter.update(_normalize_task(t))
    top = [w for w, _ in counter.most_common(3) if len(w) > 2][:3]
    if not top:
        return "auto_generated_skill"
    return "_".join(top).lower()[:60]


def _extract_cluster_keywords(tasks: list[str]) -> list[str]:
    """extract cluster keywords.
    
    Args:
        tasks (list):
    
    Returns:
        list"""
    counter = Counter()
    for t in tasks:
        counter.update(_normalize_task(t))
    return [w for w, _ in counter.most_common(8) if len(w) > 2][:8]


def _aggregate_action_types(tasks: list[str]) -> list[str]:
    """aggregate action types.
    
    Args:
        tasks (list):
    
    Returns:
        list"""
    merged = set()
    for t in tasks:
        merged.update(_deduce_action_type(t))
    return sorted(merged) if merged else ["general"]


def _generate_instructions(tasks: list[str], action_types: list[str]) -> str:
    """generate instructions.
    
    Args:
        tasks (list):
        action_types (list):
    
    Returns:
        str"""
    action_type = action_types[0] if action_types else "general"
    merged_words = Counter()
    for t in tasks:
        merged_words.update(_normalize_task(t))
    top_words = [w for w, _ in merged_words.most_common(10) if len(w) > 3][:5]
    domain = ", ".join(top_words) if top_words else "the task"

    steps = []
    steps.append("1. **Analyze the request**: Identify the specific files, components, or areas involved. "
                 f"Tasks in this category typically relate to: **{domain}**.")

    if "read" in action_types or action_type == "read":
        steps.append("2. **Read relevant files**: Use `list_chunks` and `read_chunk` to examine the code "
                     "or documents referenced in the request.")
    elif action_type in ("write", "edit"):
        steps.append("2. **Understand current state**: Read existing files to understand the structure "
                     "before making changes.")
    else:
        steps.append("2. **Gather context**: Load relevant files and data needed to complete the task.")

    if "write" in action_types or "analyze" in action_types:
        steps.append("3. **Plan the approach**: Outline the changes or analysis needed based on "
                     "the gathered context.")
        steps.append("4. **Execute**: Make the changes or perform the analysis.")
        steps.append("5. **Verify**: Confirm the result matches the expected outcome.")
    elif "read" in action_types:
        steps.append("3. **Synthesize findings**: Summarize what was learned from the files.")
        steps.append("4. **Report**: Present the findings clearly.")
    elif "git" in action_types or "github" in action_types:
        steps.append("3. **Execute git workflow**: Perform the necessary git operations.")
        steps.append("4. **Verify**: Check the git status and ensure everything is correct.")
    else:
        steps.append("3. **Execute**: Complete the task using appropriate tools.")
        steps.append("4. **Verify**: Confirm the result is correct.")

    patterns = _extract_common_patterns(tasks)
    pattern_lines = "\n".join(f"- {p}" for p in patterns) if patterns else "- _(No specific patterns extracted yet — update as you use this skill)_"

    return (
        "\n".join(steps) +
        "\n\n**Common patterns from similar tasks:**\n" +
        pattern_lines
    )


def _extract_common_patterns(tasks: list[str]) -> list[str]:
    """extract common patterns.
    
    Args:
        tasks (list):
    
    Returns:
        list"""
    bigrams = Counter()
    for t in tasks:
        words = re.findall(r"[a-z0-9]+", t.lower())
        for i in range(len(words) - 1):
            if len(words[i]) > 2 and len(words[i + 1]) > 2:
                bigrams[words[i] + " " + words[i + 1]] += 1
    return [bg for bg, count in bigrams.most_common(5) if count > 1][:3]


def _analyze_skills(stats: dict[str, Any]) -> list[dict[str, Any]]:
    """Analyse per-skill success rates and produce retain/refine/prune action recommendations."""
    actions = []
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
        elif rate < PRUNE_MAX_RATE and count >= PRUNE_MIN_COUNT:
            actions.append({
                "action": "prune",
                "skill": skill_name,
                "reason": f"Success rate {rate:.0%} < {PRUNE_MAX_RATE:.0%} with {count} uses",
                "success_rate": round(rate, 3),
                "count": count,
            })
    return actions


def _detect_clusters(unmatched_outcomes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Detect clusters of repeated unmatched tasks and produce generate action recommendations."""
    actions = []
    clusters = _cluster_unmatched(unmatched_outcomes)
    for cluster in sorted(clusters, key=len, reverse=True)[:5]:
        suggested_name = _extract_cluster_name(cluster)
        action_types = _aggregate_action_types(cluster)
        keywords = _extract_cluster_keywords(cluster)
        actions.append({
            "action": "generate",
            "skill": suggested_name,
            "reason": f"Cluster of {len(cluster)} similar unmatched tasks",
            "frequency": len(cluster),
            "cluster": cluster,
            "suggested_action_types": action_types,
            "suggested_keywords": keywords,
        })
    return actions


def analyze() -> dict[str, Any]:
    """
    Analyse all tracked outcomes and produce a list of evolution actions.

    Returns a dict with skill-level recommendations and potential new skill gaps.
    Orchestrates data validation, skill analysis, and cluster detection.
    """
    try:
        outcomes = tracker.get_outcomes()
    except (ImportError, NameError):
        return {"status": "error", "message": "skill_tracker not available"}

    total = len(outcomes)
    if total < 5:
        return {"status": "not_enough_data", "total": total, "actions": []}

    stats = tracker.get_all_skill_stats(recent=100)
    actions = _analyze_skills(stats)

    unmatched_outcomes = tracker.get_unmatched_outcomes(100)
    actions.extend(_detect_clusters(unmatched_outcomes))

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
    """suggest skill name.
    
    Args:
        task (str):
    
    Returns:
        str"""
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


def _load_skill_file(name: str) -> tuple[str | None, str | None]:
    """load skill file.
    
    Args:
        name (str):"""
    skills_dir = "skills"
    for root, _dirs, files in os.walk(skills_dir):
        for f in files:
            if f.endswith(".md") and f.replace(".md", "").lower() == name.lower():
                path = os.path.join(root, f)
                with open(path, "r", encoding="utf-8") as fh:
                    return path, fh.read()
    return None, None


def _apply_retain(skill_name: str, dry_run: bool, log: list[dict[str, Any]]) -> dict[str, Any]:
    """apply retain.
    
    Args:
        skill_name:
        dry_run:
        log:"""
    if not dry_run:
        log.append({
            "timestamp": datetime.now().isoformat(),
            "action": "retain",
            "skill": skill_name,
        })
    return {"action": "retain", "skill": skill_name, "dry_run": dry_run,
            "message": f"Kept '{skill_name}' unchanged"}


def _apply_refine(skill_name: str, action: dict[str, Any], dry_run: bool, log: list[dict[str, Any]]) -> dict[str, Any]:
    """apply refine.
    
    Args:
        skill_name:
        action:
        dry_run:
        log:"""
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
        msg = f"Refined '{skill_name}' with failure analysis"
    else:
        msg = f"Cannot refine '{skill_name}' — file not found"
    return {"action": "refine", "skill": skill_name, "dry_run": dry_run,
            "message": msg}


def _apply_prune(skill_name: str, dry_run: bool, log: list[dict[str, Any]]) -> dict[str, Any]:
    """apply prune.
    
    Args:
        skill_name:
        dry_run:
        log:"""
    path, _content = _load_skill_file(skill_name)
    if path:
        if not dry_run:
            backup = path + ".pruned"
            os.replace(path, backup)
            log.append({
                "timestamp": datetime.now().isoformat(),
                "action": "prune",
                "skill": skill_name,
                "backup": backup,
            })
            msg = f"Pruned '{skill_name}' (backup: {backup})"
        else:
            msg = f"Would prune '{skill_name}'"
    else:
        msg = f"Cannot prune '{skill_name}' — file not found"
    return {"action": "prune", "skill": skill_name, "dry_run": dry_run,
            "message": msg}


def _apply_generate(skill_name: str, action: dict[str, Any], dry_run: bool, log: list[dict[str, Any]]) -> dict[str, Any]:
    """apply generate.
    
    Args:
        skill_name:
        action:
        dry_run:
        log:"""
    cluster = action.get("cluster", [])
    action_types = action.get("suggested_action_types", ["general"])
    keywords = action.get("suggested_keywords", skill_name.split("_")[:3])

    if len(cluster) < 1:
        msg = "Skipped: no task cluster for skill generation"
    elif not dry_run:
        instructions = _generate_instructions(cluster, action_types)
        example = cluster[0][:80]
        kw_str = ", ".join(k for k in keywords if k)
        rubric_checks = []
        if any(a in action_types for a in ("read", "analyze")):
            rubric_checks.append('{"id":"context_read","desc":"L\u00e6ste relevant kontekst f\u00f8r udf\u00f8relse","check":"tool_used:read_chunk or tool_used:list_chunks or tool_used:list_files or tool_used:locate"}')
        if "write" in action_types:
            rubric_checks.append('{"id":"code_written","desc":"Implementerede med write_file eller edit_file","check":"tool_used:write_file or tool_used:edit_file"}')
        if "git" in action_types:
            rubric_checks.append('{"id":"git_used","desc":"Brugte git-v\u00e6rkt\u00f8jer","check":"tool_used:git_status or tool_used:git_commit or tool_used:git_push or tool_used:git_diff"}')
        if "github" in action_types:
            rubric_checks.append('{"id":"github_used","desc":"Brugte GitHub-v\u00e6rkt\u00f8jer","check":"tool_used:github_create_pr or tool_used:github_create_issue or tool_used:github_list_repos"}')
        rubrics_str = ", ".join(rubric_checks) if rubric_checks else ""
        frontmatter = (
            f"---\n"
            f"name: {skill_name}\n"
            f"keywords: [{kw_str}]\n"
            f"action_types: [{', '.join(action_types)}]\n"
            f"description: SkillFlow-generated \u2014 {len(cluster)} tasks: {example}...\n"
            f"base: false\n"
            f"min_score: 1\n" +
            (f"rubrics: [{rubrics_str}]\n" if rubrics_str else "") +
            f"---\n"
            f"\n"
            f"## {skill_name.replace('_', ' ').title()}\n"
            f"\n"
            f"Auto-generated skill based on {len(cluster)} similar unmatched tasks.\n"
            f"\n"
            f"**Example tasks:**\n" +
            "".join(f"- {t[:100]}\n" for t in cluster[:5]) +
            f"\n"
            f"### Instructions\n"
            f"\n"
            f"{instructions}\n"
        )
        gen_path = os.path.join("skills", f"{skill_name}.md")
        with open(gen_path, "w", encoding="utf-8") as f:
            f.write(frontmatter)
        log.append({
            "timestamp": datetime.now().isoformat(),
            "action": "generate",
            "skill": skill_name,
            "path": gen_path,
            "cluster_size": len(cluster),
            "keywords": keywords,
        })
        msg = f"Generated skill '{skill_name}' from {len(cluster)} tasks"
    else:
        msg = (f"Would generate skill '{skill_name}' "
               f"(action_types: {action.get('suggested_action_types', ['general'])}, "
               f"cluster: {len(cluster)} tasks)")
    return {"action": "generate", "skill": skill_name, "dry_run": dry_run,
            "message": msg}


def apply_evolution_actions(actions: list[dict[str, Any]], dry_run: bool = True) -> list[dict[str, Any]]:
    """
    Apply Retain/Refine/Prune/Generate actions to skill files.
    In dry_run mode, only return what would be done without touching files.
    """
    results = []
    log = _load_json(EVOLUTION_FILE, [])

    for action in actions:
        act = action["action"]
        skill_name = action["skill"]

        if act == "retain":
            result = _apply_retain(skill_name, dry_run, log)
        elif act == "refine":
            result = _apply_refine(skill_name, action, dry_run, log)
        elif act == "prune":
            result = _apply_prune(skill_name, dry_run, log)
        elif act == "generate":
            result = _apply_generate(skill_name, action, dry_run, log)
        else:
            result = {"action": act, "skill": skill_name, "dry_run": dry_run}

        results.append(result)

    if not dry_run:
        _save_json(EVOLUTION_FILE, log)

    return results


def _add_refinement_note(content: str, action: dict[str, Any]) -> str:
    """add refinement note.
    
    Args:
        content (str):
        action (dict):
    
    Returns:
        str"""
    patterns = action.get("failure_patterns", [])
    if len(patterns) < 2:
        return content
    marker = "<!-- skillflow:known_failures -->"
    section = (
        f"\n\n{marker}\n"
        f"### Kendte Fejlmønstre\n"
        f"\n"
        f"Opdateret: {datetime.now().strftime('%Y-%m-%d')}\n"
        f"\n"
        f"**Hyppige fejl ved brug af denne skill:**\n"
    )
    for p in patterns:
        section += f"- {p}\n"
    section += f"\n{{% end skillflow:known_failures %}}\n"
    if marker in content:
        start = content.find(marker)
        end_marker = "{% end skillflow:known_failures %}"
        end = content.find(end_marker, start)
        if end != -1:
            end += len(end_marker)
            return content[:start] + section + content[end:]
    lines = content.rstrip().split("\n")
    for i in range(len(lines) - 1, -1, -1):
        if lines[i].strip():
            lines.insert(i + 1, section)
            break
    else:
        lines.append(section)
    return "\n".join(lines)


_last_evolved_at = 0

def _reset_evolve_counter() -> None:
    """reset evolve counter."""
    global _last_evolved_at
    _last_evolved_at = 0

def should_evolve() -> bool:
    """should evolve.
    
    Returns:
        bool"""
    global _last_evolved_at
    try:
        total = tracker.total_outcomes
    except (ImportError, NameError, AttributeError):
        return False
    if total == 0:
        return False
    if total - _last_evolved_at >= EVOLVE_EVERY_N:
        _last_evolved_at = total
        return True
    return False


def evolve_if_needed(dry_run: bool = True) -> dict[str, Any]:
    """evolve if needed.
    
    Args:
        dry_run (bool):
    
    Returns:
        dict"""
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


def _log_applied(results: list[dict[str, Any]]) -> None:
    """log applied.
    
    Args:
        results (list):"""
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
    except Exception as e:
        log.warning("Failed to log applied evolution actions: %s", e)
