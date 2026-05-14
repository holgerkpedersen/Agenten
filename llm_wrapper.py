import requests
import json
import hashlib
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

    def _to_messages(self, prompt=None, messages=None):
        if messages is not None:
            return messages
        return [{"role": "user", "content": prompt}] if prompt else []

    def _compress_messages(self, messages):
        return [{**m, "content": " ".join(m["content"].split())} for m in messages]

    def generate(self, prompt=None, messages=None, temperature=0.7, max_tokens=32000, use_cache=True):
        msgs = self._to_messages(prompt, messages)
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

    def generate_stream(self, prompt=None, messages=None, temperature=0.7, max_tokens=32000):
        msgs = self._to_messages(prompt, messages)
        compressed = self._compress_messages(msgs)
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
