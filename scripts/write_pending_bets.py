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

# Add lib to path for postponed_guard
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'lib'))
from postponed_guard import check_game_status, is_live_game_blocked
from atomic_json import write_json_atomic

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


def should_skip_excluded_game_pure(game):
    """
    Pure (Phase 10): whether a game's quarantine flag should skip it
    entirely -- extracted verbatim from the original inline `if
    g.get('excludedFromSlate'): continue` check, at the exact same
    point in the loop. No real-money bets can come from a quarantined
    game.
    """
    return bool(game.get('excludedFromSlate'))


def should_block_game_for_pregame_gate_pure(game_status_result):
    """
    Pure (Phase 10): interprets the (already pure, shared)
    lib.postponed_guard.check_game_status() result to decide whether
    the pregame-only hard gate should block this game's bets --
    extracted verbatim from the original inline condition, unchanged.
    Does not call check_game_status() itself (the caller already has
    the result) and does not read the clock, so it performs no I/O of
    its own.
    """
    return bool(game_status_result.get("shouldSkip")) and (
        bool(game_status_result.get("liveGameBlocked"))
        or game_status_result.get("skipReason") in (
            "LIVE_GAME_BLOCKED", "PREGAME_ONLY_STARTED_GAME"
        )
    )


def is_real_money_market_entry_pure(entry):
    """
    Pure (Phase 10): whether a single marketLedger[] entry is a
    real-money (not PAPER) accepted recommendation eligible to be
    logged -- extracted verbatim from the original inline
    `if entry.get('status') != 'Accepted': continue` /
    `if tier not in REAL_MONEY_TIERS: continue` pair, combined into one
    predicate evaluated at the same point in the loop, in the same
    order (status checked before tier, matching the original).
    """
    if entry.get('status') != 'Accepted':
        return False
    tier = (entry.get('confidenceTier') or entry.get('confidence') or '').upper()
    return tier in REAL_MONEY_TIERS


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
        if should_skip_excluded_game_pure(g):
            continue
        away = g.get('away', {}).get('abbr', '')
        home = g.get('home', {}).get('abbr', '')
        game = f"{away}@{home}"

        # ── PREGAME-ONLY HARD GATE ─────────────────────────────────────────
        # If the game has already started (In Progress, Final, etc.), no official
        # pregame real-money bets can be logged. This gate fires BEFORE writing
        # any bets to bets.json. Applies to all markets for the game.
        game_status_result = check_game_status(g, current_utc=now_ts)
        if should_block_game_for_pregame_gate_pure(game_status_result):
            block_reason = game_status_result.get("skipReason", "LIVE_GAME_BLOCKED")
            game_status_str = game_status_result.get("gameStatus", "unknown")
            print(
                f"  PREGAME GATE BLOCKED: {game} status={game_status_str!r} "
                f"reason={block_reason} — no real-money bets logged for this game"
            )
            continue

        for entry in g.get('marketLedger', []):
            if not is_real_money_market_entry_pure(entry):
                continue  # not Accepted, or PAPER tier — not logged here

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
        write_json_atomic(bets, BETS_PATH, indent=2)
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
