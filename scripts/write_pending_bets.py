#!/usr/bin/env python3
"""
scripts/write_pending_bets.py v1.0
====================================
After protect_slate.py runs, reads every accepted real-money bet from
data/slate.json's per-game marketLedger[] and appends it to bets.json
as a pending entry.

Real-money = status "Accepted" AND confidenceTier in ("HIGH", "MEDIUM").

IDEMPOTENT: uses a stable composite key so re-running never duplicates.
  key = date + "|" + game + "|" + market + "|" + ticker

If actualEntryPrice (executable slate-time price) is missing, the bet is
written with a DATA_HEALTH_WARNING flag and realMoneyBlocked=True — not
silently dropped, but marked as CLV-uncapturable.

Exit codes:
  0 — success (all bets written or already present)
  1 — hard failure (slate date mismatch, bets.json unreadable, etc.)
"""

import json
import os
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SLATE_PATH = os.path.join(ROOT, 'data', 'slate.json')
BETS_PATH  = os.path.join(ROOT, 'bets.json')

REAL_MONEY_TIERS = {'HIGH', 'MEDIUM'}


def load_json(path):
    with open(path) as f:
        return json.load(f)


def stable_key(date, game, market, ticker):
    return f"{date}|{game}|{market}|{ticker or 'NO_TICKER'}"


def american_to_decimal_entry(american):
    """Return entry probability (0-1) from American odds."""
    if american is None:
        return None
    try:
        o = float(american)
        if o >= 0:
            return round(100 / (o + 100), 4)
        else:
            return round(abs(o) / (abs(o) + 100), 4)
    except (TypeError, ValueError):
        return None


def build_bet_record(date, game, entry, now_ts):
    """Build a pending bet record from a marketLedger entry."""
    mkt    = entry.get('market', '')
    tier   = (entry.get('confidenceTier') or entry.get('confidence') or '').upper()
    ticker = entry.get('ticker') or entry.get('marketTicker')

    # Derive side: TT_Away_Over → away team abbr, F5_ML_Away → away abbr, etc.
    parts   = game.split('@')
    away_ab = parts[0].strip() if len(parts) == 2 else ''
    home_ab = parts[1].strip() if len(parts) == 2 else ''
    if 'Away' in mkt:
        side = away_ab
        bet_side = 'AWAY'
    elif 'Home' in mkt:
        side = home_ab
        bet_side = 'HOME'
    else:
        side = mkt
        bet_side = mkt

    # Entry price: use kalshiPrice (American odds) → convert to 0-1 prob
    kalshi_price = entry.get('kalshiPrice')
    exec_price   = entry.get('executablePriceUsed') or entry.get('executablePriceAtOutput')
    # executablePriceUsed is in implied_pct (0-100), convert to 0-1
    if exec_price is not None:
        actual_entry = round(float(exec_price) / 100.0, 4)
    elif kalshi_price is not None:
        actual_entry = american_to_decimal_entry(kalshi_price)
    else:
        actual_entry = None

    clv_blocked = actual_entry is None

    line = entry.get('line')
    required_wins = None
    if 'TT' in mkt and line is not None:
        required_wins = int(line) + 1  # Over N means N+1 runs

    record = {
        'date':              date,
        'game':              game,
        'market':            mkt,
        'side':              side,
        'betSide':           bet_side,
        'confidenceTier':    tier,
        'edgePct':           entry.get('edge') or entry.get('calibratedEdgeVsExecutable'),
        'stake':             entry.get('betSize'),
        'betSize':           entry.get('betSize'),
        'odds':              kalshi_price,
        'kalshiPrice':       kalshi_price,
        'actualEntryPrice':  actual_entry,
        'entryTimestamp':    now_ts,
        'ticker':            ticker,
        'marketIdentity':    entry.get('seriesTicker') or (ticker.split('-')[0] if ticker else None),
        'scheduledStartTime': entry.get('scheduledStartTime'),
        'line':              line,
        'requiredRunsToWin': required_wins,
        'awayProjRuns':      entry.get('awayProjRuns'),
        'homeProjRuns':      entry.get('homeProjRuns'),
        'modelProb':         entry.get('modelProb'),
        'marketImpliedProb': entry.get('kalshiImplied') or entry.get('kalshiVF'),
        'status':            'pending',
        'result':            None,
        'pnl':               None,
        'source':            'data/slate.json',
        'createdBy':         'write_pending_bets.py',
        'realMoneyBlocked':  clv_blocked,
    }
    if clv_blocked:
        record['dataHealthWarning'] = 'actualEntryPrice_null_CLV_uncapturable'

    return record


def main():
    now_ts = datetime.now(tz=timezone.utc).isoformat()

    # ── Load slate ─────────────────────────────────────────────────────────
    if not os.path.exists(SLATE_PATH):
        print(f"ERROR: {SLATE_PATH} not found")
        sys.exit(1)

    slate = load_json(SLATE_PATH)
    date  = slate.get('date')
    if not date:
        print("ERROR: slate.json has no 'date' field")
        sys.exit(1)
    print(f"[write_pending_bets] Slate date: {date}")

    # ── Load bets.json ────────────────────────────────────────────────────
    if os.path.exists(BETS_PATH):
        bets = load_json(BETS_PATH)
        if not isinstance(bets, list):
            print(f"ERROR: bets.json is not a list")
            sys.exit(1)
    else:
        bets = []
        print("  bets.json not found — will create")

    # Build existing key set for idempotency
    existing_keys = set()
    for b in bets:
        k = stable_key(
            b.get('date', ''),
            b.get('game', ''),
            b.get('market', ''),
            b.get('ticker') or b.get('marketTicker', ''),
        )
        existing_keys.add(k)

    # ── Scan marketLedger for real-money bets ─────────────────────────────
    new_bets  = []
    skipped   = 0
    no_ticker = 0

    for g in slate.get('games', []):
        # Skip quarantined games — no real-money bets can come from them
        if g.get('excludedFromSlate'):
            continue
        away = g.get('away', {}).get('abbr', '')
        home = g.get('home', {}).get('abbr', '')
        game = f"{away}@{home}"

        for entry in g.get('marketLedger', []):
            if entry.get('status') != 'Accepted':
                continue
            tier = (entry.get('confidenceTier') or entry.get('confidence') or '').upper()
            if tier not in REAL_MONEY_TIERS:
                continue  # PAPER — not logged here

            ticker = entry.get('ticker') or entry.get('marketTicker')
            key = stable_key(date, game, entry.get('market', ''), ticker)

            if key in existing_keys:
                skipped += 1
                continue

            if not ticker:
                no_ticker += 1
                print(f"  WARNING: {game} {entry.get('market')} has no ticker — marking CLV-uncapturable")

            record = build_bet_record(date, game, entry, now_ts)
            new_bets.append(record)
            existing_keys.add(key)

    # ── Write ─────────────────────────────────────────────────────────────
    if new_bets:
        bets.extend(new_bets)
        with open(BETS_PATH, 'w') as f:
            json.dump(bets, f, indent=2)
        print(f"  Written {len(new_bets)} new pending bets to bets.json")
    else:
        print(f"  No new bets to write ({skipped} already present)")

    if no_ticker:
        print(f"  WARNING: {no_ticker} bets have no ticker — CLV will be unavailable")

    clv_blocked = sum(1 for b in new_bets if b.get('realMoneyBlocked'))
    if clv_blocked:
        print(f"  WARNING: {clv_blocked} bets written with realMoneyBlocked=True (no entry price)")

    print(f"  Done. Total bets in ledger: {len(bets)}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
