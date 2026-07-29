#!/usr/bin/env python3
"""
scripts/research/build_kalshi_market_inventory.py
======================================================
Model Performance Phase 1 (Market Audit) -- builds
data/research/kalshi_mlb_market_inventory.json from ALREADY-SAVED,
LOCAL Kalshi discovery snapshots. Makes NO live network call and does
NOT hit any Kalshi endpoint -- per the mission's explicit "no
production workflow dispatch" / research-only constraint, this script
reconstructs the inventory entirely from files already committed to
this repository:

  - data/kalshi_registry_snapshots/kalshi_search_<date>[_<time>].json
    (the freshest, most complete real discovery snapshots -- ~250
    files spanning 2026-06-08 through 2026-07-29)
  - archive/data/kalshi_full_enumeration.json,
    archive/data/kalshi_remaining_discovery.json,
    archive/data/kalshi_series_discovery.json (earlier, hand-run
    discovery probes from 2026-06-04, useful for series names not
    necessarily active in the single most-recent snapshot, e.g.
    KXMLBSPREAD's per-team margin-threshold tickers)

Output is written to data/research/kalshi_mlb_market_inventory.json --
a RESEARCH-ONLY artifact. Nothing in scripts/build_market_ledger.py,
scripts/risk_gate.py, scripts/protect_slate.py,
scripts/validate_slate_final.py, or scripts/write_pending_bets.py reads
this file. Running this script does not touch data/slate.json,
bets.json, or any production pipeline artifact.
"""
import glob
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from lib.research.market_taxonomy import classify_market, is_three_way_family, HORIZON_MARKET_STATUS

SNAPSHOT_GLOB = os.path.join(ROOT, "data", "kalshi_registry_snapshots", "kalshi_search_*.json")
ARCHIVE_FILES = [
    os.path.join(ROOT, "archive", "data", "kalshi_full_enumeration.json"),
    os.path.join(ROOT, "archive", "data", "kalshi_remaining_discovery.json"),
    os.path.join(ROOT, "archive", "data", "kalshi_series_discovery.json"),
]
OUTPUT_PATH = os.path.join(ROOT, "data", "research", "kalshi_mlb_market_inventory.json")


def _latest_snapshot_path():
    paths = sorted(glob.glob(SNAPSHOT_GLOB))
    return paths[-1] if paths else None


def _load_snapshot_markets(path):
    with open(path) as f:
        d = json.load(f)
    return d.get("markets", []), d.get("fetched_at"), d.get("date")


def _load_archive_tickers():
    """
    Best-effort: pulls any market_ticker-shaped strings out of the
    archive discovery files' nested structures without assuming a
    fixed schema (these files have differing internal shapes across
    the three discovery scripts that produced them).
    """
    found = []
    for path in ARCHIVE_FILES:
        if not os.path.exists(path):
            continue
        with open(path) as f:
            try:
                d = json.load(f)
            except json.JSONDecodeError:
                continue
        found.extend(_walk_for_market_records(d))
    return found


def _walk_for_market_records(obj):
    records = []
    if isinstance(obj, dict):
        if "ticker" in obj and "event_ticker" in obj:
            records.append({
                "market_ticker": obj.get("ticker"),
                "event_ticker": obj.get("event_ticker"),
                "title": obj.get("title"),
                "yes_bid": obj.get("yes_bid"),
                "yes_ask": obj.get("yes_ask"),
            })
        for v in obj.values():
            records.extend(_walk_for_market_records(v))
    elif isinstance(obj, list):
        for item in obj:
            records.extend(_walk_for_market_records(item))
    return records


