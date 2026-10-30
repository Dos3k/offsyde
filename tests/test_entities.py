from __future__ import annotations

import pytest
from sqlalchemy import text

from offsyde.db import create_schema, make_engine
from offsyde.nl2sql.entities import AMBIGUOUS, EntityLinker, format_hints, normalise


@pytest.fixture
def linker(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'e.db'}")
    create_schema(engine)
    teams = [
        "Bayer Leverkusen",
        "Bayern Munich",
        "Borussia Dortmund",
        "Borussia Mönchengladbach",
        "1. FC Köln",
        "RB Leipzig",
        "Union Berlin",
    ]
    players = [
        ("Florian Wirtz", None),
        ("Alejandro Grimaldo García", "Álex Grimaldo"),
        ("Thomas Müller", None),
        ("Kevin Müller", None),
        ("Tim Müller", None),
        ("Jannik Müller", None),
        ("Granit Xhaka", None),
        ("Victor Okoh Boniface", "Victor Boniface"),
    ]
    with engine.begin() as c:
        for t in teams:
            c.execute(text("INSERT INTO teams (name) VALUES (:n)"), {"n": t})
        for n, nick in players:
            c.execute(
                text("INSERT INTO players (name, nickname) VALUES (:n, :k)"), {"n": n, "k": nick}
            )
    return EntityLinker(engine)


def names(matches):
    return {m.name for m in matches}


def test_normalise_folds_accents_and_german_transliterations():
    assert normalise("Müller") == normalise("Mueller") == normalise("Muller") == "muller"
    assert normalise("Mönchengladbach") == normalise("Moenchengladbach")
    assert normalise("Straße") == "strasse"


def test_exact_player_and_team(linker):
    found = linker.link("How many goals did Wirtz score against Bayern?")
    assert names(found) == {"Florian Wirtz", "Bayern Munich"}


def test_nickname_resolves_to_database_name(linker):
    assert names(linker.link("Alex Grimaldo assists")) == {"Alejandro Grimaldo García"}
    assert names(linker.link("Boniface goals")) == {"Victor Okoh Boniface"}


def test_team_nicknames_and_german_names(linker):
    assert names(linker.link("Spiele gegen BVB")) == {"Borussia Dortmund"}
    assert names(linker.link("Ergebnis gegen Bayern München")) == {"Bayern Munich"}
    assert names(linker.link("Spiel in Köln")) == {"1. FC Köln"}
    assert names(linker.link("Gladbach match")) == {"Borussia Mönchengladbach"}


def test_german_umlaut_spellings_find_the_same_player(linker):
    for spelling in ("Thomas Müller", "Thomas Mueller", "Thomas Muller"):
        assert names(linker.link(f"Tore von {spelling}")) == {"Thomas Müller"}


def test_surname_shared_by_many_players_is_flagged_ambiguous(linker):
    found = linker.link("Wie viele Tore hat Mueller geschossen?")
    assert [m.id for m in found] == [AMBIGUOUS]
    hint = format_hints(found)
    assert "ambiguous" in hint and "Thomas Müller" in hint and "Kevin Müller" in hint


def test_typo_is_fixed_by_fuzzy_match(linker):
    assert names(linker.link("Goals by Granit Xhaaka")) == {"Granit Xhaka"}
    assert names(linker.link("Results of Leverkusn")) == {"Bayer Leverkusen"}


def test_ordinary_words_do_not_match(linker):
    assert linker.link("Which player scored the most goals this season?") == []
    assert linker.link("wie viele tore wurden insgesamt erzielt") == []


def test_longest_phrase_wins_over_fuzzy_leftovers(linker):
    found = linker.link("How did Leverkusen do?")
    assert names(found) == {"Bayer Leverkusen"} and found[0].score == 100


def test_hints_contain_ids_for_prompting(linker):
    hint = format_hints(linker.link("Xhaka"))
    assert "players.id =" in hint and "Granit Xhaka" in hint
    assert format_hints([]) == "(none)"


def seed_players(tmp_path, players: dict[str, int]):
    """Create players with a given number of events each, via the ORM (which fills defaults)."""
    from datetime import date

    from sqlalchemy.orm import Session

    from offsyde.models import Event, Match, Player, Team

    engine = make_engine(f"sqlite:///{tmp_path / 'seed.db'}")
    create_schema(engine)
    with Session(engine) as session, session.begin():
        session.add_all([Team(id=1, name="A"), Team(id=2, name="B")])
        session.flush()
        session.add(
            Match(
                id=1,
                source="t",
                source_match_id="1",
                competition_name="L",
                season_name="s",
                match_date=date(2020, 1, 1),
                home_team_id=1,
                away_team_id=2,
                events_loaded=1,
            )
        )
        session.flush()
        for pid, (name, n_events) in enumerate(players.items(), start=1):
            session.add(Player(id=pid, name=name))
            session.flush()
            session.add_all(
                Event(
                    match_id=1,
                    source_event_id=f"{pid}-{k}",
                    seq=k,
                    period=1,
                    minute=1,
                    second=1,
                    type="Pass",
                    player_id=pid,
                )
                for k in range(n_events)
            )
    return engine


def test_dominant_player_wins_an_otherwise_ambiguous_surname(tmp_path):
    engine = seed_players(
        tmp_path,
        {
            "Luis Suarez": 30,
            "Denis Suarez": 2,
            "Mario Suarez": 2,
            "Pablo Suarez": 1,
            "Juan Suarez": 1,
        },
    )
    assert [m.name for m in EntityLinker(engine).link("Suarez assists")] == ["Luis Suarez"]


def test_ambiguous_candidates_are_ordered_by_activity(tmp_path):
    engine = seed_players(tmp_path, {"A Garcia": 5, "B Garcia": 9, "C Garcia": 7, "D Garcia": 8})
    found = EntityLinker(engine).link("Goals by Garcia")
    assert found[0].id == AMBIGUOUS
    assert found[0].name.split("; ")[:2] == ["B Garcia", "D Garcia"]
