"""
lib/research/mlb_pitcher_shadow_plan.py
=======================================
Standard-library-only scheduling logic for the MLB pitcher PURE shadow collector
(scripts/research/mlb_pitcher_shadow.py). RESEARCH ONLY.

It is the single source of truth for "which capture window is due for this game now",
"which windows have already been captured" and "which windows closed with no capture"
(a miss, recorded once, never back-filled). The collector imports it, and so does the
cheap workflow gate (scripts/research/mlb_pitcher_shadow_gate.py), which decides from the
live StatsAPI schedule and a sparse view of the store whether the full job has anything
to do. Nothing here imports pandas/numpy or any model code, so the gate needs no install.
"""

from __future__ import annotations

import glob
import gzip
import json
import os
from datetime import datetime, timedelta, timezone

SCHEDULE_URL = ("https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={date}"
                "&gameType=R,F,D,L,W&hydrate=probablePitcher,lineups")
PREGAME_OPEN_MIN, PREGAME_FORCE_MIN, PREGAME_CLOSE_MIN = 80, 45, 20
MORNING_LATEST_MIN = 120
MORNING_EARLIEST_ET_HOUR = 8
OUTCOME_DELAY_H = 3
FINAL = {"Final", "Game Over", "Completed Early"}
NOT_PLAYED = {"Postponed", "Cancelled", "Suspended"}
WINDOWS = ("morning", "pregame")


def utcnow():
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)


def stamp(t):
    return t.strftime("%Y%m%dT%H%M%SZ")


def et_now(now):
    try:
        from zoneinfo import ZoneInfo
        return now.astimezone(ZoneInfo("America/New_York"))
    except Exception:  # pragma: no cover - tz database missing
        return now - timedelta(hours=4)


def capture_dates(now):
    """officialDates a run at `now` looks at: today (ET), plus yesterday before 04:00 ET
    (a game that began before midnight ET keeps the previous officialDate)."""
    et = et_now(now)
    dates = {et.strftime("%Y-%m-%d")}
    if et.hour < 4:
        dates.add((et - timedelta(days=1)).strftime("%Y-%m-%d"))
    return sorted(dates)


