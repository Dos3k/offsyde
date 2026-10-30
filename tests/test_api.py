from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from offsyde.api.main import RateLimiter, create_app
from offsyde.config import Settings
from offsyde.nl2sql.llm import LLMError
from offsyde.nl2sql.service import AskService
from test_service import ScriptedLLM, sql


@pytest.fixture
def client_factory(readonly_engine):
    def make(*replies, rate=100):
        service = AskService(readonly_engine, ScriptedLLM(*replies))
        app = create_app(service, Settings(rate_limit_per_min=rate))
        return TestClient(app)

    return make


def test_health_reports_data(client_factory):
    body = client_factory().get("/health").json()
    assert body["status"] == "ok" and body["matches"] == 2


def test_ask_returns_structured_result(client_factory):
    c = client_factory(sql("SELECT COUNT(*) AS n FROM teams"), "There are 3 teams.")
    r = c.post("/api/ask", json={"question": "How many teams?"})
    assert r.status_code == 200
    body = r.json()
    assert body["answer"] == "There are 3 teams." and body["rows"] == [[3]]
    assert body["sql"] and body["attempts"] == 1 and body["answer_source"] == "llm"


@pytest.mark.parametrize("payload", [{}, {"question": "hi"}, {"question": "x" * 501}])
def test_invalid_requests_are_rejected(client_factory, payload):
    assert client_factory().post("/api/ask", json=payload).status_code == 422


def test_llm_outage_is_a_503_with_a_helpful_message(client_factory):
    c = client_factory(LLMError("cannot reach Ollama at http://localhost:11434"))
    r = c.post("/api/ask", json={"question": "How many teams?"})
    assert r.status_code == 503 and "Ollama" in r.json()["detail"]


def test_rate_limit(client_factory):
    c = client_factory(*([sql("SELECT 1 FROM teams"), "x"] * 5), rate=2)
    codes = [c.post("/api/ask", json={"question": "How many teams?"}).status_code for _ in range(4)]
    assert codes[:2] == [200, 200] and 429 in codes[2:]


def test_schema_endpoint_documents_views(client_factory):
    body = client_factory().get("/api/schema").json()
    assert {"events", "shots", "passes", "team_matches"} <= set(body)
    assert body["shots"]["columns"][0]["name"] == "id"


def test_index_page_is_served(client_factory):
    r = client_factory().get("/")
    assert r.status_code == 200 and "Offsyde" in r.text


def test_rate_limiter_refills_over_time(monkeypatch):
    import offsyde.api.main as m

    now = [1000.0]
    monkeypatch.setattr(m.time, "monotonic", lambda: now[0])
    rl = RateLimiter(60)  # one token per second
    assert all(rl.allow("a") for _ in range(60))
    assert not rl.allow("a")
    assert rl.allow("b")  # separate bucket per client
    now[0] += 2
    assert rl.allow("a")


def test_datasets_endpoint_lists_loaded_seasons(client_factory):
    body = client_factory().get("/api/datasets").json()
    assert body == [{"competition": "1. Bundesliga", "season": "2023/2024", "matches": 2}]
