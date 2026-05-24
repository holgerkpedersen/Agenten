from llm_wrapper import LMStudioWrapper
from web_searcher import WebSearcher
from task_tree import TaskTree, TaskNode
from module_builder import ModuleBuilder
from tools import Tool, ToolRegistry
from github_wrapper import GithubAPI
from skill_loader import SkillLoader
from lang import t
from i18n import K
import git_ops
import agent_issues
import agent_files
import agent_tree
import agent_skills
import agent_git
import agent_tasks
import re
import sys
import time
import os
import json
import subprocess


def _extract_filenames(location):
    filenames = []
    if not location:
        return filenames
    if ":" in location:
        filenames.append(location.split(":")[0].strip())
    else:
        for part in location.split(","):
            part = part.strip().split()[0].strip("`'\"")
            if part.endswith(".py"):
                filenames.append(part)
    return filenames


class Agent:
    def __init__(self):
        self.llm = LMStudioWrapper(timeout=600, model="qwen/qwen3.5-9b")
        self.decompose_llm = LMStudioWrapper(timeout=600, model="qwen/qwen3.5-9b")
        self.searcher = WebSearcher()
        self.task_tree = None
        self.action_history = []
        self.execution_log = []
        self.agent_log = []
        self.original_prompt = ""
        self.full_prompt_with_context = ""
        self.show_thinking = True
        self.file_context = []
        self.file_chunks = {}
        self.images = []
        self.pending_reply = None
        self.stop_requested = False
        self._pending_refactor = None
        self.lang = "da"
        self.active_template = None
        self.current_phase = None
        self.issue_resolved = False
        self.max_tokens = 4096
        self.max_conversation_chars = 8000
        self.tool_registry = ToolRegistry()
        self._register_tools()
        self._checkpoint_tools = set()
        self._checkpoint_branch = ""
        self._skills = SkillLoader.load_all(lang=self.lang)
        self._active_skills = []
        self._task_start_time = 0

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
        self.tool_registry.register(Tool(
            "read_chunk",
            "Indlæs en chunk af en stor fil. Kræver: file_key (filnavn fra list_chunks), index (1..N). Brug 'list_chunks' først for at se tilgængelige filer og deres chunk-indekser.",
            ["file_key", "index"],
            lambda file_key, index=1: self._read_chunk(file_key, int(index))
        ))
        self.tool_registry.register(Tool(
            "list_chunks",
            "List alle tilgængelige filer (chunks) som kan læses med read_chunk. Brug DENNE først for at se hvad der er tilgængeligt.",
            [],
            lambda: self._list_chunks()
        ))
        self.tool_registry.register(Tool(
            "write_file",
            "Opret en NY fil med indhold. Virker KUN til nye filer — findes filen i forvejen, brug edit_file i stedet. Opretter mappen hvis den ikke findes. Syntestjekker .py filer.",
            ["path", "content"],
            lambda path, content: git_ops.write_file(path=path, content=content)
        ))
        self.tool_registry.register(Tool(
            "edit_file",
            "Rediger en eksisterende fil med search-and-replace. Kræver: path (filsti), old_text (præcis tekst der skal erstattes), new_text (den nye tekst). Søgeteksten skal findes præcis én gang. Syntestjekkes for .py filer. Opretter IKKE nye filer — brug write_file til det.",
            ["path", "old_text", "new_text"],
            lambda path, old_text, new_text: git_ops.edit_file(path=path, old_text=old_text, new_text=new_text)
        ))
        self.tool_registry.register(Tool(
            "list_files",
            "List filer i en mappe. Kræver: path (mappesti, default '.'). Valgfri: pattern (filtype f.eks. '.py'). Valgfri: max_depth (max dybde, default 2). Returnerer filnavne og størrelser.",
            ["path"],
            lambda path=".", pattern="", max_depth=2: git_ops.list_files(path=path, pattern=pattern or None, max_depth=int(max_depth))
        ))
        self.tool_registry.register(Tool(
            "add_image",
            "Tilføj et billede til konteksten. Kræver: path (sti til billedfil). Returnerer MIME-type og størrelse.",
            ["path"],
            lambda path: self._add_image(path)
        ))
        self.tool_registry.register(Tool(
            "run_tests",
            "Kør pytest og returner resultat. Args: test_path (valgfri). Eksempel: run_tests(test_path='tests/test_tools.py::TestToolExecution')",
            ["test_path"],
            lambda test_path="": agent_issues.run_pytest(test_path)
        ))
        self.tool_registry.register(Tool(
            "read_issue",
            "Læs et issue fra docs/issues/observed/issues.json. Args: issue_id (f.eks. 'BUG-003'). Returnerer issue-detaljer.",
            ["issue_id"],
            lambda issue_id: agent_issues.read_issue(issue_id)
        ))
        self.tool_registry.register(Tool(
            "update_issue_status",
            "Opdater status på et issue i docs/issues/observed/issues.json. Args: issue_id, status ('open'/'in_progress'/'resolved'), resolution_note (valgfri).",
            ["issue_id", "status"],
            lambda issue_id, status="resolved", resolution_note="": agent_issues.update_issue_status(self, issue_id, status, resolution_note)
        ))
        self.tool_registry.register(Tool(
            "create_refactor_issue",
            "Opret et REFAC-issue i issues.json for en fil der er for stor. Kræver: filepath (filsti), line_count (antal linjer). Valgfri: related_issues (liste af issue-IDs).",
            ["filepath", "line_count"],
            lambda filepath, line_count, related_issues="": agent_issues.create_refactor_issue(self, filepath, int(line_count), related_issues.split(",") if related_issues else None)
        ))
        self.tool_registry.register(Tool(
            "create_issue",
            "Opret et nyt issue i issues.json. ÉT issue = ÉN specifik fejl (ikke flere endpoints samlet). Kræver: title, type (bug/security/architecture/testing/performance/maintainability), severity (low/medium/high/critical), description, location (format: filnavn:funktionsnavn), impact, proposed_fix. acceptance_criteria: beskriv præcist hvordan fixet verificeres (f.eks. 'Endpoint returnerer 403 ved ../ i stien').",
            ["title", "type", "severity", "description", "location", "impact", "proposed_fix", "acceptance_criteria"],
            lambda title, type="bug", severity="medium", description="", location="", impact="", proposed_fix="", acceptance_criteria="": agent_issues.create_issue(self, title=title, type=type, severity=severity, description=description, location=location, impact=impact, proposed_fix=proposed_fix, acceptance_criteria=acceptance_criteria)
        ))

    def _add_image(self, path):
        return agent_tasks.add_image(self, path)

    def _run_pytest(self, test_path=""):
        try:
            cmd = [sys.executable, "-m", "pytest", "-v"]
            if test_path:
                cmd.append(test_path)
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            return {"success": result.returncode == 0, "stdout": result.stdout, "stderr": result.stderr, "exit_code": result.returncode}
        except subprocess.TimeoutExpired:
            return {"success": False, "stdout": "", "stderr": "Timeout (120s)", "exit_code": -1}
        except FileNotFoundError:
            return {"success": False, "stdout": "", "stderr": "python -m pytest not found", "exit_code": -1}

    def _read_issue(self, issue_id):
        import json as _json
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs", "issues", "observed", "issues.json")
        if not os.path.exists(path):
            return {"success": False, "error": f"Issue file not found at {path}"}
        with open(path, encoding="utf-8") as f:
            data = _json.load(f)
        for issue in data.get("issues", []):
            if issue.get("id", "").lower() == issue_id.lower():
                return {"success": True, "issue": issue}
        return {"success": False, "error": f"Issue '{issue_id}' not found. Available: {[i['id'] for i in data.get('issues', [])]}"}

    def _update_issue_status(self, issue_id, status, resolution_note=""):
        import json as _json
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs", "issues", "observed", "issues.json")
        if not os.path.exists(path):
            return {"success": False, "error": f"Issue file not found at {path}"}
        with open(path, encoding="utf-8") as f:
            data = _json.load(f)
        for issue in data.get("issues", []):
            if issue.get("id", "").lower() == issue_id.lower():
                issue["status"] = status
                if resolution_note:
                    issue["resolution_note"] = resolution_note
                with open(path, "w", encoding="utf-8") as f:
                    _json.dump(data, f, ensure_ascii=False, indent=2)
                self._log("INFO", f"Issue {issue_id} → {status}", resolution_note[:200])
                return {"success": True, "issue": issue, "status": status}
        return {"success": False, "error": f"Issue '{issue_id}' not found."}

    def _list_chunks(self):
        return agent_files.list_chunks(self)

    def _read_chunk(self, chunk, index):
        return agent_files.read_chunk(self, chunk, int(index))

    def _refresh_skills(self):
        agent_skills.refresh_skills(self)

    def _match_skills(self, prompt):
        return agent_skills.match_skills(self, prompt)

    def _format_skills_for_prompt(self):
        return agent_skills.format_skills_for_prompt(self)

    def _get_templates(self):
        return agent_skills.get_templates(self)

    def _record_outcome(self, task_node):
        agent_tree.record_outcome(self, task_node)

    def _evolve_if_needed(self):
        agent_tree.evolve_if_needed(self)

    def _log(self, level, message, detail=""):
        log_entry = {
            "timestamp": time.time(),
            "level": level,
            "message": message,
            "detail": detail if detail else ""
        }
        self.agent_log.append(log_entry)
        print(f"[{level}] {message}: {detail[:200]}")

    def _clean_task_name(self, name):
        return agent_tree._clean_task_name(name)

    def _read_file_content(self, filepath):
        return agent_files.read_file_content(self, filepath)

    def _get_single_file_context(self, prompt):
        return agent_files.get_single_file_context(self, prompt)

    def _get_folder_context(self, prompt):
        return agent_files.get_folder_context(self, prompt)

    def _create_fallback_tree(self, prompt):
        self.original_prompt = prompt
        return agent_tree.create_fallback_tree(self, prompt)

    def _parse_tree_from_llm(self, prompt, llm_response):
        return agent_tree.parse_tree_from_llm(self, prompt, llm_response)

    def _sanitize_prompt(self, prompt):
        """Sanitize user input to prevent prompt injection attacks."""
        # Escape potential closing tags that might break the wrapper structure
        safe_input = prompt.replace("</user_input>", "<SECURITY_TAG>")
        return f"<user_input>\n{safe_input}\n<END_USER_INPUT>"

    def decompose_prompt(self, prompt, files=None, template=None):
        self.agent_log = []
        self.original_prompt = prompt
        self.tool_registry.lang = self.lang
        self._refresh_skills()
        templates = self._get_templates()

        if not template:
            suggested = SkillLoader.suggest_template(prompt, self._skills)
            if suggested and suggested in templates:
                template = suggested

        template_config = templates.get(template, templates["fri"]) if template else templates["fri"]
        self.active_template = template
        allowed = agent_skills.TEMPLATE_TOOLS.get(template) if template else None
        self.tool_registry.set_active_tools(allowed)
        self._log("INFO", t(K.LOG_DECOMPOSE_START, self.lang), f"{prompt[:100]} ({t('ui.using_template', self.lang).format(name=template_config['name'])})")

        self._match_skills(prompt)

        self.file_context = files or []
        self.file_chunks = {}

        if template in ("bugfix", "issue_handler") and not files:
            issue_match = re.search(r'(BUG-\d+|SEC-\d+|TST-\d+|ARC-\d+|PRF-\d+|MNT-\d+|REFAC-\d+)', prompt)
            if issue_match:
                issue_id = issue_match.group(1)
                try:
                    issues_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs", "issues", "observed", "issues.json")
                    if os.path.exists(issues_path):
                        with open(issues_path, encoding="utf-8") as f:
                            issues_data = json.load(f)
                        for issue in issues_data.get("issues", []):
                            if issue.get("id", "").lower() == issue_id.lower():
                                location = issue.get("location", "")
                                filenames = _extract_filenames(location)
                                for filename in filenames:
                                    if filename.endswith('.py'):
                                        base_dir = os.path.dirname(os.path.abspath(__file__))
                                        for path in [filename, os.path.join(base_dir, filename), os.path.join(os.getcwd(), filename)]:
                                            if os.path.exists(path):
                                                content = self._read_file_content(path)
                                                if content:
                                                    files.append({"filename": filename, "content": content, "path": path})
                                                    self._log("INFO", f"Auto-loaded fil fra {issue_id}", path)
                                                break
                except Exception as e:
                    self._log("WARNING", "Kunne ikke auto-loade issue-fil", str(e))

        file_context = ""
        if files and len(files) > 0:
            file_context = t(K.FILE_CONTEXT_HEADER, self.lang)
            for f in files:
                filename = f.get('filename', t(K.UNKNOWN, self.lang))
                content = f.get('content', '')
                chunk_key = f"file_{filename}"
                chunks = agent_files.chunk_text(content)
                self.file_chunks[chunk_key] = chunks
                oversize = agent_issues.detect_oversize_file(self, filename, content)
                if len(chunks) <= 1:
                    file_context += f"\n### {filename}\n\n```{filename}\n{content}\n```\n"
                else:
                    file_context += f"\n### {filename} (chunk 1/{len(chunks)}, ~{agent_files.CHUNK_SIZE}tgn/chunk)\n\n```{filename}\n{chunks[0]}\n```\n"
                    file_context += f"\n*Filen er stor — indlæs flere chunks med read_chunk(file_key='{chunk_key}', index=2..{len(chunks)})*\n"
            self._log("INFO", t(K.LOG_ADDING_FILES, self.lang), t(K.LOG_N_FILES, self.lang).format(n=len(files)))
        else:
            scanned_files = self._get_folder_context(prompt)
            if scanned_files:
                file_context = t(K.FILE_CONTEXT_HEADER, self.lang)
                self.file_context = scanned_files
                for item in scanned_files:
                    filename = item['filename']
                    content = item['content']
                    chunk_key = f"file_{filename}"
                    chunks = agent_files.chunk_text(content)
                    self.file_chunks[chunk_key] = chunks
                    agent_issues.detect_oversize_file(self, filename, content)
                    if len(chunks) <= 1:
                        file_context += f"\n### {filename}\n\n```{filename}\n{content}\n```\n"
                    else:
                        file_context += f"\n### {filename} (chunk 1/{len(chunks)}, ~{agent_files.CHUNK_SIZE}tgn/chunk)\n\n```{filename}\n{chunks[0]}\n```\n"
                        file_context += f"\n*Filen er stor — indlæs flere chunks med read_chunk(file_key='{chunk_key}', index=2..{len(chunks)})*\n"
                self._log("INFO", t(K.LOG_ADDING_FILES, self.lang), t(K.LOG_N_FILES, self.lang).format(n=len(scanned_files)))
            else:
                file_path, file_content = self._get_single_file_context(prompt)
                if file_content:
                    filename = os.path.basename(file_path)
                    chunk_key = f"file_{filename}"
                    chunks = agent_files.chunk_text(file_content)
                    self.file_chunks[chunk_key] = chunks
                    file_context = t(K.FILE_CONTEXT_HEADER, self.lang) + filename + t(K.FILE_CONTEXT_PYTHON, self.lang).replace("{content}", file_content)

        if self._pending_refactor:
            oversize_note = (
                f"\n\n## \u26A0\uFE0F BEM\u00C6RKNING: Filen '{self._pending_refactor['file']}' er "
                f"{self._pending_refactor['lines']} linjer (gr\u00E6nse: {agent_issues.OVERSIZE_LINE_LIMIT}).\n"
                f"Sp\u00F8rg venligst brugeren om de \u00F8nsker et REFAC-issue oprettet "
                f"(brug `create_refactor_issue` v\u00E6rkt\u00F8jet).\n"
            )
            file_context += oversize_note

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

        decomposition_prompt = template_config["prompt"].replace("{prompt}", self._sanitize_prompt(prompt))
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

        if task_count <= 1 and template in (None, "", "fri"):
            self._log("INFO", "Kun én opgave — bruger generisk nedbrydning", "")
            self.task_tree = self._create_fallback_tree(prompt)
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
        return agent_tree.count_tasks(node)

    def task_tree_to_dict(self):
        return agent_tree.task_tree_to_dict(self)

    def task_tree_from_dict(self, d):
        agent_tree.task_tree_from_dict(self, d)

    def _truncate_conversation(self, conversation, system_prompt):
        return agent_tasks.truncate_conversation(self, conversation, system_prompt)

    def _build_tool_guidance(self, attempt):
        return agent_tasks.build_tool_guidance(self, attempt)

    def _ask_ai(self, prompt):
        return agent_tasks.ask_ai(self, prompt)

    def _handle_tool_call(self, action, conversation, already_called, attempt):
        return agent_tasks.handle_tool_call(self, action, conversation, already_called, attempt)

    def _set_task_tools(self, task_name):
        agent_tasks.set_task_tools(self, task_name)

    def solve_task(self, task_node, original_prompt):
        return agent_tasks.solve_task(self, task_node, original_prompt)

    def solve_task_stream(self, task_node, original_prompt):
        yield from agent_tasks.solve_task_stream(self, task_node, original_prompt)

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
