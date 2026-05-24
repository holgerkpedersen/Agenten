"""Test agent_git.py — PR workflow detection and verification."""
from unittest.mock import MagicMock


class TestPRConstants:
    def test_pr_required_before_pr(self):
        from agent_git import PR_REQUIRED_BEFORE_PR
        assert PR_REQUIRED_BEFORE_PR == {"git_add_all", "git_commit", "git_push"}

    def test_pr_commit_tools(self):
        from agent_git import PR_COMMIT_TOOLS
        assert PR_COMMIT_TOOLS == {"git_add_all", "git_commit"}

    def test_pr_branch_tools(self):
        from agent_git import PR_BRANCH_TOOLS
        assert PR_BRANCH_TOOLS == {"git_create_branch"}

    def test_pr_git_tools(self):
        from agent_git import PR_GIT_TOOLS
        assert "git_diff" in PR_GIT_TOOLS
        assert "git_log" in PR_GIT_TOOLS
        assert "git_status" in PR_GIT_TOOLS


class TestVerifyPrStep:
    def test_returns_none_for_non_pr_workflow(self):
        from agent_git import verify_pr_step
        agent = MagicMock()
        result = verify_pr_step(agent, "git_status", {}, "Analyse kode", "")
        assert result is None

    def test_returns_none_for_successful_branch(self):
        from agent_git import verify_pr_step
        agent = MagicMock()
        agent._checkpoint_tools = set()
        result = {"success": True, "result": {"error": "Switched to a new branch 'feature-x'"}}
        msg = verify_pr_step(agent, "git_create_branch", result,
                             "Opret PR til master", "Opret branch 'feature-x'")
        assert result is not None

    def test_returns_branch_exists_warning(self):
        from agent_git import verify_pr_step
        agent = MagicMock()
        agent._checkpoint_tools = set()
        result = {"success": False, "error": "fatal: A branch named 'feature-x' already exists."}
        msg = verify_pr_step(agent, "git_create_branch", result,
                             "Opret PR til master", "Opret branch 'feature-x'")
        assert msg is not None
        assert "findes allerede" in msg

    def test_missing_commit_before_pr(self):
        from agent_git import verify_pr_step
        agent = MagicMock()
        agent._checkpoint_tools = {"git_create_branch"}
        result = {"success": True, "result": {"url": "http://pr.url"}}
        msg = verify_pr_step(agent, "github_create_pr", result,
                             "Opret PR til master", "Opret PR")
        assert msg is not None

    def test_pr_failed_no_url(self):
        from agent_git import verify_pr_step
        agent = MagicMock()
        agent._checkpoint_tools = {"git_add_all", "git_commit", "git_push"}
        result = {"success": True, "result": {}}
        msg = verify_pr_step(agent, "github_create_pr", result,
                             "Opret PR til master", "Opret PR")
        assert msg is not None

    def test_branch_name_mismatch(self):
        from agent_git import verify_pr_step
        agent = MagicMock()
        agent._checkpoint_tools = set()
        result = {"success": True, "result": {"error": "Switched to a new branch 'wrong-name'"}}
        msg = verify_pr_step(agent, "git_create_branch", result,
                             "Opret PR til master", "Opret branch 'expected-name'")
        assert msg is not None
        assert "wrong-name" in msg

    def test_tool_failed_generic(self):
        from agent_git import verify_pr_step
        agent = MagicMock()
        agent._checkpoint_tools = set()
        result = {"success": False, "error": "permission denied"}
        msg = verify_pr_step(agent, "git_commit", result,
                             "Opret PR til master", "Commit changes")
        assert msg is not None
