"""Execute validated SQL on a read-only engine and return plain Python values."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError


class QueryError(RuntimeError):
    """Database rejected or aborted the query. The message can be fed back to the model."""


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[list[Any]]


def _plain(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.hex()
    return value


def execute_readonly(engine: Engine, sql: str) -> QueryResult:
    try:
        with engine.connect() as conn:
            result = conn.execute(text(sql))
            columns = list(result.keys())
            rows = [[_plain(v) for v in row] for row in result.fetchall()]
    except DBAPIError as exc:
        message = str(exc.orig).splitlines()[0] if exc.orig else str(exc)
        if "interrupted" in message.lower() or "statement timeout" in message.lower():
            message = "query exceeded the time limit; simplify it"
        raise QueryError(message) from exc
    return QueryResult(columns=columns, rows=rows)
