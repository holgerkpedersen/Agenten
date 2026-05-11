from llm_wrapper import LMStudioWrapper
from web_searcher import WebSearcher
from task_tree import TaskTree, TaskNode
from module_builder import ModuleBuilder
from tools import Tool, ToolRegistry
from github_wrapper import GithubAPI
from lang import t
import git_ops
import re
import time
import os
import json

class Agent:
    def __init__(self):
        self.llm = LMStudioWrapper(timeout=120)
        self.searcher = WebSearcher()
        self.task_tree = None
        self.action_history = []
        self.execution_log = []
        self.agent_log = []
        self.original_prompt = ""
        self.full_prompt_with_context = ""
        self.show_thinking = True
        self.lang = "da"
        self.tool_registry = ToolRegistry()
        self._register_tools()

    def _register_tools(self):
        gh = GithubAPI()
        self.tool_registry.register(Tool(
            "github_create_repo",
            t("tools.github_create_repo", self.lang),
            ["repo_navn", "beskrivelse", "privat"],
            lambda repo_navn, beskrivelse="", privat=False: gh.create_repo(name=repo_navn, description=beskrivelse, private=privat)
        ))
        self.tool_registry.register(Tool(
            "github_list_repos",
            t("tools.github_list_repos", self.lang),
            [],
            lambda: gh.list_repos()
        ))
        self.tool_registry.register(Tool(
            "github_create_issue",
            t("tools.github_create_issue", self.lang),
            ["ejer", "repo", "titel", "body"],
            lambda ejer, repo, titel, body="": gh.create_issue(owner=ejer, repo=repo, title=titel, body=body)
        ))
        self.tool_registry.register(Tool(
            "github_create_pr",
            t("tools.github_create_pr", self.lang),
            ["ejer", "repo", "titel", "branch"],
            lambda ejer, repo, titel, branch, base="master": gh.create_pr(owner=ejer, repo=repo, title=titel, head=branch, base=base)
        ))
        self.tool_registry.register(Tool(
            "git_status",
            t("tools.git_status", self.lang),
            [],
            lambda: git_ops.git_status()
        ))
        self.tool_registry.register(Tool(
            "git_add_all",
            t("tools.git_add_all", self.lang),
            [],
            lambda: git_ops.git_add_all()
        ))
        self.tool_registry.register(Tool(
            "git_commit",
            t("tools.git_commit", self.lang),
            ["besked"],
            lambda besked: git_ops.git_commit(message=besked)
        ))
        self.tool_registry.register(Tool(
            "git_push",
            t("tools.git_push", self.lang),
            ["branch"],
            lambda branch="master": git_ops.git_push(branch=branch)
        ))
        self.tool_registry.register(Tool(
            "git_set_remote",
            t("tools.git_set_remote", self.lang),
            ["url"],
            lambda url: git_ops.git_set_remote(url=url)
        ))
        self.tool_registry.register(Tool(
            "git_remote_status",
            t("tools.git_remote_status", self.lang),
            [],
            lambda: git_ops.git_remote_exists()
        ))
        self.tool_registry.register(Tool(
            "git_diff",
            t("tools.git_diff", self.lang),
            ["older", "newer"],
            lambda older="HEAD~1", newer="HEAD": git_ops.git_diff(older, newer)
        ))
        self.tool_registry.register(Tool(
            "git_log",
            t("tools.git_log", self.lang),
            ["count"],
            lambda count=10: git_ops.git_log(int(count))
        ))

    TEMPLATE_TOOLS = {
        "resume": [],
        "kodeanalyse": [],
        "diffanalyse": ["git_diff", "git_log"],
        "fri": None,
    }

    def _get_templates(self):
        lang_instr = t("answer_in", self.lang)
        return {
            "resume": {
                "name": t("templates.resume", self.lang),
                "prompt": t("template_prompts.resume", self.lang).replace("{lang_instruction}", lang_instr),
                "fallback": t("template_fallback.resume", self.lang),
            },
            "kodeanalyse": {
                "name": t("templates.kodeanalyse", self.lang),
                "prompt": t("template_prompts.kodeanalyse", self.lang).replace("{lang_instruction}", lang_instr),
                "fallback": t("template_fallback.kodeanalyse", self.lang),
            },
            "diffanalyse": {
                "name": t("templates.diffanalyse", self.lang),
                "prompt": t("template_prompts.diffanalyse", self.lang).replace("{lang_instruction}", lang_instr),
                "fallback": t("template_fallback.diffanalyse", self.lang),
            },
            "fri": {
                "name": t("templates.fri", self.lang),
                "prompt": t("template_prompts.fri", self.lang),
                "fallback": None
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
                content = content[:50000] + "\n... (filen er trunkeret)"
            return content
        except Exception as e:
            self._log("ERROR", "Kunne ikke læse fil", str(e))
            return None

    def _get_file_context(self, prompt):
        file_match = re.search(r'analyser\s+([^\s]+\.py)', prompt, re.IGNORECASE)
        if not file_match:
            return None, None

        filename = file_match.group(1)
        self._log("INFO", "Forsøger at læse fil", filename)

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
                    self._log("INFO", "Fil fundet og læst", path)
                    return path, content

        self._log("WARNING", "Fil ikke fundet", filename)
        return None, None

    def _create_fallback_tree(self, prompt):
        tree = TaskTree(prompt)
        self.original_prompt = prompt
        prompt_lower = prompt.lower()

        if "analyser" in prompt_lower and (".py" in prompt_lower or "api_server" in prompt_lower):
            tree.root.add_child(TaskNode("Forstå filens formål"))
            tree.root.children[0].add_child(TaskNode("Læs filens imports"))
            tree.root.children[0].add_child(TaskNode("Identificer anvendte rammer"))
            tree.root.add_child(TaskNode("Analyser kodestrukturen"))
            tree.root.children[1].add_child(TaskNode("Gennemgå endpoints/routes"))
            tree.root.children[1].add_child(TaskNode("Tjek konfiguration"))
            tree.root.add_child(TaskNode("Vurder kodekvalitet"))
            tree.root.children[2].add_child(TaskNode("Sikkerhedsanalyse"))
            tree.root.children[2].add_child(TaskNode("Fejlhåndtering"))
            tree.root.add_child(TaskNode("Dokumentér fund"))
        elif "2 + 2" in prompt_lower or "2 plus 2" in prompt_lower:
            tree.root.add_child(TaskNode("Forstå hvad tal repræsenterer"))
            tree.root.add_child(TaskNode("Udfør additionen"))
            tree.root.add_child(TaskNode("Konkluder"))
        else:
            tree.root.add_child(TaskNode("Analyser problemet"))
            tree.root.add_child(TaskNode("Find løsningsstrategi"))
            tree.root.add_child(TaskNode("Implementer løsningen"))
            tree.root.add_child(TaskNode("Test og valider"))
        return tree

    def _parse_tree_from_llm(self, prompt, llm_response):
        tree = TaskTree(prompt)
        if llm_response.startswith("ERROR") or not llm_response.strip():
            self._log("ERROR", "LLM fejl, bruger fallback", llm_response[:100] if llm_response else "Tom svar")
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
            self._log("WARNING", "Ingen gyldige opgaver fundet, bruger fallback", "")
            return self._create_fallback_tree(prompt)

        self._log("INFO", f"Parsede {added_count} opgaver fra LLM", "")
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
        self._log("INFO", t("log.decompose_start", self.lang), f"{prompt[:100]} ({t('ui.using_template', self.lang).format(name=template_config['name'])})")

        file_context = ""
        if files and len(files) > 0:
            file_context = t("file_context_header", self.lang)
            for f in files:
                filename = f.get('filename', t("unknown", self.lang))
                content = f.get('content', '')
                file_context += f"\n### {filename}\n\n```{filename}\n{content}\n```\n"
            self._log("INFO", t("log.adding_files", self.lang), t("log.N_files", self.lang).format(n=len(files)))
        else:
            file_path, file_content = self._get_file_context(prompt)
            if file_content:
                file_context = t("file_context_header", self.lang) + os.path.basename(file_path) + t("file_context_python", self.lang).replace("{content}", file_content)

        self.full_prompt_with_context = prompt + file_context

        if template and template != "fri" and template_config.get("fallback"):
            if template_config.get("fallback"):
                tree = TaskTree(prompt)
                for section in template_config["fallback"]:
                    tree.root.add_child(TaskNode(section))
                self.task_tree = tree
                task_count = len(template_config["fallback"]) + 1
                self._log("INFO", t("log.using_template", self.lang), t("log.tasks_created", self.lang).format(n=task_count))
                return self.task_tree_to_dict()

        decomposition_prompt = template_config["prompt"].replace("{prompt}", prompt)
        file_context_entry = f"\n\nMateriale:{file_context}" if file_context else ""
        decomposition_prompt += file_context_entry

        self._log("LLM", t("log.sending_llm", self.lang), f"Med filkontekst: {bool(file_context)}")
        response = self.llm.generate(decomposition_prompt, temperature=0.3, max_tokens=4096)
        self._log("LLM", t("log.received_llm", self.lang), t("log.N_chars", self.lang).format(n=len(response)))

        response = re.sub(r'<think>.*?</think>', '', response, flags=re.DOTALL)

        self.task_tree = self._parse_tree_from_llm(prompt, response)
        task_count = self._count_tasks(self.task_tree.root)
        self._log("INFO", t("log.decompose_done", self.lang), t("log.tasks_created", self.lang).format(n=task_count))

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
        self._log("INFO", t("log.execution_reset", self.lang), "")

    def _count_tasks(self, node):
        count = 1
        for child in node.children:
            count += self._count_tasks(child)
        return count

    def task_tree_to_dict(self):
        if not self.task_tree:
            return None
        def node_to_dict(node):
            return {"name": node.name, "status": node.status, "children": [node_to_dict(child) for child in node.children]}
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

    def solve_task(self, task_node, original_prompt):
            task_node.status = "running"
            self._log("INFO", t("log.task_start", self.lang), task_node.name)

            system_prompt = self.tool_registry.build_system_prompt(task_node.name)

            conversation = system_prompt
            full_response = ""
            max_iterations = 8

            for i in range(max_iterations):
                prompt = conversation
                if i > 0:
                    tools_list = ', '.join([k for k in self.tool_registry.tools if self.tool_registry.active_tools is None or k in self.tool_registry.active_tools])
                    prompt += f"\n\n" + t("tool_continuation", self.lang).format(
                        tools_list=tools_list,
                        TOOL_MARKER=self.tool_registry.TOOL_MARKER,
                        DONE_MARKER=self.tool_registry.DONE_MARKER
                    )

                response = ""
                for chunk in self.llm.generate_stream(prompt, max_tokens=1024):
                    response += chunk

                parsed = self.tool_registry.parse_response(response)
                self._log("LLM", t("log.iteration", self.lang).format(n=i+1), t("log.type", self.lang).format(type=parsed.get('type')))

                if parsed["type"] == "tool":
                    self._log("TOOL", t("log.tool_calling", self.lang).format(tool=parsed['tool']), str(parsed.get("args", {}))[:100])
                    result = self.tool_registry.execute(parsed["tool"], parsed["args"])
                    result_str = json.dumps(result, ensure_ascii=False)
                    self._log("TOOL", t("log.tool_result", self.lang).format(tool=parsed['tool']), result_str[:200])
                    conversation += f"\n\n" + t("tool_result_prefix", self.lang).format(tool=parsed['tool']) + f"\n{result_str}"
                    continue

                if parsed["type"] == "done":
                    full_response = parsed["result"]
                    break

                if parsed["type"] == "error":
                    conversation += f"\n\nFEJL: {parsed['message']}"
                    continue

                conversation += f"\n\n" + t("tool_no_result", self.lang)
                full_response = response
                if i >= 3:
                    break

            if not full_response or "ERROR" in full_response:
                full_response = t("log.task_failed", self.lang)

            task_node.status = "done"
            task_node.result = full_response
            self.action_history.append(task_node.name.split()[0] if task_node.name else "unknown")
            self._log("INFO", t("log.task_done", self.lang), task_node.name)
            return full_response

    def solve_task_stream(self, task_node, original_prompt):
            task_node.status = "running"
            self._log("INFO", t("log.task_start", self.lang), task_node.name)

            system_prompt = self.tool_registry.build_system_prompt(task_node.name)

            conversation = system_prompt
            full_response = ""
            max_iterations = 8

            for i in range(max_iterations):
                prompt = conversation
                if i > 0:
                    tools_list = ', '.join([k for k in self.tool_registry.tools if self.tool_registry.active_tools is None or k in self.tool_registry.active_tools])
                    prompt += f"\n\n" + t("tool_continuation", self.lang).format(
                        tools_list=tools_list,
                        TOOL_MARKER=self.tool_registry.TOOL_MARKER,
                        DONE_MARKER=self.tool_registry.DONE_MARKER
                    )

                response = ""
                for chunk in self.llm.generate_stream(prompt, max_tokens=1024):
                    response += chunk
                    yield {"type": "chunk", "chunk": chunk}

                parsed = self.tool_registry.parse_response(response)
                self._log("LLM", t("log.iteration", self.lang).format(n=i+1), t("log.type", self.lang).format(type=parsed.get('type')))

                if parsed["type"] == "tool":
                    self._log("TOOL", t("log.tool_calling", self.lang).format(tool=parsed['tool']), str(parsed.get("args", {}))[:100])
                    result = self.tool_registry.execute(parsed["tool"], parsed["args"])
                    result_str = json.dumps(result, ensure_ascii=False)
                    self._log("TOOL", t("log.tool_result", self.lang).format(tool=parsed['tool']), result_str[:200])
                    yield {"type": "tool_call", "tool": parsed["tool"], "args": parsed.get("args", {})}
                    yield {"type": "tool_result", "tool": parsed["tool"], "result": result}
                    conversation += f"\n\n" + t("tool_result_prefix", self.lang).format(tool=parsed['tool']) + f"\n{result_str}"
                    continue

                if parsed["type"] == "done":
                    full_response = parsed["result"]
                    break

                if parsed["type"] == "error":
                    conversation += f"\n\nFEJL: {parsed['message']}"
                    continue

                conversation += f"\n\n" + t("tool_no_result", self.lang)
                full_response = response
                if i >= 3:
                    break

            if not full_response or "ERROR" in full_response:
                full_response = t("log.task_failed", self.lang)

            task_node.status = "done"
            task_node.result = full_response
            self.action_history.append(task_node.name.split()[0] if task_node.name else "unknown")
            self._log("INFO", t("log.task_done", self.lang), task_node.name)
            yield {"type": "done", "result": full_response}

    def execute_tree(self, node=None):
        if node is None:
            if not self.task_tree:
                return {"error": "No task tree"}
            self._log("INFO", t("log.tree_execution_start", self.lang), "")
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
        return {"message": t("log.module_ready", self.lang)}
