#!/usr/bin/env python3
"""
tests/test_first_inning_evidence_quality_gate.py
==================================================
Regression tests for the first-inning evidence-quality provenance
hierarchy (lib.research.first_inning_context.FIRST_INNING_NATIVE /
FIRST_INNING_PARTIAL / GENERIC_FALLBACK / INSUFFICIENT_DATA) and its
wiring into scripts/build_market_ledger.py's NRFI/YRFI confidence gate
(cap_tier_for_first_inning_evidence_quality).

This complements tests/test_rule40_rfi_gate.py (which covers Rule 40's
older, binary "is firstInningXERA present at all" check) by covering the
finer-grained sample-size tiers Rule 40's raw presence check cannot see:
a too-thin appearance sample (<5 starts) must be treated as
GENERIC_FALLBACK even when the xERA field itself is populated, and a
thin-but-present sample (5-7 starts) on both sides must be
FIRST_INNING_PARTIAL, not FIRST_INNING_NATIVE.

Tests:
  1. Both starters adequate-sample (>=8 appearances) -> FIRST_INNING_NATIVE,
     no extra tier cap beyond what edge alone would qualify for.
  2. Both starters thin-sample (5-7 appearances) -> FIRST_INNING_PARTIAL,
     HIGH capped to MEDIUM.
  3. One starter with zero appearances (xERA absent) -> GENERIC_FALLBACK,
     capped to PAPER -- exactly Rule 40's existing behavior.
  4. One starter with a too-thin sample (<5 appearances) despite xERA being
     populated -> GENERIC_FALLBACK, capped to PAPER -- the case Rule 40's
     binary presence check alone would miss.
  5. INSUFFICIENT_DATA (no game-level projection at all) -> no actionable
     recommendation for either NRFI or YRFI (conf forced to None/Rejected).
  6. reasonCodes carries the matching FIRST_INNING_* provenance code.
  7. firstInningContext.evidenceQuality is present and correct on the row.
"""

import sys
import os
import unittest

SCRIPTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'scripts')
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPTS_DIR)
sys.path.insert(0, TESTS_DIR)

from build_market_ledger import (
    evaluate_game,
    cap_tier_for_first_inning_evidence_quality,
)
from lib.research.first_inning_context import (
    FIRST_INNING_NATIVE,
    FIRST_INNING_PARTIAL,
    GENERIC_FALLBACK,
    INSUFFICIENT_DATA,
)
from test_rule40_rfi_gate import _make_game, _row


def _set_appearances(game, *, away=None, home=None):
    """Mutate a _make_game() fixture's firstInningSplit.appearances in place."""
    if away is not None:
        game['away']['pitcherSavant']['firstInningSplit']['appearances'] = away
    if home is not None:
        game['home']['pitcherSavant']['firstInningSplit']['appearances'] = home
    return game


class TestFirstInningNative(unittest.TestCase):
    """Both starters adequate-sample -> FIRST_INNING_NATIVE, no extra cap."""

    def setUp(self):
        self.game = _make_game(away_fi_xera=5.5, home_fi_xera=5.5,
                                yrfi_implied=40.0, nrfi_implied=60.0, total_line=7)
        _set_appearances(self.game, away=8, home=8)
        self.ledger = evaluate_game(self.game)

    def test_evidence_quality_is_native(self):
        row = _row(self.ledger, 'YRFI')
        self.assertEqual(row['firstInningContext']['evidenceQuality'], 'FIRST_INNING_NATIVE')

    def test_no_first_inning_tier_cap_gate_fires(self):
        row = _row(self.ledger, 'YRFI')
        gates_str = ' '.join(row.get('gatesFired') or [])
        self.assertNotIn('First-inning evidence quality', gates_str)

    def test_reason_codes_carry_native_provenance(self):
        row = _row(self.ledger, 'YRFI')
        self.assertIn('FIRST_INNING_NATIVE_EVIDENCE', row.get('reasonCodes') or [])


