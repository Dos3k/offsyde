"""Load canonical records into the database.

Properties worth knowing:
- Idempotent: every write is an upsert on a natural key, so re-running never duplicates.
- Incremental: matches whose events are already loaded are skipped unless ``force``.
- Isolated: one transaction per match, so a single bad match cannot corrupt or abort a run.
- Audited: every run is recorded in ``ingestion_runs``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from ..db import upsert
from ..models import Event, IngestionRun, Lineup, Match
from .canonical import CanonicalMatch, SourceAdapter
from .resolver import EntityResolver

log = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


@dataclass
class IngestStats:
    run_id: int | None = None
    matches_loaded: int = 0
    matches_skipped: int = 0
    matches_failed: int = 0
    events_loaded: int = 0
    errors: list[str] = field(default_factory=list)


def _load_match(
    adapter: SourceAdapter, engine: Engine, cm: CanonicalMatch, force: bool
) -> int | None:
    """Returns the number of events loaded, or None if the match was skipped."""
    with Session(engine) as session, session.begin():
        existing = session.scalar(
            select(Match).where(
                Match.source == adapter.name, Match.source_match_id == cm.source_match_id
            )
        )
        if existing is not None and existing.events_loaded and not force:
            return None

        # Fetch + validate everything before writing, so failures leave no partial state.
        lineups = adapter.fetch_lineups(cm.source_match_id)
        events = adapter.fetch_events(cm.source_match_id)

        resolver = EntityResolver(session, adapter.name)
        home_id = resolver.team(cm.home)
        away_id = resolver.team(cm.away)

        match_row = {
            "source": adapter.name,
            "source_match_id": cm.source_match_id,
            "competition_name": cm.competition_name,
            "season_name": cm.season_name,
            "match_date": cm.match_date,
            "kick_off": cm.kick_off,
            "match_week": cm.match_week,
            "stage": cm.stage,
            "home_team_id": home_id,
            "away_team_id": away_id,
            "home_score": cm.home_score,
            "away_score": cm.away_score,
            "stadium": cm.stadium,
            "referee": cm.referee,
            "home_manager": cm.home_manager,
            "away_manager": cm.away_manager,
            "events_loaded": 0,
        }
        conn = session.connection()
        upsert(conn, Match.__table__, [match_row], ["source", "source_match_id"])
        match_id = session.scalar(
            select(Match.id).where(
                Match.source == adapter.name, Match.source_match_id == cm.source_match_id
            )
        )
        assert match_id is not None

        lineup_rows = {}
        for entry in lineups:
            pid = resolver.player(entry.player)
            lineup_rows[pid] = {
                "match_id": match_id,
                "player_id": pid,
                "team_id": resolver.team(entry.team),
                "jersey_number": entry.jersey_number,
                "started": int(entry.started),
            }

        event_rows = []
        for ev in events:
            event_rows.append(
                {
                    "match_id": match_id,
                    "source_event_id": ev.source_event_id,
                    "seq": ev.seq,
                    "period": ev.period,
                    "minute": ev.minute,
                    "second": ev.second,
                    "type": ev.type,
                    "team_id": resolver.team(ev.team) if ev.team else None,
                    "player_id": resolver.player(ev.player) if ev.player else None,
                    "position_name": ev.position_name,
                    "x": ev.x,
                    "y": ev.y,
                    "end_x": ev.end_x,
                    "end_y": ev.end_y,
                    "outcome": ev.outcome,
                    "sub_type": ev.sub_type,
                    "body_part": ev.body_part,
                    "xg": ev.xg,
                    "pass_length": ev.pass_length,
                    "recipient_id": resolver.player(ev.recipient) if ev.recipient else None,
                    "play_pattern": ev.play_pattern,
                    "card": ev.card,
                    "under_pressure": int(ev.under_pressure),
                    "is_goal_assist": int(ev.is_goal_assist),
                    "is_shot_assist": int(ev.is_shot_assist),
                    "details": ev.details or None,
                }
            )

        session.flush()  # make new players/teams visible before the FK-bearing inserts
        upsert(conn, Lineup.__table__, list(lineup_rows.values()), ["match_id", "player_id"])
        upsert(conn, Event.__table__, event_rows, ["match_id", "source_event_id"])
        conn.execute(Match.__table__.update().where(Match.id == match_id).values(events_loaded=1))
        return len(event_rows)


def ingest_competition(
    adapter: SourceAdapter,
    engine: Engine,
    competition_id: str,
    season_id: str,
    *,
    limit: int | None = None,
    force: bool = False,
    on_progress: Callable[[str], None] | None = None,
) -> IngestStats:
    say = on_progress or (lambda _msg: None)
    stats = IngestStats()

    with Session(engine) as session, session.begin():
        run = IngestionRun(
            source=adapter.name,
            params=f"competition={competition_id} season={season_id} limit={limit} force={force}",
            started_at=_now(),
            status="running",
        )
        session.add(run)
        session.flush()
        stats.run_id = run.id

    matches = sorted(adapter.list_matches(competition_id, season_id), key=lambda m: m.match_date)
    if limit:
        matches = matches[:limit]
    say(f"{len(matches)} matches to process")

    for i, cm in enumerate(matches, 1):
        label = f"[{i}/{len(matches)}] {cm.match_date} {cm.home.name} v {cm.away.name}"
        try:
            n = _load_match(adapter, engine, cm, force)
        except Exception as exc:  # noqa: BLE001 - one bad match must not abort the run
            stats.matches_failed += 1
            stats.errors.append(f"{cm.source_match_id}: {exc}")
            log.exception("failed to load match %s", cm.source_match_id)
            say(f"{label}: FAILED ({exc})")
            continue
        if n is None:
            stats.matches_skipped += 1
            say(f"{label}: skipped (already loaded)")
        else:
            stats.matches_loaded += 1
            stats.events_loaded += n
            say(f"{label}: {n} events")

    with Session(engine) as session, session.begin():
        run = session.get(IngestionRun, stats.run_id)
        assert run is not None
        run.finished_at = _now()
        run.status = (
            "failed"
            if stats.matches_failed and not stats.matches_loaded
            else ("partial" if stats.matches_failed else "ok")
        )
        run.matches_loaded = stats.matches_loaded
        run.matches_skipped = stats.matches_skipped
        run.matches_failed = stats.matches_failed
        run.events_loaded = stats.events_loaded
        run.errors = "\n".join(stats.errors[:50]) or None
    return stats
