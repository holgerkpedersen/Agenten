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
log = get_logger(__name__)


class LMStudioWrapper:
    """lmstudio wrapper."""
    def __init__(self, base_url=None, timeout=120, model=None, api_key=None, on_request=None):
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
        self.on_request = on_request

    def _headers(self):
        """headers."""
        h = {}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    @staticmethod
    def _resolve_base_url(base_url):
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

    def list_models(self):
        """list models."""
        try:
            r = requests.get(f"{self.base_url}/models", headers=self._headers(), timeout=5)
            if r.status_code == 200:
                return [m["id"] for m in r.json().get("data", [])]
        except Exception as e:
            log.warning("list_models error: %s", e)
        return [self.model]

    def set_model(self, model):
        """set model.
        
        Args:
            model:"""
        with self._model_lock:
            self.model = model

    def _get_cache_key(self, messages):
        """get cache key.
        
        Args:
            messages:"""
        return hashlib.md5(json.dumps(messages, sort_keys=True).encode()).hexdigest()

    @staticmethod
    def encode_image(path):
        """encode image.
        
        Args:
            path:"""
        size = os.path.getsize(path)
        if size > config.MAX_IMAGE_SIZE:
            raise ValueError(f"Image too large: {size} bytes (max {config.MAX_IMAGE_SIZE})")
        with open(path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")

    VISION_KEYWORDS = ["vision", "vl", "gemma", "qwen", "llava", "gpt", "claude", "gemini"]

    IMAGE_FORMATS = {
        "qwen": "data_url",
        "gpt": "data_url",
        "llava": "data_url",
    }

    @classmethod
    def _supports_vision(cls, model):
        """supports vision.
        
        Args:
            model:"""
        if not model:
            return False
        return any(kw in model.lower() for kw in cls.VISION_KEYWORDS)

    @classmethod
    def _image_url(cls, img, model=None):
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
    def _image_part(cls, img, model):
        """image part.
        
        Args:
            img:
            model:"""
        url = cls._image_url(img, model)
        return {"type": "image_url", "image_url": {"url": url}}

    def _to_messages(self, prompt=None, messages=None, images=None):
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
                        content = new_images + [{"type": "text", "text": messages[first_user]["content"]}]
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

    def _compress_messages(self, messages):
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

    def generate(self, prompt=None, messages=None, temperature=0.7, max_tokens=None, use_cache=True, images=None):
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

        compressed = self._compress_messages(msgs)
        log.info(f"Sending to LLM (chat, timeout: {self.timeout}s)")
        if len(compressed) > 3:
            compressed = self._truncate_messages(compressed)

        body = {
            "model": self.model,
            "messages": compressed,
            "temperature": temperature,
            "max_tokens": max_tokens
        }
        if self.on_request:
            self.on_request(body)
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
                return f"ERROR:HTTP {response.status_code}: {err_body}"
        except requests.exceptions.Timeout:
            log.error("Timeout after %ss", self.timeout)
            return f"ERROR:Timeout after {self.timeout}s"
        except Exception as e:
            log.error("Error: %s", e)
            return f"ERROR:{str(e)}"

    def _truncate_messages(self, messages):
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
            other_msgs = [other_msgs[0], {"role": "user", "content": mid}] + other_msgs[-1:]
        return system_msgs + other_msgs

    def generate_stream(self, prompt=None, messages=None, temperature=0.7, max_tokens=None, images=None, tools=None):
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
        compressed = self._compress_messages(msgs)
        self._pending_tool_calls.clear()
        self._pending_reasoning = None
        if images:
            log.info("Sending %s images with model %s", len(images), self.model)
            for i, m in enumerate(compressed):
                c = m.get("content","")
                if isinstance(c, list):
                    for j, part in enumerate(c):
                        t = part.get("type","?")
                        if t in ("image_url","image"):
                            url = part.get("image_url",{}).get("url", part.get("url",""))
                            log.debug("  msg[%s] content[%s] type=%s url_len=%s url_start=%s...", i, j, t, len(url), url[:50])
                elif isinstance(c, str):
                    log.info("  msg[%s] type=text len=%s", i, len(c))
        log.info("Streaming chat request to LLM")
        if len(compressed) > 3:
            compressed = self._truncate_messages(compressed)
        stream_timeout = config.LLM_STREAM_TIMEOUT
        body = {
            "model": self.model,
            "messages": compressed,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True
        }
        if tools:
            body["tools"] = tools
        if self.on_request:
            self.on_request(body)
        try:
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
                yield f"[ERROR: HTTP {response.status_code}]"
                return
            tool_calls_acc = {}
            for line in response.iter_lines():
                if line:
                    try:
                        line_str = line.decode("utf-8")
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
                                    if text or reasoning:
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
        except requests.exceptions.Timeout:
            yield f"\n[ERROR: Timeout after {stream_timeout}s — ingen data i 30s]"
        except requests.exceptions.ConnectionError:
            yield f"\n[ERROR: Cannot connect to LM Studio at {self.base_url}]"
        except Exception as e:
            yield f"\n[ERROR: {str(e)}]"