def jsonl(path):
    op = gzip.open if path.endswith(".gz") else open
    with op(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


# ----------------------------------------------------------------------------- schedule (pure)

def parse_schedule(doc, fetched_at):
    games = []
    for day in (doc or {}).get("dates") or []:
        for g in day.get("games") or []:
            t = g.get("teams") or {}
            lu = g.get("lineups") or {}
            row = {"pk": str(g["gamePk"]), "game_type": g.get("gameType"), "official_date": g.get("officialDate") or day.get("date"),
                   "kickoff": iso(parse(g["gameDate"])) if g.get("gameDate") else None,
                   "abstract": (g.get("status") or {}).get("abstractGameState"),
                   "detailed": (g.get("status") or {}).get("detailedState"),
                   "schedule_fetched_at": fetched_at}
            for side in ("away", "home"):
                tb = t.get(side) or {}
                pp = tb.get("probablePitcher") or {}
                row[side] = {"team_id": (tb.get("team") or {}).get("id"),
                             "probable_id": str(pp["id"]) if pp.get("id") else None,
                             "probable_name": pp.get("fullName"),
                             "lineup_posted": bool(lu.get(f"{side}Players"))}
            games.append(row)
    return games


def window_for(game, now, done):
    """Pure. Which capture window (if any) this run should take for `game` at `now`.
    `done` is the set of windows already captured for this game."""
    if not game.get("kickoff"):
        return None, "no_scheduled_start"
    k = parse(game["kickoff"])
    if now >= k:
        return None, "at_or_after_kickoff"
    if game.get("abstract") not in ("Preview",) or game.get("detailed") in NOT_PLAYED:
        return None, f"status_{game.get('detailed')}"
    mins = (k - now).total_seconds() / 60
    if "pregame" not in done and PREGAME_CLOSE_MIN <= mins <= PREGAME_OPEN_MIN:
        lineups = game["away"]["lineup_posted"] and game["home"]["lineup_posted"]
        if lineups or mins <= PREGAME_FORCE_MIN:
            return "pregame", "lineups_posted" if lineups else "lineups_not_posted_final_window"
        return None, "pregame_waiting_for_lineups"
    et = et_now(now)
    if ("morning" not in done and "pregame" not in done and mins > MORNING_LATEST_MIN
            and et.strftime("%Y-%m-%d") == game["official_date"] and et.hour >= MORNING_EARLIEST_ET_HOUR):
        return "morning", "morning_of"
    return None, "outside_windows"


def window_closes_at(game, window):
    """The instant after which `window` can no longer be taken for `game`."""
    k = parse(game["kickoff"])
    return k - timedelta(minutes=MORNING_LATEST_MIN if window == "morning" else PREGAME_CLOSE_MIN)


# ----------------------------------------------------------------------------- store state (read-only)

def existing_windows(root, date):
    """{game_id: {window, ...}} captured on officialDate `date` (from the projection files)."""
    done = {}
    for f in glob.glob(os.path.join(root, "projections", date, "*.jsonl")):
        w = os.path.basename(f).split("__")[0].rsplit("_", 1)[-1]
        for r in jsonl(f):
            done.setdefault(r["game_id"], set()).add(w)
    return done


def collection_started_at(root):
    """as_of of the first non-dry-run capture run in the store (None before collection starts)."""
    starts = []
    for f in glob.glob(os.path.join(root, "runs", "*", "*.json")):
        with open(f, encoding="utf-8") as fh:
            doc = json.load(fh)
        if not doc.get("dry_run") and doc.get("as_of"):
            starts.append(parse(doc["as_of"]))
    return min(starts) if starts else None


def recorded_missed(root):
    out = set()
    for f in glob.glob(os.path.join(root, "missed", "*", "*.jsonl")):
        for r in jsonl(f):
            out.add((r["game_id"], r["window"]))
    return out


def missed_windows(games, now, root):
    """Windows that CLOSED after collection started with no capture and are not yet recorded.

    A miss is written once and never filled in: the collector never captures late. A game
    postponed/cancelled/suspended is not a miss; nor is the morning window of a game whose
    pregame capture exists (pregame supersedes it)."""
    start = collection_started_at(root)
    if start is None:
        return []
    already = recorded_missed(root)
    by_date = {}
    out = []
    for g in games:
        if not g.get("kickoff") or g.get("detailed") in NOT_PLAYED:
            continue
        if g["official_date"] not in by_date:
            by_date[g["official_date"]] = existing_windows(root, g["official_date"])
        got = by_date[g["official_date"]].get(g["pk"], set())
        for w in WINDOWS:
            closes = window_closes_at(g, w)
            if not (start < closes < now) or w in got or (g["pk"], w) in already:
                continue
            if w == "morning" and "pregame" in got:
                continue
            if w == "morning":
                opens = et_now(closes).replace(hour=MORNING_EARLIEST_ET_HOUR, minute=0, second=0)
                if et_now(closes).strftime("%Y-%m-%d") != g["official_date"] or closes <= opens:
                    continue  # no morning window exists for this game
            out.append({"game_id": g["pk"], "window": w, "official_date": g["official_date"], "kickoff": g["kickoff"],
                        "game_type": g.get("game_type"), "status_at_detection": g.get("detailed"),
                        "window_closed_at": iso(closes), "detected_at": iso(now), "reason": "NO_CAPTURE_IN_WINDOW"})
    return out


def outcomes_have(root):
    """game_ids whose outcome provenance is final (or not played): nothing more to fetch."""
    have = set()
    for f in glob.glob(os.path.join(root, "outcome_provenance", "*", "*.jsonl")):
        for r in jsonl(f):
            if r.get("status") in FINAL or r.get("status") in NOT_PLAYED:
                have.add(r["game_id"])
    return have


def outcomes_pending(root, now, lookback_days=7):
    """Captured game_ids old enough to have finished (and at most `lookback_days` old) with no final outcome yet."""
    have = outcomes_have(root)
    kick = {}
    for f in glob.glob(os.path.join(root, "projections", "*", "*.jsonl")):
        for r in jsonl(f):
            kick[r["game_id"]] = r["kickoff"]
    return sorted(pk for pk, k in kick.items() if pk not in have
                  and now - timedelta(days=lookback_days) <= parse(k) <= now - timedelta(hours=OUTCOME_DELAY_H))
