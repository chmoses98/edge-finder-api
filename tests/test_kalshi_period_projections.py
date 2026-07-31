#!/usr/bin/env python3
"""
tests/test_kalshi_period_projections.py
============================================
Coverage for lib/kalshi_period_projections.py -- F3/F7 period-scaled
projections generalizing production's F5 formula structure.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from lib.kalshi_period_projections import (  # noqa: E402
    compute_period_projection, compute_period_projection_context, HORIZON_INNINGS,
)


def make_game(away_baseline=4.5, home_baseline=4.5, away_xfip=4.0, home_xfip=4.0,
              away_ip=6.0, home_ip=6.0, park_factor=100):
    return {
        "awayTeamStats": {"offenseBaselineAdj": away_baseline},
        "homeTeamStats": {"offenseBaselineAdj": home_baseline},
        "away": {"pitcherSavant": {"xFIP": away_xfip, "avgIPperStart": away_ip}},
        "home": {"pitcherSavant": {"xFIP": home_xfip, "avgIPperStart": home_ip}},
        "park": {"parkFactor": park_factor},
    }


class TestComputePeriodProjection:

    def test_missing_offense_baseline_returns_none_with_missing_fields(self):
        g = make_game()
        del g["awayTeamStats"]["offenseBaselineAdj"]
        away, home, missing = compute_period_projection(g, 3)
        assert away is None and home is None
        assert "awayTeamStats.offenseBaselineAdj" in missing

    def test_missing_xfip_returns_none(self):
        g = make_game()
        del g["away"]["pitcherSavant"]["xFIP"]
        away, home, missing = compute_period_projection(g, 3)
        assert away is None and home is None
        assert "away.pitcherSavant.xFIP" in missing

    def test_f3_projection_smaller_than_f7_for_same_inputs(self):
        g = make_game()
        f3_away, f3_home, _ = compute_period_projection(g, 3)
        f7_away, f7_home, _ = compute_period_projection(g, 7)
        assert f3_away < f7_away
        assert f3_home < f7_home

    def test_never_fabricates_zero_for_missing_data(self):
        g = make_game()
        del g["homeTeamStats"]["offenseBaselineAdj"]
        away, home, missing = compute_period_projection(g, 3)
        assert away is None  # never 0.0
        assert home is None

    def test_no_clamp_applied_unlike_production_f5(self):
        """
        Production's F5 clamps to [1.2, 4.1] -- that bound is tuned for
        a 5-inning horizon and must NOT be silently reused for F3/F7.
        An extreme offense/pitching mismatch should be able to exceed
        those bounds here.
        """
        g = make_game(away_baseline=9.0, home_xfip=5.5, home_ip=9.0)
        away, home, missing = compute_period_projection(g, 7)
        assert not missing
        assert away > 4.1 or away < 1.2 or True  # documents no clamp; primary proof is formula below
        # Direct formula check: no min/max applied post-computation.
        away_off_factor = 9.0 / 4.5
        home_xfip_clamped = 5.5
        park_adj = 0.0
        expected = away_off_factor * (7.0 * home_xfip_clamped / 9 * 1.0) + park_adj * (7 / 9)
        assert abs(away - round(expected, 3)) < 1e-6


class TestComputePeriodProjectionContext:

    def test_f3_context_shape(self):
        g = make_game()
        ctx = compute_period_projection_context(g, "F3")
        assert set(ctx.keys()) == {"awayProj", "homeProj", "totalProj", "missingFields"}
        assert ctx["totalProj"] == round(ctx["awayProj"] + ctx["homeProj"], 3)

    def test_f7_context_shape(self):
        g = make_game()
        ctx = compute_period_projection_context(g, "F7")
        assert ctx["awayProj"] is not None
        assert ctx["homeProj"] is not None

    def test_rejects_f5_and_full_game_scopes(self):
        """
        F5 already has a production projection
        (compute_game_projection_context) -- this module must not offer
        a second, independently-computed F5 number that could disagree.
        """
        g = make_game()
        try:
            compute_period_projection_context(g, "F5")
            assert False, "expected ValueError"
        except ValueError:
            pass
        try:
            compute_period_projection_context(g, "full_game")
            assert False, "expected ValueError"
        except ValueError:
            pass

    def test_missing_data_propagates_totalproj_none(self):
        g = make_game()
        del g["away"]["pitcherSavant"]["xFIP"]
        ctx = compute_period_projection_context(g, "F3")
        assert ctx["awayProj"] is None
        assert ctx["totalProj"] is None
        assert ctx["missingFields"]

    def test_horizon_innings_constant(self):
        assert HORIZON_INNINGS == {"F3": 3, "F5": 5, "F7": 7, "full_game": 9}
