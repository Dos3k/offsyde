from __future__ import annotations

from sqlalchemy import text

from conftest import MAINZ_LEVERKUSEN
from offsyde.ingestion import ingest_competition, run_quality_checks


def counts(engine) -> dict[str, int]:
    with engine.connect() as c:
        return {
            t: c.execute(text(f"SELECT COUNT(*) FROM {t}")).scalar_one()
            for t in ("teams", "players", "matches", "lineups", "events", "source_ids")
        }


def test_ingest_loads_everything(loaded_engine):
    n = counts(loaded_engine)
    assert n["matches"] == 2
    assert n["teams"] == 3  # Leverkusen, Leipzig, Mainz
    assert n["events"] > 150 and n["players"] > 20


def test_rerun_skips_loaded_matches(loaded_engine, adapter):
    before = counts(loaded_engine)
    stats = ingest_competition(adapter, loaded_engine, "9", "281")
    assert (stats.matches_loaded, stats.matches_skipped) == (0, 2)
    assert counts(loaded_engine) == before


def test_force_reload_is_idempotent(loaded_engine, adapter):
    before = counts(loaded_engine)
    stats = ingest_competition(adapter, loaded_engine, "9", "281", force=True)
    assert stats.matches_loaded == 2
    assert counts(loaded_engine) == before  # upserts, not duplicates


def test_limit_restricts_matches(engine, adapter):
    stats = ingest_competition(adapter, engine, "9", "281", limit=1)
    assert stats.matches_loaded == 1
    assert counts(engine)["matches"] == 1


def test_failed_match_is_isolated_and_recorded(engine, adapter):
    real = adapter.fetch_events

    def broken(match_id):
        if match_id == MAINZ_LEVERKUSEN:
            raise RuntimeError("boom")
        return real(match_id)

    adapter.fetch_events = broken  # type: ignore[method-assign]
    stats = ingest_competition(adapter, engine, "9", "281")
    assert (stats.matches_loaded, stats.matches_failed) == (1, 1)
    assert MAINZ_LEVERKUSEN in stats.errors[0]
    with engine.connect() as c:
        row = c.execute(text("SELECT status, matches_failed, errors FROM ingestion_runs")).one()
        assert row.status == "partial" and row.matches_failed == 1 and "boom" in row.errors
        # the failed match left no half-written rows behind
        assert c.execute(text("SELECT COUNT(*) FROM matches")).scalar_one() == 1


def test_run_is_audited(loaded_engine):
    with loaded_engine.connect() as c:
        row = c.execute(
            text("SELECT status, matches_loaded, events_loaded FROM ingestion_runs")
        ).one()
    assert row.status == "ok" and row.matches_loaded == 2 and row.events_loaded > 0


def test_source_ids_map_every_entity(loaded_engine):
    with loaded_engine.connect() as c:
        mapped = c.execute(text("SELECT COUNT(*) FROM source_ids")).scalar_one()
        entities = c.execute(
            text("SELECT (SELECT COUNT(*) FROM teams) + (SELECT COUNT(*) FROM players)")
        ).scalar_one()
    assert mapped == entities


def test_lineups_enrich_players(loaded_engine):
    with loaded_engine.connect() as c:
        with_country = c.execute(
            text("SELECT COUNT(*) FROM players WHERE country IS NOT NULL")
        ).scalar_one()
        starters = c.execute(text("SELECT COUNT(*) FROM lineups WHERE started = 1")).scalar_one()
    assert with_country > 0 and starters > 0


def test_quality_checks_pass_on_real_fixtures(loaded_engine):
    assert run_quality_checks(loaded_engine) == []


def test_own_goal_counts_towards_score(loaded_engine):
    # Mainz 0-3 Leverkusen: two Leverkusen shots + one Mainz own goal.
    with loaded_engine.connect() as c:
        shots = c.execute(
            text(
                "SELECT COUNT(*) FROM shots s JOIN matches m ON m.id = s.match_id "
                f"WHERE m.source_match_id = '{MAINZ_LEVERKUSEN}' AND s.is_goal = 1"
            )
        ).scalar_one()
    assert shots == 2
    assert run_quality_checks(loaded_engine) == []


def test_quality_check_detects_wrong_score(loaded_engine):
    with loaded_engine.begin() as c:
        c.execute(text("UPDATE matches SET home_score = home_score + 1 WHERE id = 1"))
    issues = run_quality_checks(loaded_engine)
    assert any(i.check == "score_consistency" for i in issues)


def test_quality_check_detects_bad_coordinates(loaded_engine):
    with loaded_engine.begin() as c:
        c.execute(
            text(
                "UPDATE events SET x = 500 "
                "WHERE id = (SELECT MIN(id) FROM events WHERE x IS NOT NULL)"
            )
        )
    assert any(i.check == "coordinates" for i in run_quality_checks(loaded_engine))


def test_views_return_sensible_data(loaded_engine):
    with loaded_engine.connect() as c:
        rows = c.execute(
            text(
                "SELECT team_name, SUM(points), SUM(goals_for) FROM team_matches "
                "GROUP BY team_name ORDER BY team_name"
            )
        ).all()
        assert [(r[0], r[1], r[2]) for r in rows] == [
            ("Bayer Leverkusen", 6, 6),
            ("FSV Mainz 05", 0, 0),
            ("RB Leipzig", 0, 2),
        ]
        assert c.execute(text("SELECT COUNT(*) FROM passes WHERE is_complete = 1")).scalar_one() > 0


def test_touchline_overshoot_is_tolerated_but_gross_errors_are_not(loaded_engine):
    with loaded_engine.begin() as c:
        c.execute(
            text(
                "UPDATE events SET y = 80.5 "
                "WHERE id = (SELECT MIN(id) FROM events WHERE y IS NOT NULL)"
            )
        )
    assert run_quality_checks(loaded_engine) == []
