#!/usr/bin/env python3
"""
tests/test_bullpen_workload_pregame.py
===========================================
Coverage for incorporating PR #51's bullpen.recentUsage data into the
pregame full-game projection engine (scripts/build_market_ledger.py's
compute_projections()/compute_game_projection_context()), via the new
lib/edgelab/bullpen_availability.compute_bullpen_workload_adjustment().

Scope, matching the mission this milestone was scoped to:
  - full-game ML, Game_Total, TT_Away_Over/TT_Home_Over respond to
    recent bullpen workload/availability
  - F5 (starter-only in production, by construction) changes materially
    less than the full-game projection for the identical workload delta
  - missing/unavailable recentUsage never fabricates an adjustment
  - a rested bullpen never receives a bonus beyond its season xFIP

Season-long bullpen quality (bullpen.xFIP) is held CONSTANT across the
compared fixtures in every test below -- only the recentUsage block
differs -- so any observed movement is attributable to the new workload
signal, not to a season-quality change.
"""
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(ROOT))
sys.path.insert(0, os.path.join(os.path.dirname(ROOT), "lib"))
sys.path.insert(0, os.path.join(os.path.dirname(ROOT), "scripts"))

import build_market_ledger as bml
from test_lineup_gate import _make_game


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _light_usage(games_considered=2):
    """dataAvailable=True, everything well under every penalty threshold
    -- a genuinely rested bullpen with real (not missing) data."""
    return {
        "dataAvailable": True, "unavailableReason": None,
        "asOfDate": "2026-08-07", "gamesConsidered": games_considered,
        "relieversUsedLastGame": [],
        "backToBackRelievers": [],
        "recentPitchCounts": [
            {"playerId": "1", "name": "Reliever A", "totalPitches": 12, "appearances": 1},
        ],
        "highLeverageRecentUsage": [],
        "handednessMix": {"L": 1, "R": 0, "unknown": 0},
        "teamPitchCountLastGame": 12,
        "teamPitchCountWindow": 12,
    }


def _heavily_taxed_usage(games_considered=2):
    """dataAvailable=True, back-to-back relievers + a taxed high-leverage
    arm + heavy individual pitch counts + a heavy aggregate window --
    every one of the four required signals firing at once."""
    return {
        "dataAvailable": True, "unavailableReason": None,
        "asOfDate": "2026-08-07", "gamesConsidered": games_considered,
        "relieversUsedLastGame": [
            {"playerId": "1", "name": "Closer", "numberOfPitches": 28},
            {"playerId": "2", "name": "Setup", "numberOfPitches": 25},
        ],
        "backToBackRelievers": [
            {"playerId": "1", "name": "Closer"},
            {"playerId": "2", "name": "Setup"},
        ],
        "recentPitchCounts": [
            {"playerId": "1", "name": "Closer", "totalPitches": 50, "appearances": 2},
            {"playerId": "2", "name": "Setup", "totalPitches": 45, "appearances": 2},
            {"playerId": "3", "name": "Middle", "totalPitches": 40, "appearances": 2},
        ],
        "highLeverageRecentUsage": [
            {"playerId": "1", "name": "Closer", "saves": 2, "holds": 0, "totalPitches": 50},
        ],
        "handednessMix": {"L": 1, "R": 2, "unknown": 0},
        "teamPitchCountLastGame": 90,
        "teamPitchCountWindow": 210,
    }


def _unavailable_usage():
    return {
        "dataAvailable": False,
        "unavailableReason": "no_completed_games_in_window",
        "asOfDate": None, "gamesConsidered": 0,
        "relieversUsedLastGame": [], "backToBackRelievers": [],
        "recentPitchCounts": [], "highLeverageRecentUsage": [],
        "handednessMix": {"L": 0, "R": 0, "unknown": 0},
        "teamPitchCountLastGame": None, "teamPitchCountWindow": None,
    }


