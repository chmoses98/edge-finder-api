#!/usr/bin/env python3
"""
scripts/log_session_bets.py  v1.0
===================================
Ingests bets identified during a live analysis session (e.g., a night slate
analyzed after the automated pipeline run) into bets.json and the CLV ticker
registry, so those bets participate in the same CLV / settlement workflow as
automated bets.

USAGE
-----
  python3 scripts/log_session_bets.py data/session_bets/2026-06-17.json
  python3 scripts/log_session_bets.py data/session_bets/2026-06-17.json --dry-run
  python3 scripts/log_session_bets.py data/session_bets/2026-06-17.json --no-clv

INPUT FILE SCHEMA  (data/session_bets/YYYY-MM-DD.json)
------------------------------------------------------
[
  {
    "date":             "2026-06-17",          // REQUIRED  YYYY-MM-DD
    "game":             "CWS@NYY",             // REQUIRED  "AWAY@HOME"
    "market":           "F5 ML",               // REQUIRED
    "side":             "HOME",                // REQUIRED  HOME | AWAY | OVER | UNDER
    "ticker":           "KXMLBF5-26JUN...",    // REQUIRED
    "entryPrice":       -111,                  // REQUIRED  American odds integer
    "stake":            4.5,                   // REQUIRED  dollars
    "modelPct":         68.0,                  // REQUIRED  0-100
    "marketPct":        52.8,                  // REQUIRED  Kalshi VF 0-100
    "edgePct":          2.84,                  // REQUIRED  calibrated edge
    "confidence":       "MEDIUM",              // REQUIRED  HIGH | MEDIUM | PAPER
    "betTeam":          "New York Yankees",    // optional
    "scheduledStartTime": "2026-06-17T23:05:00Z", // optional
    "factors":          {},                    // optional  factors{} dict
    "notes":            "...",                 // optional
    "source":           "session_analysis",    // optional  defaults to session_analysis
    "timestamp":        "2026-06-17T23:00:00Z",// optional  defaults to now
    // post-game settlement fields (backfill):
    "status":           "settled",             // optional  pending | settled
    "result":           "WIN",                 // optional  WIN | LOSS | PUSH | VOID
    "pl":               4.05,                  // optional  dollars
    "finalScore":       "CWS 5, NYY 10",       // optional
    "clvStatus":        "unavailable",         // optional
    "clvReason":        "...",                 // optional
    "post_entry_manual_review": false          // optional  set true if game already started
  }
]

VALIDATION RULES (hard failures)
---------------------------------
  - ticker, entryPrice, stake, market, game, date must all be present and non-empty
  - confidence must be HIGH, MEDIUM, or PAPER
  - PAPER bets are logged as type="paper" and NOT written as real pending bets
    (they go into bets.json but with type="paper", stake=$1 unless overridden)
  - If game is already started (scheduledStartTime < now) AND
    post_entry_manual_review is not True → FAIL
  - No duplicate bets (same date + game + market + ticker key)

EXIT CODES
----------
  0 = success
  1 = hard validation failure (missing field, duplicate prevention failed, etc.)
"""

import json
import os
import sys
import re
from datetime import datetime, timezone, timedelta

# ── Paths ────────────────────────────────────────────────────────────────────
_THIS_FILE = globals().get('__file__') or os.path.join(os.getcwd(), 'scripts', 'log_session_bets.py')
ROOT       = os.path.dirname(os.path.dirname(os.path.abspath(_THIS_FILE)))
BETS_PATH  = os.path.join(ROOT, 'data', 'bets.json')
SNAP_DIR   = os.path.join(ROOT, 'data', 'clv_snapshots')

sys.path.insert(0, os.path.join(ROOT, "lib"))
from atomic_json import write_json_atomic

REAL_MONEY_TIERS = {'HIGH', 'MEDIUM'}
DRY_RUN = '--dry-run' in sys.argv
NO_CLV  = '--no-clv'  in sys.argv


# ── Helpers ──────────────────────────────────────────────────────────────────

def now_utc() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def stable_key(date: str, game: str, market: str, ticker: str) -> str:
    """Composite deduplication key matching write_pending_bets.py convention."""
    return f"{date}|{game}|{market}|{ticker or 'NO_TICKER'}"


