"""Command line interface: ``offsyde ingest | check | ask | eval | serve | competitions``."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import replace

from .config import Settings
from .nl2sql.llm import LLMError


def _engine(settings: Settings, *, readonly: bool = False):
    from .db import make_engine

    return make_engine(settings.db_url, readonly=readonly, query_timeout_s=settings.query_timeout_s)


def cmd_competitions(args: argparse.Namespace, settings: Settings) -> int:
    from .ingestion import RawStore, StatsBombAdapter

    adapter = StatsBombAdapter(RawStore(settings.raw_dir))
    for c in sorted(adapter.list_competitions(), key=lambda c: (c.competition_name, c.season_name)):
        print(f"{c.competition_id:>4} {c.season_id:>4}  {c.competition_name} {c.season_name}")
    return 0


def cmd_ingest(args: argparse.Namespace, settings: Settings) -> int:
    from .db import create_schema
    from .ingestion import RawStore, StatsBombAdapter, ingest_competition, run_quality_checks

    engine = _engine(settings)
    create_schema(engine)
    adapter = StatsBombAdapter(RawStore(settings.raw_dir), min_interval_s=args.min_interval)
    stats = ingest_competition(
        adapter,
        engine,
        str(args.competition),
        str(args.season),
        limit=args.limit,
        force=args.force,
        on_progress=print,
    )
    print(
        f"\nrun {stats.run_id}: loaded={stats.matches_loaded} skipped={stats.matches_skipped} "
        f"failed={stats.matches_failed} events={stats.events_loaded}"
    )
    issues = run_quality_checks(engine)
    print("quality checks:", "all passed" if not issues else f"{len(issues)} issue(s)")
    for issue in issues:
        print(" ", issue)
    return 1 if stats.matches_failed or issues else 0


def cmd_check(args: argparse.Namespace, settings: Settings) -> int:
    from .ingestion import run_quality_checks

    issues = run_quality_checks(_engine(settings))
    for issue in issues:
        print(issue)
    print("all checks passed" if not issues else f"{len(issues)} issue(s)")
    return 1 if issues else 0


def cmd_ask(args: argparse.Namespace, settings: Settings) -> int:
    from .nl2sql.service import build_service

    service = build_service(settings)
    result = service.ask(args.question)
    if args.json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2, default=str))
        return 0
    print(f"\n{result.answer}\n")
    if result.sql:
        print(f"SQL ({result.attempts} attempt(s), {result.latency_ms:.0f} ms):\n{result.sql}")
    return 0 if not result.error else 1


def cmd_eval(args: argparse.Namespace, settings: Settings) -> int:
    from .evals import run_benchmark, write_report

    report = run_benchmark(
        args.benchmark,
        settings,
        limit=args.limit,
        oracle=args.oracle,
        with_answers=args.with_answers,
    )
    print(report.summary())
    if args.out:
        write_report(report, args.out)
        print(f"\nreport written to {args.out}")
    return 0


def cmd_serve(args: argparse.Namespace, settings: Settings) -> int:
    import uvicorn

    uvicorn.run("offsyde.api.main:app", host=args.host, port=args.port, reload=False)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="offsyde", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("competitions", help="list competitions available in StatsBomb open data")

    p = sub.add_parser("ingest", help="download and load a competition season")
    p.add_argument(
        "--competition", type=int, default=9, help="StatsBomb competition id (9 = Bundesliga)"
    )
    p.add_argument("--season", type=int, default=281, help="StatsBomb season id (281 = 2023/24)")
    p.add_argument("--limit", type=int, help="only the first N matches by date")
    p.add_argument("--force", action="store_true", help="reload matches that are already loaded")
    p.add_argument("--min-interval", type=float, default=0.0, help="seconds between requests")

    sub.add_parser("check", help="run data-quality checks on the loaded data")

    p = sub.add_parser("ask", help="ask a question from the terminal")
    p.add_argument("question")
    p.add_argument("--json", action="store_true")
    p.add_argument("--model", help="Ollama model name (default: $OFFSYDE_MODEL)")

    p = sub.add_parser("eval", help="run the evaluation benchmark")
    p.add_argument("--benchmark", default="benchmarks/bundesliga_2023_24.json")
    p.add_argument("--limit", type=int)
    p.add_argument("--model", help="Ollama model name (default: $OFFSYDE_MODEL)")
    p.add_argument("--out", help="write a JSON report here")
    p.add_argument(
        "--oracle", action="store_true", help="answer with the gold SQL (harness self-test)"
    )
    p.add_argument(
        "--with-answers", action="store_true", help="also generate and grounding-check answers"
    )

    p = sub.add_parser("serve", help="start the web API and chat UI")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    handler = {
        "competitions": cmd_competitions,
        "ingest": cmd_ingest,
        "check": cmd_check,
        "ask": cmd_ask,
        "eval": cmd_eval,
        "serve": cmd_serve,
    }[args.command]
    settings = Settings.from_env()
    if getattr(args, "model", None):
        settings = replace(settings, model=args.model)
    try:
        return handler(args, settings)
    except LLMError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
