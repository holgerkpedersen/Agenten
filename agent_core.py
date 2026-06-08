"""Agent core module."""

from __future__ import annotations

from llm_wrapper import LMStudioWrapper
from web_searcher import WebSearcher
from task_tree import TaskTree, TaskNode
from module_builder import ModuleBuilder
from tools import Tool, ToolRegistry, _strip_llm_tags
from github_wrapper import GithubAPI
from skill_loader import SkillLoader
from lang import t
from i18n import K
from refactoring_engine import RefactoringEngine
import git_ops
import agent_issues
import agent_files
import agent_tree
import agent_skills
import agent_git
from agent_wta import WTAState, SequenceLearner
from core_analytics import CoreAnalytics, TOOL_HANDLER_MAP
import agent_tasks
import edit_file2
import config
from config import get_logger
log = get_logger(__name__)
import re
import sys
import time
import os
import json
import subprocess
import threading
from typing import Any, Generator

def _safe_int(val: Any, default: int = 0) -> int:
    """Convert a value to an integer safely, returning default on failure."""
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


def _extract_filenames(location: str) -> list[str]:
    """Extract file paths from a location string using regex."""
    filenames = []
    if not location:
        return filenames
    for m in re.finditer(r'([\w./\\-]+\.\w+)', location):
        fn = m.group(1)
        if fn not in filenames:
            filenames.append(fn)
    return filenames


def _auto_load_issue_files(agent: Agent, prompt: str, template: str | None, files: list[dict[str, Any]]) -> None:
    """Automatically load issue-related files when a bug/issue ID is present in the prompt."""
    if template not in ("bugfix", "issue_handler") or files:
        return
    issue_match = re.search(r'(BUG-\d+|SEC-\d+|TST-\d+|ARC-\d+|PRF-\d+|MNT-\d+|REFAC-\d+)', prompt)
    if not issue_match:
        return
    issue_id = issue_match.group(1)
    try:
        base_dir = os.path.dirname(os.path.abspath(__file__))
        issues_path = os.path.join(base_dir, "docs", "issues", "observed", "issues.json")
        if not os.path.exists(issues_path):
            return
        with open(issues_path, encoding="utf-8") as f:
            issues_data = json.load(f)
        for issue in issues_data.get("issues", []):
            if issue.get("id", "").lower() != issue_id.lower():
                continue
            location = issue.get("location", "")
            filenames = _extract_filenames(location)
            for filename in filenames:
                if not filename.endswith('.py'):
                    continue
                for path in [filename, os.path.join(base_dir, filename), os.path.join(os.getcwd(), filename)]:
                    if os.path.exists(path):
                        content = agent._read_file_content(path)
                        if content:
                            files.append({"filename": filename, "content": content, "path": path})
                            agent._log("INFO", f"Auto-loaded fil fra {issue_id}", path)
                        break
    except Exception as e:
        agent._log("WARNING", "Kunne ikke auto-loade issue-fil", str(e))


def _auto_load_location_file(agent: Agent, prompt: str) -> None:
    """Load file(s) from a Location: field in the prompt."""
    if agent.file_chunks:
        return
    location_match = re.search(r'Location:\s*([^\n]+)', prompt, re.IGNORECASE)
    if not location_match:
        return
    location = location_match.group(1).strip()
    filenames = _extract_filenames(location)
    base_dir = os.path.dirname(os.path.abspath(__file__))
    for filename in filenames:
        if not filename.endswith('.py'):
            continue
        for path in [filename, os.path.join(base_dir, filename), os.path.join(os.getcwd(), filename)]:
            if os.path.exists(path):
                try:
                    content = agent._read_file_content(path)
                    if content:
                        chunk_key = f"file_{os.path.basename(filename)}"
                        agent.file_chunks[chunk_key] = agent_files.chunk_text(content)
                        agent._log("INFO", f"Auto-loaded location-fil", f"{filename} ({len(agent.file_chunks[chunk_key])} chunks)")
                except Exception as e:
                    agent._log("WARNING", f"Kunne ikke auto-loade {filename}", str(e))
                return


