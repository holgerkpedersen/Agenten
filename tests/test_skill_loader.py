"""Test skill_loader.py — skill loading, frontmatter parsing, matching."""
import sys
import os
import json
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from skill_loader import SkillLoader


class TestParseFrontmatter:
    def test_empty_string(self):
        header, body = SkillLoader._parse_frontmatter("")
        assert header == {}
        assert body == ""

    def test_no_frontmatter(self):
        header, body = SkillLoader._parse_frontmatter("# Just a heading")
        assert header == {}
        assert body == "# Just a heading"

    def test_full_frontmatter(self):
        raw = """---
name: test_skill
keywords: [python, testing]
template: kodeanalyse
description: A test skill
base: true
min_score: 3
action_types: [read, analyze]
---
# Skill Body
Content here"""
        header, body = SkillLoader._parse_frontmatter(raw)
        assert header["name"] == "test_skill"
        assert header["keywords"] == ["python", "testing"]
        assert header["template"] == "kodeanalyse"
        assert header["description"] == "A test skill"
        assert header["base"] is True
        assert header["min_score"] == 3
        assert header["action_types"] == ["read", "analyze"]
        assert "Skill Body" in body

    def test_partial_frontmatter(self):
        raw = """---
name: partial
keywords: [test]
---
Body text"""
        header, body = SkillLoader._parse_frontmatter(raw)
        assert header["name"] == "partial"
        assert "template" not in header
        assert body == "Body text"

    def test_invalid_min_score(self):
        raw = """---
name: bad
min_score: notanint
---
Body"""
        header, _ = SkillLoader._parse_frontmatter(raw)
        assert header["min_score"] == 1


class TestDeduceActionTypes:
    def test_read_actions(self):
        assert "read" in SkillLoader._deduce_action_types("read the file")
        assert "read" in SkillLoader._deduce_action_types("læs filen")

    def test_write_actions(self):
        assert "write" in SkillLoader._deduce_action_types("write code")
        assert "write" in SkillLoader._deduce_action_types("skriv en funktion")

    def test_git_actions(self):
        assert "git" in SkillLoader._deduce_action_types("commit changes")

    def test_github_actions(self):
        assert "github" in SkillLoader._deduce_action_types("create a pr")

    def test_search_actions(self):
        assert "search" in SkillLoader._deduce_action_types("søg efter")

    def test_analyze_actions(self):
        assert "analyze" in SkillLoader._deduce_action_types("analyser koden")

    def test_general_fallback(self):
        types = SkillLoader._deduce_action_types("hello world")
        assert types == ["general"]

    def test_multiple_types(self):
        types = SkillLoader._deduce_action_types("read and analyze code")
        assert "read" in types
        assert "analyze" in types


class TestParseSkill:
    def test_parse_valid_skill(self, tmp_path):
        skill_file = tmp_path / "test_skill.md"
        skill_file.write_text("""---
name: my_skill
keywords: [test, skill]
template: kodeanalyse
description: A test
---
# Instructions
Do the thing""")
        result = SkillLoader._parse_skill(str(skill_file))
        assert result["name"] == "my_skill"
        assert result["keywords"] == ["test", "skill"]
        assert result["template"] == "kodeanalyse"
        assert "Instructions" in result["body"]

    def test_parse_missing_file(self, tmp_path):
        result = SkillLoader._parse_skill(str(tmp_path / "nonexistent.md"))
        assert result == {}

    def test_parse_no_name(self, tmp_path):
        skill_file = tmp_path / "no_name.md"
        skill_file.write_text("---\nkeywords: [test]\n---\nBody")
        result = SkillLoader._parse_skill(str(skill_file))
        assert result.get("name") == "no_name"

    def test_parse_extracts_description_from_body(self, tmp_path):
        skill_file = tmp_path / "desc.md"
        skill_file.write_text("""---
name: desc_skill
---
# Heading
This is the description text that should be extracted.
More text here.""")
        result = SkillLoader._parse_skill(str(skill_file))
        assert "description" in result
        assert "description text" in result["description"]


