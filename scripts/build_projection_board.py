#!/usr/bin/env python3
"""
scripts/build_projection_board.py
====================================
Stage 1 pre-gate full-market projection board for game-derived MLB
Kalshi markets. This script is I/O only -- all board/model logic lives
in lib/kalshi_projection_board.py, which reuses
scripts/discover_kalshi_mlb_markets.py's contract discovery/pricing
(itself built on scripts/build_market_ledger.py's Poisson engine and
lib.research.three_way_projection's F3/F5/F7 model) -- no new
statistical model is introduced anywhere in this chain.

Run AFTER scripts/build_market_ledger.py (each game's marketLedger must
already be computed for advisory automated-recommendation linkage) and
after data/kalshi_search.json has been fetched for the day.

Writes data/pipeline/<date>/projection_board.json via
lib.pipeline_artifacts.write_stage_artifact() -- purely additive. Never
touches data/slate.json, bets.json, config/rules.json, or any
settlement/staking/risk-gate file. Never fails the pipeline: a missing
input file or write error is reported and the script exits 0, mirroring
scripts/discover_kalshi_mlb_markets.py's own failure posture for this
kind of analysis-only, non-required artifact.
"""
import json
import os
import sys

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPTS_DIR)
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from scripts.discover_kalshi_mlb_markets import (  # noqa: E402
    discover, load_json, DEFAULT_SEARCH_PATH, DEFAULT_SLATE_PATH,
)
from lib.kalshi_projection_board import build_projection_board  # noqa: E402
from lib.pipeline_artifacts import write_stage_artifact  # noqa: E402


def main(date_str=None, search_path=None, slate_path=None, dry_run=False):
    search_path = search_path or DEFAULT_SEARCH_PATH
    slate_path = slate_path or DEFAULT_SLATE_PATH

    try:
        search_doc = load_json(search_path)
    except FileNotFoundError:
        print(f"[build_projection_board] No search file at {search_path} — nothing to project")
        return {"date": date_str, "status": "NO_SEARCH_FILE", "totalRows": 0}

    date_str = date_str or search_doc.get("date")
    try:
        slate_doc = load_json(slate_path)
    except FileNotFoundError:
        print(f"[build_projection_board] No slate file at {slate_path} — projecting with no game context")
        slate_doc = {"games": []}

    contracts, _disc_summary = discover(date_str, search_doc, slate_doc)
    rows, summary = build_projection_board(date_str, contracts, slate_doc.get("games") or [])

    if not dry_run and date_str:
        try:
            path = write_stage_artifact(
                "projection_board", date_str, {"rows": rows, "summary": summary},
                produced_by="scripts/build_projection_board.py",
                source_stage="market_ledger",
            )
            summary = dict(summary, artifactPath=path)
        except Exception as e:
            print(f"[build_projection_board] WARNING: failed to write pipeline artifact: {e}")
            summary = dict(summary, artifactWriteError=str(e))

    return summary


if __name__ == "__main__":
    arg_date = sys.argv[1] if len(sys.argv) > 1 else None
    result = main(date_str=arg_date)
    print(json.dumps(result, indent=2))
