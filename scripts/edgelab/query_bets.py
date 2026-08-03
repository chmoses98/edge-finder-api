#!/usr/bin/env python3
"""
scripts/edgelab/query_bets.py
=================================
Cross-chat read-only query CLI over the canonical placed-bet ledger
(data/edgelab/bets/bets.jsonl). Any project chat/tool that can shell out
should read through this (or lib.edgelab.query directly) rather than
relying on conversation memory or the recommendation list -- see
docs/CANONICAL_BET_LEDGER.md's cross-chat operating protocol.

Never writes anything.

Usage:
    python3 scripts/edgelab/query_bets.py --filter today [--date 2026-08-03]
    python3 scripts/edgelab/query_bets.py --filter unsettled
    python3 scripts/edgelab/query_bets.py --filter settled
    python3 scripts/edgelab/query_bets.py --filter date --date 2026-08-01
    python3 scripts/edgelab/query_bets.py --filter date-range --start 2026-07-01 --end 2026-07-31
    python3 scripts/edgelab/query_bets.py --filter market-family --market-family FAMILY_INNING_RESULT
    python3 scripts/edgelab/query_bets.py --filter game --game-id 776123
    python3 scripts/edgelab/query_bets.py --filter snapshot [--snapshot-id ...]
    python3 scripts/edgelab/query_bets.py --filter recommendation [--recommendation-id ...]
    python3 scripts/edgelab/query_bets.py --filter manual-no-model
    python3 scripts/edgelab/query_bets.py --filter bankroll-history [--include-legacy]
    python3 scripts/edgelab/query_bets.py --filter canonical-era-summary [--include-legacy]
    [--format json|human]

`bankroll-history` and `canonical-era-summary` are official/aggregate
reports: by default they only count bets on or after
lib.edgelab.canonical_era.CANONICAL_ERA_START_DATE. Pass --include-legacy
to see the full-history (legacy-inclusive) view instead -- clearly
labelled `legacyIncluded: true` in the output so it's never mistaken for
the official canonical-era figures. Every other filter is an unfiltered,
plain historical lookup (legacy rows remain fully queryable there,
unaffected by this flag).
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab import canonical_era, query, storage
from lib.edgelab.bankroll import compute_bankroll_summary
from lib.edgelab.reports import build_canonical_era_summary


def _load_bets():
    return list(storage.read_records(storage.singleton_path("bets", "bets.jsonl")))


def _load_bankroll_transactions():
    return list(storage.read_records(storage.singleton_path("bankroll", "transactions.jsonl")))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--filter", required=True, choices=[
        "today", "unsettled", "settled", "void", "date", "date-range",
        "market-family", "game", "snapshot", "recommendation",
        "manual-no-model", "bankroll-history", "canonical-era-summary", "all",
    ])
    parser.add_argument(
        "--include-legacy", action="store_true",
        help="For bankroll-history / canonical-era-summary only: include pre-canonical-era bets "
             "in the aggregate instead of the official canonical-era-only default.",
    )
    parser.add_argument("--date", default=None)
    parser.add_argument("--start", default=None)
    parser.add_argument("--end", default=None)
    parser.add_argument("--market-family", default=None)
    parser.add_argument("--game-id", default=None)
    parser.add_argument("--snapshot-id", default=None)
    parser.add_argument("--recommendation-id", default=None)
    parser.add_argument("--format", default="json", choices=["json", "human"])
    args = parser.parse_args()

    today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    bets = _load_bets()
    result = None

    if args.filter == "today":
        result = query.todays_card(bets, args.date or today)
    elif args.filter == "unsettled":
        result = query.unsettled(bets)
    elif args.filter == "settled":
        result = query.settled(bets)
    elif args.filter == "void":
        result = query.voided(bets)
    elif args.filter == "date":
        if not args.date:
            print("--date is required for --filter date", file=sys.stderr)
            return 1
        result = query.by_date(bets, args.date)
    elif args.filter == "date-range":
        if not args.start or not args.end:
            print("--start and --end are required for --filter date-range", file=sys.stderr)
            return 1
        result = query.by_date_range(bets, args.start, args.end)
    elif args.filter == "market-family":
        if not args.market_family:
            print("--market-family is required", file=sys.stderr)
            return 1
        result = query.by_market_family(bets, args.market_family)
    elif args.filter == "game":
        if not args.game_id:
            print("--game-id is required", file=sys.stderr)
            return 1
        result = query.by_game(bets, args.game_id)
    elif args.filter == "snapshot":
        result = query.linked_to_snapshot(bets, args.snapshot_id)
    elif args.filter == "recommendation":
        result = query.linked_to_recommendation(bets, args.recommendation_id)
    elif args.filter == "manual-no-model":
        result = query.manual_without_model_support(bets)
    elif args.filter == "bankroll-history":
        transactions = _load_bankroll_transactions()
        scoped_bets = bets if args.include_legacy else canonical_era.canonical_era_bets(bets)
        result = {
            "canonicalEraStartDate": canonical_era.CANONICAL_ERA_START_DATE,
            "legacyIncluded": args.include_legacy,
            "summary": compute_bankroll_summary(transactions, scoped_bets),
            "transactions": transactions,
        }
    elif args.filter == "canonical-era-summary":
        transactions = _load_bankroll_transactions()
        scoped_bets = bets if args.include_legacy else canonical_era.canonical_era_bets(bets)
        bankroll_summary = compute_bankroll_summary(transactions, scoped_bets) if transactions or scoped_bets else None
        result = build_canonical_era_summary(bets, bankroll_summary, include_legacy=args.include_legacy)
    elif args.filter == "all":
        result = bets

    if args.format == "human":
        if isinstance(result, dict) and "bets" in result:
            print(query.render_human(result["bets"], title=f"Bet card"))
            print(json.dumps({k: v for k, v in result.items() if k != "bets"}, indent=2, sort_keys=True))
        elif isinstance(result, list):
            print(query.render_human(result, title=f"Bets ({args.filter})"))
        else:
            print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(json.dumps(result, indent=2, sort_keys=True))

    return 0


if __name__ == "__main__":
    sys.exit(main())
