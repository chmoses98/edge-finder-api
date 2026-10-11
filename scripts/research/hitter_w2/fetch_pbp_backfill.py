#!/usr/bin/env python3
"""
scripts/research/hitter_w2/fetch_pbp_backfill.py
================================================
SIFT Wave 2, MLB hitter lane -- RESEARCH ONLY.

Network adapter that builds a compact, sports-source-only player-game and
plate-appearance archive for the hitter-engine audit
(docs/research/HITTER_ENGINE_AUDIT.md). It reads ONE public MLB Stats API
endpoint per game, the same one the existing settlement pipeline already
uses (`/api/v1.1/game/<gamePk>/feed/live`), and nothing else. No Kalshi,
sportsbook, odds or settlement record is read.

The gamePk universe comes from the ALREADY-COMMITTED schedule cache
(data/research_cache/bullpen_backtest/<season>/schedules/*.json, read-only,
regular season). Only games whose feed reports Final / Completed Early /
Game Over are extracted; everything else is listed in the manifest with a
reason, never guessed.

Output (per season, under --out-root/<season>/):
  games.csv.gz     one row per game (identity, start time, teams, final
                   score, innings, starting pitchers + hands, fetched_at,
                   payload sha1)
  batting.csv.gz   one row per player-game batting line from the official
                   box score (batting order, PA, AB, H, 2B, 3B, HR, R, RBI,
                   BB, HBP, K, SF, SH, SB, TB, ...)
  pitching.csv.gz  one row per pitcher appearance (order, outs, BF, K, ...)
  pa.csv.gz        one row per play (inning, half, batter, pitcher, hands,
                   event, rbi, runs on play, outs after, bases after,
                   ids of runners who scored)
  manifest.json    counts, skips, failures, fetched_at range

Network is NOT available in the research sandbox; this script is run by
.github/workflows/research-hitter-w2-backfill.yml on a GitHub-hosted runner.
The extraction functions are pure and unit-tested offline
(tests/research/test_hitter_w2_engine.py).
"""
import argparse
import csv
import glob
import gzip
import hashlib
import io
import json
import os
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
SCHEDULE_ROOT = os.path.join(_ROOT, "data", "research_cache", "bullpen_backtest")
FEED_URL = "https://statsapi.mlb.com/api/v1.1/game/{pk}/feed/live"
FINAL_STATES = {"Final", "Completed Early", "Game Over"}

GAME_FIELDS = [
    "season", "gamePk", "officialDate", "startUTC", "gameType", "doubleHeader", "gameNumber", "venueId",
    "awayId", "awayAbbr", "homeId", "homeAbbr", "awayRuns", "homeRuns", "innings", "detailedState",
    "awayStarterId", "awayStarterHand", "homeStarterId", "homeStarterHand",
    "awayProbableId", "homeProbableId", "dayNight", "fetchedAt", "payloadSha1",
]
BAT_FIELDS = [
    "season", "gamePk", "side", "teamId", "playerId", "batSide", "battingOrder", "slot", "isStarter", "position",
    "PA", "AB", "H", "2B", "3B", "HR", "R", "RBI", "BB", "IBB", "HBP", "K", "SF", "SH", "SB", "CS", "TB", "LOB",
    "GIDP",
]
PIT_FIELDS = ["season", "gamePk", "side", "teamId", "pitcherId", "pitchHand", "order", "outs", "BF", "pitches", "K",
              "BB", "H", "R", "HR"]
PA_FIELDS = ["season", "gamePk", "atBatIndex", "inning", "half", "batterId", "batSide", "pitcherId", "pitchHand",
             "eventType", "isComplete", "rbi", "runsOnPlay", "outsAfter", "postBases", "scorerIds"]


