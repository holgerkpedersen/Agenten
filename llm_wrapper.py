import requests
import json
import hashlib
import base64
import os
import time


class LMStudioWrapper:
    def __init__(self, base_url="http://localhost:1234/v1", timeout=120, model="google/gemma-4-26b-a4b"):
        self.base_url = base_url
        self.cache = {}
        self.timeout = timeout
        self.model = model

    def list_models(self):
        try:
            r = requests.get(f"{self.base_url}/models", timeout=5)
            if r.status_code == 200:
                return [m["id"] for m in r.json().get("data", [])]
        except Exception:
            pass
        return [self.model]

    def set_model(self, model):
        self.model = model

    def _get_cache_key(self, messages):
        return hashlib.md5(json.dumps(messages, sort_keys=True).encode()).hexdigest()

    @staticmethod
    def encode_image(path):
        with open(path, "rb") as f:
            return base64.urlsafe_b64encode(f.read()).decode("utf-8")

    IMAGE_FORMATS = {
        # All models use data:image/...;base64,... format
        # Gemma confirmed working via direct LM Studio API test 2026-05-19
        "qwen": "data_url",
        "gpt": "data_url",
        "llava": "data_url",
    }

    @classmethod
    def _image_url(cls, img, model=None):
        b64 = img.get("b64", img) if isinstance(img, dict) else img
        mime = img.get("mime", "png") if isinstance(img, dict) else "png"
        if mime == "webp":
            mime = "png"
        return f"data:image/{mime};base64,{b64}"
        if fmt == "data_url":
            return f"data:image/{mime};base64,{b64}"
        return b64

    @classmethod
    def _image_part(cls, img, model):
        url = cls._image_url(img, model)
        return {"type": "image_url", "image_url": {"url": url}}

    def _to_messages(self, prompt=None, messages=None, images=None):
        model = self.model
        if messages is not None:
            if images:
                first_user = next((i for i, m in enumerate(messages) if m["role"] == "user"), None)
                if first_user is not None:
                    content = messages[first_user]["content"]
                    if isinstance(content, str):
                        content = []
                    existing_urls = {p.get("image_url", {}).get("url", p.get("url", "")) for p in content if isinstance(p, dict) and p.get("type") in ("image_url", "image")}
                    new_images = []
                    for img in images:
                        part = self._image_part(img, model)
                        url = part.get("image_url", {}).get("url", part.get("url", ""))
                        if url not in existing_urls:
                            new_images.append(part)
                    if isinstance(messages[first_user]["content"], str):
                        content = new_images + [{"type": "text", "text": messages[first_user]["content"]}]
                    else:
                        content = new_images + content
                    messages[first_user] = {**messages[first_user], "content": content}
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

    def generate(self, prompt=None, messages=None, temperature=0.7, max_tokens=32000, use_cache=True, images=None):
        msgs = self._to_messages(prompt, messages, images)
        cache_key = self._get_cache_key(msgs)
        if use_cache and cache_key in self.cache:
            print("✓ Cache hit")
            return self.cache[cache_key]

        compressed = self._compress_messages(msgs)
        print(f"📤 Sending to LLM (chat, timeout: {self.timeout}s)")

        try:
            response = requests.post(
                f"{self.base_url}/chat/completions",
                json={
                    "model": self.model,
                    "messages": compressed,
                    "temperature": temperature,
                    "max_tokens": max_tokens
                },
                timeout=(30, self.timeout)
            )
            if response.status_code == 200:
                result = response.json()["choices"][0]["message"]["content"].strip()
                self.cache[cache_key] = result
                print(f"✓ LLM response received ({len(result)} chars)")
                return result
            else:
                return f"ERROR:HTTP {response.status_code}"
        except requests.exceptions.Timeout:
            print(f"✗ Timeout after {self.timeout}s")
            return f"ERROR:Timeout after {self.timeout}s"
        except Exception as e:
            print(f"✗ Error: {e}")
            return f"ERROR:{str(e)}"

    def generate_stream(self, prompt=None, messages=None, temperature=0.7, max_tokens=32000, images=None):
        msgs = self._to_messages(prompt, messages, images)
        compressed = self._compress_messages(msgs)
        # Log image info for debugging
        if images:
            print(f"🖼️ Sending {len(images)} images with model {self.model}")
            for i, m in enumerate(compressed):
                c = m.get("content","")
                if isinstance(c, list):
                    for j, part in enumerate(c):
                        t = part.get("type","?")
                        if t in ("image_url","image"):
                            url = part.get("image_url",{}).get("url", part.get("url",""))
                            print(f"  msg[{i}] content[{j}] type={t} url_len={len(url)} url_start={url[:50]}...")
                elif isinstance(c, str):
                    print(f"  msg[{i}] type=text len={len(c)}")
        print("📡 Streaming chat request to LLM")
        stream_timeout = 300
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
                timeout=(30, stream_timeout),
                stream=True
            )
            if response.status_code != 200:
                try:
                    err_body = response.text[:500]
                    print(f"✗ HTTP {response.status_code}: {err_body}")
                except:
                    pass
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
                                    text = delta.get("content", "")
                                    if text:
                                        yield text
                    except (json.JSONDecodeError, UnicodeDecodeError) as e:
                        print(f"Parse error: {e}")
                        continue
        except requests.exceptions.Timeout:
            yield f"\n[ERROR: Timeout after {stream_timeout}s — ingen data i 30s]"
        except requests.exceptions.ConnectionError:
            yield f"\n[ERROR: Cannot connect to LM Studio at {self.base_url}]"
        except Exception as e:
            yield f"\n[ERROR: {str(e)}]"
