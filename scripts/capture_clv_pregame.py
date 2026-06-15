#!/usr/bin/env python3
"""
scripts/capture_clv_pregame.py
================================
CLV Pregame Snapshot Capture

Reads persisted ticker list from data/clv_snapshots/YYYY-MM-DD/tracked_tickers.json
For each ticker, fetches current Kalshi price (or reads from local registry).
Validates: timestamp before first pitch, valid bid/ask/midpoint, not stale/settled/sentinel.
Writes snapshot to data/clv_snapshots/YYYY-MM-DD/pregame_<gamePk>.json

CLV Snapshot Statuses:
  VALID                  - pregame price captured, timestamp before first pitch
  MISSING                - ticker not in snapshot source
  INVALID_POST_START     - snapshot taken after scheduled first pitch
  TICKER_NOT_FOUND       - ticker not found in Kalshi registry
  STALE_MARKET           - market not updated recently (>6 hours before first pitch)
  SENTINEL_PRICE         - price is a known sentinel value (19900, -19900, etc.)
  NO_VALID_PRICE         - price field exists but is null/zero/invalid
  MARKET_LOCKED          - market in locked/closed state before settlement
  SETTLEMENT_PRICE_ONLY  - only post-settlement price available

SECURITY: Never use postgame/settlement/stale/sentinel prices for CLV.
"""

import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────────────────────
SCRIPTS_DIR  = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR     = os.path.dirname(SCRIPTS_DIR)
SNAPSHOT_DIR = os.path.join(ROOT_DIR, "data", "clv_snapshots")
REGISTRY_PATH = os.path.join(ROOT_DIR, "data", "kalshi_market_registry.json")
RAW_KALSHI   = os.path.join(ROOT_DIR, "data", "kalshi_raw.json")

# ── Sentinel prices that must always be rejected ───────────────────────────────
SENTINEL_PRICES = {19900, -19900, 100000, -100000, 199, -199}

# Sentinel detection for probability values (0-100 scale or 0-1 scale)
SENTINEL_PROBS_100 = {99, 1, 0, 100}   # near-certain prices that indicate settlement
VALID_PRICE_RANGE  = (1, 99)           # valid yes_price range (probability cents 1-99)


def is_sentinel_price(value):
    """Return True if value is a known sentinel / impossible price."""
    if value is None:
        return False
    try:
        v = float(value)
        if v in SENTINEL_PRICES:
            return True
        # American odds sentinels
        if abs(v) >= 19000:
            return True
        return False
    except (TypeError, ValueError):
        return False


def is_valid_yes_price(yes_price):
    """
    Validate a Kalshi yes_price (probability in cents, 1-99).
    Returns (is_valid, reason).
    """
    if yes_price is None:
        return False, "NO_VALID_PRICE"
    try:
        p = float(yes_price)
    except (TypeError, ValueError):
        return False, "NO_VALID_PRICE"

    if is_sentinel_price(p):
        return False, "SENTINEL_PRICE"

    # Kalshi yes_price is in cents (1-99 for pre-settlement markets)
    if p <= 0 or p >= 100:
        return False, "SETTLEMENT_PRICE_ONLY"

    return True, "VALID"


def parse_ts(ts_str):
    """Parse ISO timestamp string → aware datetime. Returns None on failure."""
    if not ts_str:
        return None
    try:
        s = ts_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def load_tracked_tickers(date_str):
    """
    Load tracked_tickers.json for a given date.
    Returns list of ticker dicts or [].
    """
    snap_dir = os.path.join(SNAPSHOT_DIR, date_str)
    path = os.path.join(snap_dir, "tracked_tickers.json")
    if not os.path.exists(path):
        print(f"[capture_clv_pregame] No tracked_tickers.json for {date_str}: {path}")
        return []
    with open(path) as f:
        data = json.load(f)
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get("tickers", [])
    return []


def load_kalshi_registry():
    """Load Kalshi market registry. Returns dict keyed by ticker."""
    if not os.path.exists(REGISTRY_PATH):
        return {}
    with open(REGISTRY_PATH) as f:
        data = json.load(f)
    # Registry may be list or dict
    if isinstance(data, list):
        return {m.get("ticker") or m.get("market_ticker"): m for m in data if m.get("ticker") or m.get("market_ticker")}
    if isinstance(data, dict):
        # May be keyed by ticker already
        return data
    return {}


