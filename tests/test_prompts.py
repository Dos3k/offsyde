from __future__ import annotations

from sqlalchemy import text

from offsyde.nl2sql.prompts import build_value_hints


def add_season(engine, competition: str, season: str, weeks: list[int], team_ids=(1, 2)):
    with engine.begin() as conn:
        for i, week in enumerate(weeks):
            conn.execute(
                text(
                    "INSERT INTO matches (source, source_match_id, competition_name, season_name,"
                    " match_date, match_week, home_team_id, away_team_id, home_score, away_score,"
                    " events_loaded) VALUES ('t', :sid, :c, :s, '2020-01-01', :w, :h, :a, 1, 0, 1)"
                ),
                {
                    "sid": f"{competition}{season}{i}",
                    "c": competition,
                    "s": season,
                    "w": week,
                    "h": team_ids[0],
                    "a": team_ids[1],
                },
            )


def test_single_team_dataset_is_flagged(loaded_engine):
    hints = build_value_hints(loaded_engine)
    assert "1. Bundesliga 2023/2024: 2 matches; every match involves Bayer Leverkusen" in hints
    assert "no league-wide rankings possible" in hints
    assert "Teams:" in hints and "Event types:" in hints


def test_each_season_is_listed_separately(loaded_engine):
    add_season(loaded_engine, "La Liga", "2019/2020", list(range(1, 13)))
    hints = build_value_hints(loaded_engine)
    assert "La Liga 2019/2020: 12 matches" in hints
    assert "1. Bundesliga 2023/2024: 2 matches" in hints


def test_missing_match_weeks_are_reported(loaded_engine):
    weeks = [w for w in range(1, 15) if w not in (4, 9)]
    add_season(loaded_engine, "La Liga", "2018/2019", weeks)
    hints = build_value_hints(loaded_engine)
    assert "INCOMPLETE: match weeks 4, 9 are not loaded" in hints


def test_complete_or_tiny_seasons_are_not_flagged(loaded_engine):
    add_season(loaded_engine, "La Liga", "2017/2018", list(range(1, 13)))
    assert "INCOMPLETE" not in build_value_hints(loaded_engine)  # fixture has only 2 matches
