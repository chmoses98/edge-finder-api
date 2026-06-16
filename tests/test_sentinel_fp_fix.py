#!/usr/bin/env python3
"""
tests/test_sentinel_fp_fix.py
================================
Tests for Fix 1: Sentinel false-positive on model output fields.

The sentinel-100 rule must NOT fire when scanning model output probability
fields (modelPct, modelProb, calibProb, etc.) because these can legitimately
be exactly 100.

The sentinel-100 rule MUST still fire when scanning settlement/result fields.
"""

import sys, os, unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from lib.sentinel_validator import (
    scan_for_sentinels,
    validate_slate_for_sentinels,
    validate_no_sentinels,
    SentinelValidationError,
    MODEL_OUTPUT_PROB_FIELDS,
    SETTLEMENT_FIELDS,
    PROB_FIELDS,
)


class TestSentinelFPFix(unittest.TestCase):

    def test_yrfi_modelPct_100_does_not_quarantine(self):
        """YRFI/NRFI modelPct=100 must NOT trigger sentinel quarantine."""
        slate = {
            "games": [
                {
                    "gameId": "101",
                    "markets": [
                        {"marketType": "YRFI", "modelPct": 100, "price": -135},
                    ]
                }
            ]
        }
        is_valid, bad_games = validate_slate_for_sentinels(slate, raise_on_error=False)
        self.assertTrue(is_valid,
            f"modelPct=100 on YRFI must NOT quarantine slate. Bad games: {bad_games}")

    def test_nrfi_modelPct_100_does_not_quarantine(self):
        """NRFI modelPct=100 must NOT trigger sentinel quarantine."""
        slate = {
            "games": [
                {
                    "gameId": "102",
                    "markets": [
                        {"marketType": "NRFI", "modelPct": 100, "price": -190},
                    ]
                }
            ]
        }
        is_valid, bad_games = validate_slate_for_sentinels(slate, raise_on_error=False)
        self.assertTrue(is_valid,
            f"NRFI modelPct=100 must NOT quarantine slate. Bad games: {bad_games}")

    def test_modelProb_100_does_not_quarantine(self):
        """modelProb=100 (0-100 scale) must NOT trigger sentinel."""
        slate = {
            "games": [
                {
                    "gameId": "103",
                    "markets": [{"modelProb": 100, "price": -150}]
                }
            ]
        }
        is_valid, bad_games = validate_slate_for_sentinels(slate, raise_on_error=False)
        self.assertTrue(is_valid,
            f"modelProb=100 must NOT quarantine slate. Bad games: {bad_games}")

    def test_model_output_fields_with_100_pass(self):
        """All MODEL_OUTPUT_PROB_FIELDS at value=100 must pass the scanner."""
        for field in MODEL_OUTPUT_PROB_FIELDS:
            obj = {field: 100}
            offenders = scan_for_sentinels(obj)
            self.assertEqual(offenders, [],
                f"Field '{field}' with value=100 must NOT be flagged as sentinel. "
                f"Offenders: {offenders}")

    def test_settlement_field_at_100_triggers_sentinel(self):
        """settlement/result field with value=100 MUST trigger sentinel."""
        # At least one settlement field must be checked
        # Using 'result' which is in SETTLEMENT_FIELDS
        for field in ('result', 'settlement', 'closing_price', 'settle_price'):
            obj = {field: 100}
            offenders = scan_for_sentinels(obj)
            self.assertGreater(len(offenders), 0,
                f"Field '{field}'=100 must be flagged as sentinel but was not")

    def test_actual_sentinel_price_still_quarantines(self):
        """A true sentinel price (19900) must still quarantine the slate."""
        slate = {
            "games": [
                {
                    "gameId": "200",
                    "markets": [{"price": 19900, "modelPct": 65}]
                }
            ]
        }
        is_valid, bad_games = validate_slate_for_sentinels(slate, raise_on_error=False)
        self.assertFalse(is_valid,
            "price=19900 must quarantine slate")
        self.assertIn("200", bad_games)

    def test_protect_slate_passes_valid_modelPct_100_slate(self):
        """Simulated protect_slate scan: modelPct=100 slate passes, 19900 slate fails."""
        good_slate = {
            "games": [
                {
                    "gameId": "300",
                    "markets": [{"modelPct": 100, "price": -135, "awayML": -141}],
                    "awayTeamStats": {"modelProbYRFI": 100},
                }
            ]
        }
        offenders = scan_for_sentinels(good_slate)
        self.assertEqual(offenders, [],
            f"Clean slate with modelPct=100 must produce no offenders: {offenders}")

        bad_slate = {
            "games": [
                {
                    "gameId": "301",
                    "markets": [{"price": 19900}],
                }
            ]
        }
        offenders_bad = scan_for_sentinels(bad_slate)
        self.assertGreater(len(offenders_bad), 0,
            "Slate with price=19900 must produce offenders")

    def test_model_output_fields_excluded_from_prob_fields(self):
        """MODEL_OUTPUT_PROB_FIELDS must be completely excluded from PROB_FIELDS."""
        overlap = MODEL_OUTPUT_PROB_FIELDS & PROB_FIELDS
        self.assertEqual(overlap, frozenset(),
            f"MODEL_OUTPUT_PROB_FIELDS must have NO overlap with PROB_FIELDS. "
            f"Overlap found: {overlap}")

    def test_settlement_fields_included_in_prob_fields(self):
        """SETTLEMENT_FIELDS (result, settlement, etc.) must be in PROB_FIELDS."""
        for field in ('result', 'settlement'):
            self.assertIn(field, PROB_FIELDS,
                f"Settlement field '{field}' must be in PROB_FIELDS for sentinel detection")


if __name__ == '__main__':
    unittest.main(verbosity=2)
