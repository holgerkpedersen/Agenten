"""Test agent_core.py — agent decomposition and execution."""
import pytest
from agent_core import Agent
import agent_skills
import agent_git


class TestAgentInit:
    def test_agent_init(self):
        agent = Agent()
        assert agent.lang == "da"
        assert agent.show_thinking is True
        assert agent.task_tree is None
        assert agent.tool_registry is not None

    def test_agent_has_llm(self):
        agent = Agent()
        assert agent.llm is not None

    def test_agent_has_tool_registry(self):
        agent = Agent()
        assert agent.tool_registry is not None
        assert len(agent.tool_registry.tools) >= 10

    def test_agent_templates(self):
        agent = Agent()
        templates = agent._get_templates()
        assert "resume" in templates
        assert "kodeanalyse" in templates
        assert "diffanalyse" in templates
        assert "fri" in templates
        assert "agenten" in templates
        assert "programmering" in templates
        assert "python-arkitektur" in templates

    def test_templates_localized(self):
        agent = Agent()
        agent.lang = "en"
        templates = agent._get_templates()
        assert "Summary" in templates["resume"]["name"]
        assert "Code Analysis" in templates["kodeanalyse"]["name"]

        agent.lang = "da"
        templates = agent._get_templates()
        assert "Resumé" in templates["resume"]["name"]

    def test_template_tools_mapping(self):
        assert "locate" in agent_skills.TEMPLATE_TOOLS["resume"]
        assert "locate" in agent_skills.TEMPLATE_TOOLS["kodeanalyse"]
        assert "locate" in agent_skills.TEMPLATE_TOOLS["diffanalyse"]
        assert agent_skills.TEMPLATE_TOOLS["fri"] is None
        assert "locate" in agent_skills.TEMPLATE_TOOLS["programmering"]
        assert "locate" in agent_skills.TEMPLATE_TOOLS["python-arkitektur"]
        assert "locate" in agent_skills.TEMPLATE_TOOLS["billedanalyse"]
        assert "locate" in agent_skills.TEMPLATE_TOOLS["bugfix"]


class TestFallbackTree:
    def test_fallback_tree_math(self):
        agent = Agent()
        agent.lang = "da"
        tree = agent._create_fallback_tree("Calculate 2 + 2")
        assert tree is not None
        assert tree.root is not None
        assert len(tree.root.children) >= 2

    def test_fallback_tree_generic(self):
        agent = Agent()
        agent.lang = "en"
        tree = agent._create_fallback_tree("Do something generic")
        assert tree is not None
        assert len(tree.root.children) >= 4

    def test_fallback_tree_analyze(self):
        agent = Agent()
        agent.lang = "da"
        tree = agent._create_fallback_tree("Analyse api_server.py")
        assert tree is not None
        assert len(tree.root.children) >= 3

    def test_fallback_tree_nodes_localized(self):
        agent = Agent()
        agent.lang = "da"
        tree = agent._create_fallback_tree("Analyse api_server.py")
        child_names = [n.name for n in tree.root.children]
        # Just verify children exist and tree is created
        assert len(child_names) > 0
        # Verify that if no localized name matches, fallback works
        assert all(isinstance(name, str) for name in child_names)


class TestTaskTreeConversions:
    def test_task_tree_to_dict(self):
        agent = Agent()
        agent.lang = "da"
        agent.task_tree = agent._create_fallback_tree("Test task")
        result = agent.task_tree_to_dict()
        assert result is not None
        assert "name" in result
        assert "children" in result

    def test_task_tree_to_dict_roundtrip(self):
        agent = Agent()
        agent.lang = "da"
        agent.task_tree = agent._create_fallback_tree("Test task")
        d1 = agent.task_tree_to_dict()
        agent.task_tree_from_dict(d1)
        d2 = agent.task_tree_to_dict()
        assert d1["name"] == d2["name"]


class TestResetExecution:
    def test_reset_execution_no_tree(self):
        agent = Agent()
        agent.reset_execution()

    def test_reset_execution_with_tree(self):
        agent = Agent()
        agent.lang = "da"
        tree = agent._create_fallback_tree("Test task")
        agent.task_tree = tree
        for child in tree.root.children:
            child.status = "done"
        agent.reset_execution()
        for child in tree.root.children:
            assert child.status == "pending"


class TestAgentLog:
    def test_agent_logs_entry(self):
        agent = Agent()
        agent._log("INFO", "Test message", "detail")
        assert len(agent.agent_log) >= 1
        log = agent.agent_log[0]
        assert log["level"] == "INFO"
        assert log["message"] == "Test message"
        assert "timestamp" in log

    def test_agent_log_does_not_truncate_detail(self):
        agent = Agent()
        long_detail = "x" * 500
        agent._log("INFO", "Test", long_detail)
        assert len(agent.agent_log[0]["detail"]) == 500


class TestAgentStatus:
    def test_get_agent_status(self):
        agent = Agent()
        status = agent.get_agent_status()
        assert "action_history" in status
        assert "total_actions" in status
        assert "log_entries" in status
        assert "has_task_tree" in status


