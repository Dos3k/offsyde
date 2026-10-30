from __future__ import annotations

import json

import httpx
import pytest

from offsyde.nl2sql.llm import LLMError, OllamaClient


def client_with(handler) -> OllamaClient:
    return OllamaClient(
        "qwen2.5-coder:7b",
        "http://ollama.test:11434/",
        num_ctx=4096,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def test_request_shape_and_response_parsing():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"message": {"role": "assistant", "content": "hello"}})

    assert client_with(handler).chat("be brief", "hi") == "hello"
    assert seen["url"] == "http://ollama.test:11434/api/chat"
    body = seen["body"]
    assert body["model"] == "qwen2.5-coder:7b" and body["stream"] is False
    assert body["options"] == {"temperature": 0, "num_ctx": 4096}
    assert [m["role"] for m in body["messages"]] == ["system", "user"]


def test_missing_model_gives_actionable_error():
    with pytest.raises(LLMError, match="ollama pull qwen2.5-coder:7b"):
        client_with(lambda r: httpx.Response(404, json={"error": "model not found"})).chat("s", "u")


def test_unreachable_server_gives_actionable_error():
    def handler(request):
        raise httpx.ConnectError("refused")

    with pytest.raises(LLMError, match="ollama serve"):
        client_with(handler).chat("s", "u")


def test_server_errors_and_garbage_are_reported():
    with pytest.raises(LLMError, match="500"):
        client_with(lambda r: httpx.Response(500, text="oops")).chat("s", "u")
    with pytest.raises(LLMError, match="unexpected"):
        client_with(lambda r: httpx.Response(200, json={"nope": 1})).chat("s", "u")