def _add_file_entry(file_context: str, agent: Agent, filename: str, content: str) -> str:
    """add file entry.
    
    Args:
        file_context:
        agent:
        filename:
        content:"""
    chunk_key = f"file_{filename}"
    chunks = agent_files.chunk_text(content)
    agent.file_chunks[chunk_key] = chunks
    agent_issues.detect_oversize_file(agent, filename, content)
    if agent._pending_refactor:
        agent_issues.create_refactor_issue(agent, filename, agent._pending_refactor["lines"])
    is_python = filename.endswith('.py')
    if is_python:
        ast_index = agent_files.build_ast_index(content, filename)
        if ast_index:
            file_context += f"\n{ast_index}\n"
            if len(chunks) > 1:
                file_context += f"\n*Filen er stor ({len(chunks)} chunks) \u2014 brug read_chunk(file_key='{chunk_key}', index=1..{len(chunks)}) for at l\u00e6se indhold.*\n"
            file_context += f"\n*Brug locate(filepath='{filename}', name='funktionsnavn') for at l\u00e6se en bestemt funktion/metode.*\n"
            return file_context
    display_content = content[:config.MAX_FILE_CONTEXT_CHARS]
    truncated_note = "\n[... indhold afkortet \u2014 brug read_chunk() for at l\u00e6se hele filen ...]" if len(content) > config.MAX_FILE_CONTEXT_CHARS else ""
    if len(chunks) <= 1:
        file_context += f"\n### {filename}\n\n```{filename}\n{display_content}{truncated_note}\n```\n"
    else:
        file_context += f"\n### {filename} (chunk 1/{len(chunks)}, ~{agent_files.CHUNK_SIZE}tgn/chunk)\n\n```{filename}\n{display_content}{truncated_note}\n```\n"
        file_context += f"\n*Filen er stor \u2014 indl\u00e6s flere chunks med read_chunk(file_key='{chunk_key}', index=2..{len(chunks)})*\n"
    return file_context


def _build_file_context(agent: Agent, files: list[dict[str, Any]] | None, prompt: str) -> str:
    """build file context.
    
    Args:
        agent (Agent):
        files (list[dict[str, Any]] | None):
        prompt (str):
    
    Returns:
        str"""
    file_context = ""
    if files and len(files) > 0:
        file_context = t(K.FILE_CONTEXT_HEADER, agent.lang)
        for f in files:
            file_context = _add_file_entry(file_context, agent, f.get('filename', t(K.UNKNOWN, agent.lang)), f.get('content', ''))
        agent._log("INFO", t(K.LOG_ADDING_FILES, agent.lang), t(K.LOG_N_FILES, agent.lang).format(n=len(files)))
    else:
        scanned_files = agent._get_folder_context(prompt)
        if scanned_files:
            file_context = t(K.FILE_CONTEXT_HEADER, agent.lang)
            agent.file_context = scanned_files
            for item in scanned_files:
                file_context = _add_file_entry(file_context, agent, item['filename'], item['content'])
            agent._log("INFO", t(K.LOG_ADDING_FILES, agent.lang), t(K.LOG_N_FILES, agent.lang).format(n=len(scanned_files)))
        else:
            file_path, file_content = agent._get_single_file_context(prompt)
            if file_content:
                filename = os.path.basename(file_path)
                file_context = _add_file_entry("", agent, filename, file_content)
                file_context = t(K.FILE_CONTEXT_HEADER, agent.lang) + file_context.lstrip()
    return file_context


def _build_fallback_tree(agent: Agent, prompt: str, fallback_sections: list[str]) -> None:
    """Build a structured fallback task tree from a template's section list.

    Each section becomes a top-level task node. If the section name contains
    parentheses, the text inside is extracted as success criteria.

    Args:
        agent: The Agent instance (``task_tree`` is mutated in-place).
        prompt: The original user prompt (used as tree root name).
        fallback_sections: List of phase names, possibly with ``(criteria)``.
    """
    tree = TaskTree(prompt)
    _criteria_re = re.compile(r'^(.+?)\s*\(([^)]+)\)\s*$')
    for section in fallback_sections:
        section_str = str(section)
        m = _criteria_re.match(section_str)
        if m:
            name = m.group(1).strip()
            criteria = [c.strip() for c in m.group(2).split(",")]
        else:
            name = section_str
            criteria = []
        node = TaskNode(name)
        node.success_criteria = criteria
        tree.root.add_child(node)
    agent.task_tree = tree


def _decompose_via_llm(agent: Agent, prompt: str, file_context: str, template_config: dict[str, Any]) -> dict[str, Any]:
    """decompose via llm.
    
    Args:
        agent (Agent):
        prompt (str):
        file_context (str):
        template_config (dict[str, Any]):
    
    Returns:
        dict[str, Any]"""
    decomposition_prompt = template_config["prompt"].replace("{prompt}", agent._sanitize_prompt(prompt))
    file_context_entry = f"\n\nMateriale:{file_context}" if file_context else ""
    decomposition_prompt += file_context_entry

    model_lower = agent.decompose_llm.model.lower()
    for model_prefix, channel_tag in config.CHANNEL_TAG_MODELS.items():
        if model_prefix in model_lower:
            decomposition_prompt += channel_tag
            break

    agent._log("LLM", t(K.LOG_SENDING_LLM, agent.lang), t(K.LOG_N_FILES, agent.lang).format(n=len(agent.file_context)) if isinstance(agent.file_context, list) and agent.file_context else "")
    response = agent.decompose_llm.generate(decomposition_prompt, temperature=0.3, max_tokens=4096)
    agent._log("LLM", t(K.LOG_RECEIVED_LLM, agent.lang), t(K.LOG_N_CHARS, agent.lang).format(n=len(response)))

    response = _strip_llm_tags(response)

    agent.task_tree = agent._parse_tree_from_llm(prompt, response)
    task_count = agent._count_tasks(agent.task_tree.root)
    agent._log("INFO", t(K.LOG_DECOMPOSE_DONE, agent.lang), t(K.LOG_TASKS_CREATED, agent.lang).format(n=task_count))

    if task_count <= 1 and template_config.get("fallback"):
        agent._log("INFO", "Kun én opgave — bruger skabelonens faldback", "")
        _build_fallback_tree(agent, prompt, template_config["fallback"])
        task_count = agent._count_tasks(agent.task_tree.root)

    if task_count >= 2 and template_config.get("name") in (None, "", "fri"):
        # Check whether the LLM actually included success criteria. If none of the
        # top-level tasks have criteria, the decomposition is too vague — use fallback.
        has_criteria = any(
            bool(getattr(c, 'success_criteria', None))
            for c in agent.task_tree.root.children
        )
        if not has_criteria and template_config.get("fallback"):
            agent._log("INFO", "Ingen succeskriterier i træet — bruger skabelonens strukturerede faldback", "")
            _build_fallback_tree(agent, prompt, template_config["fallback"])
            task_count = agent._count_tasks(agent.task_tree.root)

    agent._log("INFO", t(K.LOG_DECOMPOSE_DONE, agent.lang), t(K.LOG_TASKS_CREATED, agent.lang).format(n=task_count))
    return agent.task_tree_to_dict()