def american_to_implied(american: int) -> float:
    """Convert American odds to 0-1 implied probability (no vig)."""
    o = float(american)
    if o >= 0:
        return round(100 / (o + 100), 4)
    else:
        return round(abs(o) / (abs(o) + 100), 4)


def series_ticker_from(ticker: str) -> str:
    """Extract series ticker from full Kalshi ticker (first hyphen-segment)."""
    if not ticker:
        return ''
    return ticker.split('-')[0]


# ── Validation ───────────────────────────────────────────────────────────────

REQUIRED_FIELDS = ['date', 'game', 'market', 'side', 'ticker',
                   'entryPrice', 'stake', 'modelPct', 'marketPct',
                   'edgePct', 'confidence']

VALID_CONFIDENCE = {'HIGH', 'MEDIUM', 'PAPER'}

DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')


def validate_bet(bet: dict, idx: int) -> list[str]:
    """Return list of error strings; empty list = valid."""
    errors = []
    label  = f"Bet[{idx}] {bet.get('game','?')} {bet.get('market','?')}"

    for f in REQUIRED_FIELDS:
        if bet.get(f) is None or bet.get(f) == '':
            errors.append(f"{label}: missing required field '{f}'")

    # Type checks
    if errors:           # skip further checks if basics missing
        return errors

    if not DATE_RE.match(str(bet['date'])):
        errors.append(f"{label}: 'date' must be YYYY-MM-DD, got '{bet['date']}'")

    conf = str(bet['confidence']).upper()
    if conf not in VALID_CONFIDENCE:
        errors.append(f"{label}: 'confidence' must be HIGH/MEDIUM/PAPER, got '{conf}'")

    if not isinstance(bet['entryPrice'], (int, float)):
        errors.append(f"{label}: 'entryPrice' must be a number (American odds)")

    if not isinstance(bet['stake'], (int, float)) or float(bet['stake']) <= 0:
        errors.append(f"{label}: 'stake' must be a positive number")

    if not isinstance(bet['modelPct'], (int, float)):
        errors.append(f"{label}: 'modelPct' must be a number")

    if not isinstance(bet['marketPct'], (int, float)):
        errors.append(f"{label}: 'marketPct' must be a number")

    if not isinstance(bet['edgePct'], (int, float)):
        errors.append(f"{label}: 'edgePct' must be a number")

    # Game-started check (skip for already-settled backfills with explicit override)
    start_str = bet.get('scheduledStartTime', '')
    is_manual = bet.get('post_entry_manual_review', False)
    if start_str and not is_manual:
        try:
            start_dt = datetime.fromisoformat(start_str.replace('Z', '+00:00'))
            if start_dt < datetime.now(tz=timezone.utc):
                errors.append(
                    f"{label}: game already started "
                    f"(scheduledStartTime={start_str}). "
                    f"Set 'post_entry_manual_review': true to allow backfill."
                )
        except ValueError:
            pass  # unparseable start time — don't block

    return errors


# ── Build canonical bet record ────────────────────────────────────────────────

