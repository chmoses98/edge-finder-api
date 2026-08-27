#!/usr/bin/env python3
"""
scripts/edgelab/backtest/probe_phase_a_validation.py
====================================================================
MLB-RSCH-0008 Phase A validation: resolves the two open questions the
historical sharp-market audit (docs/EDGELAB_HISTORICAL_SHARP_MARKET_AUDIT.md)
left unresolved, before any acquisition or model work begins.

1. 2025 resolution: the audit's single 2025-06-15 probe date returned
   zero events -- an anomaly, not conclusive. This probes multiple
   representative 2025 regular-season dates (spread across the season,
   never chosen to make the result look better either way).

2. F5 empirical coverage: probes real historical Pinnacle availability
   for h2h_1st_5_innings / spreads_1st_5_innings / totals_1st_5_innings
   on a small, fixed sample of already-known-good 2024 games (reusing
   the exact dates the audit's own Phase 2 already validated for full-
   game markets), via the per-event endpoint (clv_update.py's own
   documented cost: 10 credits per unique market returned).

Reuses ODDS_API_KEY/BASE_URL/SPORT/api_get from clv_update.py, with the
same defensive .strip() the audit's own bug-fix established for
probe_odds_api_historical_pinnacle.py. Bounded and cheap by design:
~5 credits for 2025 dates, ~90 credits worst-case for F5 (3 games x 3
markets x 10 credits) -- reported exactly, never estimated.
"""
import json
import os
import sys
from datetime import datetime, timedelta

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from clv_update import ODDS_API_KEY as _RAW_ODDS_API_KEY, BASE_URL, SPORT, api_get  # noqa: E402

ODDS_API_KEY = (_RAW_ODDS_API_KEY or "").strip()

CACHE_ROOT = os.path.join(_ROOT, "data", "research_cache", "sharp_market_probe")

# Spread across the 2025 regular season (April-September), never chosen
# post hoc from which dates "work" -- picked before any call was made.
RESOLUTION_2025_DATES = ["2025-04-15", "2025-05-15", "2025-06-20", "2025-07-15", "2025-08-15"]

# The exact 3 dates the audit's own Phase 2 already validated for
# full-game markets -- reused here, not a new date choice, to isolate
# "does F5 exist" from "does this date have data at all."
F5_TEST_DATES = ["2024-06-10", "2024-06-11", "2024-06-12"]
F5_MARKETS = "h2h_1st_5_innings,spreads_1st_5_innings,totals_1st_5_innings"
F5_GAMES_PER_DATE = 1  # one event per date -- keeps this a bounded probe, not a full pull


def probe_2025_date(date_str):
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
        return {"date": date_str, "reachable": False, "eventCount": 0, "creditsRemaining": remaining}
    events = data.get("data", []) if isinstance(data, dict) else data
    return {"date": date_str, "reachable": True, "eventCount": len(events), "creditsRemaining": remaining, "eventIds": [e.get("id") for e in events]}


def _list_events_for_date(date_str):
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
        return [], remaining
    events = data.get("data", []) if isinstance(data, dict) else data
    return events, remaining


def probe_f5_for_event(event_id, date_str):
    """ONE per-event historical odds call, bookmakers=pinnacle explicit,
    F5 markets only. Cost: up to 10 credits per unique market actually
    returned (clv_update.py's own documented cost model)."""
    next_day = (datetime.strptime(date_str, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
    snapshot = next_day + "T02:00:00Z"
    url = (
        f"{BASE_URL}/historical/sports/{SPORT}/events/{event_id}/odds"
        f"?apiKey={ODDS_API_KEY}&regions=us&bookmakers=pinnacle"
        f"&markets={F5_MARKETS}&oddsFormat=american&date={snapshot}"
    )
    data, remaining = api_get(url)
    if data is None:
        return {"eventId": event_id, "reachable": False, "creditsRemaining": remaining, "f5MarketsFound": []}
    game_data = data.get("data") if isinstance(data, dict) else data
    if not game_data:
        return {"eventId": event_id, "reachable": True, "creditsRemaining": remaining, "f5MarketsFound": []}
    pinnacle_books = [b for b in (game_data.get("bookmakers") or []) if b.get("key") == "pinnacle"]
    markets_found = []
    if pinnacle_books:
        for m in pinnacle_books[0].get("markets") or []:
            markets_found.append({
                "key": m.get("key"), "lastUpdate": m.get("last_update"),
                "outcomeCount": len(m.get("outcomes") or []),
            })
    return {
        "eventId": event_id, "reachable": True, "creditsRemaining": remaining,
        "homeTeam": game_data.get("home_team"), "awayTeam": game_data.get("away_team"),
        "commenceTime": game_data.get("commence_time"), "f5MarketsFound": markets_found,
    }


def main():
    if not ODDS_API_KEY:
        print(json.dumps({"error": "ODDS_API_KEY not set in this environment"}))
        return 1

    result = {"generatedAt": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"), "resolution2025": [], "f5Probe": []}

    print("== 2025 resolution: multiple representative dates ==")
    for date_str in RESOLUTION_2025_DATES:
        probe = probe_2025_date(date_str)
        result["resolution2025"].append(probe)
        print(f"  {date_str}: reachable={probe['reachable']} events={probe.get('eventCount', 0)} creditsRemaining={probe.get('creditsRemaining')}")

    reachable_2025 = [p for p in result["resolution2025"] if p["reachable"] and p["eventCount"] > 0]
    print(f"\n2025 dates with events > 0: {len(reachable_2025)} / {len(RESOLUTION_2025_DATES)}")

    print("\n== F5 empirical probe (per-event endpoint, bookmakers=pinnacle) ==")
    for date_str in F5_TEST_DATES:
        events, remaining = _list_events_for_date(date_str)
        print(f"  {date_str}: {len(events)} events listed | creditsRemaining={remaining}")
        for event in events[:F5_GAMES_PER_DATE]:
            event_id = event.get("id")
            if not event_id:
                continue
            f5_result = probe_f5_for_event(event_id, date_str)
            f5_result["date"] = date_str
            result["f5Probe"].append(f5_result)
            found_keys = [m["key"] for m in f5_result.get("f5MarketsFound", [])]
            print(f"    event {event_id[:8]}..: f5MarketsFound={found_keys} creditsRemaining={f5_result.get('creditsRemaining')}")

    os.makedirs(CACHE_ROOT, exist_ok=True)
    out_path = os.path.join(CACHE_ROOT, "phase_a_validation_result.json")
    with open(out_path, "w") as f:
        json.dump(result, f, indent=2, sort_keys=True, default=str)

    print(f"\nWrote Phase A validation result to {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
