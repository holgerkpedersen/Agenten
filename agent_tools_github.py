"""Agent tools - GitHub tool registration."""
from __future__ import annotations

from github_wrapper import GithubAPI
from i18n import K
from lang import t
from tools import Tool


def register_github_tools(agent: object) -> None:
    """Register GitHub-related tools on the agent's tool registry.
    
    Args:
        agent: Agent instance with tool_registry attribute.
    """
    gh = GithubAPI()
    
    agent.tool_registry.register(Tool(
        "github_create_repo",
        t(K.TOOL_GITHUB_CREATE_REPO, agent.lang),
        ["name", "description", "private"],
        lambda name, description="", private=False: gh.create_repo(name=name, description=description, private=private)
    ))
    
    agent.tool_registry.register(Tool(
        "github_list_repos",
        t(K.TOOL_GITHUB_LIST_REPOS, agent.lang),
        [],
        lambda: gh.list_repos()
    ))
    
    agent.tool_registry.register(Tool(
        "github_create_issue",
        t(K.TOOL_GITHUB_CREATE_ISSUE, agent.lang),
        ["owner", "repo", "title", "body"],
        lambda owner, repo, title, body="": gh.create_issue(owner=owner, repo=repo, title=title, body=body)
    ))
    
    agent.tool_registry.register(Tool(
        "github_create_pr",
        t(K.TOOL_GITHUB_CREATE_PR, agent.lang),
        ["owner", "repo", "title", "branch"],
        lambda owner, repo, title, branch, base="main": gh.create_pr(owner=owner, repo=repo, title=title, head=branch, base=base)
    ))