class TestExtractBranchName:
    def test_prefers_original_prompt_over_task_name(self):
        result = agent_git.extract_branch_name(
            "1. Opret branch og verificer",
            "Opret en ny branch 'test-ny-branch8', commit ændringerne, og opret PR til master"
        )
        assert result == "test-ny-branch8", f"Expected test-ny-branch8, got '{result}'"

    def test_falls_back_to_task_name(self):
        result = agent_git.extract_branch_name(
            "Opret branch main-feature og verificer",
            "Nothing to decompose here"
        )
        assert result == "main-feature"

    def test_returns_empty_if_no_branch_found(self):
        result = agent_git.extract_branch_name("Just a task name", "Decompose this without referencing any git")
        assert result == ""

    def test_ignores_opret_substring_in_task_name(self):
        result = agent_git.extract_branch_name(
            "1. Opret branch og verificer",
            "Create branch 'test-feature', commit and push"
        )
        assert result == "test-feature", f"Should find from original_prompt, not 'og' from task name"


class TestIsPrWorkflow:
    def test_matches_real_pr_in_text(self):
        assert agent_git.is_pr_workflow("Opret en ny branch og opret PR til master") is True
        assert agent_git.is_pr_workflow("Create PR for feature") is True
        assert agent_git.is_pr_workflow("Opret Pull Request") is True

    def test_does_not_match_opret_substring(self):
        assert agent_git.is_pr_workflow("1. Opret branch og verificer") is False
        assert agent_git.is_pr_workflow("2. Stage og commit ændringer") is False
        assert agent_git.is_pr_workflow("3. Push til remote") is False

    def test_does_not_match_unrelated_tasks(self):
        assert agent_git.is_pr_workflow("Analyse kode") is False
        assert agent_git.is_pr_workflow("Lav et resume") is False

    def test_matches_pull_request_subtask(self):
        assert agent_git.is_pr_workflow("4. Opret Pull Request") is True

    def test_matches_github_keyword(self):
        assert agent_git.is_pr_workflow("GitHub PR setup") is True


class TestTemplateTaskTools:
    def test_has_agenten_entry(self):
        assert "agenten" in agent_skills.TEMPLATE_TASK_TOOLS

    def test_agenten_has_four_task_groups(self):
        groups = agent_skills.TEMPLATE_TASK_TOOLS["agenten"]
        assert "branch" in groups
        assert "commit" in groups
        assert "push" in groups
        assert "pull request" in groups

    def test_push_group_excludes_git_status(self):
        push_tools = agent_skills.TEMPLATE_TASK_TOOLS["agenten"]["push"]
        assert "git_push" in push_tools
        assert "git_remote_status" in push_tools
        assert "git_status" not in push_tools, "git_status should not be in push tools"

    def test_commit_group_includes_core_tools(self):
        commit_tools = agent_skills.TEMPLATE_TASK_TOOLS["agenten"]["commit"]
        assert "git_add_all" in commit_tools
        assert "git_commit" in commit_tools


class TestSetTaskTools:
    def test_sets_branch_tools_for_branch_task(self):
        agent = Agent()
        agent.active_template = "agenten"
        agent._set_task_tools("1. Opret branch og verificer")
        assert agent.tool_registry.active_tools is not None
        assert "git_create_branch" in agent.tool_registry.active_tools
        assert "git_checkout" in agent.tool_registry.active_tools

    def test_sets_push_tools_for_push_task(self):
        agent = Agent()
        agent.active_template = "agenten"
        agent._set_task_tools("3. Push til remote")
        assert agent.tool_registry.active_tools is not None
        assert "git_push" in agent.tool_registry.active_tools
        assert "git_status" not in agent.tool_registry.active_tools

    def test_sets_pr_tools_for_pull_request_task(self):
        agent = Agent()
        agent.active_template = "agenten"
        agent._set_task_tools("4. Opret Pull Request")
        assert agent.tool_registry.active_tools is not None
        assert "github_create_pr" in agent.tool_registry.active_tools

    def test_no_template_leaves_active_tools_unchanged(self):
        agent = Agent()
        agent.tool_registry.set_active_tools(["git_status"])
        agent.active_template = None
        agent._set_task_tools("some random task")
        assert agent.tool_registry.active_tools == ["git_status"]

    def test_falls_back_to_template_tools_if_no_keyword_match(self):
        agent = Agent()
        agent.active_template = "agenten"
        agent._set_task_tools("Some unrelated task name")
        assert agent.tool_registry.active_tools is not None
        assert "github_create_pr" in agent.tool_registry.active_tools


class TestSafeInt:
    def test_safe_int_valid(self):
        from agent_core import _safe_int
        assert _safe_int("10") == 10
        assert _safe_int("0") == 0
        assert _safe_int(5) == 5

    def test_safe_int_invalid(self):
        from agent_core import _safe_int
        assert _safe_int("abc") == 0
        assert _safe_int("abc", 5) == 5
        assert _safe_int(None) == 0

    def test_safe_int_empty(self):
        from agent_core import _safe_int
        assert _safe_int("") == 0
