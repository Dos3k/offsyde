"""PostgreSQL integration tests.

Skipped unless OFFSYDE_TEST_PG_URL points at a database whose name ends in ``_test``.
WARNING: the schema of that database is dropped and recreated for every test.

    createdb offsyde_test
    export OFFSYDE_TEST_PG_URL=postgresql+psycopg://user@localhost/offsyde_test
    pytest tests/test_postgres.py
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url

from offsyde.config import Settings
from offsyde.db import create_schema, make_engine
from offsyde.evals import run_benchmark
from offsyde.ingestion import ingest_competition, run_quality_checks
from offsyde.nl2sql.executor import QueryError, execute_readonly

PG_URL = os.getenv("OFFSYDE_TEST_PG_URL")
pytestmark = pytest.mark.skipif(
    not PG_URL, reason="set OFFSYDE_TEST_PG_URL to run PostgreSQL tests"
)

BENCHMARK = Path(__file__).parent.parent / "benchmarks" / "bundesliga_2023_24.json"


@pytest.fixture
def pg_engine(adapter):
    assert PG_URL is not None
    assert (make_url(PG_URL).database or "").endswith("_test"), (
        "refusing to wipe a non-test database"
    )
    engine = make_engine(PG_URL)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    create_schema(engine)
    ingest_competition(adapter, engine, "9", "281")
    yield engine
    engine.dispose()


def test_ingest_and_quality_checks(pg_engine):
    with pg_engine.connect() as c:
        assert c.execute(text("SELECT COUNT(*) FROM matches")).scalar_one() == 2
    assert run_quality_checks(pg_engine) == []


def test_force_reload_is_idempotent(pg_engine, adapter):
    def n():
        with pg_engine.connect() as c:
            return c.execute(text("SELECT COUNT(*) FROM events")).scalar_one()

    before = n()
    ingest_competition(adapter, pg_engine, "9", "281", force=True)
    assert n() == before


def test_rounding_works_on_numeric_columns(pg_engine):
    ro = make_engine(PG_URL, readonly=True)
    result = execute_readonly(ro, "SELECT ROUND(AVG(xg), 3) FROM shots")
    assert 0 < result.rows[0][0] < 1


def test_oracle_benchmark_passes(pg_engine):
    report = run_benchmark(BENCHMARK, Settings(db_url=PG_URL), oracle=True)
    assert report.metrics()["execution_accuracy"] == 1.0


def test_session_is_read_only_and_has_a_timeout(pg_engine):
    ro = make_engine(PG_URL, readonly=True, query_timeout_s=1)
    with pytest.raises(QueryError, match="read-only"):
        execute_readonly(ro, "DELETE FROM teams")
    with pytest.raises(QueryError, match="time limit"):
        execute_readonly(ro, "SELECT pg_sleep(5)")