def build_inventory():
    latest_path = _latest_snapshot_path()
    if latest_path is None:
        raise FileNotFoundError(f"no snapshot files found matching {SNAPSHOT_GLOB}")

    markets, fetched_at, snapshot_date = _load_snapshot_markets(latest_path)
    archive_records = _load_archive_tickers()

    entries = []
    seen_series = set()
    seen_families = set()

    for m in markets:
        classified = classify_market(
            m.get("market_ticker"), event_ticker=m.get("event_ticker"),
            title=m.get("title"), subtitle=m.get("subtitle"),
        )
        entry = {
            **classified,
            "yesBid": m.get("yes_bid"),
            "yesAsk": m.get("yes_ask"),
            # NOTE: this repo's own snapshot format (api/kalshisearch.js's
            # output shape) does not capture NO-side bid/ask explicitly --
            # a real, documented gap (see docs/research/PROJECTION_AUDIT.md
            # Part 2 findings). NO prices are only derivable as (1 - yes)
            # for a strictly binary market, and are NOT independently
            # observed here.
            "noBid": None,
            "noAsk": None,
            "midPrice": m.get("mid"),
            "lastPrice": m.get("last_price"),
            "americanOdds": m.get("american_odds"),
            "impliedPct": m.get("implied_pct"),
            "volume": m.get("volume"),
            "openInterest": m.get("open_interest"),
            "status": m.get("status"),
            "openTime": m.get("open_time"),
            "closeTime": m.get("close_time"),
            "marketTypeRaw": m.get("market_type"),
            # NOTE: Kalshi's own settlement-rules text field
            # ("rules_primary"/"rules_secondary" in the real API) is NOT
            # captured by this repo's existing fetch scripts at all --
            # another real, documented gap. Settlement basis below is
            # INFERRED from independently observed ticker structure
            # (e.g. the presence/absence of a "-TIE" leg per event), not
            # read from Kalshi's own rules text.
            "settlementRulesText": None,
            "settlementRulesSource": "inferred_from_ticker_structure_not_kalshi_rules_field",
            "isThreeWay": is_three_way_family(classified["family"], classified["scope"]),
            "productionConsumptionStatus": _production_consumption_status(classified),
            "modelSupportStatus": _model_support_status(classified),
        }
        entries.append(entry)
        seen_series.add(classified["seriesTicker"])
        seen_families.add(classified["family"])

    archive_series = set()
    for rec in archive_records:
        series = (rec.get("market_ticker") or "").split("-", 1)[0]
        if series:
            archive_series.add(series)

    inventory = {
        "discoverySource": os.path.relpath(latest_path, ROOT),
        "discoveryTimestamp": fetched_at,
        "discoverySnapshotDate": snapshot_date,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "generatorScript": "scripts/research/build_kalshi_market_inventory.py",
        "note": (
            "RESEARCH-ONLY artifact. Never consumed by production betting "
            "logic. Built entirely from already-saved local snapshot files "
            "-- no live Kalshi API call was made to produce this file."
        ),
        "seriesTickersObservedInLatestSnapshot": sorted(seen_series),
        "seriesTickersObservedInArchiveDiscovery": sorted(archive_series),
        "familiesObserved": sorted(seen_families),
        "totalMarketsInLatestSnapshot": len(markets),
        "totalArchiveRecordsExamined": len(archive_records),
        "discoveryLimitationWarning": (
            "seriesTickersObservedInLatestSnapshot and "
            "seriesTickersObservedInArchiveDiscovery reflect ONLY what this "
            "repository's own fetchers (api/kalshisearch.js's fixed ALL_SERIES "
            "list, and the archive probes' equally fixed series lists) ever "
            "queried Kalshi for. A series absent from BOTH lists means this "
            "repository never asked Kalshi about it -- it is NOT evidence that "
            "Kalshi does not offer that series. See "
            "userConfirmedUndiscoveredHorizons below for a corrected, honest "
            "accounting of this exact failure mode as it applies to F3/F7."
        ),
        # CORRECTION (retracts this field's prior name/content,
        # "confirmedAbsentSeries", which falsely asserted "Kalshi does not
        # appear to offer" F3/F7 markets solely because this repository's
        # snapshots never contained one -- a user with direct Kalshi account
        # access has confirmed placing real wagers on both MLB F3 and F7
        # markets. Absence-from-this-repository's-own-archive was never
        # valid evidence of absence-from-Kalshi; see
        # lib/research/market_taxonomy.py's HORIZON_MARKET_STATUS for the
        # full existence/discovery/archive/normalization/projection/
        # production status breakdown reused verbatim here.
        "userConfirmedUndiscoveredHorizons": {
            "F3": HORIZON_MARKET_STATUS["F3"],
            "F7": HORIZON_MARKET_STATUS["F7"],
        },
        "entries": entries,
    }
    return inventory


def _production_consumption_status(classified):
    """
    Cross-references against production's ACTUAL consumption (per this
    phase's repository audit of scripts/build_market_ledger.py and
    scripts/merge_odds.py, not guessed): only game_result/full_game,
    inning_result/F5 (team legs only -- see below), game_total/
    full_game, team_total/full_game, and winning_margin/full_game are
    currently read into odds.kalshi.{ml,f5ml,total,team_totals,rl} and
    populate a marketLedger row. first_inning_run (NRFI/YRFI) is also
    consumed. winning_margin/F5, inning_total/F5 (F5 totals), and
    everything else in this inventory is NOT currently read by
    production at all.

    REAL FINDING (this phase, independently verified via grep):
    scripts/merge_odds.py DOES populate
    `odds.kalshi.f5ml.tie_american` (kalshi_books['f5ml']['tie_american']
    = t_am, merge_odds.py line ~317) -- the F5 TIE price genuinely
    flows all the way into the slate. But
    scripts/build_market_ledger.py reads it
    (`f5_tie_am = f5ml.get('tie_american')`, line 919) into a local
    variable that is NEVER referenced again anywhere else in the file
    -- confirmed via grep showing exactly one occurrence of
    `f5_tie_am` in the entire script. The F5 Tie outcome specifically
    is therefore classified as "data_captured_never_evaluated", not
    "consumed_by_production" -- production has real market data for
    this exact outcome in hand and does not use it for anything,
    not even a rejected/missing-data row.
    """
    consumed = {
        ("game_result", "full_game"),
        ("inning_result", "F5"),
        ("game_total", "full_game"),
        ("team_total", "full_game"),
        ("winning_margin", "full_game"),
        ("first_inning_run", "F1"),
    }
    key = (classified["family"], classified["scope"])
    if key == ("inning_result", "F5") and classified.get("outcome") == "Tie":
        return "data_captured_never_evaluated"
    return "consumed_by_production" if key in consumed else "not_currently_consumed"


def _model_support_status(classified):
    key = (classified["family"], classified["scope"])
    if key == ("inning_result", "F5") and classified.get("outcome") == "Tie":
        return "not_supported_dead_data_path"
    fully_supported = {("game_result", "full_game"), ("inning_result", "F5"),
                        ("game_total", "full_game"), ("team_total", "full_game"),
                        ("winning_margin", "full_game"), ("first_inning_run", "F1")}
    if key in fully_supported:
        return "fully_supported"
    if classified["family"] == "unknown":
        return "unclassified"
    return "not_supported"


def main():
    inventory = build_inventory()
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        json.dump(inventory, f, indent=2, sort_keys=True)
    print(f"Wrote {len(inventory['entries'])} entries to {os.path.relpath(OUTPUT_PATH, ROOT)}")
    print(f"Series observed: {inventory['seriesTickersObservedInLatestSnapshot']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