def _i(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return ""


def game_pks_for_season(season):
    pks = {}
    for path in sorted(glob.glob(os.path.join(SCHEDULE_ROOT, str(season), "schedules", "*.json"))):
        with open(path) as f:
            doc = json.load(f)
        for d in doc.get("dates") or []:
            for g in d.get("games") or []:
                if g.get("gameType") != "R":
                    continue
                pks[int(g["gamePk"])] = d.get("date")
    return pks


def extract_game(feed, season, fetched_at, payload_sha1):
    """Pure. feed/live JSON -> (game_row, batting_rows, pitching_rows, pa_rows)."""
    gd = feed.get("gameData") or {}
    ld = feed.get("liveData") or {}
    game = gd.get("game") or {}
    teams = gd.get("teams") or {}
    players = gd.get("players") or {}
    box = (ld.get("boxscore") or {}).get("teams") or {}
    linescore = ld.get("linescore") or {}
    pk = int(game.get("pk") or (feed.get("gamePk") or 0))

    def hand(pid, key):
        p = players.get(f"ID{pid}") or {}
        return ((p.get(key) or {}).get("code")) or ""

    starters = {}
    for side in ("away", "home"):
        order = (box.get(side) or {}).get("pitchers") or []
        starters[side] = order[0] if order else ""
    probable = gd.get("probablePitchers") or {}
    ls_teams = linescore.get("teams") or {}
    game_row = {
        "season": season, "gamePk": pk,
        "officialDate": (gd.get("datetime") or {}).get("officialDate", ""),
        "startUTC": (gd.get("datetime") or {}).get("dateTime", ""),
        "gameType": game.get("type", ""), "doubleHeader": game.get("doubleHeader", ""),
        "gameNumber": game.get("gameNumber", ""), "venueId": (gd.get("venue") or {}).get("id", ""),
        "awayId": (teams.get("away") or {}).get("id", ""), "awayAbbr": (teams.get("away") or {}).get("abbreviation", ""),
        "homeId": (teams.get("home") or {}).get("id", ""), "homeAbbr": (teams.get("home") or {}).get("abbreviation", ""),
        "awayRuns": (ls_teams.get("away") or {}).get("runs", ""), "homeRuns": (ls_teams.get("home") or {}).get("runs", ""),
        "innings": len(linescore.get("innings") or []),
        "detailedState": (gd.get("status") or {}).get("detailedState", ""),
        "awayStarterId": starters["away"], "awayStarterHand": hand(starters["away"], "pitchHand") if starters["away"] else "",
        "homeStarterId": starters["home"], "homeStarterHand": hand(starters["home"], "pitchHand") if starters["home"] else "",
        "awayProbableId": (probable.get("away") or {}).get("id", ""),
        "homeProbableId": (probable.get("home") or {}).get("id", ""),
        "dayNight": (gd.get("datetime") or {}).get("dayNight", ""),
        "fetchedAt": fetched_at, "payloadSha1": payload_sha1,
    }

    bat_rows, pit_rows = [], []
    for side in ("away", "home"):
        t = box.get(side) or {}
        team_id = ((t.get("team") or {}).get("id")) or ""
        for key, p in (t.get("players") or {}).items():
            pid = ((p.get("person") or {}).get("id"))
            if pid is None:
                continue
            b = ((p.get("stats") or {}).get("batting")) or {}
            bo = p.get("battingOrder")
            if b and bo:
                boi = _i(bo)
                bat_rows.append({
                    "season": season, "gamePk": pk, "side": side[0].upper(), "teamId": team_id, "playerId": pid,
                    "batSide": hand(pid, "batSide"), "battingOrder": bo,
                    "slot": (boi // 100) if boi != "" else "", "isStarter": int(boi != "" and boi % 100 == 0),
                    "position": ((p.get("position") or {}).get("abbreviation")) or "",
                    "PA": _i(b.get("plateAppearances")), "AB": _i(b.get("atBats")), "H": _i(b.get("hits")),
                    "2B": _i(b.get("doubles")), "3B": _i(b.get("triples")), "HR": _i(b.get("homeRuns")),
                    "R": _i(b.get("runs")), "RBI": _i(b.get("rbi")), "BB": _i(b.get("baseOnBalls")),
                    "IBB": _i(b.get("intentionalWalks")), "HBP": _i(b.get("hitByPitch")), "K": _i(b.get("strikeOuts")),
                    "SF": _i(b.get("sacFlies")), "SH": _i(b.get("sacBunts")), "SB": _i(b.get("stolenBases")),
                    "CS": _i(b.get("caughtStealing")), "TB": _i(b.get("totalBases")), "LOB": _i(b.get("leftOnBase")),
                    "GIDP": _i(b.get("groundIntoDoublePlay")),
                })
        for order, pid in enumerate(t.get("pitchers") or []):
            p = (t.get("players") or {}).get(f"ID{pid}") or {}
            s = ((p.get("stats") or {}).get("pitching")) or {}
            pit_rows.append({
                "season": season, "gamePk": pk, "side": side[0].upper(), "teamId": team_id, "pitcherId": pid,
                "pitchHand": hand(pid, "pitchHand"), "order": order, "outs": _i(s.get("outs")),
                "BF": _i(s.get("battersFaced")), "pitches": _i(s.get("numberOfPitches")), "K": _i(s.get("strikeOuts")),
                "BB": _i(s.get("baseOnBalls")), "H": _i(s.get("hits")), "R": _i(s.get("runs")), "HR": _i(s.get("homeRuns")),
            })

    pa_rows = []
    prev_away = prev_home = 0
    for play in ((ld.get("plays") or {}).get("allPlays") or []):
        about = play.get("about") or {}
        res = play.get("result") or {}
        mu = play.get("matchup") or {}
        half = "T" if about.get("halfInning") == "top" else "B"
        a, h = res.get("awayScore"), res.get("homeScore")
        a = prev_away if a is None else a
        h = prev_home if h is None else h
        runs = (a - prev_away) if half == "T" else (h - prev_home)
        prev_away, prev_home = a, h
        post = (1 if mu.get("postOnFirst") else 0) | (2 if mu.get("postOnSecond") else 0) | (4 if mu.get("postOnThird") else 0)
        scorers = []
        for r in play.get("runners") or []:
            mv = r.get("movement") or {}
            if mv.get("end") == "score" and not mv.get("isOut"):
                rid = ((r.get("details") or {}).get("runner") or {}).get("id")
                if rid is not None and rid not in scorers:
                    scorers.append(rid)
        pa_rows.append({
            "season": season, "gamePk": pk, "atBatIndex": about.get("atBatIndex", play.get("atBatIndex", "")),
            "inning": about.get("inning", ""), "half": half,
            "batterId": ((mu.get("batter") or {}).get("id")) or "", "batSide": ((mu.get("batSide") or {}).get("code")) or "",
            "pitcherId": ((mu.get("pitcher") or {}).get("id")) or "",
            "pitchHand": ((mu.get("pitchHand") or {}).get("code")) or "",
            "eventType": res.get("eventType", ""), "isComplete": int(bool(about.get("isComplete"))),
            "rbi": _i(res.get("rbi")), "runsOnPlay": runs, "outsAfter": _i((play.get("count") or {}).get("outs")),
            "postBases": post, "scorerIds": "|".join(str(s) for s in scorers),
        })
    return game_row, bat_rows, pit_rows, pa_rows


def _fetch(pk, timeout=60, retries=4):
    url = FEED_URL.format(pk=pk)
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "edge-finder-research/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read()
            fetched_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            return body, fetched_at
        except Exception as e:  # noqa: BLE001 -- recorded, retried, never fatal for the run
            last = e
            time.sleep(2 ** attempt)
    raise RuntimeError(f"{type(last).__name__}: {last}")


def _write_csv_gz(path, fields, rows):
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=fields, extrasaction="raise")
    w.writeheader()
    for r in rows:
        w.writerow(r)
    # mtime=0 -> byte-reproducible gzip for identical content
    with open(path, "wb") as f:
        with gzip.GzipFile(fileobj=f, mode="wb", mtime=0) as gz:
            gz.write(buf.getvalue().encode())


def _read_csv_gz(path):
    if not os.path.exists(path):
        return []
    with gzip.open(path, "rt", newline="") as f:
        return list(csv.DictReader(f))


def run_season(season, out_root, workers=6, max_games=None, spacing=0.05, deadline=None):
    """Resumable: games already extracted (games.csv.gz) are kept and never re-fetched; fetching
    stops at `deadline` (epoch seconds) and the season is marked complete only when every
    scheduled game is extracted or recorded as not final. Returns True when complete."""
    out_dir = os.path.join(out_root, str(season))
    manifest_path = os.path.join(out_dir, "manifest.json")
    old = {}
    if os.path.exists(manifest_path):
        with open(manifest_path) as f:
            old = json.load(f)
        if old.get("complete"):
            print(f"[{season}] already complete -- skipping (idempotent)")
            return True
    os.makedirs(out_dir, exist_ok=True)
    games = _read_csv_gz(os.path.join(out_dir, "games.csv.gz"))
    bats = _read_csv_gz(os.path.join(out_dir, "batting.csv.gz"))
    pits = _read_csv_gz(os.path.join(out_dir, "pitching.csv.gz"))
    pas = _read_csv_gz(os.path.join(out_dir, "pa.csv.gz"))
    done = {int(g["gamePk"]) for g in games}
    skipped = {int(k): v for k, v in (old.get("skippedNotFinal") or {}).items()}
    pks = game_pks_for_season(season)
    todo = [pk for pk in sorted(pks) if pk not in done and pk not in skipped]
    if max_games:
        todo = todo[: int(max_games)]
    failed = {}

    def work(pk):
        if deadline and time.time() > deadline:
            return pk, "DEADLINE", None
        time.sleep(spacing)
        body, fetched_at = _fetch(pk)
        feed = json.loads(body)
        state = ((feed.get("gameData") or {}).get("status") or {}).get("detailedState")
        if state not in FINAL_STATES:
            return pk, None, state
        return pk, extract_game(feed, season, fetched_at, hashlib.sha1(body).hexdigest()), state

    deferred = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(work, pk): pk for pk in todo}
        for n, fut in enumerate(as_completed(futs), 1):
            pk = futs[fut]
            try:
                _pk, out, state = fut.result()
            except Exception as e:  # noqa: BLE001
                failed[pk] = str(e)[:200]
                continue
            if out == "DEADLINE":
                deferred += 1
                continue
            if out is None:
                skipped[pk] = state
                continue
            g, b, p, a = out
            games.append(g)
            bats.extend(b)
            pits.extend(p)
            pas.extend(a)
            if n % 250 == 0:
                print(f"[{season}] {n}/{len(todo)} processed", flush=True)
    games.sort(key=lambda r: int(r["gamePk"]))
    bats.sort(key=lambda r: (int(r["gamePk"]), r["side"], str(r["battingOrder"])))
    pits.sort(key=lambda r: (int(r["gamePk"]), r["side"], int(r["order"])))
    pas.sort(key=lambda r: (int(r["gamePk"]), int(r["atBatIndex"] or 0)))
    _write_csv_gz(os.path.join(out_dir, "games.csv.gz"), GAME_FIELDS, games)
    _write_csv_gz(os.path.join(out_dir, "batting.csv.gz"), BAT_FIELDS, bats)
    _write_csv_gz(os.path.join(out_dir, "pitching.csv.gz"), PIT_FIELDS, pits)
    _write_csv_gz(os.path.join(out_dir, "pa.csv.gz"), PA_FIELDS, pas)
    fetched = sorted(g["fetchedAt"] for g in games)
    remaining = len([pk for pk in pks if pk not in {int(g["gamePk"]) for g in games} and pk not in skipped])
    manifest = {
        "season": season, "source": "MLB Stats API feed/live (statsapi.mlb.com /api/v1.1/game/<pk>/feed/live)",
        "gamePkUniverse": "data/research_cache/bullpen_backtest/<season>/schedules/*.json (gameType=R)",
        "scheduled": len(pks), "extracted": len(games), "remaining": remaining,
        "skippedNotFinal": {str(k): v for k, v in sorted(skipped.items())},
        "failedLastRun": {str(k): v for k, v in sorted(failed.items())},
        "rows": {"batting": len(bats), "pitching": len(pits), "pa": len(pas)},
        "fetchedAtMin": fetched[0] if fetched else None, "fetchedAtMax": fetched[-1] if fetched else None,
        "complete": remaining == 0 and not max_games,
    }
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=1, sort_keys=True)
    print(json.dumps({k: v for k, v in manifest.items() if k not in ("skippedNotFinal", "failedLastRun")}))
    print(f"[{season}] skipped={len(skipped)} failed={len(failed)} deferred={deferred}")
    return manifest["complete"]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--seasons", default="2026,2025,2024")
    ap.add_argument("--out-root", default=os.path.join(_ROOT, "data", "research", "hitter_w2", "mlb_feed"))
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--max-games", type=int, default=None)
    ap.add_argument("--budget-seconds", type=int, default=1080)
    args = ap.parse_args(argv)
    deadline = time.time() + args.budget_seconds
    for season in [int(s) for s in args.seasons.split(",")]:
        if time.time() > deadline:
            break
        run_season(season, args.out_root, workers=args.workers, max_games=args.max_games, deadline=deadline)


if __name__ == "__main__":
    sys.exit(main())
