import json
import re
import traceback
from lang import t


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
        self.lang = "da"
        self.active_tools = None

    def set_active_tools(self, names):
        self.active_tools = names

    def register(self, tool):
        self.tools[tool.name] = tool

    def get_tool_descriptions(self):
        lines = []
        for name, tool in self.tools.items():
            if self.active_tools is None or name in self.active_tools:
                lines.append(tool.to_prompt_desc())
        return "\n".join(lines)

    def build_system_prompt(self, task):
        tools_desc = self.get_tool_descriptions()
        prompt = t("tool_system_prompt", self.lang).format(
            TOOL_MARKER=self.TOOL_MARKER,
            DONE_MARKER=self.DONE_MARKER,
            END_MARKER=self.END_MARKER,
            tools_desc=tools_desc,
            task=task,
        )
        return prompt

    @staticmethod
    def strip_markers(text):
        return re.sub(r'<<<TOOL>>>|<<<DONE>>>|<<<END>>>', '', text)

    def parse_response(self, response):
        response = re.sub(r'<think>.*?</think>', '', response, flags=re.DOTALL)
        response = re.sub(r'```\w*\n?', '', response)
        response = re.sub(r'```', '', response)

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
                return {"type": "error", "message": t("tool_invalid_json", self.lang)}

        if done_match:
            try:
                data = json.loads(done_match.group(1))
                return {"type": "done", "result": data.get("result", response)}
            except json.JSONDecodeError:
                return {"type": "done", "result": done_match.group(1).strip()}

        return {"type": "text", "text": response}

def execute(self, tool_name, args):
        if tool_name in ("navn", "name", "nombre", "名称"):
            names = list(self.tools.keys()) if self.active_tools is None else self.active_tools
            tools_hint = ', '.join(names)
            return {"success": False, "error": f"'{tool_name}' er ikke et værktøj. Tilgængelige værktøjer: {tools_hint}"}
        if tool_name not in self.tools:
            return {"success": False, "error": t("tool_unknown", self.lang).format(tool=tool_name)}
        if self.active_tools is not None and tool_name not in self.active_tools:
            return {"success": False, "error": f"Tool '{tool_name}' er ikke tilgængelig i denne skabelon. Brug en af: {', '.join(self.active_tools)}"}
        try:
            result = self.tools[tool_name].function(**args)
            return {"success": True, "result": result}
        except Exception as e:
            return {"success": False, "error": str(e), "traceback": traceback.format_exc()}
