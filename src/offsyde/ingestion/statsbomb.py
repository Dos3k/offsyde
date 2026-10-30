"""StatsBomb open-data adapter.

Data: https://github.com/statsbomb/open-data (free for non-commercial use; attribution
to StatsBomb is required when you publish anything built on it).

Flow per resource: raw store (cache) -> HTTP with retry/backoff -> Pydantic validation
of the source format -> mapping to canonical records.
"""

from __future__ import annotations

import logging
import time
from datetime import date
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from .canonical import (
    CanonicalEvent,
    CanonicalMatch,
    LineupEntry,
    PlayerRef,
    TeamRef,
)
from .raw_store import RawStore

log = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://raw.githubusercontent.com/statsbomb/open-data/master/data"
RETRY_STATUS = {429, 500, 502, 503, 504}


# --------------------------------------------------------------------------- raw formats
class _Lenient(BaseModel):
    model_config = ConfigDict(extra="ignore")


class SBNamed(_Lenient):
    id: int
    name: str


class SBCountry(_Lenient):
    id: int | None = None
    name: str


class SBManager(_Lenient):
    id: int
    name: str
    nickname: str | None = None


class SBHome(_Lenient):
    home_team_id: int
    home_team_name: str
    country: SBCountry | None = None
    managers: list[SBManager] | None = None


class SBAway(_Lenient):
    away_team_id: int
    away_team_name: str
    country: SBCountry | None = None
    managers: list[SBManager] | None = None


class SBCompetition(_Lenient):
    competition_id: int
    season_id: int
    competition_name: str
    season_name: str
    country_name: str | None = None


class SBMatch(_Lenient):
    match_id: int
    match_date: date
    kick_off: str | None = None
    competition: SBCompetitionRef
    season: SBSeasonRef
    home_team: SBHome
    away_team: SBAway
    home_score: int | None = None
    away_score: int | None = None
    match_week: int | None = None
    competition_stage: SBNamed | None = None
    stadium: SBNamed | None = None
    referee: SBNamed | None = None


class SBCompetitionRef(_Lenient):
    competition_id: int
    competition_name: str


class SBSeasonRef(_Lenient):
    season_id: int
    season_name: str


SBMatch.model_rebuild()


class SBPosition(_Lenient):
    start_reason: str | None = None


class SBLineupPlayer(_Lenient):
    player_id: int
    player_name: str
    player_nickname: str | None = None
    jersey_number: int | None = None
    country: SBCountry | None = None
    positions: list[SBPosition] = []


class SBLineupTeam(_Lenient):
    team_id: int
    team_name: str
    lineup: list[SBLineupPlayer]


class SBEvent(BaseModel):
    """Core event fields are validated; type-specific blocks (``pass``, ``shot``, ...)
    are kept as extras and read generically."""

    model_config = ConfigDict(extra="allow")

    id: str
    index: int
    period: int
    minute: int
    second: int
    type: SBNamed
    team: SBNamed | None = None
    player: SBNamed | None = None
    position: SBNamed | None = None
    location: list[float] | None = None
    play_pattern: SBNamed | None = None
    under_pressure: bool = False


# ------------------------------------------------------------------------------ mapping
_TYPE_KEY_OVERRIDES = {"goal_keeper": "goalkeeper"}
_HANDLED_KEYS = {
    "end_location",
    "outcome",
    "type",
    "body_part",
    "statsbomb_xg",
    "length",
    "recipient",
    "card",
    "freeze_frame",
    "key_pass_id",
    "assisted_shot_id",
    "goal_assist",
    "shot_assist",
}


def _type_key(type_name: str) -> str:
    key = type_name.lower().replace(" ", "_").replace("/", "_").replace("*", "")
    return _TYPE_KEY_OVERRIDES.get(key, key)


def _name(block: dict[str, Any], key: str) -> str | None:
    value = block.get(key)
    return value.get("name") if isinstance(value, dict) else None


def map_event(ev: SBEvent) -> CanonicalEvent:
    block = (ev.model_extra or {}).get(_type_key(ev.type.name))
    block = block if isinstance(block, dict) else {}

    end = block.get("end_location") or []
    outcome = _name(block, "outcome")
    if ev.type.name == "Pass" and outcome is None:
        outcome = "Complete"  # StatsBomb omits the outcome for successful passes

    recipient = block.get("recipient")
    details = {k: v for k, v in block.items() if k not in _HANDLED_KEYS}
    if ev.type.name == "Shot":
        technique = _name(block, "technique")
        if technique:
            details["technique"] = technique

    return CanonicalEvent(
        source_event_id=ev.id,
        seq=ev.index,
        period=ev.period,
        minute=ev.minute,
        second=ev.second,
        type=ev.type.name,
        team=TeamRef(source_id=str(ev.team.id), name=ev.team.name) if ev.team else None,
        player=PlayerRef(source_id=str(ev.player.id), name=ev.player.name) if ev.player else None,
        position_name=ev.position.name if ev.position else None,
        x=ev.location[0] if ev.location and len(ev.location) >= 2 else None,
        y=ev.location[1] if ev.location and len(ev.location) >= 2 else None,
        end_x=end[0] if len(end) >= 2 else None,
        end_y=end[1] if len(end) >= 2 else None,
        outcome=outcome,
        sub_type=_name(block, "type"),
        body_part=_name(block, "body_part"),
        xg=block.get("statsbomb_xg"),
        pass_length=block.get("length") if ev.type.name == "Pass" else None,
        recipient=(
            PlayerRef(source_id=str(recipient["id"]), name=recipient["name"])
            if isinstance(recipient, dict)
            else None
        ),
        play_pattern=ev.play_pattern.name if ev.play_pattern else None,
        card=_name(block, "card"),
        under_pressure=ev.under_pressure,
        is_goal_assist=bool(block.get("goal_assist")),
        is_shot_assist=bool(block.get("shot_assist")),
        details=details,
    )


