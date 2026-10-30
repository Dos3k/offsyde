from __future__ import annotations

import pytest

from offsyde.db import make_engine
from offsyde.nl2sql.executor import QueryError, execute_readonly


def test_returns_plain_python_values(readonly_engine):
    r = execute_readonly(readonly_engine, "SELECT name, country FROM teams ORDER BY name")
    assert r.columns == ["name", "country"]
    assert ["Bayer Leverkusen", "Germany"] in r.rows


def test_database_session_refuses_writes_even_if_sql_got_through(readonly_engine):
    with pytest.raises(QueryError, match="readonly"):
        execute_readonly(readonly_engine, "DELETE FROM teams")


def test_runaway_query_is_aborted(loaded_engine):
    engine = make_engine(str(loaded_engine.url), readonly=True, query_timeout_s=0.3)
    with pytest.raises(QueryError, match="time limit"):
        execute_readonly(
            engine,
            "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c WHERE x < 900000000) "
            "SELECT COUNT(*) FROM c",
        )


def test_sql_errors_become_query_errors(readonly_engine):
    with pytest.raises(QueryError, match="no such column"):
        execute_readonly(readonly_engine, "SELECT nope FROM teams")
