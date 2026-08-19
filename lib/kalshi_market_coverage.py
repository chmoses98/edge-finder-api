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
                              repository's separate hitter research
                              engine (lib.research.hitter_board_builder /
                              data/pipeline/<date>/hitter_projection_board.json)
                              independently produced a real modelProbability
                              for this exact ticker. Distinct from
                              UNSUPPORTED_MODEL_FAMILY -- "no production
                              adapter" is not the same claim as "no model
                              anywhere in this repository." Never routed
                              into marketLedger/risk_gate/write_pending_bets
                              -- see realMoneyEligibilityStatus="RESEARCH_ONLY"
                              on these rows.
  MISSING_REQUIRED_CONTEXT -- a model (production OR research) exists for
                              this family but a required input was
                              unavailable this run (modelSupportStatus
                              MISSING_DATA; or the hitter research board
                              reports LINEUP_UNCONFIRMED/
                              PLAYER_NOT_IN_STARTING_LINEUP/
                              MISSING_REQUIRED_CONTEXT/MODEL_ERROR for
                              this exact ticker).
  UNSUPPORTED_MODEL_FAMILY -- no usable model or projection exists
                              ANYWHERE in this repository's analysis
                              stack for this contract -- neither
                              lib.kalshi_probability_adapters nor the
                              hitter research engine. Preserved,
                              classified, never silently dropped, never
                              assigned an invented probability.
  PARSER_UNRESOLVED       -- parse_contract() raised on this raw market
                              (classificationStatus == "parse_error"), or
                              lib.kalshi_mlb_market_classifier could not
                              recognize the series/ticker structure at all
                              (classificationStatus == "unclassified").
  GAME_MAPPING_UNRESOLVED -- classified fine, but no slate game matched
                              this contract's (date, away, home) -- gameId
                              fell back to parse_contract's own synthetic
                              ticker-derived id, so no real projection
                              context could ever be resolved for it. (A
                              hitter-family contract with an independent
                              RESEARCH_MODEL_ONLY/MISSING_REQUIRED_CONTEXT/
                              AMBIGUOUS_TICKER_MATCH result from the
                              hitter research board -- which resolves its
                              own game context -- is classified from that
                              result FIRST, even when THIS run's own
                              slate/game context is unavailable.)
  AMBIGUOUS_TICKER_MATCH   -- this contract's player/subject identity
                              could not be resolved to exactly one
                              candidate without guessing (the hitter
                              research board's own PLAYER_ID_UNRESOLVED/
                              AMBIGUOUS_TICKER_MATCH outcomes).
  STARTED_GAME_EXCLUDED   -- matched a real slate game (or the hitter
                              research board independently confirmed the
                              game had started), but that game's status
                              is not one of
                              lib.postponed_guard.ACTIVE_PREGAME_STATUSES
                              at observation time -- this contract is
                              intentionally excluded from pregame coverage
                              accounting, not missed. Never conflated with
                              an accidental gap for a not-started game.
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
"""
import json

from lib.pipeline_artifacts import read_stage_artifact
from lib.postponed_guard import ACTIVE_PREGAME_STATUSES
from lib.research.hitter_board_builder import MARKET_FAMILY_TO_DISTRIBUTION_KEY
from lib.edgelab.kalshi_fees import net_expected_value_per_dollar
from scripts.discover_kalshi_mlb_markets import (
    discover,
    STATUS_SUPPORTED,
    STATUS_UNSUPPORTED,
    STATUS_MISSING_DATA,
)

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

# Hitter research board projectionStatus values -> this module's terminal
# states, when production has no adapter for the family at all. Mirrors
# lib.research.hitter_board_builder's own STATUS_* vocabulary directly
# (never re-invents a parallel one) -- see that module's docstring for
# what each status means operationally.
_RESEARCH_STATUS_TO_MISSING_CONTEXT = frozenset({
    "LINEUP_UNCONFIRMED", "PLAYER_NOT_IN_STARTING_LINEUP", "MISSING_REQUIRED_CONTEXT", "MODEL_ERROR",
})
_RESEARCH_STATUS_TO_AMBIGUOUS = frozenset({"PLAYER_ID_UNRESOLVED", "AMBIGUOUS_TICKER_MATCH"})


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


def load_hitter_projection_board(date_str, path=None):
    """
    Best-effort read of the EXISTING hitter projection board pipeline
    artifact (data/pipeline/<date>/hitter_projection_board.json, written
    by scripts/build_hitter_projection_board.py on its own independent
    ~15-minute schedule -- see .github/workflows/hitter-snapshot-scheduler.yml).
    Returns that artifact's "data" payload (the dict with "rows"/
    "hitterSummaries"/"summary"), or None if the artifact doesn't exist
    yet or can't be read. Never raises, never recomputes a projection --
    this function only reads what that engine already produced.
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


