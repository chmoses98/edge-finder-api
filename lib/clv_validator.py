#!/usr/bin/env python3
"""
lib/clv_validator.py
=====================
CLV Validation Logic

Returns validated CLV data for a bet. NEVER uses postgame/settlement/stale/sentinel prices.
If CLV unavailable, returns a descriptive status string, never zero.

Usage:
    from lib.clv_validator import validate_clv

    result = validate_clv(bet, snapshot_dir="data/clv_snapshots")
    # result.clvStatus: "VALID" | "MISSING" | "INVALID_POST_START" | ...
    # result.clvPct: float | None
    # result.entryPrice: int | None
    # result.closePrice: float | None
"""

import json
import os
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, asdict
from typing import Optional

# ── Sentinel detection ────────────────────────────────────────────────────────
SENTINEL_PRICES = {19900, -19900, 100000, -100000, 199, -199}

# Valid CLV status values
CLV_STATUSES = {
    "VALID",
    "MISSING",
    "INVALID_POST_START",
    "TICKER_NOT_FOUND",
    "STALE_MARKET",
    "SENTINEL_PRICE",
    "NO_VALID_PRICE",
    "MARKET_LOCKED",
    "SETTLEMENT_PRICE_ONLY",
    "ENTRY_PRICE_MISSING",
    "PRE_GAME_ONLY",
}


def is_sentinel(value):
    """Return True if value is a known sentinel price."""
    if value is None:
        return False
    try:
        v = float(value)
        if v in SENTINEL_PRICES:
            return True
        if abs(v) >= 19000:
            return True
        return False
    except (TypeError, ValueError):
        return False


def american_to_implied(odds):
    """Convert American odds → implied probability [0,1]."""
    try:
        o = float(odds)
        if o >= 0:
            return 100.0 / (o + 100.0)
        else:
            return abs(o) / (abs(o) + 100.0)
    except Exception:
        return None


def yes_price_to_implied(yes_price):
    """
    Convert Kalshi yes_price (cents, 1-99) → implied probability [0,1].
    Returns None if invalid.
    """
    try:
        p = float(yes_price)
        if is_sentinel(p):
            return None
        if p <= 0 or p >= 100:
            return None
        return p / 100.0
    except Exception:
        return None


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


@dataclass
class CLVResult:
    clvStatus: str
    clvPct: Optional[float]          # closing line value in percentage points; None if unavailable
    entryPrice: Optional[int]         # entry American odds
    entryTimestamp: Optional[str]     # ISO timestamp of bet placement
    closePrice: Optional[float]       # closing yes_price (cents)
    closeTimestamp: Optional[str]     # ISO timestamp of closing snapshot
    clvNotes: str                     # human-readable explanation

    def to_dict(self):
        return asdict(self)


def _unavailable(status, notes, entry_price=None, entry_ts=None):
    """Helper: return a CLVResult for unavailable CLV."""
    assert status in CLV_STATUSES, f"Unknown CLV status: {status}"
    return CLVResult(
        clvStatus=status,
        clvPct=None,
        entryPrice=entry_price,
        entryTimestamp=entry_ts,
        closePrice=None,
        closeTimestamp=None,
        clvNotes=notes,
    )


def load_snapshot_for_ticker(ticker, date_str, snapshot_dir):
    """
    Find the best pregame snapshot for a ticker on a given date.
    Looks in data/clv_snapshots/YYYY-MM-DD/pregame_*.json
    Returns (snapshot_dict, source_file) or (None, None).
    """
    day_dir = os.path.join(snapshot_dir, date_str)
    if not os.path.exists(day_dir):
        return None, None

    best = None
    best_ts = None
    best_file = None

    for fname in os.listdir(day_dir):
        if not fname.startswith("pregame_") or not fname.endswith(".json"):
            continue
        fpath = os.path.join(day_dir, fname)
        try:
            with open(fpath) as f:
                data = json.load(f)
        except Exception:
            continue

        snaps = data.get("snapshots", [])
        for s in snaps:
            if (s.get("ticker") or s.get("marketTicker")) == ticker:
                cap_ts = parse_ts(s.get("captureTimestamp"))
                if cap_ts and (best_ts is None or cap_ts > best_ts):
                    best = s
                    best_ts = cap_ts
                    best_file = fpath

    return best, best_file


