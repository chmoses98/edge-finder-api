#!/usr/bin/env python3
"""
scripts/capture_pregame_closing_lines.py
==========================================
Scheduled, precision closing-line collector.

Problem this solves: before this script existed, Kalshi price snapshots were
only captured incidentally whenever some other slate workflow happened to run
(fetch-slate, lineup-recheck, the 30-minute kalshi snapshot archiver), so a
game's "closing line" snapshot could have been taken anywhere from hours
before first pitch to well after it. scripts/capture_closing_lines.py's
settle mode then used `closing_snapshots[-1]` (most recent) unconditionally
— sometimes a live/in-play price, not a closing price.

This script is meant to be run every 5 minutes by
.github/workflows/capture-closing-lines.yml. On each invocation it:

  1. Loads data/kalshi_market_registry.json.
  2. For every registered game on the target date, converts
     {date}+{time_str} (America/New_York, DST-aware) to a UTC first-pitch
     timestamp.
  3. Only fetches live Kalshi prices for a game if "now" falls inside that
     game's capture window: [first_pitch - 12min, first_pitch + 5min].
     Games outside their window are skipped entirely (no API calls).
  4. For an in-window game, refreshes every market type already present in
     that game's registry entry (moneyline, spread, total, team totals,
     F5 moneyline, F5 spread, F5 total, RFI/NRFI) by exact ticker, and
     appends a timestamped snapshot to registry[game].closing_snapshots.
  5. Classifies each snapshot as capture_timing="PRE_START" (captured at or
     before first pitch) or "LATE" (captured after first pitch but still
     inside the +5min tail of the window — e.g. a bit of clock/API drift).
  6. Recomputes registry[game].official_closing_snapshot — always the
     PRE_START snapshot closest to first pitch, NEVER a LATE snapshot — and
     registry[game].closing_capture_status, one of:
       CAPTURED_PRE_START, LATE_ONLY, MISSED, NO_PRICES
     This recompute runs for every game on the date (not just ones fetched
     this invocation), so a game whose capture window has fully elapsed
     without ever being captured is correctly flagged MISSED even on a run
     that didn't touch it.
  7. Writes an audit trail to data/clv/YYYY-MM-DD/closing_capture_log.json.

Never calls into build_market_ledger.py, risk_gate.py, write_pending_bets.py,
or any execution/recommendation script — this is a pure data-refresh tool.
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from urllib.request import urlopen, Request
from urllib.error import HTTPError

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python < 3.9 not used in this repo
    ZoneInfo = None

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPTS_DIR)

KALSHI_BASE = "https://api.elections.kalshi.com/trade-api/v2"
REGISTRY_PATH = os.path.join(ROOT_DIR, "data", "kalshi_market_registry.json")
CLV_DIR = os.path.join(ROOT_DIR, "data", "clv")

ET_ZONE = ZoneInfo("America/New_York") if ZoneInfo else None

CAPTURE_WINDOW_BEFORE_MIN = 12   # fetch starting this many minutes before first pitch
CAPTURE_WINDOW_AFTER_MIN = 5     # stop fetching this many minutes after first pitch

MAX_SNAPSHOTS_PER_GAME = 20

CAPTURE_SOURCE = "scheduled_closing_line_workflow"

STATUS_CAPTURED_PRE_START = "CAPTURED_PRE_START"
STATUS_LATE_ONLY = "LATE_ONLY"
STATUS_MISSED = "MISSED"
STATUS_NO_PRICES = "NO_PRICES"


# ── Time helpers ────────────────────────────────────────────────────────────

def parse_scheduled_start_utc(date_str, time_str):
    """
    Convert a registry game's {date}+{time_str} (America/New_York wall-clock
    time, HHMM 24h) into an aware UTC datetime. DST-aware via zoneinfo — the
    same HHMM maps to a different UTC offset in EDT (summer) vs EST (winter).

    Returns None if date_str/time_str cannot be parsed.
    """
    if not date_str or not time_str or len(time_str) < 3:
        return None
    try:
        year, month, day = (int(x) for x in date_str.split("-"))
        hour = int(time_str[:2])
        minute = int(time_str[2:])
    except (ValueError, TypeError):
        return None

    naive = datetime(year, month, day, hour, minute)
    if ET_ZONE is not None:
        et_dt = naive.replace(tzinfo=ET_ZONE)
        return et_dt.astimezone(timezone.utc)

    # Fallback (should not happen on Python 3.9+): fixed EDT offset.
    et_dt = naive.replace(tzinfo=timezone(timedelta(hours=-4)))
    return et_dt.astimezone(timezone.utc)


def iso(dt):
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(ts_str):
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


def today_et_date_str(now_utc=None):
    now_utc = now_utc or datetime.now(timezone.utc)
    if ET_ZONE is not None:
        return now_utc.astimezone(ET_ZONE).strftime("%Y-%m-%d")
    return now_utc.astimezone(timezone(timedelta(hours=-4))).strftime("%Y-%m-%d")


# ── Price fetching ───────────────────────────────────────────────────────────

def default_fetcher(ticker):
    """
    Real network fetch of a single Kalshi market by ticker.
    Returns the raw `market` dict from the Kalshi API, or None on any failure.
    Injected as `fetcher` in run()/capture_game() so tests never hit the network.
    """
    url = f"{KALSHI_BASE}/markets/{ticker}"
    try:
        req = Request(url, headers={"Accept": "application/json", "User-Agent": "Mozilla/5.0"})
        with urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
        return data.get("market")
    except HTTPError:
        return None
    except Exception:
        return None


def _norm(v):
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return round(f if f <= 1.0 else f / 100.0, 4)


def _american(mid):
    if not mid or mid <= 0 or mid >= 1:
        return None
    return round(-(mid / (1 - mid)) * 100) if mid >= 0.5 else round(((1 - mid) / mid) * 100)


def price_block(ticker, m):
    """Normalize a raw Kalshi market dict into our snapshot price-block shape."""
    if not m:
        return {
            "ticker": ticker,
            "yes_bid": None, "yes_ask": None, "mid": None,
            "implied_pct": None, "american": None,
            "last_price": None, "volume": None, "status": None,
        }
    bid = _norm(m.get("yes_bid_dollars") or m.get("yes_bid"))
    ask = _norm(m.get("yes_ask_dollars") or m.get("yes_ask"))
    last = _norm(m.get("last_price_dollars") or m.get("last_price"))
    mid = round(((bid or 0) + (ask or 0)) / 2, 4) if (bid or ask) else None
    return {
        "ticker": ticker,
        "yes_bid": bid,
        "yes_ask": ask,
        "mid": mid,
        "implied_pct": round(mid * 100, 2) if mid else None,
        "american": _american(mid),
        "last_price": last,
        "volume": m.get("volume"),
        "status": m.get("status"),
    }


def capture_game_prices(entry, fetcher):
    """
    Refresh every market type present in this game's registry entry by exact
    ticker. Returns (prices_dict, any_price_found).

    prices_dict shape mirrors entry['markets'], plus a flat 'by_ticker' index
    of {ticker: price_block} for exact-match lookups downstream (settlement
    must never fall back to a market's generic best_line for CLV).
    """
    markets = entry.get("markets", {})
    prices = {}
    by_ticker = {}
    any_price = False

    def fetch_and_index(ticker):
        nonlocal any_price
        if not ticker:
            return None
        m = fetcher(ticker)
        pb = price_block(ticker, m)
        if pb.get("mid") is not None:
            any_price = True
        by_ticker[ticker] = pb
        return pb

    for mkt_type, mkt_data in markets.items():
        if not isinstance(mkt_data, dict):
            continue

        if mkt_type in ("moneyline", "f5_moneyline"):
            snap_mkt = {}
            for side, ticker_key in (("away", "away_ticker"), ("home", "home_ticker"), ("tie", "tie_ticker")):
                ticker = mkt_data.get(ticker_key)
                if not ticker:
                    continue
                snap_mkt[side] = fetch_and_index(ticker)
            if snap_mkt:
                prices[mkt_type] = snap_mkt

        elif mkt_type == "rfi":
            ticker = mkt_data.get("ticker")
            if ticker:
                yes_pb = fetch_and_index(ticker)
                mid_yes = yes_pb.get("mid") if yes_pb else None
                mid_no = round(1.0 - mid_yes, 4) if mid_yes is not None else None
                nrfi_pb = {
                    "ticker": ticker,
                    "yes_bid": round(1.0 - (yes_pb.get("yes_ask") or 0), 4) if yes_pb and yes_pb.get("yes_ask") is not None else None,
                    "yes_ask": round(1.0 - (yes_pb.get("yes_bid") or 0), 4) if yes_pb and yes_pb.get("yes_bid") is not None else None,
                    "mid": mid_no,
                    "implied_pct": round(mid_no * 100, 2) if mid_no is not None else None,
                    "american": _american(mid_no),
                    "last_price": yes_pb.get("last_price") if yes_pb else None,
                    "volume": yes_pb.get("volume") if yes_pb else None,
                    "status": yes_pb.get("status") if yes_pb else None,
                    "side": "NO",
                    "derived_from": ticker,
                }
                prices["rfi"] = {
                    "yrfi": {**(yes_pb or {}), "side": "YES"},
                    "nrfi": nrfi_pb,
                }

        elif "lines" in mkt_data:
            refreshed = []
            for line in (mkt_data.get("lines") or []):
                ticker = line.get("ticker")
                pb = fetch_and_index(ticker) if ticker else None
                if pb:
                    refreshed.append({**line, **pb})
                else:
                    refreshed.append(line)
            if refreshed:
                prices[mkt_type] = {"lines": refreshed}

    prices["by_ticker"] = by_ticker
    return prices, any_price


# ── Snapshot classification / official-line selection ───────────────────────

def determine_closing_status(closing_snapshots, now_utc, window_end_utc):
    """
    Recompute (closing_capture_status, official_closing_snapshot) from the
    full snapshot history of a single game, independent of whether THIS run
    fetched anything for it.

    Rules (never violated):
      - official_closing_snapshot is only ever chosen from PRE_START
        snapshots that actually have prices. A LATE snapshot is never
        promoted to official, no matter how close to first pitch it is.
      - If no PRE_START snapshot with prices exists but a LATE one does,
        status is LATE_ONLY and official_closing_snapshot is left unset.
      - If the capture window has fully elapsed (now > window_end) and no
        snapshot ever had prices, status is MISSED if no snapshot was even
        attempted, or NO_PRICES if snapshots were attempted but all came
        back empty.
      - If the window hasn't elapsed yet and nothing useful has been
        captured, status is left as None (caller should not overwrite any
        prior value with a premature verdict).
    """
    pre_start_priced = [
        s for s in closing_snapshots
        if s.get("capture_timing") == "PRE_START" and s.get("prices", {}).get("by_ticker")
        and any(v.get("mid") is not None for v in s["prices"]["by_ticker"].values())
    ]
    late_priced = [
        s for s in closing_snapshots
        if s.get("capture_timing") == "LATE" and s.get("prices", {}).get("by_ticker")
        and any(v.get("mid") is not None for v in s["prices"]["by_ticker"].values())
    ]

    if pre_start_priced:
        official = min(pre_start_priced, key=lambda s: abs(s.get("minutes_to_start", 1e9)))
        return STATUS_CAPTURED_PRE_START, official

    if late_priced:
        return STATUS_LATE_ONLY, None

    if now_utc > window_end_utc:
        if closing_snapshots:
            return STATUS_NO_PRICES, None
        return STATUS_MISSED, None

    return None, None


# ── Main run ──────────────────────────────────────────────────────────────

def run(date_str=None, now_utc=None, registry_path=None, fetcher=None,
        dry_run=False, clv_dir=None):
    """
    Args:
        date_str:      YYYY-MM-DD. Defaults to today in America/New_York.
        now_utc:       aware UTC datetime for "now". Defaults to real time.
                       Tests inject this for deterministic window checks.
        registry_path: override path to kalshi_market_registry.json (tests).
        fetcher:       callable(ticker) -> raw Kalshi market dict or None.
                       Defaults to a real network call. Tests inject a fake.
        dry_run:       if True, compute everything but write nothing.
        clv_dir:       override for data/clv/ (tests).

    Returns a summary dict (also used as the audit-log payload).
    """
    now_utc = now_utc or datetime.now(timezone.utc)
    date_str = date_str or today_et_date_str(now_utc)
    reg_path = registry_path or REGISTRY_PATH
    fetch = fetcher or default_fetcher
    clv_base = clv_dir or CLV_DIR

    try:
        with open(reg_path) as f:
            reg_doc = json.load(f)
    except FileNotFoundError:
        print(f"[capture_pregame_closing_lines] No registry at {reg_path} — nothing to do")
        return {"date": date_str, "run_ts": iso(now_utc), "status": "NO_REGISTRY", "games": []}

    registry = reg_doc.get("registry", {})
    game_results = []
    any_game_touched = False

    for kalshi_key, entry in registry.items():
        if entry.get("date") != date_str:
            continue

        scheduled_start_utc = parse_scheduled_start_utc(entry.get("date"), entry.get("time_str"))
        if scheduled_start_utc is None:
            game_results.append({
                "kalshi_key": kalshi_key,
                "error": "unparseable_scheduled_start",
                "date": entry.get("date"),
                "time_str": entry.get("time_str"),
            })
            continue

        window_start = scheduled_start_utc - timedelta(minutes=CAPTURE_WINDOW_BEFORE_MIN)
        window_end = scheduled_start_utc + timedelta(minutes=CAPTURE_WINDOW_AFTER_MIN)
        minutes_to_start = (scheduled_start_utc - now_utc).total_seconds() / 60.0
        in_window = window_start <= now_utc <= window_end

        existing_snapshots = entry.get("closing_snapshots", [])
        fetched_this_run = False
        prev_status = entry.get("closing_capture_status")

        if in_window:
            any_game_touched = True
            fetched_this_run = True
            prices, _ = capture_game_prices(entry, fetch)
            capture_timing = "PRE_START" if now_utc <= scheduled_start_utc else "LATE"
            snapshot = {
                "snapshot_ts": iso(now_utc),
                "scheduled_start_ts": iso(scheduled_start_utc),
                "minutes_to_start": round(minutes_to_start, 2),
                "capture_timing": capture_timing,
                "capture_source": CAPTURE_SOURCE,
                "prices": prices,
            }
            existing_snapshots = existing_snapshots + [snapshot]
            existing_snapshots = existing_snapshots[-MAX_SNAPSHOTS_PER_GAME:]
            entry["closing_snapshots"] = existing_snapshots

        status, official = determine_closing_status(existing_snapshots, now_utc, window_end)
        if status is not None:
            entry["closing_capture_status"] = status
            if official is not None:
                entry["official_closing_snapshot"] = official
            elif status in (STATUS_LATE_ONLY, STATUS_MISSED, STATUS_NO_PRICES):
                # Explicitly clear any stale official snapshot — never leave a
                # promoted line in place once we know it can no longer be the
                # official pre-start close.
                entry.pop("official_closing_snapshot", None)
            if status != prev_status:
                any_game_touched = True

        game_results.append({
            "kalshi_key": kalshi_key,
            "scheduled_start_ts": iso(scheduled_start_utc),
            "minutes_to_start": round(minutes_to_start, 2),
            "in_window_this_run": in_window,
            "fetched_this_run": fetched_this_run,
            "closing_capture_status": entry.get("closing_capture_status"),
            "snapshot_count": len(existing_snapshots),
        })

    reg_doc["registry"] = registry
    if any_game_touched:
        reg_doc["last_pregame_closing_capture_ts"] = iso(now_utc)

    summary = {
        "date": date_str,
        "run_ts": iso(now_utc),
        "games_total": len(game_results),
        "games_in_window": sum(1 for g in game_results if g.get("in_window_this_run")),
        "registry_changed": any_game_touched,
        "games": game_results,
    }

    if not dry_run and any_game_touched:
        # Only write anything when a snapshot was captured or a game's
        # closing_capture_status actually changed (e.g. MISSED newly
        # detected) — an off-hours run with nothing in-window and no status
        # transitions is a true no-op: no registry write, no log entry,
        # nothing for the workflow to commit.
        with open(reg_path, "w") as f:
            json.dump(reg_doc, f, indent=2)

        os.makedirs(os.path.join(clv_base, date_str), exist_ok=True)
        log_path = os.path.join(clv_base, date_str, "closing_capture_log.json")
        existing_log = {"date": date_str, "runs": []}
        if os.path.exists(log_path):
            try:
                with open(log_path) as f:
                    existing_log = json.load(f)
            except Exception:
                existing_log = {"date": date_str, "runs": []}
        existing_log.setdefault("runs", []).append(summary)
        with open(log_path, "w") as f:
            json.dump(existing_log, f, indent=2)
        summary["log_path"] = log_path

    return summary


if __name__ == "__main__":
    arg_date = sys.argv[1] if len(sys.argv) > 1 else None
    result = run(date_str=arg_date)
    print(json.dumps({k: v for k, v in result.items() if k != "games"}, indent=2))
    for g in result.get("games", []):
        marker = "→" if g.get("in_window_this_run") else " "
        print(f"  {marker} {g.get('kalshi_key')}: status={g.get('closing_capture_status')} "
              f"mins_to_start={g.get('minutes_to_start')} snaps={g.get('snapshot_count')}")
    sys.exit(0)
