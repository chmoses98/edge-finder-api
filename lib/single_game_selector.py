"""
lib/single_game_selector.py
===============================
Resolve a HUMAN-FRIENDLY game selector ("Yankees vs Red Sox", "NYY@BOS",
"Yanks", "Sox" -> refused as ambiguous) to exactly ONE MLB gamePk on a
given date, or refuse and show the candidates.

Pure: no network, no filesystem. The caller supplies the already-fetched
schedule rows (lib.edgelab.mlb_schedule.parse_schedule_games' output --
the repository's one canonical MLB schedule adapter; this module
deliberately does not add a second one).

FAIL CLOSED IS THE WHOLE POINT
------------------------------
A doubleheader is two different baseball games with two different
starting pitchers, two different lineups, two different Kalshi market
sets and two different results. "Yankees vs Red Sox" on a doubleheader
day does not identify a game, and silently taking the first one would
hand back a self-consistent, confidently-wrong artifact that a human
would then handicap and bet. Every failure mode here -- no match, an
ambiguous nickname, a doubleheader, a team playing nobody that day --
returns NO game plus the full candidate list (each with its gamePk, so
the caller can re-run with an exact `--game-pk`), never a best guess.

Identity comes from MLB's own permanent numeric team ids via
lib.edgelab.mlb_schedule.TEAM_ID_TO_ABBR (reused, never re-tabulated).
The alias table below only ever maps human text -> that same
abbreviation set; it can never invent a team the schedule does not
contain.
"""
import re
import unicodedata

from lib.edgelab.mlb_schedule import TEAM_ID_TO_ABBR
from lib.kalshi_price_check import TEAM_DISPLAY_NAMES

# Full club names and home cities/regions, keyed by the SAME abbreviation
# convention TEAM_ID_TO_ABBR uses (AZ, ATH, CWS, WSH, ...). Nicknames come
# from lib.kalshi_price_check.TEAM_DISPLAY_NAMES and are not repeated here.
_CITY_AND_FULL_NAMES = {
    "AZ": ["arizona", "arizona diamondbacks", "dbacks", "d-backs", "diamond backs"],
    "ATL": ["atlanta", "atlanta braves"],
    "BAL": ["baltimore", "baltimore orioles", "o's", "os"],
    "BOS": ["boston", "boston red sox", "redsox"],
    "CHC": ["chicago cubs", "cubbies", "north siders"],
    "CWS": ["chicago white sox", "whitesox", "south siders", "chisox"],
    "CIN": ["cincinnati", "cincinnati reds"],
    "CLE": ["cleveland", "cleveland guardians"],
    "COL": ["colorado", "colorado rockies"],
    "DET": ["detroit", "detroit tigers"],
    "HOU": ["houston", "houston astros", "stros"],
    "KC": ["kansas city", "kansas city royals"],
    "LAA": ["los angeles angels", "anaheim", "angels of anaheim"],
    "LAD": ["los angeles dodgers", "la dodgers"],
    "MIA": ["miami", "miami marlins"],
    "MIL": ["milwaukee", "milwaukee brewers", "brew crew"],
    "MIN": ["minnesota", "minnesota twins"],
    "NYM": ["new york mets", "ny mets"],
    "NYY": ["new york yankees", "ny yankees", "yanks", "bronx bombers"],
    "ATH": ["athletics", "oakland", "oakland athletics", "a's", "as", "sacramento athletics"],
    "PHI": ["philadelphia", "philadelphia phillies", "phils"],
    "PIT": ["pittsburgh", "pittsburgh pirates", "bucs"],
    "SD": ["san diego", "san diego padres", "pads"],
    "SF": ["san francisco", "san francisco giants"],
    "SEA": ["seattle", "seattle mariners", "m's", "ms"],
    "STL": ["st louis", "st. louis", "saint louis", "st louis cardinals", "cards"],
    "TB": ["tampa bay", "tampa", "tampa bay rays"],
    "TEX": ["texas", "texas rangers"],
    "TOR": ["toronto", "toronto blue jays", "jays"],
    "WSH": ["washington", "washington nationals", "nats"],
}

