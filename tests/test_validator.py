from __future__ import annotations

import pytest

from offsyde.nl2sql.validator import UnsafeSQLError, validate_sql


@pytest.mark.parametrize(
    "sql",
    [
        "DROP TABLE events",
        "DELETE FROM events",
        "UPDATE teams SET name = 'x'",
        "INSERT INTO teams (name) VALUES ('x')",
        "CREATE TABLE t (a INT)",
        "ALTER TABLE teams ADD COLUMN x INT",
        "PRAGMA table_info(events)",
        "ATTACH DATABASE 'x.db' AS x",
        "SELECT 1; DROP TABLE teams",
        "SELECT * FROM teams; SELECT * FROM players",
        "-- harmless\nSELECT * FROM teams; DELETE FROM teams",
        "SELECT * FROM source_ids",
        "SELECT * FROM ingestion_runs",
        "SELECT * FROM sqlite_master",
        "SELECT * FROM main.events",
        "SELECT load_extension('evil')",
        "SELECT readfile('/etc/passwd')",
        "WITH d AS (DELETE FROM events RETURNING *) SELECT * FROM d",
        "",
        "   ;  ",
    ],
)
def test_rejects_unsafe_sql(sql):
    with pytest.raises(UnsafeSQLError):
        validate_sql(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM teams",
        "select count(*) from SHOTS where is_goal = 1",
        "SELECT p.name FROM players p JOIN lineups l ON l.player_id = p.id",
        "WITH g AS (SELECT team_id, COUNT(*) c FROM shots GROUP BY team_id) SELECT * FROM g",
        "SELECT name FROM teams UNION SELECT name FROM players",
        "SELECT * FROM teams;",
        "SELECT (SELECT COUNT(*) FROM events) AS n",
    ],
)
def test_accepts_read_only_queries(sql):
    assert validate_sql(sql).upper().startswith(("SELECT", "WITH"))


def test_adds_limit_when_missing():
    assert validate_sql("SELECT * FROM teams", max_rows=50).endswith("LIMIT 50")


def test_clamps_oversized_limit_but_keeps_smaller_one():
    assert validate_sql("SELECT * FROM teams LIMIT 100000", max_rows=200).endswith("LIMIT 200")
    assert validate_sql("SELECT * FROM teams LIMIT 5", max_rows=200).endswith("LIMIT 5")


def test_union_is_wrapped_so_the_cap_applies():
    out = validate_sql("SELECT name FROM teams UNION SELECT name FROM players", max_rows=10)
    assert out.endswith("LIMIT 10") and "combined" in out


def test_unknown_table_error_lists_what_is_available():
    with pytest.raises(UnsafeSQLError, match="Available tables"):
        validate_sql("SELECT * FROM goals")


def test_postgres_dialect_is_supported():
    assert "LIMIT" in validate_sql("SELECT COUNT(*) FROM shots", dialect="postgres")
    with pytest.raises(UnsafeSQLError):
        validate_sql("SELECT pg_sleep(100)", dialect="postgres")
