from llm_wrapper import LMStudioWrapper
from web_searcher import WebSearcher
from task_tree import TaskTree, TaskNode
from module_builder import ModuleBuilder
from tools import Tool, ToolRegistry
from github_wrapper import GithubAPI
from lang import t
from i18n import K
import git_ops
import re
import time
import os
import json


PR_REQUIRED_BEFORE_PR = {"git_add_all", "git_commit", "git_push"}
PR_COMMIT_TOOLS = {"git_add_all", "git_commit"}
PR_PUSH_TOOLS = {"git_push"}
PR_BRANCH_TOOLS = {"git_create_branch"}
PR_REMOTE_TOOLS = {"git_remote_status"}
PR_GIT_TOOLS = {"git_diff", "git_log", "git_status", "git_current_branch", "git_branch_list", "git_pull", "git_checkout"}


class Agent:
    def __init__(self):
        self.llm = LMStudioWrapper(timeout=120, model="qwen/qwen3.5-9b")
        self.decompose_llm = LMStudioWrapper(timeout=120, model="qwen/qwen3.5-9b")
        self.searcher = WebSearcher()
        self.task_tree = None
        self.action_history = []
        self.execution_log = []
        self.agent_log = []
        self.original_prompt = ""
        self.full_prompt_with_context = ""
        self.show_thinking = True
        self.file_context = []
        self.stop_requested = False
        self.lang = "da"
        self.max_tokens = 4096
        self.max_conversation_chars = 8000
        self.tool_registry = ToolRegistry()
        self._register_tools()
        self._checkpoint_tools = set()
        self._checkpoint_branch = ""

    def _register_tools(self):
        gh = GithubAPI()
        self.tool_registry.register(Tool(
            "github_create_repo",
            t(K.TOOL_GITHUB_CREATE_REPO, self.lang),
            ["name", "description", "private"],
            lambda name, description="", private=False: gh.create_repo(name=name, description=description, private=private)
        ))
        self.tool_registry.register(Tool(
            "github_list_repos",
            t(K.TOOL_GITHUB_LIST_REPOS, self.lang),
            [],
            lambda: gh.list_repos()
        ))
        self.tool_registry.register(Tool(
            "github_create_issue",
            t(K.TOOL_GITHUB_CREATE_ISSUE, self.lang),
            ["owner", "repo", "title", "body"],
            lambda owner, repo, title, body="": gh.create_issue(owner=owner, repo=repo, title=title, body=body)
        ))
        self.tool_registry.register(Tool(
            "github_create_pr",
            t(K.TOOL_GITHUB_CREATE_PR, self.lang),
            ["owner", "repo", "title", "branch"],
            lambda owner, repo, title, branch, base="master": gh.create_pr(owner=owner, repo=repo, title=title, head=branch, base=base)
        ))
        self.tool_registry.register(Tool(
            "git_status",
            t(K.TOOL_GIT_STATUS, self.lang),
            [],
            lambda: git_ops.git_status()
        ))
        self.tool_registry.register(Tool(
            "git_add_all",
            t(K.TOOL_GIT_ADD_ALL, self.lang),
            [],
            lambda: git_ops.git_add_all()
        ))
        self.tool_registry.register(Tool(
            "git_commit",
            t(K.TOOL_GIT_COMMIT, self.lang),
            ["message"],
            lambda message: git_ops.git_commit(message=message)
        ))
        self.tool_registry.register(Tool(
            "git_push",
            t(K.TOOL_GIT_PUSH, self.lang),
            ["branch"],
            lambda branch="master": git_ops.git_push(branch=branch)
        ))
        self.tool_registry.register(Tool(
            "git_set_remote",
            t(K.TOOL_GIT_SET_REMOTE, self.lang),
            ["url"],
            lambda url: git_ops.git_set_remote(url=url)
        ))
        self.tool_registry.register(Tool(
            "git_remote_status",
            t(K.TOOL_GIT_REMOTE_STATUS, self.lang),
            [],
            lambda: git_ops.git_remote_exists()
        ))
        self.tool_registry.register(Tool(
            "git_diff",
            t(K.TOOL_GIT_DIFF, self.lang),
            ["older", "newer"],
            lambda older="HEAD~1", newer="HEAD": git_ops.git_diff(older, newer)
        ))
        self.tool_registry.register(Tool(
            "git_log",
            t(K.TOOL_GIT_LOG, self.lang),
            ["count"],
            lambda count=10: git_ops.git_log(int(count))
        ))
        self.tool_registry.register(Tool(
            "git_create_branch",
            t(K.TOOL_GIT_CREATE_BRANCH, self.lang),
            ["name"],
            lambda name: git_ops.git_create_branch(name=name)
        ))
        self.tool_registry.register(Tool(
            "git_current_branch",
            t(K.TOOL_GIT_CURRENT_BRANCH, self.lang),
            [],
            lambda: git_ops.git_current_branch()
        ))
        self.tool_registry.register(Tool(
            "git_branch_list",
            t(K.TOOL_GIT_BRANCH_LIST, self.lang),
            [],
            lambda: git_ops.git_branch_list()
        ))
        self.tool_registry.register(Tool(
            "git_pull",
            t(K.TOOL_GIT_PULL, self.lang),
            ["remote", "branch"],
            lambda remote="origin", branch="master": git_ops.git_pull(remote=remote, branch=branch)
        ))
        self.tool_registry.register(Tool(
            "git_checkout",
            t(K.TOOL_GIT_CHECKOUT, self.lang),
            ["branch"],
            lambda branch: git_ops.git_checkout(branch=branch)
        ))

    TEMPLATE_TOOLS = {
        "resume": [],
        "kodeanalyse": [],
        "diffanalyse": ["git_diff", "git_log"],
        "fri": None,
        "agenten": [
            "github_create_pr",
            "git_status", "git_add_all", "git_commit", "git_push",
            "git_diff", "git_log",
            "git_create_branch", "git_current_branch", "git_pull", "git_checkout",
            "git_remote_status"
        ],
    }

    TEMPLATE_TASK_TOOLS = {
        "agenten": {
            "branch": ["git_current_branch", "git_create_branch", "git_branch_list", "git_checkout", "git_remote_status", "git_pull"],
            "commit": ["git_add_all", "git_commit", "git_status", "git_diff", "git_log"],
            "push": ["git_push", "git_remote_status"],
            "pull request": ["github_create_pr", "git_remote_status", "git_diff", "git_log"],
        }
    }

    def _get_templates(self):
        lang_instr = t(K.ANSWER_IN, self.lang)
        return {
            "resume": {
                "name": t(K.T_RESUME, self.lang),
                "prompt": t(K.TP_RESUME, self.lang).replace("{lang_instruction}", lang_instr),
                "fallback": t(K.TF_RESUME, self.lang),
            },
            "kodeanalyse": {
                "name": t(K.T_KODEANALYSE, self.lang),
                "prompt": t(K.TP_KODEANALYSE, self.lang).replace("{lang_instruction}", lang_instr),
                "fallback": t(K.TF_KODEANALYSE, self.lang),
            },
            "diffanalyse": {
                "name": t(K.T_DIFFANALYSE, self.lang),
                "prompt": t(K.TP_DIFFANALYSE, self.lang).replace("{lang_instruction}", lang_instr),
                "fallback": t(K.TF_DIFFANALYSE, self.lang),
            },
            "fri": {
                "name": t(K.T_FRI, self.lang),
                "prompt": t(K.TP_FRI, self.lang),
                "fallback": None
            },
            "agenten": {
                "name": t(K.T_AGENTEN, self.lang),
                "prompt": t(K.TP_AGENTEN, self.lang).replace("{lang_instruction}", lang_instr),
                "fallback": t(K.TF_AGENTEN, self.lang),
            },
        }

    def _log(self, level, message, detail=""):
        log_entry = {
            "timestamp": time.time(),
            "level": level,
            "message": message,
            "detail": detail[:200] if detail else ""
        }
        self.agent_log.append(log_entry)
        print(f"[{level}] {message}: {detail[:100]}")

    def _clean_task_name(self, name):
        name = re.sub(r'^[\*\-+]\s+', '', name.strip())
        name = re.sub(r'^\d+\.\s+', '', name)
        patterns = [
            r'<think>.*?</think>',
            r'Here\'s a thinking process:.*$',
            r'^\*\*.*\*\*$',
            r'^[•\-]\s*$',
            r'^Let\'s.*$',
            r'^Check.*$',
            r'^Draft:.*$',
            r'^No repetition.*$',
            r'^Drop politeness.*$',
            r'^Indentation:.*$',
            r'^Structure:.*$',
            r'^Task to break down:.*$',
            r'^Brainstorming.*$',
            r'^Main task:.*$',
            r'^Level \d+ steps.*$',
            r'^Let\'s break each down.*$',
            r'^Let\'s ensure.*$',
            r'^Udfør opgave.*$',
            r'^Analyze User Input:.*$',
            r'^Deconstruct Constraints.*$',
            r'^-\s*Task:.*$',
            r'^-\s*Input Task:.*$',
            r'^-\s*Example Provided:.*$',
            r'^-\s*Language:.*$',
            r'^-\s*Must be a tree structure.*$',
            r'^-\s*Output:.*$',
            r'^What does it mean to analyze.*$',
            r'^Wait, the example shows.*$',
            r'^This is a bit linear.*$',
            r'^Final check of the prompt.*$',
            r'^I will output exactly.*$',
            r'^Ready. Output matches exactly.*$',
            r'^✅$',
            r'^Nedbryd nu opgaven.*$',
            r'^KUN træstruktur.*$',
            r'^Returnér KUN træstruktur.*$',
            r'^Returner KUN træstruktur.*$',
            r'^Now break down the task.*$',
            r'^ONLY tree structure.*$',
            r'^Return ONLY the tree structure.*$',
            r'^Ahora descompón la tarea.*$',
            r'^SOLO estructura de árbol.*$',
            r'^Devuelve SOLO la estructura.*$',
            r'^\u73b0\u5728\u5206\u89e3\u4efb\u52a1.*$',
            r'^\u4ec5\u6811\u7ed3\u6784.*$',
            r'^<\|?channel\|?>.*$',
            r'^thought$',
            r'^namesearch$',
            r'^namesekundar.*$',
        ]
        for pattern in patterns:
            name = re.sub(pattern, '', name, flags=re.IGNORECASE | re.DOTALL)
        name = re.sub(r'\*\*', '', name)
        name = re.sub(r'`.*?`', '', name)
        name = re.sub(r'\s+', ' ', name).strip()
        if len(name) < 3 or name in ['', '-', '•', '*']:
            return None
        return name

    def _read_file_content(self, filepath):
        try:
            if not os.path.exists(filepath):
                return None
            with open(filepath, 'r', encoding='utf-8') as f:
                content = f.read()
            if len(content) > 50000:
                content = content[:50000] + "\n" + t(K.FILE_TRUNCATED, self.lang)
            return content
        except Exception as e:
            self._log("ERROR", t(K.LOG_READ_ERROR, self.lang), str(e))
            return None

    def _get_file_context(self, prompt):
        file_match = re.search(r'analyser\s+([^\s]+\.py)', prompt, re.IGNORECASE)
        if not file_match:
            return None, None

        filename = file_match.group(1)
        self._log("INFO", t(K.LOG_READING_FILE, self.lang), filename)

        base_dir = os.path.dirname(os.path.abspath(__file__))
        possible_paths = [
            filename,
            os.path.join(base_dir, filename),
            os.path.join(base_dir, 'static', filename),
            os.path.join(base_dir, 'sessions', filename),
            os.path.join(os.getcwd(), filename),
            os.path.join(base_dir, '..', filename),
        ]

        for path in possible_paths:
            if os.path.exists(path):
                content = self._read_file_content(path)
                if content:
                    self._log("INFO", t(K.LOG_FILE_FOUND, self.lang), path)
                    return path, content

        self._log("WARNING", t(K.LOG_FILE_NOT_FOUND, self.lang), filename)
        return None, None

    def _create_fallback_tree(self, prompt):
        tree = TaskTree(prompt)
        self.original_prompt = prompt
        prompt_lower = prompt.lower()

        if "analyser" in prompt_lower and (".py" in prompt_lower or "api_server" in prompt_lower):
            tree.root.add_child(TaskNode(t(K.FT_UNDERSTAND_PURPOSE, self.lang)))
            tree.root.children[0].add_child(TaskNode(t(K.FT_READ_IMPORTS, self.lang)))
            tree.root.children[0].add_child(TaskNode(t(K.FT_IDENTIFY_FRAMEWORKS, self.lang)))
            tree.root.add_child(TaskNode(t(K.FT_ANALYZE_STRUCTURE, self.lang)))
            tree.root.children[1].add_child(TaskNode(t(K.FT_REVIEW_ENDPOINTS, self.lang)))
            tree.root.children[1].add_child(TaskNode(t(K.FT_CHECK_CONFIG, self.lang)))
            tree.root.add_child(TaskNode(t(K.FT_ASSESS_QUALITY, self.lang)))
            tree.root.children[2].add_child(TaskNode(t(K.FT_SECURITY_ANALYSIS, self.lang)))
            tree.root.children[2].add_child(TaskNode(t(K.FT_ERROR_HANDLING, self.lang)))
            tree.root.add_child(TaskNode(t(K.FT_DOCUMENT_FINDINGS, self.lang)))
        elif "2 + 2" in prompt_lower or "2 plus 2" in prompt_lower:
            tree.root.add_child(TaskNode(t(K.FT_UNDERSTAND_NUMBERS, self.lang)))
            tree.root.add_child(TaskNode(t(K.FT_PERFORM_ADDITION, self.lang)))
            tree.root.add_child(TaskNode(t(K.FT_CONCLUDE, self.lang)))
        else:
            tree.root.add_child(TaskNode(t(K.FT_ANALYZE_PROBLEM, self.lang)))
            tree.root.add_child(TaskNode(t(K.FT_FIND_STRATEGY, self.lang)))
            tree.root.add_child(TaskNode(t(K.FT_IMPLEMENT_SOLUTION, self.lang)))
            tree.root.add_child(TaskNode(t(K.FT_TEST_VALIDATE, self.lang)))
        return tree

    def _parse_tree_from_llm(self, prompt, llm_response):
        tree = TaskTree(prompt)
        if llm_response.startswith("ERROR") or not llm_response.strip():
            self._log("ERROR", t(K.LOG_LLM_ERROR_FALLBACK, self.lang), llm_response[:100] if llm_response else t(K.LOG_EMPTY_RESPONSE, self.lang))
            return self._create_fallback_tree(prompt)

        lines = llm_response.strip().split('\n')
        stack = [(tree.root, 0)]
        added_count = 0

        for line in lines:
            if not line.strip():
                continue
            stripped = line.lstrip(' ')
            indent = len(line) - len(stripped)
            task_name = self._clean_task_name(stripped)
            if task_name is None:
                continue
            skip_words = ['think', 'thinking', 'brainstorm', 'draft', 'constraint',
                         'repetition', 'politeness', 'indentation', 'compliance',
                         'thought', 'channel', 'namesekundar', 'namesearch',
                         'analyze user input', 'deconstruct', 'example provided',
                         'must be a tree', 'output:', 'language:']
            if any(word in task_name.lower() for word in skip_words):
                continue
            if not task_name or len(task_name) < 3:
                continue
            level = indent // 2
            while len(stack) > level + 1:
                stack.pop()
            parent = stack[-1][0]
            new_node = TaskNode(task_name[:80])
            parent.add_child(new_node)
            stack.append((new_node, level))
            added_count += 1

        if added_count == 0:
            self._log("WARNING", t(K.LOG_NO_VALID_TASKS, self.lang), "")
            return self._create_fallback_tree(prompt)

        self._log("INFO", t(K.LOG_PARSED_TASKS, self.lang).format(n=added_count), "")
        return tree

    def decompose_prompt(self, prompt, files=None, template=None):
        self.agent_log = []
        self.original_prompt = prompt
        self.tool_registry.lang = self.lang
        templates = self._get_templates()
        template_config = templates.get(template, templates["fri"]) if template else templates["fri"]
        self.active_template = template
        allowed = self.TEMPLATE_TOOLS.get(template) if template else None
        self.tool_registry.set_active_tools(allowed)
        self._log("INFO", t(K.LOG_DECOMPOSE_START, self.lang), f"{prompt[:100]} ({t('ui.using_template', self.lang).format(name=template_config['name'])})")

        self.file_context = files or []

        file_context = ""
        if files and len(files) > 0:
            file_context = t(K.FILE_CONTEXT_HEADER, self.lang)
            for f in files:
                filename = f.get('filename', t(K.UNKNOWN, self.lang))
                content = f.get('content', '')
                file_context += f"\n### {filename}\n\n```{filename}\n{content}\n```\n"
            self._log("INFO", t(K.LOG_ADDING_FILES, self.lang), t(K.LOG_N_FILES, self.lang).format(n=len(files)))
        else:
            file_path, file_content = self._get_file_context(prompt)
            if file_content:
                file_context = t(K.FILE_CONTEXT_HEADER, self.lang) + os.path.basename(file_path) + t(K.FILE_CONTEXT_PYTHON, self.lang).replace("{content}", file_content)

        self.full_prompt_with_context = prompt + file_context

        if template and template != "fri" and template_config.get("fallback"):
            if template_config.get("fallback"):
                tree = TaskTree(prompt)
                for section in template_config["fallback"]:
                    tree.root.add_child(TaskNode(section))
                self.task_tree = tree
                task_count = len(template_config["fallback"]) + 1
                self._log("INFO", t(K.LOG_USING_TEMPLATE, self.lang), t(K.LOG_TASKS_CREATED, self.lang).format(n=task_count))
                return self.task_tree_to_dict()

        decomposition_prompt = template_config["prompt"].replace("{prompt}", prompt)
        file_context_entry = f"\n\nMateriale:{file_context}" if file_context else ""
        decomposition_prompt += file_context_entry

        if "gemma" in self.decompose_llm.model.lower():
            decomposition_prompt += "\n<|channel>thought\n<channel|>"

        self._log("LLM", t(K.LOG_SENDING_LLM, self.lang), t(K.LOG_N_FILES, self.lang).format(n=len(self.file_context)) if isinstance(self.file_context, list) and self.file_context else "")
        response = self.decompose_llm.generate(decomposition_prompt, temperature=0.3, max_tokens=4096)
        self._log("LLM", t(K.LOG_RECEIVED_LLM, self.lang), t(K.LOG_N_CHARS, self.lang).format(n=len(response)))

        response = re.sub(r'<think>.*?</think>', '', response, flags=re.DOTALL)
        response = re.sub(r'<\|channel>thought\s*<channel\|>.*?(?=<\|channel>|\Z)', '', response, flags=re.DOTALL)
        response = re.sub(r'<\|?channel\|?>.*$', '', response, flags=re.MULTILINE)

        self.task_tree = self._parse_tree_from_llm(prompt, response)
        task_count = self._count_tasks(self.task_tree.root)
        self._log("INFO", t(K.LOG_DECOMPOSE_DONE, self.lang), t(K.LOG_TASKS_CREATED, self.lang).format(n=task_count))

        return self.task_tree_to_dict()

    def reset_execution(self):
        if not self.task_tree:
            return
        def reset_node(node):
            node.status = "pending"
            node.result = None
            for child in node.children:
                reset_node(child)
        reset_node(self.task_tree.root)
        self.execution_log = []
        self._log("INFO", t(K.LOG_EXECUTION_RESET, self.lang), "")

    def _count_tasks(self, node):
        count = 1
        for child in node.children:
            count += self._count_tasks(child)
        return count

    def task_tree_to_dict(self):
        if not self.task_tree or not self.task_tree.root:
            return None
        
        def node_to_dict(node):
            return {
                "name": node.name,
                "status": node.status,
                "children": [node_to_dict(child) for child in node.children] if node.children else []
            }
        
        return node_to_dict(self.task_tree.root)

    def task_tree_from_dict(self, d):
        from task_tree import TaskTree, TaskNode
        def dict_to_node(item):
            node = TaskNode(item["name"])
            node.status = item.get("status", "pending")
            for child_data in item.get("children", []):
                node.add_child(dict_to_node(child_data))
            return node
        self.task_tree = TaskTree("temp")
        self.task_tree.root = dict_to_node(d)

    # Helper functions (makes main function more readable)
    def _truncate_conversation(self, conversation, system_prompt):
        if len(conversation) > self.max_conversation_chars and len(system_prompt) < self.max_conversation_chars:
            mid = "\n\n[... tidligere kontekst afkortet / previous context truncated...]"
            keep = self.max_conversation_chars - len(system_prompt) - len(mid)
            if keep > 0:
                return system_prompt + mid + conversation[-keep:]
        return conversation

    def _build_tool_guidance(self, attempt):
        """Builds guidance for the AI about which tools it can use"""
        if attempt > 0:
            return ""
        
        tool_list = ', '.join(self.tool_registry.tools.keys())
        if self.tool_registry.active_tools is not None:
            tool_list = ', '.join(self.tool_registry.active_tools)
        return f"\n\n" + t(K.TOOL_CONTINUATION, self.lang).format(
            tools_list=tool_list,
            TOOL_MARKER=self.tool_registry.TOOL_MARKER,
            DONE_MARKER=self.tool_registry.DONE_MARKER
        )

    def _ask_ai(self, prompt):
        """Ask the AI model and return the response"""
        response = ""
        for chunk in self.llm.generate_stream(prompt, temperature=0.3, max_tokens=self.max_tokens):
            response += chunk
        return response

    def _handle_tool_call(self, action, conversation, already_called, attempt):
        """Executes a tool call from the AI"""
        tool_name = action["tool"]
        arguments = action.get("args", {})
        
        # Check if tool was recently called (avoid loops)
        key = f"{tool_name}_{arguments}"
        times_called = already_called.get(key, 0)
        already_called[key] = times_called + 1
        
        if times_called >= 2:
            conversation += f"\n\n{t(K.SYS_ERROR_PREFIX, self.lang)}: {t(K.TOOL_DUPLICATE_MSG, self.lang).format(tool=tool_name)}"
            return conversation
        
        # Execute the tool
        result = self.tool_registry.execute(tool_name, arguments)
        result_text = json.dumps(result, ensure_ascii=False)
        
        # Tell the AI the result
        conversation += f"\n\n{t(K.TOOL_RESULT_PREFIX, self.lang).format(tool=tool_name)}\n{result_text}"
        return conversation

    def _set_task_tools(self, task_name):
        if not self.active_template or self.active_template not in self.TEMPLATE_TASK_TOOLS:
            return
        template_tools = self.TEMPLATE_TASK_TOOLS[self.active_template]
        for keyword, tools in template_tools.items():
            if keyword in task_name.lower():
                self.tool_registry.set_active_tools(tools)
                self._log("TOOL", f"Aktive tools for '{task_name[:40]}'", ', '.join(tools))
                return
        allowed = self.TEMPLATE_TOOLS.get(self.active_template)
        if allowed is not None:
            self.tool_registry.set_active_tools(allowed)

    def _is_pr_workflow(self, task_name):
        if re.search(r'\bpr\b', task_name, re.IGNORECASE):
            return True
        keywords = ["pull request", "github", "push og opret", "push and create"]
        return any(k in task_name.lower() for k in keywords)

    def _extract_branch_name(self, task_name, original_prompt):
        m = re.search(r"branch\s*['\"]?([\w\-\/]+)['\"]?", original_prompt, re.IGNORECASE)
        if m:
            return m.group(1)
        m = re.search(r"branch\s*['\"]?([\w\-\/]+)['\"]?", task_name, re.IGNORECASE)
        if m:
            return m.group(1)
        return ""

    def _verify_pr_step(self, tool_name, result, task_name, original_prompt):
        if not self._is_pr_workflow(task_name):
            return None

        if tool_name == "github_create_pr":
            called_set = {t.split("{")[0] for t in self._checkpoint_tools}
            missing = PR_REQUIRED_BEFORE_PR - called_set
            if missing:
                return t(K.CP_NO_COMMIT, self.lang)

        result_ok = result.get("success", False) and result.get("result", {}).get("success", True) is not False
        if not result_ok:
            err = result.get("error") or result.get("result", {}).get("error", "ukendt fejl")
            err_str = str(err)
            if tool_name in PR_BRANCH_TOOLS and "already exists" in err_str:
                expected = self._extract_branch_name(task_name, original_prompt)
                return f"Branch '{expected}' findes allerede. Brug git_checkout(branch='{expected}') i stedet for at oprette den igen."
            if tool_name == "github_create_pr":
                return t(K.CP_PR_FAILED, self.lang)
            return t(K.CP_TOOL_FAILED, self.lang).format(tool=tool_name, error=err_str[:100])

        if tool_name in PR_BRANCH_TOOLS:
            expected = self._extract_branch_name(task_name, original_prompt)
            actual = result.get("result", {}).get("error", "")
            m = re.search(r"Switched to a new branch '([^']+)'", actual)
            if m:
                actual_branch = m.group(1)
            else:
                actual_branch = result.get("args", {}).get("name", "")
            if expected and actual_branch and actual_branch != expected:
                return t(K.CP_BRANCH_NAME, self.lang).format(actual=actual_branch, expected=expected)
            self._checkpoint_branch = actual_branch or expected

        if tool_name == "github_create_pr":
            url = result.get("result", {}).get("url", "")
            if not url:
                return t(K.CP_PR_FAILED, self.lang)

        return None

    def solve_task(self, task_node, original_prompt):
        # 1. Start the task
        task_node.status = "running"
        self._log("INFO", f"Starting task: {task_node.name}")
        
        # 2. Prepare system prompt (instructions for the AI)
        system_prompt = self.tool_registry.build_system_prompt(task_node.name)
        conversation = system_prompt
        
        # 3. AI can call tools (e.g., read file, search the web)
        max_attempts = 5
        already_called_tools = {}
        answer = ""
        
        for attempt in range(max_attempts):
            # 3a. Ask the AI
            prompt = conversation + self._build_tool_guidance(attempt)
            ai_response = self._ask_ai(prompt)
            
            # 3b. Understand what the AI wants to do
            action = self.tool_registry.parse_response(ai_response)
            
            # 3c. Handle different action types
            if action["type"] == "tool":
                # AI wants to use a tool
                conversation = self._handle_tool_call(action, conversation, already_called_tools, attempt)
                
            elif action["type"] == "done":
                # AI is finished with the task
                answer = action["result"]
                break
                
            elif action["type"] == "error":
                conversation += f"\n\n{t(K.SYS_ERROR_PREFIX, self.lang)}: {action['message']}"
            
            elif action["type"] == "text":
                conversation += f"\n\n{t(K.SYS_ERROR_PREFIX, self.lang)}: {t(K.TOOL_NO_RESULT, self.lang)}"
            
            tool_for_msg = self.tool_registry.active_tools[0] if self.tool_registry.active_tools else t(K.SYS_FALLBACK_TOOL, self.lang)
            if attempt == 0 and not already_called_tools and action["type"] != "done":
                conversation += f"\n\n{t(K.SYS_ERROR_PREFIX, self.lang)}: {t(K.FIRST_TOOL_REQUIRED, self.lang).format(tool=tool_for_msg)}"
            
            conversation = self._truncate_conversation(conversation, system_prompt)
        
        # 4. Finish the task
        if not answer:
            answer = "Task failed"
            task_node.status = "failed"
        else:
            task_node.status = "done"
        
        task_node.result = answer
        return answer

    def solve_task_stream(self, task_node, original_prompt):
        task_node.status = "running"
        self._log("INFO", t(K.LOG_TASK_START, self.lang), f"{task_node.name} (model: {self.llm.model})")
        self._set_task_tools(task_node.name)
        self._checkpoint_tools = set()
        self._checkpoint_branch = ""

        system_prompt = self.tool_registry.build_system_prompt(task_node.name)

        conversation = system_prompt
        full_response = ""
        max_iterations = 15 if self._is_pr_workflow(task_node.name) else 5
        called_tools = {}

        for i in range(max_iterations):
            if self.stop_requested:
                break
            prompt = conversation
            if i == 0:
                tools_list = ', '.join([k for k in self.tool_registry.tools if self.tool_registry.active_tools is None or k in self.tool_registry.active_tools])
                prompt += f"\n\n" + t(K.TOOL_CONTINUATION, self.lang).format(
                    tools_list=tools_list,
                    TOOL_MARKER=self.tool_registry.TOOL_MARKER,
                    DONE_MARKER=self.tool_registry.DONE_MARKER
                )

            response = ""
            for chunk in self.llm.generate_stream(prompt, temperature=0.3, max_tokens=self.max_tokens):
                if self.stop_requested:
                    break
                response += chunk
                yield {"type": "chunk", "chunk": chunk}

            if self.stop_requested:
                break

            parsed = self.tool_registry.parse_response(response)
            self._log("LLM", t(K.LOG_ITERATION, self.lang).format(n=i+1), t(K.LOG_TYPE, self.lang).format(type=parsed.get('type')))

            if parsed["type"] == "tool":
                tool_key = parsed['tool'] + str(parsed.get('args', {}))
                dup_count = called_tools.get(tool_key, 0)
                called_tools[tool_key] = dup_count + 1
                
                if dup_count >= 2:
                    conversation += f"\n\n{t(K.SYS_ERROR_PREFIX, self.lang)}: Du har allerede dette resultat. Gå videre eller brug <<<DONE>>>."
                    conversation = self._truncate_conversation(conversation, system_prompt)
                    continue
                
                if dup_count == 1:
                    self._log("TOOL", t(K.TOOL_DUPLICATE, self.lang), parsed['tool'])
                
                self._log("TOOL", t(K.LOG_TOOL_CALLING, self.lang).format(tool=parsed['tool']), str(parsed.get("args", {}))[:100])
                result = self.tool_registry.execute(parsed["tool"], parsed["args"])
                result_str = json.dumps(result, ensure_ascii=False)
                self._log("TOOL", t(K.LOG_TOOL_RESULT, self.lang).format(tool=parsed['tool']), result_str[:200])
                yield {"type": "tool_call", "tool": parsed["tool"], "args": parsed.get("args", {})}
                yield {"type": "tool_result", "tool": parsed["tool"], "result": result}

                checkpoint_msg = self._verify_pr_step(parsed["tool"], result, task_node.name, original_prompt)
                if checkpoint_msg:
                    conversation += f"\n\n!!! CHECKPOINT - {checkpoint_msg}"
                    self._log("INFO", "CHECKPOINT", checkpoint_msg)
                    yield {"type": "checkpoint", "message": checkpoint_msg, "tool": parsed["tool"]}
                else:
                    self._checkpoint_tools.add(parsed["tool"] + str(parsed.get("args", {})))
                    conversation += f"\n\n" + t(K.TOOL_RESULT_PREFIX, self.lang).format(tool=parsed['tool']) + f"\n{result_str}"

                conversation = self._truncate_conversation(conversation, system_prompt)
                continue

            if parsed["type"] == "done":
                if self._is_pr_workflow(task_node.name) and not called_tools:
                    conversation += f"\n\n{t(K.SYS_ERROR_PREFIX, self.lang)}: Du kaldte <<<DONE>>> uden at bruge nogen værktøjer. Brug værktøjerne først."
                    conversation = self._truncate_conversation(conversation, system_prompt)
                    continue

                if self._is_pr_workflow(task_node.name):
                    called_names = {t.split("{")[0] for t in self._checkpoint_tools}
                    if "github_create_pr" not in called_names:
                        conversation += f"\n\n!!! CHECKPOINT - {t(K.CP_PR_FAILED, self.lang)}"
                        self._log("INFO", "CHECKPOINT", t(K.CP_PR_FAILED, self.lang))
                        yield {"type": "checkpoint", "message": t(K.CP_PR_FAILED, self.lang), "tool": "done"}
                        conversation = self._truncate_conversation(conversation, system_prompt)
                        continue
                    missing_commit = PR_COMMIT_TOOLS - called_names
                    if missing_commit:
                        conversation += f"\n\n!!! CHECKPOINT - {t(K.CP_NO_COMMIT, self.lang)}"
                        self._log("INFO", "CHECKPOINT", t(K.CP_NO_COMMIT, self.lang))
                        yield {"type": "checkpoint", "message": t(K.CP_NO_COMMIT, self.lang), "tool": "done"}
                        conversation = self._truncate_conversation(conversation, system_prompt)
                        continue
                    if "git_push" not in called_names:
                        conversation += f"\n\n!!! CHECKPOINT - {t(K.CP_NO_PUSH, self.lang)}"
                        self._log("INFO", "CHECKPOINT", t(K.CP_NO_PUSH, self.lang))
                        yield {"type": "checkpoint", "message": t(K.CP_NO_PUSH, self.lang), "tool": "done"}
                        conversation = self._truncate_conversation(conversation, system_prompt)
                        continue

                full_response = parsed["result"]
                break

            if parsed["type"] == "error":
                conversation += f"\n\n{t(K.SYS_ERROR_PREFIX, self.lang)}: {parsed['message']}"
                conversation = self._truncate_conversation(conversation, system_prompt)
                continue

            if i == 0 and not called_tools and parsed["type"] != "done":
                tool_for_msg = self.tool_registry.active_tools[0] if self.tool_registry.active_tools else t(K.SYS_FALLBACK_TOOL, self.lang)
                conversation += f"\n{t(K.SYS_ERROR_PREFIX, self.lang)}: {t(K.FIRST_TOOL_REQUIRED, self.lang).format(tool=tool_for_msg)}"
                conversation = self._truncate_conversation(conversation, system_prompt)
                continue

            conversation += f"\n\n" + t(K.TOOL_NO_RESULT, self.lang)
            conversation = self._truncate_conversation(conversation, system_prompt)
            full_response = response
            if i >= 3:
                break

        if not full_response or "ERROR" in full_response:
            full_response = t(K.LOG_TASK_FAILED, self.lang)
            task_node.status = "failed"
        else:
            task_node.status = "done"

        task_node.result = full_response
        self.action_history.append(task_node.name.split()[0] if task_node.name else K.UNKNOWN)
        if task_node.status == "failed":
            self._log("INFO", t(K.LOG_TASK_FAILED, self.lang), task_node.name)
        else:
            self._log("INFO", t(K.LOG_TASK_DONE, self.lang), task_node.name)
        yield {"type": "done", "result": full_response}

    def execute_tree(self, node=None):
        if node is None:
            if not self.task_tree:
                return {"error": "No task tree"}
            self._log("INFO", t(K.LOG_TREE_EXECUTION, self.lang), "")
            node = self.task_tree.root
        results = {}
        for child in node.children:
            child_results = self.execute_tree(child)
            results[child.name] = child_results
        results[node.name] = self.solve_task(node, self.original_prompt)
        return results

    def get_agent_status(self):
        return {
            "action_history": self.action_history,
            "total_actions": len(self.action_history),
            "log_entries": len(self.agent_log),
            "has_task_tree": self.task_tree is not None
        }

    def suggest_new_module(self):
        return {"message": t(K.LOG_MODULE_READY, self.lang)}
