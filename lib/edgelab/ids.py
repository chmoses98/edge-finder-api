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


def build_bet_id(game_id=None, market_ticker=None, entry_timestamp=None, *, import_batch_id=None, source_bet_key=None, side=None):
    """
    Deterministic when the inputs exist (the normal case for anything
    ingested from bets.json/data/bets.json, or logged with a real
    ticker+timestamp) so re-ingesting the legacy ledgers is idempotent.

    Timestamp-Optional Manual Imports milestone: when entry_timestamp is
    not known (the normal case for a bulk import that only has a game
    date and bet details, never an exact placement time), identity comes
    from hash(importBatchId, sourceBetKey, marketTicker, side) instead.

    sourceBetKey is a CALLER-ASSIGNED STABLE per-row identifier (e.g.
    "bet-01") -- deliberately never the row's raw list position/index.
    An earlier design used the row's array index (sourceRow) here, which
    broke under reordering or insertion: the same logical row got a
    DIFFERENT identity just because something else moved in the list, or
    an unrelated new row got inserted before it. sourceBetKey travels
    WITH its row regardless of position, so reordering the payload or
    inserting a new row never changes an existing row's identity.

    A prior design also let importBatchId default to a hash of the
    payload's own gameDate(s) when not given -- found (maintainer review)
    to be insufficiently unique: it collides across separate same-day
    manual betting sessions, multiple same-day imports, and reordered/
    modified payloads sharing the same date. importBatchId is now
    REQUIRED (by build_manual_bet_record -- see its docstring) to be
    explicit for any timestamp-free row, generated by the calling client
    (Claude, during the handoff) as a genuinely distinguishing label --
    never silently derived from ambiguous shared fields like a date.

    Both import_batch_id and source_bet_key must be given TOGETHER for
    this branch; still fully deterministic, so re-running the identical
    import (same batch id, same row key) is a true no-op, while two
    distinct rows (distinct source_bet_key) or two distinct real-world
    sessions (distinct import_batch_id) never collide. This is checked
    BEFORE the entry_timestamp branch only in the sense that a caller
    with a real entry_timestamp should simply not pass
    import_batch_id/source_bet_key -- the two identity schemes are
    mutually exclusive per bet, never combined.

    Falls back to a time-ordered token (ULID-style: ms timestamp + random
    suffix) only when NEITHER scheme's inputs are available -- still
    unique, just not re-derivable from the bet's own content.
    """
    if market_ticker and entry_timestamp:
        return _sha1("bet", game_id or "", market_ticker, entry_timestamp)
    if market_ticker and import_batch_id is not None and source_bet_key is not None:
        return _sha1("bet_import", import_batch_id, source_bet_key, market_ticker, side or "")
    ms = int(time.time() * 1000)
    return f"bet_{ms:013d}_{uuid.uuid4().hex[:8]}"


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


def build_scored_replay_run_id(replay_run_id: str, scoring_framework_version: str) -> str:
    """
    Deterministic per (replayRunId, scoringFrameworkVersion) ONLY -- never
    over the settlement/CLV/bet content being scored. This is
    deliberate: a scored replay's IDENTITY tracks which immutable
    ReplayRun it scores and which version of the scoring logic produced
    it, not the canonical inputs available at scoring time. Those inputs
    (settlement, CLV, bets) can change later (e.g. a corrected
    settlement) -- rerunning scoring must re-derive the SAME id and
    update the scored record's CONTENT in place, never mint a new id
    that would orphan the previous scoring of the same replay run. See
    lib/edgelab/scored_replay.py:write_scored_replay_outputs.
    """
    return _sha1("scored_replay_run", replay_run_id, scoring_framework_version)


def build_scored_replay_result_id(replay_result_id: str, scoring_framework_version: str) -> str:
    return _sha1("scored_replay_result", replay_result_id, scoring_framework_version)


def new_run_id(run_type: str, github_run_id=None, github_run_attempt=None, content_signature=None) -> str:
    """
    github_run_id (from `${{ github.run_id }}`) is used verbatim when
    running in Actions, so a run's ID matches the workflow log that
    produced it. github_run_attempt (`${{ github.run_attempt }}`) is
    appended when known, distinguishing a manual re-run of the same
    workflow run from its original attempt.

    Research-Run Manifest Identity fix: a bare `run_type + github_run_id`
    (rounded to second-resolution wall-clock time) is NOT enough to
    distinguish two invocations of the same run_type inside the SAME
    GitHub Actions run -- confirmed by a real CI failure where two
    scripts/edgelab/ingest_market_observations.py invocations in the same
    job landed in the same wall-clock second, producing IDENTICAL run
    ids with no content_signature; storage.append_records' dedup-by-runId
    then silently discarded the second invocation's manifest even though
    it had genuinely different inputs and output counts (see
    tests/edgelab/test_ingest_market_observations_script.py).

    content_signature: an optional caller-supplied deterministic string
    identifying WHAT this particular invocation actually processed (e.g.
    lib.edgelab.ids.build_run_content_signature over the sorted input
    file paths/capture identities). Two invocations of the same run_type
    inside the same GitHub run/attempt that process genuinely DIFFERENT
    inputs must pass different content_signature values, so their
    manifests never collide. Two invocations processing the EXACT SAME
    inputs should pass the SAME content_signature, so a true retry/no-op
    rerun deterministically re-derives the identical id (write-once
    semantics, matching build_snapshot_id/build_replay_run_id elsewhere
    in this module) rather than manufacturing a spurious duplicate
    manifest for identical work.

    Falls back to a random suffix (never bare second-resolution
    wall-clock time alone) when github_run_id is known but no
    content_signature was supplied -- preserves the old uniqueness
    guarantee for call sites that don't yet have a meaningful content
    signature to pass. Local/manual runs (github_run_id is None) are
    unaffected -- unchanged ULID-style timestamp + random suffix.
    """
    ts = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if github_run_id:
        parts = [run_type, ts, f"gh{github_run_id}"]
        if github_run_attempt:
            parts.append(f"a{github_run_attempt}")
        parts.append(content_signature if content_signature else uuid.uuid4().hex[:8])
        return "_".join(parts)
    return f"{run_type}_{ts}_{uuid.uuid4().hex[:8]}"


def build_run_content_signature(*parts) -> str:
    """
    Deterministic content signature for a research-run manifest's
    new_run_id(content_signature=...) -- e.g. hash(source_system, *sorted
    snapshot input paths) so two ingestion invocations that process
    different inputs never collide, while two invocations processing the
    exact same inputs correctly re-derive the same signature (a true
    retry/no-op rerun is idempotent, never a spurious duplicate).
    """
    return _sha1("run_content", *parts)


def utc_now_iso() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
