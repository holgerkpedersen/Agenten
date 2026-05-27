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
    def __init__(self, base_url=None, timeout=120, model=None, api_key=None):
        self.base_url = self._resolve_base_url(base_url)
        self.api_key = api_key or os.environ.get('OPENCODE_API_KEY', '')
        self.cache = {}
        self._cache_access = {}
        self._cache_lock = threading.Lock()
        self._model_lock = threading.Lock()
        self._cache_max = 50
        self.timeout = timeout
        self.model = model or os.environ.get('LM_MODEL') or config.LLM_MODEL

    def _headers(self):
        h = {}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    @staticmethod
    def _resolve_base_url(base_url):
        if os.environ.get('OPENCODE_BASE_URL'):
            return os.environ['OPENCODE_BASE_URL'].rstrip('/')
        if base_url:
            return base_url.rstrip('/')
        if os.environ.get('LM_BASE_URL'):
            return os.environ['LM_BASE_URL'].rstrip('/')
        host = os.environ.get('LM_HOST', 'localhost')
        port = os.environ.get('LM_PORT', '1234')
        return f'http://{host}:{port}/v1'

    def list_models(self):
        try:
            r = requests.get(f"{self.base_url}/models", headers=self._headers(), timeout=5)
            if r.status_code == 200:
                return [m["id"] for m in r.json().get("data", [])]
        except Exception as e:
            log.warning("list_models error: %s", e)
        return [self.model]

    def set_model(self, model):
        with self._model_lock:
            self.model = model

    def _get_cache_key(self, messages):
        return hashlib.md5(json.dumps(messages, sort_keys=True).encode()).hexdigest()

    @staticmethod
    def encode_image(path):
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
        if not model:
            return False
        return any(kw in model.lower() for kw in cls.VISION_KEYWORDS)

    @classmethod
    def _image_url(cls, img, model=None):
        b64 = img.get("b64", img) if isinstance(img, dict) else img
        mime = img.get("mime", "png") if isinstance(img, dict) else "png"
        if mime == "webp":
            mime = "png"
        return f"data:image/{mime};base64,{b64}"

    @classmethod
    def _image_part(cls, img, model):
        url = cls._image_url(img, model)
        return {"type": "image_url", "image_url": {"url": url}}

    def _to_messages(self, prompt=None, messages=None, images=None):
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

        try:
            response = requests.post(
                f"{self.base_url}/chat/completions",
                json={
                    "model": self.model,
                    "messages": compressed,
                    "temperature": temperature,
                    "max_tokens": max_tokens
                },
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
                return f"ERROR:HTTP {response.status_code}"
        except requests.exceptions.Timeout:
            log.error("Timeout after %ss", self.timeout)
            return f"ERROR:Timeout after {self.timeout}s"
        except Exception as e:
            log.error("Error: %s", e)
            return f"ERROR:{str(e)}"

    def generate_stream(self, prompt=None, messages=None, temperature=0.7, max_tokens=None, images=None):
        if max_tokens is None:
            max_tokens = config.MAX_TOKENS
        msgs = self._to_messages(prompt, messages, images)
        compressed = self._compress_messages(msgs)
        # Log image info for debugging
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
        stream_timeout = config.LLM_STREAM_TIMEOUT
        try:
            response = requests.post(
                f"{self.base_url}/chat/completions",
                json={
                    "model": self.model,
                    "messages": compressed,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                    "stream": True
                },
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
                                    text = delta.get("content") or delta.get("reasoning_content") or ""
                                    if text:
                                        yield text
                    except (json.JSONDecodeError, UnicodeDecodeError) as e:
                        log.error("Parse error in stream: %s", e)
                        continue
        except requests.exceptions.Timeout:
            yield f"\n[ERROR: Timeout after {stream_timeout}s — ingen data i 30s]"
        except requests.exceptions.ConnectionError:
            yield f"\n[ERROR: Cannot connect to LM Studio at {self.base_url}]"
        except Exception as e:
            yield f"\n[ERROR: {str(e)}]"
