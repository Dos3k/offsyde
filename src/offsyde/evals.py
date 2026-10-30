"""Evaluation harness: execution accuracy of generated SQL against gold queries.

A prediction counts as correct when it returns the same data as the gold query: same rows
in any order, columns in any order (extra columns allowed), numbers compared after rounding
to 2 decimals. This is the standard "execution accuracy" idea from text-to-SQL research:
we judge what the query returns, not how it is written.
"""

from __future__ import annotations

import itertools
import json
import statistics
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .config import Settings
from .nl2sql.executor import execute_readonly
from .nl2sql.llm import LLMClient
from .nl2sql.service import AskService, build_service


# ----------------------------------------------------------------------- result matching
def _norm(value: Any) -> Any:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int | float):
        return round(float(value), 2)
    if isinstance(value, str):
        return value.strip()
    return value


def _vec_key(values: list[Any]) -> list[str]:
    return sorted(repr(v) for v in values)


def results_match(
    gold_rows: list[list[Any]], pred_rows: list[list[Any]], max_combinations: int = 2000
) -> bool:
    if len(gold_rows) != len(pred_rows):
        return False
    if not gold_rows:
        return True
    gold = [[_norm(c) for c in row] for row in gold_rows]
    pred = [[_norm(c) for c in row] for row in pred_rows]
    n_gold, n_pred = len(gold[0]), len(pred[0])

    gold_cols = [[row[j] for row in gold] for j in range(n_gold)]
    pred_cols = [[row[i] for row in pred] for i in range(n_pred)]
    pred_keys = [_vec_key(c) for c in pred_cols]

    candidates = []
    for gcol in gold_cols:
        key = _vec_key(gcol)
        options = [i for i, pk in enumerate(pred_keys) if pk == key]
        if not options:
            return False
        candidates.append(options)

    gold_set = sorted(repr(tuple(r)) for r in gold)
    for count, mapping in enumerate(itertools.product(*candidates)):
        if count >= max_combinations:
            break
        projected = sorted(repr(tuple(row[i] for i in mapping)) for row in pred)
        if projected == gold_set:
            return True
    return False


# --------------------------------------------------------------------------------- report
@dataclass
class CaseResult:
    id: str
    category: str
    language: str
    question: str
    correct: bool
    executed: bool
    attempts: int
    first_attempt_correct: bool
    predicted_sql: str | None
    gold_sql: str
    error: str | None
    latency_ms: float
    answer_source: str = "none"
    entities: list[str] = field(default_factory=list)