# Abbreviations other sources use for the same club. Deliberately maps
# ONTO this repository's convention rather than introducing a second one
# (see scripts/enrich_data.py's ABBR_NORMALIZE, which solves the same
# problem for the teamstats feed).
_ALTERNATE_ABBRS = {
    "ARI": "AZ", "OAK": "ATH", "CHW": "CWS", "CHA": "CWS", "CHN": "CHC",
    "WAS": "WSH", "KCR": "KC", "SDP": "SD", "SFG": "SF", "TBR": "TB",
    "NYA": "NYY", "NYN": "NYM", "LAN": "LAD", "ANA": "LAA", "SLN": "STL",
}

# Tokens that separate the two sides of a matchup ("A vs B", "A @ B",
# "A at B", "A/B", "A-B"). Split on these before resolving each side.
_MATCHUP_SPLIT = re.compile(r"\s*(?:@|\bat\b|\bvs\.?\b|\bversus\b|/|\||,)\s*", re.IGNORECASE)

REASON_NO_SELECTOR = "NO_SELECTOR_GIVEN"
REASON_UNKNOWN_TEAM = "UNKNOWN_TEAM_TOKEN"
REASON_AMBIGUOUS_TEAM = "AMBIGUOUS_TEAM_TOKEN"
REASON_NO_GAME = "NO_MATCHING_GAME"
REASON_AMBIGUOUS_GAME = "AMBIGUOUS_GAME_SELECTOR"
REASON_UNKNOWN_GAME_PK = "GAME_PK_NOT_ON_THIS_DATE"


def _normalize(text):
    """Pure. Casefold, strip accents and punctuation, collapse spaces."""
    if text is None:
        return ""
    decomposed = unicodedata.normalize("NFKD", str(text))
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    cleaned = re.sub(r"[^a-z0-9@/|, ]+", " ", stripped.lower())
    return re.sub(r"\s+", " ", cleaned).strip()


def build_alias_index():
    """
    Pure. {normalized alias: {abbr, ...}}. A value with more than one
    abbr is a genuinely AMBIGUOUS alias (e.g. "chicago", "sox", "new
    york", "la") and resolve_team_token refuses it rather than choosing.
    """
    index = {}

    def add(alias, abbr):
        key = _normalize(alias)
        if key:
            index.setdefault(key, set()).add(abbr)

    for abbr in TEAM_ID_TO_ABBR.values():
        add(abbr, abbr)
    for abbr, nickname in TEAM_DISPLAY_NAMES.items():
        add(nickname, abbr)
    for abbr, names in _CITY_AND_FULL_NAMES.items():
        for name in names:
            add(name, abbr)
    for alt, abbr in _ALTERNATE_ABBRS.items():
        add(alt, abbr)

    # Deliberately ambiguous by construction, so they can never silently
    # resolve: two clubs share each of these.
    for alias, abbrs in (("chicago", ("CHC", "CWS")), ("new york", ("NYY", "NYM")),
                         ("ny", ("NYY", "NYM")), ("sox", ("BOS", "CWS")),
                         ("los angeles", ("LAD", "LAA")), ("la", ("LAD", "LAA"))):
        for abbr in abbrs:
            add(alias, abbr)
    return index


ALIAS_INDEX = build_alias_index()


def resolve_team_token(token, alias_index=None):
    """
    Pure. (abbr, reason). abbr is None whenever the token cannot be
    resolved to exactly one club; reason says which failure it was and
    (for ambiguity) which clubs it could have meant.
    """
    index = alias_index if alias_index is not None else ALIAS_INDEX
    key = _normalize(token)
    if not key:
        return None, f"{REASON_UNKNOWN_TEAM}: empty team token"
    matches = index.get(key)
    if not matches:
        return None, f"{REASON_UNKNOWN_TEAM}: {token!r} does not match any MLB club name or abbreviation"
    if len(matches) > 1:
        return None, (
            f"{REASON_AMBIGUOUS_TEAM}: {token!r} could mean {sorted(matches)} -- "
            f"use a full club name or an exact abbreviation"
        )
    return next(iter(matches)), None


