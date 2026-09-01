"""
Adversarial grid audit of EVERY production probability path that prices a
Kalshi integer-rung total ladder (KXMLBTOTAL / KXMLBF5TOTAL).

Contract semantics, verified externally against MLB Stats API ground truth
(see docs/EDGELAB_KALSHI_TOTAL_LADDER_SEMANTICS.md):

    YES(rung N) = P(total >= N)
    NO (rung N) = P(total <  N)
    YES + NO    = 1        (binary contract -- there is NO push rung)

These tests sweep projected means, integer thresholds and extreme tails,
and assert the identity holds to floating tolerance in every engine.
"""

import math
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from scripts.build_market_ledger import p_over_total, poisson_pmf
from lib.kalshi_probability_adapters import (
    adapt_total, adapt_team_total, STATUS_SUPPORTED,
)

PROJECTIONS = [0.35, 1.0, 2.6, 3.9, 4.834, 6.5, 8.0, 9.7, 12.4, 18.0]
THRESHOLDS = list(range(1, 21))
TOL = 1e-9


def p_at_least(proj, n, max_r=30):
    """Independent reference implementation: P(X >= n) for Poisson(proj)."""
    return sum((proj ** k) * math.exp(-proj) / math.factorial(k)
               for k in range(n, max_r + 1))


class TestReferenceImplementation(unittest.TestCase):
    def test_p_over_total_is_strictly_greater_than_its_argument(self):
        """The primitive itself is P(X > L); the CALLERS must adapt it."""
        for proj in PROJECTIONS:
            for n in THRESHOLDS:
                self.assertAlmostEqual(
                    p_over_total(proj, n), p_at_least(proj, n + 1), places=9,
                    msg="p_over_total(%r, %r) is not P(X >= N+1)" % (proj, n))

    def test_line_minus_one_yields_at_least_n(self):
        for proj in PROJECTIONS:
            for n in THRESHOLDS:
                self.assertAlmostEqual(
                    p_over_total(proj, n - 1), p_at_least(proj, n), places=9)


class TestAdaptTotalKalshiSemantics(unittest.TestCase):
    """lib/kalshi_probability_adapters.adapt_total -- the Kalshi ladder path."""

    def test_yes_is_probability_of_n_or_more(self):
        for proj in PROJECTIONS:
            for n in THRESHOLDS:
                p_yes, status, reason = adapt_total(proj, n, side="Over")
                self.assertEqual(status, STATUS_SUPPORTED, reason)
                self.assertAlmostEqual(
                    p_yes, p_at_least(proj, n), places=9,
                    msg="adapt_total YES at proj=%r rung=%r is not P(X >= N)" % (proj, n))

    def test_no_is_probability_of_strictly_fewer_than_n(self):
        for proj in PROJECTIONS:
            for n in THRESHOLDS:
                p_no, status, _ = adapt_total(proj, n, side="Under")
                self.assertEqual(status, STATUS_SUPPORTED)
                expected = 1.0 - p_at_least(proj, n)
                self.assertAlmostEqual(p_no, expected, places=9)

    def test_yes_plus_no_is_exactly_one_no_push_rung(self):
        """A Kalshi rung is binary: the exact-N outcome belongs to YES, so
        there is no third 'push' mass to subtract anywhere."""
        for proj in PROJECTIONS:
            for n in THRESHOLDS:
                p_yes, _, _ = adapt_total(proj, n, side="Over")
                p_no, _, _ = adapt_total(proj, n, side="Under")
                self.assertAlmostEqual(p_yes + p_no, 1.0, delta=TOL)

    def test_exact_n_mass_is_inside_yes_not_excluded(self):
        """The whole point of the defect: PMF(N) must land on the YES side."""
        for proj in PROJECTIONS:
            for n in THRESHOLDS:
                p_yes, _, _ = adapt_total(proj, n, side="Over")
                strictly_over = p_over_total(proj, n)
                self.assertAlmostEqual(
                    p_yes - strictly_over, poisson_pmf(n, proj), places=9,
                    msg="PMF(N) not included in YES at proj=%r rung=%r" % (proj, n))

    def test_monotone_decreasing_in_threshold(self):
        for proj in PROJECTIONS:
            probs = [adapt_total(proj, n, side="Over")[0] for n in THRESHOLDS]
            for a, b in zip(probs, probs[1:]):
                self.assertGreaterEqual(a + TOL, b)

    def test_extreme_probabilities_stay_in_range(self):
        for proj in (0.01, 0.35, 25.0, 40.0):
            for n in (1, 2, 30, 60):
                p_yes, status, _ = adapt_total(proj, n, side="Over")
                if status != STATUS_SUPPORTED:
                    continue
                self.assertGreaterEqual(p_yes, -TOL)
                self.assertLessEqual(p_yes, 1.0 + TOL)
                p_no, _, _ = adapt_total(proj, n, side="Under")
                self.assertAlmostEqual(p_yes + p_no, 1.0, delta=TOL)

    def test_rung_one_is_probability_of_at_least_one_run(self):
        """
        Concrete anchor: rung -1 is 'at least 1 run', i.e. 1 - P(0).

        Checked only over MLB-realistic combined-run projections. The
        primitive truncates its Poisson tail at max_r=30, so the summation
        deviates from the closed form by the discarded tail -- ~4e-8 at a
        projection of 9.7, but ~6e-6 at 12.4 and ~3e-3 at 18. Real MLB
        combined-total projections sit around 3-11, where the error is far
        below a one-cent price tick. The exact identity is pinned below.
        """
        for proj in [p for p in PROJECTIONS if p <= 11.0]:
            p_yes, _, _ = adapt_total(proj, 1, side="Over")
            self.assertAlmostEqual(p_yes, 1.0 - math.exp(-proj), places=7)

    def test_tail_truncation_is_the_only_deviation_from_the_closed_form(self):
        """
        The gap between the summation and the closed form is EXACTLY the
        probability mass discarded beyond max_r=30 -- no other error term
        exists. Documents a real limit of the shared primitive rather than
        asserting a precision it does not have.
        """
        for proj in PROJECTIONS:
            p_yes, _, _ = adapt_total(proj, 1, side="Over")
            gap = (1.0 - math.exp(-proj)) - p_yes
            discarded = sum((proj ** k) * math.exp(-proj) / math.factorial(k)
                            for k in range(31, 120))
            self.assertAlmostEqual(gap, discarded, places=12)

    def test_truncation_is_immaterial_at_realistic_mlb_projections(self):
        """Below a one-cent tick (1e-2) by a wide margin for real slates."""
        for proj in (3.0, 6.5, 8.0, 9.7, 11.0):
            p_yes, _, _ = adapt_total(proj, 1, side="Over")
            self.assertLess(abs((1.0 - math.exp(-proj)) - p_yes), 1e-6)


