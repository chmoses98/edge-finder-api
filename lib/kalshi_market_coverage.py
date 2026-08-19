#!/usr/bin/env python3
"""
lib/kalshi_market_coverage.py
================================
Full-archived-market-universe accounting layer for the MLB Kalshi slate
coverage audit (docs/KALSHI_MLB_MARKET_COVERAGE_AUDIT.md).

This module answers exactly one question for every contract Kalshi
returned for a given date: "what happened to it?" -- and guarantees the
answer is always one of a small, closed set of terminal states, never a
silent gap. It is a thin accounting layer ON TOP OF
scripts.discover_kalshi_mlb_markets.discover() (the existing universal
discovery/classification/pricing engine, unchanged here) -- it adds no
new parsing, classification, or probability logic of its own.

Deliberately does NOT import, call, or modify anything from
scripts/build_market_ledger.py, scripts/risk_gate.py,
scripts/write_pending_bets.py, or scripts/validate_slate_final.py --
same "read/classify/write only, never touches betting logic" safety
boundary discover_kalshi_mlb_markets.py itself already documents (see
its own module docstring and .github/workflows/discover-kalshi-mlb-markets.yml).
This module is audit/coverage-visibility only. It never changes which
markets are real-money eligible, never loosens a risk gate, and never
fabricates a probability for a family with no model.

TWO INDEPENDENT ACCOUNTING LAYERS
------------------------------------
1. `coverage_accounting()` sums the terminal states already attached to
   discover()'s own returned contracts -- this proves every contract
   discover() DID return has exactly one explained fate, but a bug that
   drops a raw market BEFORE discover() returns it (extraction,
   dedup, parser routing, date filtering) would not be visible to this
   layer alone, since its denominator (`len(ledger_rows)`) is itself
   discover()'s output.
2. `raw_archive_accounting()` is the independent, stronger check this
   audit actually needs: it re-derives the set of unique raw Kalshi
   ticker identities directly from the SAME raw search_doc discover()
   itself reads (mirroring, but never calling into,
   scripts.discover_kalshi_mlb_markets.extract_raw_markets's own
   dedup-by-ticker semantics), and diffs that set against the tickers
   that actually appear in the coverage ledger. Its denominator
   (`rawArchivedUnique`) is NEVER derived from discover()'s output, so a
   contract that vanishes anywhere inside discover() -- extraction,
   dedup, parser routing, date filtering, classification -- shows up as
   a nonzero `trueSilentRemainderCount`, not a false "0 unaccounted."

TERMINAL STATES
----------------
Every contract discover() returns is classified into exactly one of:

  FULLY_EVALUATED         -- production adapter computed a fair
                              probability (modelSupportStatus SUPPORTED,
                              lib.kalshi_probability_adapters); this is
                              the analysis-coverage state, independent
                              of whether the contract would ever clear a
                              recommendation threshold.
  RESEARCH_MODEL_ONLY     -- production has NO adapter for this family
                              (modelSupportStatus UNSUPPORTED), but the
                              repository's hitter research engine
                              independently produced a real
                              modelProbability for this exact ticker, at
                              or before this run's own current market
                              observation (see "HITTER RESEARCH
                              PROVENANCE" below -- never a future-dated
                              projection, never a guessed match).
                              Distinct from UNSUPPORTED_MODEL_FAMILY --
                              "no production adapter" is not the same
                              claim as "no model anywhere in this
                              repository." Never routed into
                              marketLedger/risk_gate/write_pending_bets
                              -- see realMoneyEligibilityStatus="RESEARCH_ONLY"
                              on these rows.
  MISSING_REQUIRED_CONTEXT -- a model (production OR research) exists for
                              this family but a required input was
                              unavailable this run (modelSupportStatus
                              MISSING_DATA; or the hitter research
                              evidence reports LINEUP_UNCONFIRMED/
                              PLAYER_NOT_IN_STARTING_LINEUP/
                              MISSING_REQUIRED_CONTEXT/MODEL_ERROR for
                              this exact ticker).
  UNSUPPORTED_MODEL_FAMILY -- no usable model or projection exists
                              ANYWHERE in this repository's analysis
                              stack for this contract AT THIS OBSERVATION
                              -- neither lib.kalshi_probability_adapters
                              nor a hitter research projection dated at or
                              before this run's current market
                              observation. Preserved, classified, never
                              silently dropped, never assigned an
                              invented probability.
  PARSER_UNRESOLVED       -- parse_contract() raised on this raw market
                              (classificationStatus == "parse_error"), or
                              lib.kalshi_mlb_market_classifier could not
                              recognize the series/ticker structure at all
                              (classificationStatus == "unclassified").
  GAME_MAPPING_UNRESOLVED -- no slate game matched this contract's (date,
                              away, home) in THIS run's own slate_doc --
                              gameId fell back to parse_contract's own
                              synthetic ticker-derived id, so no real
                              projection context (production OR research)
                              can be trusted for it. Checked before, and
                              takes priority over, hitter research
                              linkage -- a historical research snapshot
                              referencing the same ticker is never used
                              to infer THIS run's own game context (that
                              would reintroduce exactly the provenance
                              conflation this module exists to prevent).
  AMBIGUOUS_TICKER_MATCH   -- this contract's player/subject identity
                              could not be resolved to exactly one
                              candidate without guessing (the hitter
                              research evidence's own PLAYER_ID_UNRESOLVED/
                              AMBIGUOUS_TICKER_MATCH outcomes).
  STARTED_GAME_EXCLUDED   -- matched a real slate game in THIS run's own
                              slate_doc, but that game's status (from
                              THIS run's own slate/game data -- NEVER
                              inferred from a research snapshot's
                              possibly-much-older observation) is not one
                              of lib.postponed_guard.ACTIVE_PREGAME_STATUSES
                              at THIS run's observation time -- this
                              contract is intentionally excluded from
                              pregame coverage accounting, not missed.
  NOT_APPLICABLE          -- contract's own ticker-derived date does not
                              match this discovery run's date_str (e.g. a
                              different day's early-posted contract that
                              bled into today's broad search results) --
                              legitimately out of scope for this date, but
                              still accounted for, never a silent drop.
  NOT_EVALUATED_BUG        -- defensive fallback: a contract whose fields
                              don't match any of the above (should be
                              unreachable by construction; any nonzero
                              count here is itself a coverage-audit defect
                              to investigate, never swept under UNSUPPORTED).

HITTER RESEARCH PROVENANCE (never conflated)
----------------------------------------------
Two genuinely different observations are involved in pricing a hitter
prop's CURRENT edge, and this module never lets one silently stand in for
the other:

  1. WHEN THE PROJECTION WAS COMPUTED -- lib.research.hitter_prospective_snapshot's
     scheduler (.github/workflows/hitter-snapshot-scheduler.yml, ~every 15
     minutes during the pregame window) checkpoints one row per hitter per
     due checkpoint (T_MINUS_90/60/30, LINEUP_CONFIRMATION,
     HITTER_CLOSING_WINDOW) to the APPEND-ONLY store
     data/edgelab/hitter_projection_snapshots/<date>.jsonl -- this is the
     PRIMARY research source this module reads (`load_hitter_prospective_snapshots`).
     scripts/build_hitter_projection_board.py's canonical board artifact
     (data/pipeline/<date>/hitter_projection_board.json) is NOT written by
     that scheduler (every scheduler-triggered call passes dry_run=True --
     see run_hitter_prospective_snapshots.py's own module docstring); it
     is only produced by a separate, on-demand/standalone invocation, so
     this module treats it strictly as a FALLBACK, used only when its own
     provenance timestamp is verifiably at or before the current market
     observation (`_board_fallback_eligible`) -- never assumed fresh.
  2. WHEN THE MARKET IS CURRENTLY BEING OBSERVED -- each contract's own
     `currentMarketObservedAt` (this run's raw Kalshi snapshot_ts for
     THAT exact ticker, or the search document's own fetched_at) and its
     own `yesAsk`/`noAsk`. `select_prospective_hitter_snapshot` NEVER
     selects a projection whose own `snapshotGeneratedAt` is after this
     timestamp (no future leakage) -- and, symmetrically, every CURRENT
     economics field (`current*`) is computed from THIS observation's own
     price, never from the price recorded at projection time
     (`projectionTime*`, retained separately for CLV/provenance only).
"""
import json
import os
from datetime import datetime

