"""LLM access. A tiny interface so tests and evals can swap the model out."""

from __future__ import annotations

from typing import Protocol

import httpx


class LLMError(RuntimeError):
    pass


class LLMClient(Protocol):
    def chat(self, system: str, user: str) -> str: ...


class OllamaClient:
    """Talks to a local Ollama server (https://ollama.com) over its REST API."""

    def __init__(
        self,
        model: str,
        host: str = "http://localhost:11434",
        *,
        num_ctx: int = 8192,
        timeout_s: float = 180.0,
        client: httpx.Client | None = None,
    ):
        self.model = model
        self.host = host.rstrip("/")
        self.num_ctx = num_ctx
        self._client = client or httpx.Client(timeout=timeout_s)

    def chat(self, system: str, user: str) -> str:
        payload = {
            "model": self.model,
            "stream": False,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            # temperature 0: SQL generation should be as deterministic as possible.
            "options": {"temperature": 0, "num_ctx": self.num_ctx},
        }
        try:
            resp = self._client.post(f"{self.host}/api/chat", json=payload)
        except httpx.TransportError as exc:
            raise LLMError(
                f"cannot reach Ollama at {self.host} ({exc}). Is it running? Try `ollama serve`."
            ) from exc
        if resp.status_code == 404:
            raise LLMError(
                f"model '{self.model}' not found in Ollama. Run `ollama pull {self.model}`."
            )
        if resp.status_code >= 400:
            raise LLMError(f"Ollama returned {resp.status_code}: {resp.text[:200]}")
        try:
            return resp.json()["message"]["content"]
        except (KeyError, ValueError) as exc:
            raise LLMError(f"unexpected Ollama response: {resp.text[:200]}") from exc
