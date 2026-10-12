#!/usr/bin/env python3
"""
scripts/research/mlb_postseason_boxscore_backfill.py
====================================================
W2 Phase 3 data repair (RESEARCH ONLY): a SPORTS-SOURCE backfill of MLB
postseason pitcher box scores + workload history (outs, BF, K, pitches, IP,
start/end time, gamePk, pitcher id/team, game type F/D/L/W) for the 2022-2026
postseasons, from the public MLB StatsAPI.

Why: docs/research/PITCHER_WORKLOAD_SOURCE_AUDIT.md section 3 found that the
only committed postseason pitcher lines were Kalshi settlement evidence (a
market-selected population with fetch lags), so the 2026 postseason was
BLOCKED_DATA for the sports-only pitcher workload challenger. This script adds
the missing sports-source cache. It runs on GitHub Actions
(.github/workflows/research-mlb-postseason-boxscore-backfill.yml) because the
research sandbox cannot reach statsapi.mlb.com.

Reuse, not a new layout
-----------------------
* Same cache tree as MLB-RSCH-0004 (scripts/edgelab/backtest/
  fetch_mlb_starter_workload_cache.py): data/research_cache/starter_workload/
  <season>/. Postseason rows go to SEPARATE files so the frozen challenger
  (lib/research/pitcher_workload_sources.SOURCE_GLOBS reads
  `*/boxscores.jsonl.gz`) and the champion's loaders keep byte-identical
  inputs:
      <season>/postseason_schedule.json        official postseason schedule snapshot
      <season>/postseason_boxscores.jsonl.gz   one record per FINAL postseason game
* Pitcher lines are built by the existing, unchanged
  lib.edgelab.backtest.bullpen_backtest_reconstruction.extract_pitcher_lines
  (identical fields to the regular-season cache), plus inningsPitched.
* Fetch adapters are the existing lib.edgelab.mlb_boxscore.fetch_game_feed
  (feed/live, v1.1) and lib.edgelab.bullpen_usage.fetch_team_boxscore
  (/v1/game/<pk>/boxscore). Both endpoints are fetched for every game and the
  pitcher lines are cross-checked field by field (agreement stored per game).
* Storage: lib.edgelab.storage.append_records (id = gamePk) -> idempotent and
  write-once per game: an already-cached gamePk is never re-fetched or
  rewritten.

Provenance per game: source URL, fetched_at (UTC) and sha256 of the parsed
payload for each endpoint, plus scheduled first pitch, actual first pitch
(gameInfo.firstPitch) and game end (last play endTime). A game is written only
if its live-feed status is final AND fetched_at is after the game end, so a
partial line can never be cached.

Network only on Actions. Never reads any market, odds or settlement file.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from lib.edgelab.backtest.bullpen_backtest_reconstruction import extract_pitcher_lines  # noqa: E402
from lib.edgelab.bullpen_usage import _fetch_json, fetch_team_boxscore  # noqa: E402
from lib.edgelab.mlb_boxscore import fetch_game_feed, is_final_status  # noqa: E402
from lib.edgelab.storage import append_records, read_records  # noqa: E402

CACHE_ROOT = os.path.join(ROOT, "data", "research_cache", "starter_workload")
POSTSEASON_GAME_TYPES = ("F", "D", "L", "W")
SCHEDULE_URL = ("https://statsapi.mlb.com/api/v1/schedule?sportId=1&season={season}"
                "&gameType=F,D,L,W&startDate={season}-09-01&endDate={season}-11-30")
FEED_URL = "https://statsapi.mlb.com/api/v1.1/game/{pk}/feed/live"
BOXSCORE_URL = "https://statsapi.mlb.com/api/v1/game/{pk}/boxscore"
LINE_FIELDS = ("playerId", "orderIndex", "numberOfPitches", "outs", "battersFaced", "strikeOuts",
               "baseOnBalls", "hits", "runs", "earnedRuns", "inningsPitched")


def utcnow():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def payload_sha256(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def schedule_path(season):
    return os.path.join(CACHE_ROOT, str(season), "postseason_schedule.json")


def boxscore_path(season):
    return os.path.join(CACHE_ROOT, str(season), "postseason_boxscores.jsonl.gz")


# ----------------------------------------------------------------------------- pure

def parse_schedule(doc):
    """Pure. Official postseason schedule rows (one per gamePk, last listing wins)."""
    games = {}
    for day in (doc or {}).get("dates") or []:
        for g in day.get("games") or []:
            if g.get("gameType") not in POSTSEASON_GAME_TYPES or g.get("gamePk") is None:
                continue
            st = g.get("status") or {}
            t = g.get("teams") or {}
            games[g["gamePk"]] = {
                "gamePk": g["gamePk"],
                "gameType": g.get("gameType"),
                "officialDate": g.get("officialDate") or day.get("date"),
                "gameDate": g.get("gameDate"),
                "abstractGameState": st.get("abstractGameState"),
                "detailedState": st.get("detailedState"),
                "seriesDescription": g.get("seriesDescription"),
                "seriesGameNumber": g.get("seriesGameNumber"),
                "gamesInSeries": g.get("gamesInSeries"),
                "ifNecessary": g.get("ifNecessary"),
                "doubleHeader": g.get("doubleHeader"),
                "gameNumber": g.get("gameNumber"),
                "rescheduledFrom": g.get("rescheduledFrom"),
                "resumedFrom": g.get("resumedFrom"),
                "away": {"id": ((t.get("away") or {}).get("team") or {}).get("id"),
                         "name": ((t.get("away") or {}).get("team") or {}).get("name")},
                "home": {"id": ((t.get("home") or {}).get("team") or {}).get("id"),
                         "name": ((t.get("home") or {}).get("team") or {}).get("name")},
            }
    return [games[k] for k in sorted(games)]


def pitcher_lines(box, side):
    """Pure. extract_pitcher_lines (unchanged, shared with the regular-season cache) plus
    inningsPitched for the same pitcher."""
    lines = extract_pitcher_lines(box, side)
    players = (((box or {}).get("teams") or {}).get(side) or {}).get("players") or {}
    for ln in lines:
        p = ((players.get(f"ID{ln['playerId']}") or {}).get("stats") or {}).get("pitching") or {}
        ln["inningsPitched"] = p.get("inningsPitched")
    return lines


def game_times(feed):
    """Pure. (actual first pitch, game end) from the live feed: gameInfo.firstPitch (else the
    first play's startTime) and the max endTime over plays."""
    gd = (feed or {}).get("gameData") or {}
    plays = (((feed or {}).get("liveData") or {}).get("plays") or {}).get("allPlays") or []
    first = (gd.get("gameInfo") or {}).get("firstPitch")
    if not first and plays:
        first = (plays[0].get("about") or {}).get("startTime")
    ends = [(p.get("about") or {}).get("endTime") for p in plays]
    ends = [e for e in ends if e]
    return first, (max(ends) if ends else None)


def _norm_ts(s):
    if not s:
        return None
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def compare_lines(a, b):
    """Pure. Field-level disagreements between two sides' pitcher-line lists."""
    out = []
    if len(a) != len(b):
        out.append({"field": "n_pitchers", "feed": len(a), "boxscore": len(b)})
    for x, y in zip(a, b):
        for f in LINE_FIELDS:
            if x.get(f) != y.get(f):
                out.append({"playerId": x.get("playerId"), "field": f, "feed": x.get(f), "boxscore": y.get(f)})
    return out


def build_record(season, sched_row, feed, feed_meta, box, box_meta):
    """Pure. One cached postseason game record (or (None, reason))."""
    gd = (feed or {}).get("gameData") or {}
    status = (gd.get("status") or {}).get("detailedState")
    if not is_final_status(status):
        return None, f"not_final:{status}"
    if gd.get("game", {}).get("type") not in POSTSEASON_GAME_TYPES:
        return None, f"not_postseason:{gd.get('game', {}).get('type')}"
    fbox = ((feed.get("liveData") or {}).get("boxscore")) or {}
    first, end = game_times(feed)
    first, end = _norm_ts(first), _norm_ts(end)
    if end is None:
        return None, "no_game_end_time"
    if feed_meta["fetched_at"] <= end:
        return None, "fetched_before_game_end"
    rec = {
        "gamePk": sched_row["gamePk"],
        "season": int(season),
        "gameType": gd["game"]["type"],
        "officialDate": (gd.get("datetime") or {}).get("officialDate") or sched_row.get("officialDate"),
        "scheduledStart": _norm_ts((gd.get("datetime") or {}).get("dateTime") or sched_row.get("gameDate")),
        "firstPitch": first,
        "gameEnd": end,
        "detailedState": status,
        "seriesDescription": sched_row.get("seriesDescription"),
        "seriesGameNumber": sched_row.get("seriesGameNumber"),
        "gamesInSeries": sched_row.get("gamesInSeries"),
        "doubleHeader": sched_row.get("doubleHeader"),
        "gameNumber": sched_row.get("gameNumber"),
        "awayTeam": {"id": ((gd.get("teams") or {}).get("away") or {}).get("id"),
                     "abbr": ((gd.get("teams") or {}).get("away") or {}).get("abbreviation")},
        "homeTeam": {"id": ((gd.get("teams") or {}).get("home") or {}).get("id"),
                     "abbr": ((gd.get("teams") or {}).get("home") or {}).get("abbreviation")},
        "awayPitchers": pitcher_lines(fbox, "away"),
        "homePitchers": pitcher_lines(fbox, "home"),
        "sources": [dict(feed_meta, endpoint="statsapi_v1.1_feed_live")],
        "fetched_at": feed_meta["fetched_at"],
        "source_url": feed_meta["url"],
    }
    if box is not None:
        dis = []
        for side in ("away", "home"):
            dis += [dict(d, side=side) for d in compare_lines(rec[f"{side}Pitchers"], pitcher_lines(box, side))]
        rec["sources"].append(dict(box_meta, endpoint="statsapi_v1_boxscore"))
        rec["boxscoreEndpointAgreement"] = not dis
        rec["boxscoreEndpointDisagreements"] = dis
    else:
        rec["boxscoreEndpointAgreement"] = None
        rec["boxscoreEndpointDisagreements"] = []
    return rec, None


# ----------------------------------------------------------------------------- network

def fetch_schedule(season):
    url = SCHEDULE_URL.format(season=season)
    fetched_at = utcnow()
    doc = _fetch_json(url, 30)
    return doc, {"url": url, "fetched_at": fetched_at, "payload_sha256": payload_sha256(doc) if doc else None}


def write_schedule(season, rows, meta):
    path = schedule_path(season)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    old = None
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            old = json.load(fh)
    if old is not None and old.get("games") == rows:
        return False
    doc = {"season": int(season), "source": "mlb_statsapi_schedule", "source_url": meta["url"],
           "fetched_at": meta["fetched_at"], "payload_sha256": meta["payload_sha256"],
           "gameTypes": list(POSTSEASON_GAME_TYPES), "games": rows}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1, sort_keys=True)
        fh.write("\n")
    return True