def _game_with_home_bullpen_usage(recent_usage_or_none, home_pen_xfip=3.8, away_pen_xfip=4.5):
    """
    Minimal fully-computable game (same shape as
    tests/test_build_market_ledger_projection_boundary.py's
    _fully_computable_game()) with a `recentUsage` block only on the
    HOME side's bullpen, so the away side is a stable baseline holding
    season xFIP constant across all fixtures in a given test.
    `recent_usage_or_none=None` means the recentUsage KEY is absent
    entirely (as opposed to present-but-dataAvailable=False).
    """
    home_bp = {"xFIP": home_pen_xfip}
    if recent_usage_or_none is not None:
        home_bp["recentUsage"] = recent_usage_or_none
    return {
        "away": {"abbr": "AWY", "pitcherSavant": {"xFIP": 3.6, "avgIPperStart": 6.0},
                  "bullpen": {"xFIP": away_pen_xfip}},
        "home": {"abbr": "HME", "pitcherSavant": {"xFIP": 3.9, "avgIPperStart": 6.0},
                  "bullpen": home_bp},
        "awayTeamStats": {"offenseBaselineAdj": 4.5},
        "homeTeamStats": {"offenseBaselineAdj": 4.5},
        "park": {"parkFactor": 100},
        "odds": {"kalshi": {}},
    }


# ── Heavily taxed bullpen worsens that team's full-game outlook ──────────────

class TestHeavilyTaxedBullpenWorsensFullGameOutlook:

    def test_home_full_game_win_prob_drops_when_home_bullpen_is_taxed(self):
        rested_game = _game_with_home_bullpen_usage(_light_usage())
        taxed_game = _game_with_home_bullpen_usage(_heavily_taxed_usage())

        away_r, home_r, _, _, missing_r = bml.compute_projections(rested_game)
        away_t, home_t, _, _, missing_t = bml.compute_projections(taxed_game)
        assert missing_r == [] and missing_t == []

        # A taxed HOME bullpen means the AWAY team scores more against it
        # (away_proj depends on home pitching/bullpen quality).
        assert away_t > away_r, "opponent's projected runs must rise when the home bullpen is taxed"

        p_home_win_rested, _ = bml.p_team_wins(home_r, away_r)
        p_home_win_taxed, _ = bml.p_team_wins(home_t, away_t)
        assert p_home_win_taxed < p_home_win_rested, (
            "home team's full-game win probability must worsen when its own bullpen is taxed"
        )

    def test_taxed_bullpen_debug_dict_shows_adjustment_applied(self):
        taxed_game = _game_with_home_bullpen_usage(_heavily_taxed_usage())
        ctx = bml.compute_game_projection_context(taxed_game)
        home_avail = ctx["homeBullpenAvailability"]
        assert home_avail["adjustmentApplied"] is True
        assert home_avail["multiplier"] > 1.0
        assert home_avail["components"]["backToBackCount"] == 2
        assert home_avail["components"]["taxedHighLeverageArmCount"] == 1


# ── Rested bullpen does not receive a fabricated bonus ────────────────────────

class TestRestedBullpenNoFabricatedBonus:

    def test_light_usage_projection_matches_no_recent_usage_at_all(self):
        """A REAL, present, dataAvailable=True 'lightly used' bullpen must
        project IDENTICALLY to a bullpen with no recentUsage block at
        all -- proving 'rested' never earns a discount below season xFIP."""
        no_data_game = _game_with_home_bullpen_usage(None)
        light_game = _game_with_home_bullpen_usage(_light_usage())

        proj_none = bml.compute_projections(no_data_game)
        proj_light = bml.compute_projections(light_game)
        assert proj_none[:4] == proj_light[:4], (
            "a rested-but-present bullpen profile must not project any better than "
            "having no recentUsage data at all"
        )

    def test_light_usage_multiplier_is_exactly_neutral(self):
        light_game = _game_with_home_bullpen_usage(_light_usage())
        ctx = bml.compute_game_projection_context(light_game)
        assert ctx["homeBullpenAvailability"]["multiplier"] == 1.0
        assert ctx["homeBullpenAvailability"]["adjustmentApplied"] is False


