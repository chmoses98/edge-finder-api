"""
Sportsbook capture and deterministic identity joins for the MRV collector.

Source: The Odds API v4, GET /sports/baseball_mlb/odds with an explicit
`bookmakers` list (Pinnacle, DraftKings, FanDuel, BetMGM) and markets
h2h, spreads, totals (decimal odds).  Every stored row is one (event,
bookmaker, market, outcome) with the provider's `last_update` per market
AND the fetch's requestedAt/respondedAt; simultaneity is never inferred
from the cycle.

Join rule (deterministic, refuses ambiguity):
  sportsbook event -> (awayAbbr, homeAbbr) via the fixed name table, and
  commence_time; Kalshi game candidates are the collector's eligible games
  with the same (away, home) whose scheduled start is within +/-45 minutes.
  exactly one candidate -> MATCHED; several -> AMBIGUOUS (doubleheader or
  clock mismatch; recorded, never guessed); none -> UNMATCHED.
"""
from datetime import datetime, timezone

from lib.edgelab.research.market_structure.sharp import TEAM_NAME_TO_ABBR

ODDS = "https://api.the-odds-api.com/v4"
BOOKMAKERS = ("pinnacle", "draftkings", "fanduel", "betmgm")
MARKETS = ("h2h", "spreads", "totals")
JOIN_TOLERANCE_S = 45 * 60


def odds_url(api_key, bookmakers=BOOKMAKERS, markets=MARKETS):
    return ("%s/sports/baseball_mlb/odds?apiKey=%s&bookmakers=%s&markets=%s&oddsFormat=decimal"
            % (ODDS, api_key, ",".join(bookmakers), ",".join(markets)))


def credits_from_headers(headers):
    h = headers or {}
    return {"last": h.get("x-requests-last"), "used": h.get("x-requests-used"), "remaining": h.get("x-requests-remaining")}


def _ts(s):
    try:
        return int(datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp())
    except (TypeError, ValueError):
        return None


def flatten_event(ev, stamp):
    """One Odds API event -> list of quote rows (one per book/market/outcome)."""
    rows = []
    for b in ev.get("bookmakers") or []:
        for m in b.get("markets") or []:
            for o in m.get("outcomes") or []:
                rows.append({"eventId": ev.get("id"), "commenceTime": ev.get("commence_time"), "awayName": ev.get("away_team"),
                             "homeName": ev.get("home_team"), "bookmaker": b.get("key"), "market": m.get("key"),
                             "outcomeName": o.get("name"), "point": o.get("point"), "priceDecimal": o.get("price"),
                             "providerLastUpdate": m.get("last_update") or b.get("last_update"), **stamp})
    return rows


def join_event(ev, eligible_games):
    """
    eligible_games: [{gamePk, physicalGameKey, awayAbbr, homeAbbr, scheduledStartTs}]
    -> {eventId, awayAbbr, homeAbbr, commenceTs, status: MATCHED|AMBIGUOUS|UNMATCHED, gamePk, physicalGameKey,
        candidates:[gamePk...], reason}
    """
    away, home = TEAM_NAME_TO_ABBR.get(ev.get("away_team")), TEAM_NAME_TO_ABBR.get(ev.get("home_team"))
    ct = _ts(ev.get("commence_time") or "")
    out = {"eventId": ev.get("id"), "awayName": ev.get("away_team"), "homeName": ev.get("home_team"), "awayAbbr": away,
           "homeAbbr": home, "commenceTime": ev.get("commence_time"), "gamePk": None, "physicalGameKey": None, "candidates": []}
    if not away or not home:
        out.update(status="UNMATCHED", reason="TEAM_NAME_NOT_IN_TABLE")
        return out
    if ct is None:
        out.update(status="UNMATCHED", reason="COMMENCE_TIME_UNPARSEABLE")
        return out
    cands = [g for g in eligible_games if g.get("awayAbbr") == away and g.get("homeAbbr") == home
             and g.get("scheduledStartTs") is not None and abs(g["scheduledStartTs"] - ct) <= JOIN_TOLERANCE_S]
    out["candidates"] = [g["gamePk"] for g in cands]
    if len(cands) == 1:
        out.update(status="MATCHED", reason=None, gamePk=cands[0]["gamePk"], physicalGameKey=cands[0].get("physicalGameKey"))
    elif len(cands) > 1:
        out.update(status="AMBIGUOUS", reason="MULTIPLE_GAMES_WITHIN_TOLERANCE")
    else:
        out.update(status="UNMATCHED", reason="NO_ELIGIBLE_GAME_WITHIN_TOLERANCE")
    return out