class TestLoadAll:
    def test_load_nonexistent_dir(self):
        skills = SkillLoader.load_all("/nonexistent/path")
        assert skills == []

    def test_load_from_empty_dir(self, tmp_path):
        skills = SkillLoader.load_all(str(tmp_path))
        assert skills == []

    def test_load_single_skill(self, tmp_path):
        (tmp_path / "skill1.md").write_text("""---
name: skill_one
keywords: [test]
---
Body""")
        skills = SkillLoader.load_all(str(tmp_path))
        assert len(skills) == 1
        assert skills[0]["name"] == "skill_one"

    def test_load_multiple_skills(self, tmp_path):
        (tmp_path / "a.md").write_text("---\nname: alpha\n---\nA")
        (tmp_path / "b.md").write_text("---\nname: beta\n---\nB")
        skills = SkillLoader.load_all(str(tmp_path))
        assert len(skills) == 2
        names = {s["name"] for s in skills}
        assert names == {"alpha", "beta"}

    def test_load_ignores_non_md(self, tmp_path):
        (tmp_path / "skill.md").write_text("---\nname: real\n---\nBody")
        (tmp_path / "notes.txt").write_text("not a skill")
        skills = SkillLoader.load_all(str(tmp_path))
        assert len(skills) == 1

    def test_load_with_lang_subdir(self, tmp_path):
        (tmp_path / "base.md").write_text("---\nname: base\n---\nBase")
        lang_dir = tmp_path / "da"
        lang_dir.mkdir()
        (lang_dir / "local.md").write_text("---\nname: local\n---\nLocal")
        skills = SkillLoader.load_all(str(tmp_path), lang="da")
        assert len(skills) == 2


class TestScore:
    def test_keyword_match_scores(self):
        skill = {"keywords": ["python", "testing"], "name": "", "action_types": [], "min_score": 1}
        score = SkillLoader._score("write python tests", skill)
        assert score > 0

    def test_name_match_boost(self):
        skill = {"keywords": [], "name": "code_review", "action_types": [], "min_score": 1}
        score = SkillLoader._score("do a code review please", skill)
        assert score >= 3.0

    def test_no_match_returns_zero(self):
        skill = {"keywords": ["rust"], "name": "", "action_types": [], "min_score": 1}
        score = SkillLoader._score("python code", skill)
        assert score == 0.0


class TestFindForTask:
    def test_finds_best_skill(self):
        skills = [
            {"name": "python_dev", "keywords": ["python"], "template": "programmering",
             "action_types": ["write"], "min_score": 1, "base": False},
            {"name": "code_review", "keywords": ["review", "analyze"], "template": "kodeanalyse",
             "action_types": ["analyze"], "min_score": 1, "base": False},
        ]
        result = SkillLoader.find_for_task("review this python code", skills)
        assert result is not None
        assert result["name"] in ("python_dev", "code_review")

    def test_no_match_returns_none(self):
        skill = {"name": "git_skill", "keywords": ["git", "commit"], "template": "agenten",
                 "action_types": ["git"], "min_score": 1, "base": False}
        result = SkillLoader.find_for_task("cooking recipe", [skill])
        assert result is None

    def test_respects_min_score(self):
        skill = {"name": "sk", "keywords": ["rare"], "template": "fri",
                 "action_types": [], "min_score": 10, "base": False}
        result = SkillLoader.find_for_task("rare keyword", [skill])
        assert result is None


class TestFindAllForTask:
    def test_returns_top_n_plus_base(self):
        base = {"name": "base_skill", "keywords": [], "template": None,
                "action_types": [], "min_score": 1, "base": True}
        s1 = {"name": "a", "keywords": ["python"], "template": "fri",
              "action_types": [], "min_score": 1, "base": False}
        s2 = {"name": "b", "keywords": ["test"], "template": "fri",
              "action_types": [], "min_score": 1, "base": False}
        s3 = {"name": "c", "keywords": ["analysis"], "template": "fri",
              "action_types": [], "min_score": 1, "base": False}
        result = SkillLoader.find_all_for_task("python test analysis", [base, s1, s2, s3], top=2)
        assert len(result) == 3
        assert result[-1]["base"] is True


class TestSuggestTemplate:
    def test_returns_template_from_best_match(self):
        skills = [
            {"name": "code_analysis", "keywords": ["review", "analyze"], "template": "kodeanalyse",
             "action_types": ["analyze"], "min_score": 1, "base": False},
        ]
        result = SkillLoader.suggest_template("analyze this code", skills)
        assert result == "kodeanalyse"

    def test_returns_none_on_no_match(self):
        skills = [{"name": "s", "keywords": ["rare"], "template": "fri",
                    "action_types": [], "min_score": 1, "base": False}]
        result = SkillLoader.suggest_template("unrelated", skills)
        assert result is None


class TestGetSuccessRate:
    def test_tracker_not_loaded_returns_zero(self):
        rate = SkillLoader._get_success_rate("nonexistent_skill")
        assert rate == 0.0