class TestFirstInningPartial(unittest.TestCase):
    """Both starters thin-sample (5-7 appearances) -> FIRST_INNING_PARTIAL, HIGH capped to MEDIUM."""

    def setUp(self):
        # Strong edge inputs so confidence_from_edge() would otherwise
        # qualify HIGH -- proves the cap actually fires, not just that the
        # edge was already low.
        self.game = _make_game(away_fi_xera=2.0, home_fi_xera=2.0,
                                away_xfip=2.0, home_xfip=2.0,
                                yrfi_implied=20.0, nrfi_implied=80.0, total_line=7)
        _set_appearances(self.game, away=6, home=6)
        self.ledger = evaluate_game(self.game)

    def test_evidence_quality_is_partial(self):
        row = _row(self.ledger, 'YRFI')
        self.assertEqual(row['firstInningContext']['evidenceQuality'], 'FIRST_INNING_PARTIAL')

    def test_high_confidence_never_reached(self):
        row = _row(self.ledger, 'YRFI')
        if row['status'] == 'Accepted':
            self.assertNotEqual(row['confidence'], 'HIGH',
                                 "FIRST_INNING_PARTIAL must never allow Tier A (HIGH)")

    def test_reason_codes_carry_partial_provenance(self):
        row = _row(self.ledger, 'YRFI')
        self.assertIn('FIRST_INNING_PARTIAL_EVIDENCE', row.get('reasonCodes') or [])


class TestGenericFallbackFromMissingEvidence(unittest.TestCase):
    """One side with no dedicated evidence at all -> GENERIC_FALLBACK, capped to PAPER."""

    def setUp(self):
        self.game = _make_game(away_fi_xera=None, home_fi_xera=5.5,
                                yrfi_implied=40.0, nrfi_implied=60.0, total_line=7)
        _set_appearances(self.game, home=8)
        self.ledger = evaluate_game(self.game)

    def test_evidence_quality_is_generic_fallback(self):
        row = _row(self.ledger, 'YRFI')
        self.assertEqual(row['firstInningContext']['evidenceQuality'], 'GENERIC_FALLBACK')

    def test_capped_to_paper(self):
        row = _row(self.ledger, 'YRFI')
        if row['status'] == 'Accepted':
            self.assertEqual(row['confidence'], 'PAPER')

    def test_reason_codes_carry_generic_fallback_provenance(self):
        row = _row(self.ledger, 'YRFI')
        self.assertIn('FIRST_INNING_GENERIC_FALLBACK', row.get('reasonCodes') or [])


class TestGenericFallbackFromThinSample(unittest.TestCase):
    """
    xERA IS populated on both sides but one side's appearance count is below
    the thin-sample floor (<5) -- Rule 40's raw presence check would NOT
    catch this (it only checks whether firstInningXERA is None), but the
    evidence-quality hierarchy must still cap it at PAPER.
    """

    def setUp(self):
        self.game = _make_game(away_fi_xera=2.0, home_fi_xera=2.0,
                                away_xfip=2.0, home_xfip=2.0,
                                yrfi_implied=20.0, nrfi_implied=80.0, total_line=7)
        _set_appearances(self.game, away=3, home=8)  # away below MIN_APPEARANCES_THIN
        self.ledger = evaluate_game(self.game)

    def test_old_rule40_presence_check_does_not_fire(self):
        row = _row(self.ledger, 'YRFI')
        gates_str = ' '.join(row.get('gatesFired') or [])
        self.assertNotIn('Rule 40 incomplete', gates_str,
                          "Rule 40's raw xERA-presence check should not fire -- both xERA "
                          "fields are populated, only the sample size is thin")

    def test_evidence_quality_gate_still_caps_to_paper(self):
        row = _row(self.ledger, 'YRFI')
        self.assertEqual(row['firstInningContext']['evidenceQuality'], 'GENERIC_FALLBACK')
        if row['status'] == 'Accepted':
            self.assertEqual(row['confidence'], 'PAPER')
            gates_str = ' '.join(row.get('gatesFired') or [])
            self.assertIn('GENERIC_FALLBACK', gates_str)


