"""Hallucination guard: every number in a generated answer must be traceable to the data.

The answer model sees the query result and is told to use only those values. We do not
trust that: numbers it writes are checked against the result rows (plus the question and
the SQL, which legitimately contain numbers such as "top 5" or "after minute 75").
If anything is untraceable, the caller falls back to a deterministic rendering of the rows.
"""

from __future__ import annotations

import re
from typing import Any

_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
_TOLERANCE = 0.011


def _interpretations(token: str) -> list[float]:
    values: list[float] = []
    for candidate in (token.replace(",", "."), token.replace(",", "").replace(".", "")):
        try:
            values.append(float(candidate))
        except ValueError:
            continue
    return values


def numbers_in(text: str) -> list[list[float]]:
    return [_interpretations(t) for t in _NUMBER.findall(text)]


def _allowed_values(
    columns: list[str], rows: list[list[Any]], question: str, sql: str
) -> list[float]:
    allowed: list[float] = [float(len(rows))]
    allowed += [float(i) for i in range(1, len(rows) + 1)]  # list numbering
    for text in (question, sql):
        for interp in numbers_in(text):
            allowed += interp
    for row in rows:
        for cell in row:
            if isinstance(cell, bool) or cell is None:
                continue
            if isinstance(cell, int | float):
                allowed += [float(cell), float(cell) * 100.0]  # also "25%" for 0.25
            else:
                for interp in numbers_in(str(cell)):
                    allowed += interp
    # simple derived values a faithful summary may state
    numeric = [
        float(c)
        for row in rows
        for c in row
        if isinstance(c, int | float) and not isinstance(c, bool)
    ]
    if 1 < len(numeric) <= 50:
        allowed.append(sum(numeric))
    return allowed


def is_grounded(
    answer: str, columns: list[str], rows: list[list[Any]], question: str = "", sql: str = ""
) -> bool:
    allowed = _allowed_values(columns, rows, question, sql)
    for interpretations in numbers_in(answer):
        if not interpretations:
            continue
        if not any(abs(v - a) <= _TOLERANCE for v in interpretations for a in allowed):
            return False
    return True


def render_rows(
    columns: list[str], rows: list[list[Any]], lang: str = "en", limit: int = 10
) -> str:
    """Deterministic, hallucination-proof answer built directly from the rows."""
    if not rows:
        return "Keine passenden Daten gefunden." if lang == "de" else "No matching data found."

    def fmt(v: Any) -> str:
        if isinstance(v, float):
            return f"{v:.2f}".rstrip("0").rstrip(".")
        return str(v)

    if len(rows) == 1 and len(columns) == 1:
        return f"{columns[0]}: {fmt(rows[0][0])}"
    lines = [
        ", ".join(f"{c}: {fmt(v)}" for c, v in zip(columns, row, strict=False))
        for row in rows[:limit]
    ]
    more = len(rows) - limit
    if more > 0:
        lines.append(f"... (+{more} weitere)" if lang == "de" else f"... (+{more} more)")
    return "\n".join(lines)
