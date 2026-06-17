#!/usr/bin/env python3
"""
scripts/validate_bet_logging.py v1.0
=====================================
Hard gate: counts accepted real-money bets in data/slate.json marketLedger
and confirms each has a matching pending entry in bets.json.

Exit 0 — counts match, all bets are logged
Exit 1 — any ledger bet is absent from bets.json

A real-money official slate MUST NOT commit if any bet is unlogged.
"""

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SLATE_PATH = os.path.join(ROOT, 'data', 'slate.json')
BETS_PATH  = os.path.join(ROOT, 'bets.json')

REAL_MONEY_TIERS = {'HIGH', 'MEDIUM'}


def stable_key(date, game, market, ticker):
    return f"{date}|{game}|{market}|{ticker or 'NO_TICKER'}"


def main():
    # ── Load slate ─────────────────────────────────────────────────────────
    if not os.path.exists(SLATE_PATH):
        print(f"GATE FAIL: {SLATE_PATH} not found")
        sys.exit(1)
    with open(SLATE_PATH) as f:
        slate = json.load(f)
    date = slate.get('date', '')

    # ── Load bets.json ────────────────────────────────────────────────────
    if not os.path.exists(BETS_PATH):
        print(f"GATE FAIL: bets.json not found")
        sys.exit(1)
    with open(BETS_PATH) as f:
        bets = json.load(f)

    # Build key set from bets.json for this date
    logged_keys = set()
    for b in bets:
        if b.get('date') != date:
            continue
        k = stable_key(
            b.get('date', ''),
            b.get('game', ''),
            b.get('market', ''),
            b.get('ticker') or b.get('marketTicker', ''),
        )
        logged_keys.add(k)

    # ── Check every real-money ledger entry ───────────────────────────────
    expected = []
    for g in slate.get('games', []):
        away = g.get('away', {}).get('abbr', '')
        home = g.get('home', {}).get('abbr', '')
        game = f"{away}@{home}"
        for entry in g.get('marketLedger', []):
            if entry.get('status') != 'Accepted':
                continue
            tier = (entry.get('confidenceTier') or entry.get('confidence') or '').upper()
            if tier not in REAL_MONEY_TIERS:
                continue
            ticker = entry.get('ticker') or entry.get('marketTicker')
            key = stable_key(date, game, entry.get('market', ''), ticker)
            expected.append({
                'key': key,
                'game': game,
                'market': entry.get('market', ''),
                'side': entry.get('side', ''),
                'ticker': ticker,
                'stake': entry.get('betSize'),
                'tier': tier,
            })

    print(f"[validate_bet_logging] Slate date: {date}")
    print(f"  Expected real-money bets in ledger:  {len(expected)}")
    print(f"  Logged in bets.json for {date}:       {len(logged_keys)}")

    missing = [e for e in expected if e['key'] not in logged_keys]

    if missing:
        print(f"\n  GATE FAIL — {len(missing)} bets NOT in bets.json:")
        for m in missing:
            print(f"    game={m['game']} market={m['market']} tier={m['tier']} "
                  f"stake={m['stake']}u ticker={m['ticker']}")
        print("\n  Run scripts/write_pending_bets.py to fix before committing.")
        sys.exit(1)

    print(f"  GATE PASS — all {len(expected)} real-money bets are logged")
    return 0


if __name__ == '__main__':
    sys.exit(main())
