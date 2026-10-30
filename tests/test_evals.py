from __future__ import annotations

import json
from pathlib import Path

import pytest

from offsyde.config import Settings
from offsyde.evals import results_match, run_benchmark, write_report
from offsyde.nl2sql.service import AskService
from offsyde.nl2sql.validator import validate_sql

BENCHMARK = Path(__file__).parent.parent / "benchmarks" / "bundesliga_2023_24.json"


# ------------------------------------------------------------------ result matching
def test_matching_ignores_row_and_column_order():
    gold = [["a", 1], ["b", 2]]
    assert results_match(gold, [[2, "b"], [1, "a"]])


def test_extra_columns_are_allowed_but_missing_ones_are_not():
    assert results_match([["a"], ["b"]], [["a", 1], ["b", 2]])
    assert not results_match([["a", 1]], [["a"]])


def test_wrong_values_or_row_counts_fail():
    assert not results_match([[28]], [[27]])
    assert not results_match([[1], [2]], [[1]])
    assert not results_match([["x"]], [])


def test_numbers_compare_after_rounding_to_two_decimals():
    assert results_match([[92.31]], [[92.3100001]])
    assert not results_match([[92.3]], [[92.4]])
    assert results_match([[3]], [[3.0]])


def test_projection_must_be_row_aligned():
    gold = [["a", 1], ["b", 2]]
    assert not results_match(gold, [["a", 2], ["b", 1]])  # same column values, wrong pairing


def test_empty_results_match():
    assert results_match([], [])


# --------------------------------------------------------------------- benchmark files
BENCHMARKS = sorted(BENCHMARK.parent.glob("*.json"))


@pytest.fixture(params=BENCHMARKS, ids=lambda p: p.stem)
def cases(request):
    return json.loads(request.param.read_text(encoding="utf-8"))


def test_benchmark_is_well_formed(cases):
    assert len(cases) >= 20
    assert len({c["id"] for c in cases}) == len(cases)
    assert len({c["question"] for c in cases}) == len(cases)
    assert {c["language"] for c in cases} == {"en", "de"}
    for c in cases:
        assert {"id", "category", "language", "question", "gold_sql"} <= set(c)


def test_every_gold_query_passes_the_guardrail(cases):
    for c in cases:
        validate_sql(c["gold_sql"])


def test_every_gold_query_runs_on_the_schema(cases, readonly_engine):
    from offsyde.nl2sql.executor import execute_readonly

    for c in cases:
        execute_readonly(readonly_engine, c["gold_sql"])  # raises on unknown columns/tables


def test_benchmark_ids_are_unique_across_files():
    ids = [c["id"] for path in BENCHMARKS for c in json.loads(path.read_text(encoding="utf-8"))]
    assert len(ids) == len(set(ids))


# --------------------------------------------------------------------------- harness
@pytest.fixture
def settings(loaded_engine):
    return Settings(db_url=str(loaded_engine.url))


def test_oracle_scores_perfectly_proving_the_harness_is_sound(settings):
    report = run_benchmark(BENCHMARK, settings, oracle=True)
    assert report.metrics()["execution_accuracy"] == 1.0
    assert report.metrics()["first_attempt_accuracy"] == 1.0
    assert report.n >= 40


def test_a_bad_model_scores_zero(settings, readonly_engine):
    class Bad:
        def chat(self, system, user):
            return "```sql\nSELECT 'wrong' AS x\n```"

    service = AskService(readonly_engine, Bad())
    report = run_benchmark(BENCHMARK, settings, service=service, limit=10)
    assert report.metrics()["execution_accuracy"] == 0.0
    assert report.metrics()["valid_sql_rate"] == 1.0  # runs fine, just wrong


def test_unsafe_model_output_counts_as_invalid_sql(settings, readonly_engine):
    class Evil:
        def chat(self, system, user):
            return "```sql\nDROP TABLE events\n```"

    report = run_benchmark(
        BENCHMARK, settings, service=AskService(readonly_engine, Evil()), limit=5
    )
    m = report.metrics()
    assert m["valid_sql_rate"] == 0.0 and m["execution_accuracy"] == 0.0
    assert m["avg_attempts"] == 3  # 1 try + 2 repairs


def test_report_summary_and_json(settings, tmp_path):
    report = run_benchmark(BENCHMARK, settings, oracle=True, limit=8)
    assert "execution accuracy" in report.summary()
    out = tmp_path / "r.json"
    write_report(report, out)
    data = json.loads(out.read_text())
    assert data["metrics"]["n"] == 8 and len(data["cases"]) == 8
