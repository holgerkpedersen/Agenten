"""Git workflow helpers for Agent."""

import re
import os
from lang import t
from i18n import K


PR_REQUIRED_BEFORE_PR = {"git_add_all", "git_commit", "git_push"}
PR_COMMIT_TOOLS = {"git_add_all", "git_commit"}
PR_PUSH_TOOLS = {"git_push"}
PR_BRANCH_TOOLS = {"git_create_branch"}
PR_REMOTE_TOOLS = {"git_remote_status"}
PR_GIT_TOOLS = {"git_diff", "git_log", "git_status", "git_current_branch", "git_branch_list", "git_pull", "git_checkout"}


def is_pr_workflow(task_name):
    if not task_name:
        return False
    if re.search(r'\bpr\b', task_name, re.IGNORECASE):
        return True
    keywords = ["pull request", "github", "push og opret", "push and create"]
    return any(k in task_name.lower() for k in keywords)


def extract_branch_name(task_name, original_prompt):
    m = re.search(r"branch\s*['\"]?([\w\-\/]+)['\"]?", original_prompt, re.IGNORECASE)
    if m:
        return m.group(1)
    m = re.search(r"branch\s*['\"]?([\w\-\/]+)['\"]?", task_name, re.IGNORECASE)
    if m:
        return m.group(1)
    return ""


def verify_pr_step(agent, tool_name, result, task_name, original_prompt):
    if not is_pr_workflow(task_name):
        return None

    if tool_name == "github_create_pr":
        called_set = {t.split("{")[0] for t in agent._checkpoint_tools}
        missing = PR_REQUIRED_BEFORE_PR - called_set
        if missing:
            return t(K.CP_NO_COMMIT, agent.lang)

    nested_success = result.get("result", {}).get("success", True)
    result_ok = result.get("success", False) and nested_success is not False
    if not result_ok:
        err = result.get("error") or result.get("result", {}).get("error", "ukendt fejl")
        err_str = str(err)
        if tool_name in PR_BRANCH_TOOLS and "already exists" in err_str:
            expected = extract_branch_name(task_name, original_prompt)
            return f"Branch '{expected}' findes allerede. Brug git_checkout(branch='{expected}') i stedet for at oprette den igen."
        if tool_name == "github_create_pr":
            return t(K.CP_PR_FAILED, agent.lang)
        return t(K.CP_TOOL_FAILED, agent.lang).format(tool=tool_name, error=err_str[:100])

    if tool_name in PR_BRANCH_TOOLS:
        expected = extract_branch_name(task_name, original_prompt)
        actual_branch = result.get("args", {}).get("name", "")
        if not actual_branch:
            actual_output = result.get("result", {}).get("error", "")
            m = re.search(r"'([^']+)'", actual_output)
            if m:
                actual_branch = m.group(1)
        if expected and actual_branch and actual_branch != expected:
            return t(K.CP_BRANCH_NAME, agent.lang).format(actual=actual_branch, expected=expected)
        agent._checkpoint_branch = actual_branch or expected

    if tool_name == "github_create_pr":
        url = result.get("result", {}).get("url", "")
        if not url:
            return t(K.CP_PR_FAILED, agent.lang)

    return None
