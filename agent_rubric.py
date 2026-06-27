from typing import Any, Generator

def _validate_rubrics(agent: Any, called_tools: dict[str, int]) -> tuple[list, list]:
    """validate rubrics.
    
    Args:
        agent:
        called_tools:"""
    skill = agent._active_skills[0] if agent._active_skills else None
    if not skill:
        return [], []
    skill_rubrics = skill.get("rubrics", [])
    if not skill_rubrics:
        return [], []
    called = {k.split("{")[0] for k in called_tools}
    passed, failed = [], []
    for rubric in skill_rubrics:
        check = rubric.get("check", "")
        ok = _evaluate_rubric_check(check, called)
        if ok:
            passed.append(rubric)
        else:
            failed.append(rubric)
    return passed, failed



def _evaluate_rubric_check(check_str: str, called_tools: set[str]) -> bool:
    """evaluate rubric check.
    
    Args:
        check_str:
        called_tools:"""
    if not check_str:
        return True
    for part in check_str.split(" or "):
        cond = part.strip()
        if cond.startswith("tool_used:"):
            target = cond[len("tool_used:"):].strip()
            if target in called_tools:
                return True
    return False
