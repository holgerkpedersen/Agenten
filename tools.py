"""Tool registry and execution for Agent."""

import json
import re
import inspect
import traceback
from typing import Any
import config
from lang import t
from i18n import K
from config import get_logger
log = get_logger(__name__)


def _strip_llm_tags(text: str) -> str:
    """Strip thought tags and channel markers from LLM output.
    Shared between ToolRegistry.parse_response() and decompose path."""
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
    text = re.sub(r'<\|channel\|>.*?<\|end\|>', '', text, flags=re.DOTALL)
    text = re.sub(r'<\|channel>.*?<\|end>', '', text, flags=re.DOTALL)
    text = re.sub(r'<\|channel>thought\s*<channel\|>.*?(?=<\|channel>|\Z)', '', text, flags=re.DOTALL)
    text = re.sub(r'<\|[^|]*\|>', '', text)
    text = re.sub(r'<\|[^|]*>', '', text)
    text = re.sub(r'<\|?channel\|?>.*$', '', text, flags=re.MULTILINE)
    return text


class Tool:
    """tool."""
    def __init__(self, name: str, description: str, parameters: list[str], function: Any, optional_params: list[str] | None = None) -> None:
        """Initialize the instance.
        
        Args:
            name:
            description:
            parameters:
            function:
            optional_params:"""
        self.name = name
        self.description = description
        self.function = function

        sig = inspect.signature(function)
        sig_params = [p for p in sig.parameters if p != 'self']
        sig_optional = {p for p, par in sig.parameters.items() if p != 'self' and par.default is not par.empty}

        if set(parameters) != set(sig_params):
            config.get_logger(__name__).warning(
                "Tool '%s': manual parameters %s don't match signature %s — auto-correcting",
                name, parameters, sig_params
            )
            self.parameters = sig_params
        else:
            self.parameters = parameters

        self.optional_params = sig_optional
        if optional_params:
            self.optional_params = set(optional_params)

    def to_prompt_desc(self) -> str:
        """to prompt desc."""
        parts = []
        for p in self.parameters:
            parts.append(f"[{p}]" if p in self.optional_params else p)
        return f"- {self.name}({', '.join(parts)}): {self.description}"