from lib.pipeline_artifacts import read_stage_artifact
from lib.postponed_guard import ACTIVE_PREGAME_STATUSES
from lib.research.hitter_board_builder import MARKET_FAMILY_TO_DISTRIBUTION_KEY
from lib.edgelab.kalshi_fees import (
    net_expected_value_per_dollar,
    fee_adjusted_break_even_probability,
    fee_adjusted_bet_up_to_price,
)
from scripts.discover_kalshi_mlb_markets import (
    discover,
    STATUS_SUPPORTED,
    STATUS_UNSUPPORTED,
    STATUS_MISSING_DATA,
)

HITTER_SNAPSHOTS_DIR = os.path.join("data", "edgelab", "hitter_projection_snapshots")

FULLY_EVALUATED = "FULLY_EVALUATED"
RESEARCH_MODEL_ONLY = "RESEARCH_MODEL_ONLY"
MISSING_REQUIRED_CONTEXT = "MISSING_REQUIRED_CONTEXT"
UNSUPPORTED_MODEL_FAMILY = "UNSUPPORTED_MODEL_FAMILY"
PARSER_UNRESOLVED = "PARSER_UNRESOLVED"
GAME_MAPPING_UNRESOLVED = "GAME_MAPPING_UNRESOLVED"
AMBIGUOUS_TICKER_MATCH = "AMBIGUOUS_TICKER_MATCH"
STARTED_GAME_EXCLUDED = "STARTED_GAME_EXCLUDED"
NOT_APPLICABLE = "NOT_APPLICABLE"
NOT_EVALUATED_BUG = "NOT_EVALUATED_BUG"

