#!/usr/bin/env python3
"""
scripts/build_full_market_coverage.py
========================================
MLB slate coverage audit -- Phase 5/7/10 deliverable: a machine-readable,
full-archived-market-universe accounting artifact, separate from (and
never bloating) data/slate.json's recommendation-eligible marketLedger.

Wraps lib.kalshi_market_coverage.full_accounting() around the SAME data
scripts/discover_kalshi_mlb_markets.py already reads (data/kalshi_search.json
+ data/slate.json for the given date) -- this guarantees the coverage
ledger and the discovery artifact describe the exact same archived Kalshi
market observation, never a separately-fetched or re-classified copy
(docs/KALSHI_MLB_MARKET_COVERAGE_AUDIT.md Phase 5: "ensure both use the
same archived market universe for the same observation"). It additionally
best-effort loads the existing hitter research projection board
(data/pipeline/<date>/hitter_projection_board.json, built independently by
scripts/build_hitter_projection_board.py on its own schedule -- see
.github/workflows/hitter-snapshot-scheduler.yml) and links hitter-family
contracts to it by ticker, so a hitter prop with real research evidence is
never lumped in with a family that has no model anywhere. No new Kalshi
API calls, no hitter model recomputation.

Read/classify/write only -- like discover_kalshi_mlb_markets.py, this
script never touches data/slate.json, bets.json, marketLedger,
risk_gate.py, or write_pending_bets.py. It does not change which markets
are real-money eligible; it only reports, for every archived pregame MLB
contract, whether it was fully evaluated (production or research) or has
an explicit, named reason it wasn't -- never a silent gap.

Writes:
    data/pipeline/<date>/full_market_coverage.json  (immutable stage
        artifact, lib.pipeline_artifacts envelope)
    data/kalshi/discovery/<date>_coverage.json       (flat sibling file,
        matching discover_kalshi_mlb_markets.py's own <date>_summary.json
        convention)

Exit code is non-zero if EITHER coverage invariant is violated for the
date's ledger: the discover()-output-based unaccountedCount, or (the
stronger, independent check) raw_archive_accounting's
trueSilentRemainderCount. See lib.kalshi_market_coverage's module
docstring for why both exist and what each one actually proves.
"""
import json
import os
import sys

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPTS_DIR)
sys.path.insert(0, ROOT_DIR)

from lib.kalshi_market_coverage import full_accounting, load_hitter_projection_board  # noqa: E402
from lib.pipeline_artifacts import write_stage_artifact  # noqa: E402

DEFAULT_SEARCH_PATH = os.path.join(ROOT_DIR, "data", "kalshi_search.json")
DEFAULT_SLATE_PATH = os.path.join(ROOT_DIR, "data", "slate.json")
COVERAGE_DIR = os.path.join(ROOT_DIR, "data", "kalshi", "discovery")


def load_json(path):
    with open(path) as f:
        return json.load(f)


def print_ledger_report(date_str, result):
    coverage = result["coverageAccounting"]
    raw = result["rawArchiveAccounting"]
    pregame = result["pregameView"]

    print(f"\nFULL MARKET COVERAGE REPORT -- {date_str}")
    print("\n[Raw archive invariant -- independent of discover()'s own output]")
    print(f"  Raw entries seen        : {raw['totalRawEntriesSeen']}")
    print(f"  Entries without ticker  : {raw['entriesWithoutTicker']}")
    print(f"  Duplicate raw tickers   : {raw['duplicateRawTickerCount']}")
    print(f"  Raw archived unique     : {raw['rawArchivedUnique']}")
    print(f"  Accounted for           : {raw['accountedTickerCount']}")
    print(f"  TRUE SILENT REMAINDER   : {raw['trueSilentRemainderCount']}  <- must be 0")

    print("\n[Discover()-output accounting -- every returned contract's fate]")
    print(f"  Archived total : {coverage['archivedTotal']}")
    print(f"  Accounted for  : {coverage['accountedTotal']}")
    print(f"  Unaccounted    : {coverage['unaccountedCount']}  <- must be 0")
    for state in sorted(coverage["byState"]):
        print(f"    {state:26s}: {coverage['byState'][state]}")

    print("\n[Pregame-scoped view]")
    for key in sorted(pregame):
        print(f"  {key:32s}: {pregame[key]}")


def main(date_str=None, search_path=None, slate_path=None, out_dir=None,
         hitter_board_path=None, dry_run=False):
    search_path = search_path or DEFAULT_SEARCH_PATH
    slate_path = slate_path or DEFAULT_SLATE_PATH
    out_dir = out_dir or COVERAGE_DIR

    try:
        search_doc = load_json(search_path)
    except FileNotFoundError:
        print(f"[build_full_market_coverage] No search file at {search_path} -- nothing to audit")
        return {"date": date_str, "status": "NO_SEARCH_FILE"}

    date_str = date_str or search_doc.get("date")
    try:
        slate_doc = load_json(slate_path)
    except FileNotFoundError:
        slate_doc = {"games": []}

    hitter_board_data = load_hitter_projection_board(date_str, path=hitter_board_path)
    hitter_board_status = "LOADED" if hitter_board_data else "NOT_AVAILABLE"

    result = full_accounting(date_str, search_doc, slate_doc, hitter_board_data=hitter_board_data)
    ledger_rows = result["ledgerRows"]

    payload = {
        "date": date_str,
        "hitterResearchBoardStatus": hitter_board_status,
        "discoverySummary": result["discoverySummary"],
        "coverageAccounting": {k: v for k, v in result["coverageAccounting"].items() if k != "byFamilyState"},
        "byFamilyState": result["coverageAccounting"]["byFamilyState"],
        "rawArchiveAccounting": result["rawArchiveAccounting"],
        "pregameView": result["pregameView"],
    }

    if not dry_run:
        os.makedirs(out_dir, exist_ok=True)
        with open(os.path.join(out_dir, f"{date_str}_coverage.json"), "w") as f:
            json.dump({"date": date_str, "ledger": ledger_rows, **payload}, f, indent=2)
        try:
            write_stage_artifact(
                "full_market_coverage", date_str, payload,
                produced_by="scripts/build_full_market_coverage.py",
                status="canonical",
                source_stage="kalshi_mlb_discovery",
            )
        except Exception as e:
            print(f"WARNING: could not write full_market_coverage pipeline artifact: {e}")

    print_ledger_report(date_str, result)
    return {"date": date_str, "status": "OK", **payload}


if __name__ == "__main__":
    arg_date = sys.argv[1] if len(sys.argv) > 1 else None
    result = main(date_str=arg_date)
    if result.get("status") == "OK":
        raw_remainder = result["rawArchiveAccounting"]["trueSilentRemainderCount"]
        unaccounted = result["coverageAccounting"]["unaccountedCount"]
        if raw_remainder > 0 or unaccounted > 0:
            print(
                f"\nFAIL: rawArchiveAccounting.trueSilentRemainderCount={raw_remainder}, "
                f"coverageAccounting.unaccountedCount={unaccounted} -- coverage invariant violated.",
                file=sys.stderr,
            )
            sys.exit(1)
    sys.exit(0)