# ── Opponent team-total probability responds appropriately ───────────────────

class TestOpponentTeamTotalResponds:

    def test_away_team_total_model_prob_rises_when_home_bullpen_taxed(self):
        rested_game = _game_with_home_bullpen_usage(_light_usage())
        taxed_game = _game_with_home_bullpen_usage(_heavily_taxed_usage())

        away_r, _, _, _, _ = bml.compute_projections(rested_game)
        away_t, _, _, _, _ = bml.compute_projections(taxed_game)

        line = 4
        p_over_rested = bml.p_over_total(away_r, line)
        p_over_taxed = bml.p_over_total(away_t, line)
        assert p_over_taxed > p_over_rested, (
            "TT_Away_Over (the opponent's team total) must become MORE likely "
            "to clear the line when the home bullpen is taxed"
        )

    def test_own_team_total_unaffected_by_own_bullpen_workload(self):
        """TT_Home_Over is driven by home_proj, which depends on the
        AWAY side's pitching -- the home team's own bullpen workload
        must not move its own team total."""
        rested_game = _game_with_home_bullpen_usage(_light_usage())
        taxed_game = _game_with_home_bullpen_usage(_heavily_taxed_usage())

        _, home_r, _, _, _ = bml.compute_projections(rested_game)
        _, home_t, _, _, _ = bml.compute_projections(taxed_game)
        assert home_r == home_t


# ── Game total responds appropriately ─────────────────────────────────────────

class TestGameTotalResponds:

    def test_game_total_model_prob_rises_when_relief_availability_worsens(self):
        rested_game = _game_with_home_bullpen_usage(_light_usage())
        taxed_game = _game_with_home_bullpen_usage(_heavily_taxed_usage())

        ctx_r = bml.compute_game_projection_context(rested_game)
        ctx_t = bml.compute_game_projection_context(taxed_game)
        assert ctx_t["totalProj"] > ctx_r["totalProj"]

        line = 8
        p_over_rested = bml.p_over_total(ctx_r["totalProj"], line)
        p_over_taxed = bml.p_over_total(ctx_t["totalProj"], line)
        assert p_over_taxed > p_over_rested


# ── F5 changes materially less than full-game ML ──────────────────────────────

class TestF5ChangesMaterialLessThanFullGame:

    def test_f5_projections_are_completely_unchanged_by_bullpen_workload(self):
        """F5 is starter-only by construction in compute_projections() --
        it must be byte-identical regardless of bullpen workload, while
        the full-game projection moves."""
        rested_game = _game_with_home_bullpen_usage(_light_usage())
        taxed_game = _game_with_home_bullpen_usage(_heavily_taxed_usage())

        away_r, home_r, f5a_r, f5h_r, _ = bml.compute_projections(rested_game)
        away_t, home_t, f5a_t, f5h_t, _ = bml.compute_projections(taxed_game)

        assert f5a_r == f5a_t
        assert f5h_r == f5h_t
        assert away_r != away_t, "full-game projection must actually move for this test to be meaningful"

    def test_f5_win_prob_shift_is_materially_smaller_than_full_game(self):
        rested_game = _game_with_home_bullpen_usage(_light_usage())
        taxed_game = _game_with_home_bullpen_usage(_heavily_taxed_usage())

        away_r, home_r, f5a_r, f5h_r, _ = bml.compute_projections(rested_game)
        away_t, home_t, f5a_t, f5h_t, _ = bml.compute_projections(taxed_game)

        p_home_full_rested, _ = bml.p_team_wins(home_r, away_r)
        p_home_full_taxed, _ = bml.p_team_wins(home_t, away_t)
        full_game_shift = abs(p_home_full_rested - p_home_full_taxed)

        p_f5_home_rested, _ = bml.p_team_wins(f5h_r, f5a_r)
        p_f5_home_taxed, _ = bml.p_team_wins(f5h_t, f5a_t)
        f5_shift = abs(p_f5_home_rested - p_f5_home_taxed)

        assert f5_shift == 0.0
        assert full_game_shift > f5_shift


