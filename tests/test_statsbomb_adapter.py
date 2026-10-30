from __future__ import annotations

import httpx
import pytest

from conftest import LEVERKUSEN_LEIPZIG, MAINZ_LEVERKUSEN, fixture_handler, make_adapter
from offsyde.ingestion.statsbomb import SBEvent, map_event


def test_list_matches_maps_canonical_fields(adapter):
    matches = {m.source_match_id: m for m in adapter.list_matches("9", "281")}
    m = matches[LEVERKUSEN_LEIPZIG]
    assert (m.home.name, m.away.name) == ("Bayer Leverkusen", "RB Leipzig")
    assert (m.home_score, m.away_score) == (3, 2)
    assert m.competition_name == "1. Bundesliga" and m.season_name == "2023/2024"
    assert m.home_manager == "Xabier Alonso Olano"
    assert m.stadium


def test_events_are_mapped(adapter):
    events = adapter.fetch_events(LEVERKUSEN_LEIPZIG)
    passes = [e for e in events if e.type == "Pass"]
    shots = [e for e in events if e.type == "Shot"]
    assert passes and shots

    p = passes[0]
    assert p.outcome in {"Complete", "Incomplete", "Out", "Pass Offside", "Unknown"}
    assert p.x is not None and p.end_x is not None and p.pass_length is not None
    assert p.player is not None

    goals = [s for s in shots if s.outcome == "Goal"]
    assert len(goals) == 5  # 3 + 2 in the recorded match
    assert all(s.xg is not None and 0 <= s.xg <= 1 for s in shots)
    assert all(s.sub_type for s in shots)


def test_successful_pass_gets_explicit_complete_outcome():
    ev = SBEvent.model_validate(
        {
            "id": "x",
            "index": 1,
            "period": 1,
            "minute": 3,
            "second": 4,
            "type": {"id": 30, "name": "Pass"},
            "team": {"id": 1, "name": "A"},
            "player": {"id": 2, "name": "P"},
            "location": [10.0, 20.0],
            "pass": {
                "recipient": {"id": 3, "name": "Q"},
                "length": 5.0,
                "end_location": [15.0, 20.0],
                "goal_assist": True,
            },
        }
    )
    c = map_event(ev)
    assert c.outcome == "Complete"
    assert c.recipient and c.recipient.name == "Q"
    assert c.is_goal_assist and not c.is_shot_assist


def test_cards_are_extracted(adapter):
    cards = [
        e
        for e in adapter.fetch_events(MAINZ_LEVERKUSEN) + adapter.fetch_events(LEVERKUSEN_LEIPZIG)
        if e.card
    ]
    assert cards, "fixtures should contain at least one carded foul"
    assert {c.card for c in cards} <= {"Yellow Card", "Second Yellow", "Red Card"}


def test_lineups_flag_starters(adapter):
    entries = adapter.fetch_lineups(LEVERKUSEN_LEIPZIG)
    assert entries and any(e.started for e in entries)
    assert all(e.team.name in {"Bayer Leverkusen", "RB Leipzig"} for e in entries)


def test_unexpected_format_fails_loudly(tmp_path):
    def handler(request):
        return httpx.Response(200, json=[{"match_id": "not-a-number"}])

    adapter = make_adapter(tmp_path, handler)
    with pytest.raises(ValueError, match="unexpected StatsBomb format"):
        adapter.list_matches("9", "281")


def test_retries_transient_errors_then_succeeds(tmp_path):
    attempts = {"n": 0}
    inner = fixture_handler()

    def flaky(request):
        attempts["n"] += 1
        if attempts["n"] <= 2:
            return httpx.Response(503, text="try later")
        return inner(request)

    adapter = make_adapter(tmp_path, flaky)
    assert adapter.list_matches("9", "281")
    assert attempts["n"] == 3


def test_gives_up_after_max_attempts(tmp_path):
    adapter = make_adapter(tmp_path, lambda r: httpx.Response(429), max_attempts=3)
    with pytest.raises(RuntimeError, match="giving up"):
        adapter.list_matches("9", "281")


def test_client_errors_are_not_retried(tmp_path):
    calls: list[str] = []
    adapter = make_adapter(tmp_path, fixture_handler(calls))
    with pytest.raises(httpx.HTTPStatusError):
        adapter.fetch_events("does-not-exist")
    assert len(calls) == 1


def test_raw_store_acts_as_cache(tmp_path):
    calls: list[str] = []
    adapter = make_adapter(tmp_path, fixture_handler(calls))
    adapter.fetch_events(LEVERKUSEN_LEIPZIG)
    adapter.fetch_events(LEVERKUSEN_LEIPZIG)
    assert len(calls) == 1
    assert (tmp_path / "raw" / "statsbomb" / "events" / f"{LEVERKUSEN_LEIPZIG}.json").exists()
