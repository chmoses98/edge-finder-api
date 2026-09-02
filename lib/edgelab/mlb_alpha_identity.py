"""
lib/edgelab/mlb_alpha_identity.py
=================================
Exact Kalshi MLB event identity, including DOUBLEHEADERS.

RESEARCH INFRASTRUCTURE, not strategy. This module changes no trading
rule; it only resolves *which game* a Kalshi event ticker refers to.

WHY IT EXISTS. The MLB-ALPHA-0001 blind holdout conservatively excluded
four events -- 2026-08-29 BOS@NYY G1/G2 and AZ@SF G1/G2 -- because the
research parser's team group `[A-Z]+` rejects the digit in Kalshi's
`G1`/`G2` doubleheader marker. Refusing was correct (it never guessed),
but it silently dropped real games. This module resolves them exactly.

TICKER SHAPE
    <SERIES>-<YY><MON><DD><HHMM><AWAY><HOME>[G<n>]
e.g. KXMLBF5TOTAL-26AUG291915BOSNYYG2

Team abbreviations are variable length (2-3 chars) and concatenated with
no separator, so `BOSNYY` is split against this repo's canonical
abbreviation table (lib.edgelab.mlb_schedule.TEAM_ID_TO_ABBR). If a
concatenation admits more than one valid (away, home) split, or none, the
result is UNRESOLVED -- never a guess, and never an arbitrary pick of one
doubleheader game.

Times are US/Eastern in the ticker; the archive's whole reliable range is
EDT (UTC-4), which was validated at 0.0 min median error against the
`scheduledStart` field on 26,261 observations.
"""

import re
from datetime import datetime, timedelta

from lib.edgelab.mlb_schedule import TEAM_ID_TO_ABBR

_MON = {m: i + 1 for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
     "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"])}

ET_UTC_OFFSET_HOURS = 4

# <SERIES>-<YY><MON><DD><HHMM><TEAMS>[G<n>]
_EVENT_RE = re.compile(
    r"^(?P<series>[A-Z0-9]+)-(?P<yy>\d{2})(?P<mon>[A-Z]{3})(?P<dd>\d{2})"
    r"(?P<hhmm>\d{4})(?P<teams>[A-Z]+?)(?:G(?P<dh>\d))?$")

TEAM_ABBRS = frozenset(TEAM_ID_TO_ABBR.values())

STATUS_RESOLVED = "RESOLVED"
STATUS_UNRESOLVED = "UNRESOLVED"


def split_team_pair(blob):
    """Every (away, home) split of a concatenated abbreviation pair that is
    valid against the canonical table. Returns a list; callers must refuse
    unless it has exactly one element."""
    out = []
    for cut in range(2, len(blob) - 1):
        away, home = blob[:cut], blob[cut:]
        if away in TEAM_ABBRS and home in TEAM_ABBRS:
            out.append((away, home))
    return out


def parse_event_ticker(event_ticker):
    """
    Exact identity for a Kalshi MLB event ticker.

    Returns a dict always containing `status`; on RESOLVED it also carries
    seriesTicker, gameDate, scheduledStartUtc, awayTeam, homeTeam and
    doubleheaderGame (None, 1 or 2). Never raises, never guesses.
    """
    base = {"eventTicker": event_ticker, "status": STATUS_UNRESOLVED,
            "unresolvedReason": None, "doubleheaderGame": None}
    if not event_ticker or not isinstance(event_ticker, str):
        base["unresolvedReason"] = "missing_event_ticker"
        return base
    m = _EVENT_RE.match(event_ticker)
    if not m:
        base["unresolvedReason"] = "ticker_shape_unrecognized"
        return base

    mon = _MON.get(m.group("mon"))
    if mon is None:
        base["unresolvedReason"] = "month_token_unrecognized"
        return base
    try:
        local = datetime(2000 + int(m.group("yy")), mon, int(m.group("dd")),
                         int(m.group("hhmm")[:2]), int(m.group("hhmm")[2:]))
    except ValueError:
        base["unresolvedReason"] = "date_or_time_out_of_range"
        return base

    splits = split_team_pair(m.group("teams"))
    if not splits:
        base["unresolvedReason"] = "no_valid_team_split:%s" % m.group("teams")
        return base
    if len(splits) > 1:
        base["unresolvedReason"] = "ambiguous_team_split:%s->%s" % (
            m.group("teams"), sorted(splits))
        return base

    away, home = splits[0]
    dh = m.group("dh")
    return {
        "eventTicker": event_ticker,
        "status": STATUS_RESOLVED,
        "unresolvedReason": None,
        "seriesTicker": m.group("series"),
        "gameDate": "%04d-%02d-%02d" % (2000 + int(m.group("yy")), mon, int(m.group("dd"))),
        "scheduledStartUtc": local + timedelta(hours=ET_UTC_OFFSET_HOURS),
        "awayTeam": away,
        "homeTeam": home,
        "doubleheaderGame": int(dh) if dh else None,
    }


def resolve_game_pk(identity, schedule_games, start_tolerance_minutes=45):
    """
    Match a RESOLVED identity to exactly one MLB gamePk from
    lib.edgelab.mlb_schedule.parse_schedule_games output.

    Uses, in order: exact (away, home) abbreviations; then, when more than
    one candidate remains (a doubleheader), the ticker's G1/G2 marker
    against the feed's own `gameNumber`; then scheduled start time. Returns
    (gamePk, reason) and REFUSES with (None, reason) if any ambiguity
    survives -- one doubleheader game is never chosen arbitrarily.
    """
    if not identity or identity.get("status") != STATUS_RESOLVED:
        return None, "identity_unresolved"

    cands = [g for g in (schedule_games or [])
             if TEAM_ID_TO_ABBR.get(g.get("awayTeamId")) == identity["awayTeam"]
             and TEAM_ID_TO_ABBR.get(g.get("homeTeamId")) == identity["homeTeam"]]
    if not cands:
        return None, "no_schedule_match_for_%s_at_%s" % (
            identity["awayTeam"], identity["homeTeam"])
    if len(cands) == 1:
        return cands[0]["gamePk"], "unique_matchup"

    dh = identity.get("doubleheaderGame")
    if dh is not None:
        numbered = [g for g in cands if g.get("gameNumber") == dh]
        if len(numbered) == 1:
            return numbered[0]["gamePk"], "doubleheader_resolved_by_game_number"
        if len(numbered) > 1:
            return None, "ambiguous_game_number_%s" % dh

    start = identity.get("scheduledStartUtc")
    if start is not None:
        timed = []
        for g in cands:
            ss = g.get("scheduledStart")
            if not ss:
                continue
            try:
                sched = datetime.fromisoformat(ss.replace("Z", "+00:00")).replace(tzinfo=None)
            except ValueError:
                continue
            if abs((sched - start).total_seconds()) / 60.0 <= start_tolerance_minutes:
                timed.append(g)
        if len(timed) == 1:
            return timed[0]["gamePk"], "doubleheader_resolved_by_start_time"

    return None, "ambiguous_%d_candidates_refused" % len(cands)
