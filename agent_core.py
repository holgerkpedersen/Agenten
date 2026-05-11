from llm_wrapper import LMStudioWrapper
from web_searcher import WebSearcher
from task_tree import TaskTree, TaskNode
from module_builder import ModuleBuilder
import re
import time
import os

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

    TEMPLATES = {
        "resume": {
            "name": "📄 Resumé",
            "prompt": """Lav et struktureret resumé af nedenstående materiale. Returnér KUN følgende sektioner:
## Overblik
## Nøglepunkter
## Konklusion
## Anbefalinger

Svar på dansk. Brug ikke <think> tags. Ingen anden tekst:""",
            "fallback": ["Overblik", "Nøglepunkter", "Konklusion", "Anbefalinger"]
        },
        "kodeanalyse": {
            "name": "🔍 Kodeanalyse",
            "prompt": """Analyser følgende kode struktureret. Returnér KUN følgende sektioner:
## Formål
## Imports og afhængigheder
## Arkitektur
## Kodekvalitet
## Sikkerhed

Svar på dansk. Brug ikke <think> tags. Ingen anden tekst:""",
            "fallback": ["Formål", "Imports og afhængigheder", "Arkitektur", "Kodekvalitet", "Sikkerhed"]
        },
        "fri": {
            "name": "🌳 Fri nedbrydning",
            "prompt": """Nedbryd følgende opgave i delopgaver. Brug 2 mellemrum per niveau.
Returner KUN træstrukturen. Ingen forklaringer, ingen tankeprocess.

Opgave: {prompt}

Eksempel på format:
Forstå problemet
  Identificer krav
  Saml information
Find løsning
  Overvej muligheder
  Vælg metode
Konkluder

Nedbryd nu opgaven (KUN træstruktur):""",
            "fallback": None
        }
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
        template_config = self.TEMPLATES.get(template, self.TEMPLATES["fri"]) if template else self.TEMPLATES["fri"]
        self._log("INFO", "Starter nedbrydning", f"{prompt[:100]} (skabelon: {template_config['name']})")
        
        file_context = ""
        if files and len(files) > 0:
            file_context = "\n\n## FILINHOLD TIL ANALYSE:\n"
            for f in files:
                filename = f.get('filename', 'ukendt')
                content = f.get('content', '')
                file_context += f"\n### {filename}\n\n```{filename}\n{content}\n```\n"
            self._log("INFO", "Tilføjer filer til kontekst", f"{len(files)} filer")
        else:
            file_path, file_content = self._get_file_context(prompt)
            if file_content:
                file_context = "\n\n## FILINHOLD TIL ANALYSE: " + os.path.basename(file_path) + "\n\nHer er indholdet af filen, du skal analysere:\n\n```python\n" + file_content + "\n```\n\nBrug dette filindhold til at lave en konkret analyse. Identificér:\n- Hvad er filens formål?\n- Hvilke imports bruges?\n- Hvilke endpoints/routes findes?\n- Eventuelle problemer eller forbedringsmuligheder\n\n"
        
        self.full_prompt_with_context = prompt + file_context
        
        if template and template != "fri" and template_config.get("fallback"):
            if template_config.get("fallback"):
                tree = TaskTree(prompt)
                for section in template_config["fallback"]:
                    tree.root.add_child(TaskNode(section))
                self.task_tree = tree
                task_count = len(template_config["fallback"]) + 1
                self._log("INFO", "Bruger skabelon", f"{task_count} opgaver oprettet")
                return self.task_tree_to_dict()
        
        decomposition_prompt = template_config["prompt"].replace("{prompt}", prompt)
        file_context_entry = f"\n\nMateriale:{file_context}" if file_context else ""
        decomposition_prompt += file_context_entry

        self._log("LLM", "Sender forespørgsel til LLM", f"Med filkontekst: {bool(file_context)}")
        response = self.llm.generate(decomposition_prompt, temperature=0.3, max_tokens=32000)
        self._log("LLM", "Modtog svar fra LLM", f"{len(response)} tegn")
        
        response = re.sub(r'<think>.*?</think>', '', response, flags=re.DOTALL)
        
        self.task_tree = self._parse_tree_from_llm(prompt, response)
        task_count = self._count_tasks(self.task_tree.root)
        self._log("INFO", "Nedbrydning færdig", f"{task_count} opgaver oprettet")
        
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
        self._log("INFO", "Nulstillede udførelsesstatus", "")

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
        self._log("INFO", "Påbegynder opgave", task_node.name)
        solve_prompt = "Løs følgende delopgave i kontekst af den overordnede opgave.\n\nOVERORDNET OPGAVE: " + original_prompt + "\n\nDELOPGAVE: " + task_node.name + "\n\nSvar kort og præcist på dansk:"
        full_response = ""
        for chunk in self.llm.generate_stream(solve_prompt):
            full_response += chunk
        if not full_response or "ERROR" in full_response:
            full_response = "Løsning på '" + task_node.name + "':\n1. Analyser krav\n2. Implementer\n3. Verificer"
        task_node.status = "done"
        task_node.result = full_response
        self.action_history.append(task_node.name.split()[0] if task_node.name else "unknown")
        self._log("INFO", "Opgave færdig", task_node.name)
        return full_response

    def execute_tree(self, node=None):
        if node is None:
            if not self.task_tree:
                return {"error": "No task tree"}
            self._log("INFO", "Starter udførelse af opgavetræ", "")
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
        return {"message": "Modulbygger klar"}