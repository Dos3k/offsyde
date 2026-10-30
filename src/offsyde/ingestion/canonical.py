"""Source-agnostic records that every adapter must produce.

Adapters turn their own payloads into these models; the pipeline only ever sees these.
Adding a new data source therefore means writing one adapter class and nothing else.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Protocol

from pydantic import BaseModel, Field


class TeamRef(BaseModel):
    source_id: str
    name: str
    country: str | None = None


class PlayerRef(BaseModel):
    source_id: str
    name: str
    nickname: str | None = None
    country: str | None = None


class CanonicalMatch(BaseModel):
    source_match_id: str
    competition_name: str
    season_name: str
    match_date: date
    kick_off: str | None = None
    match_week: int | None = None
    stage: str | None = None
    home: TeamRef
    away: TeamRef
    home_score: int | None = None
    away_score: int | None = None
    stadium: str | None = None
    referee: str | None = None
    home_manager: str | None = None
    away_manager: str | None = None


class LineupEntry(BaseModel):
    team: TeamRef
    player: PlayerRef
    jersey_number: int | None = None
    started: bool = False


class CanonicalEvent(BaseModel):
    source_event_id: str
    seq: int
    period: int
    minute: int
    second: int
    type: str
    team: TeamRef | None = None
    player: PlayerRef | None = None
    position_name: str | None = None
    x: float | None = None
    y: float | None = None
    end_x: float | None = None
    end_y: float | None = None
    outcome: str | None = None
    sub_type: str | None = None
    body_part: str | None = None
    xg: float | None = None
    pass_length: float | None = None
    recipient: PlayerRef | None = None
    play_pattern: str | None = None
    card: str | None = None
    under_pressure: bool = False
    is_goal_assist: bool = False
    is_shot_assist: bool = False
    details: dict[str, Any] = Field(default_factory=dict)


class SourceAdapter(Protocol):
    """The whole contract a data source has to fulfil."""

    name: str

    def list_matches(self, competition_id: str, season_id: str) -> list[CanonicalMatch]: ...

    def fetch_lineups(self, source_match_id: str) -> list[LineupEntry]: ...

    def fetch_events(self, source_match_id: str) -> list[CanonicalEvent]: ...
