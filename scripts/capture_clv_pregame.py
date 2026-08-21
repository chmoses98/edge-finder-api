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

if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from lib.sentinel_validator import is_sentinel_american  # noqa: E402

# Primary price source: kalshi_registry_snapshots/ (updated every 30min by
# capture-snapshots-scheduled.yml — the freshest available market prices).
# This is keyed by date so we always read the most recent snapshot for today.
KALSHI_REGISTRY_SNAPSHOTS = os.path.join(ROOT_DIR, "data", "kalshi_registry_snapshots")

# Fallback: kalshi_raw.json (ML markets only; written by fetch-slate, may be 12–24h old)
RAW_KALSHI   = os.path.join(ROOT_DIR, "data", "kalshi_raw.json")

# NOTE: kalshi_market_registry.json is NOT used here.
# It is keyed by game pair (e.g. "SDBAL"), not by market ticker, so it cannot
# be used for direct ticker lookup. It is also built only during fetch-slate.

# ── Sentinel prices that must always be rejected ───────────────────────────────
# Sentinel Single-Source mission (docs/DUPLICATE_LOGIC_INVENTORY.md #2): the
# broad, cross-domain sentinel set/threshold (19900/-19900/100000/-100000,
# and any |value| >= 19000) is no longer copied here -- it comes live from
# the canonical lib.sentinel_validator.is_sentinel_american(), so a future
# addition to that canonical set propagates here automatically, closing the
# exact drift risk the inventory doc flagged ("if the canonical set is ever
# updated ... this copy would silently miss it").
#
# {199, -199} stays as an explicit LOCAL addition, deliberately NOT promoted
# into the shared canonical set: these are ordinary, legitimate American
# odds elsewhere in this repository (a real moneyline favorite/underdog at
# -199/+199 is completely normal), so adding them to a set every other
# consumer of is_sentinel_american() also checks would misclassify real
# prices as sentinels everywhere else. They are only ever a genuine anomaly
# in THIS script's own yes_price domain (Kalshi cents, 1-99 scale), where a
# legitimate price can never actually be 199 in the first place.
_LOCAL_EXTRA_SENTINEL_PRICES = {199, -199}

# Sentinel detection for probability values (0-100 scale or 0-1 scale)
SENTINEL_PROBS_100 = {99, 1, 0, 100}   # near-certain prices that indicate settlement
VALID_PRICE_RANGE  = (1, 99)           # valid yes_price range (probability cents 1-99)


def is_sentinel_price(value):
    """Return True if value is a known sentinel / impossible price."""
    if value is None:
        return False
    try:
        v = float(value)
    except (TypeError, ValueError):
        return False
    return is_sentinel_american(v) or v in _LOCAL_EXTRA_SENTINEL_PRICES


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


def load_kalshi_search_snapshot(date_str):
    """
    Load the freshest Kalshi market snapshot for date_str.

    Reads from data/kalshi_registry_snapshots/kalshi_search_DATE.json,
    which is updated every 30 minutes by capture-snapshots-scheduled.yml.
    This is the same file that clv_from_snapshot.py uses as its primary source.

    Returns dict keyed by market_ticker, or {} if not found.
    """
    # Try dated snapshot file (most recent prices for today)
    snap_path = os.path.join(KALSHI_REGISTRY_SNAPSHOTS, f"kalshi_search_{date_str}.json")
    if not os.path.exists(snap_path):
        print(f"[capture_clv_pregame] No snapshot for {date_str} at {snap_path}")
        # Try yesterday's snapshot as fallback
        from datetime import datetime, timezone, timedelta
        try:
            d = datetime.strptime(date_str, "%Y-%m-%d") - timedelta(days=1)
            prev = os.path.join(KALSHI_REGISTRY_SNAPSHOTS, f"kalshi_search_{d.strftime('%Y-%m-%d')}.json")
            if os.path.exists(prev):
                print(f"[capture_clv_pregame] Using previous day snapshot: {prev}")
                snap_path = prev
            else:
                return {}
        except Exception:
            return {}

    try:
        with open(snap_path) as f:
            data = json.load(f)
    except Exception as e:
        print(f"[capture_clv_pregame] Failed to load snapshot {snap_path}: {e}")
        return {}

    markets = data.get("markets", [])
    if isinstance(markets, dict):
        markets = list(markets.values())

    # Build ticker index
    index = {}
    for m in markets:
        t = m.get("market_ticker") or m.get("ticker")
        if t:
            index[t] = m

    print(f"[capture_clv_pregame] Loaded {len(index)} tickers from {os.path.basename(snap_path)}"
          f" (fetched_at={data.get('fetched_at', 'unknown')})")
    return index


def load_kalshi_raw():
    """
    Load kalshi_raw.json as a fallback price source.
    Contains ML markets only. Written by fetch-slate; may be 12–24h old.
    Returns list of market dicts.
    """
    if not os.path.exists(RAW_KALSHI):
        return []
    with open(RAW_KALSHI) as f:
        data = json.load(f)
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get("markets", [])
    return []


