#!/usr/bin/env python3
"""
tests/test_tier_disagreement_cap.py
=======================================
Tier/confidence calibration mission: coverage for
scripts/build_market_ledger.py's cap_tier_for_disagreement() and its
wiring into the ML/TT/F5/NRFI/YRFI confidence_from_edge() call sites.

Audited gap: confidence_from_edge() only ever looked at the row's own
calibrated executable edge -- nothing capped Tier A ("HIGH") when the
model and the market (Kalshi VF) sharply disagreed. cap_tier_for_
disagreement() closes that gap: a row that would otherwise qualify for
HIGH is capped at MEDIUM whenever |rawEdgeVsVF| exceeds
DISAGREEMENT_FLAG_PCT (7 percentage points) -- downgrade-only, never a
booster, and never touches a row that wasn't already HIGH.
"""
import os
import sys
import unittest

SCRIPTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'scripts')
sys.path.insert(0, SCRIPTS_DIR)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import build_market_ledger as bml
from test_lineup_gate import _make_game


def _row(ledger, market):
    for r in ledger:
        if r['market'] == market:
            return r
    raise KeyError(f'Market {market!r} not found in ledger')


class TestCapTierForDisagreementPure(unittest.TestCase):
    """Pure-function coverage for cap_tier_for_disagreement()."""

    def test_thin_edge_cannot_become_tier_a(self):
        """A merely-qualifying (non-HIGH) edge never reaches Tier A --
        confidence_from_edge()'s own threshold, unaffected by this gate."""
        self.assertEqual(bml.confidence_from_edge(1.2), 'PAPER')
        self.assertEqual(bml.confidence_from_edge(2.0), 'MEDIUM')
        self.assertIsNone(bml.confidence_from_edge(0.5))

    def test_unresolved_gt_7pt_disagreement_prevents_tier_a(self):
        gates = []
        capped = bml.cap_tier_for_disagreement('HIGH', 8.0, gates)
        self.assertEqual(capped, 'MEDIUM')
        self.assertEqual(len(gates), 1)
        self.assertIn('Tier cap', gates[0])
        self.assertIn('8.0pt', gates[0])

    def test_clean_strong_edge_can_qualify(self):
        """Disagreement within the 7pt flag leaves a genuine HIGH tier untouched."""
        gates = []
        result = bml.cap_tier_for_disagreement('HIGH', 4.6, gates)
        self.assertEqual(result, 'HIGH')
        self.assertEqual(gates, [])

    def test_disagreement_exactly_at_boundary_does_not_cap(self):
        """Exactly DISAGREEMENT_FLAG_PCT (7.0) is the inclusive pass side -- only strictly > caps."""
        gates = []
        result = bml.cap_tier_for_disagreement('HIGH', bml.DISAGREEMENT_FLAG_PCT, gates)
        self.assertEqual(result, 'HIGH')
        self.assertEqual(gates, [])

    def test_disagreement_one_tenth_over_boundary_caps(self):
        gates = []
        result = bml.cap_tier_for_disagreement('HIGH', bml.DISAGREEMENT_FLAG_PCT + 0.1, gates)
        self.assertEqual(result, 'MEDIUM')
        self.assertEqual(len(gates), 1)

    def test_negative_disagreement_is_measured_by_magnitude(self):
        """The model can disagree in EITHER direction -- a large negative
        gap (model well below market) is just as much an audit flag as
        a large positive one."""
        gates = []
        result = bml.cap_tier_for_disagreement('HIGH', -12.0, gates)
        self.assertEqual(result, 'MEDIUM')

    def test_never_a_confidence_booster_on_non_high_tiers(self):
        """A huge disagreement never touches MEDIUM/PAPER/None -- this
        is a ceiling on HIGH specifically, never a general re-scoring,
        and it can never raise a tier."""
        gates = []
        self.assertEqual(bml.cap_tier_for_disagreement('MEDIUM', 40.0, gates), 'MEDIUM')
        self.assertEqual(bml.cap_tier_for_disagreement('PAPER', 40.0, gates), 'PAPER')
        self.assertIsNone(bml.cap_tier_for_disagreement(None, 40.0, gates))
        self.assertEqual(gates, [])

    def test_unknown_disagreement_never_fabricates_a_cap(self):
        """No VF reference to compare against -- never guessed, never capped."""
        gates = []
        result = bml.cap_tier_for_disagreement('HIGH', None, gates)
        self.assertEqual(result, 'HIGH')
        self.assertEqual(gates, [])


class TestDisagreementCapIntegration(unittest.TestCase):
    """Full evaluate_game() coverage: proves the gate is actually wired
    into all four market sections, not just defined."""

    def test_large_disagreement_caps_ml_home_at_medium(self):
        g = _make_game(ml_away_am=-400, ml_home_am=320)
        row = _row(bml.evaluate_game(g), 'ML_Home')

        self.assertEqual(row['status'], 'Accepted')
        self.assertGreaterEqual(row['calibratedEdgeVsExecutable'], bml.THRESHOLD_HIGH,
                                "fixture must actually qualify for HIGH on edge alone")
        self.assertGreater(abs(row['rawEdgeVsVF']), bml.DISAGREEMENT_FLAG_PCT)
        self.assertEqual(row['confidenceTier'], 'MEDIUM')
        self.assertTrue(any('Tier cap' in gc for gc in row['gatesFired']))

    def test_clean_strong_edge_reaches_tier_a_in_evaluate_game(self):
        """Real executable price meaningfully better than the model's fair
        value (post-friction edge >= HIGH threshold) while the mid-derived
        Kalshi VF stays close to the model (disagreement well within the
        7pt flag) -- a genuinely clean, well-supported HIGH-tier row."""
        g = _make_game(ml_away_am=-105, ml_home_am=-105)
        g['odds']['kalshi']['ml']['home_yes_ask'] = 40.0
        row = _row(bml.evaluate_game(g), 'ML_Home')

        self.assertEqual(row['status'], 'Accepted')
        self.assertLessEqual(abs(row['rawEdgeVsVF']), bml.DISAGREEMENT_FLAG_PCT)
        self.assertEqual(row['confidenceTier'], 'HIGH')
        self.assertFalse(any('Tier cap' in gc for gc in row['gatesFired']))


if __name__ == '__main__':
    unittest.main(verbosity=2)
