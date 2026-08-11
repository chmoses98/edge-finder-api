#!/usr/bin/env python3
"""
tests/test_first_inning_context.py
=======================================
Unit tests for lib/research/first_inning_context.py -- NRFI/YRFI
first-inning-specific projection context (Baseball Input Data /
Platoon Context mission).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.research.first_inning_context import (
    build_first_inning_context,
    MIN_APPEARANCES_THIN,
    MIN_APPEARANCES_ADEQUATE,
    FIRST_INNING_ADJ_CAP_FRACTION,
)
from lib.research.platoon_context import STATUS_OK


def _game(away_fi=None, home_fi=None):
    return {
        "away": {"pitcherSavant": {"firstInningSplit": away_fi} if away_fi is not None else {}},
        "home": {"pitcherSavant": {"firstInningSplit": home_fi} if home_fi is not None else {}},
    }


class TestNoDedicatedEvidenceIsRegressionSafe:
    def test_identical_to_naive_when_no_evidence(self):
        g = _game()
        ctx = build_first_inning_context(g, away_proj_runs=4.5, home_proj_runs=4.0)
        assert ctx["awayLambda1st"] == 4.5 / 9.0
        assert ctx["homeLambda1st"] == 4.0 / 9.0
        assert ctx["dedicatedEvidenceApplied"] is False
        assert "away.pitcherSavant.firstInningSplit.firstInningXERA" in ctx["missing"]
        assert "home.pitcherSavant.firstInningSplit.firstInningXERA" in ctx["missing"]

    def test_none_proj_runs_yields_none_lambda(self):
        ctx = build_first_inning_context(_game(), away_proj_runs=None, home_proj_runs=None)
        assert ctx["awayLambda1st"] is None
        assert ctx["homeLambda1st"] is None

    def test_never_raises_on_empty_game(self):
        ctx = build_first_inning_context({}, away_proj_runs=4.5, home_proj_runs=4.0)
        assert ctx["dedicatedEvidenceApplied"] is False


class TestDedicatedEvidenceDirectionality:
    def test_weak_home_starter_first_inning_raises_away_lambda(self):
        """
        away's 1st-inning lambda is driven by the HOME starter's
        dedicated evidence (away bats first, facing the home starter).
        A weak (high xERA) home starter should raise away's lambda
        above the naive proxy; a strong one should lower it.
        """
        naive = 4.5 / 9.0
        g_weak = _game(home_fi={"firstInningXERA": 7.0, "appearances": 10})
        g_strong = _game(home_fi={"firstInningXERA": 1.5, "appearances": 10})

        ctx_weak = build_first_inning_context(g_weak, away_proj_runs=4.5, home_proj_runs=4.5)
        ctx_strong = build_first_inning_context(g_strong, away_proj_runs=4.5, home_proj_runs=4.5)

        assert ctx_weak["awayLambda1st"] > naive
        assert ctx_strong["awayLambda1st"] < naive
        assert ctx_weak["dedicatedEvidenceApplied"] is True

    def test_away_starter_evidence_drives_home_lambda(self):
        naive = 4.5 / 9.0
        g = _game(away_fi={"firstInningXERA": 7.0, "appearances": 10})
        ctx = build_first_inning_context(g, away_proj_runs=4.5, home_proj_runs=4.5)
        assert ctx["homeLambda1st"] > naive
        # away lambda untouched by away-starter evidence (that's home's own evidence)
        assert ctx["awayLambda1st"] == naive


class TestSampleTiers:
    def test_thin_sample_applies_smaller_weight_than_adequate(self):
        naive = 4.5 / 9.0
        g_thin = _game(home_fi={"firstInningXERA": 7.0, "appearances": MIN_APPEARANCES_THIN})
        g_adequate = _game(home_fi={"firstInningXERA": 7.0, "appearances": MIN_APPEARANCES_ADEQUATE})

        ctx_thin = build_first_inning_context(g_thin, away_proj_runs=4.5, home_proj_runs=4.5)
        ctx_adequate = build_first_inning_context(g_adequate, away_proj_runs=4.5, home_proj_runs=4.5)

        thin_move = ctx_thin["awayLambda1st"] - naive
        adequate_move = ctx_adequate["awayLambda1st"] - naive
        assert 0 < thin_move < adequate_move

    def test_below_thin_floor_is_not_applied(self):
        naive = 4.5 / 9.0
        g = _game(home_fi={"firstInningXERA": 7.0, "appearances": MIN_APPEARANCES_THIN - 1})
        ctx = build_first_inning_context(g, away_proj_runs=4.5, home_proj_runs=4.5)
        assert ctx["awayLambda1st"] == naive
        assert ctx["dedicatedEvidenceApplied"] is False


class TestBoundedAdjustment:
    def test_extreme_xera_still_capped(self):
        naive = 4.5 / 9.0
        g = _game(home_fi={"firstInningXERA": 20.0, "appearances": 20})
        ctx = build_first_inning_context(g, away_proj_runs=4.5, home_proj_runs=4.5)
        hi = naive * (1 + FIRST_INNING_ADJ_CAP_FRACTION)
        assert ctx["awayLambda1st"] <= hi + 1e-9

    def test_extreme_low_xera_still_capped(self):
        naive = 4.5 / 9.0
        g = _game(home_fi={"firstInningXERA": 0.0, "appearances": 20})
        ctx = build_first_inning_context(g, away_proj_runs=4.5, home_proj_runs=4.5)
        lo = naive * (1 - FIRST_INNING_ADJ_CAP_FRACTION)
        assert ctx["awayLambda1st"] >= lo - 1e-9


class TestPlatoonNudge:
    def test_favorable_platoon_context_nudges_lambda_up(self):
        naive = 4.5 / 9.0
        favorable_platoon = {"status": STATUS_OK, "aggregatePlatoonAdvantageRPG": 0.10}
        unfavorable_platoon = {"status": STATUS_OK, "aggregatePlatoonAdvantageRPG": -0.10}

        ctx_fav = build_first_inning_context(
            _game(), away_proj_runs=4.5, home_proj_runs=4.5,
            away_platoon_ctx=favorable_platoon,
        )
        ctx_unfav = build_first_inning_context(
            _game(), away_proj_runs=4.5, home_proj_runs=4.5,
            away_platoon_ctx=unfavorable_platoon,
        )
        assert ctx_fav["awayLambda1st"] > naive > ctx_unfav["awayLambda1st"]

    def test_non_ok_platoon_context_is_not_applied(self):
        naive = 4.5 / 9.0
        g = _game()
        ctx = build_first_inning_context(
            g, away_proj_runs=4.5, home_proj_runs=4.5,
            away_platoon_ctx={"status": "LINEUP_UNCONFIRMED", "aggregatePlatoonAdvantageRPG": 0.0},
        )
        assert ctx["awayLambda1st"] == naive


class TestOutputShape:
    def test_available_and_missing_lists_populated(self):
        g = _game(away_fi={"firstInningXERA": 3.0, "appearances": 10})
        ctx = build_first_inning_context(g, away_proj_runs=4.5, home_proj_runs=4.5)
        assert "awayStarterFirstInningXERA" in ctx["available"]
        assert "home.pitcherSavant.firstInningSplit.firstInningXERA" in ctx["missing"]
        assert isinstance(ctx["genericFallbacksUsed"], list)
        assert "sampleThresholds" in ctx
