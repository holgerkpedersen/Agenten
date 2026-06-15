"""Agent tools - Git tool registration."""
from __future__ import annotations

import git_ops
from i18n import K
from lang import t
from tools import Tool
from agent_helpers import _safe_int


def register_git_tools(agent: object) -> None:
    """Register git-related tools on the agent's tool registry.
    
    Args:
        agent: Agent instance with tool_registry attribute.
    """
    agent.tool_registry.register(Tool(
        "git_status",
        t(K.TOOL_GIT_STATUS, agent.lang),
        [],
        lambda: git_ops.git_status()
    ))
    
    agent.tool_registry.register(Tool(
        "git_add_all",
        t(K.TOOL_GIT_ADD_ALL, agent.lang),
        [],
        lambda: git_ops.git_add_all()
    ))
    
    agent.tool_registry.register(Tool(
        "git_commit",
        t(K.TOOL_GIT_COMMIT, agent.lang),
        ["message"],
        lambda message: git_ops.git_commit(message=message)
    ))
    
    agent.tool_registry.register(Tool(
        "git_push",
        t(K.TOOL_GIT_PUSH, agent.lang),
        ["branch"],
        lambda branch="main": git_ops.git_push(branch=branch)
    ))
    
    agent.tool_registry.register(Tool(
        "git_set_remote",
        t(K.TOOL_GIT_SET_REMOTE, agent.lang),
        ["url"],
        lambda url: git_ops.git_set_remote(url=url)
    ))
    
    agent.tool_registry.register(Tool(
        "git_remote_status",
        t(K.TOOL_GIT_REMOTE_STATUS, agent.lang),
        [],
        lambda: git_ops.git_remote_exists()
    ))
    
    agent.tool_registry.register(Tool(
        "git_diff",
        t(K.TOOL_GIT_DIFF, agent.lang),
        ["older", "newer"],
        lambda older="HEAD~1", newer="HEAD": git_ops.git_diff(older, newer)
    ))
    
    agent.tool_registry.register(Tool(
        "git_log",
        t(K.TOOL_GIT_LOG, agent.lang),
        ["count"],
        lambda count=10: git_ops.git_log(_safe_int(count, 10))
    ))
    
    agent.tool_registry.register(Tool(
        "git_create_branch",
        t(K.TOOL_GIT_CREATE_BRANCH, agent.lang),
        ["name"],
        lambda name: git_ops.git_create_branch(name=name)
    ))
    
    agent.tool_registry.register(Tool(
        "git_current_branch",
        t(K.TOOL_GIT_CURRENT_BRANCH, agent.lang),
        [],
        lambda: git_ops.git_current_branch()
    ))
    
    agent.tool_registry.register(Tool(
        "git_branch_list",
        t(K.TOOL_GIT_BRANCH_LIST, agent.lang),
        [],
        lambda: git_ops.git_branch_list()
    ))
    
    agent.tool_registry.register(Tool(
        "git_pull",
        t(K.TOOL_GIT_PULL, agent.lang),
        ["remote", "branch"],
        lambda remote="origin", branch="main": git_ops.git_pull(remote=remote, branch=branch)
    ))
    
    agent.tool_registry.register(Tool(
        "git_checkout",
        t(K.TOOL_GIT_CHECKOUT, agent.lang),
        ["branch"],
        lambda branch: git_ops.git_checkout(branch=branch)
    ))