@dataclass
class Report:
    model: str
    cases: list[CaseResult]

    @property
    def n(self) -> int:
        return len(self.cases)

    @staticmethod
    def _rate(items: list[bool]) -> float:
        return sum(items) / len(items) if items else 0.0

    def metrics(self) -> dict[str, Any]:
        lat = sorted(c.latency_ms for c in self.cases)
        by_cat: dict[str, list[bool]] = defaultdict(list)
        by_lang: dict[str, list[bool]] = defaultdict(list)
        for c in self.cases:
            by_cat[c.category].append(c.correct)
            by_lang[c.language].append(c.correct)
        answered = [c for c in self.cases if c.answer_source != "none"]
        return {
            "model": self.model,
            "n": self.n,
            "execution_accuracy": self._rate([c.correct for c in self.cases]),
            "first_attempt_accuracy": self._rate([c.first_attempt_correct for c in self.cases]),
            "valid_sql_rate": self._rate([c.executed for c in self.cases]),
            "avg_attempts": statistics.mean(c.attempts for c in self.cases) if self.cases else 0,
            "latency_ms_p50": statistics.median(lat) if lat else 0,
            "latency_ms_p95": lat[min(len(lat) - 1, int(0.95 * len(lat)))] if lat else 0,
            "by_category": {k: self._rate(v) for k, v in sorted(by_cat.items())},
            "by_language": {k: self._rate(v) for k, v in sorted(by_lang.items())},
            "llm_answer_accepted_rate": (
                self._rate([c.answer_source == "llm" for c in answered]) if answered else None
            ),
        }

    def summary(self) -> str:
        m = self.metrics()
        lines = [
            f"model: {m['model']}    questions: {m['n']}",
            f"execution accuracy:      {m['execution_accuracy']:.1%}",
            f"  first attempt only:    {m['first_attempt_accuracy']:.1%}",
            f"valid SQL rate:          {m['valid_sql_rate']:.1%}",
            f"avg attempts/question:   {m['avg_attempts']:.2f}",
            f"latency p50 / p95:       {m['latency_ms_p50']:.0f} ms / {m['latency_ms_p95']:.0f} ms",
        ]
        if m["llm_answer_accepted_rate"] is not None:
            lines.append(
                f"LLM answers passing grounding check: {m['llm_answer_accepted_rate']:.1%}"
            )
        lines.append(
            "by category: " + ", ".join(f"{k} {v:.0%}" for k, v in m["by_category"].items())
        )
        lines.append(
            "by language: " + ", ".join(f"{k} {v:.0%}" for k, v in m["by_language"].items())
        )
        failures = [c for c in self.cases if not c.correct]
        if failures:
            lines.append(f"\nfailed ({len(failures)}):")
            for c in failures:
                why = c.error or "wrong result"
                lines.append(f"  {c.id} [{c.category}] {c.question[:70]}  -> {why[:80]}")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {"metrics": self.metrics(), "cases": [asdict(c) for c in self.cases]}


def write_report(report: Report, path: str | Path) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )


# ----------------------------------------------------------------------------- oracle LLM
class OracleLLM:
    """Answers every question with its gold SQL. Used to self-test the harness: it must
    score 100%, otherwise the benchmark or the matching logic is broken."""

    def __init__(self, gold_by_question: dict[str, str]):
        self.gold = gold_by_question

    def chat(self, system: str, user: str) -> str:
        if "read-only" not in system:  # the answer-writing call, not the SQL call
            return "Done."
        question = user.rsplit("Question:", 1)[-1].strip().split("\n")[0].strip()
        return f"```sql\n{self.gold[question]}\n```"


# ---------------------------------------------------------------------------------- runner
def run_benchmark(
    benchmark_path: str | Path,
    settings: Settings,
    *,
    limit: int | None = None,
    oracle: bool = False,
    with_answers: bool = False,
    llm: LLMClient | None = None,
    service: AskService | None = None,
) -> Report:
    cases = json.loads(Path(benchmark_path).read_text(encoding="utf-8"))
    if limit:
        cases = cases[:limit]
    if oracle:
        llm = OracleLLM({c["question"]: c["gold_sql"] for c in cases})
    service = service or build_service(settings, llm=llm)
    gold_engine = service.engine

    results: list[CaseResult] = []
    for case in cases:
        gold = execute_readonly(gold_engine, case["gold_sql"])
        answer = service.ask(case["question"], generate_answer=with_answers)
        correct = False
        if answer.error is None and answer.sql:
            correct = results_match(gold.rows, answer.rows)
        results.append(
            CaseResult(
                id=case["id"],
                category=case["category"],
                language=case["language"],
                question=case["question"],
                correct=correct,
                executed=answer.error is None and answer.sql is not None,
                attempts=answer.attempts,
                first_attempt_correct=correct and answer.attempts == 1,
                predicted_sql=answer.sql,
                gold_sql=case["gold_sql"],
                error=answer.error,
                latency_ms=answer.latency_ms,
                answer_source=answer.answer_source,
                entities=[e["name"] for e in answer.entities],
            )
        )
    model = (
        "oracle (gold SQL)" if oracle else getattr(service.llm, "model", type(service.llm).__name__)
    )
    return Report(model=model, cases=results)


__all__ = ["OracleLLM", "Report", "results_match", "run_benchmark", "write_report"]