def link_hitter_research(contract, hitter_index):
    """
    Joins one discover()-produced contract to its row on the hitter
    research board (by marketTicker -- the same stable identity across
    snapshots; the board may have been built from a DIFFERENT archived
    Kalshi observation than this coverage run's own search_doc, which is
    fine -- ticker identity, not observation timestamp, is what a hitter
    contract's projection is keyed on). Reuses that engine's OWN
    modelProbability/executableKalshiPrice/rawProbabilityEdge/
    expectedValuePerDollar/monteCarloStderr/researchRunId/
    projectionGeneratedAt/sourceCapturePath fields verbatim -- computes
    NOTHING about the hitter model itself, only the fee-aware net EV
    (lib.edgelab.kalshi_fees.net_expected_value_per_dollar, the existing
    canonical pure fee utility -- never touches staking/bankroll) as one
    additional derived field from the board's own already-published
    probability and price.

    Returns {"researchModelSupportStatus": None} for a non-hitter-research
    family (no research engine exists for it today). For a hitter-research
    family with no matching board row, researchModelSupportStatus is
    "NO_RESEARCH_BOARD_AVAILABLE" (hitter_index itself is empty -- no
    board was loadable for this date at all) or
    "NOT_LINKED_NO_BOARD_DATA" (a board exists but has no row for this
    exact ticker -- e.g. built from a different snapshot that didn't
    carry this contract). Never guesses a player/threshold match outside
    what the board itself already resolved.
    """
    family = contract.get("marketFamily")
    if family not in HITTER_RESEARCH_FAMILIES:
        return {"researchModelSupportStatus": None}

    ticker = contract.get("ticker")
    row = hitter_index.get(ticker) if ticker else None
    if row is None:
        status = "NOT_LINKED_NO_BOARD_DATA" if hitter_index else "NO_RESEARCH_BOARD_AVAILABLE"
        return {"researchModelSupportStatus": status}

    fee_aware_net_ev = None
    if row.get("modelProbability") is not None and row.get("executableKalshiPrice") is not None:
        fee_aware_net_ev = net_expected_value_per_dollar(row["modelProbability"], row["executableKalshiPrice"])

    return {
        "researchModelSupportStatus": row.get("projectionStatus"),
        "hitterProjectionStatus": row.get("projectionStatus"),
        "hitterProjectionStatusReason": row.get("projectionStatusReason"),
        "hitterModelProbability": row.get("modelProbability"),
        "hitterFairAmericanOdds": row.get("fairAmericanOdds"),
        "hitterExecutableKalshiPrice": row.get("executableKalshiPrice"),
        "hitterRawProbabilityEdge": row.get("rawProbabilityEdge"),
        "hitterExpectedValuePerDollar": row.get("expectedValuePerDollar"),
        "hitterFeeAwareNetExpectedValuePerDollar": fee_aware_net_ev,
        "hitterMonteCarloStderr": row.get("monteCarloStderr"),
        "hitterResearchRunId": row.get("researchRunId"),
        "hitterProjectionGeneratedAt": row.get("projectionGeneratedAt"),
        "hitterSourceCapturePath": row.get("sourceCapturePath"),
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

    model_status = contract.get("modelSupportStatus")

    # Hitter research linkage is checked BEFORE this run's own
    # game-mapping/status signal: the hitter research board resolves its
    # own game/lineup context independently (on its own schedule, often
    # from a different archived snapshot), so a hitter contract can be
    # RESEARCH_MODEL_ONLY / MISSING_REQUIRED_CONTEXT / AMBIGUOUS_TICKER_MATCH
    # / STARTED_GAME_EXCLUDED from THAT board even when this specific
    # coverage run's own slate_doc has no game context at all (e.g. no
    # live slate.json yet for today) -- reusing that existing archived
    # research output rather than re-deriving game context a second time.
    research_status = research.get("researchModelSupportStatus")
    if model_status == STATUS_UNSUPPORTED and research_status:
        if research_status == "PROJECTED":
            return RESEARCH_MODEL_ONLY
        if research_status == "GAME_STARTED":
            return STARTED_GAME_EXCLUDED
        if research_status in _RESEARCH_STATUS_TO_AMBIGUOUS:
            return AMBIGUOUS_TICKER_MATCH
        if research_status in _RESEARCH_STATUS_TO_MISSING_CONTEXT:
            return MISSING_REQUIRED_CONTEXT
        # MARKET_SEMANTICS_UNSUPPORTED / NOT_LINKED_NO_BOARD_DATA /
        # NO_RESEARCH_BOARD_AVAILABLE: fall through to this contract's
        # own game-mapping/production-model status below.

    if not contract.get("gameMatched"):
        return GAME_MAPPING_UNRESOLVED

    game_status = contract.get("gameStatus")
    if game_status is not None and game_status not in ACTIVE_PREGAME_STATUSES:
        return STARTED_GAME_EXCLUDED

    if model_status == STATUS_SUPPORTED:
        return FULLY_EVALUATED
    if model_status == STATUS_MISSING_DATA:
        return MISSING_REQUIRED_CONTEXT
    if model_status == STATUS_UNSUPPORTED:
        return UNSUPPORTED_MODEL_FAMILY

    return NOT_EVALUATED_BUG


def build_coverage_ledger(date_str, search_doc, slate_doc, hitter_board_data=None):
    """
    Runs the existing discover() engine (no new parsing/classification/
    pricing logic) and returns (ledger_rows, discovery_summary), where
    each ledger row is the contract dict discover() already built, plus:

      - productionModelSupportStatus: alias of modelSupportStatus (item
        2's explicit naming) -- lib.kalshi_probability_adapters' verdict.
      - researchModelSupportStatus + hitter* fields: link_hitter_research()
        output for hitter-research families (None for every other family
        -- no research engine exists for them today).
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
    returned -- nothing added, nothing removed here. `hitter_board_data`
    is the "data" payload from load_hitter_projection_board() (or None to
    run without hitter-research linkage, e.g. when no board artifact
    exists yet for this date -- every hitter contract then falls back to
    UNSUPPORTED_MODEL_FAMILY exactly as before this linkage existed).
    """
    contracts, summary = discover(date_str, search_doc, slate_doc)
    hitter_index = index_hitter_board_by_ticker(hitter_board_data)

    ledger_rows = []
    for contract in contracts:
        row = dict(contract)
        research = link_hitter_research(contract, hitter_index)
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


def full_accounting(date_str, search_doc, slate_doc, hitter_board_data=None):
    """
    Convenience wrapper combining every accounting layer this module
    provides for one date's coverage run: the coverage ledger itself,
    the (weaker) discover()-output-based accounting, the (strong)
    raw-archive invariant, and the pregame-scoped view. Used by
    scripts/build_full_market_coverage.py; also handy for one-off
    research/audit scripts.
    """
    ledger_rows, discovery_summary = build_coverage_ledger(date_str, search_doc, slate_doc, hitter_board_data)
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
