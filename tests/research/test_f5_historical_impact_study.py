#!/usr/bin/env python3
"""
tests/research/test_f5_historical_impact_study.py
=======================================================
F5 Three-Way Pricing Correction milestone: coverage for
scripts/research/f5_historical_impact_study.py.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts", "research"))

import f5_historical_impact_study as study  # noqa: E402


class TestLegacyRenormalizedHelper:

    def test_matches_the_actual_legacy_production_formula(self):
        # Same formula as the old, now-removed build_market_ledger.py code:
        # p_net = p_win / (1 - p_push)
        assert study._legacy_renormalized(0.4759, 0.1983) == (0.4759 / (1 - 0.1983))

    def test_degenerate_all_tie_mass_returns_win_prob_unchanged(self):
        assert study._legacy_renormalized(0.0, 1.0) == 0.0


class TestConfidenceTier:

    def test_below_floor_is_none(self):
        assert study._confidence_tier(0.5) is None

    def test_high_at_or_above_threshold(self):
        assert study._confidence_tier(3.0) == "HIGH"

    def test_medium_band(self):
        assert study._confidence_tier(2.0) == "MEDIUM"

    def test_paper_band(self):
        assert study._confidence_tier(1.2) == "PAPER"

    def test_f5_amplified_floor_is_one_point_zero(self):
        assert study._confidence_tier(1.0, f5_amplified=True) == "PAPER"
        assert study._confidence_tier(0.5, f5_amplified=True) is None


class TestBuildReportAgainstRealRepositoryData:
    """
    Runs the real script against this repo's actual data/pipeline/ and
    bets.json -- proving it executes cleanly end-to-end and never
    modifies either.
    """

    def test_report_has_the_required_sections(self):
        report = study.build_report()
        for key in (
            "reproduciblePipelineDates", "numberOfF5MarketsEvaluated",
            "averageOldTeamSideProbabilityInflationPercentagePoints",
            "averageTieProbabilityPercent", "approximateTierChanges",
            "placedBetsCrossReference", "settlementAndClvDataAvailability",
            "detail",
        ):
            assert key in report

    def test_never_modifies_bets_json(self):
        with open(study.BETS_PATH) as f:
            before = f.read()
        study.build_report()
        with open(study.BETS_PATH) as f:
            after = f.read()
        assert before == after

    def test_average_tie_probability_is_a_real_percentage_not_a_fraction(self):
        """Regression guard for a units bug found while building this
        script: tieProb (0-1 fraction) must be scaled to percent before
        averaging, not reported as a bare fraction mislabeled 'Percent'."""
        report = study.build_report()
        if report["numberOfF5MarketsEvaluated"] > 0:
            assert report["averageTieProbabilityPercent"] > 1.0

    def test_deterministic_across_repeated_runs(self):
        report1 = study.build_report()
        report2 = study.build_report()
        assert report1 == report2

    def test_placed_bets_cross_reference_accounts_for_every_f5_bet(self):
        report = study.build_report()
        ref = report["placedBetsCrossReference"]
        assert (ref["f5BetsWithinReproducibleProjectionWindow"]
                + ref["f5BetsNotReproducible_noPreservedProjectionInputs"]) == ref["totalF5PlacedBets"]

    def test_detail_rows_each_carry_the_no_fabrication_caveat_context(self):
        report = study.build_report()
        for row in report["detail"]:
            assert "legacyModelProbability_asRecorded" in row
            assert "correctedModelProbability" in row