# ── Missing recentUsage causes no guessed adjustment ──────────────────────────

class TestMissingRecentUsageNoGuess:

    def test_absent_recent_usage_key_produces_neutral_multiplier(self):
        game = _game_with_home_bullpen_usage(None)
        ctx = bml.compute_game_projection_context(game)
        avail = ctx["homeBullpenAvailability"]
        assert avail["multiplier"] == 1.0
        assert avail["dataAvailable"] is False
        assert avail["adjustmentApplied"] is False

    def test_explicit_data_unavailable_produces_neutral_multiplier(self):
        game = _game_with_home_bullpen_usage(_unavailable_usage())
        ctx = bml.compute_game_projection_context(game)
        avail = ctx["homeBullpenAvailability"]
        assert avail["multiplier"] == 1.0
        assert avail["dataAvailable"] is False
        assert avail["unavailableReason"] == "no_completed_games_in_window"

    def test_missing_and_unavailable_project_identically_to_no_bullpen_key_at_all(self):
        no_key_game = _game_with_home_bullpen_usage(None)
        unavailable_game = _game_with_home_bullpen_usage(_unavailable_usage())

        proj_no_key = bml.compute_projections(no_key_game)
        proj_unavailable = bml.compute_projections(unavailable_game)
        assert proj_no_key[:4] == proj_unavailable[:4]


# ── End-to-end: debug output surfaces on real market rows ─────────────────────

class TestDebugOutputExposedOnLedgerRows:

    def _row(self, ledger, market):
        for r in ledger:
            if r["market"] == market:
                return r
        raise KeyError(market)

    def test_ml_and_team_total_and_game_total_rows_carry_bullpen_debug_fields(self):
        game = _make_game()
        game["home"]["bullpen"]["recentUsage"] = _heavily_taxed_usage()
        game["away"]["bullpen"]["recentUsage"] = _light_usage()
        # F5 three-way pricing requires a real tie price/ticker
        # (docs/F5_THREE_WAY_PRICING.md) -- _make_game()'s default f5ml
        # block only carries away/home, which routes F5 rows to Missing
        # Data (a pre-existing, bullpen-unrelated gate) before this
        # test's debug-field assertion ever runs.
        game["odds"]["kalshi"]["f5ml"]["tie_american"] = +260
        game["odds"]["kalshi"]["f5ml"]["tie_ticker"] = "KXMLBF5-26JUN101545AAAHH-TIE"

        ledger = bml.evaluate_game(game)
        for market in ("ML_Away", "ML_Home", "TT_Away_Over", "TT_Home_Over", "Game_Total",
                       "F5_ML_Away", "F5_ML_Home"):
            row = self._row(ledger, market)
            assert "awayBullpenAvailability" in row
            assert "homeBullpenAvailability" in row
            assert row["homeBullpenAvailability"]["adjustmentApplied"] is True
            assert row["awayBullpenAvailability"]["adjustmentApplied"] is False

    def test_row_debug_fields_absent_recent_usage_show_unavailable_not_rested(self):
        game = _make_game()
        # Neither side's bullpen carries a recentUsage block at all.
        ledger = bml.evaluate_game(game)
        row = self._row(ledger, "ML_Away")
        assert row["awayBullpenAvailability"]["dataAvailable"] is False
        assert row["homeBullpenAvailability"]["dataAvailable"] is False
        assert row["awayBullpenAvailability"]["multiplier"] == 1.0
        assert row["homeBullpenAvailability"]["multiplier"] == 1.0
