#!/usr/bin/env python3
"""
scripts/edgelab/backtest/fetch_historical_pinnacle_cache.py
====================================================================
MLB-RSCH-0008 bulk historical Pinnacle acquisition. Credit-aware,
resumable, and deliberately bounded -- see CREDIT_ESTIMATE_PER_DATE
below and the orchestration script's own preflight guard (never
dispatched without an explicit credit estimate checked against the
mission's 50%-of-remaining-balance guard first).

DESIGN (fixes the audit's own found gap -- docs/EDGELAB_HISTORICAL_
SHARP_MARKET_AUDIT.md §7b, "blanket daily snapshot risks capturing a
near-final or in-progress price"): TWO fixed, preregistered snapshot
request times per date (not one), chosen to bracket the two dominant
real-world MLB start-time clusters (standard 18:10-19:40 ET starts,
and West-coast/late 21:00-22:30 ET starts) -- SNAPSHOT_TIMES_ET below.
Every game in the resulting cache is later matched, by
lib.edgelab.backtest.pinnacle_reconstruction.select_closest_pregame_
snapshot, to whichever of these snapshots (if any) falls strictly
before that SPECIFIC game's own scheduled start, within the
preregistered MAX_MINUTES_BEFORE_START -- a game with no qualifying
snapshot (e.g. an unusual early-afternoon start neither snapshot
covers) is excluded, never approximated.

Markets requested: h2h (moneyline) + totals (game total) only --
MLB-RSCH-0008's two PRIMARY families (spreads/run-line intentionally
excluded to control cost; F5 markets, empirically tested separately by
scripts/edgelab/backtest/probe_phase_a_validation.py via the smaller,
more expensive per-event endpoint, not this bulk endpoint).

CACHE FORMAT: data/research_cache/pinnacle_historical/<season>/<date>.json
-- one file per (season, date), containing BOTH raw snapshot pulls for
that date (so snapshot selection can be re-run later without
re-fetching), never a single collapsed "the" price. Resumable: a date
whose cache file already exists is skipped without any API call.
"""
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

RATE_LIMIT_SECONDS = 0.4  # matches clv_update.py's own existing rate-limit convention

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from clv_update import ODDS_API_KEY as _RAW_ODDS_API_KEY, BASE_URL, SPORT, api_get  # noqa: E402

ODDS_API_KEY = (_RAW_ODDS_API_KEY or "").strip()

CACHE_ROOT = os.path.join(_ROOT, "data", "research_cache", "pinnacle_historical")

# Fixed, preregistered -- never tuned from observed coverage. Chosen
# from ordinary MLB scheduling knowledge (most games start 18:10-19:40
# ET; West Coast/late games 21:00-22:30 ET), not from which times
# happened to maximize matched games in a trial run.
SNAPSHOT_TIMES_ET = ["18:15", "21:15"]
ET_UTC_OFFSET_HOURS = 4  # EDT (MLB regular season is entirely within EDT, not EST)

MARKETS = "h2h,totals"
CREDITS_PER_MARKET = 10  # empirically confirmed, docs/EDGELAB_HISTORICAL_SHARP_MARKET_AUDIT.md §7a/7b


def credit_estimate_per_date(num_markets=2):
    return len(SNAPSHOT_TIMES_ET) * num_markets * CREDITS_PER_MARKET


def _et_time_to_utc_iso(date_str, hhmm_et):
    hour, minute = (int(x) for x in hhmm_et.split(":"))
    naive_et = datetime.strptime(f"{date_str} {hour:02d}:{minute:02d}", "%Y-%m-%d %H:%M")
    utc_dt = naive_et + timedelta(hours=ET_UTC_OFFSET_HOURS)
    return utc_dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def date_cache_path(season, date_str):
    return os.path.join(CACHE_ROOT, str(season), f"{date_str}.json")


def already_cached(season, date_str):
    return os.path.exists(date_cache_path(season, date_str))