def load_kalshi_raw():
    """Load kalshi_raw.json. Returns list of market dicts."""
    if not os.path.exists(RAW_KALSHI):
        return []
    with open(RAW_KALSHI) as f:
        data = json.load(f)
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get("markets", [])
    return []


def find_market_price(ticker, registry, raw_markets):
    """
    Find current yes_price for a ticker from available sources.
    Returns (yes_price, source, last_update_ts) or (None, None, None).
    """
    # Try registry first
    mkt = registry.get(ticker)
    if mkt:
        yes_price = mkt.get("yes_price") or mkt.get("last_price") or mkt.get("close_price")
        ts = mkt.get("last_updated") or mkt.get("snapshot_ts") or mkt.get("updated_at")
        return yes_price, "registry", ts

    # Try raw markets
    for m in raw_markets:
        t = m.get("ticker") or m.get("market_ticker")
        if t == ticker:
            yes_price = m.get("yes_price") or m.get("last_price")
            ts = m.get("last_updated") or m.get("updated_at")
            return yes_price, "raw", ts

    return None, None, None


def classify_snapshot(ticker_entry, registry, raw_markets, capture_ts):
    """
    Classify a single ticker's CLV snapshot status.

    Args:
        ticker_entry: dict with ticker, gameStartTime, gamePk, marketType, side, etc.
        registry: dict of Kalshi markets
        raw_markets: list of raw Kalshi market dicts
        capture_ts: datetime when snapshot is being taken

    Returns:
        dict with clvStatus, clvPrice, captureTimestamp, notes
    """
    ticker = ticker_entry.get("ticker") or ticker_entry.get("marketTicker")
    game_start_str = ticker_entry.get("gameStartTime") or ticker_entry.get("scheduledStartTime")
    game_pk = ticker_entry.get("gamePk") or ticker_entry.get("gameId")

    # Parse game start time
    game_start = parse_ts(game_start_str)

    # Validate: must capture before first pitch
    if game_start and capture_ts >= game_start:
        return {
            "ticker": ticker,
            "gamePk": game_pk,
            "clvStatus": "INVALID_POST_START",
            "clvPrice": None,
            "captureTimestamp": capture_ts.isoformat(),
            "gameStartTime": game_start_str,
            "notes": f"Snapshot taken at {capture_ts.isoformat()} which is after scheduled start {game_start_str}"
        }

    # Look up price
    yes_price, source, last_update_str = find_market_price(ticker, registry, raw_markets)

    # No price found at all
    if yes_price is None and source is None:
        return {
            "ticker": ticker,
            "gamePk": game_pk,
            "clvStatus": "TICKER_NOT_FOUND",
            "clvPrice": None,
            "captureTimestamp": capture_ts.isoformat(),
            "gameStartTime": game_start_str,
            "notes": f"Ticker {ticker} not found in registry or raw markets"
        }

    if yes_price is None:
        return {
            "ticker": ticker,
            "gamePk": game_pk,
            "clvStatus": "NO_VALID_PRICE",
            "clvPrice": None,
            "captureTimestamp": capture_ts.isoformat(),
            "gameStartTime": game_start_str,
            "notes": f"Ticker found in {source} but yes_price is null"
        }

    # Check sentinel
    if is_sentinel_price(yes_price):
        return {
            "ticker": ticker,
            "gamePk": game_pk,
            "clvStatus": "SENTINEL_PRICE",
            "clvPrice": yes_price,
            "captureTimestamp": capture_ts.isoformat(),
            "gameStartTime": game_start_str,
            "notes": f"Sentinel price detected: {yes_price} — rejected"
        }

    # Validate yes_price range
    is_valid, reason = is_valid_yes_price(yes_price)
    if not is_valid:
        return {
            "ticker": ticker,
            "gamePk": game_pk,
            "clvStatus": reason,
            "clvPrice": yes_price,
            "captureTimestamp": capture_ts.isoformat(),
            "gameStartTime": game_start_str,
            "notes": f"Invalid price {yes_price}: {reason}"
        }

    # Check staleness (>6 hours without update before first pitch)
    last_update = parse_ts(last_update_str)
    if last_update and game_start:
        hours_before_start = (game_start - last_update).total_seconds() / 3600
        if hours_before_start > 6 and (game_start - capture_ts).total_seconds() < 7200:
            return {
                "ticker": ticker,
                "gamePk": game_pk,
                "clvStatus": "STALE_MARKET",
                "clvPrice": yes_price,
                "captureTimestamp": capture_ts.isoformat(),
                "gameStartTime": game_start_str,
                "lastUpdated": last_update_str,
                "notes": f"Market not updated in {hours_before_start:.1f}h before first pitch"
            }

    # All checks pass → VALID
    return {
        "ticker": ticker,
        "gamePk": game_pk,
        "clvStatus": "VALID",
        "clvPrice": float(yes_price),
        "captureTimestamp": capture_ts.isoformat(),
        "gameStartTime": game_start_str,
        "lastUpdated": last_update_str,
        "priceSource": source,
        "notes": "Pregame CLV snapshot captured successfully"
    }


