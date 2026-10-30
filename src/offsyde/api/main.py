"""FastAPI application: JSON API plus a tiny chat UI."""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import text

from .. import __version__
from ..config import Settings
from ..db import DatabaseNotReady
from ..nl2sql.llm import LLMError
from ..nl2sql.schema_doc import SCHEMA
from ..nl2sql.service import AskService, build_service

log = logging.getLogger("offsyde.api")
STATIC = Path(__file__).parent / "static"


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=500)


class RateLimiter:
    """Token bucket per client IP. In-memory: fine for one process, use Redis for many."""

    def __init__(self, per_minute: int):
        self.capacity = float(per_minute)
        self.rate = per_minute / 60.0
        self._buckets: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            tokens, last = self._buckets.get(key, (self.capacity, now))
            tokens = min(self.capacity, tokens + (now - last) * self.rate)
            allowed = tokens >= 1.0
            self._buckets[key] = (tokens - 1.0 if allowed else tokens, now)
            return allowed


def create_app(service: AskService | None = None, settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    app = FastAPI(title="Offsyde", version=__version__, docs_url="/docs")
    limiter = RateLimiter(settings.rate_limit_per_min)
    state: dict[str, Any] = {"service": service}

    def get_service() -> AskService:
        if state["service"] is None:  # built lazily so the app can start before the DB exists
            try:
                state["service"] = build_service(settings)
            except DatabaseNotReady as exc:
                raise HTTPException(status_code=503, detail=str(exc)) from exc
        return state["service"]

    def rate_limit(request: Request) -> None:
        client = request.client.host if request.client else "unknown"
        if not limiter.allow(client):
            raise HTTPException(status_code=429, detail="Too many requests. Please slow down.")

    @app.exception_handler(Exception)
    async def unexpected_error(_request: Request, exc: Exception) -> JSONResponse:
        # Always answer in JSON so the UI shows the real reason, not a parse error.
        log.exception("unhandled error")
        return JSONResponse(
            status_code=500, content={"detail": f"{type(exc).__name__}: {str(exc)[:300]}"}
        )

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    @app.get("/health")
    def health() -> dict[str, Any]:
        info: dict[str, Any] = {"status": "ok", "version": __version__, "model": settings.model}
        try:
            svc = get_service()
            with svc.engine.connect() as conn:
                info["matches"] = conn.execute(text("SELECT COUNT(*) FROM matches")).scalar_one()
        except Exception as exc:  # noqa: BLE001
            info.update(status="degraded", detail=str(getattr(exc, "detail", exc))[:400])
        return info

    @app.get("/api/datasets")
    def datasets(svc: AskService = Depends(get_service)) -> list[dict[str, Any]]:
        with svc.engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT competition_name, season_name, COUNT(*) FROM matches "
                    "GROUP BY competition_name, season_name ORDER BY competition_name, season_name"
                )
            ).all()
        return [{"competition": c, "season": s, "matches": n} for c, s, n in rows]

    @app.get("/api/schema")
    def schema() -> dict[str, Any]:
        return {
            t: {
                "description": d,
                "columns": [{"name": n, "type": ty, "description": cd} for n, ty, cd in cols],
            }
            for t, (d, cols) in SCHEMA.items()
        }

    @app.post("/api/ask", dependencies=[Depends(rate_limit)])
    def ask(req: AskRequest, svc: AskService = Depends(get_service)) -> dict[str, Any]:
        try:
            result = svc.ask(req.question)
        except LLMError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        log.info(
            "ask attempts=%d rows=%d source=%s %.0fms",
            result.attempts,
            result.row_count,
            result.answer_source,
            result.latency_ms,
        )
        return result.to_dict()

    return app


app = create_app()