def fetch_date_snapshots(date_str):
    """Two bulk historical odds calls (bookmakers=pinnacle explicit) for
    `date_str`, one per SNAPSHOT_TIMES_ET entry. Returns
    {"date": date_str, "snapshots": [{"requestedAtEt": ..., "requestedAt": epoch,
    "games": [...raw game objects...]}, ...]}. Never raises -- a failed
    call yields an empty games list for that snapshot, logged, not
    silently fabricated."""
    next_day = (datetime.strptime(date_str, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
    snapshots = []
    for hhmm in SNAPSHOT_TIMES_ET:
        snapshot_iso = _et_time_to_utc_iso(date_str, hhmm)
        url = (
            f"{BASE_URL}/historical/sports/{SPORT}/odds"
            f"?apiKey={ODDS_API_KEY}&regions=us&bookmakers=pinnacle"
            f"&markets={MARKETS}&oddsFormat=american"
            f"&commenceTimeFrom={date_str}T00:00:00Z&commenceTimeTo={next_day}T08:00:00Z"
            f"&date={snapshot_iso}"
        )
        data, remaining = api_get(url)
        games = (data.get("data", []) if isinstance(data, dict) else data) if data is not None else []
        epoch = int(datetime.strptime(snapshot_iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp())
        snapshots.append({
            "requestedAtEt": hhmm, "requestedAtIso": snapshot_iso, "requestedAt": epoch,
            "gameCount": len(games), "creditsRemaining": remaining, "games": games,
        })
        time.sleep(RATE_LIMIT_SECONDS)
    return {"date": date_str, "snapshots": snapshots}


def run(season, dates, max_dates=None, dry_run=False):
    """Fetches and caches every date in `dates` not already cached, up
    to `max_dates` NEW fetches (None = unbounded within `dates`).
    dry_run=True estimates cost without calling the API."""
    to_fetch = [d for d in dates if not already_cached(season, d)]
    if max_dates is not None:
        to_fetch = to_fetch[:max_dates]

    if dry_run:
        return {
            "season": season, "requestedDates": len(dates), "alreadyCached": len(dates) - len(to_fetch),
            "wouldFetch": len(to_fetch), "estimatedCredits": len(to_fetch) * credit_estimate_per_date(),
        }

    os.makedirs(os.path.join(CACHE_ROOT, str(season)), exist_ok=True)
    fetched, total_credits_spent_estimate = 0, 0
    last_remaining = None
    for date_str in to_fetch:
        result = fetch_date_snapshots(date_str)
        with open(date_cache_path(season, date_str), "w") as f:
            json.dump(result, f, indent=2, sort_keys=True, default=str)
        fetched += 1
        for snap in result["snapshots"]:
            if snap.get("creditsRemaining") is not None:
                last_remaining = snap["creditsRemaining"]
        print(f"  [{season}] {date_str}: fetched, {sum(s['gameCount'] for s in result['snapshots'])} raw game-snapshot rows, creditsRemaining={last_remaining}")

    return {
        "season": season, "requestedDates": len(dates), "alreadyCached": len(dates) - len(to_fetch) - fetched,
        "newlyFetched": fetched, "creditsRemainingAfter": last_remaining,
    }


def season_date_range(season):
    """Regular-season-ish window: April 1 through the earlier of
    September 30 or today (for the current/in-progress season). Never
    includes spring training or postseason -- a coarse but honest
    bound, refined later by which dates actually returned events."""
    start = f"{season}-04-01"
    end = f"{season}-09-30"
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if season >= datetime.now(timezone.utc).year and end > today:
        end = today
    return start, end


def sampled_dates(season, stride=4):
    """Every `stride`-th date in the season's regular-season window --
    fixed, preregistered, never chosen from which dates have data."""
    start, end = season_date_range(season)
    start_dt, end_dt = datetime.strptime(start, "%Y-%m-%d"), datetime.strptime(end, "%Y-%m-%d")
    dates = []
    current = start_dt
    while current <= end_dt:
        dates.append(current.strftime("%Y-%m-%d"))
        current += timedelta(days=stride)
    return dates


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seasons", required=True, help="Comma-separated seasons, e.g. 2022,2023,2024,2026")
    parser.add_argument("--stride", type=int, default=5)
    parser.add_argument("--max-dates-per-season", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not ODDS_API_KEY and not args.dry_run:
        print(json.dumps({"error": "ODDS_API_KEY not set in this environment"}))
        return 1

    seasons = [int(s) for s in args.seasons.split(",")]
    results = {}
    for season in seasons:
        dates = sampled_dates(season, stride=args.stride)
        results[season] = run(season, dates, max_dates=args.max_dates_per_season, dry_run=args.dry_run)

    print(json.dumps(results, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
