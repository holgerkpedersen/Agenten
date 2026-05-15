"""
Skills-system for Agenten — indlæser skills fra skills/*.md og matcher dem til prompts.
Baseret på ReactAgent skills-arkitektur, tilpasset til Agentens template-baserede workflow.
"""

import os
import re
from typing import List, Optional


class SkillLoader:
    SKILLS_DIR = "skills"

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
                    if line.startswith("name:"):
                        header["name"] = line.split(":", 1)[1].strip()
                    elif line.startswith("keywords:"):
                        raw_kw = line.split(":", 1)[1].strip().strip("[]")
                        header["keywords"] = [k.strip() for k in raw_kw.split(",") if k.strip()]
                    elif line.startswith("template:"):
                        header["template"] = line.split(":", 1)[1].strip()
                    elif line.startswith("description:"):
                        header["description"] = line.split(":", 1)[1].strip()
                    elif line.startswith("base:"):
                        header["base"] = line.split(":", 1)[1].strip().lower() == "true"
                    elif line.startswith("min_score:"):
                        try:
                            header["min_score"] = int(line.split(":", 1)[1].strip())
                        except ValueError:
                            header["min_score"] = 1
        return header, body

    @staticmethod
    def _parse_skill(path: str) -> dict:
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = f.read()
            header, body = SkillLoader._parse_frontmatter(raw)

            name = header.get("name", os.path.splitext(os.path.basename(path))[0])
            if not name:
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
            }
        except (IOError, OSError):
            return {}

    @classmethod
    def load_all(cls, skills_dir: str = None, lang: str = None) -> List[dict]:
        """Indlæs skills — sprog-specifikke overrides har forrang over default."""
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
    def _score(cls, text: str, skill: dict) -> int:
        expanded = re.sub(r"([A-Z])", r" \1", text)
        t = expanded.lower()
        t = re.sub(r"\s+", " ", t).strip()
        score = 0

        for kw in skill.get("keywords", []):
            kw_l = kw.lower().strip()
            if not kw_l:
                continue
            if " " in kw_l:
                if kw_l in t:
                    score += 2
            else:
                if re.search(r"\b" + re.escape(kw_l) + r"\b", t):
                    score += 1
                elif len(kw_l) > 5 and kw_l in t:
                    score += 1

        name = skill.get("name", "").replace("_", " ").replace("-", " ").lower()
        if name:
            if re.search(r"\b" + re.escape(name) + r"\b", t):
                score += 3
            elif name in t:
                score += 1

        return score

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
        """Foreslå en template baseret på bedst matchende skill."""
        best = cls.find_for_task(prompt, skills)
        if best and best.get("template"):
            return best["template"]
        scored = [(cls._score(prompt, s), s) for s in skills if s.get("template")]
        if scored:
            best_score, best_skill = max(scored, key=lambda x: x[0])
            if best_score > 1:
                return best_skill["template"]
        return None
