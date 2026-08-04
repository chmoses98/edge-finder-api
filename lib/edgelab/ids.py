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


def _sha1(*parts) -> str:
    """Accepts str or any value with a stable str() form (e.g. an MLB gamePk int) -- never raises on a non-string identifier."""
    h = hashlib.sha1()
    for p in parts:
        h.update((str(p) if p is not None else "").encode("utf-8"))
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


def build_snapshot_id(snapshot_stage: str, snapshot_date: str, production_run_key: str = None) -> str:
    """
    Deterministic and write-once per (snapshotStage, snapshotDate,
    productionRunKey) -- a second capture attempt for the same key must
    re-derive the SAME id, so the write-once storage primitive can detect
    "this snapshot already exists" rather than minting a colliding second
    one under a different name. production_run_key distinguishes separate
    production decision moments for the same date (lineup recheck,
    doubleheader, retry) -- see lib.edgelab.snapshot's module docstring.
    Omitted (None) for stages that are not run-keyed
    (POST_GAME_SETTLEMENT, CLOSING_LINE).
    """
    return _sha1("snapshot", snapshot_stage, snapshot_date, production_run_key or "")


def build_bankroll_transaction_id(transaction_type: str, occurred_at: str, reference: str = None) -> str:
    """
    Deterministic per (type, occurredAt, reference) so re-submitting the
    same manual bankroll entry (e.g. a retried GitHub Actions form
    submission) is idempotent, exactly like build_bet_id. `reference` is
    typically a betId for STAKE_RESERVED/STAKE_RETURNED/REALIZED_PNL
    (computed transactions, never independently written -- see
    lib/edgelab/bankroll.py) or None for a pure cash transaction
    (DEPOSIT/WITHDRAWAL/ADJUSTMENT/STARTING_BALANCE/USER_REPORTED_BALANCE).
    """
    return _sha1("bankroll_transaction", transaction_type, occurred_at, reference or "")


def build_bet_id(game_id=None, market_ticker=None, entry_timestamp=None, *, import_batch_id=None, source_row=None, side=None):
    """
    Deterministic when the inputs exist (the normal case for anything
    ingested from bets.json/data/bets.json, or logged with a real
    ticker+timestamp) so re-ingesting the legacy ledgers is idempotent.

    Timestamp-Optional Manual Imports milestone: when entry_timestamp is
    not known (the normal case for a bulk import that only has a game
    date and bet details, never an exact placement time), identity comes
    from hash(importBatchId, sourceRow, marketTicker, side) instead --
    still fully deterministic, so re-running the identical import batch
    is a true no-op, while two distinct rows in the same batch (distinct
    source_row) or two distinct batches (distinct import_batch_id) never
    collide. This is checked BEFORE the entry_timestamp branch only in
    the sense that a caller with a real entry_timestamp should simply not
    pass import_batch_id/source_row -- the two identity schemes are
    mutually exclusive per bet, never combined.

    Falls back to a time-ordered token (ULID-style: ms timestamp + random
    suffix) only when NEITHER scheme's inputs are available -- still
    unique, just not re-derivable from the bet's own content.
    """
    if market_ticker and entry_timestamp:
        return _sha1("bet", game_id or "", market_ticker, entry_timestamp)
    if market_ticker and import_batch_id is not None and source_row is not None:
        return _sha1("bet_import", import_batch_id, source_row, market_ticker, side or "")
    ms = int(time.time() * 1000)
    return f"bet_{ms:013d}_{uuid.uuid4().hex[:8]}"


def build_import_batch_id(*parts) -> str:
    """
    Stable batch identifier for a bulk manual-bet import (Timestamp-
    Optional Manual Imports milestone) -- deterministic hash of the
    caller-supplied content (typically the normalized row payloads
    themselves), so re-running the identical import file/payload always
    resolves to the SAME importBatchId and therefore the same betIds
    (see build_bet_id), making a repeat import a pure no-op. A caller
    that wants to force two literally-identical rows to be treated as
    genuinely separate real-world tranches should fold a distinguishing
    label (e.g. a batch label/description) into `parts`.
    """
    return _sha1("import_batch", *parts)


def build_postmortem_id(game_date: str) -> str:
    """One logical postmortem per calendar date; corrections are new revisions of the SAME id, never a new one."""
    return _sha1("postmortem", game_date)


def build_replay_run_id(snapshot_id: str, replay_mode: str, candidate_model_commit_sha: str,
                         replay_framework_version: str) -> str:
    """
    Deterministic and write-once per (snapshotId, replayMode,
    candidateModelCommitSha, replayFrameworkVersion) -- a second replay
    attempt with identical inputs must re-derive the SAME id, so it can
    verify-and-no-op rather than producing a duplicate run.
    """
    return _sha1("replay_run", snapshot_id, replay_mode, candidate_model_commit_sha or "", replay_framework_version)


def build_replay_result_id(replay_run_id: str, game_id, market_key: str) -> str:
    return _sha1("replay_result", replay_run_id, game_id or "", market_key)


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
