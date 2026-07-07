"""LM Studio API wrapper for LLM interaction."""

import requests
import json
import hashlib
import base64
import os
import time
import threading
import config
from config import get_logger
from typing import Any, Generator
log = get_logger(__name__)


class LMStudioWrapper:
    """lmstudio wrapper."""
    def __init__(self, base_url: str | None = None, timeout: int = 120, model: str | None = None, api_key: str | None = None, on_request: Any = None) -> None:
        """Initialize the instance.
        
        Args:
            base_url:
            timeout:
            model:
            api_key:
            on_request:"""
        self.base_url = self._resolve_base_url(base_url)
        self.api_key = api_key or os.environ.get('OPENCODE_API_KEY', '')
        self.cache = {}
        self._cache_access = {}
        self._cache_lock = threading.Lock()
        self._model_lock = threading.Lock()
        self._cache_max = 50
        self.timeout = timeout
        self.model = model or os.environ.get('LM_MODEL') or config.LLM_MODEL
        self._pending_tool_calls = []
        self._pending_reasoning = None
        self._stream_timeout = config.LLM_STREAM_TIMEOUT
        self._stream_timeout_max = 3600
        self.on_request = on_request

    def _headers(self) -> dict[str, str]:
        """headers."""
        h = {}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    @staticmethod
    def _resolve_base_url(base_url: str | None) -> str:
        """resolve base url.
        
        Args:
            base_url:"""
        if base_url:
            return base_url.rstrip('/')
        if os.environ.get('LM_BASE_URL'):
            return os.environ['LM_BASE_URL'].rstrip('/')
        host = os.environ.get('LM_HOST', 'localhost')
        port = os.environ.get('LM_PORT', '1234')
        return f'http://{host}:{port}/v1'

    def list_models(self) -> list[str]:
        """list models."""
        try:
            r = requests.get(f"{self.base_url}/models", headers=self._headers(), timeout=5)
            if r.status_code == 200:
                return [m["id"] for m in r.json().get("data", [])]
        except Exception as e:
            log.warning("list_models error: %s", e)
        return [self.model]

    def set_model(self, model: str) -> None:
        """set model.
        
        Args:
            model:"""
        if not model or model in ("test-model", "test", "mock-model", "fake-model"):
            return
        with self._model_lock:
            self.model = model

    def _get_cache_key(self, messages: list[dict]) -> str:
        """get cache key.
        
        Args:
            messages:"""
        return hashlib.md5(json.dumps(messages, sort_keys=True).encode()).hexdigest()

    @staticmethod
    def encode_image(path: str) -> str:
        """encode image.
        
        Args:
            path:"""
        size = os.path.getsize(path)
        if size > config.MAX_IMAGE_SIZE:
            raise ValueError(f"Image too large: {size} bytes (max {config.MAX_IMAGE_SIZE})")
        with open(path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")

    VISION_KEYWORDS = ["vision", "vl", "gemma", "qwen", "llava", "gpt", "claude", "gemini"]
    # Models that DON'T support OpenAI native function calling well.
    # The system falls back to text-mode tools (<<<TOOL>>> markers)
    # when the active model matches one of these prefixes.
    NATIVE_TOOLS_BLACKLIST: list[str] = [
        # Models known NOT to support OpenAI native function calling.
        # Add model name prefixes here to force text-mode tools.
        # Test with: python scripts/benchmark_tools.py --model <name> --native
    ]

    IMAGE_FORMATS = {
        "qwen": "data_url",
        "gpt": "data_url",
        "llava": "data_url",
    }

    @classmethod
    def _supports_vision(cls, model: str | None) -> bool:
        """supports vision.
        
        Args:
            model:"""
        if not model:
            return False
        return any(kw in model.lower() for kw in cls.VISION_KEYWORDS)

    @classmethod
    def _supports_native_tools(cls, model: str | None) -> bool:
        """Check if a model supports OpenAI native function calling.
        
        Returns False when the model name matches an entry in
        NATIVE_TOOLS_BLACKLIST, causing the system to fall back
        to text-mode tool markers (<<<TOOL>>>).
        """
        if not model:
            return True
        model_lower = model.lower()
        return not any(banned in model_lower for banned in cls.NATIVE_TOOLS_BLACKLIST)

    @classmethod
    def _image_url(cls, img: str | dict[str, str], model: str | None = None) -> str:
        """image url.
        
        Args:
            img:
            model:"""
        b64 = img.get("b64", img) if isinstance(img, dict) else img
        mime = img.get("mime", "png") if isinstance(img, dict) else "png"
        if mime == "webp":
            mime = "png"
        return f"data:image/{mime};base64,{b64}"

    @classmethod
    def _image_part(cls, img: str | dict[str, str], model: str) -> dict:
        """image part.
        
        Args:
            img:
            model:"""
        url = cls._image_url(img, model)
        return {"type": "image_url", "image_url": {"url": url}}

    def _to_messages(self, prompt: str | None = None, messages: list[dict] | None = None, images: list | None = None) -> list[dict]:
        """to messages.
        
        Args:
            prompt:
            messages:
            images:"""
        model = self.model
        if images and not self._supports_vision(model):
            images = None
        if messages is not None:
            if images:
                user_indices = [i for i, m in enumerate(messages) if m["role"] == "user"]
                target_idx = user_indices[-1] if user_indices else None
                if target_idx is not None:
                    content = messages[target_idx]["content"]
                    if isinstance(content, str):
                        content = []
                    existing_urls = {p.get("image_url", {}).get("url", p.get("url", "")) for p in content if isinstance(p, dict) and p.get("type") in ("image_url", "image")}
                    new_images = []
                    for img in images:
                        part = self._image_part(img, model)
                        url = part.get("image_url", {}).get("url", part.get("url", ""))
                        if url not in existing_urls:
                            new_images.append(part)
                    if isinstance(messages[target_idx]["content"], str):
                        content = new_images + [{"type": "text", "text": messages[target_idx]["content"]}]
                    else:
                        content = new_images + content
                    messages[target_idx] = {**messages[target_idx], "content": content}
            return messages
        if not prompt and not images:
            return []
        if images:
            content = []
            for img in images:
                content.append(self._image_part(img, model))
            content.append({"type": "text", "text": prompt or ""})
            return [{"role": "user", "content": content}]
        return [{"role": "user", "content": prompt}] if prompt else []

    def _compress_messages(self, messages: list[dict]) -> list[dict]:
        """compress messages.

        Args:
            messages:"""
        compressed = []
        for m in messages:
            content = m["content"]
            if isinstance(content, str):
                compressed.append({**m, "content": " ".join(content.split())})
            elif isinstance(content, list):
                compressed_content = []
                for part in content:
                    if part.get("type") == "text":
                        compressed_content.append({**part, "text": " ".join(part["text"].split())})
                    else:
                        compressed_content.append(part)
                compressed.append({**m, "content": compressed_content})
            else:
                compressed.append(m)
        return compressed

    def _merge_system_messages(self, messages: list[dict]) -> list[dict]:
        """Merge consecutive system messages into a single one at position 0.

        Some Jinja templates (qwen, nemotron) only accept ONE system message.
        Multiple system messages cause 'System message must be at the beginning'
        errors even when they ARE at position 0.
        """
        sys_msgs = [m for m in messages if m.get("role") == "system"]
        other_msgs = [m for m in messages if m.get("role") != "system"]
        if len(sys_msgs) <= 1:
            return messages
        merged_parts = []
        for m in sys_msgs:
            c = m.get("content", "")
            if isinstance(c, str):
                merged_parts.append(c)
            elif isinstance(c, list):
                for part in c:
                    if part.get("type") == "text":
                        merged_parts.append(part.get("text", ""))
            else:
                merged_parts.append(str(c))
        merged = "\n\n".join(p for p in merged_parts if p.strip())
        log.info("Merged %d system messages into one (%d chars)", len(sys_msgs), len(merged))
        return [{"role": "system", "content": merged}] + other_msgs

    def _ensure_system_first(self, messages: list[dict]) -> list[dict]:
        """Ensure ALL system messages are at position 0. Unconditional — all models."""
        if len(messages) <= 1:
            return messages
        sys_msgs = [m for m in messages if m.get("role") == "system"]
        other_msgs = [m for m in messages if m.get("role") != "system"]
        if not sys_msgs:
            return messages
        # Check if already correct: first N messages are all system
        already_correct = all(m.get("role") == "system" for m in messages[:len(sys_msgs)])
        if already_correct:
            return messages
        log.info("Moving %d system message(s) to beginning", len(sys_msgs))
        return sys_msgs + other_msgs

    def _prepare_body(
        self,
        msgs: list[dict],
        temperature: float,
        max_tokens: int,
        stream: bool,
        tools: list | None = None,
    ) -> dict | None:
        """Single chokepoint for ALL request construction.

        Handles: compression, system message merging, system-first reorder,
        truncation, validation, body construction.

        Args:
            msgs: Pre-built message list from _to_messages().
            temperature:
            max_tokens:
            stream:
            tools:
        """
        compressed = self._compress_messages(msgs)
        compressed = self._ensure_system_first(compressed)

        # Merge system messages for models whose Jinja templates require
        # exactly one system message at the beginning (qwen, nemotron, glm).
        # After _ensure_system_first, all system messages are at position 0,
        # but qwen rejects having multiple system messages even in sequence.
        if any(kw in self.model.lower() for kw in ("qwen", "nemotron", "glm")):
            compressed = self._merge_system_messages(compressed)

        if len(compressed) > 3:
            compressed = self._truncate_messages(compressed)

        # Validate: system message must be at position 0
        if compressed and compressed[0].get("role") != "system":
            log.error("CRITICAL: First message role=%s, expected 'system'. "
                      "Messages: %s", compressed[0].get("role"),
                      [m.get("role") for m in compressed[:5]])

        # Log message order for debugging
        log.info("Request to %s: %d messages, stream=%s, tools=%s",
                 self.model, len(compressed), stream,
                 len(tools) if tools else 0)
        for mi, mm in enumerate(compressed):
            role = mm.get("role", "?")
            c = mm.get("content", "")
            clen = len(c) if isinstance(c, str) else len(str(c))
            log.info("  msg[%d] role=%s len=%d", mi, role, clen)

        body: dict[str, Any] = {
            "model": self.model,
            "messages": compressed,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if stream:
            body["stream"] = True
        if tools:
            body["tools"] = tools
        if self.on_request:
            self.on_request(body)
        return body

    def generate(self, prompt: str | None = None, messages: list[dict] | None = None, temperature: float = 0.7, max_tokens: int | None = None, use_cache: bool = True, images: list | None = None) -> str:
        """generate.

        Args:
            prompt:
            messages:
            temperature:
            max_tokens:
            use_cache:
            images:"""
        if max_tokens is None:
            max_tokens = config.MAX_TOKENS
        msgs = self._to_messages(prompt, messages, images)
        cache_key = self._get_cache_key(msgs)
        if use_cache:
            with self._cache_lock:
                if cache_key in self.cache:
                    log.info("Cache hit")
                    self._cache_access[cache_key] = time.time()
                    return self.cache[cache_key]

        body = self._prepare_body(msgs, temperature, max_tokens, stream=False)
        log.info("Sending to LLM (chat, timeout: %ss)", self.timeout)
        try:
            response = requests.post(
                f"{self.base_url}/chat/completions",
                json=body,
                headers=self._headers(),
                timeout=(30, self.timeout)
            )
            if response.status_code == 200:
                msg = response.json()["choices"][0]["message"]
                result = (msg.get("content") or msg.get("reasoning_content") or "").strip()
                with self._cache_lock:
                    if len(self.cache) >= self._cache_max:
                        oldest = min(self._cache_access, key=self._cache_access.get)
                        del self.cache[oldest]
                        del self._cache_access[oldest]
                    self.cache[cache_key] = result
                    self._cache_access[cache_key] = time.time()
                log.info("LLM response received (%s chars)", len(result))
                return result
            else:
                err_body = response.text[:500]
                log.error("LLM API error (Status %s): %s", response.status_code, err_body)
                # Provide more helpful error messages with recovery guidance
                if response.status_code == 429:
                    return f"ERROR: Rate limit exceeded. Vent venligst og prøv igen senere. Hvis problemet fortsætter, så reducer kompleksiteten af din forespørgsel."
                elif response.status_code == 500:
                    return f"ERROR: Internal server error i LLM. Prøv igen med en enklere forespørgsel eller kontroller at LM Studio kører korrekt."
                elif response.status_code == 503:
                    return f"ERROR: Tjenesten er utilgængelig. Kontroller at LM Studio er startet og tilgængelig på {self.base_url}"
                else:
                    return f"ERROR:HTTP {response.status_code}: {err_body}. Prøv at forenkle din forespørgsel eller skift til en anden model."
        except requests.exceptions.Timeout:
            log.error("Timeout after %ss", self.timeout)
            return f"ERROR: Timeout efter {self.timeout}s. Forsøg at forenkle din forespørgsel, reducere mængden af kontekst, eller øge timeout-værdien. Overvej at bruge en mindre kompleks model til denne type opgave."
        except Exception as e:
            log.error("Error: %s", e)
            # Provide more helpful error messages with recovery guidance
            error_str = str(e).lower()
            if "connection" in error_str:
                return f"ERROR: Forbindelsesfejl: {str(e)}. Kontroller at LM Studio kører og er tilgængelig på {self.base_url}. Sørg for at serveren er startet korrekt."
            elif "memory" in error_str:
                return f"ERROR: Minsk hukommelse: {str(e)}. Luk andre programmer for at frigøre hukommelse, eller reducér mængden af data der sendes til modellen."
            else:
                return f"ERROR: {str(e)}. Prøv at forenkle din forespørgsel eller genstart tjenesten hvis problemet fortsætter."

    def _truncate_messages(self, messages: list[dict]) -> list[dict]:
        """truncate messages.
        
        Args:
            messages:"""
        total = sum(len(m.get("content", "")) if isinstance(m.get("content"), str) else 0 for m in messages)
        if total <= config.MAX_MESSAGE_CHARS:
            return messages
        system_msgs = [m for m in messages if m["role"] == "system"]
        other_msgs = [m for m in messages if m["role"] != "system"]
        mid = "\n[... tidligere kontekst afkortet ...]"
        if len(other_msgs) > 2:
            # Keep first non-system message + truncation note.
            # Then find the LAST complete assistant+tool pair to avoid LM Studio
            # "tool role without tool_calls" errors. Walk backwards from the end
            # and locate the last assistant message that either has tool_calls or
            # is a plain text response.
            last_pair_start = -1
            for i in range(len(other_msgs) - 1, -1, -1):
                role = other_msgs[i].get("role")
                if role == "assistant":
                    last_pair_start = i
                    break
                if role in ("user", "system"):
                    last_pair_start = i
                    break
            if last_pair_start >= 0 and last_pair_start < len(other_msgs) - 1:
                # Keep from last_pair_start to end (entire last pair)
                other_msgs = [other_msgs[0], {"role": "user", "content": mid}] + other_msgs[last_pair_start:]
            elif last_pair_start >= 0:
                # Last message is assistant or user, keep it
                other_msgs = [other_msgs[0], {"role": "user", "content": mid}, other_msgs[last_pair_start]]
            else:
                # Only tool messages remain — drop them all
                other_msgs = [other_msgs[0], {"role": "user", "content": mid}]
        return system_msgs + other_msgs

    def generate_stream(self, prompt: str | None = None, messages: list[dict] | None = None, temperature: float = 0.7, max_tokens: int | None = None, images: list | None = None, tools: list | None = None) -> Generator[str, None, None]:
        """generate stream.

        Args:
            prompt:
            messages:
            temperature:
            max_tokens:
            images:
            tools:

        Yields:
            ..."""
        if max_tokens is None:
            max_tokens = config.MAX_TOKENS
        msgs = self._to_messages(prompt, messages, images)
        body = self._prepare_body(msgs, temperature, max_tokens, stream=True, tools=tools)
        self._pending_tool_calls.clear()
        self._pending_reasoning = None
        if images:
            log.info("Sending %s images with model %s", len(images), self.model)
            for i, m in enumerate(body["messages"]):
                c = m.get("content","")
                if isinstance(c, list):
                    for j, part in enumerate(c):
                        t = part.get("type","?")
                        if t in ("image_url","image"):
                            url = part.get("image_url",{}).get("url", part.get("url",""))
                            log.debug("  msg[%s] content[%s] type=%s url_len=%s url_start=%s...", i, j, t, len(url), url[:50])
                elif isinstance(c, str):
                    log.info("  msg[%s] type=text len=%s", i, len(c))
        stream_timeout = self._stream_timeout
        try:
            workdir = os.environ.get('AGENT_WORKDIR') or os.getcwd()
            req_dir = os.path.join(workdir, "logs", "llm_requests")
            os.makedirs(req_dir, exist_ok=True)
            ts = int(time.time() * 1000)
            sess = os.environ.get('AGENT_SESSION_ID', 'unknown')
            req_path = os.path.join(req_dir, f"{sess}_{ts}_request.json")
            resp_path = os.path.join(req_dir, f"{sess}_{ts}_response.json")
            try:
                with open(req_path, 'w', encoding='utf-8') as rf:
                    json.dump(body, rf, ensure_ascii=False, indent=2, default=str)
            except Exception as dump_err:
                log.debug("Could not save LLM request body to %s: %s", req_path, dump_err)
            response = requests.post(
                f"{self.base_url}/chat/completions",
                json=body,
                headers=self._headers(),
                timeout=(config.LLM_CONNECT_TIMEOUT, stream_timeout),
                stream=True
            )
            if response.status_code != 200:
                try:
                    err_body = response.text[:500]
                    log.error("HTTP %s: %s", response.status_code, err_body)
                except Exception as e:
                    log.warning("Failed to read error body: %s", e)
                try:
                    with open(resp_path, 'w', encoding='utf-8') as ef:
                        json.dump({"http_status": response.status_code, "error": response.text[:2000]}, ef, ensure_ascii=False, indent=2)
                except Exception:
                    pass
                yield f"[ERROR: HTTP {response.status_code}]"
                return
            tool_calls_acc = {}
            accumulated_text = ""
            accumulated_reasoning = ""
            raw_chunks = []
            for line in response.iter_lines():
                if line:
                    try:
                        line_str = line.decode("utf-8")
                        raw_chunks.append(line_str)
                        if line_str.startswith("data: "):
                            data = line_str[6:]
                            if data != "[DONE]":
                                chunk = json.loads(data)
                                if "choices" in chunk and chunk["choices"]:
                                    delta = chunk["choices"][0].get("delta", {})
                                    text = delta.get("content") or ""
                                    reasoning = delta.get("reasoning_content") or ""
                                    if reasoning:
                                        self._pending_reasoning = (self._pending_reasoning or "") + reasoning
                                        accumulated_reasoning += reasoning
                                    if text or reasoning:
                                        if text:
                                            accumulated_text += text
                                        yield text or reasoning
                                    tool_calls_list = delta.get("tool_calls")
                                    if tool_calls_list:
                                        for tc in tool_calls_list:
                                            idx = tc["index"]
                                            if idx not in tool_calls_acc:
                                                tool_calls_acc[idx] = {
                                                    "id": tc.get("id"),
                                                    "function": {
                                                        "name": tc.get("function", {}).get("name", ""),
                                                        "arguments": ""
                                                    }
                                                }
                                            args_chunk = tc.get("function", {}).get("arguments", "")
                                            if args_chunk:
                                                tool_calls_acc[idx]["function"]["arguments"] += args_chunk
                                    finish_reason = chunk["choices"][0].get("finish_reason")
                                    if finish_reason == "tool_calls" and tool_calls_acc:
                                        for idx in sorted(tool_calls_acc):
                                            tc_data = tool_calls_acc[idx]
                                            try:
                                                parsed_args = json.loads(tc_data["function"]["arguments"])
                                            except (json.JSONDecodeError, ValueError):
                                                parsed_args = {}
                                            tc_data["function"]["arguments"] = parsed_args
                                            self._pending_tool_calls.append({
                                                "id": tc_data.get("id", f"call_{idx}"),
                                                "type": "function",
                                                "function": {
                                                    "name": tc_data["function"]["name"],
                                                    "arguments": json.dumps(parsed_args)
                                                }
                                            })
                                        yield f"\n[Kalde: {self._pending_tool_calls[0]['function']['name']}]"
                    except (json.JSONDecodeError, UnicodeDecodeError) as e:
                        log.error("Parse error in stream: %s", e)
                        continue
            try:
                resp_dump = {
                    "accumulated_text": accumulated_text,
                    "accumulated_reasoning": accumulated_reasoning,
                    "tool_calls": [
                        {
                            "id": tc.get("id"),
                            "function": {
                                "name": tc.get("function", {}).get("name", ""),
                                "arguments": tc.get("function", {}).get("arguments", "")
                            }
                        }
                        for tc in self._pending_tool_calls
                    ],
                    "raw_chunks_count": len(raw_chunks),
                    "raw_chunks": raw_chunks
                }
                with open(resp_path, 'w', encoding='utf-8') as respf:
                    json.dump(resp_dump, respf, ensure_ascii=False, indent=2, default=str)
            except Exception as resp_dump_err:
                log.debug("Could not save LLM response to %s: %s", resp_path, resp_dump_err)
        except requests.exceptions.Timeout:
            self._stream_timeout = min(self._stream_timeout * 2, self._stream_timeout_max)
            log.warning("Stream timeout after %ss — next timeout will be %ss", stream_timeout, self._stream_timeout)
            try:
                with open(resp_path, 'w', encoding='utf-8') as tf:
                    json.dump({"error": f"Timeout after {stream_timeout}s"}, tf, ensure_ascii=False, indent=2)
            except Exception:
                pass
            yield f"\n[ERROR: Timeout efter {stream_timeout}s — ingen data i 30s. Forsøg at forenkle din forespørgsel eller brug en mindre kompleks model.]"
        except requests.exceptions.ConnectionError:
            try:
                with open(resp_path, 'w', encoding='utf-8') as cf:
                    json.dump({"error": f"Cannot connect to LM Studio at {self.base_url}"}, cf, ensure_ascii=False, indent=2)
            except Exception:
                pass
            yield f"\n[ERROR: Kan ikke oprette forbindelse til LM Studio på {self.base_url}. Kontroller at LM Studio er startet og at adressen er korrekt.]"
        except Exception as e:
            try:
                with open(resp_path, 'w', encoding='utf-8') as exf:
                    json.dump({"error": str(e)}, exf, ensure_ascii=False, indent=2)
            except Exception:
                pass
            # Provide more helpful error messages with recovery guidance
            error_str = str(e).lower()
            if "connection" in error_str:
                yield f"\n[ERROR: Forbindelsesfejl: {str(e)}. Kontroller at LM Studio kører og er tilgængelig på {self.base_url}.]"
            elif "timeout" in error_str:
                yield f"\n[ERROR: Timeout: {str(e)}. Forsøg at forenkle din forespørgsel eller brug en mindre kompleks model.]"
            else:
                yield f"\n[ERROR: {str(e)}. Prøv at forenkle din forespørgsel eller genstart tjenesten hvis problemet fortsætter.]"
        else:
            if self._stream_timeout != config.LLM_STREAM_TIMEOUT:
                self._stream_timeout = config.LLM_STREAM_TIMEOUT
                log.info("Stream completed successfully — timeout reset to %ss", self._stream_timeout)