def cached_pks(season):
    return {r.get("gamePk") for r in read_records(boxscore_path(season))}


def run(seasons, rate_limit=0.25, max_games=None):
    summary = []
    for season in seasons:
        doc, meta = fetch_schedule(season)
        if not doc:
            summary.append({"season": season, "error": "schedule_fetch_failed", "url": meta["url"]})
            continue
        rows = parse_schedule(doc)
        sched_changed = write_schedule(season, rows, meta)
        have = cached_pks(season)
        todo = [r for r in rows if is_final_status(r.get("detailedState")) and r["gamePk"] not in have]
        if max_games:
            todo = todo[:max_games]
        recs, skipped = [], []
        for r in todo:
            pk = r["gamePk"]
            f_at = utcnow()
            feed = fetch_game_feed(pk, timeout=30)
            if feed is None:
                skipped.append({"gamePk": pk, "reason": "feed_fetch_failed"})
                continue
            fmeta = {"url": FEED_URL.format(pk=pk), "fetched_at": f_at, "payload_sha256": payload_sha256(feed)}
            time.sleep(rate_limit)
            b_at = utcnow()
            box = fetch_team_boxscore(pk, timeout=30)
            bmeta = {"url": BOXSCORE_URL.format(pk=pk), "fetched_at": b_at,
                     "payload_sha256": payload_sha256(box) if box else None}
            time.sleep(rate_limit)
            rec, why = build_record(season, r, feed, fmeta, box, bmeta)
            if rec is None:
                skipped.append({"gamePk": pk, "reason": why})
                continue
            recs.append(rec)
        written, dup = append_records(boxscore_path(season), recs, id_field="gamePk") if recs else (0, 0)
        by_state = {}
        for r in rows:
            by_state[r.get("detailedState")] = by_state.get(r.get("detailedState"), 0) + 1
        summary.append({"season": season, "schedule_games": len(rows), "schedule_by_state": by_state,
                        "schedule_snapshot_changed": sched_changed, "already_cached": len(have),
                        "attempted": len(todo), "written": written, "duplicates": dup, "skipped": skipped,
                        "disagreeing_games": [x["gamePk"] for x in recs if x.get("boxscoreEndpointAgreement") is False]})
    return summary


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seasons", default="2022,2023,2024,2025,2026")
    ap.add_argument("--rate-limit-seconds", type=float, default=0.25)
    ap.add_argument("--max-games-per-season", type=int, default=None)
    a = ap.parse_args(argv)
    seasons = [int(s) for s in a.seasons.split(",") if s.strip()]
    out = run(seasons, a.rate_limit_seconds, a.max_games_per_season)
    print(json.dumps(out, indent=1, sort_keys=True))
    return 1 if any("error" in s for s in out) else 0


if __name__ == "__main__":
    sys.exit(main())
