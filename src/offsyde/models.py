"""Canonical relational schema.

The schema is source-agnostic: every adapter maps its own format onto these tables.
Flags are stored as INTEGER 0/1 on purpose so the same SQL runs on SQLite and PostgreSQL.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# SQLite: plain float. PostgreSQL: NUMERIC, because Postgres has no round(double precision, int)
# and model-written queries like ROUND(AVG(xg), 2) must work on both.
FloatCol = Float().with_variant(Numeric(14, 8, asdecimal=False), "postgresql")


class Base(DeclarativeBase):
    pass


class Team(Base):
    __tablename__ = "teams"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), index=True)
    country: Mapped[str | None] = mapped_column(String(80))


class Player(Base):
    __tablename__ = "players"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(160), index=True)
    nickname: Mapped[str | None] = mapped_column(String(160))
    country: Mapped[str | None] = mapped_column(String(80))


class SourceId(Base):
    """Maps a source's own ID for an entity to our internal ID."""

    __tablename__ = "source_ids"

    entity_type: Mapped[str] = mapped_column(String(20), primary_key=True)
    source: Mapped[str] = mapped_column(String(40), primary_key=True)
    source_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    internal_id: Mapped[int] = mapped_column(Integer)

    __table_args__ = (Index("ix_source_ids_internal", "entity_type", "internal_id"),)


class Match(Base):
    __tablename__ = "matches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(40))
    source_match_id: Mapped[str] = mapped_column(String(80))
    competition_name: Mapped[str] = mapped_column(String(120))
    season_name: Mapped[str] = mapped_column(String(40))
    match_date: Mapped[date] = mapped_column(Date, index=True)
    kick_off: Mapped[str | None] = mapped_column(String(20))
    match_week: Mapped[int | None] = mapped_column(Integer)
    stage: Mapped[str | None] = mapped_column(String(60))
    home_team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"))
    away_team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"))
    home_score: Mapped[int | None] = mapped_column(Integer)
    away_score: Mapped[int | None] = mapped_column(Integer)
    stadium: Mapped[str | None] = mapped_column(String(160))
    referee: Mapped[str | None] = mapped_column(String(160))
    home_manager: Mapped[str | None] = mapped_column(String(160))
    away_manager: Mapped[str | None] = mapped_column(String(160))
    events_loaded: Mapped[int] = mapped_column(Integer, default=0)

    __table_args__ = (UniqueConstraint("source", "source_match_id", name="uq_match_source"),)


class Lineup(Base):
    __tablename__ = "lineups"

    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"), primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"))
    jersey_number: Mapped[int | None] = mapped_column(Integer)
    started: Mapped[int] = mapped_column(Integer, default=0)


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"))
    source_event_id: Mapped[str] = mapped_column(String(64))
    seq: Mapped[int] = mapped_column(Integer)
    period: Mapped[int] = mapped_column(Integer)
    minute: Mapped[int] = mapped_column(Integer)
    second: Mapped[int] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(String(40))
    team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"))
    player_id: Mapped[int | None] = mapped_column(ForeignKey("players.id"))
    position_name: Mapped[str | None] = mapped_column(String(40))
    x: Mapped[float | None] = mapped_column(FloatCol)
    y: Mapped[float | None] = mapped_column(FloatCol)
    end_x: Mapped[float | None] = mapped_column(FloatCol)
    end_y: Mapped[float | None] = mapped_column(FloatCol)
    outcome: Mapped[str | None] = mapped_column(String(40))
    sub_type: Mapped[str | None] = mapped_column(String(40))
    body_part: Mapped[str | None] = mapped_column(String(30))
    xg: Mapped[float | None] = mapped_column(FloatCol)
    pass_length: Mapped[float | None] = mapped_column(FloatCol)
    recipient_id: Mapped[int | None] = mapped_column(ForeignKey("players.id"))
    play_pattern: Mapped[str | None] = mapped_column(String(40))
    card: Mapped[str | None] = mapped_column(String(30))
    under_pressure: Mapped[int] = mapped_column(Integer, default=0)
    is_goal_assist: Mapped[int] = mapped_column(Integer, default=0)
    is_shot_assist: Mapped[int] = mapped_column(Integer, default=0)
    details: Mapped[dict | None] = mapped_column(JSON)

    __table_args__ = (
        UniqueConstraint("match_id", "source_event_id", name="uq_event_source"),
        Index("ix_events_match_type", "match_id", "type"),
        Index("ix_events_type", "type"),
        Index("ix_events_player", "player_id"),
        Index("ix_events_team", "team_id"),
    )


class IngestionRun(Base):
    __tablename__ = "ingestion_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(40))
    params: Mapped[str | None] = mapped_column(String(200))
    started_at: Mapped[datetime] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(20))
    matches_loaded: Mapped[int] = mapped_column(Integer, default=0)
    matches_skipped: Mapped[int] = mapped_column(Integer, default=0)
    matches_failed: Mapped[int] = mapped_column(Integer, default=0)
    events_loaded: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[str | None] = mapped_column(Text)