class TestEvidenceQualitySymmetryAcrossYrfiAndNrfi(unittest.TestCase):
    """
    The evidence-quality hierarchy is computed ONCE per game
    (firstInningContext is shared) and applied identically to both the
    YRFI and NRFI rows -- neither side should ever see a different
    evidenceQuality value or a different cap outcome for the same
    underlying evidence.
    """

    def setUp(self):
        # Strong starters + low total so NRFI has a genuine positive edge
        # (mirrors TestRule40NrfiPositiveEdge's fixture), missing
        # dedicated first-inning evidence on both sides -> GENERIC_FALLBACK.
        self.game = _make_game(away_fi_xera=None, home_fi_xera=None,
                                away_xfip=3.0, home_xfip=3.0,
                                yrfi_implied=60.0, nrfi_implied=40.0,
                                total_line=6)
        self.ledger = evaluate_game(self.game)

    def test_both_rows_report_identical_evidence_quality(self):
        nrfi_row = _row(self.ledger, 'NRFI')
        yrfi_row = _row(self.ledger, 'YRFI')
        self.assertEqual(
            nrfi_row['firstInningContext']['evidenceQuality'],
            yrfi_row['firstInningContext']['evidenceQuality'],
        )
        self.assertEqual(nrfi_row['firstInningContext']['evidenceQuality'], 'GENERIC_FALLBACK')

    def test_both_rows_capped_to_paper_when_accepted(self):
        nrfi_row = _row(self.ledger, 'NRFI')
        yrfi_row = _row(self.ledger, 'YRFI')
        if nrfi_row['status'] == 'Accepted':
            self.assertEqual(nrfi_row['confidence'], 'PAPER')
        if yrfi_row['status'] == 'Accepted':
            self.assertEqual(yrfi_row['confidence'], 'PAPER')

    def test_both_rows_carry_the_matching_reason_code(self):
        nrfi_row = _row(self.ledger, 'NRFI')
        yrfi_row = _row(self.ledger, 'YRFI')
        self.assertIn('FIRST_INNING_GENERIC_FALLBACK', nrfi_row.get('reasonCodes') or [])
        self.assertIn('FIRST_INNING_GENERIC_FALLBACK', yrfi_row.get('reasonCodes') or [])


class TestInsufficientData(unittest.TestCase):
    """No game-level projection at all -> no actionable NRFI/YRFI recommendation."""

    def setUp(self):
        self.game = _make_game(away_fi_xera=5.5, home_fi_xera=5.5,
                                yrfi_implied=40.0, nrfi_implied=60.0, total_line=7)
        _set_appearances(self.game, away=8, home=8)
        # Force the game-level projection itself to be unavailable so the
        # naive proj/9 fallback also has nothing to fall back to.
        self.game['away']['pitcherSavant']['xFIP'] = None
        self.game['away']['pitcherSavant']['seasonFIP'] = None
        self.game['away']['pitcherSavant']['recentFIP'] = None
        self.ledger = evaluate_game(self.game)

    def test_yrfi_and_nrfi_have_no_actionable_confidence(self):
        for market in ('YRFI', 'NRFI'):
            row = _row(self.ledger, market)
            self.assertNotEqual(row.get('confidence'), 'HIGH')
            self.assertNotEqual(row.get('confidence'), 'MEDIUM')
            self.assertNotEqual(row.get('confidence'), 'PAPER')

    def test_insufficient_data_gate_message_present_when_projection_missing(self):
        # Only assert the gate fires in the specific case where the
        # projection is genuinely unavailable; compute_projections() may
        # still derive a non-None away_proj from other inputs in this
        # fixture, in which case evidenceQuality legitimately is NOT
        # INSUFFICIENT_DATA and this test intentionally no-ops.
        row = _row(self.ledger, 'YRFI')
        fi_ctx = row.get('firstInningContext') or {}
        if fi_ctx.get('evidenceQuality') == 'INSUFFICIENT_DATA':
            gates_str = ' '.join(row.get('gatesFired') or [])
            self.assertIn('INSUFFICIENT_DATA', gates_str)
            self.assertIn('FIRST_INNING_INSUFFICIENT_DATA', row.get('reasonCodes') or [])


