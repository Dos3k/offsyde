from __future__ import annotations

from sqlalchemy import text

from offsyde.nl2sql.executor import execute_readonly
from offsyde.nl2sql.prompts import EXAMPLES, detect_language, extract_sql
from offsyde.nl2sql.schema_doc import ALLOWED_TABLES, SCHEMA
from offsyde.nl2sql.validator import validate_sql

INTERNAL = {"source_ids", "ingestion_runs"}
# Columns that exist in the database but are deliberately not shown to the model.
HIDDEN_COLUMNS = {
    "source",
    "source_match_id",
    "kick_off",
    "events_loaded",
    "source_event_id",
    "details",
}


def db_columns(engine, name):
    with engine.connect() as c:
        return [r[1] for r in c.execute(text(f"PRAGMA table_info({name})"))]


def test_documented_columns_match_the_real_database(loaded_engine):
    """The prompt's schema description must never drift from the database."""
    for table, (_, cols) in SCHEMA.items():
        documented = [name for name, _, _ in cols]
        actual = db_columns(loaded_engine, table)
        assert actual, f"{table} does not exist"
        assert set(documented) <= set(actual), (
            f"{table}: unknown columns {set(documented) - set(actual)}"
        )
        assert set(actual) - set(documented) <= HIDDEN_COLUMNS, (
            f"{table}: undocumented columns {set(actual) - set(documented) - HIDDEN_COLUMNS}"
        )
        assert documented == [c for c in actual if c in documented], (
            f"{table}: column order differs"
        )


def test_every_queryable_table_and_view_is_documented(loaded_engine):
    with loaded_engine.connect() as c:
        names = {
            r[0]
            for r in c.execute(
                text("SELECT name FROM sqlite_master WHERE type IN ('table','view')")
            )
        }
    assert names - INTERNAL - {"sqlite_sequence"} == set(ALLOWED_TABLES)


def test_few_shot_examples_are_valid_and_executable(readonly_engine):
    for _question, query in EXAMPLES:
        execute_readonly(readonly_engine, validate_sql(query))


def test_extract_sql_variants():
    assert extract_sql("```sql\nSELECT 1\n```") == "SELECT 1"
    assert extract_sql("Here you go:\n```\nSELECT 2\n```\nHope it helps") == "SELECT 2"
    assert extract_sql("Sure! SELECT 3 FROM teams") == "SELECT 3 FROM teams"
    assert extract_sql("```SQL\nWITH a AS (SELECT 1) SELECT * FROM a\n```").startswith("WITH")


def test_language_detection():
    assert detect_language("Wie viele Tore hat Wirtz erzielt?") == "de"
    assert detect_language("Wer hat die meisten Schüsse abgegeben?") == "de"
    assert detect_language("How many goals did Wirtz score?") == "en"
    assert detect_language("Wirtz xG") == "en"
