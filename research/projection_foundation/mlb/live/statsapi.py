"""MLB Stats API access + pure parsers (no pandas, stdlib only).

Endpoints used (all public, no key):
  schedule  https://statsapi.mlb.com/api/v1/schedule?sportId=1&date=D&gameType=R,F,D,L,W&hydrate=probablePitcher,lineups,team
            -> probable pitchers (teams.<side>.probablePitcher) and the POSTED lineup card
               (lineups.awayPlayers / lineups.homePlayers, in batting order; empty until the club submits it)
  boxscore  https://statsapi.mlb.com/api/v1/game/<pk>/boxscore            (final results; same shape as
            liveData.boxscore of feed/live, which the repo's own extractors parse)

The parsers take already-decoded JSON, so tests feed them recorded/reconstructed payloads and the live run feeds them
the network response; nothing here knows the clock.
"""
from __future__ import annotations

import json
import time
import urllib.request
from datetime import datetime, timedelta, timezone

SCHEDULE_URL = ("https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={date}"
                "&gameType=R,F,D,L,W&hydrate=probablePitcher,lineups,team")
BOXSCORE_URL = "https://statsapi.mlb.com/api/v1/game/{pk}/boxscore"
FINAL_DETAILED = {"Final", "Game Over", "Completed Early"}
NOT_PLAYED = {"Postponed", "Cancelled"}          # voided; Suspended stays pending (it resumes later)
UA = {"User-Agent": "sift-projection-foundation/1.0 (research)", "Accept": "application/json"}


def iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_ts(s: str) -> datetime:
    return datetime.fromisoformat(str(s).replace("Z", "+00:00")).astimezone(timezone.utc)


def et_date(t: datetime) -> str:
    try:
        from zoneinfo import ZoneInfo
        return t.astimezone(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
    except Exception:  # pragma: no cover - tz database missing
        return (t - timedelta(hours=4)).strftime("%Y-%m-%d")


def capture_dates(now: datetime, ahead_hours: float = 30.0) -> list[str]:
    """officialDates a run at `now` must look at: yesterday (late games / results), today and tomorrow (ET)."""
    return sorted({et_date(now - timedelta(hours=24)), et_date(now), et_date(now + timedelta(hours=ahead_hours))})


# ----------------------------------------------------------------------------- network
def http_json(url: str, retries: int = 3, timeout: float = 25.0):
    """GET + decode. Returns (doc, fetched_utc_iso). Raises the last error after `retries` attempts (a failed fetch must
    fail the stage, never be silently treated as 'no games')."""
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read()
            return json.loads(body.decode("utf-8")), iso(datetime.now(timezone.utc))
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1.5 * (2 ** i))
    raise RuntimeError(f"statsapi fetch failed after {retries} attempts: {url}: {last}")


# ----------------------------------------------------------------------------- schedule
def _player(p: dict, slot: int) -> dict:
    return {"player_id": str(p["id"]), "name": p.get("fullName") or p.get("name"), "slot": slot,
            "position": ((p.get("primaryPosition") or {}).get("abbreviation")) or (p.get("position") or None)}


def parse_schedule(doc: dict | None) -> list[dict]:
    """Games of one schedule response. Lineups are in batting order; slot = list position (1-9)."""
    games = []
    for day in (doc or {}).get("dates") or []:
        for g in day.get("games") or []:
            if g.get("gamePk") is None or not g.get("gameDate"):
                continue
            st = g.get("status") or {}
            lu = g.get("lineups") or {}
            row = {"game_pk": str(g["gamePk"]), "game_type": g.get("gameType"),
                   "official_date": g.get("officialDate") or day.get("date"),
                   "start_utc": iso(parse_ts(g["gameDate"])), "abstract": st.get("abstractGameState"),
                   "detailed": st.get("detailedState"), "double_header": g.get("doubleHeader"),
                   "game_number": g.get("gameNumber"), "series": g.get("seriesDescription"),
                   "series_game": g.get("seriesGameNumber"),
                   "venue_id": (g.get("venue") or {}).get("id"), "venue": (g.get("venue") or {}).get("name"),
                   "runs": {}}
            for side in ("away", "home"):
                tb = (g.get("teams") or {}).get(side) or {}
                tm = tb.get("team") or {}
                pp = tb.get("probablePitcher") or {}
                players = lu.get(f"{side}Players") or []
                row[side] = {"team_id": tm.get("id"), "abbr": tm.get("abbreviation"), "name": tm.get("name"),
                             "probable_id": str(pp["id"]) if pp.get("id") else None,
                             "probable_name": pp.get("fullName"),
                             "lineup": [_player(p, i + 1) for i, p in enumerate(players) if p.get("id")],
                             "lineup_posted": bool(players)}
                if tb.get("score") is not None:
                    row["runs"][side] = tb["score"]
            games.append(row)
    return games


