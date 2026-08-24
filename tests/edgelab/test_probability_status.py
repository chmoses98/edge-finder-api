#!/usr/bin/env python3
"""
tests/edgelab/test_probability_status.py
============================================
Phase 2 (Full-Universe MLB Kalshi Probability Persistence): pure unit
tests for lib/edgelab/probability_status.py -- vocabulary mapping,
protected-expression algebra, and consistency invariants. No I/O.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab import probability_status as ps


# ── missing_reason_for_text ──────────────────────────────────────────

def test_missing_reason_matches_starter_keyword():
    assert ps.missing_reason_for_text("no confirmed starter for this game") == ps.MISSING_REASON_MISSING_STARTER


def test_missing_reason_matches_lineup_keyword():
    assert ps.missing_reason_for_text("lineup not yet posted") == ps.MISSING_REASON_MISSING_LINEUP


def test_missing_reason_matches_pitcher_keyword():
    assert ps.missing_reason_for_text("pitcherSavant data unavailable") == ps.MISSING_REASON_MISSING_PITCHER_PROJECTION


def test_missing_reason_matches_hitter_keyword():
    assert ps.missing_reason_for_text("no hitter projection board row") == ps.MISSING_REASON_MISSING_HITTER_PROJECTION


def test_missing_reason_defaults_to_other_for_unmatched_text():
    assert ps.missing_reason_for_text("some completely novel unclassified reason") == ps.MISSING_REASON_OTHER


def test_missing_reason_defaults_to_other_for_empty_text():
    assert ps.missing_reason_for_text(None) == ps.MISSING_REASON_OTHER
    assert ps.missing_reason_for_text("") == ps.MISSING_REASON_OTHER


def test_missing_reason_always_returns_a_valid_vocabulary_value():
    for text in (None, "", "starter", "lineup", "hand split", "savant", "hitter", "run distribution", "xyz"):
        assert ps.missing_reason_for_text(text) in ps.VALID_MISSING_REASONS


# ── probability_status_for_evaluation ────────────────────────────────

def test_evaluated_status_maps_to_evaluated():
    status, needs_reason = ps.probability_status_for_evaluation("EVALUATED", 61.2, discovery_covered=True)
    assert status == ps.PROBABILITY_STATUS_EVALUATED
    assert needs_reason is False


def test_partial_evaluation_maps_to_evaluated():
    status, needs_reason = ps.probability_status_for_evaluation("PARTIAL_EVALUATION", 61.2, discovery_covered=True)
    assert status == ps.PROBABILITY_STATUS_EVALUATED
    assert needs_reason is False


def test_probability_present_overrides_status_to_evaluated():
    # Even an unrecognized/legacy evaluationStatus is EVALUATED if a real
    # probability is present -- probabilityStatus is about the NUMBER,
    # never about which status string produced it.
    status, needs_reason = ps.probability_status_for_evaluation("SOME_FUTURE_STATUS", 50.0, discovery_covered=True)
    assert status == ps.PROBABILITY_STATUS_EVALUATED
    assert needs_reason is False


def test_parser_unresolved_maps_directly():
    status, needs_reason = ps.probability_status_for_evaluation("PARSER_UNRESOLVED", None, discovery_covered=False)
    assert status == ps.PROBABILITY_STATUS_PARSER_UNRESOLVED
    assert needs_reason is False


def test_no_model_support_maps_to_unsupported_family():
    status, needs_reason = ps.probability_status_for_evaluation("NO_MODEL_SUPPORT", None, discovery_covered=False)
    assert status == ps.PROBABILITY_STATUS_UNSUPPORTED_FAMILY
    assert needs_reason is False


def test_not_evaluated_with_discovery_coverage_maps_to_unsupported_family():
    # Discovery ran THIS run and reported UNSUPPORTED for this exact
    # ticker -- a genuine family gap, not a missing input.
    status, needs_reason = ps.probability_status_for_evaluation("NOT_EVALUATED", None, discovery_covered=True)
    assert status == ps.PROBABILITY_STATUS_UNSUPPORTED_FAMILY
    assert needs_reason is False


def test_not_evaluated_without_discovery_coverage_maps_to_missing_input():
    # No discovery run reached this ticker at all this run.
    status, needs_reason = ps.probability_status_for_evaluation("NOT_EVALUATED", None, discovery_covered=False)
    assert status == ps.PROBABILITY_STATUS_MISSING_INPUT
    assert needs_reason is True


def test_data_quality_block_maps_to_missing_input():
    status, needs_reason = ps.probability_status_for_evaluation("DATA_QUALITY_BLOCK", None, discovery_covered=True)
    assert status == ps.PROBABILITY_STATUS_MISSING_INPUT
    assert needs_reason is True


def test_unrecognized_status_never_silently_omitted():
    # Any status this function doesn't explicitly special-case still
    # gets a real (non-EVALUATED) classification -- never falls through
    # to nothing.
    status, needs_reason = ps.probability_status_for_evaluation("SOME_FUTURE_STATUS", None, discovery_covered=False)
    assert status == ps.PROBABILITY_STATUS_MISSING_INPUT
    assert needs_reason is True


def test_probability_status_always_in_valid_vocabulary():
    cases = [
        ("EVALUATED", 1.0, True), ("PARTIAL_EVALUATION", 1.0, False),
        ("NO_MODEL_SUPPORT", None, False), ("PARSER_UNRESOLVED", None, False),
        ("NOT_EVALUATED", None, True), ("NOT_EVALUATED", None, False),
        ("DATA_QUALITY_BLOCK", None, True), ("INVALID_PROBABILITY", None, False),
    ]
    for evaluation_status, prob, covered in cases:
        status, _ = ps.probability_status_for_evaluation(evaluation_status, prob, discovery_covered=covered)
        assert status in ps.VALID_PROBABILITY_STATUSES


# ── protected_expression_supported / compute_protected_no_probability ──

def test_f5_is_protected_expression_supported():
    assert ps.protected_expression_supported("F5") is True


def test_f3_and_f7_are_protected_expression_supported():
    # Systematic Best-Expression Comparison mission independently
    # confirmed CONFIRMED_THREE_WAY for F3/F7 too -- see
    # lib.research.market_taxonomy.HORIZON_MARKET_STATUS.
    assert ps.protected_expression_supported("F3") is True
    assert ps.protected_expression_supported("F7") is True


def test_full_game_is_not_protected_expression_supported():
    # Full game always continues to extra innings -- no tradable tie leg.
    assert ps.protected_expression_supported("full_game") is False


def test_unknown_period_is_not_supported():
    assert ps.protected_expression_supported("F9_NOT_A_REAL_PERIOD") is False


def test_compute_protected_no_probability_away_favored():
    prob, basis = ps.compute_protected_no_probability("away", away_win_prob=0.55, tie_prob=0.10, home_win_prob=0.35, period="F5")
    assert abs(prob - 0.65) < 1e-9
    assert "away" in basis.lower() and "tie" in basis.lower()


def test_compute_protected_no_probability_home_favored():
    prob, basis = ps.compute_protected_no_probability("home", away_win_prob=0.40, tie_prob=0.15, home_win_prob=0.45, period="F5")
    assert abs(prob - 0.60) < 1e-9


def test_compute_protected_no_probability_matches_complement_of_opposing_leg():
    # By construction, protected-NO for `favored_side` equals 1 - the
    # OPPOSING side's own three-way leg (it is literally the NO side of
    # the opposing side's winner contract: away+tie = 1-home, home+tie = 1-away).
    away, tie, home = 0.52, 0.12, 0.36
    prob, _ = ps.compute_protected_no_probability("away", away, tie, home, period="F5")
    assert abs(prob - (1.0 - home)) < 1e-9
    prob2, _ = ps.compute_protected_no_probability("home", away, tie, home, period="F5")
    assert abs(prob2 - (1.0 - away)) < 1e-9


def test_compute_protected_no_probability_none_for_unsupported_period():
    prob, basis = ps.compute_protected_no_probability("away", 0.5, 0.1, 0.4, period="full_game")
    assert prob is None
    assert basis is None


def test_compute_protected_no_probability_none_for_invalid_side():
    prob, basis = ps.compute_protected_no_probability("home_or_away", 0.5, 0.1, 0.4, period="F5")
    assert prob is None


def test_compute_protected_no_probability_none_for_missing_inputs():
    assert ps.compute_protected_no_probability("away", None, 0.1, 0.4, period="F5") == (None, None)
    assert ps.compute_protected_no_probability("away", 0.5, None, 0.4, period="F5") == (None, None)
    assert ps.compute_protected_no_probability("away", 0.5, 0.1, None, period="F5") == (None, None)


def test_compute_protected_no_probability_period_none_skips_gate():
    # A caller that already knows the period is verified (e.g. has
    # already checked protected_expression_supported itself) can pass
    # period=None to skip the gate -- documented escape hatch, not a
    # silent bypass a normal caller would hit by accident.
    prob, basis = ps.compute_protected_no_probability("away", 0.5, 0.1, 0.4, period=None)
    assert prob is not None


# ── consistency invariants ───────────────────────────────────────────

def test_binary_complementarity_holds_for_exact_complement():
    assert ps.binary_complementarity_holds(0.63, 0.37) is True


def test_binary_complementarity_fails_for_non_complement():
    assert ps.binary_complementarity_holds(0.63, 0.50) is False


def test_binary_complementarity_false_for_missing_probability():
    assert ps.binary_complementarity_holds(None, 0.4) is False
    assert ps.binary_complementarity_holds(0.6, None) is False


def test_binary_complementarity_within_tolerance():
    assert ps.binary_complementarity_holds(0.6000001, 0.3999999) is True


def test_outcomes_sum_to_one_true_for_three_way():
    assert ps.outcomes_sum_to_one([0.45, 0.10, 0.45]) is True


def test_outcomes_sum_to_one_false_when_not_summing():
    assert ps.outcomes_sum_to_one([0.45, 0.10, 0.40]) is False


def test_outcomes_sum_to_one_false_with_missing_value():
    assert ps.outcomes_sum_to_one([0.45, None, 0.45]) is False


def test_outcomes_sum_to_one_works_for_binary_case_too():
    assert ps.outcomes_sum_to_one([0.7, 0.3]) is True
