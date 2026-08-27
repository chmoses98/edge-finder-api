#!/usr/bin/env python3
"""
scripts/edgelab/backtest/probe_odds_api_historical_pinnacle.py
====================================================================
RESEARCH-ONLY feasibility probe for docs/EDGELAB_HISTORICAL_SHARP_MARKET_AUDIT.md.

Question: can this repo's EXISTING ODDS_API_KEY (The Odds API,
https://api.the-odds-api.com/v4) reach historical Pinnacle MLB odds for
2022-2026, and at what timestamp depth / market coverage?

REUSES, DOES NOT REIMPLEMENT: imports ODDS_API_KEY, BASE_URL, SPORT, and
api_get() directly from clv_update.py -- the same credential, base URL,
sport constant, and HTTP helper (with its existing error handling and
x-requests-remaining credit tracking) production's CLV update already
uses. This script calls The Odds API's `/historical/` endpoints, which
clv_update.py itself already defines call-sites for
(fetch_historical/fetch_historical_events/fetch_historical_event_odds)
but -- per that module's own v6.4 changelog ("Kalshi is the ONLY closing
line source. Odds API removed entirely.") -- never actually calls in its
current main() flow. Those functions are reused here for the SAME
purpose they were originally built for, just invoked for research rather
than left dead.

COST DISCIPLINE (spec: "Do NOT launch a full multi-season download until
the source is validated. Do not purchase anything or alter billing."):
this probe is deliberately two-phase and stops early if phase 1 fails.

  Phase 1 -- coverage probe: ONE historical `/events` call per target
  year (2022, 2023, 2024, 2025, 2026), each documented at 1 credit by
  clv_update.py's own fetch_historical_events docstring -- 5 credits
  total, cheapest possible way to answer "which years does this key's
  plan actually reach."

  Phase 2 -- SMALL deterministic odds sample: only runs for years where
  phase 1 confirmed events exist. Pulls historical odds (bookmakers=
  pinnacle explicitly, not the kalshi-first fallback chain
  fetch_historical uses for production CLV) for a small, fixed set of
  dates (not a full week of every game) in each reachable target year --
  bounded to control credit cost while still producing a real,
  deterministic, inspectable sample. Every credit-costing call and its
  reported `x-requests-remaining` is logged in the output so actual
  cost is auditable, never estimated.

NEVER writes to any production path -- output goes only to
data/research_cache/sharp_market_probe/ and this script's own stdout
JSON summary.
"""
import json
import os
import sys
from datetime import datetime, timedelta

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from clv_update import ODDS_API_KEY, BASE_URL, SPORT, api_get  # noqa: E402

CACHE_ROOT = os.path.join(_ROOT, "data", "research_cache", "sharp_market_probe")

# One representative mid-season date per target year -- maximizes the
# chance of games existing (avoids spring training / offseason edges),
# never chosen to make the result look better (same date-of-year for
# every target year).
PHASE1_PROBE_DATES = {
    2022: "2022-06-15", 2023: "2023-06-15", 2024: "2024-06-15",
    2025: "2025-06-15", 2026: "2026-06-15",
}

# Phase 2: a small, fixed, deterministic sample -- 3 dates per reachable
# year, not a full week of every game, to bound credit cost for this
# validation pass (spec: "limited deterministic sample").
PHASE2_SAMPLE_DATES = {
    2024: ["2024-06-10", "2024-06-11", "2024-06-12"],
    2025: ["2025-06-10", "2025-06-11", "2025-06-12"],
}

PHASE2_MARKETS = "h2h,totals,spreads"  # game ML, game total, run line -- the cheap bulk-endpoint markets


