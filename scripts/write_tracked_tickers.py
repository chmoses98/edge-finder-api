#!/usr/bin/env python3
"""
scripts/write_tracked_tickers.py v1.0
========================================
Extracts tickers from accepted real-money bets and writes:
  data/clv_snapshots/YYYY-MM-DD/tracked_tickers.json

This file is consumed by capture_clv_pregame.py (runs every 10 min via
clv_capture.yml) to snapshot live prices before first pitch.

IDEMPOTENT: safe to re-run; merges with any existing ticker list.

If any real-money bet has no ticker, exits 1 unless --warn-only is passed.

Exit 0 — success
Exit 1 — any real-money bet is missing ticker (CLV cannot be tracked)
"""

import json
import os
import sys
from datetime import datetime, timezone

ROOT        = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SLATE_PATH  = os.path.join(ROOT, 'data', 'slate.json')
BETS_PATH   = os.path.join(ROOT, 'bets.json')
SNAP_DIR    = os.path.join(ROOT, 'data', 'clv_snapshots')

REAL_MONEY_TIERS = {'HIGH', 'MEDIUM'}
WARN_ONLY = '--warn-only' in sys.argv


def main():
    now_ts = datetime.now(tz=timezone.utc).isoformat()

    # ── Load slate ─────────────────────────────────────────────────────────
    if not os.path.exists(SLATE_PATH):
        print(f"ERROR: {SLATE_PATH} not found"); sys.exit(1)
    with open(SLATE_PATH) as f:
        slate = json.load(f)
    date = slate.get('date', '')
    if not date:
        print("ERROR: slate.json has no 'date' field"); sys.exit(1)

    # ── Collect tickers from slate marketLedger ────────────────────────────
    tickers = {}          # ticker -> record
    missing_ticker = []

    for g in slate.get('games', []):
        away = g.get('away', {}).get('abbr', '')
        home = g.get('home', {}).get('abbr', '')
        game = f"{away}@{home}"
        start = g.get('startTime', '')

        for entry in g.get('marketLedger', []):
            if entry.get('status') != 'Accepted':
                continue
            tier = (entry.get('confidenceTier') or entry.get('confidence') or '').upper()
            if tier not in REAL_MONEY_TIERS:
                continue

            ticker = entry.get('ticker') or entry.get('marketTicker')
            mkt    = entry.get('market', '')

            if not ticker:
                missing_ticker.append(f"{game} {mkt} tier={tier}")
                continue

            if ticker not in tickers:
                tickers[ticker] = {
                    'ticker':           ticker,
                    'seriesTicker':     entry.get('seriesTicker') or (ticker.split('-')[0] if ticker else None),
                    'market':           mkt,
                    'game':             game,
                    'date':             date,
                    'scheduledStartTime': entry.get('scheduledStartTime') or start,
                    'confidenceTier':   tier,
                    'stake':            entry.get('betSize'),
                    'addedAt':          now_ts,
                }

    print(f"[write_tracked_tickers] Date: {date}")
    print(f"  Real-money tickers found: {len(tickers)}")

    if missing_ticker:
        print(f"  MISSING TICKER ({len(missing_ticker)} bets):")
        for m in missing_ticker:
            print(f"    {m}")
        if not WARN_ONLY:
            print("  FAIL: Real-money bets without tickers cannot be CLV-tracked.")
            print("  Pass --warn-only to suppress this exit.")
            sys.exit(1)

    if not tickers:
        print("  No real-money tickers to write.")
        return 0

    # ── Write tracked_tickers.json ────────────────────────────────────────
    snap_date_dir = os.path.join(SNAP_DIR, date)
    os.makedirs(snap_date_dir, exist_ok=True)
    out_path = os.path.join(snap_date_dir, 'tracked_tickers.json')

    # Merge with existing if present (idempotent)
    existing = {}
    if os.path.exists(out_path):
        with open(out_path) as f:
            prev = json.load(f)
        for t in prev.get('tickers', []):
            existing[t['ticker']] = t

    existing.update(tickers)
    merged = sorted(existing.values(), key=lambda x: x['ticker'])

    out = {
        'date':       date,
        'generatedAt': now_ts,
        'count':       len(merged),
        'tickers':     merged,
    }
    with open(out_path, 'w') as f:
        json.dump(out, f, indent=2)

    print(f"  Written {len(merged)} tickers → {out_path}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