ALL_TERMINAL_STATES = (
    FULLY_EVALUATED, RESEARCH_MODEL_ONLY, MISSING_REQUIRED_CONTEXT, UNSUPPORTED_MODEL_FAMILY,
    PARSER_UNRESOLVED, GAME_MAPPING_UNRESOLVED, AMBIGUOUS_TICKER_MATCH, STARTED_GAME_EXCLUDED,
    NOT_APPLICABLE, NOT_EVALUATED_BUG,
)

# Terminal states excluded from the pregame-scoped view (item 6): a
# different-date contract is out of scope for this date entirely, and a
# started game is intentionally excluded from "what could a manual
# analyst still act on before first pitch" -- both remain fully counted
# in coverage_accounting()/raw_archive_accounting() above, just not
# double-reported as part of the pregame breakdown.
_PREGAME_EXCLUDED_STATES = frozenset({NOT_APPLICABLE, STARTED_GAME_EXCLUDED})

_UNRESOLVED_CLASSIFICATION_STATUSES = frozenset({"parse_error", "unclassified"})

# The exact real, confirmed Kalshi hitter-prop families the hitter
# research engine (lib.research.hitter_board_builder) can price today --
# reused directly from that module rather than re-listed here, so this
# module never drifts out of sync with which families that engine
# actually covers. hitter_stolen_bases is a confirmed real series but
# explicitly out of that engine's scope (no stolen-base projection) --
# it stays UNSUPPORTED_MODEL_FAMILY here too, honestly, not guessed.
HITTER_RESEARCH_FAMILIES = frozenset(MARKET_FAMILY_TO_DISTRIBUTION_KEY)

# Hitter research evidence projectionStatus values -> this module's
# terminal states, when production has no adapter for the family at all.
# Mirrors lib.research.hitter_board_builder's own STATUS_* vocabulary
# directly (never re-invents a parallel one) -- see that module's
# docstring for what each status means operationally. GAME_STARTED is
# deliberately NOT mapped here -- whether the game has started is decided
# exclusively from THIS run's own gameStatus (see classify_terminal_state),
# never from a research snapshot's own (possibly stale) observation.
_RESEARCH_STATUS_TO_MISSING_CONTEXT = frozenset({
    "LINEUP_UNCONFIRMED", "PLAYER_NOT_IN_STARTING_LINEUP", "MISSING_REQUIRED_CONTEXT", "MODEL_ERROR",
})
_RESEARCH_STATUS_TO_AMBIGUOUS = frozenset({"PLAYER_ID_UNRESOLVED", "AMBIGUOUS_TICKER_MATCH"})


def _parse_iso(ts):
    """UTC ISO-8601 'Z' timestamp -> datetime, or None if unparseable/absent."""
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _minutes_between(earlier_iso, later_iso):
    """later - earlier, in minutes (positive when later is truly later).
    None if either timestamp is missing/unparseable -- never a fabricated age."""
    earlier, later = _parse_iso(earlier_iso), _parse_iso(later_iso)
    if earlier is None or later is None:
        return None
    return round((later - earlier).total_seconds() / 60.0, 1)


def extract_raw_ticker_index(search_doc):
    """
    Independent re-derivation of the unique raw Kalshi ticker universe
    directly from `search_doc` -- mirrors (but never imports or calls)
    scripts.discover_kalshi_mlb_markets.extract_raw_markets's own
    dedup-by-ticker semantics (markets list first, then
    discoveredUnknownSeriesMarkets only adding tickers not already seen),
    so this function's result is never definitionally tied to whatever
    discover() itself happened to do with the same input -- it is the
    audit's own independent ground truth, computed from the raw archive
    alone.

    Returns (unique_by_ticker: {ticker: raw_market}, duplicate_count,
    entries_without_ticker, total_raw_entries_seen). A raw entry with no
    ticker field at all cannot be identified/tracked by ticker-based
    accounting and is counted separately in `entries_without_ticker`
    (never silently folded into either the unique or duplicate count).
    A ticker seen more than once across BOTH lists combined (including
    two entries for the same ticker within `markets` itself) increments
    `duplicate_count` once per repeat occurrence, not the denominator.
    """
    seen = {}
    duplicate_count = 0
    entries_without_ticker = 0
    total_raw_entries_seen = 0
    for source_list in (search_doc.get("markets") or [], search_doc.get("discoveredUnknownSeriesMarkets") or []):
        for m in source_list:
            total_raw_entries_seen += 1
            ticker = m.get("market_ticker") or m.get("ticker")
            if not ticker:
                entries_without_ticker += 1
                continue
            if ticker in seen:
                duplicate_count += 1
            else:
                seen[ticker] = m
    return seen, duplicate_count, entries_without_ticker, total_raw_entries_seen


