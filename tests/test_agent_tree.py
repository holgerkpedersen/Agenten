"""Test agent_tree.py — tree operations, parsing, and utility functions."""
from unittest.mock import MagicMock
from task_tree import TaskTree, TaskNode


class TestCleanTaskName:
    def test_clean_basic(self):
        from agent_tree import _clean_task_name
        result = _clean_task_name("  Analyse koden  ")
        assert result == "Analyse koden"

    def test_clean_strips_number_prefix(self):
        from agent_tree import _clean_task_name
        result = _clean_task_name("1. Opret branch")
        assert result == "Opret branch"

    def test_clean_strips_bullet(self):
        from agent_tree import _clean_task_name
        result = _clean_task_name("- Læs fil")
        assert result == "Læs fil"

    def test_clean_strips_think_tags(self):
        from agent_tree import _clean_task_name
        result = _clean_task_name("<think>overvejer</think> Gør noget")
        assert "think" not in result.lower() or len(result) < 10

    def test_clean_removes_thinking_process(self):
        from agent_tree import _clean_task_name
        result = _clean_task_name("Here's a thinking process: analyse")
        assert result is None or "thinking" not in result.lower()

    def test_clean_too_short_returns_none(self):
        from agent_tree import _clean_task_name
        result = _clean_task_name("-")
        assert result is None

    def test_clean_returns_none_for_empty(self):
        from agent_tree import _clean_task_name
        result = _clean_task_name("")
        assert result is None

    def test_clean_handles_bold_inside(self):
        from agent_tree import _clean_task_name
        result = _clean_task_name("Opret **ny** branch")
        assert result is not None
        assert "Opret" in result

    def test_clean_removes_entire_bold_line(self):
        from agent_tree import _clean_task_name
        result = _clean_task_name("**Opret branch**")
        assert result is None

    def test_clean_handles_inline_code(self):
        from agent_tree import _clean_task_name
        result = _clean_task_name("Kør `run.py`")
        assert "run.py" not in result
        assert "Kør" in result


class TestCreateFallbackTree:
    def test_math_fallback(self):
        from agent_tree import create_fallback_tree
        agent = MagicMock()
        agent.lang = "da"
        tree = create_fallback_tree(agent, "Calculate 2 + 2")
        assert tree is not None
        assert len(tree.root.children) >= 2

    def test_generic_fallback(self):
        from agent_tree import create_fallback_tree
        agent = MagicMock()
        agent.lang = "da"
        tree = create_fallback_tree(agent, "Do something generic")
        assert tree is not None
        assert len(tree.root.children) >= 4

    def test_analyze_fallback(self):
        from agent_tree import create_fallback_tree
        agent = MagicMock()
        agent.lang = "da"
        tree = create_fallback_tree(agent, "Analyse api_server.py")
        assert tree is not None
        assert len(tree.root.children) >= 3

    def test_fallback_respects_lang(self):
        from agent_tree import create_fallback_tree
        agent = MagicMock()
        agent.lang = "en"
        tree = create_fallback_tree(agent, "Analyse api_server.py")
        result = [n.name for n in tree.root.children]
        assert len(result) > 0


class TestParseTreeFromLLM:
    def test_parse_simple(self):
        from agent_tree import parse_tree_from_llm
        agent = MagicMock()
        response = "Opgave 1\n  Under 1.1\n  Under 1.2\nOpgave 2"
        tree = parse_tree_from_llm(agent, "prompt", response)
        assert tree is not None
        assert len(tree.root.children) == 2
        assert len(tree.root.children[0].children) == 2

    def test_parse_deep_nesting(self):
        from agent_tree import parse_tree_from_llm
        agent = MagicMock()
        response = "Root\n  Level1\n    Level2\n      Level3"
        tree = parse_tree_from_llm(agent, "prompt", response)
        assert tree is not None
        deepest = tree.root.children[0].children[0].children[0]
        assert deepest is not None

    def test_parse_empty_falls_back(self):
        from agent_tree import parse_tree_from_llm
        agent = MagicMock()
        tree = parse_tree_from_llm(agent, "prompt", "")
        assert tree is not None

    def test_parse_error_falls_back(self):
        from agent_tree import parse_tree_from_llm
        agent = MagicMock()
        tree = parse_tree_from_llm(agent, "prompt", "ERROR: failed")
        assert tree is not None

    def test_parse_skips_noise_lines(self):
        from agent_tree import parse_tree_from_llm
        agent = MagicMock()
        response = "<think>hmm</think>\nOpgave 1\n  Detalje\n--------"
        tree = parse_tree_from_llm(agent, "prompt", response)
        assert tree is not None


class TestCountTasks:
    def test_single(self):
        from agent_tree import count_tasks
        tree = TaskTree("test")
        assert count_tasks(tree.root) == 1

    def test_with_children(self):
        from agent_tree import count_tasks
        tree = TaskTree("root")
        tree.root.add_child(TaskNode("c1"))
        tree.root.add_child(TaskNode("c2"))
        assert count_tasks(tree.root) == 3

    def test_nested(self):
        from agent_tree import count_tasks
        tree = TaskTree("root")
        c = tree.root.add_child(TaskNode("c1"))
        c.add_child(TaskNode("gc1"))
        c.add_child(TaskNode("gc2"))
        assert count_tasks(tree.root) == 4


class TestTaskTreeConversions:
    def test_to_dict_roundtrip(self):
        from agent_tree import task_tree_to_dict, task_tree_from_dict
        agent = MagicMock()
        tree = TaskTree("root")
        c = tree.root.add_child(TaskNode("child1"))
        c.add_child(TaskNode("gc1"))
        agent.task_tree = tree

        d1 = task_tree_to_dict(agent)
        assert d1 is not None
        assert d1["name"] == "root"
        assert len(d1["children"]) == 1

        agent.task_tree = TaskTree("temp")
        task_tree_from_dict(agent, d1)
        d2 = task_tree_to_dict(agent)
        assert d2["name"] == "root"
        assert len(d2["children"]) == 1

    def test_to_dict_empty(self):
        from agent_tree import task_tree_to_dict
        agent = MagicMock()
        agent.task_tree = None
        assert task_tree_to_dict(agent) is None


class TestRecordOutcome:
    def test_record_outcome_handles_importerror(self):
        from agent_tree import record_outcome
        agent = MagicMock()
        agent._active_skills = []
        agent._task_start_time = 0
        agent.active_template = None
        node = TaskNode("test task")
        node.status = "done"
        record_outcome(agent, node)


class TestEvolveIfNeeded:
    def test_evolve_handles_importerror(self):
        from agent_tree import evolve_if_needed
        agent = MagicMock()
        evolve_if_needed(agent)
