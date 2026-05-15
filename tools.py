import json
import re
import traceback
from lang import t
from i18n import K


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
        active_names = list(self.tools.keys()) if self.active_tools is None else self.active_tools
        example_tool = active_names[0] if active_names else t(K.SYS_FALLBACK_TOOL, self.lang)
        marker_warning = t(K.SYS_MARKER_WARNING, self.lang).format(TOOL=self.TOOL_MARKER, DONE=self.DONE_MARKER)
        prompt = f"{t('answer_in', self.lang)}. {marker_warning}.\n\n"
        prompt += t(K.TOOL_SYSTEM_PROMPT, self.lang).format(
            TOOL_MARKER=self.TOOL_MARKER,
            DONE_MARKER=self.DONE_MARKER,
            END_MARKER=self.END_MARKER,
            tools_desc=tools_desc,
            task=task,
        )
        if active_names:
            example_prefix = t(K.SYS_EXAMPLE_PREFIX, self.lang)
            prompt += f"\n\n{example_prefix}: {self.TOOL_MARKER}{{\"tool\":\"{example_tool}\",\"args\":{{}}}}{self.END_MARKER}"
        return prompt

    @staticmethod
    def strip_markers(text):
        return re.sub(r'<<<TOOL>>>|<<<DONE>>>|<<<END>>>', '', text)

    def parse_response(self, response):
        response = re.sub(r'<think>.*?</think>', '', response, flags=re.DOTALL)
        response = re.sub(r'<\|channel\|>.*?<\|end\|>', '', response, flags=re.DOTALL)
        response = re.sub(r'<\|channel>.*?<\|end>', '', response, flags=re.DOTALL)
        response = re.sub(r'<\|[^|]*\|>', '', response)
        response = re.sub(r'<\|[^|]*>', '', response)
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
                tool_name = data.get("tool", "")
                if tool_name in ("navn", "name", "nombre", "名称"):
                    names = list(self.tools.keys()) if self.active_tools is None else self.active_tools
                    return {"type": "error", "message": t(K.TOOL_HALLUCINATED, self.lang).format(tool=tool_name, tools=', '.join(names))}
                return {"type": "tool", "tool": tool_name, "args": data.get("args", {})}
            except json.JSONDecodeError:
                return {"type": "error", "message": t(K.TOOL_INVALID_JSON, self.lang)}

        if done_match:
            try:
                data = json.loads(done_match.group(1))
                result_val = data.get("result", response)
                if not isinstance(result_val, str):
                    result_val = json.dumps(result_val, ensure_ascii=False)
                return {"type": "done", "result": result_val}
            except json.JSONDecodeError:
                return {"type": "done", "result": done_match.group(1).strip()}

        return {"type": "text", "text": response}

    def execute(self, tool_name, args):
        if tool_name in ("navn", "name", "nombre", "\u540d\u79f0"):
            names = list(self.tools.keys()) if self.active_tools is None else self.active_tools
            tools_hint = ', '.join(names)
            return {"success": False, "error": t(K.TOOL_HALLUCINATED, self.lang).format(tool=tool_name, tools=tools_hint)}
        if tool_name not in self.tools:
            return {"success": False, "error": t(K.TOOL_UNKNOWN, self.lang).format(tool=tool_name)}
        if self.active_tools is not None and tool_name not in self.active_tools:
                return {"success": False, "error": t(K.TOOL_BLOCKED, self.lang).format(tool=tool_name, tools=', '.join(self.active_tools))}
        try:
            result = self.tools[tool_name].function(**args)
            return {"success": True, "result": result}
        except Exception as e:
            return {"success": False, "error": str(e), "traceback": traceback.format_exc()}
