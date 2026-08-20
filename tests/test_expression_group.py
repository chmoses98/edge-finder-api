#!/usr/bin/env python3
"""
tests/test_expression_group.py
==================================
Systematic Best-Expression Comparison mission: unit coverage for
lib/research/expression_group.py -- the canonical relation between
every currently-available expression of one side's early-game thesis
(F3/F5/F7 winner, opponent F5 NO, full-game ML, run-line/winning
margin).
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from lib.research.expression_group import (  # noqa: E402
    FAMILY_F3_WIN, FAMILY_F5_PROTECTED_NO, FAMILY_F5_WIN, FAMILY_F7_WIN,
    FAMILY_FULL_GAME_ML, FAMILY_WINNING_MARGIN,
    build_expression_group, price_only_reference, reference_from_ledger_row,
    reference_from_tie_tax_leg,
)


def _f5_row(model_prob=50.0, price_cents=45.0, break_even=47.0, gross=5.0, net=3.0,
            bet_up_gross=48, bet_up_net=46, status="Accepted"):
    return {
        "executablePriceUsed": price_cents,
        "modelProb": model_prob,
        "feeAdjustedBreakEvenProbability": break_even,
        "calibratedEdgeVsExecutable": gross,
        "netExecutableEdge": net,
        "betUpToPriceGross": bet_up_gross,
        "betUpToPriceNet": bet_up_net,
        "status": status,
    }


def _tie_leg(price_cents=60.0, true_prob=0.65, break_even=0.62, payoff="away leads or tie",
             net_ev=0.05, reason=None):
    return {
        "priceCents": price_cents,
        "trueProbability": true_prob,
        "feeAdjustedBreakEvenProbability": break_even,
        "payoffCondition": payoff,
        "netExpectedValuePerDollar": net_ev,
        "reasonCode": reason,
    }


class TestPriceOnlyReferenceNeverInventsProbability:

    def test_f3_reference_has_no_model_probability_or_edge(self):
        ref = price_only_reference(FAMILY_F3_WIN, "away", "g1", "NYY", 30.0, "away leads after three")
        assert ref["modelProbability"] is None
        assert ref["supportsModelProbability"] is False
        assert ref["grossEdge"] is None
        assert ref["netExecutableEdge"] is None
        assert ref["betUpToPriceGross"] is None
        assert ref["betUpToPriceNet"] is None
        # Fee-adjusted breakeven IS computable from price alone -- no model needed.
        assert ref["feeAdjustedBreakEvenProbability"] is not None

    def test_f7_reference_has_no_model_probability_or_edge(self):
        ref = price_only_reference(FAMILY_F7_WIN, "home", "g1", "PHI", 55.0, "home leads after seven")
        assert ref["modelProbability"] is None
        assert ref["supportsModelProbability"] is False
        assert ref["netExecutableEdge"] is None

    def test_run_line_reference_has_no_model_probability_or_edge(self):
        ref = price_only_reference(FAMILY_WINNING_MARGIN, "away", "g1", "NYY", 40.0, "away wins by margin")
        assert ref["modelProbability"] is None
        assert ref["supportsModelProbability"] is False
        assert ref["grossEdge"] is None
        assert ref["netExecutableEdge"] is None

    def test_none_price_yields_none_reference_not_a_fabricated_placeholder(self):
        assert price_only_reference(FAMILY_F3_WIN, "away", "g1", "NYY", None, "x") is None

    def test_fee_adjusted_break_even_is_a_pure_function_of_price_alone(self):
        """Same price -> same breakeven, regardless of side/game/team -- no hidden state."""
        r1 = price_only_reference(FAMILY_F3_WIN, "away", "g1", "NYY", 30.0, "x")
        r2 = price_only_reference(FAMILY_F7_WIN, "home", "g2", "BOS", 30.0, "y")
        assert r1["feeAdjustedBreakEvenProbability"] == r2["feeAdjustedBreakEvenProbability"]


class TestReferenceFromLedgerRowCrossReferencesNeverRecomputes:

    def test_reads_row_fields_verbatim(self):
        row = _f5_row(model_prob=52.5, price_cents=44.0, break_even=46.0, gross=6.5, net=4.1,
                      bet_up_gross=49, bet_up_net=47, status="Accepted")
        ref = reference_from_ledger_row(FAMILY_F5_WIN, "away", "g1", "NYY", row,
                                        payoff_condition="away leads after five")
        assert ref["modelProbability"] == 52.5
        assert ref["priceCents"] == 44.0
        assert ref["feeAdjustedBreakEvenProbability"] == 46.0
        assert ref["grossEdge"] == 6.5
        assert ref["netExecutableEdge"] == 4.1
        assert ref["betUpToPriceGross"] == 49
        assert ref["betUpToPriceNet"] == 47
        assert ref["status"] == "Accepted"
        assert ref["supportsModelProbability"] is True

    def test_none_row_yields_none_reference(self):
        assert reference_from_ledger_row(FAMILY_FULL_GAME_ML, "away", "g1", "NYY", None) is None

    def test_missing_row_model_prob_reported_honestly(self):
        row = _f5_row()
        row["modelProb"] = None
        ref = reference_from_ledger_row(FAMILY_FULL_GAME_ML, "away", "g1", "NYY", row)
        assert ref["supportsModelProbability"] is False
        assert ref["modelProbability"] is None


class TestReferenceFromTieTaxLegRescalesConsistently:

    def test_probability_rescaled_from_0_1_to_0_100(self):
        leg = _tie_leg(true_prob=0.65, break_even=0.62)
        ref = reference_from_tie_tax_leg(FAMILY_F5_PROTECTED_NO, "away", "g1", "NYY", leg, tie_protected=True)
        assert ref["modelProbability"] == 65.0
        assert ref["feeAdjustedBreakEvenProbability"] == 62.0

    def test_none_leg_yields_none_reference(self):
        assert reference_from_tie_tax_leg(FAMILY_F5_WIN, "away", "g1", "NYY", None, tie_protected=False) is None

    def test_gross_and_net_edge_derived_consistently(self):
        """grossEdge = model - price-implied-prob; netExecutableEdge = model - fee breakeven -- mirrors build_edge_fields()'s own rawEdgeVsExecutable/netExecutableEdge relationship."""
        leg = _tie_leg(price_cents=60.0, true_prob=0.65, break_even=0.62)
        ref = reference_from_tie_tax_leg(FAMILY_F5_PROTECTED_NO, "away", "g1", "NYY", leg, tie_protected=True)
        assert ref["grossEdge"] == round((0.65 - 0.60) * 100, 3)
        assert ref["netExecutableEdge"] == round((0.65 - 0.62) * 100, 3)