def build_bet_record(bet: dict, now_ts: str) -> dict:
    """Build a bets.json-compatible record from a session bet input."""
    conf          = str(bet['confidence']).upper()
    is_real_money = conf in REAL_MONEY_TIERS
    status_init   = bet.get('status', 'pending')
    source        = bet.get('source', 'session_analysis')
    ts            = bet.get('timestamp') or now_ts

    # Coerce stake: PAPER bets use declared stake (could be $1 or overridden)
    stake = float(bet['stake'])

    # CLV fields: either supplied (backfill) or set to pending/unavailable
    clv_status = bet.get('clvStatus', 'not_yet_captured')
    clv_reason = bet.get('clvReason',
                         'Session bet. Pregame CLV snapshot was not captured. '
                         'Settle via update-clv workflow when closing price available.')

    record = {
        # Identity
        'date':               str(bet['date']),
        'game':               str(bet['game']),
        'market':             str(bet['market']),
        'ticker':             str(bet['ticker']),
        'side':               str(bet['side']).upper(),
        'betTeam':            bet.get('betTeam', ''),

        # Pricing
        'entryPrice':         int(bet['entryPrice']),
        'stake':              stake,
        'type':               'real' if is_real_money else 'paper',
        'confidence':         conf,

        # Model
        'edgePct':            float(bet['edgePct']),
        'modelPct':           float(bet['modelPct']),
        'kalshiVF':           float(bet['marketPct']),

        # Metadata
        'origin':             source,
        'source':             source,
        'timestamp':          ts,
        'scheduledStartTime': bet.get('scheduledStartTime', ''),

        # Status
        'status':             status_init,
        'result':             bet.get('result', None),
        'pl':                 bet.get('pl', None),
        'finalScore':         bet.get('finalScore', None),

        # CLV
        'betTimeLine':        int(bet['entryPrice']),
        'closingPrice':       bet.get('closingPrice', None),
        'closingTimestamp':   bet.get('closingTimestamp', None),
        'clvStatus':          clv_status,
        'clvReason':          clv_reason,
        'clvSource':          bet.get('clvSource', 'pending'),
        'clvDelta':           bet.get('clvDelta', None),
        'clv':                bet.get('clv', None),
        'clvError':           None,

        # Optional rich fields
        'factors':            bet.get('factors', {}),
        'notes':              bet.get('notes', ''),
        'gatesFired':         bet.get('gatesFired', []),
        'post_entry_manual_review': bet.get('post_entry_manual_review', False),
    }
    return record


# ── CLV ticker record ─────────────────────────────────────────────────────────

def build_ticker_record(bet: dict, now_ts: str) -> dict:
    conf = str(bet['confidence']).upper()
    return {
        'ticker':             str(bet['ticker']),
        'seriesTicker':       series_ticker_from(bet['ticker']),
        'market':             str(bet['market']),
        'game':               str(bet['game']),
        'date':               str(bet['date']),
        'scheduledStartTime': bet.get('scheduledStartTime', ''),
        'confidenceTier':     conf,
        'stake':              float(bet['stake']),
        'source':             bet.get('source', 'session_analysis'),
        'addedAt':            now_ts,
    }


# ── Write helpers ─────────────────────────────────────────────────────────────

def load_bets() -> list:
    if not os.path.exists(BETS_PATH):
        return []
    with open(BETS_PATH) as f:
        return json.load(f)


def save_bets(bets: list) -> None:
    write_json_atomic(bets, BETS_PATH, indent=2)


def existing_keys(bets: list) -> set:
    return {
        stable_key(b.get('date',''), b.get('game',''),
                   b.get('market',''), b.get('ticker',''))
        for b in bets
    }


def load_tracked_tickers(date: str) -> dict:
    """Load existing tracked_tickers.json for date; return {ticker: record}."""
    path = os.path.join(SNAP_DIR, date, 'tracked_tickers.json')
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        data = json.load(f)
    return {t['ticker']: t for t in data.get('tickers', [])}


def save_tracked_tickers(date: str, tickers: dict, now_ts: str) -> str:
    """Write merged tickers back; return path written."""
    date_dir = os.path.join(SNAP_DIR, date)
    os.makedirs(date_dir, exist_ok=True)
    path = os.path.join(date_dir, 'tracked_tickers.json')
    merged = sorted(tickers.values(), key=lambda x: x['ticker'])
    out = {
        'date':        date,
        'generatedAt': now_ts,
        'count':       len(merged),
        'tickers':     merged,
    }
    with open(path, 'w') as f:
        json.dump(out, f, indent=2)
    return path


# ── Main ──────────────────────────────────────────────────────────────────────