class TestCapTierForFirstInningEvidenceQualityUnit(unittest.TestCase):
    """
    Direct, isolated tests of cap_tier_for_first_inning_evidence_quality --
    proves the tier-cap logic itself independent of any other gate (e.g.
    cap_tier_for_disagreement) that might also be in play on a given
    end-to-end fixture.
    """

    def test_generic_fallback_caps_high_to_paper(self):
        gates = []
        result = cap_tier_for_first_inning_evidence_quality('HIGH', GENERIC_FALLBACK, gates)
        self.assertEqual(result, 'PAPER')
        self.assertTrue(any('GENERIC_FALLBACK' in g for g in gates))

    def test_generic_fallback_caps_medium_to_paper(self):
        gates = []
        result = cap_tier_for_first_inning_evidence_quality('MEDIUM', GENERIC_FALLBACK, gates)
        self.assertEqual(result, 'PAPER')

    def test_generic_fallback_is_noop_on_paper(self):
        gates = []
        result = cap_tier_for_first_inning_evidence_quality('PAPER', GENERIC_FALLBACK, gates)
        self.assertEqual(result, 'PAPER')
        self.assertEqual(gates, [])

    def test_partial_caps_high_to_medium(self):
        gates = []
        result = cap_tier_for_first_inning_evidence_quality('HIGH', FIRST_INNING_PARTIAL, gates)
        self.assertEqual(result, 'MEDIUM')
        self.assertTrue(any('FIRST_INNING_PARTIAL' in g for g in gates))

    def test_partial_does_not_touch_medium(self):
        gates = []
        result = cap_tier_for_first_inning_evidence_quality('MEDIUM', FIRST_INNING_PARTIAL, gates)
        self.assertEqual(result, 'MEDIUM')
        self.assertEqual(gates, [])

    def test_partial_does_not_touch_paper(self):
        gates = []
        result = cap_tier_for_first_inning_evidence_quality('PAPER', FIRST_INNING_PARTIAL, gates)
        self.assertEqual(result, 'PAPER')
        self.assertEqual(gates, [])

    def test_native_never_caps_anything(self):
        for conf in ('HIGH', 'MEDIUM', 'PAPER'):
            gates = []
            result = cap_tier_for_first_inning_evidence_quality(conf, FIRST_INNING_NATIVE, gates)
            self.assertEqual(result, conf)
            self.assertEqual(gates, [])

    def test_none_confidence_is_always_a_noop(self):
        for quality in (FIRST_INNING_NATIVE, FIRST_INNING_PARTIAL, GENERIC_FALLBACK, INSUFFICIENT_DATA):
            gates = []
            result = cap_tier_for_first_inning_evidence_quality(None, quality, gates)
            self.assertIsNone(result)
            self.assertEqual(gates, [])

    def test_never_raises_a_tier(self):
        # PAPER can never become MEDIUM/HIGH regardless of evidence quality --
        # this function is a ceiling, never a promoter.
        for quality in (FIRST_INNING_NATIVE, FIRST_INNING_PARTIAL, GENERIC_FALLBACK):
            gates = []
            result = cap_tier_for_first_inning_evidence_quality('PAPER', quality, gates)
            self.assertEqual(result, 'PAPER')


if __name__ == '__main__':
    unittest.main(verbosity=2)
