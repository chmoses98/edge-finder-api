#!/usr/bin/env python3
"""
tests/research/test_inning_result_settlement.py
=====================================================
Model Performance Phase 2A Part 14 -- fixture tests for
lib/research/inning_result_settlement.py, covering the mission's
required scenario matrix: Away/Tie/Home outcomes, suspended-before-5,
suspended-after-5, postponed, cancelled, shortened-after-5,
doubleheader game-number collision, resumed game, missing official
inning score.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from lib.research.inning_result_settlement import (
    settle_f5_result,
    settle_inning_result,
    SETTLEMENT_AWAY,
    SETTLEMENT_TIE,
    SETTLEMENT_HOME,
    SETTLEMENT_UNRESOLVED,
)


class TestF5AwayHomeTieOutcomes:

    def test_away_leads_settles_away(self):
        result, reason = settle_f5_result(3, 1, completed_innings=5, game_status="Final")
        assert result == SETTLEMENT_AWAY
        assert reason is None

    def test_home_leads_settles_home(self):
        result, reason = settle_f5_result(1, 4, completed_innings=5, game_status="Final")
        assert result == SETTLEMENT_HOME
        assert reason is None

    def test_tie_settles_tie_not_push(self):
        result, reason = settle_f5_result(2, 2, completed_innings=5, game_status="Final")
        assert result == SETTLEMENT_TIE
        assert reason is None
        assert result != "PUSH"


class TestSuspendedGames:

    def test_suspended_before_5_innings_unresolved(self):
        result, reason = settle_f5_result(1, 1, completed_innings=3, game_status="Suspended")
        assert result == SETTLEMENT_UNRESOLVED
        assert reason == "fewer_than_5_complete_innings"

    def test_suspended_after_5_innings_settleable(self):
        """A game suspended AFTER inning 5 still has a determinate F5
        result -- the suspension affects the full game, not F5."""
        result, reason = settle_f5_result(2, 2, completed_innings=6, game_status="Suspended")
        assert result == SETTLEMENT_TIE
        assert reason is None


class TestPostponedCancelled:

    def test_postponed_game_unresolved(self):
        result, reason = settle_f5_result(0, 0, completed_innings=0, game_status="Postponed")
        assert result == SETTLEMENT_UNRESOLVED
        assert "not_settleable" in reason or "fewer_than_5" in reason

    def test_cancelled_game_unresolved(self):
        result, reason = settle_f5_result(None, None, completed_innings=0, game_status="Cancelled")
        assert result == SETTLEMENT_UNRESOLVED


class TestShortenedGame:

    def test_shortened_after_5_settleable(self):
        """A rain-shortened game called official at 6 innings still has
        a determinate F5 result."""
        result, reason = settle_f5_result(4, 2, completed_innings=6, game_status="Final")
        assert result == SETTLEMENT_AWAY
        assert reason is None


class TestDoubleheaderIdentityCollision:

    def test_two_games_same_teams_same_day_settle_independently(self):
        """Doubleheader game-number identity is an upstream concern
        (stable gameId construction) -- this settlement function must
        not conflate two independent score inputs regardless of
        identity collision risk elsewhere."""
        game1_result, _ = settle_f5_result(3, 1, completed_innings=5, game_status="Final")
        game2_result, _ = settle_f5_result(1, 1, completed_innings=5, game_status="Final")
        assert game1_result == SETTLEMENT_AWAY
        assert game2_result == SETTLEMENT_TIE


class TestResumedGame:

    def test_resumed_and_completed_game_settles_normally(self):
        """A previously-suspended game that resumed and reached Final
        with 5+ complete innings settles the same as any Final game."""
        result, reason = settle_f5_result(5, 3, completed_innings=9, game_status="Final")
        assert result == SETTLEMENT_AWAY
        assert reason is None


class TestMissingOfficialScore:

    def test_missing_scores_unresolved(self):
        result, reason = settle_f5_result(None, None, completed_innings=5, game_status="Final")
        assert result == SETTLEMENT_UNRESOLVED
        assert reason == "missing_official_f5_score"

    def test_partial_missing_score_unresolved(self):
        result, reason = settle_f5_result(3, None, completed_innings=5, game_status="Final")
        assert result == SETTLEMENT_UNRESOLVED


class TestF3F7NeverSettled:
    """Model Performance Phase 2A -- F3/F7 must NEVER be settled by
    this module, since their outcome structure is unverified."""

    def test_f3_now_settles_after_live_structure_verification(self):
        """
        Spread/F3-F7-correction mission: a live dispatch of
        scripts/discover_kalshi_series_catalogue.py against the real
        Kalshi exchange confirmed F3/F7 are genuine three-way series
        (see lib.research.market_taxonomy.HORIZON_MARKET_STATUS
        docstring for the exact evidence). settle_inning_result() is
        parametric on that verified-structure flag, so F3 now settles
        exactly like F5 -- no code change was needed here, only the
        taxonomy flag flip.
        """
        result, reason = settle_inning_result("F3", 3, 1, completed_innings=3, game_status="Final")
        assert result == SETTLEMENT_AWAY
        assert reason is None

    def test_f7_now_settles_after_live_structure_verification(self):
        result, reason = settle_inning_result("F7", 1, 4, completed_innings=7, game_status="Final")
        assert result == SETTLEMENT_HOME
        assert reason is None

    def test_f5_dispatches_to_real_settlement(self):
        result, reason = settle_inning_result("F5", 3, 1, completed_innings=5, game_status="Final")
        assert result == SETTLEMENT_AWAY
        assert reason is None


class TestDeterminism:

    def test_settle_f5_result_deterministic(self):
        r1 = settle_f5_result(2, 2, completed_innings=5, game_status="Final")
        r2 = settle_f5_result(2, 2, completed_innings=5, game_status="Final")
        assert r1 == r2