class TestF5YesAndOpponentNoRemainDistinctOnTie:
    """
    Core acceptance criterion: F5 YES and opponent F5 NO must remain
    distinct entries -- different true probability, different payoff
    condition -- specifically because a tie settles them differently
    (YES loses on a tie; NO wins on a tie).
    """

    def test_protected_no_probability_exceeds_yes_by_exactly_the_tie_probability(self):
        p_tie = 0.15
        p_lead = 0.45
        f5_row = _f5_row(model_prob=p_lead * 100)
        yes_leg_via_row = f5_row  # F5 YES sourced from the row per build_expression_group()
        no_leg = _tie_leg(true_prob=p_lead + p_tie, break_even=0.50,
                          payoff="away leads after five OR tie (loses only if opponent leads)")

        group = build_expression_group(
            "away", "g1", "NYY",
            f5_row=yes_leg_via_row, f5_protected_no_leg=no_leg,
            full_game_ml_row=None,
        )
        by_family = {e["family"]: e for e in group}
        yes_entry = by_family[FAMILY_F5_WIN]
        no_entry = by_family[FAMILY_F5_PROTECTED_NO]

        assert yes_entry["modelProbability"] == pytest.approx(p_lead * 100)
        assert no_entry["modelProbability"] == pytest.approx((p_lead + p_tie) * 100)
        assert no_entry["modelProbability"] > yes_entry["modelProbability"]
        assert "loses on a tie" in yes_entry["payoffCondition"]
        assert "tie" in no_entry["payoffCondition"].lower()
        assert yes_entry["tieProtected"] is False
        assert no_entry["tieProtected"] is True

    def test_yes_and_no_are_never_merged_into_one_entry(self):
        f5_row = _f5_row()
        no_leg = _tie_leg()
        group = build_expression_group(
            "away", "g1", "NYY", f5_row=f5_row, f5_protected_no_leg=no_leg, full_game_ml_row=None,
        )
        families = [e["family"] for e in group]
        assert families.count(FAMILY_F5_WIN) == 1
        assert families.count(FAMILY_F5_PROTECTED_NO) == 1


