"""The question-answering pipeline.

question -> entity linking -> prompt -> LLM -> SQL guardrail -> read-only execution
         -> (repair loop on failure) -> grounded answer
"""

from __future__ import annotations

import logging
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from sqlalchemy import Engine

from ..config import Settings
from ..db import make_engine, verify_database
from .entities import EntityLinker, EntityMatch, format_hints
from .executor import QueryError, QueryResult, execute_readonly
from .grounding import is_grounded, render_rows
from .llm import LLMClient, LLMError, OllamaClient
from .prompts import (
    SYSTEM_ANSWER,
    SYSTEM_SQL,
    build_answer_prompt,
    build_repair_prompt,
    build_sql_prompt,
    build_value_hints,
    detect_language,
    extract_sql,
)
from .validator import UnsafeSQLError, validate_sql

log = logging.getLogger(__name__)

SQLGLOT_DIALECTS = {"sqlite": "sqlite", "postgresql": "postgres"}


@dataclass
class AskResult:
    question: str
    answer: str
    sql: str | None = None
    columns: list[str] = field(default_factory=list)
    rows: list[list[Any]] = field(default_factory=list)
    row_count: int = 0
    attempts: int = 0
    answer_source: str = "none"  # "llm" | "template" | "none"
    grounded: bool = True
    language: str = "en"
    entities: list[dict[str, Any]] = field(default_factory=list)
    latency_ms: float = 0.0
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class AskService:
    def __init__(
        self,
        engine: Engine,
        llm: LLMClient,
        *,
        max_rows: int = 200,
        max_repair_attempts: int = 2,
        linker: EntityLinker | None = None,
        value_hints: str | None = None,
    ):
        self.engine = engine
        self.llm = llm
        self.max_rows = max_rows
        self.max_repair_attempts = max_repair_attempts
        self.dialect = SQLGLOT_DIALECTS.get(engine.dialect.name, "sqlite")
        self.linker = linker or EntityLinker(engine)
        self.value_hints = value_hints or build_value_hints(engine)

    def ask(self, question: str, *, generate_answer: bool = True) -> AskResult:
        started = time.perf_counter()
        question = question.strip()
        lang = detect_language(question)
        entities = self.linker.link(question)
        result = AskResult(
            question=question,
            answer="",
            language=lang,
            entities=[_entity_dict(e) for e in entities],
        )

        base_prompt = build_sql_prompt(question, format_hints(entities), self.value_hints)
        system = SYSTEM_SQL.format(dialect=self.dialect)
        prompt = base_prompt
        query: QueryResult | None = None

        for attempt in range(1, self.max_repair_attempts + 2):
            result.attempts = attempt
            candidate = extract_sql(self.llm.chat(system, prompt))
            try:
                safe_sql = validate_sql(candidate, dialect=self.dialect, max_rows=self.max_rows)
                query = execute_readonly(self.engine, safe_sql)
                result.sql = safe_sql
                result.error = None
                break
            except (UnsafeSQLError, QueryError) as exc:
                log.info("attempt %d rejected: %s", attempt, exc)
                result.sql = candidate
                result.error = str(exc)
                prompt = build_repair_prompt(base_prompt, candidate, str(exc))

        if query is None:
            result.answer = (
                "Ich konnte keine gültige Abfrage für diese Frage erstellen."
                if lang == "de"
                else "I couldn't produce a valid query for that question."
            )
            result.latency_ms = (time.perf_counter() - started) * 1000
            return result

        result.columns, result.rows, result.row_count = query.columns, query.rows, len(query.rows)
        if generate_answer:
            self._compose_answer(result)
        result.latency_ms = (time.perf_counter() - started) * 1000
        return result

    def _compose_answer(self, result: AskResult) -> None:
        template = render_rows(result.columns, result.rows, result.language)
        if not result.rows:
            result.answer, result.answer_source = template, "template"
            return
        try:
            answer = self.llm.chat(
                SYSTEM_ANSWER.format(language="German" if result.language == "de" else "English"),
                build_answer_prompt(result.question, result.columns, result.rows, result.row_count),
            ).strip()
        except LLMError as exc:
            log.warning("answer generation failed, using template: %s", exc)
            answer = ""
        if answer and is_grounded(
            answer, result.columns, result.rows, result.question, result.sql or ""
        ):
            result.answer, result.answer_source = answer, "llm"
        else:
            # Never show a sentence containing numbers we cannot trace back to the data.
            result.answer, result.answer_source = template, "template"
            result.grounded = not answer  # False only when a model answer was rejected


def _entity_dict(e: EntityMatch) -> dict[str, Any]:
    return {"kind": e.kind, "mention": e.mention, "id": e.id, "name": e.name, "score": e.score}


def build_service(settings: Settings, llm: LLMClient | None = None) -> AskService:
    engine = make_engine(settings.db_url, readonly=True, query_timeout_s=settings.query_timeout_s)
    verify_database(engine, settings.db_url)
    llm = llm or OllamaClient(
        settings.model,
        settings.ollama_host,
        num_ctx=settings.num_ctx,
        timeout_s=settings.llm_timeout_s,
    )
    return AskService(
        engine,
        llm,
        max_rows=settings.max_rows,
        max_repair_attempts=settings.max_repair_attempts,
    )
