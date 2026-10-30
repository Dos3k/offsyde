from __future__ import annotations

import pytest

from offsyde.nl2sql.llm import LLMError
from offsyde.nl2sql.service import AskService


class ScriptedLLM:
    """Returns canned replies in order; records every prompt it was given."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls: list[tuple[str, str]] = []

    def chat(self, system, user):
        self.calls.append((system, user))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def sql(q: str) -> str:
    return f"```sql\n{q}\n```"


@pytest.fixture
def make(readonly_engine):
    def _make(*replies, **kw):
        llm = ScriptedLLM(*replies)
        return AskService(readonly_engine, llm, **kw), llm

    return _make


def test_happy_path(make):
    svc, llm = make(
        sql(
            "SELECT COUNT(*) AS wins FROM team_matches "
            "WHERE team_name = 'Bayer Leverkusen' AND result = 'W'"
        ),
        "Bayer Leverkusen won 2 matches.",
    )
    r = svc.ask("How many matches did Leverkusen win?")
    assert r.rows == [[2]] and r.columns == ["wins"]
    assert r.answer == "Bayer Leverkusen won 2 matches."
    assert r.answer_source == "llm" and r.grounded and r.attempts == 1 and r.error is None
    assert [e["name"] for e in r.entities] == ["Bayer Leverkusen"]
    assert "teams.id =" in llm.calls[0][1]  # entity hint reached the prompt
    assert "LIMIT" in r.sql  # guardrail normalised the query


def test_prompt_contains_schema_and_data_coverage(make):
    svc, llm = make(sql("SELECT 1 FROM teams"), "x")
    svc.ask("Anything")
    prompt = llm.calls[0][1]
    assert "TABLE team_matches" in prompt and "TABLE shots" in prompt
    assert "Loaded data:" in prompt and "1. Bundesliga 2023/2024: 2 matches" in prompt
    assert "read-only" in llm.calls[0][0]
    assert "source_ids" not in prompt  # internal tables are never exposed


def test_unsafe_sql_triggers_repair_with_feedback(make):
    svc, llm = make(
        sql("DROP TABLE events"),
        sql("SELECT COUNT(*) FROM events"),
        "There are many events.",
    )
    r = svc.ask("How many events are there?")
    assert r.attempts == 2 and r.error is None and r.rows[0][0] > 0
    repair_prompt = llm.calls[1][1]
    assert "was rejected" in repair_prompt and "DROP TABLE events" in repair_prompt
    assert "only SELECT" in repair_prompt


def test_database_error_triggers_repair(make):
    svc, llm = make(
        sql("SELECT nope FROM teams"), sql("SELECT COUNT(*) FROM teams"), "There are 3 teams."
    )
    r = svc.ask("How many teams?")
    assert r.attempts == 2 and r.rows == [[3]]
    assert "no such column" in llm.calls[1][1]


def test_gives_up_after_repair_budget(make):
    svc, _ = make(
        sql("DROP TABLE teams"),
        sql("DELETE FROM teams"),
        sql("UPDATE teams SET name='x'"),
        max_repair_attempts=2,
    )
    r = svc.ask("Please delete everything")
    assert r.attempts == 3 and r.error and r.rows == []
    assert "couldn't produce a valid query" in r.answer


def test_german_fallback_messages(make):
    svc, _ = make(sql("DROP TABLE teams"), max_repair_attempts=0)
    r = svc.ask("Wie viele Tore hat Leverkusen geschossen?")
    assert r.language == "de" and "keine gültige Abfrage" in r.answer


def test_hallucinated_numbers_never_reach_the_user(make):
    svc, _ = make(
        sql("SELECT COUNT(*) AS wins FROM team_matches WHERE result = 'W'"),
        "Leverkusen won 99 matches this season!",
    )
    r = svc.ask("How many wins?")
    assert r.answer_source == "template" and r.grounded is False
    assert "99" not in r.answer and "wins: 2" in r.answer


def test_answer_model_failure_falls_back_to_template(make):
    svc, _ = make(sql("SELECT COUNT(*) AS n FROM teams"), LLMError("ollama down"))
    r = svc.ask("How many teams?")
    assert r.answer_source == "template" and r.answer == "n: 3"


def test_empty_result_is_reported_without_asking_the_model(make):
    svc, llm = make(sql("SELECT * FROM teams WHERE name = 'Nobody FC'"))
    r = svc.ask("Show Nobody FC")
    assert r.rows == [] and r.answer == "No matching data found."
    assert len(llm.calls) == 1  # no answer-generation call


def test_generate_answer_can_be_skipped(make):
    svc, llm = make(sql("SELECT COUNT(*) FROM teams"))
    r = svc.ask("How many teams?", generate_answer=False)
    assert r.rows == [[3]] and r.answer == "" and len(llm.calls) == 1


def test_sql_llm_outage_propagates(make):
    svc, _ = make(LLMError("cannot reach Ollama"))
    with pytest.raises(LLMError):
        svc.ask("How many teams?")


def test_row_cap_is_enforced(make):
    svc, _ = make(sql("SELECT id FROM events"), "ok", max_rows=7)
    r = svc.ask("List all events")
    assert r.row_count == 7