class Agent:
    """agent."""
    def __init__(self) -> None:
        """Initialize the instance.
        
        Returns:
            None"""
        self.llm: LMStudioWrapper = LMStudioWrapper(
            timeout=600, model=config.LLM_MODEL, base_url=config.LLM_BASE_URL,
            api_key=os.environ.get('OPENCODE_API_KEY'),
            on_request=lambda body: self._log("LLM_REQUEST", f"Anmodning til {body.get('model', '?')}",
                f"{len(body.get('messages', []))} beskeder, tools={len(body.get('tools', [])) if body.get('tools') else 0}, temperature={body.get('temperature')}, max_tokens={body.get('max_tokens')}")
        )
        self.decompose_llm: LMStudioWrapper = LMStudioWrapper(
            timeout=600, model=config.LLM_MODEL, base_url=config.LLM_BASE_URL,
            api_key=os.environ.get('OPENCODE_API_KEY'),
            on_request=lambda body: self._log("LLM_REQUEST", f"Decompose-anmodning til {body.get('model', '?')}",
                f"{len(body.get('messages', []))} beskeder, temperature={body.get('temperature')}, max_tokens={body.get('max_tokens')}")
        )
        self.searcher: WebSearcher = WebSearcher()
        self.task_tree: TaskTree | None = None
        self.action_history: list[str] = []
        self.execution_log: list[dict[str, Any]] = []
        self.agent_log: list[dict[str, Any]] = []
        self.original_prompt: str = ""
        self.full_prompt_with_context: str = ""
        self._file_context_str: str = ""
        self.show_thinking: bool = True
        self.file_context: list[dict[str, Any]] = []
        self.file_chunks: dict[str, list[str]] = {}
        self.images: list[dict[str, Any]] = []
        self.images_lock: threading.Lock = threading.Lock()
        self.pending_reply: str | None = None
        self.stop_requested: bool = False
        self._pending_refactor: dict[str, Any] | None = None
        self.lang: str = "da"
        self.active_template: str | None = None
        self.current_phase: str | None = None
        self.issue_resolved: bool = False
        self.max_tokens: int = config.MAX_TOKENS
        self.max_conversation_chars: int = config.MAX_CONVERSATION_CHARS
        self.tool_registry: ToolRegistry = ToolRegistry()
        self._register_tools()
        self._checkpoint_tools: set[str] = set()
        self._checkpoint_branch: str = ""
        self._skills: list[dict[str, Any]] | None = None
        self._active_skills: list[dict[str, Any]] = []
        self._task_start_time: float | None = None
        self._file_hash_registry: dict[str, str] = {}
        self._delegation_index: dict[str, tuple[str, str]] | None = None
        self._tool_log: list[dict[str, Any]] = []
        self._wta: WTAState = WTAState()
        self._wta.load()
        self._seq: SequenceLearner = SequenceLearner()
        self._seq.load()
        self._core: CoreAnalytics = CoreAnalytics()
        self._core.load()
        self._hints_requested: set[str] = set()
        self._hints_available: set[str] = set()
        self._rubric_retried: bool = False
        self._write_failed: bool = False
        self._tests_failed: bool = False
        self._located_files: set[str] = set()
        self.refactoring_engine: RefactoringEngine = RefactoringEngine()

    def _register_tools(self) -> None:
        """register tools."""
        self._register_github_tools()
        self._register_git_tools()
        self._register_file_tools()
        self._register_agent_tools()

    def _register_github_tools(self) -> None:
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
            lambda owner, repo, title, branch, base="main": gh.create_pr(owner=owner, repo=repo, title=title, head=branch, base=base)
        ))

    def _register_git_tools(self) -> None:
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
            lambda branch="main": git_ops.git_push(branch=branch)
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
            lambda count=10: git_ops.git_log(_safe_int(count, 10))
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
            lambda remote="origin", branch="main": git_ops.git_pull(remote=remote, branch=branch)
        ))
        self.tool_registry.register(Tool(
            "git_checkout",
            t(K.TOOL_GIT_CHECKOUT, self.lang),
            ["branch"],
            lambda branch: git_ops.git_checkout(branch=branch)
        ))

    def _register_file_tools(self) -> None:
        self.tool_registry.register(Tool(
            "read_location",
            "Læs KUN en bestemt funktion/metode/klasse vha. AST — IKKE hele filen. Angiv filepath='fil.py' og name='funktionsnavn' (eller name='Klasse.metode'). Returnerer funktionens fulde kode. Brug DENNE i stedet for read_chunk når du skal se specifik kode — meget mere effektivt end at læse hele filen.",
            ["filepath", "name"],
            lambda filepath, name=None, line_no=None: agent_files.read_location(filepath=filepath, name=name, line_no=line_no),
            optional_params=["line_no"]
        ))
        self.tool_registry.register(Tool(
            "read_chunk",
            "Indlæs en chunk af en stor fil. Kræver: file_key (filnavn fra list_chunks), index (1..N). Brug 'list_chunks' først for at se tilgængelige filer og deres chunk-indekser. Brug kun read_chunk HVIS read_location ikke virker (f.eks. ikke-Python filer).",
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
            "list_symbols",
            "List ALLE top-level symboler (funktioner, klasser, variabler) i en Python-fil via AST. Returnerer navn, type, linjenummer og signatur for hvert symbol. Brug DENNE før locate/read_location når du ikke kender symbolnavnene i en fil. Kræver: filepath='fil.py'.",
            ["filepath"],
            lambda filepath: agent_files.list_symbols(filepath=filepath)
        ))
        self.tool_registry.register(Tool(
            "locate",
            "Find en funktion/metode/klasse i en Python-fil via AST. Brug name='funktionsnavn' for at søge på tværs af ALLE .py-filer. Brug name='Klassnavn.metode' for metoder. Brug filepath='fil.py' for at begrænse søgningen til én fil. Returnerer linjenummer, typen, funktionens fulde kode (body), og en 'also_in_file'-liste over ANDRE symboler i filen.",
            ["name", "filepath"],
            lambda name=None, filepath=None, line_no=None: agent_files.locate_code(filepath=filepath, name=name, line_no=line_no),
            optional_params=["filepath"]
        ))
        self.tool_registry.register(Tool(
            "write_file",
            "Opret en NY fil med indhold, eller overskriv en eksisterende med overwrite=true. Brug path='docs/fil.md' for at gemme i docs-mappen. Opretter mappen hvis den ikke findes. Syntestjekker .py filer.",
            ["path", "content"],
            lambda path, content, overwrite=False: git_ops.write_file(path=path, content=content, overwrite=overwrite)
        ))
        self.tool_registry.register(Tool(
            "edit_file",
            "Rediger en eksisterende fil. To tilstande:\n"
            "1) AST-tilstand: Angiv symbol='funktionsnavn' + new_text (hele funktionen). Systemet finder funktionen via AST-linjenumre — old_text ignoreres.\n"
            "2) Search-and-replace: Angiv old_text (præcis tekst) + new_text. Søgeteksten skal findes præcis én gang.\n"
            "Syntestjekker .py filer. Opretter IKKE nye filer — brug write_file til det.",
            ["path", "old_text", "new_text"],
            lambda path, old_text="", new_text="", symbol=None: git_ops.edit_file(
                path=path, old_text=old_text, new_text=new_text,
                expected_hash=self._file_hash_registry.get(os.path.normcase(os.path.abspath(path))),
                symbol=symbol
            ),
            optional_params=["symbol"]
        ))
        self.tool_registry.register(Tool(
            "edit_file2",
            "AST-bevidst symbol-redigering med LLM-forbedring og automatisk retry. "
            "Trin: (0) backup, (1) find symbol via AST, (2) send til LLM med krav, "
            "(3) erstat symbol, (4) kør test — hvis fejl: gendan backup + tilføj fejl til prompt + gentag, "
            "(5) slet backup. Meget smartere end edit_file — LLM'en ser hele funktionen og forbedrer den.",
            ["filepath", "name", "requirements"],
            lambda filepath, name, requirements, test_path="": edit_file2.edit_file2(
                filepath=filepath, name=name, requirements=requirements,
                llm=self.llm, test_path=test_path or None,
            ),
            optional_params=["test_path"]
        ))
        self.tool_registry.register(Tool(
            "list_files",
            "List filer i en mappe. Kræver: path (mappesti, default '.'). Valgfri: pattern (filtype f.eks. '.py'). Valgfri: max_depth (max dybde, default 2). Returnerer filnavne og størrelser.",
            ["path"],
            lambda path=".", pattern="", max_depth=2: git_ops.list_files(path=path, pattern=pattern or None, max_depth=_safe_int(max_depth, 2))
        ))
        self.tool_registry.register(Tool(
            "add_image",
            "Tilføj et billede til konteksten. Kræver: path (sti til billedfil). Returnerer MIME-type og størrelse.",
            ["path"],
            lambda path: self._add_image(path)
        ))
        self.tool_registry.register(Tool(
            "extract_symbol",
            "Flyt en funktion/klasse/variabel fra én .py-fil til en NY .py-fil. "
            "Systemet finder symbolet via AST, kopierer det (med imports) til target-filen, "
            "fjerner det fra source-filen, og tilføjer en import i source der peger på target-modulet. "
            "Kræver: source (kildefil), symbol_name (f.eks. 'UserHandler'), target (målfil, f.eks. 'routes.py'). "
            "Dette er DETERMINISTISK — bruger IKKE LLM'en. Returnerer detaljer om hvad der blev flyttet.",
            ["source", "symbol_name", "target"],
            lambda source, symbol_name, target: self.refactoring_engine.move_symbol(
                source=source, symbol_name=symbol_name, target=target
            )
        ))
        self.tool_registry.register(Tool(
            "remove_symbol",
            "Fjern en funktion/klasse/variabel fra en .py-fil via AST (deterministisk). "
            "Bruges af Opdatér-fasen til at fjerne kode der allerede er flyttet til et modul. "
            "Kræver: source (filsti), symbol_name (f.eks. 'UserHandler'). "
            "Returnerer symbol, linjer fjernet, og resterende symboler i filen.",
            ["source", "symbol_name"],
            lambda source, symbol_name: self.refactoring_engine.remove_symbol(
                source=source, symbol_name=symbol_name
            )
        ))
        self.tool_registry.register(Tool(
            "add_import",
            "Tilføj 'from module import symbol' til en .py-fil (deterministisk). "
            "Bruges af Opdatér-fasen når en symbol er flyttet til et modul — tilføj importen i den originale fil. "
            "Kræver: source (filsti), module (modulnavn uden .py), symbol (symbolnavn). "
            "Kræver IKKE at symbolet findes — systemet tilføjer blot import-linjen hvis den ikke allerede findes.",
            ["source", "module", "symbol"],
            lambda source, module, symbol: self.refactoring_engine.add_import(
                source=source, module=module, symbol=symbol
            )
        ))
        self.tool_registry.register(Tool(
            "verify_refactor",
            "Verificér at en .py-fil er syntaktisk gyldig efter refactoring. "
            "Kræver: source (filsti). Returnerer success, antal linjer og symboler. "
            "Bruges efter extract_symbol/remove_symbol/add_import for at bekræfte at filen ikke er korrupt.",
            ["source"],
            lambda source: self.refactoring_engine.verify_refactor(source=source)
        ))
        self.tool_registry.register(Tool(
            "analyze_dependencies",
            "Analysér ALLE top-level-symboler i en .py-fil og kortlæg afhængigheder mellem dem. "
            "For hvert symbol vises: (1) hvilke andre symboler i samme fil det afhænger af, "
            "(2) hvilke imports det bruger, (3) hvilke decorators det har. "
            "Giver et komplet afhængighedsgraf over filen — BRUG DET FØRST for at planlægge modulopdeling. "
            "Kræver: source (filsti).",
            ["source"],
            lambda source: self.refactoring_engine.analyze_dependencies(source=source)
        ))
        self.tool_registry.register(Tool(
            "suggest_module_groups",
            "Foreslå modulopdeling baseret på afhængighedsgrafen. "
            "Bruger Tarjan's SCC-algoritme til at finde symboler der SKAL være sammen (cirkulære afhængigheder). "
            "Returnerer grupper af symboler sorteret efter linjenummer, med markering af SCC-grupper. "
            "Kræver: source (filsti), max_group_size (valgfri, default 5). "
            "BRUG EFTER analyze_dependencies når du skal beslutte modulgrænser.",
            ["source"],
            lambda source, max_group_size=5: self.refactoring_engine.suggest_module_groups(
                source=source, max_group_size=max_group_size
            )
        ))

    def _register_agent_tools(self) -> None:
        self.tool_registry.register(Tool(
            "run_tests",
            "Kør pytest og returner resultat. Args: test_path (valgfri). Eksempel: run_tests(test_path='tests/test_tools.py::TestToolExecution')",
            ["test_path"],
            lambda test_path="": agent_issues.run_pytest(test_path)
        ))
        self.tool_registry.register(Tool(
            "read_issue",
            "L\u00e6s et issue fra docs/issues/observed/issues.json. Args: issue_id (f.eks. 'BUG-003'), include_hints (valgfri boolean, default false \u2014 s\u00e6t til true for at se proposed_fix).",
            ["issue_id"],
            lambda issue_id, include_hints=False: agent_issues.read_issue(issue_id, include_hints)
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
            lambda filepath, line_count, related_issues="": agent_issues.create_refactor_issue(self, filepath, int(line_count), (related_issues.split(",") if isinstance(related_issues, str) else related_issues) if related_issues else None)
        ))
        self.tool_registry.register(Tool(
            "done",
            t(K.TOOL_DONE, self.lang),
            ["result"],
            lambda result="": result,
            optional_params=["result"]
        ))
        self.tool_registry.register(Tool(
            "create_issue",
            "Opret et nyt issue i issues.json. ÉT issue = ÉN specifik fejl (ikke flere endpoints samlet). Kræver: title, type (bug/security/architecture/testing/performance/maintainability), severity (low/medium/high/critical), description, location (format: filnavn:funktionsnavn), impact, proposed_fix. acceptance_criteria: beskriv præcist hvordan fixet verificeres (f.eks. 'Endpoint returnerer 403 ved ../ i stien').",
            ["title", "type", "severity", "description", "location", "impact", "proposed_fix", "acceptance_criteria"],
            lambda title, type="bug", severity="medium", description="", location="", impact="", proposed_fix="", acceptance_criteria="": agent_issues.create_issue(self, title=title, type=type, severity=severity, description=description, location=location, impact=impact, proposed_fix=proposed_fix, acceptance_criteria=acceptance_criteria)
        ))

    def _add_image(self, path: str) -> dict[str, Any]:
        """add image.
        
        Args:
            path (str):
        
        Returns:
            dict[str, Any]"""
        return agent_tasks.add_image(self, path)

    def _list_chunks(self) -> dict[str, Any]:
        """list chunks.
        
        Returns:
            dict[str, Any]"""
        return agent_files.list_chunks(self)

    def _read_chunk(self, chunk: str, index: int) -> dict[str, Any]:
        """read chunk.
        
        Args:
            chunk (str):
            index (int):
        
        Returns:
            dict[str, Any]"""
        return agent_files.read_chunk(self, chunk, int(index))

    def _refresh_skills(self) -> None:
        """refresh skills.
        
        Returns:
            None"""
        agent_skills.refresh_skills(self)

    def _match_skills(self, prompt: str) -> list[dict[str, Any]]:
        """match skills.
        
        Args:
            prompt (str):
        
        Returns:
            list[dict[str, Any]]"""
        return agent_skills.match_skills(self, prompt)

    def _format_skills_for_prompt(self) -> str:
        """format skills for prompt.
        
        Returns:
            str"""
        return agent_skills.format_skills_for_prompt(self)

    def _get_templates(self) -> dict[str, Any]:
        """get templates.
        
        Returns:
            dict[str, Any]"""
        return agent_skills.get_templates(self)

    def _record_outcome(self, task_node: TaskNode) -> None:
        """record outcome.
        
        Args:
            task_node (TaskNode):
        
        Returns:
            None"""
        agent_tree.record_outcome(self, task_node)

    def _evolve_if_needed(self) -> None:
        """evolve if needed.
        
        Returns:
            None"""
        agent_tree.evolve_if_needed(self)

    def _log(self, level: str, message: str, detail: str = "", log_file: str | None = None) -> None:
        """log.
        
        Args:
            level (str):
            message (str):
            detail (str):
            log_file (str):
        
        Returns:
            None"""
        log_entry = {
            "timestamp": time.time(),
            "level": level,
            "message": message,
            "detail": str(detail) if detail else ""
        }
        if log_file:
            log_entry["log_file"] = log_file
        self.agent_log.append(log_entry)
        try:
            log_fn = {'INFO': log.info, 'WARNING': log.warning, 'ERROR': log.error}.get(str(level).upper(), log.info)
            log_fn("%s: %s", str(message), str(detail)[:200])
        except Exception:
            pass

    def _record_tool_call(self, phase: str, tool: str, args: dict,
                          success: bool, error: str = "", duration: float = 0) -> None:
        self._tool_log.append({
            "timestamp": time.time(),
            "phase": phase,
            "tool": tool,
            "args": args,
            "success": success,
            "error": error[:500] if error else "",
            "duration": round(duration, 3),
        })
        template = self.active_template or "fri"
        self._wta.record(template, phase, tool, success)
        self._core.record_tool_outcome(tool, success, error=error)
        if tool in ("write_file", "edit_file", "extract_symbol", "remove_symbol"):
            handler = TOOL_HANDLER_MAP.get(tool, "unknown.py")
            self._core.record_edit(handler)
        if len(self._tool_log) % 5 == 0:
            self._wta.save()
            self._core.save()

    def _clean_task_name(self, name: str) -> str:
        """clean task name.
        
        Args:
            name (str):
        
        Returns:
            str"""
        return agent_tree._clean_task_name(name)

    def _ensure_delegation_index(self) -> None:
        """ensure delegation index.
        
        Returns:
            None"""
        if self._delegation_index is not None:
            return
        self._delegation_index = {}
        scanned = {}
        file_count = 0
        for fname in os.listdir('.'):
            if not fname.endswith('.py'):
                continue
            if file_count >= 100:
                break
            content = agent_files.read_file_content(self, fname)
            if content:
                stubs = agent_files.detect_delegations(content)
                if stubs:
                    scanned[os.path.abspath(fname)] = (fname, content, stubs)
                    file_count += 1
        for fpath, (fname, content, stubs) in scanned.items():
            for func_name, target_module in stubs:
                visited = {fpath}
                cur_module, cur_file = target_module, f'{target_module}.py'
                depth = 0
                while depth < 20:
                    cur_abspath = os.path.abspath(cur_file)
                    if cur_abspath in visited or not os.path.exists(cur_file):
                        log.warning("Circular delegation for %s at %s — not indexing", func_name, cur_file)
                        break
                    visited.add(cur_abspath)
                    inner = agent_files.read_file_content(self, cur_file)
                    inner_stubs = agent_files.detect_delegations(inner) if inner else []
                    next_stub = [s for s in inner_stubs if s[0] == func_name]
                    if not next_stub:
                        self._delegation_index[func_name] = (cur_abspath, cur_file)
                        break
                    cur_module = next_stub[0][1]
                    cur_file = f'{cur_module}.py'
                    depth += 1

    def _resolve_delegations_for_context(self, file_context: str) -> str:
        """resolve delegations for context.
        
        Args:
            file_context (str):
        
        Returns:
            str"""
        self._ensure_delegation_index()
        loaded_files = {os.path.normcase(os.path.abspath(k.replace('file_', '', 1)))
                        for k in self.file_chunks}
        for key in list(self.file_chunks.keys()):
            filename = key.replace('file_', '', 1)
            all_content = '\n'.join(self.file_chunks.get(key, []))
            if not all_content:
                continue
            for func_name, _ in agent_files.detect_delegations(all_content):
                if func_name not in self._delegation_index:
                    continue
                real_abspath, real_filename = self._delegation_index[func_name]
                real_norm = os.path.normcase(real_abspath)
                if real_norm in loaded_files:
                    continue
                if f'file_{real_filename}' in self.file_chunks:
                    continue
                tgt_content = agent_files.read_file_content(self, real_filename)
                if not tgt_content:
                    continue
                tgt_key = f'file_{real_filename}'
                chunks = agent_files.chunk_text(tgt_content)
                self.file_chunks[tgt_key] = chunks
                self._file_hash_registry[real_norm] = agent_files.file_hash(real_filename)
                preview = tgt_content[:3000] + ('\n...' if len(tgt_content) > 3000 else '')
                file_context += (
                    f'\n\n### {real_filename} (DELEGATIONSM\u00C5L for {func_name})\n\n'
                    f'```{real_filename}\n{preview}\n```\n'
                    f'*Ovenst\u00E5ende fil er m\u00E5let for {func_name} \u2014 '
                    f'den rigtige implementering er HER, ikke i stubbet.*\n'
                )
                self._log('INFO', f'Loaded delegation target for {func_name}', real_filename)
                loaded_files.add(real_norm)
        return file_context

    def _read_file_content(self, filepath: str) -> str | None:
        """read file content.
        
        Args:
            filepath (str):
        
        Returns:
            str | None"""
        return agent_files.read_file_content(self, filepath)

    def _get_single_file_context(self, prompt: str) -> tuple[str | None, str | None]:
        """get single file context.
        
        Args:
            prompt (str):
        
        Returns:
            tuple[str | None, str | None]"""
        return agent_files.get_single_file_context(self, prompt)

    def _get_folder_context(self, prompt: str) -> list[dict[str, Any]] | None:
        """get folder context.
        
        Args:
            prompt (str):
        
        Returns:
            list[dict[str, Any]] | None"""
        return agent_files.get_folder_context(self, prompt)

    def _create_fallback_tree(self, prompt: str) -> dict[str, Any]:
        """create fallback tree.
        
        Args:
            prompt (str):
        
        Returns:
            dict[str, Any]"""
        self.original_prompt = prompt
        return agent_tree.create_fallback_tree(self, prompt)

    def _parse_tree_from_llm(self, prompt: str, llm_response: str) -> dict[str, Any]:
        """parse tree from llm.
        
        Args:
            prompt (str):
            llm_response (str):
        
        Returns:
            dict[str, Any]"""
        return agent_tree.parse_tree_from_llm(self, prompt, llm_response)

    def _sanitize_prompt(self, prompt: str) -> str:
        """sanitize prompt.
        
        Args:
            prompt (str):
        
        Returns:
            str"""
        safe = str(prompt)[:10000]  # limit length
        safe = ''.join(c for c in safe if ord(c) >= 32 or c in '\n\r\t')
        safe = safe.replace("</user_input>", "<SECURITY_TAG>")
        return f"<user_input>\n{safe}\n<END_USER_INPUT>"

    def decompose_prompt(self, prompt: str, files: list[dict[str, Any]] | None = None, template: str | None = None) -> dict[str, Any]:
        """decompose prompt.
        
        Args:
            prompt (str):
            files (list[dict[str, Any]] | None):
            template (str | None):
        
        Returns:
            dict[str, Any]"""
        # Re-decompose loop protection — max 2 re-decomposes after failed execution
        if hasattr(self, '_redecompose_count'):
            self._redecompose_count += 1
        else:
            self._redecompose_count = 0
        if self._redecompose_count > 2:
            self._log("WARNING", "Re-decompose loop stopped",
                       f"Forsøgte at re-decompose {self._redecompose_count} gange. Stopper for at undgå uendelig loop.")
            return {
                "success": False,
                "error": f"Re-decompose loop stopped after {self._redecompose_count} attempts.",
                "root": {"name": prompt[:50], "children": [
                    {"name": prompt[:50], "status": "failed",
                     "result": "Re-decompose loop stopped — maks 2 genforsøg."}
                ]}
            }
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

        _auto_load_issue_files(self, prompt, template, files)
        _auto_load_location_file(self, prompt)

        file_context = _build_file_context(self, files, prompt)

        if self._pending_refactor:
            oversize_note = (
                f"\n\n## \u26A0\uFE0F BEM\u00C6RKNING: Filen '{self._pending_refactor['file']}' er "
                f"{self._pending_refactor['lines']} linjer (gr\u00E6nse: {agent_issues.OVERSIZE_LINE_LIMIT}).\n"
                f"Der er automatisk oprettet et REFAC-issue. "
                f"Brug `read_issue` for at se detaljer.\n"
            )
            file_context += oversize_note

        file_context = self._resolve_delegations_for_context(file_context)
        self._file_context_str = file_context
        self.full_prompt_with_context = prompt + file_context

        if template and template != "fri" and template_config.get("fallback"):
            tree = TaskTree(prompt)
            for section in template_config["fallback"]:
                tree.root.add_child(TaskNode(section))
            self.task_tree = tree
            task_count = len(template_config["fallback"]) + 1
            self._log("INFO", t(K.LOG_USING_TEMPLATE, self.lang), t(K.LOG_TASKS_CREATED, self.lang).format(n=task_count))
            return self.task_tree_to_dict()

        return _decompose_via_llm(self, prompt, file_context, template_config)

    def reset_execution(self) -> None:
        """reset execution.
        
        Returns:
            None"""
        if not self.task_tree:
            return
        def reset_node(node: TaskNode) -> None:
            """reset node.
            
            Args:
                node (TaskNode):
            
            Returns:
                None"""
            node.status = "pending"
            node.result = None
            for child in node.children:
                reset_node(child)
        reset_node(self.task_tree.root)
        self.execution_log = []
        self._log("INFO", t(K.LOG_EXECUTION_RESET, self.lang), "")

    def _count_tasks(self, node: TaskNode) -> int:
        """count tasks.
        
        Args:
            node (TaskNode):
        
        Returns:
            int"""
        return agent_tree.count_tasks(node)

    def task_tree_to_dict(self) -> dict[str, Any] | None:
        """task tree to dict.
        
        Returns:
            dict[str, Any] | None"""
        return agent_tree.task_tree_to_dict(self)

    def task_tree_from_dict(self, d: dict[str, Any]) -> None:
        """task tree from dict.
        
        Args:
            d (dict[str, Any]):
        
        Returns:
            None"""
        agent_tree.task_tree_from_dict(self, d)

    def _set_task_tools(self, task_name: str) -> None:
        """set task tools.
        
        Args:
            task_name (str):
        
        Returns:
            None"""
        agent_tasks.set_task_tools(self, task_name)

    def solve_task(self, task_node: TaskNode, original_prompt: str) -> str:
        """solve task.
        
        Args:
            task_node (TaskNode):
            original_prompt (str):
        
        Returns:
            str"""
        return agent_tasks.solve_task(self, task_node, original_prompt)

    def solve_task_stream(self, task_node: TaskNode, original_prompt: str) -> Generator[dict[str, Any], None, None]:
        """solve task stream.
        
        Args:
            task_node (TaskNode):
            original_prompt (str):
        
        Returns:
            Generator[dict[str, Any], None, None]"""
        yield from agent_tasks.solve_task_stream(self, task_node, original_prompt)

    def execute_tree(self, node: TaskNode | None = None) -> dict[str, Any]:
        """execute tree.
        
        Args:
            node (TaskNode | None):
        
        Returns:
            dict[str, Any]"""
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

    def get_agent_status(self) -> dict[str, Any]:
        """get agent status.
        
        Returns:
            dict[str, Any]"""
        return {
            "action_history": self.action_history,
            "total_actions": len(self.action_history),
            "log_entries": len(self.agent_log),
            "has_task_tree": self.task_tree is not None
        }

    def suggest_new_module(self) -> dict[str, Any]:
        """suggest new module.
        
        Returns:
            dict[str, Any]"""
        return {"message": t(K.LOG_MODULE_READY, self.lang)}
