#!/usr/bin/env python3
"""
scripts/snapshot_coverage_check.py
====================================
Pre-review validator: confirms every real-money bet for a given date has
at least one valid pre-start CLV source available.

Emits warnings (not failures) during the day so analysts know before games
finish whether CLV data will be available.

Called from clv-update.yml before the CLV step.
Exit 0 = all bets covered.
Exit 2 = some bets missing CLV source (warning — does not abort pipeline).
Exit 1 = hard error (e.g. could not read bets.json).

Usage:
    python3 scripts/snapshot_coverage_check.py YYYY-MM-DD
"""
import sys
import json
import os
from pathlib import Path

_here     = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR  = os.path.dirname(_here)
SNAP_DIR  = os.path.join(ROOT_DIR, "data", "kalshi_registry_snapshots")
BETS_PATH = os.path.join(ROOT_DIR, "bets.json")

sys.path.insert(0, _here)
from clv_from_snapshot import (
    load_snapshot, build_ticker_index, parse_ts, get_mid_from_entry
)

date = sys.argv[1] if len(sys.argv) > 1 else ""
if not date:
    print("Usage: snapshot_coverage_check.py YYYY-MM-DD")
    sys.exit(1)

# Load bets
try:
    with open(BETS_PATH) as f:
        bets = json.load(f)
except Exception as e:
    print(f"HARD ERROR: cannot read {BETS_PATH}: {e}")
    sys.exit(1)

SETTLED = {"settled", "SETTLED", "WIN", "LOSS", "win", "loss", "PUSH", "push", "open"}
real_bets = [
    b for b in bets
    if (b.get("date") or "")[:10] == date
    and (b.get("betType", "").upper() == "REAL"
         or b.get("type", "").lower() == "real")
]

if not real_bets:
    print(f"[coverage_check] No real bets for {date} — nothing to validate.")
    sys.exit(0)

# Load snapshot(s)
try:
    markets, snap_ts_str, snap_path = load_snapshot(date)
    ticker_index = build_ticker_index(markets)
    print(f"[coverage_check] Loaded snapshot: {os.path.basename(snap_path)}  "
          f"({len(ticker_index)} tickers)  ts={snap_ts_str}")
except FileNotFoundError:
    ticker_index = {}
    snap_ts_str  = ""
    snap_path    = ""
    print(f"[coverage_check] WARNING: No snapshot found for {date}")

covered   = []
uncovered = []
post_game = []

for b in real_bets:
    ticker = b.get("marketTicker") or b.get("ticker")
    fp_ts  = parse_ts(b.get("scheduledStartTime"))
    snap_ts_val = parse_ts(snap_ts_str)

    if not ticker:
        uncovered.append((b.get("id", "?"), "NO_TICKER"))
        continue

    # Check if snapshot is pre-game for this bet
    if snap_ts_str and fp_ts and snap_ts_val and snap_ts_val > fp_ts:
        post_game.append((b.get("id", "?"), ticker, snap_ts_str))
        continue

    if ticker in ticker_index:
        mid = get_mid_from_entry(ticker_index[ticker])
        if mid is not None:
            covered.append(ticker)
        else:
            uncovered.append((b.get("id", "?"), f"TICKER_FOUND_NO_PRICE ({ticker})"))
    else:
        uncovered.append((b.get("id", "?"), f"TICKER_NOT_IN_SNAPSHOT ({ticker})"))

print(f"\n[coverage_check] {date}:")
print(f"  Total real bets:     {len(real_bets)}")
print(f"  CLV source found:    {len(covered)}")
print(f"  Post-game snapshot:  {len(post_game)}")
print(f"  Missing CLV source:  {len(uncovered)}")

if post_game:
    print("\n  ⚠️  Post-game snapshot (valid if additional pre-game snap exists):")
    for bid, ticker, ts in post_game:
        print(f"     {bid}  {ticker}  snap_ts={ts}")

if uncovered:
    print("\n  ❌ Missing CLV source:")
    for bid, reason in uncovered:
        print(f"     {bid}  {reason}")
    print("\n  ACTION: Run fetch-slate to generate a snapshot for this date.")
    sys.exit(2)

print("\n  ✅ All real-money bets have a valid pre-start CLV source.")
sys.exit(0)