def parse_selector(selector, alias_index=None):
    """
    Pure. Turn free text into the set of clubs it names.
    Returns (abbrs, reason): abbrs is a list of 1 or 2 abbreviations
    (order NOT treated as away/home -- "Yankees vs Red Sox" and
    "Red Sox vs Yankees" must select the same game), reason is a string
    when nothing usable could be parsed.
    """
    text = _normalize(selector)
    if not text:
        return [], f"{REASON_NO_SELECTOR}: no game/team selector supplied"

    parts = [p for p in _MATCHUP_SPLIT.split(text) if p.strip()]
    if len(parts) == 1:
        # A bare team name. Try the whole string first so multi-word
        # names ("red sox") work; only then give up.
        abbr, reason = resolve_team_token(parts[0], alias_index)
        if abbr is None:
            return [], reason
        return [abbr], None

    if len(parts) > 2:
        return [], (
            f"{REASON_AMBIGUOUS_GAME}: {selector!r} names {len(parts)} sides; "
            f"a single game has exactly two"
        )

    abbrs = []
    for part in parts:
        abbr, reason = resolve_team_token(part, alias_index)
        if abbr is None:
            return [], reason
        abbrs.append(abbr)
    if abbrs[0] == abbrs[1]:
        return [], f"{REASON_AMBIGUOUS_GAME}: both sides of {selector!r} resolve to {abbrs[0]}"
    return abbrs, None


def describe_candidate(game):
    """Pure. One human-readable line per candidate game -- this is what a
    refused, ambiguous selection prints so the operator can immediately
    re-run with an exact gamePk."""
    away = TEAM_ID_TO_ABBR.get(game.get("awayTeamId"), f"team{game.get('awayTeamId')}")
    home = TEAM_ID_TO_ABBR.get(game.get("homeTeamId"), f"team{game.get('homeTeamId')}")
    leg = game.get("gameNumber")
    leg_note = f" (doubleheader game {leg})" if leg and leg > 1 else ""
    return (
        f"gamePk={game.get('gamePk')} {away}@{home}{leg_note} "
        f"start={game.get('scheduledStart')} status={game.get('status')} venue={game.get('venue')}"
    )


def annotate_game(game):
    """Pure. The schedule row plus this repo's abbreviation convention
    and a stable matchup string (AWAY@HOME, the exact shape
    lib.kalshi_price_check's `games` filter matches on)."""
    away = TEAM_ID_TO_ABBR.get(game.get("awayTeamId"))
    home = TEAM_ID_TO_ABBR.get(game.get("homeTeamId"))
    return {
        **game,
        "awayTeam": away,
        "homeTeam": home,
        "matchup": f"{away}@{home}" if away and home else None,
        "isDoubleheaderLeg": bool(game.get("gameNumber") and game["gameNumber"] > 1),
    }


def resolve_single_game(schedule_games, *, selector=None, game_pk=None):
    """
    Pure. THE resolver.

    Returns (game_or_None, candidates, reason):
      * game       -- annotate_game()'d schedule row, only when exactly
                      one game matched.
      * candidates -- every game the selector could have meant (always
                      populated on a refusal, so the caller can print
                      gamePks; empty only when nothing matched at all).
      * reason     -- None on success; otherwise a specific, machine-
                      greppable reason code plus an explanation.

    `game_pk` wins outright when supplied: it IS the exact identity, and
    is the documented escape hatch for a doubleheader. It is still
    validated against the date's schedule -- a gamePk that is not
    playing that day is refused, never trusted blindly.
    """
    annotated = [annotate_game(g) for g in (schedule_games or [])]

    if game_pk:
        wanted = str(game_pk)
        exact = [g for g in annotated if str(g.get("gamePk")) == wanted]
        if len(exact) == 1:
            return exact[0], exact, None
        return None, annotated, (
            f"{REASON_UNKNOWN_GAME_PK}: gamePk={game_pk} is not on this date's schedule "
            f"({len(annotated)} games listed)"
        )

    abbrs, reason = parse_selector(selector)
    if reason:
        return None, annotated, reason

    wanted = set(abbrs)
    candidates = [
        g for g in annotated
        if g["awayTeam"] and g["homeTeam"] and wanted <= {g["awayTeam"], g["homeTeam"]}
    ]

    if not candidates:
        return None, [], (
            f"{REASON_NO_GAME}: no scheduled game on this date involves {sorted(wanted)}"
        )
    if len(candidates) > 1:
        legs = [c.get("gameNumber") for c in candidates]
        kind = "doubleheader" if len([leg for leg in legs if leg]) == len(candidates) and len(set(legs)) > 1 \
            else "multiple games"
        return None, candidates, (
            f"{REASON_AMBIGUOUS_GAME}: {sorted(wanted)} matches {len(candidates)} games "
            f"on this date ({kind}) -- re-run with an exact gamePk"
        )
    return candidates[0], candidates, None