def validate_clv(bet, snapshot_dir=None, root_dir=None):
    """
    Validate CLV for a single bet dict.

    Args:
        bet: dict with at minimum: ticker/marketTicker, price/entry_price,
             scheduledStartTime/gameStartTime, betTimeLine/placementConfirmedAt,
             date/slateDate
        snapshot_dir: path to clv_snapshots directory (default: data/clv_snapshots
                      relative to repo root)
        root_dir: repo root directory (default: two levels up from lib/)

    Returns:
        CLVResult dataclass
    """
    if root_dir is None:
        root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if snapshot_dir is None:
        snapshot_dir = os.path.join(root_dir, "data", "clv_snapshots")

    # Extract key fields
    ticker = bet.get("ticker") or bet.get("marketTicker")
    entry_price_raw = bet.get("price") or bet.get("entry_price") or bet.get("betTimeLine")
    entry_ts_str = bet.get("placementConfirmedAt") or bet.get("betTimeLine") or bet.get("entryTimestamp")
    game_start_str = bet.get("scheduledStartTime") or bet.get("gameStartTime")
    date_str = (bet.get("date") or bet.get("slateDate") or "")[:10]

    # Entry price required
    if entry_price_raw is None:
        return _unavailable(
            "ENTRY_PRICE_MISSING",
            "Entry price missing from bet record — cannot calculate CLV",
            entry_ts=entry_ts_str,
        )

    # Check entry price for sentinel
    if is_sentinel(entry_price_raw):
        return _unavailable(
            "SENTINEL_PRICE",
            f"Entry price {entry_price_raw} is a sentinel value — cannot calculate CLV",
            entry_price=int(entry_price_raw),
            entry_ts=entry_ts_str,
        )

    # Entry implied probability
    entry_implied = american_to_implied(entry_price_raw)
    if entry_implied is None:
        return _unavailable(
            "NO_VALID_PRICE",
            f"Cannot convert entry price {entry_price_raw} to implied probability",
            entry_ts=entry_ts_str,
        )

    # Need ticker for snapshot lookup
    if not ticker:
        return _unavailable(
            "TICKER_NOT_FOUND",
            "No market ticker in bet record — cannot look up closing snapshot",
            entry_price=int(float(entry_price_raw)) if entry_price_raw else None,
            entry_ts=entry_ts_str,
        )

    # Need date
    if not date_str:
        return _unavailable(
            "MISSING",
            "No date in bet record — cannot locate snapshot directory",
            entry_price=int(float(entry_price_raw)),
            entry_ts=entry_ts_str,
        )

    # Look up snapshot
    snap, snap_file = load_snapshot_for_ticker(ticker, date_str, snapshot_dir)

    if snap is None:
        return _unavailable(
            "MISSING",
            f"No pregame snapshot found for ticker {ticker} on {date_str}",
            entry_price=int(float(entry_price_raw)),
            entry_ts=entry_ts_str,
        )

    snap_status = snap.get("clvStatus")

    # Pass through bad snapshot statuses
    if snap_status and snap_status != "VALID":
        return _unavailable(
            snap_status,
            snap.get("notes", f"Snapshot status: {snap_status}"),
            entry_price=int(float(entry_price_raw)),
            entry_ts=entry_ts_str,
        )

    close_price = snap.get("clvPrice")
    close_ts = snap.get("captureTimestamp")

    # Validate close price
    if close_price is None:
        return _unavailable(
            "NO_VALID_PRICE",
            "Snapshot exists but clvPrice is null",
            entry_price=int(float(entry_price_raw)),
            entry_ts=entry_ts_str,
        )

    if is_sentinel(close_price):
        return _unavailable(
            "SENTINEL_PRICE",
            f"Closing price {close_price} is a sentinel value — rejected",
            entry_price=int(float(entry_price_raw)),
            entry_ts=entry_ts_str,
        )

    # Validate close snapshot is pre-game
    close_ts_dt = parse_ts(close_ts)
    game_start_dt = parse_ts(game_start_str)

    if close_ts_dt and game_start_dt and close_ts_dt >= game_start_dt:
        return _unavailable(
            "INVALID_POST_START",
            f"Closing snapshot at {close_ts} is after game start {game_start_str} — cannot use for CLV",
            entry_price=int(float(entry_price_raw)),
            entry_ts=entry_ts_str,
        )

    # Calculate CLV
    close_implied = yes_price_to_implied(close_price)
    if close_implied is None:
        return _unavailable(
            "NO_VALID_PRICE",
            f"Cannot convert closing yes_price {close_price} to implied probability",
            entry_price=int(float(entry_price_raw)),
            entry_ts=entry_ts_str,
        )

    # CLV formula: (entry_implied - close_implied) * 100
    # Positive = we bought cheaper than market closed → good CLV
    clv_pct = (entry_implied - close_implied) * 100.0

    return CLVResult(
        clvStatus="VALID",
        clvPct=round(clv_pct, 3),
        entryPrice=int(float(entry_price_raw)),
        entryTimestamp=entry_ts_str,
        closePrice=float(close_price),
        closeTimestamp=close_ts,
        clvNotes=f"CLV calculated from pregame snapshot {os.path.basename(snap_file or '')}",
    )


if __name__ == "__main__":
    # Quick test
    import sys
    test_bet = {
        "ticker": "KXMLBF5-26JUN141340ATLNYM-ATL",
        "price": -141,
        "placementConfirmedAt": "2026-06-14T11:36:00-04:00",
        "scheduledStartTime": "2026-06-14T13:40:00Z",
        "date": "2026-06-14",
    }
    result = validate_clv(test_bet)
    print(json.dumps(result.to_dict(), indent=2))
