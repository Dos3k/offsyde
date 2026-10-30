"""Post-load data-quality checks. Each returns human-readable issues; empty list = healthy."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Engine, text


@dataclass(frozen=True)
class Issue:
    check: str
    detail: str

    def __str__(self) -> str:
        return f"[{self.check}] {self.detail}"


def run_quality_checks(engine: Engine) -> list[Issue]:
    issues: list[Issue] = []
    with engine.connect() as conn:
        # 1. Every fully loaded match has events from exactly two distinct teams.
        rows = conn.execute(
            text(
                """
                SELECT m.id, COUNT(DISTINCT e.team_id) AS n
                FROM matches m LEFT JOIN events e ON e.match_id = m.id
                WHERE m.events_loaded = 1
                GROUP BY m.id HAVING COUNT(DISTINCT e.team_id) <> 2
                """
            )
        ).all()
        issues += [Issue("two_teams", f"match {r.id} has events from {r.n} teams") for r in rows]

        # 2. Goals reconstructed from events equal the recorded final score.
        #    Shootout penalties (period 5) are not part of the score; own goals credit the
        #    opponent via an 'Own Goal For' event.
        rows = conn.execute(
            text(
                """
                WITH goals AS (
                    SELECT match_id, team_id, COUNT(*) AS g FROM events
                    WHERE (type = 'Shot' AND outcome = 'Goal' AND period < 5)
                       OR type = 'Own Goal For'
                    GROUP BY match_id, team_id
                )
                SELECT m.id, m.home_score, m.away_score,
                       COALESCE(h.g, 0) AS hg, COALESCE(a.g, 0) AS ag
                FROM matches m
                LEFT JOIN goals h ON h.match_id = m.id AND h.team_id = m.home_team_id
                LEFT JOIN goals a ON a.match_id = m.id AND a.team_id = m.away_team_id
                WHERE m.events_loaded = 1
                  AND (COALESCE(h.g, 0) <> m.home_score OR COALESCE(a.g, 0) <> m.away_score)
                """
            )
        ).all()
        issues += [
            Issue(
                "score_consistency",
                f"match {r.id}: score {r.home_score}-{r.away_score} but events give {r.hg}-{r.ag}",
            )
            for r in rows
        ]

        # 3. Plausible clock values.
        n = conn.execute(
            text("SELECT COUNT(*) FROM events WHERE minute < 0 OR minute > 130")
        ).scalar_one()
        if n:
            issues.append(Issue("minute_range", f"{n} events with minute outside 0-130"))

        # 4. Pitch coordinates inside the 120 x 80 pitch (1 yard tolerance: the provider's
        #    tracking overshoots the touchline by up to ~0.5 in real data).
        n = conn.execute(
            text("SELECT COUNT(*) FROM events WHERE x < -1 OR x > 121 OR y < -1 OR y > 81")
        ).scalar_one()
        if n:
            issues.append(Issue("coordinates", f"{n} events with coordinates outside the pitch"))

        # 5. Loaded matches must actually have events.
        rows = conn.execute(
            text(
                """
                SELECT m.id FROM matches m
                WHERE m.events_loaded = 1
                  AND NOT EXISTS (SELECT 1 FROM events e WHERE e.match_id = m.id)
                """
            )
        ).all()
        issues += [
            Issue("empty_match", f"match {r.id} marked loaded but has no events") for r in rows
        ]
    return issues
