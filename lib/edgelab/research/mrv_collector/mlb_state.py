"""
MLB information-state capture for the MRV collector.

Sources (public MLB Stats API, already the project's trusted lineup /
pitcher source):
  GET /api/v1/schedule?sportId=1&date=<D>&hydrate=probablePitcher,lineups
  GET /api/v1.1/game/<gamePk>/feed/live            (status, probable pitchers,
                                                    batting orders, weather,
                                                    metaData.timeStamp)

Timestamp semantics (never conflated):
  observedAt       = respondedAt of OUR fetch that first showed the state
  prevObservedAt   = respondedAt of the previous fetch that showed the prior
                     state -> the event happened in (prevObservedAt, observedAt]
  sourceTimestamp  = the feed's own metaData.timeStamp (the feed's last update
                     clock, YYYYMMDD_HHMMSS UTC), stored verbatim; it is NOT
                     the event time and is never used to backfill one.
"""
from datetime import datetime, timezone

from lib.edgelab.research.mrv_collector import season_phase as SP

MLB = "https://statsapi.mlb.com/api/v1"
MLB11 = "https://statsapi.mlb.com/api/v1.1"

PREGAME_STATES = ("Scheduled", "Pre-Game", "Warmup", "Delayed Start", "Delayed Start: Rain", "Postponed")
STATE_FIELDS = ("status", "awayProbableId", "homeProbableId", "awayLineupIds", "homeLineupIds", "awayLineupPosted",
                "homeLineupPosted", "weather", "scheduledStart", "postponed", "gameType", "seasonPhase")


def schedule_url(date):
    return "%s/schedule?sportId=1&date=%s&hydrate=probablePitcher,lineups,team" % (MLB, date)


def live_feed_url(game_pk):
    return "%s/game/%s/feed/live" % (MLB11, game_pk)


def games_from_schedule(payload):
    """-> [{gamePk, gameDate(iso), status, awayId, homeId, awayAbbr, homeAbbr, awayProbableId, homeProbableId, doubleheader, gameNumber}]"""
    out = []
    for dd in ((payload or {}).get("dates") or []):
        for g in dd.get("games") or []:
            teams = g.get("teams") or {}
            away, home = teams.get("away") or {}, teams.get("home") or {}
            out.append({"gamePk": g.get("gamePk"), "gameDate": g.get("gameDate"), "officialDate": g.get("officialDate"),
                        "gameTypeSchedule": g.get("gameType"), "season": g.get("season"),
                        "status": (g.get("status") or {}).get("detailedState"), "abstractState": (g.get("status") or {}).get("abstractGameState"),
                        "awayId": (away.get("team") or {}).get("id"), "homeId": (home.get("team") or {}).get("id"),
                        "awayAbbr": (away.get("team") or {}).get("abbreviation"), "homeAbbr": (home.get("team") or {}).get("abbreviation"),
                        "awayProbableId": (away.get("probablePitcher") or {}).get("id"), "homeProbableId": (home.get("probablePitcher") or {}).get("id"),
                        "doubleHeader": g.get("doubleHeader"), "gameNumber": g.get("gameNumber"),
                        "lineups": g.get("lineups") or {}})
    return out


def is_pregame(game):
    st = game.get("status") or ""
    abstract = game.get("abstractState") or ""
    if abstract == "Preview":
        return True
    return st in PREGAME_STATES


def state_from_feed(game, feed):
    """Merge a schedule game with its live feed into the canonical state dict + sourceTimestamp."""
    gd = (feed or {}).get("gameData") or {}
    ld = (feed or {}).get("liveData") or {}
    box = (ld.get("boxscore") or {}).get("teams") or {}
    pp = gd.get("probablePitchers") or {}
    status = (gd.get("status") or {}).get("detailedState") or game.get("status")
    weather = gd.get("weather") or None
    lu = game.get("lineups") or {}
    away_lu = [p.get("id") for p in (lu.get("awayPlayers") or [])] or list((box.get("away") or {}).get("battingOrder") or [])
    home_lu = [p.get("id") for p in (lu.get("homePlayers") or [])] or list((box.get("home") or {}).get("battingOrder") or [])
    st = {"status": status,
          "awayProbableId": (pp.get("away") or {}).get("id") or game.get("awayProbableId"),
          "homeProbableId": (pp.get("home") or {}).get("id") or game.get("homeProbableId"),
          "awayLineupIds": [int(x) for x in away_lu if x is not None], "homeLineupIds": [int(x) for x in home_lu if x is not None],
          "awayLineupPosted": bool(away_lu), "homeLineupPosted": bool(home_lu),
          "weather": {k: weather.get(k) for k in ("condition", "temp", "wind")} if isinstance(weather, dict) else None,
          "scheduledStart": ((gd.get("datetime") or {}).get("dateTime")) or game.get("gameDate"),
          "postponed": (status or "").startswith("Postponed")}
    source_ts = ((feed or {}).get("metaData") or {}).get("timeStamp")
    phase = SP.resolve(game.get("gameTypeSchedule"), ((gd.get("game") or {}).get("type")))
    st["gameType"] = phase["gameType"]
    st["seasonPhase"] = phase["seasonPhase"]
    return st, source_ts


def transitions(prev_state, new_state):
    """[{field, from, to}] for every STATE_FIELD that changed; [] when identical or no prior state."""
    if not prev_state:
        return []
    out = []
    for f in STATE_FIELDS:
        a, b = prev_state.get(f), new_state.get(f)
        if a != b:
            out.append({"field": f, "from": a, "to": b})
    return out


def et_dates_for(now_utc=None):
    """Today's and tomorrow's MLB (ET) dates for the schedule query."""
    now = now_utc or datetime.now(timezone.utc)
    et = now.replace(tzinfo=None) - __import__("datetime").timedelta(hours=4)
    d0 = et.strftime("%Y-%m-%d")
    d1 = (et + __import__("datetime").timedelta(days=1)).strftime("%Y-%m-%d")
    return [d0, d1]