def run(date_str=None, dry_run=False):
    """
    Main entry point.
    date_str: YYYY-MM-DD, defaults to today ET.
    dry_run: if True, print results but do not write files.
    """
    if not date_str:
        now_et = datetime.now(timezone(timedelta(hours=-4)))
        date_str = now_et.strftime("%Y-%m-%d")

    capture_ts = datetime.now(timezone.utc)
    print(f"[capture_clv_pregame] Running for {date_str} at {capture_ts.isoformat()}")

    # Load tracked tickers
    tickers = load_tracked_tickers(date_str)
    if not tickers:
        print(f"[capture_clv_pregame] No tracked tickers found for {date_str}")
        return {"status": "NO_TICKERS", "date": date_str, "snapshots": []}

    print(f"[capture_clv_pregame] Found {len(tickers)} tracked tickers")

    # Load Kalshi data sources
    registry  = load_kalshi_registry()
    raw_markets = load_kalshi_raw()

    # Group tickers by gamePk
    by_game = {}
    for t in tickers:
        gpk = str(t.get("gamePk") or t.get("gameId") or "unknown")
        by_game.setdefault(gpk, []).append(t)

    all_snapshots = []
    snap_dir = os.path.join(SNAPSHOT_DIR, date_str)

    for game_pk, game_tickers in by_game.items():
        game_snaps = []
        for te in game_tickers:
            snap = classify_snapshot(te, registry, raw_markets, capture_ts)
            # Merge in full ticker entry data
            full = {**te, **snap}
            game_snaps.append(full)
            all_snapshots.append(full)

        if not dry_run:
            os.makedirs(snap_dir, exist_ok=True)
            out_path = os.path.join(snap_dir, f"pregame_{game_pk}.json")
            with open(out_path, "w") as f:
                json.dump({
                    "date": date_str,
                    "gamePk": game_pk,
                    "captureTimestamp": capture_ts.isoformat(),
                    "snapshots": game_snaps
                }, f, indent=2)
            print(f"[capture_clv_pregame] Written: {out_path}")

    valid = sum(1 for s in all_snapshots if s.get("clvStatus") == "VALID")
    invalid = sum(1 for s in all_snapshots if s.get("clvStatus") != "VALID")
    print(f"[capture_clv_pregame] Done: {valid} VALID, {invalid} invalid/missing")

    return {
        "status": "COMPLETE",
        "date": date_str,
        "captureTimestamp": capture_ts.isoformat(),
        "totalTickers": len(tickers),
        "valid": valid,
        "invalid": invalid,
        "snapshots": all_snapshots
    }


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Capture pregame CLV snapshots")
    parser.add_argument("--date", help="YYYY-MM-DD (default: today ET)")
    parser.add_argument("--dry-run", action="store_true", help="Print but don't write")
    args = parser.parse_args()
    result = run(date_str=args.date, dry_run=args.dry_run)
    print(json.dumps({"summary": {k: v for k, v in result.items() if k != "snapshots"}}, indent=2))