def map_match(m: SBMatch) -> CanonicalMatch:
    def manager(side: SBHome | SBAway) -> str | None:
        return side.managers[0].name if side.managers else None

    return CanonicalMatch(
        source_match_id=str(m.match_id),
        competition_name=m.competition.competition_name,
        season_name=m.season.season_name,
        match_date=m.match_date,
        kick_off=m.kick_off,
        match_week=m.match_week,
        stage=m.competition_stage.name if m.competition_stage else None,
        home=TeamRef(
            source_id=str(m.home_team.home_team_id),
            name=m.home_team.home_team_name,
            country=m.home_team.country.name if m.home_team.country else None,
        ),
        away=TeamRef(
            source_id=str(m.away_team.away_team_id),
            name=m.away_team.away_team_name,
            country=m.away_team.country.name if m.away_team.country else None,
        ),
        home_score=m.home_score,
        away_score=m.away_score,
        stadium=m.stadium.name if m.stadium else None,
        referee=m.referee.name if m.referee else None,
        home_manager=manager(m.home_team),
        away_manager=manager(m.away_team),
    )


def map_lineups(teams: list[SBLineupTeam]) -> list[LineupEntry]:
    out: list[LineupEntry] = []
    for t in teams:
        team = TeamRef(source_id=str(t.team_id), name=t.team_name)
        for p in t.lineup:
            out.append(
                LineupEntry(
                    team=team,
                    player=PlayerRef(
                        source_id=str(p.player_id),
                        name=p.player_name,
                        nickname=p.player_nickname,
                        country=p.country.name if p.country else None,
                    ),
                    jersey_number=p.jersey_number,
                    started=any(pos.start_reason == "Starting XI" for pos in p.positions),
                )
            )
    return out


# ---------------------------------------------------------------------------- the adapter
class StatsBombAdapter:
    name = "statsbomb"

    def __init__(
        self,
        raw_store: RawStore | None = None,
        *,
        base_url: str = DEFAULT_BASE_URL,
        client: httpx.Client | None = None,
        max_attempts: int = 5,
        backoff_s: float = 1.0,
        min_interval_s: float = 0.0,
    ):
        self.raw_store = raw_store
        self.base_url = base_url.rstrip("/")
        self.client = client or httpx.Client(timeout=60.0, follow_redirects=True)
        self.max_attempts = max_attempts
        self.backoff_s = backoff_s
        self.min_interval_s = min_interval_s
        self._last_request = 0.0

    # -- transport -----------------------------------------------------------------
    def _get_json(self, path: str, endpoint: str, key: str) -> Any:
        if self.raw_store is not None:
            cached = self.raw_store.load(self.name, endpoint, key)
            if cached is not None:
                return cached

        url = f"{self.base_url}/{path}"
        last_error: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            wait = self.min_interval_s - (time.monotonic() - self._last_request)
            if wait > 0:
                time.sleep(wait)
            self._last_request = time.monotonic()
            try:
                resp = self.client.get(url)
            except httpx.TransportError as exc:
                last_error = exc
            else:
                if resp.status_code in RETRY_STATUS:
                    last_error = httpx.HTTPStatusError(
                        f"{resp.status_code} for {url}", request=resp.request, response=resp
                    )
                else:
                    resp.raise_for_status()
                    payload = resp.json()
                    if self.raw_store is not None:
                        self.raw_store.save(self.name, endpoint, key, payload)
                    return payload
            if attempt < self.max_attempts:
                delay = self.backoff_s * 2 ** (attempt - 1)
                log.warning(
                    "retry %d/%d for %s in %.1fs (%s)",
                    attempt,
                    self.max_attempts,
                    url,
                    delay,
                    last_error,
                )
                time.sleep(delay)
        raise RuntimeError(f"giving up on {url} after {self.max_attempts} attempts: {last_error}")

    @staticmethod
    def _validate(model: type[BaseModel], payload: Any, what: str) -> Any:
        try:
            return model.model_validate(payload)
        except ValidationError as exc:
            raise ValueError(f"unexpected StatsBomb format in {what}: {exc}") from exc

    # -- public API ----------------------------------------------------------------
    def list_competitions(self) -> list[SBCompetition]:
        raw = self._get_json("competitions.json", "competitions", "all")
        return [self._validate(SBCompetition, c, "competitions") for c in raw]

    def list_matches(self, competition_id: str, season_id: str) -> list[CanonicalMatch]:
        raw = self._get_json(
            f"matches/{competition_id}/{season_id}.json",
            "matches",
            f"{competition_id}_{season_id}",
        )
        return [map_match(self._validate(SBMatch, m, "match")) for m in raw]

    def fetch_lineups(self, source_match_id: str) -> list[LineupEntry]:
        raw = self._get_json(f"lineups/{source_match_id}.json", "lineups", source_match_id)
        return map_lineups([self._validate(SBLineupTeam, t, "lineup") for t in raw])

    def fetch_events(self, source_match_id: str) -> list[CanonicalEvent]:
        raw = self._get_json(f"events/{source_match_id}.json", "events", source_match_id)
        return [map_event(self._validate(SBEvent, e, "event")) for e in raw]
