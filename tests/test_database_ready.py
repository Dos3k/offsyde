from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from offsyde.api.main import create_app
from offsyde.config import Settings
from offsyde.db import DatabaseNotReady, create_schema, make_engine, verify_database
from offsyde.nl2sql.service import build_service


def test_missing_database_is_reported_and_not_silently_created(tmp_path):
    path = tmp_path / "nope" / "offsyde.db"
    with pytest.raises(DatabaseNotReady, match="Database file not found"):
        make_engine(f"sqlite:///{path}", readonly=True)
    assert not path.exists() and not path.parent.exists()


def test_error_message_says_what_to_do(tmp_path):
    with pytest.raises(DatabaseNotReady) as info:
        build_service(Settings(db_url=f"sqlite:///{tmp_path / 'x.db'}"))
    msg = str(info.value)
    assert "offsyde_database.zip" in msg and "offsyde ingest" in msg and "working directory" in msg


def test_empty_database_is_reported(tmp_path):
    url = f"sqlite:///{tmp_path / 'empty.db'}"
    engine = make_engine(url)  # creates an empty file
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE unrelated (a INT)"))
    with pytest.raises(DatabaseNotReady, match="no match data"):
        verify_database(make_engine(url, readonly=True), url)


def test_database_without_matches_is_reported(tmp_path):
    url = f"sqlite:///{tmp_path / 'schema_only.db'}"
    engine = make_engine(url)
    create_schema(engine)
    with pytest.raises(DatabaseNotReady, match="no matches"):
        verify_database(make_engine(url, readonly=True), url)


def test_loaded_database_passes(loaded_engine):
    verify_database(make_engine(str(loaded_engine.url), readonly=True), str(loaded_engine.url))


def test_api_reports_a_missing_database_as_json(tmp_path):
    app = create_app(None, Settings(db_url=f"sqlite:///{tmp_path / 'missing.db'}"))
    client = TestClient(app)
    health = client.get("/health").json()
    assert health["status"] == "degraded" and "Database file not found" in health["detail"]
    for response in (
        client.get("/api/datasets"),
        client.post("/api/ask", json={"question": "How many goals?"}),
    ):
        assert response.status_code == 503
        assert "Database file not found" in response.json()["detail"]


def test_unexpected_errors_come_back_as_json_not_html(readonly_engine):
    class Broken:
        engine = readonly_engine

        def ask(self, question, **kwargs):
            raise ValueError("something odd")

    client = TestClient(create_app(Broken(), Settings()), raise_server_exceptions=False)
    r = client.post("/api/ask", json={"question": "How many goals?"})
    assert r.status_code == 500
    assert r.json() == {"detail": "ValueError: something odd"}