def find_market_price(ticker, snapshot_index, raw_markets):
    """
    Find current yes_price for a ticker from available sources.

    Args:
        ticker:         market_ticker string to look up
        snapshot_index: dict keyed by market_ticker from load_kalshi_search_snapshot()
                        — fresh prices updated every 30min. This is the primary source.
        raw_markets:    list of dicts from load_kalshi_raw() — ML markets only, fallback.

    Returns (yes_price, source, last_update_ts) or (None, None, None).
    """
    # Primary: snapshot index (fresh, keyed by market_ticker)
    mkt = snapshot_index.get(ticker)
    if mkt:
        # kalshi_search snapshot fields: yes_bid, yes_ask, mid (as 0-1 float or 0-100 int)
        # mid is the cleanest single price; fall back to (bid+ask)/2, last_price, yes_price.
        # yes_price is supported for backwards compatibility with test fixtures.
        mid = mkt.get("mid")
        yes_bid = mkt.get("yes_bid")
        yes_ask = mkt.get("yes_ask")
        last_price = mkt.get("last_price")
        yes_price_direct = mkt.get("yes_price")  # legacy / test fixture field

        if mid is not None:
            # mid in snapshot is already 0-1 float (implied probability)
            # Convert to 0-100 scale (yes_price format used by classify_snapshot)
            try:
                mid_f = float(mid)
                yes_price = round(mid_f * 100, 2) if mid_f <= 1.0 else mid_f
            except (TypeError, ValueError):
                yes_price = None
        elif yes_bid is not None and yes_ask is not None:
            try:
                yes_price = round((float(yes_bid) + float(yes_ask)) / 2 * 100, 2)                     if float(yes_bid) <= 1.0 else round((float(yes_bid) + float(yes_ask)) / 2, 2)
            except (TypeError, ValueError):
                yes_price = None
        elif last_price is not None:
            try:
                lp = float(last_price)
                yes_price = round(lp * 100, 2) if lp <= 1.0 else round(lp, 2)
            except (TypeError, ValueError):
                yes_price = None
        elif yes_price_direct is not None:
            # Direct yes_price field (already in 0-100 scale)
            try:
                yes_price = float(yes_price_direct)
            except (TypeError, ValueError):
                yes_price = None
        else:
            yes_price = None

        ts = mkt.get("snapshot_ts") or mkt.get("fetched_at") or mkt.get("last_updated")
        if yes_price is not None:
            return yes_price, "kalshi_search_snapshot", ts

    # Fallback: raw ML markets (keyed by ticker or market_ticker)
    for m in raw_markets:
        t = m.get("ticker") or m.get("market_ticker")
        if t == ticker:
            yes_price = m.get("yes_price") or m.get("last_price")
            ts = m.get("last_updated") or m.get("updated_at")
            return yes_price, "kalshi_raw_fallback", ts

    return None, None, None


def classify_snapshot(ticker_entry, snapshot_index, raw_markets, capture_ts):
    """
    Classify a single ticker's CLV snapshot status.

    Args:
        ticker_entry:   dict with ticker, gameStartTime, gamePk, marketType, side, etc.
        snapshot_index: dict keyed by market_ticker from load_kalshi_search_snapshot()
        raw_markets:    list of dicts from load_kalshi_raw() (ML fallback)
        capture_ts:     datetime when snapshot is being taken

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
    yes_price, source, last_update_str = find_market_price(ticker, snapshot_index, raw_markets)

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


def run(date_str=None, dry_run=False, current_utc=None):
    """
    Main entry point.
    date_str: YYYY-MM-DD, defaults to today ET.
    dry_run: if True, print results but do not write files.
    current_utc: ISO 8601 UTC string for "now" — same convention as
        lib/postponed_guard.check_game_status(current_utc=...). Injected by
        tests so pregame/post-start classification is deterministic and does
        not depend on the real wall clock. Production callers (the CLI entry
        point below, clv_capture.yml) omit it and get real current time.
    """
    if not date_str:
        now_et = datetime.now(timezone(timedelta(hours=-4)))
        date_str = now_et.strftime("%Y-%m-%d")

    if current_utc:
        capture_ts = datetime.fromisoformat(current_utc.replace('Z', '+00:00'))
    else:
        capture_ts = datetime.now(timezone.utc)
    print(f"[capture_clv_pregame] Running for {date_str} at {capture_ts.isoformat()}")

    # Load tracked tickers
    tickers = load_tracked_tickers(date_str)
    if not tickers:
        print(f"[capture_clv_pregame] No tracked tickers found for {date_str}")
        return {"status": "NO_TICKERS", "date": date_str, "snapshots": []}

    print(f"[capture_clv_pregame] Found {len(tickers)} tracked tickers")

    # Load Kalshi data sources
    # Primary: fresh snapshot (updated every 30min, no auth required)
    snapshot_index = load_kalshi_search_snapshot(date_str)
    # Fallback: kalshi_raw.json (ML markets only, written by last fetch-slate run)
    raw_markets = load_kalshi_raw()

    if not snapshot_index and not raw_markets:
        print(f"[capture_clv_pregame] WARNING: No price data available for {date_str}")
        print(f"[capture_clv_pregame] Ensure capture-snapshots-scheduled.yml has run today")

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
            snap = classify_snapshot(te, snapshot_index, raw_markets, capture_ts)
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
