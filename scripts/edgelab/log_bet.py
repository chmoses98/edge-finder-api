#!/usr/bin/env python3
"""
scripts/edgelab/log_bet.py
=============================
Manual entry point for the EdgeLab canonical bet ledger (Phase 1 section
E; hardened by the Canonical Placed-Bet Ledger milestone). Supports
logging a bet from chat/manual analysis with only the fields needed to
identify the exact contract and stake -- every analytical field is
optional. Writes exclusively through lib.edgelab.bets.write_placed_bet
(the one canonical write function -- see its docstring), so duplicate
retries, tranches, and conflicting duplicates are handled identically
here and in the "Record Placed Bet" GitHub Actions form.

Usage:
    python3 scripts/edgelab/log_bet.py \\
        --ticker KXMLBF5-26JUL312140DETATH-DET \\
        --selection "DET F5 moneyline" \\
        --stake 5 --entry-price 0.505 \\
        --entry-timestamp 2026-07-31T22:38:09Z \\
        [--game-id ... --source MANUAL --entry-method MANUAL_CHAT_CONFIRMED \\
         --confidence MEDIUM --tag STARTER_EDGE --tag BULLPEN_EDGE] \\
        [--rationale "..."] [--receipt-out receipt.json] [--on-conflict overwrite]

Exit codes: 0 on a successful write (NEW/DUPLICATE_NOOP/CORRECTED),
1 on schema-invalid input or an unresolved CONFLICT.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab import tags as tags_mod
from lib.edgelab.bets import build_manual_bet_record, write_placed_bet


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ticker", required=True, help="Exact Kalshi market ticker")
    parser.add_argument("--selection", required=True, help="Human-readable description of what was bought")
    parser.add_argument("--stake", required=True, type=float)
    parser.add_argument("--entry-price", required=True, type=float, help="0-1 implied probability")
    parser.add_argument("--entry-timestamp", required=True, help="ISO 8601 UTC")
    parser.add_argument("--game-id", default=None)
    parser.add_argument("--game-date", default=None)
    parser.add_argument("--matchup", default=None, help="e.g. 'DET @ ATH'")
    parser.add_argument("--event-ticker", default=None)
    parser.add_argument("--series-ticker", default=None)
    parser.add_argument("--market-family", default=None)
    parser.add_argument("--market-horizon", default=None, choices=["FULL_GAME", "F3", "F5", "F7"])
    parser.add_argument("--side", default="YES", choices=["YES", "NO"])
    parser.add_argument("--threshold", type=float, default=None)
    parser.add_argument("--contracts", type=float, default=None)
    parser.add_argument("--scheduled-start", default=None)
    parser.add_argument("--entry-odds", type=float, default=None)
    parser.add_argument("--source", default="MANUAL", choices=["MANUAL", "MODEL", "OTHER"])
    parser.add_argument("--entry-method", default="MANUAL_CHAT_CONFIRMED",
                         choices=["MANUAL_GITHUB_FORM", "MANUAL_CHAT_CONFIRMED",
                                  "PRODUCTION_RECOMMENDATION_CONFIRMED", "LEGACY_BACKFILL", "IMPORTED_RECEIPT"])
    parser.add_argument("--recommendation-id", default=None)
    parser.add_argument("--production-run-id", default=None)
    parser.add_argument("--snapshot-id", default=None)
    parser.add_argument("--manual-fair-probability", type=float, default=None)
    parser.add_argument("--model-fair-probability", type=float, default=None)
    # No --model-supported / --model-evaluation-id here on purpose: this is
    # a manual-entry surface, and modelSupported=True requires a real
    # modelEvaluationId (enforced by build_manual_bet_record) -- that link
    # is only ever established later by scripts/edgelab/build_recommendations.py's
    # link_bets_to_recommendations() backfill, never claimed at entry time
    # (maintainer review finding: this flag previously existed with no
    # matching --model-evaluation-id, letting a purely manual bet falsely
    # claim model backing).
    parser.add_argument("--confidence", default=None)
    parser.add_argument("--data-quality", default=None)
    parser.add_argument("--correlation-group", action="append", dest="correlation_groups", default=[])
    parser.add_argument("--tracking-type", default=None, choices=["REAL", "PAPER", "REAL_PROBE"])
    parser.add_argument("--tag", action="append", dest="tags", default=[], help="Thesis tag; repeatable")
    parser.add_argument("--rationale", default=None)
    parser.add_argument("--on-conflict", default="reject", choices=["reject", "overwrite"],
                         help="reject (default): refuse to overwrite a same-betId row with different content. "
                              "overwrite: explicit correction, marks recordStatus=CORRECTED.")
    parser.add_argument("--receipt-out", default=None, help="Write the JSON receipt to this path in addition to stdout")
    args = parser.parse_args()

    if args.tags:
        tags_mod.validate_tags(args.tags)

    estimated_edge = None
    if args.model_fair_probability is not None:
        implied = args.entry_price * 100
        estimated_edge = round(args.model_fair_probability - implied, 4)

    record = build_manual_bet_record(
        args.ticker, args.selection, args.stake, args.entry_price, args.entry_timestamp,
        game_id=args.game_id, game_date=args.game_date, matchup=args.matchup,
        event_ticker=args.event_ticker, series_ticker=args.series_ticker,
        market_family=args.market_family, market_horizon=args.market_horizon,
        side=args.side, threshold=args.threshold,
        contracts=args.contracts, scheduled_start=args.scheduled_start, entry_odds=args.entry_odds,
        source=args.source, entry_method=args.entry_method,
        recommendation_id=args.recommendation_id, production_run_id=args.production_run_id,
        snapshot_id=args.snapshot_id,
        manual_fair_probability=args.manual_fair_probability,
        model_fair_probability=args.model_fair_probability, estimated_edge_at_entry=estimated_edge,
        confidence=args.confidence, data_quality=args.data_quality,
        correlation_groups=args.correlation_groups, tracking_type=args.tracking_type,
        thesis_tags=args.tags, rationale=args.rationale,
    )

    receipt = write_placed_bet(record, on_conflict=args.on_conflict)

    print(json.dumps(receipt, indent=2, sort_keys=True))
    if args.receipt_out:
        with open(args.receipt_out, "w") as f:
            json.dump(receipt, f, indent=2, sort_keys=True)

    if not receipt["success"]:
        print(f"[log_bet] NOT written: duplicateStatus={receipt['duplicateStatus']}", file=sys.stderr)
        return 1

    print(f"[log_bet] betId={receipt['betId']} duplicateStatus={receipt['duplicateStatus']}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
