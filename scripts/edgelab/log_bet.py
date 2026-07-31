#!/usr/bin/env python3
"""
scripts/edgelab/log_bet.py
=============================
Manual entry point for the EdgeLab canonical bet ledger (Phase 1 section
E). Supports logging a bet from chat/manual analysis with only the
fields needed to identify the exact contract and stake -- every
analytical field is optional.

Usage:
    python3 scripts/edgelab/log_bet.py \\
        --ticker KXMLBF5-26JUL312140DETATH-DET \\
        --selection "DET F5 moneyline" \\
        --stake 5 --entry-price 0.505 \\
        --entry-timestamp 2026-07-31T22:38:09Z \\
        [--game-id ... --source MANUAL --confidence MEDIUM --tag STARTER_EDGE --tag BULLPEN_EDGE] \\
        [--rationale "..."]
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab import schema, storage, tags as tags_mod
from lib.edgelab.bets import build_manual_bet_record


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ticker", required=True, help="Exact Kalshi market ticker")
    parser.add_argument("--selection", required=True, help="Human-readable description of what was bought")
    parser.add_argument("--stake", required=True, type=float)
    parser.add_argument("--entry-price", required=True, type=float, help="0-1 implied probability")
    parser.add_argument("--entry-timestamp", required=True, help="ISO 8601 UTC")
    parser.add_argument("--game-id", default=None)
    parser.add_argument("--event-ticker", default=None)
    parser.add_argument("--series-ticker", default=None)
    parser.add_argument("--market-family", default=None)
    parser.add_argument("--side", default="YES", choices=["YES", "NO"])
    parser.add_argument("--threshold", type=float, default=None)
    parser.add_argument("--contracts", type=float, default=None)
    parser.add_argument("--scheduled-start", default=None)
    parser.add_argument("--source", default="MANUAL", choices=["MANUAL", "MODEL", "OTHER"])
    parser.add_argument("--recommendation-id", default=None)
    parser.add_argument("--manual-fair-probability", type=float, default=None)
    parser.add_argument("--model-fair-probability", type=float, default=None)
    parser.add_argument("--confidence", default=None)
    parser.add_argument("--data-quality", default=None)
    parser.add_argument("--correlation-group", default=None)
    parser.add_argument("--tracking-type", default=None, choices=["REAL", "PAPER", "REAL_PROBE"])
    parser.add_argument("--tag", action="append", dest="tags", default=[], help="Thesis tag; repeatable")
    parser.add_argument("--rationale", default=None)
    args = parser.parse_args()

    if args.tags:
        tags_mod.validate_tags(args.tags)

    estimated_edge = None
    if args.model_fair_probability is not None:
        implied = args.entry_price * 100
        estimated_edge = round(args.model_fair_probability - implied, 4)

    record = build_manual_bet_record(
        args.ticker, args.selection, args.stake, args.entry_price, args.entry_timestamp,
        game_id=args.game_id, event_ticker=args.event_ticker, series_ticker=args.series_ticker,
        market_family=args.market_family, side=args.side, threshold=args.threshold,
        contracts=args.contracts, scheduled_start=args.scheduled_start, source=args.source,
        recommendation_id=args.recommendation_id, manual_fair_probability=args.manual_fair_probability,
        model_fair_probability=args.model_fair_probability, estimated_edge_at_entry=estimated_edge,
        confidence=args.confidence, data_quality=args.data_quality,
        correlation_group=args.correlation_group, tracking_type=args.tracking_type,
        thesis_tags=args.tags, rationale=args.rationale,
    )

    errors = schema.validate_record("placed_bet", record)
    if errors:
        print("[log_bet] record failed schema validation, NOT written:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1

    path = storage.singleton_path("bets", "bets.jsonl")
    updated, inserted = storage.upsert_records(path, [record], "betId")
    print(f"[log_bet] betId={record['betId']} inserted={inserted} updated={updated} -> {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
