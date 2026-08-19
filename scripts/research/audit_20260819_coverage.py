#!/usr/bin/env python3
"""
scripts/research/audit_20260819_coverage.py
================================================
One-off Phase 11 audit run for the MLB slate coverage mission: applies
lib.kalshi_market_coverage's full-archived-market-universe accounting to
the REAL 2026-08-19 archived Kalshi registry snapshot
(data/kalshi_registry_snapshots/kalshi_search_2026-08-19_1931.json, 2390
markets across 17 series -- the latest pregame-window snapshot for that
date), against a representative slate built from the real
(away, home) pairs and observed game start times in that same snapshot
(no live slate.json existed for 2026-08-19 at audit time -- see PR
description). Games that had already started as of the snapshot's
19:31 UTC capture time (first pitch <= 15:31 ET) are marked "In Progress"
so STARTED_GAME_EXCLUDED classification is exercised correctly; the rest
are "Scheduled".

Research/reporting script only -- reads archived data, prints a report,
writes nothing back to data/. Not part of any production workflow.
"""
import json
import os
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from lib.kalshi_mlb_contract_parser import parse_contract  # noqa: E402
from lib.kalshi_market_coverage import build_coverage_ledger, coverage_accounting  # noqa: E402

SNAPSHOT = os.path.join(ROOT, "data", "kalshi_registry_snapshots", "kalshi_search_2026-08-19_1931.json")
OBSERVATION_TIME = datetime(2026, 8, 19, 19, 31, tzinfo=timezone.utc)


def build_representative_slate(snapshot):
    games = {}
    for m in snapshot["markets"]:
        try:
            p = parse_contract(m)
        except Exception:
            continue
        away, home, close_time = p.get("awayTeam"), p.get("homeTeam"), p.get("closeTime")
        if not (away and home):
            continue
        games.setdefault((away, home), []).append(close_time)

    # Derive each game's real ET first-pitch HHMM from a KXMLBGAME ticker
    # for that (away, home) pair -- closeTime on these tickers is Kalshi's
    # market-close time (days after first pitch), not first pitch itself.
    import re
    pat = re.compile(r"^KXMLBGAME-\d{2}[A-Z]{3}\d{2}(\d{4})([A-Z]+)-")
    hhmm_by_pair = {}
    for m in snapshot["markets"]:
        ticker = m.get("market_ticker") or ""
        mm = pat.match(ticker)
        if not mm:
            continue
        try:
            p = parse_contract(m)
        except Exception:
            continue
        away, home = p.get("awayTeam"), p.get("homeTeam")
        if away and home:
            hhmm_by_pair[(away, home)] = mm.group(1)

    obs_et_hhmm = "1531"  # 19:31 UTC == 15:31 ET on 2026-08-19 (EDT, UTC-4)
    slate_games = []
    for i, ((away, home), _closes) in enumerate(sorted(games.items()), start=1):
        hhmm = hhmm_by_pair.get((away, home))
        # ET (UTC-4, EDT) -> UTC ISO startTime, same convention
        # build_slate_index()/_et_time_str() round-trips.
        start_time_iso = None
        if hhmm is not None:
            hour, minute = int(hhmm[:2]), int(hhmm[2:])
            utc_hour = (hour + 4) % 24
            day = 19 if (hour + 4) < 24 else 20
            start_time_iso = f"2026-08-{day:02d}T{utc_hour:02d}:{minute:02d}:00Z"
        slate_games.append({
            "gameId": 900000 + i, "away": {"abbr": away, "pitcherSavant": {"xFIP": 3.9, "avgIPperStart": 5.7, "kPct": 0.225}},
            "home": {"abbr": home, "pitcherSavant": {"xFIP": 4.0, "avgIPperStart": 5.7, "kPct": 0.22}},
            "awayTeamStats": {"offenseBaselineAdj": 4.5}, "homeTeamStats": {"offenseBaselineAdj": 4.4},
            "park": {"parkFactor": 100}, "startTime": start_time_iso, "startTimeHHMM_ET": hhmm,
            "status": "In Progress" if (hhmm is not None and hhmm <= obs_et_hhmm) else "Scheduled",
        })

    return {"date": "2026-08-19", "games": slate_games}


def main():
    with open(SNAPSHOT) as f:
        snapshot = json.load(f)

    search_doc = {
        "date": "2026-08-19",
        "markets": snapshot["markets"],
        "discoveredUnknownSeriesMarkets": snapshot.get("discoveredUnknownSeriesMarkets") or [],
    }
    slate_doc = build_representative_slate(snapshot)
    started = sum(1 for g in slate_doc["games"] if g["status"] != "Scheduled")
    print(f"Representative slate: {len(slate_doc['games'])} games, {started} already in progress "
          f"as of observation time {OBSERVATION_TIME.isoformat()}")

    ledger_rows, discovery_summary = build_coverage_ledger("2026-08-19", search_doc, slate_doc)
    accounting = coverage_accounting(ledger_rows)

    print(f"\nDiscovery summary: {json.dumps(discovery_summary, indent=2)}")
    print(f"\nArchived total : {accounting['archivedTotal']}")
    print(f"Accounted for  : {accounting['accountedTotal']}")
    print(f"Unaccounted    : {accounting['unaccountedCount']}")
    print("\nBy terminal state:")
    for state, count in sorted(accounting["byState"].items()):
        print(f"  {state:26s}: {count}")

    print("\nBy family x terminal state (family counts across ALL states):")
    for family, states in sorted(accounting["byFamilyState"].items()):
        total = sum(states.values())
        nonzero = {k: v for k, v in states.items() if v}
        print(f"  {family or 'UNKNOWN':28s} total={total:5d}  {nonzero}")


if __name__ == "__main__":
    main()
