"""Test agent_skills.py — skills matching, template constants."""
from unittest.mock import MagicMock


class TestTemplateTools:
    def test_all_templates_have_entry(self):
        from agent_skills import TEMPLATE_TOOLS
        expected = {"resume", "kodeanalyse", "diffanalyse", "fri",
                    "agenten", "programmering", "python-arkitektur",
                    "billedanalyse", "bugfix", "refactor", "testgenerering",
                    "issue_handler", "selvforbedring"}
        assert set(TEMPLATE_TOOLS.keys()) == expected

    def test_fri_is_none(self):
        from agent_skills import TEMPLATE_TOOLS
        assert TEMPLATE_TOOLS["fri"] is None

    def test_resume_tools(self):
        from agent_skills import TEMPLATE_TOOLS
        assert "locate" in TEMPLATE_TOOLS["resume"]
        assert "read_chunk" in TEMPLATE_TOOLS["resume"]

    def test_programmering_tools(self):
        from agent_skills import TEMPLATE_TOOLS
        assert "locate" in TEMPLATE_TOOLS["programmering"]
        assert "read_location" in TEMPLATE_TOOLS["programmering"]
        assert "read_chunk" not in TEMPLATE_TOOLS["programmering"]


class TestTemplateTaskTools:
    def test_has_agenten_entry(self):
        from agent_skills import TEMPLATE_TASK_TOOLS
        assert "agenten" in TEMPLATE_TASK_TOOLS

    def test_agenten_has_four_groups(self):
        from agent_skills import TEMPLATE_TASK_TOOLS
        groups = TEMPLATE_TASK_TOOLS["agenten"]
        assert set(groups.keys()) == {"branch", "commit", "push", "pull request"}

    def test_commit_includes_add_and_commit(self):
        from agent_skills import TEMPLATE_TASK_TOOLS
        tools = TEMPLATE_TASK_TOOLS["agenten"]["commit"]
        assert "git_add_all" in tools
        assert "git_commit" in tools
        assert "git_status" in tools
        assert "git_log" in tools
        assert "git_diff" in tools

    def test_branch_includes_checkout(self):
        from agent_skills import TEMPLATE_TASK_TOOLS
        tools = TEMPLATE_TASK_TOOLS["agenten"]["branch"]
        assert "git_checkout" in tools
        assert "git_create_branch" in tools


class TestSectionInstructions:
    def test_all_sections_have_keys(self):
        from agent_skills import SECTION_INSTRUCTIONS
        for template, sections in SECTION_INSTRUCTIONS.items():
            assert len(sections) >= 1, f"{template} has no sections"
            for key, val in sections.items():
                assert len(val) > 10, f"{template}.{key} too short"

    def test_kodeanalyse_has_five(self):
        from agent_skills import SECTION_INSTRUCTIONS
        sections = SECTION_INSTRUCTIONS["kodeanalyse"]
        assert len(sections) == 5

    def test_bugfix_has_five_phases(self):
        from agent_skills import SECTION_INSTRUCTIONS
        sections = SECTION_INSTRUCTIONS["bugfix"]
        assert "Analyse" in sections
        assert "Test (Red)" in sections
        assert "Implementering" in sections
        assert "Verifikation (Green)" in sections
        assert "Opdatering" in sections


class TestHasMatchingIntent:
    def test_base_skill_always_matches(self):
        from agent_skills import has_matching_intent
        agent = MagicMock()
        agent.active_template = "kodeanalyse"
        skill = {"base": True, "template": None}
        assert has_matching_intent(agent, skill) is True

    def test_matching_template(self):
        from agent_skills import has_matching_intent
        agent = MagicMock()
        agent.active_template = "kodeanalyse"
        skill = {"template": "kodeanalyse"}
        assert has_matching_intent(agent, skill) is True

    def test_nonmatching_template(self):
        from agent_skills import has_matching_intent
        agent = MagicMock()
        agent.active_template = "kodeanalyse"
        skill = {"template": "resume"}
        assert not has_matching_intent(agent, skill)

    def test_no_active_template(self):
        from agent_skills import has_matching_intent
        agent = MagicMock()
        agent.active_template = None
        skill = {"template": "kodeanalyse"}
        assert has_matching_intent(agent, skill) is True


class TestFormatSkillsForPrompt:
    def test_no_skills_returns_empty(self):
        from agent_skills import format_skills_for_prompt
        agent = MagicMock()
        agent._active_skills = []
        assert format_skills_for_prompt(agent) == ""

    def test_with_active_skills(self):
        from agent_skills import format_skills_for_prompt
        agent = MagicMock()
        agent._active_skills = [
            {"name": "test", "description": "A test skill", "base": False}
        ]
        result = format_skills_for_prompt(agent)
        assert "test" in result
        assert "MATCH" in result
        assert "A test skill" in result

    def test_base_skill_tagged_as_base(self):
        from agent_skills import format_skills_for_prompt
        agent = MagicMock()
        agent._active_skills = [
            {"name": "base", "description": "Base skill", "base": True}
        ]
        result = format_skills_for_prompt(agent)
        assert "BASE" in result
        assert "MATCH" not in result


class TestGetTemplates:
    def test_keys_present_in_da(self):
        from agent_skills import get_templates
        agent = MagicMock()
        agent.lang = "da"
        templates = get_templates(agent)
        assert "fri" in templates
        assert "kodeanalyse" in templates
        assert "resume" in templates
        assert "programmering" in templates
        assert "billedanalyse" in templates
        assert "bugfix" in templates

    def test_template_names_are_not_empty(self):
        from agent_skills import get_templates
        agent = MagicMock()
        agent.lang = "da"
        templates = get_templates(agent)
        for key, tpl in templates.items():
            assert tpl["name"], f"{key} name is empty"
            assert tpl["prompt"], f"{key} prompt is empty"