def raw_archive_accounting(search_doc, ledger_rows):
    """
    THE strong coverage invariant (item 1 of this audit): rawArchivedUnique
    (derived independently via extract_raw_ticker_index, NEVER from
    len(ledger_rows) or any discover()-produced count) must equal
    accountedTickerCount, i.e. trueSilentRemainderCount must be zero.
    Unlike coverage_accounting() below (which only proves every contract
    discover() DID return has an explained terminal state),
    trueSilentRemainderCount catches a raw market that disappeared
    ANYWHERE inside discover() -- extraction, dedup, parser routing, date
    filtering, or classification -- before it was ever turned into a
    contract at all.
    """
    raw_index, duplicate_count, entries_without_ticker, total_raw_entries_seen = extract_raw_ticker_index(search_doc)
    raw_ticker_set = set(raw_index.keys())
    ledger_ticker_set = {row.get("ticker") for row in ledger_rows if row.get("ticker")}
    missing = sorted(raw_ticker_set - ledger_ticker_set)
    return {
        "totalRawEntriesSeen": total_raw_entries_seen,
        "entriesWithoutTicker": entries_without_ticker,
        "duplicateRawTickerCount": duplicate_count,
        "rawArchivedUnique": len(raw_ticker_set),
        "accountedTickerCount": len(raw_ticker_set) - len(missing),
        "trueSilentRemainderCount": len(missing),
        "missingTickers": missing,
    }


