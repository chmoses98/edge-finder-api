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
from lib.kalshi_market_coverage import (  # noqa: E402
    full_accounting, load_hitter_prospective_snapshots, load_hitter_projection_board,
)

SNAPSHOT = os.path.join(ROOT, "data", "kalshi_registry_snapshots", "kalshi_search_2026-08-19_1931.json")
HITTER_SNAPSHOTS = os.path.join(ROOT, "data", "edgelab", "hitter_projection_snapshots", "2026-08-19.jsonl")
HITTER_BOARD = os.path.join(ROOT, "data", "pipeline", "2026-08-19", "hitter_projection_board.json")
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
    print("NOTE: this slate has NO probable-starter identity (game[side]['pitcher']) -- none is")
    print("committed to this repo for 2026-08-19 in this network-isolated environment, and none is")
    print("fabricated here. Pitcher strikeout/outs contracts therefore correctly report")
    print("MISSING_REQUIRED_CONTEXT below, not FULLY_EVALUATED -- see tests/test_kalshi_market_coverage.py::")
    print("TestPitcherPropCoverage for deterministic proof the wiring itself resolves correctly once a")
    print("real probable-starter feed (from a live fetch-slate.yml run) supplies game[side]['pitcher'].")

    hitter_snapshot_rows = load_hitter_prospective_snapshots("2026-08-19", path=HITTER_SNAPSHOTS)
    print(f"\nHitter PRIMARY research source (prospective snapshot store): "
          f"{'LOADED (' + HITTER_SNAPSHOTS + f'), {len(hitter_snapshot_rows)} checkpoint rows' if hitter_snapshot_rows else 'NOT AVAILABLE'}")
    if hitter_snapshot_rows:
        from collections import Counter
        print(f"  Checkpoint distribution: {dict(Counter(r.get('checkpoint') for r in hitter_snapshot_rows))}")
        print(f"  Distinct tickers checkpointed: {len({r['marketTicker'] for r in hitter_snapshot_rows if r.get('marketTicker')})}")

    hitter_board_data = load_hitter_projection_board("2026-08-19", path=HITTER_BOARD)
    print(f"Hitter FALLBACK source (legacy standalone board): "
          f"{'LOADED (' + HITTER_BOARD + ')' if hitter_board_data else 'NOT AVAILABLE'}")

    result = full_accounting(
        "2026-08-19", search_doc, slate_doc,
        hitter_snapshot_rows=hitter_snapshot_rows, hitter_board_data=hitter_board_data,
    )
    coverage = result["coverageAccounting"]
    raw = result["rawArchiveAccounting"]
    pregame = result["pregameView"]

    print(f"\nDiscovery summary: {json.dumps(result['discoverySummary'], indent=2)}")

    print("\n[Raw archive invariant -- independent of discover()'s own output]")
    for key in ("totalRawEntriesSeen", "entriesWithoutTicker", "duplicateRawTickerCount",
                "rawArchivedUnique", "accountedTickerCount", "trueSilentRemainderCount"):
        print(f"  {key:26s}: {raw[key]}")

    print(f"\nArchived total : {coverage['archivedTotal']}")
    print(f"Accounted for  : {coverage['accountedTotal']}")
    print(f"Unaccounted    : {coverage['unaccountedCount']}")
    print("\nBy terminal state:")
    for state, count in sorted(coverage["byState"].items()):
        print(f"  {state:26s}: {count}")

    print("\nBy family x terminal state (family counts across ALL states):")
    for family, states in sorted(coverage["byFamilyState"].items()):
        total = sum(states.values())
        nonzero = {k: v for k, v in states.items() if v}
        print(f"  {family or 'UNKNOWN':28s} total={total:5d}  {nonzero}")

    print("\n[Pregame-scoped view]")
    for key in sorted(pregame):
        print(f"  {key:32s}: {pregame[key]}")

    print("\n[Pitcher-prop specific counts]")
    pitcher_rows = [r for r in result["ledgerRows"] if r.get("marketFamily") in ("pitcher_strikeouts", "pitcher_outs")]
    print(f"  Pitcher K/outs contracts archived      : {len(pitcher_rows)}")
    print(f"  Correctly mapped to a probable starter  : "
          f"{sum(1 for r in pitcher_rows if r.get('subjectId'))}")
    print(f"  FULLY_EVALUATED                         : "
          f"{sum(1 for r in pitcher_rows if r['finalCoverageState'] == 'FULLY_EVALUATED')}")
    print(f"  MISSING_REQUIRED_CONTEXT (no starter ID) : "
          f"{sum(1 for r in pitcher_rows if r['finalCoverageState'] == 'MISSING_REQUIRED_CONTEXT')}")

    print("\n[Hitter research provenance -- item 9 re-audit]")
    hitter_families = ("hitter_hits", "hitter_total_bases", "hitter_rbis", "hitter_hits_runs_rbis", "hitter_stolen_bases")
    hitter_rows = [r for r in result["ledgerRows"] if r.get("marketFamily") in hitter_families]
    pregame_hitter_rows = [r for r in hitter_rows if r["finalCoverageState"] not in ("STARTED_GAME_EXCLUDED", "NOT_APPLICABLE")]
    print(f"  Remaining pregame hitter contracts (all 5 families) : {len(pregame_hitter_rows)}")
    research_linked = [r for r in pregame_hitter_rows if r.get("researchModelSupportStatus") not in (None, "NO_SNAPSHOTS_FOR_TICKER", "NO_SNAPSHOT_AT_OR_BEFORE_MARKET_OBSERVATION")]
    print(f"  Research-linked (any usable evidence)               : {len(research_linked)}")
    from collections import Counter
    print(f"  Checkpoint distribution of linked projections       : "
          f"{dict(Counter(r.get('hitterProjectionCheckpoint') for r in research_linked if r.get('hitterProjectionCheckpoint')))}")
    print(f"  Source type distribution                            : "
          f"{dict(Counter(r.get('hitterProjectionSourceType') for r in research_linked))}")
    current_price_available = sum(1 for r in pregame_hitter_rows if r.get("currentExecutableKalshiPrice") is not None)
    print(f"  Current-price coverage (currentExecutableKalshiPrice available) : {current_price_available}")
    current_ev_available = sum(1 for r in pregame_hitter_rows if r.get("currentFeeAwareNetExpectedValuePerDollar") is not None)
    print(f"  Current fee-aware EV coverage                                    : {current_ev_available}")
    no_projection = [r for r in pregame_hitter_rows if r["finalCoverageState"] == "UNSUPPORTED_MODEL_FAMILY"]
    print(f"  Lacking usable projection (UNSUPPORTED_MODEL_FAMILY) : {len(no_projection)}")
    print(f"    reasons: {dict(Counter(r.get('researchModelSupportStatus') for r in no_projection))}")


if __name__ == "__main__":
    main()
