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
import re
import time
import os
import json
import subprocess

CHUNK_SIZE = 150000

def chunk_text(text, size=CHUNK_SIZE):
    chunks = []
    for i in range(0, len(text), size):
        chunks.append(text[i:i+size])
    return chunks


PR_REQUIRED_BEFORE_PR = {"git_add_all", "git_commit", "git_push"}
PR_COMMIT_TOOLS = {"git_add_all", "git_commit"}
PR_PUSH_TOOLS = {"git_push"}
PR_BRANCH_TOOLS = {"git_create_branch"}
PR_REMOTE_TOOLS = {"git_remote_status"}
PR_GIT_TOOLS = {"git_diff", "git_log", "git_status", "git_current_branch", "git_branch_list", "git_pull", "git_checkout"}


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
        self.lang = "da"
        self.active_template = None
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
            "Indlæs en chunk af en stor fil. Kræver: chunk (filnavn), index (1..N). Læs ALLE chunks (1,2,3...) før du analyserer — filen er delt i flere chunks. Brug 'list_chunks' først for at se tilgængelige filer.",
            ["chunk", "index"],
            lambda chunk, index=1: self._read_chunk(chunk, int(index))
        ))
        self.tool_registry.register(Tool(
            "list_chunks",
            "List alle tilgængelige filer (chunks) som kan læses med read_chunk. Brug DENNE først for at se hvad der er tilgængeligt.",
            [],
            lambda: self._list_chunks()
        ))
        self.tool_registry.register(Tool(
            "write_file",
            "Skriv indhold til en fil. Opretter mappen hvis den ikke findes. Returnerer stien og antal tegn.",
            ["path", "content"],
            lambda path, content: git_ops.write_file(path=path, content=content)
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
            lambda test_path="": self._run_pytest(test_path)
        ))
        self.tool_registry.register(Tool(
            "read_issue",
            "Læs et issue fra docs/issues/observed/issues.json. Args: issue_id (f.eks. 'BUG-003'). Returnerer issue-detaljer.",
            ["issue_id"],
            lambda issue_id: self._read_issue(issue_id)
        ))
        self.tool_registry.register(Tool(
            "update_issue_status",
            "Opdater status på et issue i docs/issues/observed/issues.json. Args: issue_id, status ('open'/'in_progress'/'resolved'), resolution_note (valgfri).",
            ["issue_id", "status"],
            lambda issue_id, status="resolved", resolution_note="": self._update_issue_status(issue_id, status, resolution_note)
        ))

    def _add_image(self, path):
        # 1. Already loaded in self.images? (match by filename or filepath)
        basename = os.path.basename(path)
        for img in self.images:
            if isinstance(img, dict):
                if img.get("filename") == basename or img.get("filepath") == path:
                    return {"success": True, "file": basename, "size": len(img.get("b64","")), "mime": img.get("mime",""), "note": "Allerede indlæst"}
                if img.get("filepath") and os.path.normpath(img["filepath"]) == os.path.normpath(path):
                    return {"success": True, "file": basename, "size": len(img.get("b64","")), "mime": img.get("mime",""), "note": "Allerede indlæst"}

        # 2. Exists on disk?
        if os.path.exists(path):
            return self._encode_and_store(path)

        # 3. Try UPLOAD_DIR
        upload_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploads", basename)
        if os.path.exists(upload_path):
            return self._encode_and_store(upload_path)

        # 4. Not found
        loaded = [f"{i.get('filename','?')} ({i.get('filepath','?')})" if isinstance(i,dict) else str(i)[:40] for i in self.images]
        return {"success": False, "error": f"Fil ikke fundet: {path}. Allerede indlæste: {loaded or 'ingen'}"}

    def _encode_and_store(self, path):
        raw_b64 = LMStudioWrapper.encode_image(path)
        size = os.path.getsize(path)
        ext = os.path.splitext(path)[1].lower().lstrip(".")
        mime = "jpeg" if ext in ("jpg","jpeg") else ext
        self.images.append({"b64": raw_b64, "mime": mime, "filename": os.path.basename(path), "filepath": path})
        self._log("TOOL", f"Billede tilføjet: {os.path.basename(path)}", f"{size:,} bytes ({ext})")
        return {"success": True, "file": os.path.basename(path), "size": size, "mime": mime}

    def _run_pytest(self, test_path=""):
        try:
            cmd = ["pytest", "-v"]
            if test_path:
                cmd.append(test_path)
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            return {"success": result.returncode == 0, "stdout": result.stdout, "stderr": result.stderr, "exit_code": result.returncode}
        except subprocess.TimeoutExpired:
            return {"success": False, "stdout": "", "stderr": "Timeout (120s)", "exit_code": -1}
        except FileNotFoundError:
            return {"success": False, "stdout": "", "stderr": "pytest not found", "exit_code": -1}

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
        if not self.file_chunks:
            return {"success": True, "chunks": [], "message": "Ingen filer indlæst. Brug 'list_chunks' igen efter at have specificeret filer eller en mappe i din prompt."}
        result = []
        for key, chunks in self.file_chunks.items():
            display = key.replace("file_", "", 1)
            result.append({"file": display, "chunks": len(chunks)})
        return {"success": True, "chunks": result, "count": len(result)}

    def _read_chunk(self, chunk, index):
        original = chunk
        if not chunk.startswith("file_"):
            chunk = "file_" + chunk
        chunks = self.file_chunks.get(chunk)
        if not chunks:
            available = [k.replace("file_", "", 1) for k in self.file_chunks.keys()] or ["ingen"]
            return {"success": False, "error": f"Ukendt chunk: '{original}'. Tilgængelige filer: {available}. Brug 'list_chunks' for at se alle."}
        if index < 1 or index > len(chunks):
            return {"success": False, "error": f"Chunk {index} findes ikke (1..{len(chunks)})"}
        return {"success": True, "chunk": chunk, "index": index, "total": len(chunks), "content": chunks[index-1]}

    TEMPLATE_TOOLS = {
        "resume": ["list_chunks", "read_chunk"],
        "kodeanalyse": ["list_chunks", "read_chunk"],
        "diffanalyse": ["list_chunks", "read_chunk", "git_diff", "git_log"],
        "fri": None,
        "agenten": [
            "list_chunks",
            "read_chunk",
            "github_create_pr",
            "git_status", "git_add_all", "git_commit", "git_push",
            "git_diff", "git_log",
            "git_create_branch", "git_current_branch", "git_pull", "git_checkout",
            "git_remote_status"
        ],
        "programmering": ["list_chunks", "read_chunk", "write_file", "add_image"],
        "python-arkitektur": ["list_chunks", "read_chunk", "write_file"],
        "billedanalyse": ["add_image", "write_file", "list_chunks", "read_chunk"],
        "bugfix": ["read_issue", "update_issue_status", "run_tests", "list_chunks", "read_chunk", "write_file"],
    }

    TEMPLATE_TASK_TOOLS = {
        "agenten": {
            "branch": ["git_current_branch", "git_create_branch", "git_branch_list", "git_checkout", "git_remote_status", "git_pull"],
            "commit": ["git_add_all", "git_commit", "git_status", "git_diff", "git_log"],
            "push": ["git_push", "git_remote_status"],
            "pull request": ["github_create_pr", "git_remote_status", "git_diff", "git_log"],
        }
    }

    SECTION_INSTRUCTIONS = {
        "resume": {
            "Overblik": "Skriv afsnittet 'Overblik': beskriv filens formål, struktur og hovedindhold.",
            "Nøglepunkter": "Skriv afsnittet 'Nøglepunkter': fremhæv de vigtigste tekniske detaljer, features og arkitektur.",
            "Konklusion": "Skriv afsnittet 'Konklusion': vurder filens kvalitet, styrker og svagheder.",
            "Anbefalinger": "Skriv afsnittet 'Anbefalinger': foreslå konkrete forbedringer og næste skridt.",
        },
        "kodeanalyse": {
            "Formål": "Skriv afsnittet 'Formål': forklar hvad filen gør og dens rolle i projektet.",
            "Imports og afhængigheder": "Skriv afsnittet 'Imports og afhængigheder': gennemgå filens imports og eksterne afhængigheder.",
            "Arkitektur": "Skriv afsnittet 'Arkitektur': analysér filens struktur, klasser og funktioner.",
            "Kodekvalitet": "Skriv afsnittet 'Kodekvalitet': vurder kodens læsbarhed, vedligeholdbarhed og test coverage.",
            "Sikkerhed": "Skriv afsnittet 'Sikkerhed': identificér potentielle sikkerhedsproblemer og sårbarheder.",
        },
        "diffanalyse": {
            "Oversigt": "Skriv afsnittet 'Oversigt': beskriv hvad diff'en indeholder af ændringer.",
            "Risikovurdering": "Skriv afsnittet 'Risikovurdering': vurder risikoen (høj/middel/lav) for hver ændret fil.",
            "Brydende ændringer": "Skriv afsnittet 'Brydende ændringer': identificér breaking changes og bagudkompatibilitet.",
            "Kodekvalitet": "Skriv afsnittet 'Kodekvalitet': vurder ændringernes kvalitet og konsistens.",
            "Anbefalinger": "Skriv afsnittet 'Anbefalinger': foreslå forbedringer til diff'en.",
        },
        "programmering": {
            "Kravanalyse": "Analyser kravene grundigt. Identificér funktionelle og ikke-funktionelle krav, input/output, og eventuelle begrænsninger. Beskriv hvad systemet skal kunne.",
            "Arkitekturdesign": "Design systemarkitekturen: komponenter, moduler, dataflow og afhængigheder. Overvej relevante design patterns og SOLID-principper. Tegn arkitekturen med tekst.",
            "Implementeringsplan": "Planlæg implementeringen: hvilke filer skal oprettes, i hvilken rækkefølge, og hvad skal hver fil indeholde. Overvej teststrategi og edge cases.",
            "Sikkerhedsanalyse": "Analyser sikkerhedsaspekter: inputvalidering, autentifikation, kryptering, håndtering af følsomme data (passwords, keys). Følg OWASP best practices og princip om mindste rettighed.",
            "Kodeimplementering": "Implementér koden baseret på arkitekturdesign og implementeringsplan. Brug write_file til at oprette/redigere hver fil. Skriv ren, vedligeholdelsesvenlig kode med korrekt fejlhåndtering og logging.",
        },
        "python-arkitektur": {
            "Arkitekturplanlægning": "Analyser projektet og planlæg arkitekturen baseret på Python/Flask/HTML/JS best practices. Brug write_file til at oprette ./docs/arkitektur.md med følgende sektioner:\n\n## Systemoversigt\n- Formål og målsætning\n- Teknologistak (Python, Flask, HTML, JS, database)\n\n## Komponentarkitektur\n- Modulopdeling og ansvar for hvert modul\n- Lagdelt struktur (præsentation, forretningslogik, data)\n- Dataflow mellem komponenter\n\n## Flask-struktur\n- Blueprint-moduler, routes, middleware\n- Request/response-lifecycle\n- Fejlhåndtering og logging\n\n## Database design\n- ORM-modeller (SQLAlchemy) og relationer\n- Migration-strategi (Alembic)\n- Indeksering og query-optimering\n\n## Sikkerhed\n- CSRF, XSS, SQL injection beskyttelse\n- Autentifikation og autorisation (Flask-Login, JWT)\n- Miljøvariabler og secrets-håndtering\n\n## Frontend (HTML/JS)\n- Template-struktur (Jinja2) og statiske filer\n- JS-moduler og event-håndtering\n- API-kommunikation (fetch/AJAX)\n\n## Udviklings-workflow\n- Virtuelt miljø og afhængighedsstyring\n- Testing (pytest, unittest)\n- Kodekvalitet (flake8, black, mypy, type hints)\n\nFølg Python best practices: PEP 8, SOLID, DRY, separation of concerns.",
        },
        "billedanalyse": {
            "Beskrivelse": "Analyser billedet og beskriv hvad der ses. Brug add_image hvis billedet ikke allerede er tilføjet. Beskriv motiv, personer, objekter, farver, layout og overordnet indtryk.",
            "Kontekst": "Kontekstualiser billedet. Hvor stammer det fra (app, hjemmeside, dokument)? Hvad er formålet? Hvilke brugere er det målrettet? Hvilken situation viser det?",
            "Detaljer": "Gennemgå specifikke detaljer: tekstindhold, UI-elementer, kodeblokke, tal, datoer, navne, fejlmeddelelser. Fremhæv alt specifikt og målbart.",
            "Vurdering": "Vurder billedets kvalitet og indhold: Hvad fungerer godt? Hvad kunne forbedres? Er der fejl, inkonsistenser eller mangler? Giv konkrete forbedringsforslag.",
            "Eksportér": "Skriv den fulde analyse til en .md fil. Brug write_file til at gemme i ./exports/billedanalyse_{timestamp}.md. Filen skal indeholde alle sektioner samlet. Brug formatet:\n\n# Billedanalyse\n\n## Beskrivelse\n...\n\n## Kontekst\n...\n\n## Detaljer\n...\n\n## Vurdering\n...",
        },
        "bugfix": {
            "Analyse": "Læs issue med read_issue(). Forstå hvad bug'en er og hvilken kode der skal ændres. Læs den relevante kildekode med read_chunk(). Forstå rodårsagen.",
            "Test (Red)": "Skriv en pytest der fanger bug'en. Gå til den relevante testfil og tilføj en test. Kør testen med run_tests() — den SKAL fejle (rød fase). Hvis testen ikke fejler, fanger den ikke bug'en.",
            "Implementering": "Ret kildekoden med den mindst mulige ændring. Brug write_file til at opdatere filen.",
            "Verifikation (Green)": "Kør testen igen med run_tests() — den SKAL bestå (grøn fase). Kør HELE testsuiten med run_tests() for at verificere ingen regressions.",
            "Opdatering": "Opdater issue-status til 'resolved' med update_issue_status(). Tilføj en kort resolution_note om hvad der blev fikset.",
        },
    }

    def _refresh_skills(self):
        self._skills = SkillLoader.load_all(lang=self.lang)

    def _match_skills(self, prompt):
        scored = SkillLoader.find_all_for_task(prompt, self._skills, top=3)
        self._active_skills = [s for s in scored if s.get("base") or self._has_matching_intent(s)]
        template_match = [s for s in self._skills if s.get("template") and s["template"] == self.active_template and s not in self._active_skills]
        self._active_skills.extend(template_match)
        if self._active_skills:
            names = [f"{s['name']}[{'BASE' if s.get('base') else 'MATCH'}]" for s in self._active_skills]
            self._log("SKILL", f"Aktive skills ({len(self._active_skills)})", ", ".join(names))
        else:
            self._log("SKILL", "Ingen skills matchede", "")
        return self._active_skills

    def _has_matching_intent(self, skill):
        intent = skill.get("template") or skill.get("intent")
        return not self.active_template or intent == self.active_template or skill.get("base")

    def _record_outcome(self, task_node):
        try:
            from skill_tracker import tracker
            skill_name = "__none__"
            for s in self._active_skills:
                if not s.get("base"):
                    skill_name = s["name"]
                    break
            duration = int((time.time() - self._task_start_time) * 1000) if self._task_start_time else 0
            tracker.record(
                skill_name=skill_name,
                task_summary=task_node.name,
                success=task_node.status == "done",
                duration_ms=duration,
                template=self.active_template or "",
            )
        except ImportError:
            pass

    def _evolve_if_needed(self):
        try:
            from skill_evolution import evolve_if_needed
            result = evolve_if_needed(dry_run=True)
            if result.get("status") == "evolved":
                self._log("SKILLFLOW", "Evolution triggered", f"{len(result.get('analysis', {}).get('actions', []))} actions")
            elif result.get("status") == "ok":
                self._log("SKILLFLOW", "Analysis ready", f"{len(result.get('actions', []))} actions available")
        except ImportError:
            pass

    def _format_skills_for_prompt(self):
        if not self._active_skills:
            return ""
        lines = ["\n## 📋 Retningslinjer (ikke værktøjer)\n"]
        for s in self._active_skills:
            tag = "BASE" if s.get("base") else "MATCH"
            lines.append(f"- **{s['name']}** [{tag}]: {s.get('description', '')[:120]}")
        return "\n".join(lines)

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
            "programmering": {
                "name": t(K.T_PROGRAMMERING, self.lang),
                "prompt": t(K.TP_PROGRAMMERING, self.lang),
                "fallback": t(K.TF_PROGRAMMERING, self.lang),
            },
            "python-arkitektur": {
                "name": t(K.T_PYTHON_ARKITEKTUR, self.lang),
                "prompt": t(K.TP_PYTHON_ARKITEKTUR, self.lang),
                "fallback": t(K.TF_PYTHON_ARKITEKTUR, self.lang),
            },
            "billedanalyse": {
                "name": t(K.T_BILLEDANALYSE, self.lang),
                "prompt": t(K.TP_BILLEDANALYSE, self.lang),
                "fallback": t(K.TF_BILLEDANALYSE, self.lang),
            },
            "bugfix": {
                "name": t(K.T_BUGFIX, self.lang),
                "prompt": t(K.TP_BUGFIX, self.lang),
                "fallback": t(K.TF_BUGFIX, self.lang),
            },
        }

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
        ext = os.path.splitext(filepath)[1].lower()
        if ext in {'.png','.jpg','.jpeg','.gif','.webp','.bmp','.ico','.svg','.pdf','.zip','.exe','.dll'}:
            return None
        try:
            if not os.path.exists(filepath):
                return None
            with open(filepath, 'r', encoding='utf-8') as f:
                content = f.read()
            if len(content) > 50000:
                content = content[:50000] + "\n" + t(K.FILE_TRUNCATED, self.lang)
            return content
        except (UnicodeDecodeError, Exception) as e:
            self._log("WARNING", f"Kan ikke læse {os.path.basename(filepath)} som tekst", str(e))
            return None

    FOLDER_SCAN_EXCLUDE = {'node_modules', '.git', 'venv', '.venv', '__pycache__', '.opencode', '.agent_storage'}
    FOLDER_SCAN_EXTENSIONS = {'.py', '.js', '.json', '.html', '.css', '.yml', '.yaml', '.toml', '.env', '.md', '.txt', '.bat', '.cfg', '.ini', '.sh', '.jsx', '.ts', '.tsx', '.vue', '.svelte'}
    FOLDER_SCAN_MAX_FILES = 20
    FOLDER_SCAN_MAX_DEPTH = 2

    def _get_single_file_context(self, prompt):
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

    def _get_folder_context(self, prompt):
        folder_pattern = re.compile(r'(?:[A-Za-z]:[\\/][^\s,;""\']+|/[^\s,;""\']+)')
        folders = set()
        for match in folder_pattern.finditer(prompt):
            raw = match.group(0)
            path = os.path.normpath(raw)
            if os.path.isdir(path):
                folders.add(path)
            elif os.path.isfile(path):
                parent = os.path.dirname(path)
                if os.path.isdir(parent):
                    folders.add(parent)

        if not folders:
            return None

        self._log("INFO", "Automatisk scanning af mapper", ", ".join(sorted(folders)))

        found_files = []
        for folder in sorted(folders):
            for dirpath, dirnames, filenames in os.walk(folder):
                rel = os.path.relpath(dirpath, folder)
                depth = 0 if rel == '.' else rel.count(os.sep) + 1
                if depth > self.FOLDER_SCAN_MAX_DEPTH:
                    dirnames.clear()
                    continue
                dirnames[:] = [d for d in dirnames if d not in self.FOLDER_SCAN_EXCLUDE]
                for f in sorted(filenames):
                    ext = os.path.splitext(f)[1].lower()
                    if ext not in self.FOLDER_SCAN_EXTENSIONS and not f.startswith('.env'):
                        continue
                    if len(found_files) >= self.FOLDER_SCAN_MAX_FILES:
                        break
                    filepath = os.path.join(dirpath, f)
                    content = self._read_file_content(filepath)
                    if content:
                        relpath = os.path.relpath(filepath, folder)
                        found_files.append({"filename": relpath, "content": content, "path": filepath})
                if len(found_files) >= self.FOLDER_SCAN_MAX_FILES:
                    break
            if len(found_files) >= self.FOLDER_SCAN_MAX_FILES:
                break

        if not found_files:
            self._log("WARNING", "Ingen relevante filer fundet i mapper", ", ".join(sorted(folders)))
            return None

        for item in found_files:
            self._log("INFO", "Fundet fil", item["path"])
        return found_files

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
        self._refresh_skills()
        templates = self._get_templates()

        if not template:
            suggested = SkillLoader.suggest_template(prompt, self._skills)
            if suggested and suggested in templates:
                template = suggested

        template_config = templates.get(template, templates["fri"]) if template else templates["fri"]
        self.active_template = template
        allowed = self.TEMPLATE_TOOLS.get(template) if template else None
        self.tool_registry.set_active_tools(allowed)
        self._log("INFO", t(K.LOG_DECOMPOSE_START, self.lang), f"{prompt[:100]} ({t('ui.using_template', self.lang).format(name=template_config['name'])})")

        self._match_skills(prompt)

        self.file_context = files or []
        self.file_chunks = {}

        file_context = ""
        if files and len(files) > 0:
            file_context = t(K.FILE_CONTEXT_HEADER, self.lang)
            for f in files:
                filename = f.get('filename', t(K.UNKNOWN, self.lang))
                content = f.get('content', '')
                chunk_key = f"file_{filename}"
                chunks = chunk_text(content)
                self.file_chunks[chunk_key] = chunks
                if len(chunks) <= 1:
                    file_context += f"\n### {filename}\n\n```{filename}\n{content}\n```\n"
                else:
                    file_context += f"\n### {filename} (chunk 1/{len(chunks)}, ~{CHUNK_SIZE}tgn/chunk)\n\n```{filename}\n{chunks[0]}\n```\n"
                    file_context += f"\n*Filen er stor — indlæs flere chunks med read_chunk(chunk='{chunk_key}', index=2..{len(chunks)})*\n"
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
                    chunks = chunk_text(content)
                    self.file_chunks[chunk_key] = chunks
                    if len(chunks) <= 1:
                        file_context += f"\n### {filename}\n\n```{filename}\n{content}\n```\n"
                    else:
                        file_context += f"\n### {filename} (chunk 1/{len(chunks)}, ~{CHUNK_SIZE}tgn/chunk)\n\n```{filename}\n{chunks[0]}\n```\n"
                        file_context += f"\n*Filen er stor — indlæs flere chunks med read_chunk(chunk='{chunk_key}', index=2..{len(chunks)})*\n"
                self._log("INFO", t(K.LOG_ADDING_FILES, self.lang), t(K.LOG_N_FILES, self.lang).format(n=len(scanned_files)))
            else:
                file_path, file_content = self._get_single_file_context(prompt)
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
        self._task_start_time = time.time()
        self._log("INFO", t(K.LOG_TASK_START, self.lang), f"{task_node.name} (model: {self.llm.model})")
        self._set_task_tools(task_node.name)
        self._checkpoint_tools = set()
        self._checkpoint_branch = ""

        available_keys = list(self.file_chunks.keys())
        is_chunked = any(len(v) > 1 for v in self.file_chunks.values())
        single_file = len(available_keys) == 1
        if single_file and not is_chunked and self.tool_registry.active_tools and 'read_chunk' in self.tool_registry.active_tools:
            self.tool_registry.active_tools = [t for t in self.tool_registry.active_tools if t != 'read_chunk']
        if is_chunked:
            chunk_hint_parts = []
            for key in available_keys:
                total = len(self.file_chunks[key])
                display = key.replace("file_", "", 1)
                chunk_hint_parts.append(f"\n  read_chunk(chunk='{display}', index=2..{total}) eller chunk='{key}', index=2..{total}")
            chunk_hint = f"\n\n## TILGÆNGELIGE FILER (brug read_chunk for at læse alle chunks):{''.join(chunk_hint_parts)}\n"
        else:
            chunk_hint = ""

        section_instr = self.SECTION_INSTRUCTIONS.get(self.active_template, {}).get(task_node.name, "")
        if section_instr:
            task_prompt = f"{section_instr}\n\nKontekst / Context: {original_prompt}{chunk_hint}"
        else:
            task_prompt = f"{task_node.name}\n\nKontekst / Context: {original_prompt}{chunk_hint}"

        prev_results = []
        if task_node.parent:
            for sibling in task_node.parent.children:
                if sibling is not task_node and sibling.status == "done" and sibling.result:
                    shortened = sibling.result[:800] + ("..." if len(sibling.result) > 800 else "")
                    prev_results.append(f"## Resultat fra '{sibling.name}':\n{shortened}")
        if prev_results:
            task_prompt += "\n\n---\n## Foregående resultater (brug disse i din besvarelse):\n" + "\n\n".join(prev_results)

        self._refresh_skills()
        self._match_skills(original_prompt)
        skills_block = self._format_skills_for_prompt()
        if skills_block:
            task_prompt = skills_block + task_prompt
            self._log("SKILL", "Skills injectet i prompt", skills_block[:200])

        system_prompt = self.tool_registry.build_system_prompt(task_prompt)
        self._log("DEBUG", f"file_chunks keys: {list(self.file_chunks.keys())}", "")
        self._log("DEBUG", f"original_prompt length: {len(original_prompt)}", f"starts with: {original_prompt[:100]}")
        self._log("DEBUG", f"system_prompt length: {len(system_prompt)}", f"contains file content: {'###' in system_prompt}")

        tools_list = ', '.join([k for k in self.tool_registry.tools if self.tool_registry.active_tools is None or k in self.tool_registry.active_tools])
        lang_instr = t(K.ANSWER_IN, self.lang)
        user_guidance = f"{lang_instr}. "
        if chunk_hint:
            user_guidance += chunk_hint.replace("## TILGÆNGELIGE FILER (brug read_chunk for at læse alle chunks):", "FILER:").strip() + " "
        if tools_list:
            user_guidance += t(K.TOOL_CONTINUATION, self.lang).format(
                tools_list=tools_list,
                TOOL_MARKER=self.tool_registry.TOOL_MARKER,
                DONE_MARKER=self.tool_registry.DONE_MARKER
            )
        else:
            user_guidance += t(K.DONE_CONTINUATION, self.lang).format(DONE_MARKER=self.tool_registry.DONE_MARKER)

        if not chunk_hint and tools_list:
            read_only = all(t not in ('write_file',) for t in self.tool_registry.active_tools or [])
            if read_only and not self.images:
                user_guidance += f"\n\nOBS: Ingen filer er indlæst. Du KAN svare direkte med <<<DONE>>> uden at kalde værktøjer først. Spørg IKKE efter filnavne — brug din egen viden til at besvare opgaven."

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_guidance}
        ]
        self._log("LLM", "System prompt", f"{len(system_prompt)} chars — {system_prompt[:300]}...")
        self._log("LLM", "User guidance", user_guidance)

        full_response = ""
        text_fallback = ""
        max_iterations = 15 if self._is_pr_workflow(task_node.name) else 10
        called_tools = {}

        def _add_user_msg(content):
            nonlocal messages
            messages.append({"role": "user", "content": content})

        def _truncate_messages():
            nonlocal messages
            def _content_len(m):
                c = m.get("content", "")
                if isinstance(c, str):
                    return len(c)
                if isinstance(c, list):
                    return sum(p.get("text", "").__len__() if isinstance(p, dict) and p.get("type") == "text" else 0 for p in c)
                return 0
            total = sum(_content_len(m) for m in messages)
            if total > self.max_conversation_chars and len(messages) > 3:
                mid = "\n[... tidligere kontekst afkortet ...]"
                keep = self.max_conversation_chars - _content_len(messages[0]) - _content_len(messages[1]) - len(mid)
                if keep > 0:
                    tail_content = messages[-1]["content"]
                    if isinstance(tail_content, str):
                        cropped = tail_content[-keep:] if len(tail_content) > keep else tail_content
                    else:
                        cropped = "[...]"
                    messages = messages[:2] + [{"role": "user", "content": mid + cropped}]

        for i in range(max_iterations):
            if self.stop_requested:
                break

            # Check for user reply
            if self.pending_reply:
                messages.append({"role": "user", "content": self.pending_reply})
                self._log("USER", "Bruger svarer", self.pending_reply[:100])
                self.pending_reply = None

            response = ""
            for chunk in self.llm.generate_stream(messages=messages, temperature=0.3, max_tokens=self.max_tokens, images=self.images):
                if self.stop_requested:
                    break
                response += chunk
                yield {"type": "chunk", "chunk": chunk}

            if self.stop_requested:
                break

            messages.append({"role": "assistant", "content": response})

            parsed = self.tool_registry.parse_response(response)
            self._log("LLM", t(K.LOG_ITERATION, self.lang).format(n=i+1), t(K.LOG_TYPE, self.lang).format(type=parsed.get('type')))
            self._log("LLM", "LLM response (raw)", response)

            if parsed["type"] == "tool":
                tool_key = parsed['tool'] + str(parsed.get('args', {}))
                dup_count = called_tools.get(tool_key, 0)
                called_tools[tool_key] = dup_count + 1

                if dup_count >= 2:
                    _add_user_msg(f"{t(K.SYS_ERROR_PREFIX, self.lang)}: Du har allerede dette resultat. Gå videre eller brug <<<DONE>>>.")
                    _truncate_messages()
                    continue

                if dup_count == 1:
                    self._log("TOOL", t(K.TOOL_DUPLICATE, self.lang), parsed['tool'])

                self._log("TOOL", t(K.LOG_TOOL_CALLING, self.lang).format(tool=parsed['tool']), str(parsed.get("args", {})))
                result = self.tool_registry.execute(parsed["tool"], parsed["args"])
                result_str = json.dumps(result, ensure_ascii=False)
                self._log("TOOL", t(K.LOG_TOOL_RESULT, self.lang).format(tool=parsed['tool']), result_str)
                yield {"type": "tool_call", "tool": parsed["tool"], "args": parsed.get("args", {})}
                yield {"type": "tool_result", "tool": parsed["tool"], "result": result}

                checkpoint_msg = self._verify_pr_step(parsed["tool"], result, task_node.name, original_prompt)
                if checkpoint_msg:
                    _add_user_msg(f"!!! CHECKPOINT - {checkpoint_msg}")
                    self._log("INFO", "CHECKPOINT", checkpoint_msg)
                    yield {"type": "checkpoint", "message": checkpoint_msg, "tool": parsed["tool"]}
                else:
                    self._checkpoint_tools.add(parsed["tool"] + str(parsed.get("args", {})))
                    cont_hint = t(K.TOOL_CONTINUATION, self.lang).format(
                        tools_list=tools_list,
                        TOOL_MARKER=self.tool_registry.TOOL_MARKER,
                        DONE_MARKER=self.tool_registry.DONE_MARKER
                    )
                    _add_user_msg(f"{t(K.TOOL_RESULT_PREFIX, self.lang).format(tool=parsed['tool'])}\n{result_str}\n\n{cont_hint}")

                _truncate_messages()
                total_calls = sum(called_tools.values())
                if total_calls >= 8:
                    full_response = t(K.LOG_AUTO_DONE, self.lang).format(count=total_calls)
                    break
                continue

            if parsed["type"] == "done":
                if self._is_pr_workflow(task_node.name) and not called_tools:
                    _add_user_msg(f"{t(K.SYS_ERROR_PREFIX, self.lang)}: Du kaldte <<<DONE>>> uden at bruge nogen værktøjer. Brug værktøjerne først.")
                    _truncate_messages()
                    continue

                if self._is_pr_workflow(task_node.name):
                    called_names = {t.split("{")[0] for t in self._checkpoint_tools}
                    if "github_create_pr" not in called_names:
                        msg = f"!!! CHECKPOINT - {t(K.CP_PR_FAILED, self.lang)}"
                        _add_user_msg(msg)
                        self._log("INFO", "CHECKPOINT", t(K.CP_PR_FAILED, self.lang))
                        yield {"type": "checkpoint", "message": t(K.CP_PR_FAILED, self.lang), "tool": "done"}
                        _truncate_messages()
                        continue
                    missing_commit = PR_COMMIT_TOOLS - called_names
                    if missing_commit:
                        msg = f"!!! CHECKPOINT - {t(K.CP_NO_COMMIT, self.lang)}"
                        _add_user_msg(msg)
                        self._log("INFO", "CHECKPOINT", t(K.CP_NO_COMMIT, self.lang))
                        yield {"type": "checkpoint", "message": t(K.CP_NO_COMMIT, self.lang), "tool": "done"}
                        _truncate_messages()
                        continue
                    if "git_push" not in called_names:
                        msg = f"!!! CHECKPOINT - {t(K.CP_NO_PUSH, self.lang)}"
                        _add_user_msg(msg)
                        self._log("INFO", "CHECKPOINT", t(K.CP_NO_PUSH, self.lang))
                        yield {"type": "checkpoint", "message": t(K.CP_NO_PUSH, self.lang), "tool": "done"}
                        _truncate_messages()
                        continue

                full_response = parsed["result"]
                done_idx = response.find(self.tool_registry.DONE_MARKER)
                if done_idx > 0:
                    pre_done = response[:done_idx].strip()
                    if len(pre_done.strip()) > max(50, len(full_response) * 2):
                        full_response = pre_done
                break

            if parsed["type"] == "error":
                _add_user_msg(f"{t(K.SYS_ERROR_PREFIX, self.lang)}: {parsed['message']}")
                _truncate_messages()
                continue

            if i == 0 and not called_tools:
                all_files_loaded = all(len(v) <= 1 for v in self.file_chunks.values()) if self.file_chunks else True
                if all_files_loaded and parsed["type"] in ("text", "done"):
                    text_fallback = response.strip() if parsed["type"] == "text" else parsed.get("result", response.strip())
                    if text_fallback and "ERROR" not in text_fallback and not text_fallback.startswith("<<<"):
                        full_response = text_fallback
                        break
                if parsed["type"] == "text":
                    tool_for_msg = self.tool_registry.active_tools[0] if self.tool_registry.active_tools else t(K.SYS_FALLBACK_TOOL, self.lang)
                    _add_user_msg(f"{t(K.SYS_ERROR_PREFIX, self.lang)}: {t(K.FIRST_TOOL_REQUIRED, self.lang).format(tool=tool_for_msg)}")
                    _truncate_messages()
                    continue

            clean = response.strip() if "ERROR" not in response else ""
            if clean:
                text_fallback = clean
            _add_user_msg(t(K.TOOL_NO_RESULT, self.lang))
            _truncate_messages()
            full_response = response
            if i >= 3:
                break

        if not full_response or "ERROR" in full_response:
            if called_tools:
                full_response = t(K.LOG_AUTO_DONE, self.lang).format(count=len(called_tools))
                task_node.status = "done"
            elif text_fallback and "ERROR" not in text_fallback:
                full_response = text_fallback
                task_node.status = "done"
            else:
                full_response = t(K.LOG_TASK_FAILED, self.lang)
                task_node.status = "failed"
        else:
            task_node.status = "done"

        task_node.result = full_response
        if task_node.status == "done":
            bad_patterns = ["angiv venligst", "hvilken fil", "hvilket filnavn", "which file", "what file",
                          "venligst angiv", "specificer fil", "give me the file", "jeg har brug for filen",
                          "send mig filen"]
            is_short = len(full_response) < 100
            asks_for_files = any(p in full_response.lower() for p in bad_patterns)
            if is_short or asks_for_files:
                self._log("WARNING", "Mistænkeligt kort resultat", f"{len(full_response)} tegn, asks_for_files={asks_for_files}")
                full_response = full_response + "\n\n⚠️  ADVARSEL: Dette resultat ser ufuldstændigt ud. Overvej at køre opgaven igen med en tydeligere prompt."
        self.action_history.append(task_node.name.split()[0] if task_node.name else "unknown")
        self._record_outcome(task_node)
        if task_node.status == "failed":
            self._log("INFO", t(K.LOG_TASK_FAILED, self.lang), task_node.name)
        else:
            self._log("INFO", t(K.LOG_TASK_DONE, self.lang), task_node.name)
        self._evolve_if_needed()
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
