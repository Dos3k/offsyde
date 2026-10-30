"""The schema as the LLM sees it: only whitelisted tables, with column semantics.

A test checks this document against the real database, so it cannot silently drift.
"""

from __future__ import annotations

# table -> (description, [(column, type, description)])
SCHEMA: dict[str, tuple[str, list[tuple[str, str, str]]]] = {
    "teams": (
        "One row per team.",
        [
            ("id", "INTEGER", "primary key"),
            ("name", "TEXT", "e.g. 'Bayer Leverkusen', 'Bayern Munich'"),
            ("country", "TEXT", ""),
        ],
    ),
    "players": (
        "One row per player. `name` is often the full legal name (e.g. 'Victor Okoh Boniface'); "
        "`nickname` is the common name (e.g. 'Victor Boniface') and may be NULL. "
        "Prefer filtering by id, or match both name and nickname with LIKE.",
        [
            ("id", "INTEGER", "primary key"),
            ("name", "TEXT", "full legal name"),
            ("nickname", "TEXT", "common name, may be NULL"),
            ("country", "TEXT", "nationality"),
        ],
    ),
    "matches": (
        "One row per match, with the final score (excluding penalty shootouts).",
        [
            ("id", "INTEGER", "primary key"),
            ("competition_name", "TEXT", "e.g. '1. Bundesliga'"),
            ("season_name", "TEXT", "e.g. '2023/2024'"),
            ("match_date", "DATE", "'YYYY-MM-DD'"),
            ("match_week", "INTEGER", "matchday number"),
            ("stage", "TEXT", "e.g. 'Regular Season'"),
            ("home_team_id", "INTEGER", "-> teams.id"),
            ("away_team_id", "INTEGER", "-> teams.id"),
            ("home_score", "INTEGER", ""),
            ("away_score", "INTEGER", ""),
            ("stadium", "TEXT", ""),
            ("referee", "TEXT", ""),
            ("home_manager", "TEXT", ""),
            ("away_manager", "TEXT", ""),
        ],
    ),
    "team_matches": (
        "VIEW: one row per team per match (every match appears twice, once per team). "
        "Best starting point for results, goals scored/conceded, wins and league points.",
        [
            ("match_id", "INTEGER", "-> matches.id"),
            ("competition_name", "TEXT", ""),
            ("season_name", "TEXT", ""),
            ("match_date", "DATE", ""),
            ("match_week", "INTEGER", ""),
            ("team_id", "INTEGER", "-> teams.id"),
            ("team_name", "TEXT", ""),
            ("opponent_id", "INTEGER", "-> teams.id"),
            ("opponent_name", "TEXT", ""),
            ("is_home", "INTEGER", "1 = home, 0 = away"),
            ("goals_for", "INTEGER", ""),
            ("goals_against", "INTEGER", ""),
            ("result", "TEXT", "'W', 'D' or 'L' for team_id"),
            ("points", "INTEGER", "league points: 3 / 1 / 0"),
        ],
    ),
    "lineups": (
        "One row per player per match they were in the squad for.",
        [
            ("match_id", "INTEGER", "-> matches.id"),
            ("player_id", "INTEGER", "-> players.id"),
            ("team_id", "INTEGER", "-> teams.id"),
            ("jersey_number", "INTEGER", ""),
            ("started", "INTEGER", "1 if in the starting XI, else 0 (bench)"),
        ],
    ),
    "events": (
        "One row per on-pitch event (~4,000 per match). Prefer the `shots` and `passes` "
        "views for those event types. Pitch is 120 x 80, teams attack left to right, "
        "so the opponent's goal is at x = 120.",
        [
            ("id", "INTEGER", "primary key"),
            ("match_id", "INTEGER", "-> matches.id"),
            ("seq", "INTEGER", "order of the event within the match"),
            ("period", "INTEGER", "1, 2 = halves; 3, 4 = extra time; 5 = penalty shootout"),
            ("minute", "INTEGER", ""),
            ("second", "INTEGER", ""),
            (
                "type",
                "TEXT",
                "Pass, Shot, Carry, Pressure, Ball Recovery, Duel, Block, Clearance, "
                "Dribble, Interception, Foul Committed, Foul Won, Goal Keeper, ...",
            ),
            ("team_id", "INTEGER", "team performing the event -> teams.id"),
            ("player_id", "INTEGER", "player performing the event -> players.id"),
            ("position_name", "TEXT", "e.g. 'Center Forward'"),
            ("x", "REAL", "start x, 0-120"),
            ("y", "REAL", "start y, 0-80"),
            ("end_x", "REAL", "end x for passes, carries, shots"),
            ("end_y", "REAL", ""),
            ("outcome", "TEXT", "result of the event; meaning depends on type"),
            ("sub_type", "TEXT", "e.g. pass type, shot type, duel type ('Tackle')"),
            ("body_part", "TEXT", "'Right Foot', 'Left Foot', 'Head', ..."),
            ("xg", "REAL", "expected goals value, only for shots"),
            ("pass_length", "REAL", "only for passes"),
            ("recipient_id", "INTEGER", "pass recipient -> players.id"),
            ("play_pattern", "TEXT", "'Regular Play', 'From Corner', ..."),
            ("card", "TEXT", "'Yellow Card', 'Second Yellow', 'Red Card' on foul events"),
            ("under_pressure", "INTEGER", "1 if the player was pressured"),
            ("is_goal_assist", "INTEGER", "1 on the pass that led directly to a goal"),
            ("is_shot_assist", "INTEGER", "1 on the pass that led directly to a shot"),
        ],
    ),
    "shots": (
        "VIEW over events where type = 'Shot'. Use for goals, xG, shots on target.",
        [
            ("id", "INTEGER", "event id"),
            ("match_id", "INTEGER", "-> matches.id"),
            ("period", "INTEGER", "period 5 = penalty shootout (exclude when counting goals)"),
            ("minute", "INTEGER", ""),
            ("team_id", "INTEGER", "-> teams.id"),
            ("player_id", "INTEGER", "shooter -> players.id"),
            ("x", "REAL", ""),
            ("y", "REAL", ""),
            ("xg", "REAL", "expected goals of the shot"),
            (
                "outcome",
                "TEXT",
                "Goal, Saved, Blocked, Off T, Post, Wayward, Saved Off Target, ...",
            ),
            ("shot_type", "TEXT", "Open Play, Free Kick, Penalty, Corner, Kick Off"),
            ("body_part", "TEXT", ""),
            ("is_goal", "INTEGER", "1 if the shot was a goal"),
            ("is_on_target", "INTEGER", "1 if goal or saved"),
            ("competition_name", "TEXT", "competition of the match"),
            ("season_name", "TEXT", "e.g. '2019/2020'"),
        ],
    ),
    "passes": (
        "VIEW over events where type = 'Pass'. `is_progressive` is a simplified definition: "
        "a completed pass that ends at least 25% closer to the opponent's goal.",
        [
            ("id", "INTEGER", "event id"),
            ("match_id", "INTEGER", "-> matches.id"),
            ("period", "INTEGER", ""),
            ("minute", "INTEGER", ""),
            ("team_id", "INTEGER", "-> teams.id"),
            ("passer_id", "INTEGER", "-> players.id"),
            ("recipient_id", "INTEGER", "-> players.id, NULL if incomplete"),
            ("x", "REAL", ""),
            ("y", "REAL", ""),
            ("end_x", "REAL", ""),
            ("end_y", "REAL", ""),
            ("length", "REAL", "pass length"),
            (
                "outcome",
                "TEXT",
                "Complete, Incomplete, Out, Pass Offside, Unknown, Injury Clearance",
            ),
            (
                "pass_type",
                "TEXT",
                "Corner, Free Kick, Throw-in, Goal Kick, Kick Off, Recovery, ...",
            ),
            ("body_part", "TEXT", ""),
            ("is_complete", "INTEGER", "1 if outcome = 'Complete'"),
            ("is_goal_assist", "INTEGER", ""),
            ("is_shot_assist", "INTEGER", ""),
            ("is_progressive", "INTEGER", "1 if a progressive completed pass"),
            ("competition_name", "TEXT", "competition of the match"),
            ("season_name", "TEXT", "e.g. '2019/2020'"),
        ],
    ),
}

