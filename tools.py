import json
import re
import traceback

class Tool:
    def __init__(self, name, description, parameters, function):
        self.name = name
        self.description = description
        self.parameters = parameters
        self.function = function

    def to_prompt_desc(self):
        return f"- {self.name}({', '.join(self.parameters)}): {self.description}"


class ToolRegistry:
    TOOL_MARKER = "<<<TOOL>>>"
    DONE_MARKER = "<<<DONE>>>"
    END_MARKER = "<<<END>>>"

    def __init__(self):
        self.tools = {}

    def register(self, tool):
        self.tools[tool.name] = tool

    def get_tool_descriptions(self):
        lines = []
        for name, tool in self.tools.items():
            lines.append(tool.to_prompt_desc())
        return "\n".join(lines)

    def build_system_prompt(self, task):
        tools_desc = self.get_tool_descriptions()
        prompt = f"""Du er Agenten, en AI-assistent med direkte adgang til værktøjer.

TILGÆNGELIGE VÆRKTØJER:
{tools_desc}

Du kan udføre opgaver selvstændigt ved at bruge disse værktøjer.
Brug IKKE <think> tags. Svar på dansk.

Når du vil bruge et værktøj, svar KUN med ét af følgende formater:

For at kalde et værktøj:
{self.TOOL_MARKER}
{{"tool": "navn", "args": {{"param1": "værdi", ...}}}}
{self.END_MARKER}

Når opgaven er fuldstændigt løst:
{self.DONE_MARKER}
{{"result": "din samlede konklusion og resultat"}}
{self.END_MARKER}

OPGAVE: {task}"""
        return prompt

    @staticmethod
    def strip_markers(text):
        return re.sub(r'<<<TOOL>>>|<<<DONE>>>|<<<END>>>', '', text)

    def parse_response(self, response):
        tool_match = re.search(
            re.escape(self.TOOL_MARKER) + r'\s*(.*?)\s*' + re.escape(self.END_MARKER),
            response, re.DOTALL
        )
        done_match = re.search(
            re.escape(self.DONE_MARKER) + r'\s*(.*?)\s*' + re.escape(self.END_MARKER),
            response, re.DOTALL
        )

        if tool_match:
            try:
                data = json.loads(tool_match.group(1))
                return {"type": "tool", "tool": data.get("tool"), "args": data.get("args", {})}
            except json.JSONDecodeError:
                return {"type": "error", "message": "Ugyldigt JSON i tool-kald"}

        if done_match:
            try:
                data = json.loads(done_match.group(1))
                return {"type": "done", "result": data.get("result", response)}
            except json.JSONDecodeError:
                return {"type": "done", "result": done_match.group(1).strip()}

        return {"type": "text", "text": response}

    def execute(self, tool_name, args):
        if tool_name not in self.tools:
            return {"success": False, "error": f"Ukendt værktøj: {tool_name}"}
        try:
            result = self.tools[tool_name].function(**args)
            return {"success": True, "result": result}
        except Exception as e:
            return {"success": False, "error": str(e), "traceback": traceback.format_exc()}
