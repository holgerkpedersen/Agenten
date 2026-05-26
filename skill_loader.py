"""
Skills-system for Agenten — indlæser skills fra skills/*.md og matcher dem til prompts.
Baseret på ReactAgent skills-arkitektur, tilpasset til Agentens template-baserede workflow.
"""

import os
import re
from typing import List, Optional


class SkillLoader:
    SKILLS_DIR = "skills"

    # Patterns to deduce action types from task text
    ACTION_TYPE_PATTERNS = {
        "read": [r"\bread\b", r"\blæs\b", r"\bhent\b", r"\bfetch\b", r"\bget\b", r"\bvis\b", r"\bshow\b"],
        "write": [r"\bwrite\b", r"\bskriv\b", r"\bcreate\b", r"\bopret\b", r"\bedit\b", r"\bret\b",
                   r"\btilføj\b", r"\badd\b", r"\bupdate\b", r"\bopdater\b", r"\bgenerate\b"],
        "git": [r"\bgit\b", r"\bcommit\b", r"\bpush\b", r"\bbranch\b", r"\bmerge\b", r"\bpull\b",
                 r"\bcheckout\b", r"\brebase\b"],
        "github": [r"\bgithub\b", r"\bpr\b", r"\bissue\b", r"\bpull request\b", r"\brepository\b",
                    r"\brepo\b"],
        "search": [r"\bsearch\b", r"\bsøg\b", r"\bfind\b", r"\bled\b", r"\blookup\b", r"\bslå op\b"],
        "analyze": [r"\banalyze\b", r"\banalyser\b", r"\breview\b", r"\bgennemgå\b",
                     r"\brefactor\b", r"\bomstrukturer\b", r"\bevaluate\b", r"\bevaluer\b"],
    }

    @staticmethod
    def _parse_frontmatter(raw: str) -> "tuple[dict, str]":
        header = {}
        body = raw
        if raw.startswith("---"):
            parts = raw.split("---", 2)
            if len(parts) >= 3:
                body = parts[2].strip()
                for line in parts[1].splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        key, _, val = line.partition(":")
                        key = key.strip()
                        val = val.strip()
                        if key == "name":
                            header["name"] = val
                        elif key == "keywords":
                            raw_kw = val.strip("[]")
                            header["keywords"] = [k.strip() for k in raw_kw.split(",") if k.strip()]
                        elif key == "template":
                            header["template"] = val
                        elif key == "description":
                            header["description"] = val
                        elif key == "base":
                            header["base"] = val.lower() == "true"
                        elif key == "min_score":
                            try:
                                header["min_score"] = int(val)
                            except ValueError:
                                header["min_score"] = 1
                        elif key == "action_types":
                            raw_at = val.strip("[]")
                            header["action_types"] = [a.strip() for a in raw_at.split(",") if a.strip()]
                    except (IndexError, AttributeError):
                        continue
        return header, body

    @staticmethod
    def _deduce_action_types(task: str) -> list:
        task_lower = task.lower()
        matched = []
        for atype, patterns in SkillLoader.ACTION_TYPE_PATTERNS.items():
            for pat in patterns:
                if re.search(pat, task_lower):
                    matched.append(atype)
                    break
        return matched if matched else ["general"]

    @staticmethod
    def _parse_skill(path: str) -> dict:
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = f.read()
            header, body = SkillLoader._parse_frontmatter(raw)

            name = header.get("name", os.path.splitext(os.path.basename(path))[0])
            if not name:
                return {}

            # Skip auto-generated stubs with no real instructions
            if "_(Add instructions here based on observed patterns)_" in body:
                return {}

            description = header.get("description")
            if not description and body:
                for line in body.splitlines():
                    stripped = line.strip()
                    if stripped and not stripped.startswith("#"):
                        description = stripped[:120]
                        break

            return {
                "name": name,
                "keywords": header.get("keywords", []),
                "template": header.get("template"),
                "base": header.get("base", False),
                "min_score": header.get("min_score", 1),
                "description": description or "",
                "body": body,
                "action_types": header.get("action_types", []),
            }
        except (IOError, OSError):
            return {}

    @classmethod
    def load_all(cls, skills_dir: str = None, lang: str = None) -> List[dict]:
        if skills_dir is None:
            skills_dir = cls.SKILLS_DIR

        all_skills = {}
        dirs_to_load = [skills_dir]
        if lang and os.path.isdir(os.path.join(skills_dir, lang)):
            dirs_to_load.append(os.path.join(skills_dir, lang))

        for d in dirs_to_load:
            if not os.path.isdir(d):
                continue
            for fname in sorted(os.listdir(d)):
                if fname.endswith(".md"):
                    s = cls._parse_skill(os.path.join(d, fname))
                    if s and s["name"]:
                        all_skills[s["name"]] = s

        return list(all_skills.values())

    @classmethod
    def _get_success_rate(cls, skill_name: str) -> float:
        try:
            from skill_tracker import tracker
            stats = tracker.get_stats(skill_name, recent=50)
            return stats.get("success_rate", 0)
        except (ImportError, Exception):
            return 0

    @classmethod
    def _score(cls, text: str, skill: dict) -> float:
        expanded = re.sub(r"([A-Z])", r" \1", text)
        t = expanded.lower()
        t = re.sub(r"\s+", " ", t).strip()
        score = 0.0

        for kw in skill.get("keywords", []):
            kw_l = kw.lower().strip()
            if not kw_l:
                continue
            if " " in kw_l:
                if kw_l in t:
                    score += 2.0
            else:
                if re.search(r"\b" + re.escape(kw_l) + r"\b", t):
                    score += 1.0
                elif len(kw_l) > 5 and kw_l in t:
                    score += 1.0

        name = skill.get("name", "").replace("_", " ").replace("-", " ").lower()
        if name:
            if re.search(r"\b" + re.escape(name) + r"\b", t):
                score += 3.0
            elif name in t:
                score += 1.0

        action_types = skill.get("action_types", [])
        if action_types:
            deduced = cls._deduce_action_types(t)
            overlap = len(set(action_types) & set(deduced))
            if overlap > 0:
                score += overlap * 1.5

        success_rate = cls._get_success_rate(skill.get("name", ""))
        if success_rate > 0:
            score *= 1.0 + success_rate * 0.5

        return round(score, 2)

    @classmethod
    def find_for_task(cls, task: str, skills: List[dict]) -> Optional[dict]:
        scored = [
            (cls._score(task, s), s)
            for s in skills
            if cls._score(task, s) >= s.get("min_score", 1)
        ]
        best_score, best = max(scored, key=lambda x: x[0], default=(0, None))
        return best if best_score > 0 else None

    @classmethod
    def find_all_for_task(cls, task: str, skills: List[dict], top: int = 3) -> List[dict]:
        base_skills = [s for s in skills if s.get("base")]
        scored = [
            (sc, s)
            for s in skills
            for sc in (cls._score(task, s),)
            if sc >= max(1, s.get("min_score", 1)) and not s.get("base")
        ]
        scored.sort(key=lambda x: x[0], reverse=True)
        return [s for _, s in scored[:top]] + base_skills

    @classmethod
    def suggest_template(cls, prompt: str, skills: List[dict]) -> Optional[str]:
        best = cls.find_for_task(prompt, skills)
        if best and best.get("template"):
            return best["template"]
        scored = [(cls._score(prompt, s), s) for s in skills if s.get("template")]
        if scored:
            best_score, best_skill = max(scored, key=lambda x: x[0])
            if best_score > 1:
                return best_skill["template"]
        return None
