"""
lib/edgelab/ids.py
====================
Deterministic identifier builders for the EdgeLab schema (data/edgelab/schema_v1).

Every ID here is either a straight pass-through of an already-stable
upstream identifier (Kalshi's own marketTicker/eventTicker/seriesTicker),
or a deterministic hash of stable inputs -- never a random UUID for
anything that must dedupe across reruns. Re-ingesting the same upstream
snapshot/ledger file must always produce the same IDs, so storage.py's
append-with-dedup can tell "already recorded this" from "genuinely new".
"""

import hashlib
import os
import time
import uuid
from datetime import datetime, timezone


def _sha1(*parts: str) -> str:
    h = hashlib.sha1()
    for p in parts:
        h.update((p or "").encode("utf-8"))
        h.update(b"\x1f")  # unit separator, avoids "a"+"bc" colliding with "ab"+"c"
    return h.hexdigest()


def build_game_id(mlb_game_pk=None, game_date=None, away_team=None, home_team=None):
    """
    Prefer the MLB Stats API's own gamePk (stable, doubleheader-safe).
    Falls back to a deterministic 'date_away_home' string only when gamePk
    isn't known yet -- this fallback IS doubleheader-collision-prone (same
    known gap as lib/kalshi_mlb_contract_parser.py's synthesized gameId),
    documented here rather than silently hidden. Callers that later learn
    the real gamePk should re-write the Game record with it and update
    every dependent record's gameId -- not currently automated in Phase 1.
    """
    if mlb_game_pk:
        return str(mlb_game_pk)
    if game_date and away_team and home_team:
        return f"{game_date}_{away_team}_{home_team}"
    return None


def build_market_observation_id(market_ticker: str, captured_at: str) -> str:
    return _sha1("market_observation", market_ticker, captured_at)


def build_clv_quote_id(market_ticker: str, captured_at: str) -> str:
    # Deliberately the same scheme as build_market_observation_id: a CLV
    # quote IS a market observation, just tagged with checkpoint/isClosingQuote.
    return _sha1("market_observation", market_ticker, captured_at)


def build_model_evaluation_id(run_id: str, market_ticker: str) -> str:
    return _sha1("model_evaluation", run_id, market_ticker)


def build_recommendation_id(run_id: str, market_ticker: str) -> str:
    return _sha1("recommendation", run_id, market_ticker)


def build_settlement_id(game_id: str, market_ticker: str) -> str:
    return _sha1("settlement", game_id or "", market_ticker)


def build_bet_id(game_id=None, market_ticker=None, entry_timestamp=None):
    """
    Deterministic when the inputs exist (the normal case for anything
    ingested from bets.json/data/bets.json, or logged with a real
    ticker+timestamp) so re-ingesting the legacy ledgers is idempotent.
    Falls back to a time-ordered token (ULID-style: ms timestamp + random
    suffix) only for a manual entry missing one of those inputs -- still
    unique, just not re-derivable from the bet's own content.
    """
    if market_ticker and entry_timestamp:
        return _sha1("bet", game_id or "", market_ticker, entry_timestamp)
    ms = int(time.time() * 1000)
    return f"bet_{ms:013d}_{uuid.uuid4().hex[:8]}"


def new_run_id(run_type: str, github_run_id=None) -> str:
    """
    github_run_id (from `${{ github.run_id }}`) is used verbatim when
    running in Actions, so a run's ID matches the workflow log that
    produced it. Otherwise a timestamp + random suffix, for local/manual runs.
    """
    ts = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if github_run_id:
        return f"{run_type}_{ts}_gh{github_run_id}"
    return f"{run_type}_{ts}_{uuid.uuid4().hex[:8]}"


def utc_now_iso() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
