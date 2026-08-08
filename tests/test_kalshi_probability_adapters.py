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
from lib.research.three_way_projection import three_way_result_probs  # noqa: E402


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
    """
    F3/F5/F7 three-way-outcome mission: adapt_f5_result() previously
    priced Away/Home from the legacy two-way-renormalized formula
    (p_win / (1 - p_push), which sums Away+Home to 1 ON ITS OWN,
    excluding the tie) while pricing Tie separately from the raw joint
    distribution -- so a game's three sibling F5 contracts never
    actually summed to 100% fair probability. Away/Tie/Home are now all
    read from the SAME three_way_result_probs() call instead.
    """

    def test_f5_away_home_now_match_three_way_formula_not_legacy(self):
        f5_away, f5_home = 2.5, 2.2
        prob_away, status, _ = adapters.adapt_f5_result(f5_away, f5_home, "Away")
        expected = three_way_result_probs(f5_away, f5_home)["awayWinProb"]
        assert status == adapters.STATUS_SUPPORTED
        assert abs(prob_away - expected) < 1e-12

        # The OLD legacy-renormalized value is a materially different
        # number -- proves this is no longer silently the old formula.
        p_away_win, p_push = p_team_wins(f5_away, f5_home)
        legacy = p_away_win / (1 - p_push)
        assert abs(prob_away - legacy) > 1e-6

    def test_f5_tie_leg_is_real_probability_not_none(self):
        prob_tie, status, _ = adapters.adapt_f5_result(2.5, 2.2, "Tie")
        assert status == adapters.STATUS_SUPPORTED
        assert prob_tie is not None
        assert 0 < prob_tie < 1

    def test_f5_away_tie_home_sum_to_one(self):
        """Requirement: P(away lead) + P(tie) + P(home lead) == 1."""
        f5_away, f5_home = 2.5, 2.2
        away_p, _, _ = adapters.adapt_f5_result(f5_away, f5_home, "Away")
        tie_p, _, _ = adapters.adapt_f5_result(f5_away, f5_home, "Tie")
        home_p, _, _ = adapters.adapt_f5_result(f5_away, f5_home, "Home")
        assert abs((away_p + tie_p + home_p) - 1.0) < 1e-9

    def test_f5_correct_side_mapping_favorite_vs_underdog(self):
        """The heavier-projected side must get the larger lead probability, and it's the AWAY/HOME label that must map correctly, not swapped."""
        f5_away, f5_home = 3.4, 1.6  # away is the clear favorite
        away_p, _, _ = adapters.adapt_f5_result(f5_away, f5_home, "Away")
        home_p, _, _ = adapters.adapt_f5_result(f5_away, f5_home, "Home")
        assert away_p > home_p

        # Flipping which side is favored must flip which leg is larger.
        away_p2, _, _ = adapters.adapt_f5_result(f5_home, f5_away, "Away")
        home_p2, _, _ = adapters.adapt_f5_result(f5_home, f5_away, "Home")
        assert home_p2 > away_p2
        assert abs(away_p - home_p2) < 1e-12  # symmetric under swapping proj+label together

    def test_f5_missing_projection_is_missing_data_for_every_side(self):
        for side in ("Away", "Tie", "Home"):
            prob, status, reason = adapters.adapt_f5_result(None, 2.2, side)
            assert prob is None
            assert status == adapters.STATUS_MISSING_DATA
            assert reason


class TestF3AndF5ThreeWayViaDispatch:
    """
    F3/F5/F7 three-way-outcome mission, requirement 6: F3 and F5 must be
    verified INDEPENDENTLY through the real adapt_contract() dispatch
    path (not just the shared adapt_f5_result() helper directly), since
    that's what scripts/discover_kalshi_mlb_markets.py actually calls
    per contract -- each of a game's Away/Tie/Home tickers is priced as
    a separate call, so the sum-to-one guarantee has to hold ACROSS
    three independent adapt_contract() calls, not just within one.
    """

    def _three_legs(self, period, ctx):
        away, _, _ = adapters.adapt_contract("inning_result", period, "Away", None, ctx)
        tie, _, _ = adapters.adapt_contract("inning_result", period, "Tie", None, ctx)
        home, _, _ = adapters.adapt_contract("inning_result", period, "Home", None, ctx)
        return away, tie, home

    def test_f3_away_tie_home_sum_to_one_and_tie_nonzero(self):
        away, tie, home = self._three_legs("F3", {"f3AwayProj": 1.9, "f3HomeProj": 1.6})
        assert abs((away + tie + home) - 1.0) < 1e-9
        assert tie > 0

    def test_f5_away_tie_home_sum_to_one_and_tie_nonzero(self):
        away, tie, home = self._three_legs("F5", {"f5AwayProj": 2.5, "f5HomeProj": 2.2})
        assert abs((away + tie + home) - 1.0) < 1e-9
        assert tie > 0

    def test_f3_and_f5_are_independent_not_cross_contaminated(self):
        """A game's F3 legs and F5 legs must come from their own
        period-scaled projections, never from each other's context key."""
        ctx = {"f3AwayProj": 1.9, "f3HomeProj": 1.6, "f5AwayProj": 2.5, "f5HomeProj": 2.2}
        f3_away, f3_tie, f3_home = self._three_legs("F3", ctx)
        f5_away, f5_tie, f5_home = self._three_legs("F5", ctx)
        assert (f3_away, f3_tie, f3_home) != (f5_away, f5_tie, f5_home)
        assert abs((f3_away + f3_tie + f3_home) - 1.0) < 1e-9
        assert abs((f5_away + f5_tie + f5_home) - 1.0) < 1e-9

    def test_f3_correct_side_mapping_matches_favorite(self):
        """The confirmed-favorite side's contract must carry the larger lead probability -- proves Away/Home aren't swapped in the F3 dispatch path."""
        away, tie, home = self._three_legs("F3", {"f3AwayProj": 2.6, "f3HomeProj": 1.1})
        assert away > home > 0
        assert abs((away + tie + home) - 1.0) < 1e-9


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


