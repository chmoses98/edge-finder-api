#!/usr/bin/env python3
"""
scripts/edgelab/cancel_bet.py
=================================
Mark a bet in the canonical ledger CANCELLED (logged in error) -- never
deletes the row, never touches its stake/price/settlement/CLV fields,
just excludes it from ROI/postmortem/bankroll aggregation while
preserving the audit trail. See lib.edgelab.bets.cancel_placed_bet.

Usage:
    python3 scripts/edgelab/cancel_bet.py --bet-id <sha1...> --reason "Logged in error, never actually placed"
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab.bets import cancel_placed_bet


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--bet-id", required=True)
    parser.add_argument("--reason", required=True)
    args = parser.parse_args()

    try:
        result = cancel_placed_bet(args.bet_id, args.reason)
    except ValueError as e:
        print(f"[cancel_bet] {e}", file=sys.stderr)
        return 1

    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
