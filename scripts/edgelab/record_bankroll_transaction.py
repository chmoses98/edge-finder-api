#!/usr/bin/env python3
"""
scripts/edgelab/record_bankroll_transaction.py
===================================================
Manual entry point for the canonical bankroll ledger
(data/edgelab/bankroll/transactions.jsonl). Tracking/observability only
-- see lib/edgelab/bankroll.py's module docstring for what this
deliberately does NOT do (size stakes, feed risk_gate.py, infer a real
account balance from recommendations or unsettled bets).

Usage:
    python3 scripts/edgelab/record_bankroll_transaction.py \\
        --type DEPOSIT --amount 500 --occurred-at 2026-08-03T13:00:00Z

    python3 scripts/edgelab/record_bankroll_transaction.py \\
        --type ADJUSTMENT --amount -12.50 --occurred-at 2026-08-03T13:00:00Z \\
        --reason "Kalshi fee correction"
"""
import argparse
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab.bankroll import build_bankroll_transaction, write_bankroll_transaction


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--type", required=True,
                         choices=["STARTING_BALANCE", "DEPOSIT", "WITHDRAWAL", "ADJUSTMENT", "USER_REPORTED_BALANCE"])
    parser.add_argument("--amount", required=True, type=float)
    parser.add_argument("--occurred-at", required=True, help="ISO 8601 UTC")
    parser.add_argument("--reason", default=None)
    parser.add_argument("--reference", default=None)
    parser.add_argument("--entered-by", default=None)
    parser.add_argument("--receipt-out", default=None)
    args = parser.parse_args()

    try:
        record = build_bankroll_transaction(
            args.type, args.amount, args.occurred_at,
            reason=args.reason, reference=args.reference, entered_by=args.entered_by,
        )
    except ValueError as e:
        print(f"[record_bankroll_transaction] {e}", file=sys.stderr)
        return 1

    receipt = write_bankroll_transaction(record)
    print(json.dumps(receipt, indent=2, sort_keys=True))
    if args.receipt_out:
        with open(args.receipt_out, "w") as f:
            json.dump(receipt, f, indent=2, sort_keys=True)

    if not receipt["success"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