class TestPitcherWorkloadJoint:
    """
    Pitcher workload/K/outs joint-modeling mission: pitcher_strikeouts
    and pitcher_outs are now priced through
    lib.research.pitcher_workload_projection.project_pitcher_workload(),
    both sharing the SAME ctx-resolved workload inputs -- see that
    module's own regression suite (tests/research/
    test_pitcher_workload_projection.py) for the underlying math; these
    tests only cover the adapter/dispatch wiring.
    """

    def _ctx(self, **overrides):
        ctx = {"pitcherAvgIPperStart": 6.0, "pitcherKPct": 22.0, "pitcherBBPct": 8.5}
        ctx.update(overrides)
        return ctx

    def test_strikeouts_supported_with_full_workload_context(self):
        prob, status, reason = adapters.adapt_pitcher_strikeouts(self._ctx(), 6)
        assert status == adapters.STATUS_SUPPORTED
        assert 0.0 < prob < 1.0

    def test_outs_supported_with_full_workload_context(self):
        prob, status, reason = adapters.adapt_pitcher_outs(self._ctx(), 19)
        assert status == adapters.STATUS_SUPPORTED
        assert 0.0 < prob < 1.0

    def test_reachable_via_adapt_contract_dispatch(self):
        prob, status, _ = adapters.adapt_contract("pitcher_strikeouts", "full_game", "Yes", 6, self._ctx())
        assert status == adapters.STATUS_SUPPORTED
        assert 0.0 < prob < 1.0
        prob, status, _ = adapters.adapt_contract("pitcher_outs", "full_game", "Yes", 19, self._ctx())
        assert status == adapters.STATUS_SUPPORTED
        assert 0.0 < prob < 1.0

    def test_yes_no_complementary_for_both_families(self):
        for family, threshold, adapter in (
            ("pitcher_strikeouts", 6, adapters.adapt_pitcher_strikeouts),
            ("pitcher_outs", 19, adapters.adapt_pitcher_outs),
        ):
            yes_p, _, _ = adapter(self._ctx(), threshold, side="Yes")
            no_p, _, _ = adapter(self._ctx(), threshold, side="No")
            assert abs((yes_p + no_p) - 1.0) < 1e-9, family

    def test_missing_avg_ip_is_missing_data_not_unsupported(self):
        """Never conflate 'no model exists' (UNSUPPORTED) with 'this row just didn't carry the workload fields' (MISSING_DATA) -- the same distinction lib.edgelab.recommendations.classify_ticker_resolution draws elsewhere in this codebase."""
        ctx = self._ctx(pitcherAvgIPperStart=None)
        prob, status, reason = adapters.adapt_pitcher_strikeouts(ctx, 6)
        assert prob is None
        assert status == adapters.STATUS_MISSING_DATA
        assert reason

    def test_missing_threshold_is_missing_data(self):
        prob, status, reason = adapters.adapt_pitcher_outs(self._ctx(), None)
        assert prob is None
        assert status == adapters.STATUS_MISSING_DATA

    def test_strikeouts_and_outs_react_together_to_the_same_workload_change(self):
        """The adapter layer must preserve the underlying model's joint-response property, not just the pure module."""
        baseline_k, _, _ = adapters.adapt_pitcher_strikeouts(self._ctx(), 6)
        baseline_outs, _, _ = adapters.adapt_pitcher_outs(self._ctx(), 19)

        worse_ctx = self._ctx(pitcherOpenerRole=True)
        worse_k, _, _ = adapters.adapt_pitcher_strikeouts(worse_ctx, 6)
        worse_outs, _, _ = adapters.adapt_pitcher_outs(worse_ctx, 19)

        assert worse_k < baseline_k
        assert worse_outs < baseline_outs

    def test_opponent_k_rate_reaches_the_adapter_when_supplied(self):
        vs_average, _, _ = adapters.adapt_pitcher_strikeouts(self._ctx(), 6)
        vs_high_k_lineup, _, _ = adapters.adapt_pitcher_strikeouts(self._ctx(opponentTeamKPct=28.0), 6)
        assert vs_high_k_lineup > vs_average


class TestNeverFabricateUnsupported:

    def test_pitcher_strikeouts_missing_data_without_workload_context(self):
        """
        Pitcher workload/K/outs joint-modeling mission: pitcher_strikeouts
        is no longer permanently UNSUPPORTED (see TestPitcherWorkloadJoint
        below for the now-supported case) -- an empty projection_context
        correctly reports MISSING_DATA (the caller never supplied a
        pitcher's workload fields), not a claim that no model exists.
        """
        prob, status, reason = adapters.adapt_contract("pitcher_strikeouts", None, "Yes", 5.5, {})
        assert prob is None
        assert status == adapters.STATUS_MISSING_DATA

    def test_pitcher_outs_missing_data_without_workload_context(self):
        prob, status, reason = adapters.adapt_contract("pitcher_outs", None, "Yes", 15.5, {})
        assert prob is None
        assert status == adapters.STATUS_MISSING_DATA

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