def probe_year_events(year, date_str):
    """Phase 1: ONE historical /events call (1 credit) for `date_str`.
    Returns a dict describing reachability -- never raises; a failure
    (401/403/network) is recorded as unreachable, not fabricated."""
    next_day = (datetime.strptime(date_str, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
    snapshot = next_day + "T02:00:00Z"
    url = (
        f"{BASE_URL}/historical/sports/{SPORT}/events"
        f"?apiKey={ODDS_API_KEY}"
        f"&commenceTimeFrom={date_str}T00:00:00Z"
        f"&commenceTimeTo={next_day}T06:00:00Z"
        f"&date={snapshot}"
    )
    data, remaining = api_get(url)
    if data is None:
        return {"year": year, "probeDate": date_str, "reachable": False, "eventCount": 0, "creditsRemaining": remaining}
    events = data.get("data", []) if isinstance(data, dict) else data
    return {
        "year": year, "probeDate": date_str, "reachable": True, "eventCount": len(events),
        "creditsRemaining": remaining,
        "sampleEventIds": [e.get("id") for e in events[:3]],
    }


def probe_day_odds_pinnacle(date_str):
    """Phase 2: ONE historical bulk odds call (bookmakers=pinnacle
    explicit, not the kalshi-first fallback fetch_historical uses in
    production) for one date. Cost per The Odds API's own metering is
    per-market-per-region returned, not flat -- logged via
    x-requests-remaining, never estimated."""
    next_day = (datetime.strptime(date_str, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
    snapshot = next_day + "T02:00:00Z"
    url = (
        f"{BASE_URL}/historical/sports/{SPORT}/odds"
        f"?apiKey={ODDS_API_KEY}&regions=us&bookmakers=pinnacle"
        f"&markets={PHASE2_MARKETS}&oddsFormat=american"
        f"&commenceTimeFrom={date_str}T00:00:00Z&commenceTimeTo={next_day}T06:00:00Z"
        f"&date={snapshot}"
    )
    data, remaining = api_get(url)
    if data is None:
        return {"date": date_str, "reachable": False, "games": [], "creditsRemaining": remaining}
    games = data.get("data", []) if isinstance(data, dict) else data
    snapshot_timestamp = data.get("timestamp") if isinstance(data, dict) else None
    return {
        "date": date_str, "reachable": True, "gameCount": len(games),
        "snapshotTimestamp": snapshot_timestamp, "creditsRemaining": remaining,
        "games": games,
    }


def summarize_pinnacle_coverage(day_result):
    """Pure. Inspects one probe_day_odds_pinnacle() result for exactly
    what the audit needs to know: is Pinnacle actually present, both
    sides, real timestamps, real line thresholds -- not just that SOME
    bookmaker responded."""
    summary = []
    for game in day_result.get("games", []):
        pinnacle_books = [b for b in (game.get("bookmakers") or []) if b.get("key") == "pinnacle"]
        if not pinnacle_books:
            summary.append({
                "homeTeam": game.get("home_team"), "awayTeam": game.get("away_team"),
                "commenceTime": game.get("commence_time"), "pinnaclePresent": False,
            })
            continue
        book = pinnacle_books[0]
        markets_seen = {}
        for m in book.get("markets") or []:
            outcomes = m.get("outcomes") or []
            markets_seen[m.get("key")] = {
                "lastUpdate": m.get("last_update"),
                "outcomeCount": len(outcomes),
                "bothSidesPresent": len(outcomes) >= 2,
                "sampleOutcomes": outcomes[:2],
            }
        summary.append({
            "homeTeam": game.get("home_team"), "awayTeam": game.get("away_team"),
            "commenceTime": game.get("commence_time"), "pinnaclePresent": True,
            "pinnacleLastUpdate": book.get("last_update"), "marketsSeen": markets_seen,
        })
    return summary


def main():
    if not ODDS_API_KEY:
        print(json.dumps({"error": "ODDS_API_KEY not set in this environment -- this probe only runs where the secret is injected (GitHub Actions), never locally."}))
        return 1

    result = {"generatedAt": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"), "phase1CoverageProbe": [], "phase2PinnacleSample": {}}

    print("== Phase 1: coverage probe (1 credit per year, 5 credits total) ==")
    reachable_years = []
    for year, date_str in sorted(PHASE1_PROBE_DATES.items()):
        probe = probe_year_events(year, date_str)
        result["phase1CoverageProbe"].append(probe)
        print(f"  {year} ({date_str}): reachable={probe['reachable']} events={probe.get('eventCount', 0)} creditsRemaining={probe.get('creditsRemaining')}")
        if probe["reachable"] and probe["eventCount"] > 0:
            reachable_years.append(year)

    print(f"\nReachable years (events > 0): {reachable_years}")

    print("\n== Phase 2: small deterministic Pinnacle odds sample ==")
    for year, dates in PHASE2_SAMPLE_DATES.items():
        if year not in reachable_years:
            print(f"  Skipping {year}: phase 1 found no events for this year -- not spending phase-2 credits on an unreachable year.")
            result["phase2PinnacleSample"][str(year)] = {"skipped": True, "reason": "PHASE1_UNREACHABLE"}
            continue
        year_results = []
        for date_str in dates:
            day_result = probe_day_odds_pinnacle(date_str)
            coverage = summarize_pinnacle_coverage(day_result)
            year_results.append({
                "date": date_str, "reachable": day_result["reachable"],
                "gameCount": day_result.get("gameCount", 0),
                "creditsRemaining": day_result.get("creditsRemaining"),
                "pinnacleCoverage": coverage,
            })
            n_with_pinnacle = sum(1 for c in coverage if c["pinnaclePresent"])
            print(f"  {date_str}: reachable={day_result['reachable']} games={day_result.get('gameCount', 0)} withPinnacle={n_with_pinnacle} creditsRemaining={day_result.get('creditsRemaining')}")
        result["phase2PinnacleSample"][str(year)] = {"skipped": False, "days": year_results}

    os.makedirs(CACHE_ROOT, exist_ok=True)
    out_path = os.path.join(CACHE_ROOT, "probe_result.json")
    with open(out_path, "w") as f:
        json.dump(result, f, indent=2, sort_keys=True, default=str)

    print(f"\nWrote probe result to {out_path}")
    print(json.dumps({"reachableYears": reachable_years, "phase1Credits": 5}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
