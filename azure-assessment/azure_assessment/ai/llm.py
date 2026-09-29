"""Minimal client for a self-hosted LLM behind an OpenAI-compatible chat API.

Ollama, llama.cpp (llama-server), vLLM, LM Studio and LocalAI all expose
``POST {base_url}/chat/completions``, so one client covers them. It uses only the standard
library: no SDK, and nothing is sent anywhere except the URL you configure.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from typing import Protocol

log = logging.getLogger(__name__)

DEFAULT_URL = "http://localhost:11434/v1"      # Ollama
DEFAULT_MODEL = "qwen2.5:7b-instruct"          # Apache-2.0; see docs/AI-NARRATIVE.md for alternatives


class LLMError(RuntimeError):
    pass


class ChatClient(Protocol):
    model: str

    def chat(self, system: str, user: str) -> str: ...


class OpenAICompatibleClient:
    def __init__(self, base_url: str | None = None, model: str | None = None, api_key: str | None = None,
                 timeout: float = 180, temperature: float = 0.3, max_tokens: int = 900):
        self.base_url = (base_url or os.environ.get("AZURE_ASSESS_LLM_URL") or DEFAULT_URL).rstrip("/")
        self.model = model or os.environ.get("AZURE_ASSESS_LLM_MODEL") or DEFAULT_MODEL
        self.api_key = api_key or os.environ.get("AZURE_ASSESS_LLM_API_KEY") or ""
        self.timeout = timeout
        self.temperature = temperature
        self.max_tokens = max_tokens

    def _request(self, method: str, path: str, body: dict | None = None, timeout: float | None = None) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(self.base_url + path, method=method, headers=headers,
                                     data=json.dumps(body).encode() if body is not None else None)
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            raise LLMError(f"{self.base_url}{path} returned HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise LLMError(f"cannot reach the LLM server at {self.base_url}: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise LLMError(f"{self.base_url}{path} did not return JSON") from exc

    def models(self) -> list[str]:
        data = self._request("GET", "/models", timeout=10)
        return [m.get("id", "") for m in data.get("data", [])]

    def check(self) -> None:
        """Fail fast, with a useful message, when the server or model is not available."""
        available = self.models()
        if available and self.model not in available:
            raise LLMError(f"model '{self.model}' is not loaded on {self.base_url}; available: "
                           + ", ".join(available) + f". For Ollama run: ollama pull {self.model}")

    def chat(self, system: str, user: str) -> str:
        data = self._request("POST", "/chat/completions", {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "stream": False,
        })
        try:
            return data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"unexpected response from {self.base_url}: {str(data)[:300]}") from exc
