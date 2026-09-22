"""
Ticker identity for the MRV program: series, family, physical game key,
side, rung.  Pure functions; never guesses.

Physical game key == the event-ticker suffix (date+time+teams[+G<n>]),
exactly as lib.edgelab.research.night_before_timing.physical_game_key,
because ~17 series price the same game and eventTicker/gameId are not
safe cluster keys (see that function's docstring).
"""
import re

from lib.edgelab.mlb_alpha_identity import parse_event_ticker, STATUS_RESOLVED

# series -> (family, horizon, contractShape)
SERIES_MAP = {
    "KXMLBGAME": ("game_result", "FULL_GAME", "WINNER"),
    "KXMLBF5": ("inning_result", "F5", "WINNER3"),
    "KXMLBF3": ("inning_result", "F3", "WINNER3"),
    "KXMLBF7": ("inning_result", "F7", "WINNER3"),
    "KXMLBTOTAL": ("game_total", "FULL_GAME", "LADDER"),
    "KXMLBF5TOTAL": ("inning_total", "F5", "LADDER"),
    "KXMLBTEAMTOTAL": ("team_total", "FULL_GAME", "TEAM_LADDER"),
    "KXMLBSPREAD": ("winning_margin", "FULL_GAME", "TEAM_LADDER"),
    "KXMLBF5SPREAD": ("winning_margin", "F5", "TEAM_LADDER"),
    "KXMLBRFI": ("first_inning_run", "FIRST_INNING", "SINGLE"),
}

_TICKER_RE = re.compile(r"^(?P<series>[A-Z0-9]+)-(?P<event>[0-9A-Z]+)(?:-(?P<contract>[A-Z0-9]+))?$")
_TEAM_COUNT_RE = re.compile(r"^(?P<team>[A-Z]+?)(?P<n>\d+)$")


def parse_ticker(market_ticker):
    """
    Returns a dict with status RESOLVED/UNRESOLVED.  On RESOLVED:
      seriesTicker, eventTicker, physicalGameKey, family, horizon, shape,
      side ("AWAY"/"HOME"/"TIE"/None), team (abbr or None), rung (int or None),
      awayTeam, homeTeam, gameDate, scheduledStartUtc.
    Rung semantics are the RAW ticker integer: for LADDER series the contract
    pays YES iff total >= rung (Kalshi "N or more"); for TEAM_LADDER the
    team-total contract stores N and pays YES iff runs >= N as well (the
    repository documents `over_n=4` == "over 3.5"); for SPREAD the contract
    pays YES iff (team - opponent) > rung - 0.5, i.e. margin >= rung.
    """
    base = {"marketTicker": market_ticker, "status": "UNRESOLVED", "unresolvedReason": None}
    if not isinstance(market_ticker, str):
        base["unresolvedReason"] = "not_a_string"
        return base
    m = _TICKER_RE.match(market_ticker)
    if not m:
        base["unresolvedReason"] = "ticker_shape_unrecognized"
        return base
    series = m.group("series")
    if series not in SERIES_MAP:
        base["unresolvedReason"] = "series_not_in_scope:%s" % series
        return base
    event_ticker = "%s-%s" % (series, m.group("event"))
    ev = parse_event_ticker(event_ticker)
    if ev.get("status") != STATUS_RESOLVED:
        base["unresolvedReason"] = "event:%s" % ev.get("unresolvedReason")
        return base
    family, horizon, shape = SERIES_MAP[series]
    contract = m.group("contract")
    side = team = rung = None
    if shape == "WINNER":
        if contract not in (ev["awayTeam"], ev["homeTeam"]):
            base["unresolvedReason"] = "winner_contract_not_a_team:%r" % contract
            return base
        team = contract
        side = "AWAY" if contract == ev["awayTeam"] else "HOME"
    elif shape == "WINNER3":
        if contract == "TIE":
            side = "TIE"
        elif contract in (ev["awayTeam"], ev["homeTeam"]):
            team = contract
            side = "AWAY" if contract == ev["awayTeam"] else "HOME"
        else:
            base["unresolvedReason"] = "three_way_contract_unrecognized:%r" % contract
            return base
    elif shape == "LADDER":
        if contract is None or not contract.isdigit():
            base["unresolvedReason"] = "ladder_rung_missing:%r" % contract
            return base
        rung = int(contract)
    elif shape == "TEAM_LADDER":
        tm = _TEAM_COUNT_RE.match(contract or "")
        if not tm or tm.group("team") not in (ev["awayTeam"], ev["homeTeam"]):
            base["unresolvedReason"] = "team_ladder_contract_unrecognized:%r" % contract
            return base
        team = tm.group("team")
        side = "AWAY" if team == ev["awayTeam"] else "HOME"
        rung = int(tm.group("n"))
    elif shape == "SINGLE":
        if contract is not None:
            base["unresolvedReason"] = "single_contract_has_suffix:%r" % contract
            return base
    return {
        "marketTicker": market_ticker, "status": "RESOLVED", "unresolvedReason": None,
        "seriesTicker": series, "eventTicker": event_ticker,
        "physicalGameKey": m.group("event"),
        "family": family, "horizon": horizon, "shape": shape,
        "side": side, "team": team, "rung": rung,
        "awayTeam": ev["awayTeam"], "homeTeam": ev["homeTeam"],
        "gameDate": ev["gameDate"], "scheduledStartUtc": ev["scheduledStartUtc"],
        "doubleheaderGame": ev["doubleheaderGame"],
    }


def physical_game_key(market_or_event_ticker):
    """Event-ticker suffix shared by every series pricing one physical game; None if unparseable."""
    if not isinstance(market_or_event_ticker, str):
        return None
    m = _TICKER_RE.match(market_or_event_ticker)
    return m.group("event") if m else None
