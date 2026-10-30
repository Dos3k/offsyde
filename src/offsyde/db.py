"""Engine creation, schema setup, and a dialect-aware bulk upsert."""

from __future__ import annotations

import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, Table, create_engine, event, text
from sqlalchemy.engine import make_url

from .models import Base
from .views import VIEWS


class DatabaseNotReady(RuntimeError):
    """The database is missing, empty, or not loaded. The message tells the user what to do."""


def _sqlite_path(url: str) -> Path | None:
    parsed = make_url(url)
    if parsed.get_backend_name() == "sqlite" and parsed.database not in (None, "", ":memory:"):
        return Path(parsed.database)
    return None


def verify_database(engine: Engine, url: str) -> None:
    """Fail early and clearly instead of with an opaque 'no such table' later."""
    path = _sqlite_path(url)
    if path is not None and not path.exists():
        raise DatabaseNotReady(
            f"Database file not found: {path.resolve()} (working directory: {Path.cwd()}). "
            "Unzip offsyde_database.zip inside the project folder so it lands at "
            "data/offsyde.db, run `offsyde ingest`, or set OFFSYDE_DB_URL to the right file."
        )
    try:
        with engine.connect() as conn:
            n = conn.execute(text("SELECT COUNT(*) FROM matches")).scalar_one()
    except Exception as exc:  # noqa: BLE001 - any failure here means "not usable"
        raise DatabaseNotReady(
            "The database exists but has no match data "
            f"({type(exc).__name__}). Run `offsyde ingest` or use the prebuilt database."
        ) from exc
    if n == 0:
        raise DatabaseNotReady("The database contains no matches. Run `offsyde ingest`.")


def make_engine(url: str, *, readonly: bool = False, query_timeout_s: float = 10.0) -> Engine:
    """Create an engine. With ``readonly=True`` the DB session itself refuses writes
    and aborts long queries, independent of any SQL validation done upstream."""
    parsed = make_url(url)
    backend = parsed.get_backend_name()

    if backend == "sqlite":
        if parsed.database and parsed.database != ":memory:":
            if readonly and not Path(parsed.database).exists():
                # Opening a missing file would silently create an empty database.
                raise DatabaseNotReady(
                    f"Database file not found: {Path(parsed.database).resolve()} "
                    f"(working directory: {Path.cwd()}). Unzip offsyde_database.zip inside the "
                    "project folder so it lands at data/offsyde.db, run `offsyde ingest`, "
                    "or set OFFSYDE_DB_URL to the right file."
                )
            Path(parsed.database).parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(url, connect_args={"check_same_thread": False})
        deadlines: dict[int, float] = {}

        @event.listens_for(engine, "connect")
        def _sqlite_connect(dbapi_conn: Any, _rec: Any) -> None:
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            if readonly:
                cur.execute("PRAGMA query_only=ON")
            cur.close()
            if readonly:
                key = id(dbapi_conn)
                # Called every N SQLite VM instructions; non-zero return aborts the query.
                dbapi_conn.set_progress_handler(
                    lambda: 1 if time.monotonic() > deadlines.get(key, float("inf")) else 0,
                    50_000,
                )

        if readonly:

            @event.listens_for(engine, "before_cursor_execute")
            def _set_deadline(conn: Any, *_a: Any) -> None:
                deadlines[id(conn.connection.dbapi_connection)] = time.monotonic() + query_timeout_s

        return engine

    engine = create_engine(url, pool_pre_ping=True)
    if backend == "postgresql" and readonly:

        @event.listens_for(engine, "connect")
        def _pg_connect(dbapi_conn: Any, _rec: Any) -> None:
            cur = dbapi_conn.cursor()
            cur.execute("SET default_transaction_read_only = on")
            cur.execute(f"SET statement_timeout = {int(query_timeout_s * 1000)}")
            cur.close()
            dbapi_conn.commit()

    return engine


def create_schema(engine: Engine) -> None:
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        for name, sql in VIEWS.items():
            conn.execute(text(f"DROP VIEW IF EXISTS {name}"))
            conn.execute(text(f"CREATE VIEW {name} AS {sql}"))


def upsert(
    conn: Any,
    table: Table,
    rows: Sequence[dict[str, Any]],
    conflict_cols: Sequence[str],
    update_cols: Sequence[str] | None = None,
) -> None:
    """INSERT ... ON CONFLICT DO UPDATE, so re-running a load never duplicates rows."""
    if not rows:
        return
    dialect = conn.dialect.name
    if dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
    elif dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:  # pragma: no cover
        raise NotImplementedError(f"upsert not implemented for {dialect}")

    if update_cols is None:
        update_cols = [c for c in rows[0] if c not in conflict_cols]
    stmt = insert(table)
    if update_cols:
        stmt = stmt.on_conflict_do_update(
            index_elements=list(conflict_cols),
            set_={c: stmt.excluded[c] for c in update_cols},
        )
    else:
        stmt = stmt.on_conflict_do_nothing(index_elements=list(conflict_cols))
    conn.execute(stmt, list(rows))
