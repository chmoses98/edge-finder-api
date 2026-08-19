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

TERMINAL STATES
----------------
Every contract discover() returns is classified into exactly one of:

  FULLY_EVALUATED         -- fair probability computed (modelSupportStatus
                              SUPPORTED); this is the analysis-coverage
                              state, independent of whether the contract
                              would ever clear a recommendation threshold.
  MISSING_REQUIRED_CONTEXT -- model exists for this family but a required
                              input (projection, pitcher workload data,
                              price) was unavailable this run
                              (modelSupportStatus MISSING_DATA).
  UNSUPPORTED_MODEL_FAMILY -- no probability distribution exists in this
                              codebase for this family (modelSupportStatus
                              UNSUPPORTED) -- e.g. hitter hits/total-bases/
                              RBIs/stolen-bases/hits+runs+RBIs, pitcher
                              hits/earned-runs-allowed, or an F3/F7 winner
                              market whose outcome structure is still
                              unverified. Preserved, classified, never
                              silently dropped, never assigned an invented
                              probability.
  PARSER_UNRESOLVED       -- parse_contract() raised on this raw market
                              (classificationStatus == "parse_error"), or
                              lib.kalshi_mlb_market_classifier could not
                              recognize the series/ticker structure at all
                              (classificationStatus == "unclassified").
  GAME_MAPPING_UNRESOLVED -- classified fine, but no slate game matched
                              this contract's (date, away, home) -- gameId
                              fell back to parse_contract's own synthetic
                              ticker-derived id, so no real projection
                              context could ever be resolved for it.
  STARTED_GAME_EXCLUDED   -- matched a real slate game, but that game's
                              status is not one of
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

from lib.postponed_guard import ACTIVE_PREGAME_STATUSES
from scripts.discover_kalshi_mlb_markets import (
    discover,
    STATUS_SUPPORTED,
    STATUS_UNSUPPORTED,
    STATUS_MISSING_DATA,
)

FULLY_EVALUATED = "FULLY_EVALUATED"
MISSING_REQUIRED_CONTEXT = "MISSING_REQUIRED_CONTEXT"
UNSUPPORTED_MODEL_FAMILY = "UNSUPPORTED_MODEL_FAMILY"
PARSER_UNRESOLVED = "PARSER_UNRESOLVED"
GAME_MAPPING_UNRESOLVED = "GAME_MAPPING_UNRESOLVED"
STARTED_GAME_EXCLUDED = "STARTED_GAME_EXCLUDED"
NOT_APPLICABLE = "NOT_APPLICABLE"
NOT_EVALUATED_BUG = "NOT_EVALUATED_BUG"

ALL_TERMINAL_STATES = (
    FULLY_EVALUATED, MISSING_REQUIRED_CONTEXT, UNSUPPORTED_MODEL_FAMILY,
    PARSER_UNRESOLVED, GAME_MAPPING_UNRESOLVED, STARTED_GAME_EXCLUDED,
    NOT_APPLICABLE, NOT_EVALUATED_BUG,
)

_UNRESOLVED_CLASSIFICATION_STATUSES = frozenset({"parse_error", "unclassified"})


def classify_terminal_state(contract):
    """
    Pure function: one discover()-produced contract dict -> exactly one
    terminal state string from ALL_TERMINAL_STATES. Never raises, never
    returns None -- an unrecognized shape falls into NOT_EVALUATED_BUG
    rather than being skipped, so the accounting invariant in
    coverage_accounting() below can never be satisfied by silently
    excluding a contract this function doesn't understand.
    """
    classification_status = contract.get("classificationStatus")

    if classification_status == "different_slate_date":
        return NOT_APPLICABLE

    if classification_status in _UNRESOLVED_CLASSIFICATION_STATUSES:
        return PARSER_UNRESOLVED

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
        return UNSUPPORTED_MODEL_FAMILY

    return NOT_EVALUATED_BUG


def build_coverage_ledger(date_str, search_doc, slate_doc):
    """
    Runs the existing discover() engine (no new parsing/classification/
    pricing logic) and returns (ledger_rows, discovery_summary), where
    each ledger row is the contract dict discover() already built, plus
    one additional key: "finalCoverageState" (see classify_terminal_state
    above). Every row in `ledger_rows` corresponds 1:1 with a raw Kalshi
    market discover() considered -- nothing added, nothing removed.
    """
    contracts, summary = discover(date_str, search_doc, slate_doc)
    ledger_rows = []
    for contract in contracts:
        row = dict(contract)
        row["finalCoverageState"] = classify_terminal_state(contract)
        ledger_rows.append(row)
    return ledger_rows, summary


def coverage_accounting(ledger_rows):
    """
    Pure aggregation over a coverage ledger (as produced by
    build_coverage_ledger): counts by terminal state, by market family,
    and the "no silent remainder" invariant this audit exists to
    guarantee -- archivedTotal must always equal the sum of every
    terminal-state bucket. Because classify_terminal_state() is total
    (every contract maps to exactly one state, defaulting to
    NOT_EVALUATED_BUG rather than nothing), unaccountedCount is computed
    independently here (archivedTotal minus the sum of counted buckets)
    rather than assumed to be zero -- this keeps the invariant an actual
    assertion a future code change could break, not a tautology.
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
