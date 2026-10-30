"""Prompt construction. Kept free of I/O so it is easy to test and to iterate on."""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import Engine, text

from .schema_doc import DOMAIN_NOTES, render_schema

GERMAN_WORDS = frozenset(
    """wie viele wer welche welcher welches wann wo hat haben ist sind der die das den dem und
    mit von für pro alle jede meisten meiste tore tor spiel spiele spieler saison punkte
    gegen gewonnen verloren insgesamt durchschnittlich zeige gib mir wurde wurden machte
    schoss erzielte erzielt gelbe rote karten wieviele nach vor zwischen""".split()
)


def detect_language(question: str) -> str:
    words = re.findall(r"\w+", question.casefold())
    hits = sum(w in GERMAN_WORDS for w in words)
    return "de" if hits >= 2 or (words and hits / len(words) >= 0.25) else "en"


SYSTEM_SQL = """\
You are a careful football data analyst. You translate questions (English or German) into ONE \
read-only {dialect} SELECT query over the schema you are given.
Rules:
- Output exactly one SQL query inside a ```sql code block, and nothing else.
- Use only the tables and columns in the schema. Never write INSERT, UPDATE, DELETE, DDL or PRAGMA.
- Use the entity hints for names. Prefer `id` filters over name matching.
- The data covers only the loaded matches (see "Loaded data"); never assume other matches exist.
- Give columns readable aliases.\
"""

EXAMPLES: list[tuple[str, str]] = [
    (
        "How many league points did Bayer Leverkusen collect away from home?",
        "SELECT SUM(points) AS away_points FROM team_matches "
        "WHERE team_name = 'Bayer Leverkusen' AND is_home = 0",
    ),
    (
        "Who were the top 3 goal scorers overall?",
        "SELECT p.name, COUNT(*) AS goals FROM shots s JOIN players p ON p.id = s.player_id "
        "WHERE s.is_goal = 1 AND s.period < 5 GROUP BY p.id, p.name ORDER BY goals DESC LIMIT 3",
    ),
    (
        "Wie viele gelbe Karten gab es insgesamt?",
        "SELECT COUNT(*) AS gelbe_karten FROM events WHERE card = 'Yellow Card'",
    ),
    (
        "Who scored the most goals in the 2019/2020 season?",
        "SELECT p.name, COUNT(*) AS goals FROM shots s JOIN players p ON p.id = s.player_id "
        "WHERE s.is_goal = 1 AND s.period < 5 AND s.season_name = '2019/2020' "
        "GROUP BY p.id, p.name ORDER BY goals DESC LIMIT 1",
    ),
    (
        "What is Wirtz's average xG per shot?",
        "SELECT ROUND(AVG(xg), 3) AS avg_xg FROM shots WHERE player_id = "
        "(SELECT id FROM players WHERE name LIKE '%Wirtz%' OR nickname LIKE '%Wirtz%' LIMIT 1)",
    ),
]


def build_value_hints(engine: Engine) -> str:
    """Facts about what is actually loaded, so the model does not guess at coverage."""
    with engine.connect() as conn:
        groups = conn.execute(
            text(
                "SELECT competition_name, season_name, COUNT(*) FROM matches "
                "GROUP BY competition_name, season_name ORDER BY competition_name, season_name"
            )
        ).all()
        # For each season: which team (if any) is in every one of its matches?
        per_team = conn.execute(
            text(
                "SELECT competition_name, season_name, team_name, COUNT(*) AS n "
                "FROM team_matches GROUP BY competition_name, season_name, team_id, team_name"
            )
        ).all()
        types = [r[0] for r in conn.execute(text("SELECT DISTINCT type FROM events ORDER BY type"))]
        weeks: dict[tuple[str, str], list[int]] = {}
        for comp, season, week in conn.execute(
            text("SELECT competition_name, season_name, match_week FROM matches")
        ):
            if week is not None:
                weeks.setdefault((comp, season), []).append(week)
    lines = ["Loaded data:"]
    for comp, season, n in groups:
        focus = [t for c, s, t, k in per_team if (c, s) == (comp, season) and k == n]
        note = (
            f"; every match involves {focus[0]}, other teams only appear in their games against it "
            "(no league-wide rankings possible)"
            if len(focus) == 1
            else ""
        )
        lines.append(f"- {comp} {season}: {n} matches{note}")
        have = set(weeks.get((comp, season), []))
        missing = sorted(set(range(1, max(have) + 1)) - have) if n >= 10 and have else []
        if missing:
            lines.append(
                f"  INCOMPLETE: match weeks {', '.join(map(str, missing))} are not loaded, "
                "so season totals (points, goals, wins) cover loaded matches only."
            )
    teams = sorted({t for _, _, t, _ in per_team})
    lines.append("Teams: " + ", ".join(teams))
    lines.append("Event types: " + ", ".join(types))
    return "\n".join(lines)


def build_sql_prompt(question: str, entity_hints: str, value_hints: str) -> str:
    examples = "\n\n".join(f"Question: {q}\n```sql\n{sql}\n```" for q, sql in EXAMPLES)
    return (
        f"SCHEMA\n{render_schema()}\n\n"
        f"NOTES\n{DOMAIN_NOTES}\n\n"
        f"{value_hints}\n\n"
        f"EXAMPLES\n{examples}\n\n"
        f"ENTITY HINTS (exact database values for names in the question)\n{entity_hints}\n\n"
        f"Question: {question}"
    )


def build_repair_prompt(base_prompt: str, previous_sql: str, error: str) -> str:
    return (
        f"{base_prompt}\n\n"
        f"Your previous attempt was rejected.\n"
        f"Previous query:\n```sql\n{previous_sql}\n```\n"
        f"Problem: {error}\n"
        f"Write a corrected query. Output one ```sql block only."
    )


SYSTEM_ANSWER = """\
You answer football data questions using ONLY the query result you are given.
- Be concise: one to three sentences, or a short list for several rows.
- Use only numbers and names that appear in the result. Never estimate or add outside facts.
- The data may cover only part of a season. Never present a total as the full-season figure
  unless the question is explicitly about the loaded matches.
- Do not mention SQL, tables or queries.
- Answer in {language}.\
"""


def build_answer_prompt(
    question: str, columns: list[str], rows: list[list[Any]], total_rows: int, max_rows: int = 30
) -> str:
    shown = rows[:max_rows]
    body = "\n".join(
        " | ".join(f"{c}={v}" for c, v in zip(columns, row, strict=False)) for row in shown
    )
    extra = f"\n(showing {len(shown)} of {total_rows} rows)" if total_rows > len(shown) else ""
    return f"Question: {question}\n\nQuery result ({total_rows} rows):\n{body}{extra}"


_SQL_BLOCK = re.compile(r"```(?:sql|sqlite|postgresql)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)
_BARE_SQL = re.compile(r"\b(WITH|SELECT)\b.*", re.DOTALL | re.IGNORECASE)


def extract_sql(llm_output: str) -> str:
    """Pull the query out of a model reply (fenced block preferred, bare SQL tolerated)."""
    m = _SQL_BLOCK.search(llm_output)
    if m:
        return m.group(1).strip()
    m = _BARE_SQL.search(llm_output)
    return m.group(0).strip() if m else llm_output.strip()