class TestBuildExpressionGroupIsDeterministic:

    def test_same_inputs_produce_identical_output(self):
        f5_row = _f5_row()
        no_leg = _tie_leg()
        ml_row = _f5_row(model_prob=55.0, price_cents=52.0)
        kwargs = dict(
            f5_row=f5_row, f5_protected_no_leg=no_leg, full_game_ml_row=ml_row,
            f3_price_cents=30.0, f3_ticker="t3", f7_price_cents=55.0, f7_ticker="t7",
            run_line_price_cents=40.0, run_line_ticker="trl",
        )
        g1 = build_expression_group("away", "g1", "NYY", **kwargs)
        g2 = build_expression_group("away", "g1", "NYY", **kwargs)
        assert g1 == g2

    def test_only_present_data_produces_entries_never_a_fabricated_placeholder(self):
        """Omitting F3/F7/RL price data must shrink the list, never insert a fake entry."""
        f5_row = _f5_row()
        group = build_expression_group("away", "g1", "NYY", f5_row=f5_row,
                                       f5_protected_no_leg=None, full_game_ml_row=None)
        families = [e["family"] for e in group]
        assert families == [FAMILY_F5_WIN]

    def test_all_six_entries_present_when_all_data_supplied(self):
        f5_row = _f5_row()
        no_leg = _tie_leg()
        ml_row = _f5_row(model_prob=55.0)
        group = build_expression_group(
            "away", "g1", "NYY", f5_row=f5_row, f5_protected_no_leg=no_leg, full_game_ml_row=ml_row,
            f3_price_cents=30.0, f7_price_cents=55.0, run_line_price_cents=40.0,
        )
        families = {e["family"] for e in group}
        assert families == {FAMILY_F5_WIN, FAMILY_F5_PROTECTED_NO, FAMILY_FULL_GAME_ML,
                            FAMILY_F3_WIN, FAMILY_F7_WIN, FAMILY_WINNING_MARGIN}


class TestCorrelationRelationshipsReuseCanonicalClassifier:

    def test_full_game_ml_is_duplicate_thesis_with_f5(self):
        f5_row = _f5_row()
        ml_row = _f5_row(model_prob=55.0)
        group = build_expression_group("away", "g1", "NYY", f5_row=f5_row,
                                       f5_protected_no_leg=None, full_game_ml_row=ml_row)
        ml_entry = next(e for e in group if e["family"] == FAMILY_FULL_GAME_ML)
        assert ml_entry["correlationWithPrimary"]["severity"] == "DUPLICATE_THESIS"

    def test_f3_and_f7_are_duplicate_thesis_with_f5(self):
        f5_row = _f5_row()
        group = build_expression_group("away", "g1", "NYY", f5_row=f5_row,
                                       f5_protected_no_leg=None, full_game_ml_row=None,
                                       f3_price_cents=30.0, f7_price_cents=55.0)
        f3_entry = next(e for e in group if e["family"] == FAMILY_F3_WIN)
        f7_entry = next(e for e in group if e["family"] == FAMILY_F7_WIN)
        assert f3_entry["correlationWithPrimary"]["severity"] == "DUPLICATE_THESIS"
        assert f7_entry["correlationWithPrimary"]["severity"] == "DUPLICATE_THESIS"

    def test_run_line_is_duplicate_thesis_with_f5(self):
        f5_row = _f5_row()
        group = build_expression_group("away", "g1", "NYY", f5_row=f5_row,
                                       f5_protected_no_leg=None, full_game_ml_row=None,
                                       run_line_price_cents=40.0)
        rl_entry = next(e for e in group if e["family"] == FAMILY_WINNING_MARGIN)
        assert rl_entry["correlationWithPrimary"]["severity"] == "DUPLICATE_THESIS"

    def test_protected_no_is_tagged_tie_protected_alternate(self):
        f5_row = _f5_row()
        no_leg = _tie_leg()
        group = build_expression_group("away", "g1", "NYY", f5_row=f5_row,
                                       f5_protected_no_leg=no_leg, full_game_ml_row=None)
        no_entry = next(e for e in group if e["family"] == FAMILY_F5_PROTECTED_NO)
        assert no_entry["correlationWithPrimary"]["severity"] == "DUPLICATE_THESIS"
        assert "TIE_PROTECTED_ALTERNATE_EXPRESSION" in no_entry["correlationWithPrimary"]["tags"]

    def test_fee_adjusted_break_even_matches_kalshi_fees_directly(self):
        """Fee-aware fields must be unchanged/unduplicated: this module's
        price-only breakeven is exactly lib.edgelab.kalshi_fees's own
        function output, never a second, locally-approximated formula."""
        from lib.edgelab.kalshi_fees import fee_adjusted_break_even_probability, FEE_TYPE_TAKER
        ref = price_only_reference(FAMILY_F3_WIN, "away", "g1", "NYY", 33.0, "x")
        expected = fee_adjusted_break_even_probability(33.0 / 100.0, fee_type=FEE_TYPE_TAKER)
        assert ref["feeAdjustedBreakEvenProbability"] == round(expected * 100, 3)

    def test_different_teams_in_same_game_are_not_duplicate_thesis(self):
        """Away F3 and home F3 in the SAME game are not the same bet -- a sanity
        check that correlation identity is keyed by team, not just by family."""
        from lib.edgelab.thesis_classification import classify_pair_severity
        away_f3 = {"market": "F3_ML_Away", "gameId": "g1", "awayAbbr": "NYY"}
        home_f3 = {"market": "F3_ML_Home", "gameId": "g1", "homeAbbr": "PHI"}
        severity, _ = classify_pair_severity(away_f3, home_f3)
        assert severity != "DUPLICATE_THESIS"