class TestAdaptTeamTotalNeedsNoCorrection(unittest.TestCase):
    """Team totals arrive as the HALF-POINT N - 0.5 and are already correct;
    applying adapt_total's -1 here would double-count the shift."""

    def test_half_point_line_already_means_at_least_n(self):
        for proj in PROJECTIONS:
            for n in THRESHOLDS:
                p_yes, status, _ = adapt_team_total(proj, n - 0.5, side="Over")
                self.assertEqual(status, STATUS_SUPPORTED)
                self.assertAlmostEqual(p_yes, p_at_least(proj, n), places=9)

    def test_team_total_yes_plus_no_is_one(self):
        for proj in PROJECTIONS:
            for n in THRESHOLDS:
                p_yes, _, _ = adapt_team_total(proj, n - 0.5, side="Over")
                p_no, _, _ = adapt_team_total(proj, n - 0.5, side="Under")
                self.assertAlmostEqual(p_yes + p_no, 1.0, delta=TOL)


class TestBuildMarketLedgerGameTotalCallSite(unittest.TestCase):
    """The ledger's Game_Total block must pass tot_line - 1 (source check --
    the block itself is wrapped in slate-loading code that is not importable
    in isolation)."""

    def _src(self):
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            '..', 'scripts', 'build_market_ledger.py')
        with open(path) as fh:
            return [l for l in fh.readlines() if not l.lstrip().startswith('#')]

    def test_game_total_uses_minus_one(self):
        lines = self._src()
        self.assertTrue(
            any('p_over_total(total_proj, tot_line - 1)' in l for l in lines),
            'Game_Total must price P(total >= N) via tot_line - 1')

    def test_no_unadjusted_game_total_call_remains(self):
        lines = self._src()
        bad = [l.rstrip() for l in lines
               if 'p_over_total(total_proj, tot_line)' in l and 'tot_line - 1' not in l]
        self.assertEqual(bad, [], 'unadjusted strict-over Game_Total call found')


class TestNoThirdStrictOverKalshiTotalPath(unittest.TestCase):
    """Regression guard: adapt_total is the only Kalshi-facing total pricer,
    and it must not revert to an unadjusted p_over_total call."""

    def test_adapt_total_does_not_call_p_over_total_unadjusted(self):
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            '..', 'lib', 'kalshi_probability_adapters.py')
        with open(path) as fh:
            lines = [l for l in fh.readlines() if not l.lstrip().startswith('#')]
        bad = [l.rstrip() for l in lines
               if 'p_over_total(total_proj, line)' in l]
        self.assertEqual(bad, [], 'adapt_total reverted to strict-over semantics')


if __name__ == '__main__':
    unittest.main()
