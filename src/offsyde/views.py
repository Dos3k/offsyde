"""Analytical views on top of the canonical tables.

Views give the text-to-SQL model simple, pre-joined, pre-derived building blocks
(one row per team per match, one row per shot, ...), which removes whole classes of
mistakes such as joining the teams table twice or forgetting to exclude shootouts.
"""

from __future__ import annotations

# Pitch coordinates follow the StatsBomb convention: 120 x 80, attacking left to right,
# so the opponent's goal centre is at (120, 40).
_DIST_SQ_START = "((120.0 - e.x) * (120.0 - e.x) + (40.0 - e.y) * (40.0 - e.y))"
_DIST_SQ_END = "((120.0 - e.end_x) * (120.0 - e.end_x) + (40.0 - e.end_y) * (40.0 - e.end_y))"

VIEWS: dict[str, str] = {
    "team_matches": """
SELECT m.id AS match_id, m.competition_name, m.season_name, m.match_date, m.match_week,
       m.home_team_id AS team_id, t.name AS team_name,
       m.away_team_id AS opponent_id, o.name AS opponent_name,
       1 AS is_home, m.home_score AS goals_for, m.away_score AS goals_against,
       CASE WHEN m.home_score > m.away_score THEN 'W'
            WHEN m.home_score = m.away_score THEN 'D' ELSE 'L' END AS result,
       CASE WHEN m.home_score > m.away_score THEN 3
            WHEN m.home_score = m.away_score THEN 1 ELSE 0 END AS points
FROM matches m
JOIN teams t ON t.id = m.home_team_id
JOIN teams o ON o.id = m.away_team_id
UNION ALL
SELECT m.id, m.competition_name, m.season_name, m.match_date, m.match_week,
       m.away_team_id, t.name,
       m.home_team_id, o.name,
       0, m.away_score, m.home_score,
       CASE WHEN m.away_score > m.home_score THEN 'W'
            WHEN m.away_score = m.home_score THEN 'D' ELSE 'L' END,
       CASE WHEN m.away_score > m.home_score THEN 3
            WHEN m.away_score = m.home_score THEN 1 ELSE 0 END
FROM matches m
JOIN teams t ON t.id = m.away_team_id
JOIN teams o ON o.id = m.home_team_id
""",
    "shots": """
SELECT e.id, e.match_id, e.period, e.minute, e.team_id, e.player_id,
       e.x, e.y, e.xg, e.outcome, e.sub_type AS shot_type, e.body_part,
       CASE WHEN e.outcome = 'Goal' THEN 1 ELSE 0 END AS is_goal,
       CASE WHEN e.outcome IN ('Goal', 'Saved', 'Saved to Post') THEN 1 ELSE 0 END AS is_on_target,
       m.competition_name, m.season_name
FROM events e
JOIN matches m ON m.id = e.match_id
WHERE e.type = 'Shot'
""",
    "passes": f"""
SELECT e.id, e.match_id, e.period, e.minute, e.team_id,
       e.player_id AS passer_id, e.recipient_id,
       e.x, e.y, e.end_x, e.end_y, e.pass_length AS length,
       e.outcome, e.sub_type AS pass_type, e.body_part,
       CASE WHEN e.outcome = 'Complete' THEN 1 ELSE 0 END AS is_complete,
       e.is_goal_assist, e.is_shot_assist,
       CASE WHEN e.outcome = 'Complete' AND e.x IS NOT NULL AND e.end_x IS NOT NULL
                 AND e.end_x > e.x AND {_DIST_SQ_END} <= 0.5625 * {_DIST_SQ_START}
            THEN 1 ELSE 0 END AS is_progressive,
       m.competition_name, m.season_name
FROM events e
JOIN matches m ON m.id = e.match_id
WHERE e.type = 'Pass'
""",
}