def main(args: list[str]) -> int:
    # ── Find input file ───────────────────────────────────────────────────
    input_files = [a for a in args if not a.startswith('--')]
    if not input_files:
        print("ERROR: provide path to session bets JSON file")
        print("  usage: python3 scripts/log_session_bets.py data/session_bets/YYYY-MM-DD.json")
        return 1

    input_path = input_files[0]
    if not os.path.exists(input_path):
        print(f"ERROR: input file not found: {input_path}")
        return 1

    with open(input_path) as f:
        session_bets = json.load(f)

    if not isinstance(session_bets, list):
        print(f"ERROR: session bets file must be a JSON array, got {type(session_bets)}")
        return 1

    now_ts = now_utc()
    print(f"[log_session_bets] Processing {len(session_bets)} session bet(s)")
    print(f"  Input:   {input_path}")
    print(f"  DryRun:  {DRY_RUN}")
    print(f"  NoCLV:   {NO_CLV}")
    print()

    # ── Validate all bets first ───────────────────────────────────────────
    all_errors = []
    for idx, bet in enumerate(session_bets):
        errs = validate_bet(bet, idx)
        all_errors.extend(errs)

    if all_errors:
        print("VALIDATION FAILED:")
        for e in all_errors:
            print(f"  ✗ {e}")
        return 1

    print(f"  Validation: all {len(session_bets)} bets passed")

    # ── Load existing bets ────────────────────────────────────────────────
    existing = load_bets()
    keys     = existing_keys(existing)

    # ── Process bets ──────────────────────────────────────────────────────
    added_bets    = []
    skipped_bets  = []

    for bet in session_bets:
        k = stable_key(bet['date'], bet['game'], bet['market'], bet['ticker'])
        if k in keys:
            skipped_bets.append(bet)
            continue
        record = build_bet_record(bet, now_ts)
        added_bets.append(record)
        keys.add(k)

    # ── Report bet results ────────────────────────────────────────────────
    print(f"\n  Bets to add:  {len(added_bets)}")
    print(f"  Bets skipped (duplicates): {len(skipped_bets)}")

    for b in added_bets:
        conf  = b['confidence']
        emoji = '🟢' if conf == 'HIGH' else ('🟡' if conf == 'MEDIUM' else '📄')
        print(f"    {emoji} {b['game']} | {b['market']} | {b['side']} | "
              f"{b['entryPrice']:+d} | ${b['stake']:.2f} | "
              f"edge={b['edgePct']:.2f}% | status={b['status']}")

    for b in skipped_bets:
        print(f"    ⏭  SKIP (duplicate): {b['game']} {b['market']} {b['ticker']}")

    # ── Process CLV tickers ───────────────────────────────────────────────
    tickers_to_add   = {}
    tickers_skipped  = {}

    if not NO_CLV:
        for bet in session_bets:
            conf = str(bet['confidence']).upper()
            # Only track real-money tiers (or all, keyed by ticket_id; paper tickers
            # have limited CLV value but we add them anyway for data accumulation)
            ticker = bet['ticker']
            date   = bet['date']

            existing_tickers = load_tracked_tickers(date)

            if ticker in existing_tickers:
                tickers_skipped[ticker] = bet
            else:
                tickers_to_add[ticker] = build_ticker_record(bet, now_ts)

        print(f"\n  CLV tickers to add:    {len(tickers_to_add)}")
        print(f"  CLV tickers skipped:   {len(tickers_skipped)}")
        for t, rec in tickers_to_add.items():
            print(f"    + {t} ({rec['market']} {rec['game']})")
        for t in tickers_skipped:
            print(f"    ⏭  SKIP ticker (already tracked): {t}")

    # ── Dry-run gate ──────────────────────────────────────────────────────
    if DRY_RUN:
        print("\n  DRY RUN — no files written.")
        return 0

    # ── Write bets.json ───────────────────────────────────────────────────
    if added_bets:
        existing.extend(added_bets)
        save_bets(existing)
        print(f"\n  ✅ Written {len(added_bets)} bet(s) → {BETS_PATH}")
    else:
        print(f"\n  ℹ️  No new bets to write (all duplicates).")

    # ── Write CLV tickers ─────────────────────────────────────────────────
    if not NO_CLV and tickers_to_add:
        # Group by date (most cases same date, but handle multi-day backfills)
        by_date: dict[str, dict] = {}
        for ticker, rec in tickers_to_add.items():
            d = rec['date']
            if d not in by_date:
                by_date[d] = load_tracked_tickers(d)
            by_date[d][ticker] = rec

        for d, tickers in by_date.items():
            path = save_tracked_tickers(d, tickers, now_ts)
            print(f"  ✅ Written {len(tickers)} ticker(s) → {path}")

    elif not NO_CLV:
        print(f"  ℹ️  No new CLV tickers to write (all duplicates).")

    print(f"\n[log_session_bets] Done.")
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