def load_hitter_prospective_snapshots(date_str, path=None):
    """
    Best-effort read of the PRIMARY hitter research source: the
    append-only checkpoint store
    data/edgelab/hitter_projection_snapshots/<date>.jsonl, written by
    lib.research.hitter_prospective_snapshot's scheduler (see this
    module's own "HITTER RESEARCH PROVENANCE" docstring section). Returns
    a list of row dicts (possibly containing MULTIPLE checkpoints over
    time for the same ticker), or [] if the file doesn't exist yet or
    can't be read -- never raises, never recomputes a projection. A
    malformed individual line is skipped (not fatal to the rest of the
    file) so one corrupt row can never hide every other ticker's history.
    """
    file_path = path or os.path.join(HITTER_SNAPSHOTS_DIR, f"{date_str}.jsonl")
    rows = []
    try:
        with open(file_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except (FileNotFoundError, OSError):
        return []
    return rows


def index_hitter_snapshots_by_ticker(snapshot_rows):
    """{marketTicker: [rows, ascending by snapshotGeneratedAt]} -- a
    ticker may have several checkpoint rows across the day; callers pick
    the one appropriate for a specific market observation via
    select_prospective_hitter_snapshot(), never just "the last one added
    to this list" (checkpoint capture order is not guaranteed to match
    chronological order under a catch-up run)."""
    index = {}
    for row in snapshot_rows or []:
        ticker = row.get("marketTicker")
        if not ticker:
            continue
        index.setdefault(ticker, []).append(row)
    for ticker in index:
        index[ticker].sort(key=lambda r: r.get("snapshotGeneratedAt") or "")
    return index


def select_prospective_hitter_snapshot(ticker, snapshots_by_ticker, market_observed_at):
    """
    Pure. THE no-future-leakage selection rule (hitter research
    provenance mission): among every checkpoint snapshot ever captured
    for `ticker`, returns the one with the LATEST snapshotGeneratedAt
    that is still <= `market_observed_at` -- i.e. the most recent
    projection that genuinely existed at or before the market observation
    this coverage run is analyzing. A snapshot generated AFTER
    market_observed_at is never selected, no matter how much better it
    would make the count look.

    Returns (snapshot_dict_or_None, status):
      "SELECTED"                                     -- a qualifying snapshot found
      "NO_SNAPSHOTS_FOR_TICKER"                       -- this ticker has no checkpoint history at all
      "NO_SNAPSHOT_AT_OR_BEFORE_MARKET_OBSERVATION"   -- snapshots exist, but either
                                                          market_observed_at is unknown (so no
                                                          selection could be PROVEN safe -- never
                                                          guessed) or every one is strictly after it.
    """
    candidates = snapshots_by_ticker.get(ticker) or []
    if not candidates:
        return None, "NO_SNAPSHOTS_FOR_TICKER"
    if not market_observed_at:
        return None, "NO_SNAPSHOT_AT_OR_BEFORE_MARKET_OBSERVATION"
    eligible = [
        c for c in candidates
        if c.get("snapshotGeneratedAt") and c["snapshotGeneratedAt"] <= market_observed_at
    ]
    if not eligible:
        return None, "NO_SNAPSHOT_AT_OR_BEFORE_MARKET_OBSERVATION"
    return max(eligible, key=lambda c: c["snapshotGeneratedAt"]), "SELECTED"


def load_hitter_projection_board(date_str, path=None):
    """
    Best-effort read of the FALLBACK-ONLY hitter research source: the
    on-demand/standalone canonical board artifact
    (data/pipeline/<date>/hitter_projection_board.json). NOT written by
    the scheduler (see this module's own "HITTER RESEARCH PROVENANCE"
    docstring section) -- only by a separate, manually/standalone-
    triggered scripts/build_hitter_projection_board.py run. Returns that
    artifact's "data" payload (the dict with "rows"/"hitterSummaries"/
    "summary"), or None if the artifact doesn't exist or can't be read.
    Never raises, never recomputes a projection.
    """
    try:
        if path:
            with open(path) as f:
                envelope = json.load(f)
        else:
            envelope = read_stage_artifact("hitter_projection_board", date_str)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    return envelope.get("data")


def index_hitter_board_by_ticker(board_data):
    """{marketTicker: row} for every row on an already-loaded hitter
    projection board payload (see load_hitter_projection_board). Returns
    {} for None/empty input -- callers treat an empty index as "no board
    data available" rather than special-casing None everywhere."""
    if not board_data:
        return {}
    return {
        row["marketTicker"]: row
        for row in (board_data.get("rows") or [])
        if row.get("marketTicker")
    }


def _board_fallback_eligible(board_row, market_observed_at):
    """
    A fallback board row may only be used when its OWN provenance
    timestamp (projectionGeneratedAt) is verifiably at or before
    `market_observed_at` -- exactly the same no-future-leakage rule
    applied to the primary prospective-snapshot source, so falling back
    to the legacy board can never silently smuggle in a later-dated
    projection either. Requires market_observed_at to be known (never
    guesses eligibility when it isn't).
    """
    if not board_row or not market_observed_at:
        return False
    generated_at = board_row.get("projectionGeneratedAt")
    return bool(generated_at) and generated_at <= market_observed_at


def link_hitter_research(contract, snapshots_by_ticker, board_index=None):
    """
    Joins one discover()-produced contract to its hitter research
    evidence, PREFERRING the primary prospective-snapshot-store selection
    (select_prospective_hitter_snapshot) and falling back to the legacy
    on-demand board ONLY when no qualifying snapshot exists AND the
    board's own row for this ticker is itself provenance-eligible (see
    _board_fallback_eligible). Reuses that evidence's OWN
    modelProbability/monteCarloStderr/researchRunId/checkpoint/
    sourceCapturePath fields verbatim -- computes NOTHING about the
    hitter model itself.

    CRITICAL (hitter research provenance mission): every `current*`
    economics field below is computed from THIS CONTRACT'S OWN current
    market observation (`contract["currentMarketObservedAt"]`,
    `contract["yesAsk"]`/`["noAsk"]`) -- NEVER from the price recorded at
    projection time. The projection-time price is retained separately,
    under `projectionTime*` fields, for CLV/research provenance only, and
    is never an input to any current*-prefixed field.

    Returns {"researchModelSupportStatus": None} for a non-hitter-research
    family (no research engine exists for it today). For a hitter-research
    family with no usable evidence, researchModelSupportStatus carries the
    exact reason (see select_prospective_hitter_snapshot's status values)
    -- never a guessed match, never a probability from a family with none.
    """
    empty = {
        "researchModelSupportStatus": None, "hitterProjectionStatusReason": None,
        "hitterProjectionSourceType": None, "hitterModelProbability": None,
        "hitterMonteCarloStderr": None, "hitterResearchRunId": None,
        "hitterProjectionCheckpoint": None, "hitterProjectionSnapshotGeneratedAt": None,
        "hitterProjectionSourceCapturePath": None, "hitterProjectionAgeMinutes": None,
        "currentMarketObservedAt": None, "currentExecutableKalshiPrice": None,
        "currentYesPrice": None, "currentNoPrice": None, "currentRawProbabilityEdge": None,
        "currentFeeAwareNetExpectedValuePerDollar": None, "currentFeeAdjustedBreakEvenProbability": None,
        "currentFeeAwareBetUpToPrice": None, "projectionTimeExecutablePrice": None,
        "projectionTimeMarketObservedAt": None,
    }

    family = contract.get("marketFamily")
    if family not in HITTER_RESEARCH_FAMILIES:
        return dict(empty)

    ticker = contract.get("ticker")
    market_observed_at = contract.get("currentMarketObservedAt")

    snapshot, snap_status = (
        select_prospective_hitter_snapshot(ticker, snapshots_by_ticker, market_observed_at)
        if ticker else (None, "NO_SNAPSHOTS_FOR_TICKER")
    )

    if snapshot is not None:
        evidence, source_type = snapshot, "PROSPECTIVE_SNAPSHOT"
    else:
        board_row = (board_index or {}).get(ticker) if (ticker and board_index) else None
        if _board_fallback_eligible(board_row, market_observed_at):
            evidence, source_type = board_row, "LEGACY_BOARD_FALLBACK"
        else:
            result = dict(empty)
            result["researchModelSupportStatus"] = snap_status
            result["currentMarketObservedAt"] = market_observed_at
            return result

    model_prob = evidence.get("modelProbability")
    # Kalshi prices this contract carries (contract["yesAsk"]/["noAsk"])
    # are normalized to a 0-100 PERCENTAGE scale by
    # lib.kalshi_mlb_contract_parser.parse_contract (see its own
    # _price_to_pct) -- the SAME convention
    # scripts/discover_kalshi_mlb_markets.compute_edge_fields already
    # divides by 100 before using as a cost/probability. Converted here
    # to a 0-1 DOLLAR scale so it is directly comparable to
    # modelProbability, to projectionTime* (which IS 0-1 dollars, straight
    # from the hitter engine's own executableKalshiPrice convention), and
    # to what lib.edgelab.kalshi_fees's utilities require (0 < price < 1).
    current_yes_price = contract.get("yesAsk") / 100.0 if contract.get("yesAsk") is not None else None
    current_no_price = contract.get("noAsk") / 100.0 if contract.get("noAsk") is not None else None

    current_edge = None
    if model_prob is not None and current_yes_price is not None:
        current_edge = round(model_prob - current_yes_price, 4)

    fee_aware_net_ev = None
    if model_prob is not None and current_yes_price is not None:
        fee_aware_net_ev = net_expected_value_per_dollar(model_prob, current_yes_price)

    break_even = None
    if current_yes_price is not None and 0 < current_yes_price < 1:
        break_even = fee_adjusted_break_even_probability(current_yes_price)
    bet_up_to = fee_adjusted_bet_up_to_price(model_prob) if model_prob is not None else None

    projection_timestamp = evidence.get("snapshotGeneratedAt") or evidence.get("projectionGeneratedAt")

    return {
        "researchModelSupportStatus": evidence.get("projectionStatus"),
        "hitterProjectionStatusReason": evidence.get("projectionStatusReason"),
        "hitterProjectionSourceType": source_type,
        "hitterModelProbability": model_prob,
        "hitterMonteCarloStderr": evidence.get("monteCarloStderr"),
        "hitterResearchRunId": evidence.get("researchRunId"),
        "hitterProjectionCheckpoint": evidence.get("checkpoint"),
        "hitterProjectionSnapshotGeneratedAt": projection_timestamp,
        "hitterProjectionSourceCapturePath": evidence.get("sourceCapturePath"),
        "hitterProjectionAgeMinutes": _minutes_between(projection_timestamp, market_observed_at),
        # Current observation -- what decides whether this market is
        # attractive RIGHT NOW, always sourced from this contract's own
        # current fields, never from the projection's own historical price.
        "currentMarketObservedAt": market_observed_at,
        "currentExecutableKalshiPrice": current_yes_price,
        "currentYesPrice": current_yes_price,
        "currentNoPrice": current_no_price,
        "currentRawProbabilityEdge": current_edge,
        "currentFeeAwareNetExpectedValuePerDollar": fee_aware_net_ev,
        "currentFeeAdjustedBreakEvenProbability": break_even,
        "currentFeeAwareBetUpToPrice": bet_up_to,
        # Provenance-only -- retained for CLV/research history, NEVER an
        # input to any current*-prefixed field above.
        "projectionTimeExecutablePrice": evidence.get("executableKalshiPrice"),
        "projectionTimeMarketObservedAt": evidence.get("marketObservedAt"),
    }


def classify_terminal_state(contract, research=None):
    """
    Pure function: one discover()-produced contract dict (+ optional
    link_hitter_research() result for it) -> exactly one terminal state
    string from ALL_TERMINAL_STATES. Never raises, never returns None --
    an unrecognized shape falls into NOT_EVALUATED_BUG rather than being
    skipped, so the accounting invariants above can never be satisfied by
    silently excluding a contract this function doesn't understand.
    """
    research = research or {}
    classification_status = contract.get("classificationStatus")

    if classification_status == "different_slate_date":
        return NOT_APPLICABLE

    if classification_status in _UNRESOLVED_CLASSIFICATION_STATUSES:
        return PARSER_UNRESOLVED

    # This run's OWN game-mapping/status signal is checked FIRST and is
    # authoritative for both GAME_MAPPING_UNRESOLVED and
    # STARTED_GAME_EXCLUDED -- NEVER inferred from a research snapshot's
    # own (possibly much older) game context, which would conflate that
    # snapshot's observation with THIS run's current one (hitter research
    # provenance mission).
    if not contract.get("gameMatched"):
        return GAME_MAPPING_UNRESOLVED

    game_status = contract.get("gameStatus")
    if game_status is not None and game_status not in ACTIVE_PREGAME_STATUSES:
        return STARTED_GAME_EXCLUDED

    model_status = contract.get("modelSupportStatus")
    if model_status == STATUS_SUPPORTED:
        return FULLY_EVALUATED
    if model_status == STATUS_MISSING_DATA:
        return MISSING_REQUIRED_CONTEXT

    if model_status == STATUS_UNSUPPORTED:
        research_status = research.get("researchModelSupportStatus")
        if research_status == "PROJECTED":
            return RESEARCH_MODEL_ONLY
        if research_status in _RESEARCH_STATUS_TO_AMBIGUOUS:
            return AMBIGUOUS_TICKER_MATCH
        if research_status in _RESEARCH_STATUS_TO_MISSING_CONTEXT:
            return MISSING_REQUIRED_CONTEXT
        # research_status is one of: None (non-hitter family),
        # NO_SNAPSHOTS_FOR_TICKER, NO_SNAPSHOT_AT_OR_BEFORE_MARKET_OBSERVATION,
        # GAME_STARTED, or MARKET_SEMANTICS_UNSUPPORTED -- none constitute
        # usable research evidence for THIS observation (GAME_STARTED can
        # only reach here via the legacy board fallback, and is
        # deliberately NOT treated as this run's own game-started signal
        # -- see this module's "HITTER RESEARCH PROVENANCE" docstring).
        return UNSUPPORTED_MODEL_FAMILY

    return NOT_EVALUATED_BUG


def build_coverage_ledger(date_str, search_doc, slate_doc, hitter_snapshot_rows=None, hitter_board_data=None):
    """
    Runs the existing discover() engine (no new parsing/classification/
    pricing logic) and returns (ledger_rows, discovery_summary), where
    each ledger row is the contract dict discover() already built, plus:

      - productionModelSupportStatus: alias of modelSupportStatus (item
        2's explicit naming) -- lib.kalshi_probability_adapters' verdict.
      - researchModelSupportStatus + hitter*/current*/projectionTime*
        fields: link_hitter_research() output for hitter-research
        families (None for every other family -- no research engine
        exists for them today).
      - finalCoverageState: classify_terminal_state() output.
      - realMoneyEligibilityStatus overridden to "RESEARCH_ONLY" for
        every hitter-research-family row, REGARDLESS of that specific
        ticker's research linkage outcome -- these families are policy-
        blocked from marketLedger/risk_gate/write_pending_bets as a
        whole (never promoted to production real-money eligibility by
        this module), not merely when a projection happens to exist.
        This overrides only the LOCAL row copy: discover()'s own
        contracts (and its own tests) are never mutated.

    Every row in `ledger_rows` corresponds 1:1 with a contract discover()
    returned -- nothing added, nothing removed here. `hitter_snapshot_rows`
    is the list from load_hitter_prospective_snapshots() (the PRIMARY
    research source -- pass None/[] to run without it, e.g. when no
    checkpoint store exists yet for this date). `hitter_board_data` is the
    FALLBACK-only "data" payload from load_hitter_projection_board(),
    used only when no qualifying prospective snapshot exists for a given
    ticker AND the board row's own provenance is not later than that
    contract's current market observation (see link_hitter_research).
    """
    contracts, summary = discover(date_str, search_doc, slate_doc)
    snapshots_by_ticker = index_hitter_snapshots_by_ticker(hitter_snapshot_rows)
    board_index = index_hitter_board_by_ticker(hitter_board_data)

    ledger_rows = []
    for contract in contracts:
        row = dict(contract)
        research = link_hitter_research(contract, snapshots_by_ticker, board_index)
        row["productionModelSupportStatus"] = contract.get("modelSupportStatus")
        row.update(research)
        row["finalCoverageState"] = classify_terminal_state(contract, research=research)
        if contract.get("marketFamily") in HITTER_RESEARCH_FAMILIES:
            row["realMoneyEligibilityStatus"] = "RESEARCH_ONLY"
        ledger_rows.append(row)
    return ledger_rows, summary


def coverage_accounting(ledger_rows):
    """
    Pure aggregation over a coverage ledger (as produced by
    build_coverage_ledger): counts by terminal state and by market
    family. This is the WEAKER of the two invariants this module
    provides -- see raw_archive_accounting() above for the independent
    check that does not derive its denominator from discover()'s own
    output. unaccountedCount here is computed independently
    (len(ledger_rows) minus the sum of counted buckets) rather than
    assumed to be zero, so it stays an actual assertion a future
    classify_terminal_state edit could break, not a tautology -- but a
    contract dropped BEFORE discover() returns it is invisible to this
    function by construction; use raw_archive_accounting() for that.
    """
    archived_total = len(ledger_rows)
    by_state = {state: 0 for state in ALL_TERMINAL_STATES}
    by_family_state = {}

    for row in ledger_rows:
        state = row.get("finalCoverageState")
        if state not in by_state:
            # Defensive: a row whose state isn't even one of the known
            # terminal states (should be impossible given
            # classify_terminal_state's closed return set) is still
            # counted, never dropped from the total.
            by_state[state] = by_state.get(state, 0) + 1
        else:
            by_state[state] += 1

        family = row.get("marketFamily") or row.get("seriesTicker") or "UNKNOWN"
        by_family_state.setdefault(family, {s: 0 for s in ALL_TERMINAL_STATES})
        by_family_state[family][state] = by_family_state[family].get(state, 0) + 1

    accounted_total = sum(by_state.values())
    unaccounted_count = archived_total - accounted_total

    return {
        "archivedTotal": archived_total,
        "accountedTotal": accounted_total,
        "unaccountedCount": unaccounted_count,
        "byState": by_state,
        "byFamilyState": by_family_state,
    }


def pregame_view(ledger_rows, raw_accounting=None):
    """
    Pregame-scoped breakdown (item 6): the number a manual analyst
    actually cares about before first pitch. Never replaces
    coverage_accounting()'s all-market view -- this is a strict,
    non-mutating filter/re-tally of the SAME already-fully-accounted
    ledger, excluding only NOT_APPLICABLE (a different date's contract,
    out of scope for this date entirely) and STARTED_GAME_EXCLUDED
    (intentionally excluded, not missed) rows from the denominator.
    Every remaining terminal state bucket is reported explicitly, and
    they sum exactly to validPregameMarkets by construction (the 8
    non-excluded states in ALL_TERMINAL_STATES partition the pregame
    rows completely -- verified in tests/test_kalshi_market_coverage.py).
    """
    valid_rows = [r for r in ledger_rows if r.get("finalCoverageState") != NOT_APPLICABLE]
    started = sum(1 for r in valid_rows if r.get("finalCoverageState") == STARTED_GAME_EXCLUDED)
    pregame_rows = [r for r in valid_rows if r.get("finalCoverageState") not in _PREGAME_EXCLUDED_STATES]

    def count(state):
        return sum(1 for r in pregame_rows if r.get("finalCoverageState") == state)

    return {
        "totalValidArchivedMlbMarkets": len(valid_rows),
        "startedGameExcluded": started,
        "validPregameMarkets": len(pregame_rows),
        "pregameFullyEvaluatedProduction": count(FULLY_EVALUATED),
        "pregameResearchSupportedHitterMarkets": count(RESEARCH_MODEL_ONLY),
        "pregameMissingRequiredContext": count(MISSING_REQUIRED_CONTEXT),
        "pregameUnsupportedByAllModels": count(UNSUPPORTED_MODEL_FAMILY),
        "pregameParserUnresolved": count(PARSER_UNRESOLVED),
        "pregameMappingUnresolved": count(GAME_MAPPING_UNRESOLVED),
        "pregameAmbiguousTickerMatch": count(AMBIGUOUS_TICKER_MATCH),
        "pregameNotEvaluatedBug": count(NOT_EVALUATED_BUG),
        "trueSilentRemainder": (raw_accounting or {}).get("trueSilentRemainderCount", 0),
    }


def full_accounting(date_str, search_doc, slate_doc, hitter_snapshot_rows=None, hitter_board_data=None):
    """
    Convenience wrapper combining every accounting layer this module
    provides for one date's coverage run: the coverage ledger itself,
    the (weaker) discover()-output-based accounting, the (strong)
    raw-archive invariant, and the pregame-scoped view. Used by
    scripts/build_full_market_coverage.py; also handy for one-off
    research/audit scripts.
    """
    ledger_rows, discovery_summary = build_coverage_ledger(
        date_str, search_doc, slate_doc, hitter_snapshot_rows, hitter_board_data,
    )
    coverage = coverage_accounting(ledger_rows)
    raw_accounting = raw_archive_accounting(search_doc, ledger_rows)
    pregame = pregame_view(ledger_rows, raw_accounting)
    return {
        "ledgerRows": ledger_rows,
        "discoverySummary": discovery_summary,
        "coverageAccounting": coverage,
        "rawArchiveAccounting": raw_accounting,
        "pregameView": pregame,
    }