def is_preview(g: dict) -> bool:
    return g.get("abstract") == "Preview" and g.get("detailed") not in NOT_PLAYED


def is_final(g: dict) -> bool:
    return g.get("abstract") == "Final" or g.get("detailed") in FINAL_DETAILED


# ----------------------------------------------------------------------------- boxscore
def _i(x):
    try:
        return int(x)
    except (TypeError, ValueError):
        return None


def parse_boxscore(doc: dict | None) -> dict | None:
    """Final box score -> {'pitchers': {side: [lines in order]}, 'batters': {side: [...]}, 'team': {side: {...}}}.

    Accepts the /game/<pk>/boxscore document (top-level `teams`) or a feed/live document (`liveData.boxscore.teams`).
    Field names are the ones the repo's committed extractors read (scripts/research/hitter_w2/fetch_pbp_backfill.py,
    lib/edgelab/backtest/bullpen_backtest_reconstruction.extract_pitcher_lines)."""
    if not doc:
        return None
    teams = doc.get("teams") or ((doc.get("liveData") or {}).get("boxscore") or {}).get("teams")
    if not teams:
        return None
    out = {"pitchers": {}, "batters": {}, "team": {}}
    for side in ("away", "home"):
        t = teams.get(side) or {}
        players = t.get("players") or {}
        pit = []
        for order, pid in enumerate(t.get("pitchers") or []):
            p = players.get(f"ID{pid}") or {}
            s = (p.get("stats") or {}).get("pitching") or {}
            pit.append({"player_id": str(pid), "name": (p.get("person") or {}).get("fullName"), "order": order,
                        "outs": _i(s.get("outs")), "bf": _i(s.get("battersFaced")), "k": _i(s.get("strikeOuts")),
                        "bb": _i(s.get("baseOnBalls")), "h": _i(s.get("hits")), "r": _i(s.get("runs")),
                        "er": _i(s.get("earnedRuns")), "hr": _i(s.get("homeRuns")),
                        "pitches": _i(s.get("numberOfPitches")),
                        "hand": ((p.get("person") or {}).get("pitchHand") or {}).get("code")})
        bat = []
        for key, p in players.items():
            b = (p.get("stats") or {}).get("batting") or {}
            bo = _i(p.get("battingOrder"))
            if bo is None or not (p.get("person") or {}).get("id"):
                continue
            g = lambda k: _i(b.get(k)) or 0  # noqa: E731
            h, d2, d3, hr = g("hits"), g("doubles"), g("triples"), g("homeRuns")
            tb = _i(b.get("totalBases"))
            if tb is None:
                tb = (h - d2 - d3 - hr) + 2 * d2 + 3 * d3 + 4 * hr
            r, rbi = g("runs"), g("rbi")
            bat.append({"player_id": str(p["person"]["id"]), "name": p["person"].get("fullName"), "slot": bo // 100,
                        "is_starter": bo % 100 == 0, "position": (p.get("position") or {}).get("abbreviation"),
                        "bat_side": ((p["person"].get("batSide") or {}).get("code")),
                        "PA": _i(b.get("plateAppearances")), "AB": g("atBats"), "H": h, "d2": d2, "d3": d3, "HR": hr,
                        "R": r, "RBI": rbi, "BB": g("baseOnBalls"), "IBB": g("intentionalWalks"), "HBP": g("hitByPitch"),
                        "K": g("strikeOuts"), "SF": g("sacFlies"), "SH": g("sacBunts"), "SB": g("stolenBases"),
                        "CS": g("caughtStealing"), "TB": tb, "LOB": g("leftOnBase"), "GIDP": g("groundIntoDoublePlay"),
                        "HRR": h + r + rbi})
        bat.sort(key=lambda x: (x["slot"], not x["is_starter"]))
        ts = ((t.get("teamStats") or {}).get("batting")) or {}
        out["pitchers"][side] = pit
        out["batters"][side] = bat
        out["team"][side] = {"runs": _i(ts.get("runs"))}
    return out
