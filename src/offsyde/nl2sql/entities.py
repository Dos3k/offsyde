"""Entity linking: find players and teams mentioned in a question.

Football names are a mess for LLMs: users write "Mueller", "Müller" or "Muller", "Bayern",
"Bayern München" or "FC Bayern", and the database holds 'Bayern Munich'. We resolve mentions
deterministically (normalised exact match, then fuzzy match) and hand the exact database
values to the model as hints, instead of hoping it guesses the spelling.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from rapidfuzz import fuzz, process
from sqlalchemy import Engine, text

MAX_AMBIGUITY = 3
AMBIGUOUS = -1  # entity id marking 'several players share this name'
DOMINANCE = 3  # a player with this many times more events than the runner-up wins the tie

# Words that look like names but are not (English + German + football vocabulary).
STOPWORDS = frozenset(
    """
    the and who which what when where how many much most more least did does have has had was were
    with from for per all any each their his her this that than into over under after before
    season match matches game games team teams player players goal goals shot shots pass passes
    score scored scorer scorers total average avg top first last home away win wins won lost
    draw draws points point assists assist minutes minute yellow red card cards expected against
    wie viele wer welche welcher welches wann wo hat haben der die das den dem und mit von fur
    für pro alle jede jeder meisten meiste tore tor spiel spiele spieler saison punkte vorlagen
    schusse schüsse gegen heim auswarts auswärts gewonnen verloren unentschieden insgesamt
    bundesliga
    """.split()
)

# Tokens too generic to identify a team on their own.
GENERIC_TEAM_TOKENS = frozenset(
    {
        "borussia",
        "fc",
        "sv",
        "vfl",
        "vfb",
        "tsg",
        "fsv",
        "sc",
        "1899",
        "04",
        "05",
        "98",
        "1846",
        "eintracht",
        "union",
        "werder",
        "1.",
    }
)

# Common alternative names -> substring of the stored team name.
TEAM_NICKNAMES = {
    "bayern munchen": "Bayern",
    "bayern munich": "Bayern",
    "fc bayern": "Bayern",
    "bvb": "Dortmund",
    "gladbach": "Gladbach",
    "monchengladbach": "Gladbach",
    "mgladbach": "Gladbach",
    "koln": "Köln",
    "cologne": "Köln",
    "werkself": "Leverkusen",
    "bayer 04": "Leverkusen",
    "rbl": "Leipzig",
    "eintracht frankfurt": "Frankfurt",
    "hertha": "Hertha",
    "stuttgart": "Stuttgart",
    "wolfsburg": "Wolfsburg",
    "mainz": "Mainz",
    "barca": "Barcelona",
    "fc barcelona": "Barcelona",
    "blaugrana": "Barcelona",
    "real": "Real Madrid",
    "atleti": "Atlético",
}


def normalise(s: str) -> str:
    """Case-fold, strip accents, and collapse German transliterations (ü/ue/u -> u)."""
    s = s.casefold().replace("ß", "ss")
    for src, dst in (("ä", "a"), ("ö", "o"), ("ü", "u"), ("ae", "a"), ("oe", "o"), ("ue", "u")):
        s = s.replace(src, dst)
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9 ]+", " ", s).strip()


@dataclass(frozen=True)
class EntityMatch:
    kind: str  # "player" | "team"
    mention: str
    id: int
    name: str
    score: float  # 100 = exact


class EntityLinker:
    def __init__(self, engine: Engine):
        self._aliases: dict[str, list[tuple[str, int, str]]] = {}
        self._activity: dict[int, int] = {}
        self._load(engine)
        self._keys = list(self._aliases)

    # -- index ---------------------------------------------------------------------
    def _add(self, alias: str, kind: str, ident: int, name: str) -> None:
        key = normalise(alias)
        if len(key) < 3:
            return
        entry = (kind, ident, name)
        bucket = self._aliases.setdefault(key, [])
        if entry not in bucket:
            bucket.append(entry)

    def _load(self, engine: Engine) -> None:
        with engine.connect() as conn:
            teams = conn.execute(text("SELECT id, name FROM teams")).all()
            players = conn.execute(text("SELECT id, name, nickname FROM players")).all()
            self._activity = {
                pid: n
                for pid, n in conn.execute(
                    text(
                        "SELECT player_id, COUNT(*) FROM events WHERE player_id IS NOT NULL "
                        "GROUP BY player_id"
                    )
                )
            }

        for tid, name in teams:
            self._add(name, "team", tid, name)
            for tok in normalise(name).split():
                if len(tok) >= 4 and tok not in GENERIC_TEAM_TOKENS:
                    self._add(tok, "team", tid, name)
        for alias, needle in TEAM_NICKNAMES.items():
            for tid, name in teams:
                if needle.casefold() in name.casefold():
                    self._add(alias, "team", tid, name)

        for pid, name, nickname in players:
            for full in filter(None, (name, nickname)):
                self._add(full, "player", pid, name)
                for tok in normalise(full).split():
                    if len(tok) >= 4:
                        self._add(tok, "player", pid, name)

    # -- linking -------------------------------------------------------------------
    def link(self, question: str) -> list[EntityMatch]:
        raw_tokens = re.findall(r"[\w'’.-]+", question)
        tokens = [normalise(t) for t in raw_tokens]
        used = [False] * len(tokens)
        found: list[EntityMatch] = []

        # Pass 1: exact alias matches, longest phrase first. Pass 2: fuzzy, leftovers only.
        for fuzzy, sizes in ((False, (3, 2, 1)), (True, (2, 1))):
            for n in sizes:
                for i in range(len(tokens) - n + 1):
                    if any(used[i : i + n]):
                        continue
                    words = tokens[i : i + n]
                    if any(not w for w in words) or (n == 1 and words[0] in STOPWORDS):
                        continue
                    capitalised = all(t[0].isupper() for t in raw_tokens[i : i + n])
                    phrase = " ".join(words)
                    mention = " ".join(raw_tokens[i : i + n])
                    matches = (
                        self._fuzzy(phrase, mention)
                        if fuzzy and capitalised
                        else []
                        if fuzzy
                        else self._exact(phrase, mention, capitalised)
                    )
                    if matches:
                        found.extend(matches)
                        for j in range(i, i + n):
                            used[j] = True

        best: dict[tuple[str, int, str], EntityMatch] = {}
        for m in found:
            key = (m.kind, m.id, m.mention if m.id < 0 else "")
            if key not in best or m.score > best[key].score:
                best[key] = m
        return sorted(best.values(), key=lambda m: (-m.score, m.kind, m.name))

    def _exact(self, phrase: str, mention: str, capitalised: bool) -> list[EntityMatch]:
        entries = self._aliases.get(phrase)
        if not entries:
            return []
        players = [e for e in entries if e[0] == "player"]
        teams = [e for e in entries if e[0] == "team"]
        out = [EntityMatch("team", mention, i, n, 100.0) for _, i, n in teams[:MAX_AMBIGUITY]]
        if len(players) > MAX_AMBIGUITY:
            # e.g. "Suárez": many players share it. Rank by how much they appear in the data.
            if capitalised:
                ranked = sorted(players, key=lambda e: -self._activity.get(e[1], 0))
                top = self._activity.get(ranked[0][1], 0)
                second = self._activity.get(ranked[1][1], 0)
                if top > 0 and top >= DOMINANCE * second:
                    out.append(EntityMatch("player", mention, ranked[0][1], ranked[0][2], 90.0))
                else:
                    names = [n for _, _, n in ranked[:6]]
                    out.append(EntityMatch("player", mention, AMBIGUOUS, "; ".join(names), 50.0))
        else:
            out += [EntityMatch("player", mention, i, n, 100.0) for _, i, n in players]
        return out

    def _fuzzy(self, phrase: str, mention: str) -> list[EntityMatch]:
        if len(phrase) < 5 or phrase in STOPWORDS:
            return []
        hit = process.extractOne(phrase, self._keys, scorer=fuzz.ratio, score_cutoff=86)
        if hit is None:
            return []
        key, score, _ = hit
        entries = self._aliases[key]
        if len({e[1] for e in entries if e[0] == "player"}) > MAX_AMBIGUITY:
            return []
        return [EntityMatch(k, mention, i, n, float(score)) for k, i, n in entries][:MAX_AMBIGUITY]


def format_hints(matches: list[EntityMatch]) -> str:
    if not matches:
        return "(none)"
    lines = []
    for m in matches:
        if m.id == AMBIGUOUS:
            lines.append(
                f"- '{m.mention}' is ambiguous. Candidates (most active first): {m.name}. "
                "Match with players.name LIKE '%...%' using the spelling above."
            )
            continue
        table = "players" if m.kind == "player" else "teams"
        lines.append(f"- '{m.mention}' -> {table}.id = {m.id}  ({m.name})")
    return "\n".join(lines)
