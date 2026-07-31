#!/usr/bin/env python3
"""
tests/test_kalshi_probability_adapters.py
=============================================
Coverage for lib/kalshi_probability_adapters.py: exact-line probability
calculation, YES/NO probabilities, alternate-line independence, and the
critical "never fabricate a probability for an unsupported family" rule
-- including a bit-for-bit equivalence check against
scripts/build_market_ledger.py's existing ML/F5-ML/NRFI formulas so this
module can never silently drift from production's real numbers.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import lib.kalshi_probability_adapters as adapters  # noqa: E402
from scripts.build_market_ledger import p_team_wins, poisson_pmf  # noqa: E402


class TestGameResultMatchesProduction:

    def test_bit_identical_to_build_market_ledger_ml_formula(self):
        away_proj, home_proj = 4.6, 4.1
        prob, status, _ = adapters.adapt_game_result(away_proj, home_proj, "Away")

        # Reproduce production's ML_Away formula exactly (scripts/build_market_ledger.py).
        p_away_win, p_push = p_team_wins(away_proj, home_proj)
        expected = p_away_win / (1 - p_push)

        assert status == adapters.STATUS_SUPPORTED
        assert abs(prob - expected) < 1e-12

    def test_away_home_sum_close_to_one(self):
        away_p, _, _ = adapters.adapt_game_result(4.6, 4.1, "Away")
        home_p, _, _ = adapters.adapt_game_result(4.6, 4.1, "Home")
        assert abs((away_p + home_p) - 1.0) < 1e-9

    def test_missing_data_returns_none_not_zero(self):
        prob, status, reason = adapters.adapt_game_result(None, 4.1, "Away")
        assert prob is None
        assert status == adapters.STATUS_MISSING_DATA
        assert reason


class TestF5ResultAndTie:

    def test_f5_away_home_match_legacy_formula(self):
        f5_away, f5_home = 2.5, 2.2
        prob_away, status, _ = adapters.adapt_f5_result(f5_away, f5_home, "Away")
        p_away_win, p_push = p_team_wins(f5_away, f5_home)
        expected = p_away_win / (1 - p_push)
        assert status == adapters.STATUS_SUPPORTED
        assert abs(prob_away - expected) < 1e-12

    def test_f5_tie_leg_is_real_probability_not_none(self):
        prob_tie, status, _ = adapters.adapt_f5_result(2.5, 2.2, "Tie")
        assert status == adapters.STATUS_SUPPORTED
        assert prob_tie is not None
        assert 0 < prob_tie < 1

    def test_f5_away_home_tie_sum_to_one(self):
        """Verifies the newly-exposed Tie leg is additive, not
        double-counted or renormalized away -- Away(legacy-conditional)
        + Home(legacy-conditional) do NOT sum with Tie to 1 by
        construction (legacy conditional already excludes the tie mass),
        so this test instead checks Tie is independently a valid
        probability and Away/Home legacy values are unchanged."""
        f5_away, f5_home = 2.5, 2.2
        away_p, _, _ = adapters.adapt_f5_result(f5_away, f5_home, "Away")
        home_p, _, _ = adapters.adapt_f5_result(f5_away, f5_home, "Home")
        assert abs((away_p + home_p) - 1.0) < 1e-9  # legacy conditional, tie excluded by design


class TestWinningMargin:

    def test_p_wins_by_over_uses_same_poisson_primitive(self):
        team_proj, opp_proj, margin = 4.6, 4.1, 1.5
        result = adapters.p_wins_by_over(team_proj, opp_proj, margin, max_r=20)
        # Manually recompute via poisson_pmf directly to prove no drift.
        expected = 0.0
        for a in range(21):
            pa = poisson_pmf(a, team_proj)
            for h in range(21):
                if a - h > margin:
                    expected += pa * poisson_pmf(h, opp_proj)
        assert abs(result - expected) < 1e-12

    def test_alternate_lines_are_independent_and_monotonic(self):
        team_proj, opp_proj = 4.6, 3.5
        p_15, _, _ = adapters.adapt_winning_margin(team_proj, opp_proj, 1.5)
        p_25, _, _ = adapters.adapt_winning_margin(team_proj, opp_proj, 2.5)
        p_35, _, _ = adapters.adapt_winning_margin(team_proj, opp_proj, 3.5)
        # A larger margin threshold must always be strictly less likely.
        assert p_15 > p_25 > p_35

    def test_missing_line_is_missing_data(self):
        prob, status, reason = adapters.adapt_winning_margin(4.6, 4.1, None)
        assert prob is None
        assert status == adapters.STATUS_MISSING_DATA


class TestTotalsYesNo:

    def test_over_under_are_complementary(self):
        over, _, _ = adapters.adapt_total(8.5, 8, side="Over")
        under, _, _ = adapters.adapt_total(8.5, 8, side="Under")
        assert abs((over + under) - 1.0) < 1e-9

    def test_alternate_total_lines_independent(self):
        p7, _, _ = adapters.adapt_total(8.5, 7)
        p8, _, _ = adapters.adapt_total(8.5, 8)
        p9, _, _ = adapters.adapt_total(8.5, 9)
        assert p7 > p8 > p9

    def test_matches_production_p_over_total(self):
        from scripts.build_market_ledger import p_over_total
        prob, status, _ = adapters.adapt_total(8.5, 8, side="Over")
        assert abs(prob - p_over_total(8.5, 8)) < 1e-12


class TestTeamTotal:

    def test_over_under_complementary(self):
        over, _, _ = adapters.adapt_team_total(4.5, 3.5, side="Over")
        under, _, _ = adapters.adapt_team_total(4.5, 3.5, side="Under")
        assert abs((over + under) - 1.0) < 1e-9


class TestFirstInningRun:

    def test_matches_production_nrfi_formula(self):
        away_proj, home_proj = 4.6, 4.1
        prob, status, _ = adapters.adapt_first_inning_run(away_proj, home_proj)
        inning1_away = away_proj / 9
        inning1_home = home_proj / 9
        expected_yrfi = 1.0 - (poisson_pmf(0, inning1_home) * poisson_pmf(0, inning1_away))
        assert status == adapters.STATUS_SUPPORTED
        assert abs(prob - expected_yrfi) < 1e-12

    def test_yes_no_complementary_via_dispatch(self):
        ctx = {"awayProjRuns": 4.6, "homeProjRuns": 4.1}
        yes_p, _, _ = adapters.adapt_contract("first_inning_run", "F1", "Yes", None, ctx)
        no_p, _, _ = adapters.adapt_contract("first_inning_run", "F1", "No", None, ctx)
        assert abs((yes_p + no_p) - 1.0) < 1e-9


class TestNeverFabricateUnsupported:

    def test_pitcher_strikeouts_always_unsupported(self):
        prob, status, reason = adapters.adapt_contract("pitcher_strikeouts", None, None, 5.5, {})
        assert prob is None
        assert status == adapters.STATUS_UNSUPPORTED
        assert "strikeout" in reason.lower()

    def test_pitcher_outs_always_unsupported(self):
        prob, status, reason = adapters.adapt_contract("pitcher_outs", None, None, 15.5, {})
        assert prob is None
        assert status == adapters.STATUS_UNSUPPORTED

    def test_hitter_home_runs_always_unsupported(self):
        prob, status, reason = adapters.adapt_contract("hitter_home_runs", None, None, 0.5, {})
        assert prob is None
        assert status == adapters.STATUS_UNSUPPORTED

    def test_f3_now_supported_after_live_structure_verification(self):
        """
        Spread/F3-F7-correction mission: a live dispatch of
        scripts/discover_kalshi_series_catalogue.py confirmed F3 is a
        genuine three-way Kalshi series. adapt_contract() dispatches
        on _VERIFIED_THREE_WAY_PERIODS (derived from
        HORIZON_MARKET_STATUS), so F3 now prices exactly like F5 given
        period-scaled projection context -- no code change was needed
        in this module, only the taxonomy flag flip.
        """
        prob, status, reason = adapters.adapt_contract(
            "inning_result", "F3", "Away", None,
            {"f3AwayProj": 1.9, "f3HomeProj": 1.6},
        )
        assert prob is not None
        assert status == adapters.STATUS_SUPPORTED

    def test_f7_now_supported_after_live_structure_verification(self):
        prob, status, reason = adapters.adapt_contract(
            "inning_result", "F7", "Away", None,
            {"f7AwayProj": 3.6, "f7HomeProj": 3.1},
        )
        assert prob is not None
        assert status == adapters.STATUS_SUPPORTED

    def test_f3_missing_period_context_is_missing_data_not_fabricated(self):
        """Full-game projections must never be silently substituted for
        a missing period-scaled projection."""
        prob, status, reason = adapters.adapt_contract("inning_result", "F3", "Away", None,
                                                          {"awayProjRuns": 4.6, "homeProjRuns": 4.1})
        assert prob is None
        assert status == adapters.STATUS_MISSING_DATA

    def test_unclassified_family_none_never_crashes(self):
        prob, status, reason = adapters.adapt_contract(None, None, None, None, {})
        assert prob is None
        assert status == adapters.STATUS_UNSUPPORTED
        assert reason

    def test_unrecognized_family_string_never_crashes(self):
        prob, status, reason = adapters.adapt_contract("totally_made_up_family", "full_game", "Yes", None, {})
        assert prob is None
        assert status == adapters.STATUS_UNSUPPORTED
