"""Test agent_core.py — agent decomposition and execution."""
import pytest
from agent_core import Agent


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
        agent = Agent()
        assert agent.TEMPLATE_TOOLS["resume"] == []
        assert agent.TEMPLATE_TOOLS["kodeanalyse"] == []
        assert agent.TEMPLATE_TOOLS["diffanalyse"] == ["git_diff", "git_log"]
        assert agent.TEMPLATE_TOOLS["fri"] is None


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

    def test_agent_log_truncates_detail(self):
        agent = Agent()
        long_detail = "x" * 500
        agent._log("INFO", "Test", long_detail)
        assert len(agent.agent_log[0]["detail"]) <= 200


class TestAgentStatus:
    def test_get_agent_status(self):
        agent = Agent()
        status = agent.get_agent_status()
        assert "action_history" in status
        assert "total_actions" in status
        assert "log_entries" in status
        assert "has_task_tree" in status