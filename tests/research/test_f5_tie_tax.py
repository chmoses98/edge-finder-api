#!/usr/bin/env python3
"""
tests/research/test_f5_tie_tax.py
=====================================
Regression tests for lib/research/f5_tie_tax.py -- the F3/F5 "tie tax" /
contract-structure comparison (THREE_WAY_YES vs PROTECTED_NO).

Required coverage per the MLB Model Expression Guardrails spec:
  1. three-way YES loses on tie (its payoff condition excludes tie).
  2. protected NO wins on tie (its true probability includes tie mass).
  3. tie probability enters valuation correctly.
  4. model can prefer protected NO when net EV is higher.
  5. model can still prefer three-way YES when price is sufficiently better.
  6. no accidental inversion of YES/NO semantics.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from lib.research.f5_tie_tax import (
    evaluate_f5_tie_tax,
    THREE_WAY_YES,
    PROTECTED_NO,
    BEST_EXPRESSION,
    INFERIOR_NET_EV,
    TIE_PROTECTION_ADVANTAGE,
    NO_QUALIFYING_EXPRESSION,
)


class TestPayoffSemantics:
    """1/2/6: THREE_WAY_YES excludes tie, PROTECTED_NO includes it -- never inverted."""

    def test_three_way_yes_true_probability_excludes_tie(self):
        r = evaluate_f5_tie_tax('away', p_favored_lead=0.45, p_tie=0.20,
                                 three_way_yes_price_cents=45, protected_no_price_cents=35)
        assert r['threeWayYes']['trueProbability'] == 0.45
        assert 'loses on a tie' in r['threeWayYes']['payoffCondition']

    def test_protected_no_true_probability_includes_tie(self):
        r = evaluate_f5_tie_tax('away', p_favored_lead=0.45, p_tie=0.20,
                                 three_way_yes_price_cents=45, protected_no_price_cents=35)
        assert r['protectedNo']['trueProbability'] == 0.65  # 0.45 + 0.20
        assert 'OR tie' in r['protectedNo']['payoffCondition']

    def test_protected_no_probability_always_greater_or_equal_to_three_way_yes(self):
        for p_tie in (0.0, 0.05, 0.15, 0.30):
            r = evaluate_f5_tie_tax('home', p_favored_lead=0.40, p_tie=p_tie,
                                     three_way_yes_price_cents=40, protected_no_price_cents=40)
            assert r['protectedNo']['trueProbability'] >= r['threeWayYes']['trueProbability']

    def test_zero_tie_probability_makes_true_probabilities_equal(self):
        """Sanity limit case: with no tie mass, the two expressions have identical true probability."""
        r = evaluate_f5_tie_tax('away', p_favored_lead=0.55, p_tie=0.0,
                                 three_way_yes_price_cents=50, protected_no_price_cents=50)
        assert r['threeWayYes']['trueProbability'] == r['protectedNo']['trueProbability'] == 0.55

    def test_favored_side_recorded_verbatim(self):
        r = evaluate_f5_tie_tax('home', p_favored_lead=0.5, p_tie=0.1,
                                 three_way_yes_price_cents=50, protected_no_price_cents=45)
        assert r['favoredSide'] == 'home'


class TestTieProbabilityEntersValuationCorrectly:
    """3: increasing tie probability (at fixed prices) strictly increases PROTECTED_NO's net EV."""

    def test_increasing_tie_probability_increases_protected_no_ev(self):
        evs = []
        for p_tie in (0.05, 0.15, 0.25):
            r = evaluate_f5_tie_tax('away', p_favored_lead=0.40, p_tie=p_tie,
                                     three_way_yes_price_cents=40, protected_no_price_cents=40)
            evs.append(r['protectedNo']['netExpectedValuePerDollar'])
        assert evs[0] < evs[1] < evs[2]

    def test_increasing_tie_probability_does_not_change_three_way_yes_ev(self):
        """THREE_WAY_YES's own EV is a pure function of p_favored_lead and its own price -- tie is irrelevant to it."""
        evs = []
        for p_tie in (0.05, 0.15, 0.25):
            r = evaluate_f5_tie_tax('away', p_favored_lead=0.40, p_tie=p_tie,
                                     three_way_yes_price_cents=40, protected_no_price_cents=40)
            evs.append(r['threeWayYes']['netExpectedValuePerDollar'])
        assert evs[0] == evs[1] == evs[2]


