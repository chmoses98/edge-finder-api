#!/usr/bin/env python3
"""
scripts/edgelab/import_2026_08_03_manual_bets.py
====================================================
One-time-and-repeatable bulk import of the 12 real, chat-confirmed
wagers placed on 2026-08-03 (source file:
data/manual_imports/2026-08-03_chat_confirmed_bets.json).

This is a thin bulk wrapper, not a second write path: every row is
built with lib.edgelab.bets.build_manual_bet_record and written with
lib.edgelab.bets.write_placed_bet -- the same canonical function
scripts/edgelab/log_bet.py and the "Record Placed Bet" GitHub Actions
form use. It never appends to data/edgelab/bets/bets.jsonl directly.

Before writing, each row's marketTicker is confirmed against the
2026-08-03 market registry (data/edgelab/markets/2026-08-03.jsonl,
itself sourced from data/kalshi_registry_snapshots/) -- a ticker with
no matching registry row is skipped and reported, never guessed or
silently written.

Idempotent: write_placed_bet upserts by betId (sha1 of gameId +
marketTicker + entryTimestamp), so re-running this script against an
unchanged source file and unchanged registry always yields
duplicateStatus=DUPLICATE_NOOP for all 12 rows -- never duplicate
ledger entries.

Usage:
    python3 scripts/edgelab/import_2026_08_03_manual_bets.py \\
        [--source data/manual_imports/2026-08-03_chat_confirmed_bets.json] \\
        [--markets-registry data/edgelab/markets/2026-08-03.jsonl] \\
        [--receipts-out receipts.json]
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab import storage
from lib.edgelab.bets import build_manual_bet_record, write_placed_bet

DEFAULT_SOURCE = "data/manual_imports/2026-08-03_chat_confirmed_bets.json"
DEFAULT_REGISTRY = "data/edgelab/markets/2026-08-03.jsonl"


def _load_registry(path):
    """marketTicker -> registry row, for the one authoritative ticker check."""
    by_ticker = {}
    for row in storage.read_records(path):
        ticker = row.get("marketTicker")
        if ticker and ticker not in by_ticker:
            by_ticker[ticker] = row
    return by_ticker


def import_bets(source_path, registry_path):
    """
    Returns (receipts, skipped) where receipts is a list of
    write_placed_bet() receipts (one per verified row, in source order)
    and skipped is a list of {"marketTicker", "selection", "reason"}
    dicts for any row whose ticker could not be verified against the
    registry -- those rows are never written.
    """
    with open(source_path) as f:
        payload = json.load(f)

    registry = _load_registry(registry_path)

    receipts = []
    skipped = []
    for bet in payload["placedBets"]:
        ticker = bet["marketTicker"]
        market = registry.get(ticker)
        if market is None:
            skipped.append({
                "marketTicker": ticker,
                "selection": bet.get("selection"),
                "reason": f"marketTicker not found in {registry_path}; left unwritten",
            })
            continue

        record = build_manual_bet_record(
            ticker,
            bet["selection"],
            bet["stake"],
            bet["entryPrice"],
            bet["entryTimestamp"],
            game_id=market.get("gameId"),
            game_date=bet.get("gameDate"),
            matchup=bet.get("matchup"),
            event_ticker=market.get("eventTicker"),
            series_ticker=market.get("seriesTicker"),
            market_family=bet.get("marketFamily"),
            market_horizon=bet.get("marketHorizon"),
            side=bet.get("side", "YES"),
            threshold=bet.get("threshold"),
            entry_odds=bet.get("entryOdds"),
            source=bet.get("source", "MANUAL"),
            entry_method=bet.get("entryMethod", "MANUAL_CHAT_CONFIRMED"),
            tracking_type=bet.get("trackingType"),
            rationale=bet.get("rationale"),
        )
        receipts.append(write_placed_bet(record))

    return receipts, skipped


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", default=DEFAULT_SOURCE)
    parser.add_argument("--markets-registry", default=DEFAULT_REGISTRY)
    parser.add_argument("--receipts-out", default=None)
    args = parser.parse_args()

    receipts, skipped = import_bets(args.source, args.markets_registry)

    print(json.dumps({"receipts": receipts, "skipped": skipped}, indent=2, sort_keys=True))
    if args.receipts_out:
        with open(args.receipts_out, "w") as f:
            json.dump({"receipts": receipts, "skipped": skipped}, f, indent=2, sort_keys=True)

    failed = [r for r in receipts if not r["success"]]
    print(
        f"[import_2026_08_03_manual_bets] {len(receipts)} rows verified+processed, "
        f"{len(receipts) - len(failed)} written/no-op, {len(failed)} failed, "
        f"{len(skipped)} skipped (unverifiable ticker)",
        file=sys.stderr,
    )
    if skipped:
        for s in skipped:
            print(f"[import_2026_08_03_manual_bets] SKIPPED: {s['marketTicker']} ({s['selection']}): {s['reason']}", file=sys.stderr)

    return 1 if (failed or skipped) else 0


if __name__ == "__main__":
    sys.exit(main())