class ToolRegistry:
    """tool registry."""
    TOOL_MARKER = "<<<TOOL>>>"
    DONE_MARKER = "<<<DONE>>>"
    END_MARKER = "<<<END>>>"

    def __init__(self) -> None:
        """Initialize the instance."""
        self.tools = {}
        self.lang = "da"
        self.active_tools = None

    def set_active_tools(self, names: list[str]) -> None:
        """set active tools.
        
        Args:
            names:"""
        self.active_tools = names

    def register(self, tool: Tool) -> None:
        """register.
        
        Args:
            tool:"""
        self.tools[tool.name] = tool

    def get_tool_descriptions(self) -> str:
        """get tool descriptions."""
        lines = []
        for name, tool in self.tools.items():
            if self.active_tools is None or name in self.active_tools:
                lines.append(tool.to_prompt_desc())
        return "\n".join(lines)

    def get_openai_tools_for_active(self) -> list[dict]:
        """get openai tools for active."""
        tools = []
        for name, tool in self.tools.items():
            if self.active_tools is not None and name not in self.active_tools:
                continue
            required = [p for p in tool.parameters if p not in tool.optional_params]
            param_descs = {}
            for p in tool.parameters:
                param_descs[p] = {
                    "type": "string",
                    "description": t(K.TOOL_PARAM_DESC, self.lang).format(param=p, tool=tool.name, desc=tool.description)
                }
            tools.append({
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": {
                        "type": "object",
                        "properties": param_descs,
                        "required": required,
                    }
                }
            })
        return tools

    def build_system_prompt(self, task: str) -> str:
        """build system prompt.
        
        Args:
            task:"""
        tools_desc = self.get_tool_descriptions()
        active_names = list(self.tools.keys()) if self.active_tools is None else self.active_tools
        if not active_names:
            answer_in = t('answer_in', self.lang)
            return f'{answer_in}. Svar KUN med <<<DONE>>>{{"result":"DIN KONKLUSION — ikke bare done"}}<<<END>>>. Skriv en detaljeret opsummering af hvad du fandt og hvilke beslutninger du traf.\n\n{task}'
        example_tool = active_names[0]

        if config.NATIVE_TOOLS:
            prompt = f"{t('answer_in', self.lang)}. {t(K.SYS_USE_AVAILABLE_TOOLS, self.lang)}\n\n"
            prompt += t(K.TOOL_SYSTEM_PROMPT_NATIVE, self.lang).format(
                tools_desc=tools_desc,
                task=task,
            )
            return prompt

        marker_warning = t(K.SYS_MARKER_WARNING, self.lang).format(TOOL=self.TOOL_MARKER, DONE=self.DONE_MARKER)
        prompt = f"{t('answer_in', self.lang)}. {marker_warning}.\n\n"
        prompt += t(K.TOOL_SYSTEM_PROMPT, self.lang).format(
            TOOL_MARKER=self.TOOL_MARKER,
            DONE_MARKER=self.DONE_MARKER,
            END_MARKER=self.END_MARKER,
            tools_desc=tools_desc,
            task=task,
        )
        strict_rule = t(K.STRICT_TOOL_RULE, self.lang).format(
            TOOL_MARKER=self.TOOL_MARKER, END_MARKER=self.END_MARKER)
        prompt += f"\n\n**{strict_rule}**"
        example_prefix = t(K.SYS_EXAMPLE_PREFIX, self.lang)
        real_example = active_names[0]
        first_params = [p for p in self.tools[real_example].parameters][:2] if real_example in self.tools else []
        param_kvs = ", ".join(f'"{p}":"..."' for p in first_params)
        prompt += f"\n\n{example_prefix}: {self.TOOL_MARKER}{{\"tool\":\"{real_example}\",\"args\":{{{param_kvs}}}}}{self.END_MARKER}"
        return prompt

    @staticmethod
    def strip_markers(text: str) -> str:
        """strip markers.
        
        Args:
            text:"""
        return re.sub(r'<<<TOOL>>>|<<<DONE>>>|<<<END>>>', '', text)

    def parse_response(self, response: str) -> dict:
        """parse response.
        
        Args:
            response:"""
        response = _strip_llm_tags(response)
        response = re.sub(r'\bfinal\s*(?=<<<)', '', response)
        response = re.sub(r'```\w*\n?', '', response)
        response = re.sub(r'```', '', response)

        end_pat = r'<<<END>>>?'
        tool_match = re.search(
            re.escape(self.TOOL_MARKER) + r'\s*(.*?)\s*' + end_pat,
            response, re.DOTALL
        )
        done_match = re.search(
            re.escape(self.DONE_MARKER) + r'\s*(.*?)\s*' + end_pat,
            response, re.DOTALL
        )

        if tool_match:
            raw = tool_match.group(1)
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                try:
                    data, idx = json.JSONDecoder().raw_decode(raw)
                    if idx < len(raw) and raw[idx:].strip() and not all(c in ']})>' for c in raw[idx:].strip()):
                        raise ValueError("Trailing content after JSON")
                except (json.JSONDecodeError, ValueError):
                    escaped = raw.replace('\r\n', '\\n').replace('\r', '\\n').replace('\n', '\\n')
                    try:
                        data = json.loads(escaped)
                    except json.JSONDecodeError:
                        try:
                            data, idx = json.JSONDecoder().raw_decode(escaped)
                            if idx < len(escaped) and escaped[idx:].strip() and not all(c in ']})>' for c in escaped[idx:].strip()):
                                raise ValueError("Trailing content after JSON")
                        except (json.JSONDecodeError, ValueError):
                            return {"type": "error", "message": t(K.TOOL_INVALID_JSON, self.lang)}
            tool_name = data.get("tool", "")
            if tool_name.lower() in ("navn", "name", "nombre", "名称"):
                names = list(self.tools.keys()) if self.active_tools is None else self.active_tools
                return {"type": "error", "message": t(K.TOOL_HALLUCINATED, self.lang).format(tool=tool_name, tools=', '.join(names))}
            args = data.get("args", {})
            if not isinstance(args, dict):
                args = {}
            return {"type": "tool", "tool": tool_name, "args": args}

        # Handle truncated tool call (starts with marker but missing END)
        if self.TOOL_MARKER in response and self.END_MARKER not in response:
            return {"type": "error", "message": "Your response was truncated (missing <<<END>>>). Use shorter content or split into smaller chunks."}

        # Handle malformed tool tag (missing opening <<<)
        loose_tool = re.search(r'(?:<<<)?TOOL>>>\s*(.*?)\s*' + end_pat, response, re.DOTALL)
        if loose_tool and not done_match:
            raw = loose_tool.group(1)
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                escaped = raw.replace('\r\n', '\\n').replace('\r', '\\n').replace('\n', '\\n')
                try:
                    data = json.loads(escaped)
                except json.JSONDecodeError:
                    return {"type": "error", "message": "JSON parse error"}
            if isinstance(data, dict):
                tool_name = data.get("tool", "")
                if tool_name.lower() not in ("navn", "name", "nombre", "\u540d\u79f0"):
                    return {"type": "tool", "tool": tool_name, "args": data.get("args", {})}
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

    def execute(self, tool_name: str, args: dict) -> dict:
        """execute.
        
        Args:
            tool_name:
            args:"""
        if tool_name.lower() in ("navn", "name", "nombre", "\u540d\u79f0"):
            names = list(self.tools.keys()) if self.active_tools is None else self.active_tools
            tools_hint = ', '.join(names)
            return {"success": False, "error": t(K.TOOL_HALLUCINATED, self.lang).format(tool=tool_name, tools=tools_hint)}
        if tool_name not in self.tools:
            return {"success": False, "error": t(K.TOOL_UNKNOWN, self.lang).format(tool=tool_name)}
        if self.active_tools is not None and tool_name not in self.active_tools:
                return {"success": False, "error": t(K.TOOL_BLOCKED, self.lang).format(tool=tool_name, tools=', '.join(self.active_tools))}
        try:
            fn = self.tools[tool_name].function
            sig = inspect.signature(fn)
            if not isinstance(args, dict):
                args = {}
            valid_args = {k: v for k, v in args.items() if k in sig.parameters}
            extra = [k for k in args if k not in sig.parameters]
            if extra:
                log.warning("Tool '%s' received unknown args (ignored): %s", tool_name, extra)
            missing = [name for name, p in sig.parameters.items() if p.default is p.empty and name not in valid_args]
            if missing:
                return {"success": False, "error": f"Manglende argumenter: {', '.join(missing)}. Kræves: {', '.join(self.tools[tool_name].parameters)}"}
            result = fn(**valid_args)
            if isinstance(result, dict) and "success" in result:
                return {"success": result["success"], "result": result}
            return {"success": True, "result": result}
        except Exception as e:
            log.error("Tool '%s' failed: %s", tool_name, traceback.format_exc())
            return {"success": False, "error": f"Værktøjet '{tool_name}' fejlede: {str(e)}"}

    def _parse_json_robust(self, raw, default_error_message=None):
        try:
            data = json.loads(raw)
        except Exception as e:
            error = e
            try:
                data, idx = json.JSONDecoder().raw_decode(raw)
                if idx < len(raw) and raw[idx:].strip() and not all(c in ']})>' for c in raw[idx:].strip()):
                    raise ValueError("Trailing content after JSON")
            except (json.JSONDecodeError, ValueError) as err:
                error = err
            escaped = raw.replace('\r\n', '\\n').replace('\r', '\\n').replace('\n', '\\n')
            try:
                data = json.loads(escaped)
            except Exception as e2:
                error = e2
                try:
                    data, idx = json.JSONDecoder().raw_decode(escaped)
                    if idx < len(escaped) and escaped[idx:].strip() and not all(c in ']})>' for c in escaped[idx:].strip()):
                        raise ValueError("Trailing content after JSON")
                except (json.JSONDecodeError, ValueError) as err:
                    error = err
            return {"error": default_error_message} if default_error_message is not None else {"error": str(error)}


def _parse_json_robust(raw: str) -> tuple[dict | None, str | None]:
    """Parse JSON robustly and return data plus error message."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        try:
            decoder = json.JSONDecoder()
            data, idx = decoder.raw_decode(raw)
            if idx < len(raw) and raw[idx:].strip() and not all(c in ']})>' for c in raw[idx:].strip()):
                raise ValueError("Trailing content after JSON")
        except (json.JSONDecodeError, ValueError):
            escaped = raw.replace('\r\n', '\\n').replace('\r', '\\n').replace('\n', '\\n')
            try:
                data = json.loads(escaped)
            except json.JSONDecodeError:
                try:
                    data, idx = decoder.raw_decode(escaped)
                    if idx < len(escaped) and escaped[idx:].strip() and not all(c in ']})>' for c in escaped[idx:].strip()):
                        raise ValueError("Trailing content after JSON")
                except (json.JSONDecodeError, ValueError):
                    return None, t(K.TOOL_INVALID_JSON)

## Oversættelser fundet i koden:
# TOOL_INVALID_JSON = "Ugyldigt JSON i tool-kald"

## Oversættelser fundet i koden:
# TOOL_INVALID_JSON = "Ugyldigt JSON i tool-kald"

