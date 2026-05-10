from llm_wrapper import LMStudioWrapper
from web_searcher import WebSearcher
from task_tree import TaskTree, TaskNode
from module_builder import ModuleBuilder
import re
import time

class Agent:
    def __init__(self):
        self.llm = LMStudioWrapper(timeout=120)
        self.searcher = WebSearcher()
        self.task_tree = None
        self.action_history = []
        self.execution_log = []
        self.agent_log = []  # Log af alle handlinger
        self.original_prompt = ""

    def _log(self, level, message, detail=""):
        """Tilføj til agent log"""
        log_entry = {
            "timestamp": time.time(),
            "level": level,  # INFO, DEBUG, ERROR, LLM
            "message": message,
            "detail": detail
        }
        self.agent_log.append(log_entry)
        print(f"[{level}] {message}: {detail}")

    def _clean_task_name(self, name):
        """Fjern sprog-relaterede instruktioner fra opgavenavne"""
        # Fjern sætninger om sprog
        patterns = [
            r'dansk.*?assistent',
            r'svar.*?p[aå] dansk',
            r'kun.*?dansk',
            r'du er.*?dansk',
            r'bruger.*?svensk',
            r'ikke.*?svensk',
            r'Danish.*?assistant',
            r'answer.*?danish',
            r'only.*?danish',
        ]
        for pattern in patterns:
            name = re.sub(pattern, '', name, flags=re.IGNORECASE)
        
        # Fjern dobbelte mellemrum
        name = re.sub(r'\s+', ' ', name).strip()
        
        # Hvis navnet bliver for kort, returner standard
        if len(name) < 5 or not name:
            return "Udfør opgave"
        return name

    def _create_fallback_tree(self, prompt):
        """Opretter et meningsfyldt træ på dansk - uden sprog-opgaver"""
        tree = TaskTree(prompt)
        self.original_prompt = prompt
        
        prompt_lower = prompt.lower()
        
        # Rens prompten for sprog-relaterede instruktioner
        clean_prompt = self._clean_task_name(prompt)
        
        if "2 + 2" in prompt_lower or "2 plus 2" in prompt_lower:
            tree.root.add_child(TaskNode("Forstå hvad spørgsmålet betyder"))
            tree.root.children[0].add_child(TaskNode("Identificer at der spørges om addition"))
            tree.root.children[0].add_child(TaskNode("Forstå at 2 repræsenterer to enheder"))
            
            tree.root.add_child(TaskNode("Find bevis for at 2+2=4"))
            tree.root.children[1].add_child(TaskNode("Brug konkrete genstande"))
            tree.root.children[1].add_child(TaskNode("Brug matematiske aksiomer"))
            
            tree.root.add_child(TaskNode("Konkluder"))
            
        elif "token" in prompt_lower or "komprimere" in prompt_lower:
            tree.root.add_child(TaskNode("Analyser nuværende token-forbrug"))
            tree.root.add_child(TaskNode("Implementer cache"))
            tree.root.add_child(TaskNode("Komprimer prompts"))
            tree.root.add_child(TaskNode("Benchmark og optimer"))
            
        else:
            tree.root.add_child(TaskNode("Analyser problemet"))
            tree.root.add_child(TaskNode("Udvikl løsningsstrategi"))
            tree.root.add_child(TaskNode("Implementer løsningen"))
            tree.root.add_child(TaskNode("Test og valider"))
        
        self._log("INFO", "Oprettede fallback træ", f"{len(tree.root.children)} hovedopgaver")
        return tree

    def _parse_tree_from_llm(self, prompt, llm_response):
        """Parser LLM's output til en rigtig træstruktur"""
        tree = TaskTree(prompt)
        self.original_prompt = prompt
        
        if llm_response.startswith("ERROR") or not llm_response.strip():
            self._log("ERROR", "LLM fejl, bruger fallback", llm_response[:100])
            return self._create_fallback_tree(prompt)
        
        lines = list(dict.fromkeys(llm_response.strip().split('\n')))
        stack = [(tree.root, 0)]
        
        for line in lines:
            if not line.strip():
                continue
            
            stripped = line.lstrip(' ')
            indent = len(line) - len(stripped)
            
            task_name = re.sub(r'^[\d\-*•]+\.?\s*', '', stripped).strip()
            
            # Spring over sprog-relaterede linjer
            if any(word in task_name.lower() for word in ['dansk', 'svensk', 'norsk', 'english', 'language', 'sprog']):
                continue
            
            if "Break down" in task_name or "Output format" in task_name or len(task_name) > 100:
                continue
            
            # Rens opgavenavnet
            task_name = self._clean_task_name(task_name)
            
            if not task_name or len(task_name) < 3:
                continue
            
            level = indent // 2
            
            while len(stack) > level + 1:
                stack.pop()
            
            parent = stack[-1][0]
            new_node = TaskNode(task_name[:80])
            parent.add_child(new_node)
            stack.append((new_node, level))
            self._log("DEBUG", "Tilføjede opgave", task_name[:50])
        
        if not tree.root.children:
            return self._create_fallback_tree(prompt)
        
        return tree

    def decompose_prompt(self, prompt):
        """Nedbryder en prompt til træstruktur - uden sprog-opgaver"""
        self.agent_log = []
        self._log("INFO", "Starter nedbrydning", prompt[:100])
        
        # Rens prompten først
        clean_prompt = self._clean_task_name(prompt)
        
        decomposition_prompt = f"""Nedbryd følgende opgave i delopgaver. Brug 2 mellemrum per niveau.

Opgave: {clean_prompt}

Eksempel:
Forstå problemet
  Identificer hvad der spørges om
  Saml relevant information
Find løsning
  Overvej muligheder
  Vælg bedste metode
Konkluder

Nedbryd nu opgaven: {clean_prompt}"""

        self._log("LLM", "Sender forespørgsel til LLM", "")
        response = self.llm.generate(decomposition_prompt, temperature=0.3, max_tokens=1024)
        self._log("LLM", "Modtog svar fra LLM", f"{len(response)} tegn")
        
        self.task_tree = self._parse_tree_from_llm(clean_prompt, response)
        
        task_count = self._count_tasks(self.task_tree.root)
        self._log("INFO", "Nedbrydning færdig", f"{task_count} opgaver oprettet")
        
        return self.task_tree_to_dict()

    def _count_tasks(self, node):
        count = 1
        for child in node.children:
            count += self._count_tasks(child)
        return count

    def task_tree_to_dict(self):
        if not self.task_tree:
            return None
        
        def node_to_dict(node):
            return {
                "name": node.name,
                "status": node.status,
                "children": [node_to_dict(child) for child in node.children]
            }
        
        return node_to_dict(self.task_tree.root)

    def solve_task(self, task_node, original_prompt):
        """Løser en enkelt opgave med kontekst"""
        task_node.status = "running"
        self._log("INFO", "Påbegynder opgave", task_node.name)
        
        solve_prompt = f"""Løs følgende delopgave.

OVERORDNET OPGAVE: {original_prompt}

DELOPGAVE: {task_node.name}

Løsningen skal være praktisk og direkte relateret til den overordnede opgave.
Svar på dansk:"""
        
        full_response = ""
        for chunk in self.llm.generate_stream(solve_prompt):
            full_response += chunk
        
        if not full_response or "ERROR" in full_response:
            full_response = f"Løsning på '{task_node.name}':\n1. Analyser krav\n2. Implementer\n3. Verificer"
        
        task_node.status = "done"
        task_node.result = full_response
        self.action_history.append(task_node.name.split()[0] if task_node.name else "unknown")
        self._log("INFO", "Opgave færdig", task_node.name)
        
        return full_response

    def get_agent_status(self):
        return {
            "action_history": self.action_history,
            "total_actions": len(self.action_history),
            "log_entries": len(self.agent_log)
        }