ALLOWED_TABLES: frozenset[str] = frozenset(SCHEMA)

DOMAIN_NOTES = """\
- Goals scored by a team = rows in `shots` with is_goal = 1 and period < 5, plus events with \
type = 'Own Goal For' for that team. `matches.home_score` / `away_score` already include \
everything; for team totals prefer `team_matches`.
- To get a player's name, JOIN players ON players.id = <events/shots/passes player column>.
- Several seasons may be loaded. When the question names a season or year, filter on \
`season_name` (available on `matches`, `team_matches`, `shots`, `passes`; for `events` JOIN \
matches). Season names look like '2018/2019'; a bare year such as 2019 can mean '2018/2019' or \
'2019/2020', so use season_name LIKE '%2019%' unless the question is clearer. \
If no season is named, use all data.
- When the question names a team or player, use the exact values from the entity hints.
- Rates and averages: multiply by 1.0 to avoid integer division, and ROUND(..., 2).
- Matches that have not been loaded simply do not exist; do not invent rows. Seasons can be \
incomplete (see Loaded data), so totals cover the loaded matches only.\
"""


def render_schema() -> str:
    lines: list[str] = []
    for table, (desc, cols) in SCHEMA.items():
        lines.append(f"TABLE {table}  -- {desc}")
        for name, typ, cdesc in cols:
            suffix = f"  -- {cdesc}" if cdesc else ""
            lines.append(f"  {name} {typ}{suffix}")
        lines.append("")
    return "\n".join(lines).rstrip()