class TestPreferenceLogic:
    """4/5: the model can prefer either expression depending on net EV -- never a hardcoded default."""

    def test_prefers_protected_no_when_net_ev_is_higher(self):
        # Large tie probability, same price -> PROTECTED_NO's true probability
        # is materially higher at an identical price, so its net EV wins.
        r = evaluate_f5_tie_tax('away', p_favored_lead=0.40, p_tie=0.25,
                                 three_way_yes_price_cents=40, protected_no_price_cents=40)
        assert r['preferredExpression'] == PROTECTED_NO
        assert r['reasonCode'] == TIE_PROTECTION_ADVANTAGE
        assert r['protectedNo']['reasonCode'] == BEST_EXPRESSION
        assert r['threeWayYes']['reasonCode'] == INFERIOR_NET_EV

    def test_prefers_three_way_yes_when_price_is_sufficiently_better(self):
        # THREE_WAY_YES priced very cheaply relative to its true probability;
        # PROTECTED_NO priced expensively despite its higher true probability --
        # net EV should still favor THREE_WAY_YES.
        r = evaluate_f5_tie_tax('away', p_favored_lead=0.55, p_tie=0.10,
                                 three_way_yes_price_cents=35, protected_no_price_cents=64)
        assert r['preferredExpression'] == THREE_WAY_YES
        assert r['reasonCode'] == BEST_EXPRESSION
        assert r['threeWayYes']['reasonCode'] == BEST_EXPRESSION
        assert r['protectedNo']['reasonCode'] == INFERIOR_NET_EV

    def test_at_equal_price_positive_tie_mass_always_favors_protected_no(self):
        """No structural inversion: at an IDENTICAL price, PROTECTED_NO's strictly higher true probability (any positive tie mass) must win -- this is a mathematical fact of the two payoff conditions, not a hardcoded preference."""
        r = evaluate_f5_tie_tax('home', p_favored_lead=0.60, p_tie=0.01,
                                 three_way_yes_price_cents=50, protected_no_price_cents=50)
        assert r['preferredExpression'] == PROTECTED_NO

    def test_no_automatic_preference_for_no_contracts(self):
        """Never a hardcoded bias toward PROTECTED_NO -- a cheaper THREE_WAY_YES price can still win even with positive tie mass present."""
        r = evaluate_f5_tie_tax('home', p_favored_lead=0.60, p_tie=0.01,
                                 three_way_yes_price_cents=45, protected_no_price_cents=50)
        assert r['preferredExpression'] == THREE_WAY_YES

    def test_neither_qualifies_when_both_below_fee_adjusted_breakeven(self):
        r = evaluate_f5_tie_tax('away', p_favored_lead=0.30, p_tie=0.05,
                                 three_way_yes_price_cents=60, protected_no_price_cents=60)
        assert r['preferredExpression'] is None
        assert r['reasonCode'] == NO_QUALIFYING_EXPRESSION


class TestMissingDataNeverFabricated:
    def test_returns_none_when_probabilities_missing(self):
        assert evaluate_f5_tie_tax('away', None, 0.2, 40, 40) is None
        assert evaluate_f5_tie_tax('away', 0.4, None, 40, 40) is None

    def test_returns_none_when_prices_missing(self):
        assert evaluate_f5_tie_tax('away', 0.4, 0.2, None, 40) is None
        assert evaluate_f5_tie_tax('away', 0.4, 0.2, 40, None) is None

    def test_returns_none_for_invalid_favored_side(self):
        assert evaluate_f5_tie_tax('tie', 0.4, 0.2, 40, 40) is None


class TestDeterminism:
    def test_repeated_calls_produce_identical_output(self):
        r1 = evaluate_f5_tie_tax('away', 0.42, 0.18, 41, 38)
        r2 = evaluate_f5_tie_tax('away', 0.42, 0.18, 41, 38)
        assert r1 == r2
