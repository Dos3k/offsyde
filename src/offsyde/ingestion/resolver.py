"""Entity resolution: source IDs -> internal IDs, via the ``source_ids`` mapping table.

The same real-world player has different IDs (and name spellings) in every source.
Internal IDs are our own; the mapping table is what makes a second source possible.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Player, SourceId, Team
from .canonical import PlayerRef, TeamRef


class EntityResolver:
    def __init__(self, session: Session, source: str):
        self.session = session
        self.source = source
        self._cache: dict[tuple[str, str], int] = {
            (r.entity_type, r.source_id): r.internal_id
            for r in session.execute(select(SourceId).where(SourceId.source == source)).scalars()
        }

    def team(self, ref: TeamRef) -> int:
        key = ("team", ref.source_id)
        if key not in self._cache:
            row = Team(name=ref.name, country=ref.country)
            self.session.add(row)
            self.session.flush()
            self._register(key, row.id)
        return self._cache[key]

    def player(self, ref: PlayerRef) -> int:
        key = ("player", ref.source_id)
        if key not in self._cache:
            row = Player(name=ref.name, nickname=ref.nickname, country=ref.country)
            self.session.add(row)
            self.session.flush()
            self._register(key, row.id)
        elif ref.nickname or ref.country:
            # Lineups carry richer data than events: fill gaps, never overwrite.
            row = self.session.get(Player, self._cache[key])
            if row is not None:
                row.nickname = row.nickname or ref.nickname
                row.country = row.country or ref.country
        return self._cache[key]

    def _register(self, key: tuple[str, str], internal_id: int) -> None:
        self.session.add(
            SourceId(
                entity_type=key[0], source=self.source, source_id=key[1], internal_id=internal_id
            )
        )
        self._cache[key] = internal_id
