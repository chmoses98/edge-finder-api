#!/usr/bin/env python3
"""
tests/research/test_k_ladder.py
====================================
Regression tests for lib/research/k_ladder.py -- pitcher strikeout ladder
tail discipline (full-ladder construction, monotonicity self-check, and
the distributional uncertainty-aware "knee" selector).

Required coverage per the MLB Model Expression Guardrails spec:
  - P(K>=8) <= P(K>=7) <= P(K>=6);
  - ladder probabilities form a valid distribution;
  - extreme tail cannot receive inflated confidence from tiny probability errors;
  - central threshold can be preferred even when tail has larger nominal payout;
  - tail can still qualify when genuine edge is strong.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from lib.research.k_ladder import (
    build_strikeout_ladder,
    tail_uncertainty_discount,
    evaluate_k_ladder_expressions,
    CENTRAL_Z_BAND,
)


# A realistic starter: ~24 batters faced, 24% K rate -> mean ~5.76 Ks.
BATTERS_FACED = 24.0
K_PCT = 24.0


class TestLadderMonotonicityAndBounds:
    def test_probabilities_decline_as_threshold_rises(self):
        ladder = build_strikeout_ladder(BATTERS_FACED, K_PCT, [5, 6, 7, 8, 9, 10])
        p = ladder["probabilities"]
        assert p[8] <= p[7] <= p[6] <= p[5]
        assert p[10] <= p[9] <= p[8]

    def test_monotonic_flag_is_true(self):
        ladder = build_strikeout_ladder(BATTERS_FACED, K_PCT, range(1, 15))
        assert ladder["monotonic"] is True

    def test_bounds_valid_flag_is_true(self):
        ladder = build_strikeout_ladder(BATTERS_FACED, K_PCT, range(1, 15))
        assert ladder["boundsValid"] is True
        for p in ladder["probabilities"].values():
            assert 0.0 <= p <= 1.0

    def test_threshold_zero_is_certain(self):
        ladder = build_strikeout_ladder(BATTERS_FACED, K_PCT, [0])
        assert ladder["probabilities"][0] == 1.0

    def test_missing_data_never_fabricated(self):
        ladder = build_strikeout_ladder(None, K_PCT, [5, 6, 7])
        assert all(v is None for v in ladder["probabilities"].values())
        assert ladder["mean"] is None
        assert ladder["monotonic"] is True   # nothing to violate
        assert ladder["boundsValid"] is True

    def test_duplicate_and_unsorted_thresholds_deduplicated(self):
        ladder = build_strikeout_ladder(BATTERS_FACED, K_PCT, [7, 5, 7, 6])
        assert set(ladder["probabilities"].keys()) == {5, 6, 7}


class TestTailUncertaintyDiscount:
    def test_central_threshold_is_undiscounted(self):
        # mean ~5.76, std ~2.09 for this fixture -- threshold 6 is well within 1 SD.
        ladder = build_strikeout_ladder(BATTERS_FACED, K_PCT, [6])
        discount = tail_uncertainty_discount(6, ladder["mean"], ladder["std"])
        assert discount == 1.0

    def test_far_tail_threshold_is_materially_discounted(self):
        ladder = build_strikeout_ladder(BATTERS_FACED, K_PCT, [11])
        discount = tail_uncertainty_discount(11, ladder["mean"], ladder["std"])
        assert discount < 0.2

    def test_discount_is_monotonic_non_increasing_with_distance_from_mean(self):
        ladder = build_strikeout_ladder(BATTERS_FACED, K_PCT, [5, 6, 7, 8, 9, 10, 11, 12])
        mean, std = ladder["mean"], ladder["std"]
        discounts = [tail_uncertainty_discount(n, mean, std) for n in (6, 7, 8, 9, 10, 11, 12)]
        assert discounts == sorted(discounts, reverse=True)

    def test_missing_std_never_discounts(self):
        assert tail_uncertainty_discount(20, 5.0, None) == 1.0
        assert tail_uncertainty_discount(20, None, 2.0) == 1.0


class TestEvaluateKLadderExpressions:
    def test_probabilities_reported_honestly_unmodified_by_discount(self):
        """The tail discount touches EV/ranking, never the reported probability itself."""
        ladder = build_strikeout_ladder(BATTERS_FACED, K_PCT, [6, 10])
        prices = {6: 55, 10: 8}
        result = evaluate_k_ladder_expressions(ladder, prices)
        assert result["thresholds"][6]["probability"] == ladder["probabilities"][6]
        assert result["thresholds"][10]["probability"] == ladder["probabilities"][10]

    def test_central_threshold_preferred_over_tail_with_larger_nominal_payout(self):
        """
        A central 6+ threshold priced fairly (small but real edge) beats a
        deep-tail 12+ threshold that LOOKS attractive on raw nominal edge
        (a cheap price implying a big payout) once the tail's own
        uncertainty discount is applied.
        """
        ladder = build_strikeout_ladder(BATTERS_FACED, K_PCT, [6, 12])
        p6 = ladder["probabilities"][6]
        p12 = ladder["probabilities"][12]
        assert p12 < 0.01   # confirm this really is a deep, thin tail probability
        # Price 6+ with a modest, realistic edge; price 12+ at 70% of its
        # own fair probability -- a real, meaningful mispricing that
        # produces a LARGER raw nominal edge than 6+'s -- the naive
        # "biggest edge wins" rule would pick 12+.
        price_6 = round((p6 - 0.03) * 100, 2)
        price_12 = round(p12 * 100 * 0.7, 2)
        prices = {6: price_6, 12: price_12}
        result = evaluate_k_ladder_expressions(ladder, prices)
        raw_edge_6 = result["thresholds"][6]["netExpectedValuePerDollar"]
        raw_edge_12 = result["thresholds"][12]["netExpectedValuePerDollar"]
        assert raw_edge_12 > raw_edge_6   # the tail DOES look better on raw edge alone
        assert result["bestExpression"] == 6   # but risk-adjusted, the central threshold wins

    def test_tail_can_still_qualify_with_a_genuinely_strong_edge(self):
        """A strong enough raw tail edge survives the discount and wins -- never an outright ban on high thresholds."""
        ladder = build_strikeout_ladder(BATTERS_FACED, K_PCT, [6, 9])
        p6 = ladder["probabilities"][6]
        p9 = ladder["probabilities"][9]
        # 6+ priced with only a razor-thin (but still qualifying) edge;
        # 9+ priced at 30% of its own fair probability -- an extreme
        # market mispricing, but a genuine, honestly-priced one (no
        # unrealistic price-floor exploitation).
        price_6 = round((p6 - 0.02) * 100, 2)
        price_9 = round(max(1.0, p9 * 100 * 0.3), 2)
        prices = {6: price_6, 9: price_9}
        result = evaluate_k_ladder_expressions(ladder, prices)
        assert result["thresholds"][6]["qualifies"] is True   # central also genuinely qualifies here
        assert result["thresholds"][9]["qualifies"] is True
        assert result["bestExpression"] == 9

    def test_never_selects_a_non_qualifying_threshold(self):
        ladder = build_strikeout_ladder(BATTERS_FACED, K_PCT, [6, 9])
        p6 = ladder["probabilities"][6]
        p9 = ladder["probabilities"][9]
        # Both priced ABOVE fair probability (both -EV).
        prices = {6: round((p6 + 0.05) * 100, 2), 9: round((p9 + 0.05) * 100, 2)}
        result = evaluate_k_ladder_expressions(ladder, prices)
        assert result["bestExpression"] is None
        assert result["thresholds"][6]["qualifies"] is False
        assert result["thresholds"][9]["qualifies"] is False

    def test_extreme_tail_tiny_probability_error_cannot_inflate_confidence(self):
        """
        A 12+ threshold (extreme tail, probability near zero) priced just
        barely favorably by a hairline probability estimation nudge
        produces only a tiny raw edge -- and the tail discount suppresses
        its risk-adjusted EV well below a properly-priced central
        threshold's, so it can never look like the best expression off a
        near-noise-level probability wobble.
        """
        ladder = build_strikeout_ladder(BATTERS_FACED, K_PCT, [6, 12])
        p6 = ladder["probabilities"][6]
        p12 = ladder["probabilities"][12]
        assert p12 < 0.01   # confirm genuinely deep tail
        price_6 = round((p6 - 0.03) * 100, 2)
        # 12+ priced just below its own (tiny) fair probability -- the
        # kind of wafer-thin "edge" a small model error could produce.
        price_12 = round(max(0.5, (p12 - p12 * 0.1) * 100), 2)
        prices = {6: price_6, 12: price_12}
        result = evaluate_k_ladder_expressions(ladder, prices)
        assert result["bestExpression"] == 6

    def test_missing_price_for_a_threshold_is_simply_excluded(self):
        ladder = build_strikeout_ladder(BATTERS_FACED, K_PCT, [6, 7])
        prices = {6: 55}
        result = evaluate_k_ladder_expressions(ladder, prices)
        assert 6 in result["thresholds"]
        assert 7 not in result["thresholds"]


class TestDeterminism:
    def test_repeated_calls_produce_identical_output(self):
        ladder1 = build_strikeout_ladder(BATTERS_FACED, K_PCT, [5, 6, 7, 8, 9])
        ladder2 = build_strikeout_ladder(BATTERS_FACED, K_PCT, [5, 6, 7, 8, 9])
        assert ladder1 == ladder2
        prices = {5: 65, 6: 50, 7: 32, 8: 18, 9: 9}
        r1 = evaluate_k_ladder_expressions(ladder1, prices)
        r2 = evaluate_k_ladder_expressions(ladder2, prices)
        assert r1 == r2
