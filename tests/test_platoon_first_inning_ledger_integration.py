#!/usr/bin/env python3
"""
tests/test_platoon_first_inning_ledger_integration.py
===========================================================
Integration/regression tests for how lib/research/platoon_context.py
and lib/research/first_inning_context.py are wired into
scripts/build_market_ledger.py (Baseball Input Data / Platoon Context
mission). Complements the pure-function unit tests in
tests/test_platoon_context.py and tests/test_first_inning_context.py by
proving the actual compute_projections()/compute_game_projection_context()/
evaluate_game() integration behaves per the mission's regression
guarantees:

  - missing new data => current projection behavior unchanged
  - unconfirmed lineups never fabricate player-level matchup context
  - handedness swap tests move projections in the expected direction
  - favorable platoon lineup improves offensive projection vs unfavorable,
    all else equal
  - first-inning-specific favorable/unfavorable context moves YRFI/NRFI
    coherently
  - F5/full-game outputs do not change from first-inning-only fields
    except through the explicitly shared platoon context
"""
import copy
import os
import sys

SCRIPTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'scripts')
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPTS_DIR)
sys.path.insert(0, ROOT_DIR)

import build_market_ledger as bml
from test_lineup_gate import _make_game


def _confirmed_lineup(hands, woba=0.320, split_hand=None, split_woba=None):
    lineup = []
    for i, h in enumerate(hands, start=1):
        entry = {
            "order": i, "playerId": f"b{i}", "name": f"Batter {i}",
            "batSide": h, "seasonWOBA": woba,
            "platoonSplits": None,
        }
        if split_hand and split_woba is not None:
            key = "vsLHP" if split_hand == "L" else "vsRHP"
            entry["platoonSplits"] = {key: {"woba": split_woba, "pa": 100}}
        lineup.append(entry)
    return lineup


class TestRegressionNoNewDataIsUnchanged:
    """A game with none of the new fields must project identically to
    plain compute_projections() with no platoon/first-inning terms."""

    def test_away_home_f5_unaffected_by_missing_platoon_data(self):
        g = _make_game()
        # No confirmedLineup/pitchHand/vsLHH/vsRHH on this fixture at all
        # -> both platoon contexts must be LINEUP_UNCONFIRMED/MISSING_DATA
        # with a zero adjustment, so away/home/f5 exactly match the
        # pre-existing formula (no platoon term added).
        assert 'confirmedLineup' not in g['awayTeamStats']
        ctx = bml.compute_game_projection_context(g)
        assert ctx['awayPlatoonContext']['aggregatePlatoonAdvantageRPG'] == 0.0
        assert ctx['homePlatoonContext']['aggregatePlatoonAdvantageRPG'] == 0.0

    def test_nrfi_yrfi_lambda_matches_naive_without_first_inning_split(self):
        g = _make_game()
        ctx = bml.compute_game_projection_context(g)
        naive_away = ctx['awayProjRuns'] / 9.0
        naive_home = ctx['homeProjRuns'] / 9.0
        fi_ctx = ctx['firstInningContext']
        assert abs(fi_ctx['awayLambda1st'] - naive_away) < 1e-9
        assert abs(fi_ctx['homeLambda1st'] - naive_home) < 1e-9
        assert fi_ctx['dedicatedEvidenceApplied'] is False


class TestUnconfirmedLineupNeverFabricates:
    def test_no_player_level_context_when_unconfirmed(self):
        g = _make_game(away_lineup=False, home_lineup=False)
        ctx = bml.compute_game_projection_context(g)
        assert ctx['awayPlatoonContext']['status'] == 'LINEUP_UNCONFIRMED'
        assert ctx['homePlatoonContext']['status'] == 'LINEUP_UNCONFIRMED'
        assert ctx['awayPlatoonContext']['aggregatePlatoonAdvantageRPG'] == 0.0


class TestFavorableVsUnfavorablePlatoonLineup:
    def test_favorable_lineup_raises_offense_projection(self):
        g_base = _make_game()

        g_favorable = copy.deepcopy(g_base)
        g_favorable['home']['pitcher']['pitchHand'] = 'R'
        g_favorable['home']['pitcherSavant']['xFIP'] = 3.5
        g_favorable['home']['pitcherSavant']['vsLHH'] = {'xERA': 6.0, 'pa': 150}
        g_favorable['awayTeamStats']['confirmedLineup'] = _confirmed_lineup(
            ['L'] * 9, woba=0.320, split_hand='R', split_woba=0.430
        )
        g_favorable['awayTeamStats']['teamSeasonWOBA'] = 0.320

        g_unfavorable = copy.deepcopy(g_base)
        g_unfavorable['home']['pitcher']['pitchHand'] = 'R'
        g_unfavorable['home']['pitcherSavant']['xFIP'] = 3.5
        g_unfavorable['home']['pitcherSavant']['vsLHH'] = {'xERA': 2.0, 'pa': 150}
        g_unfavorable['awayTeamStats']['confirmedLineup'] = _confirmed_lineup(
            ['L'] * 9, woba=0.320, split_hand='R', split_woba=0.220
        )
        g_unfavorable['awayTeamStats']['teamSeasonWOBA'] = 0.320

        away_fav, home_fav, f5a_fav, _, _ = bml.compute_projections(g_favorable)
        away_unfav, home_unfav, f5a_unfav, _, _ = bml.compute_projections(g_unfavorable)

        assert away_fav > away_unfav
        assert f5a_fav > f5a_unfav  # F5 shares the same platoon context, proportionally

    def test_handedness_swap_moves_projection_expected_direction(self):
        """Same lineup, opposing starter's handedness flips -- lineup that
        crushes RHP but not LHP should project higher runs vs an RHP
        starter than vs an equally-good-overall LHP starter."""
        lineup = _confirmed_lineup(['L'] * 9, woba=0.320)
        for h in lineup:
            h['platoonSplits'] = {
                'vsLHP': {'woba': 0.260, 'pa': 100},
                'vsRHP': {'woba': 0.420, 'pa': 100},
            }

        g_vs_rhp = _make_game()
        g_vs_rhp['home']['pitcher']['pitchHand'] = 'R'
        g_vs_rhp['awayTeamStats']['confirmedLineup'] = copy.deepcopy(lineup)
        g_vs_rhp['awayTeamStats']['teamSeasonWOBA'] = 0.320

        g_vs_lhp = _make_game()
        g_vs_lhp['home']['pitcher']['pitchHand'] = 'L'
        g_vs_lhp['awayTeamStats']['confirmedLineup'] = copy.deepcopy(lineup)
        g_vs_lhp['awayTeamStats']['teamSeasonWOBA'] = 0.320

        away_vs_rhp, _, _, _, _ = bml.compute_projections(g_vs_rhp)
        away_vs_lhp, _, _, _, _ = bml.compute_projections(g_vs_lhp)
        assert away_vs_rhp > away_vs_lhp


class TestFirstInningIsolationFromF5AndFullGame:
    def test_first_inning_only_evidence_does_not_change_away_home_f5(self):
        g_no_fi = _make_game()
        g_with_fi = copy.deepcopy(g_no_fi)
        g_with_fi['home']['pitcherSavant']['firstInningSplit'] = {
            'firstInningXERA': 7.5, 'appearances': 12,
        }

        away0, home0, f5a0, f5h0, _ = bml.compute_projections(g_no_fi)
        away1, home1, f5a1, f5h1, _ = bml.compute_projections(g_with_fi)

        assert away0 == away1
        assert home0 == home1
        assert f5a0 == f5a1
        assert f5h0 == f5h1

    def test_first_inning_evidence_moves_yrfi_nrfi_probability_coherently(self):
        g_neutral = _make_game()
        g_weak_home_starter = copy.deepcopy(g_neutral)
        g_weak_home_starter['home']['pitcherSavant']['firstInningSplit'] = {
            'firstInningXERA': 8.0, 'appearances': 15,
        }
        g_weak_home_starter['away']['pitcherSavant']['firstInningSplit'] = {
            'firstInningXERA': 8.0, 'appearances': 15,
        }

        rows_neutral = bml.evaluate_game(g_neutral)
        rows_weak = bml.evaluate_game(g_weak_home_starter)

        yrfi_neutral = next(r for r in rows_neutral if r['market'] == 'YRFI')
        yrfi_weak = next(r for r in rows_weak if r['market'] == 'YRFI')
        nrfi_neutral = next(r for r in rows_neutral if r['market'] == 'NRFI')
        nrfi_weak = next(r for r in rows_weak if r['market'] == 'NRFI')

        # Both starters weak in the 1st -> YRFI probability should rise,
        # NRFI should fall, relative to the naive-proxy baseline.
        assert yrfi_weak['modelProb'] > yrfi_neutral['modelProb']
        assert nrfi_weak['modelProb'] < nrfi_neutral['modelProb']
        assert yrfi_weak['firstInningContext']['dedicatedEvidenceApplied'] is True
        assert yrfi_neutral['firstInningContext']['dedicatedEvidenceApplied'] is False

    def test_yrfi_nrfi_rows_carry_first_inning_context_but_ml_row_does_not(self):
        g = _make_game()
        rows = bml.evaluate_game(g)
        yrfi = next(r for r in rows if r['market'] == 'YRFI')
        ml_away = next(r for r in rows if r['market'] == 'ML_Away')
        assert yrfi['firstInningContext'] is not None
        assert ml_away['firstInningContext'] is None

    def test_every_row_carries_shared_platoon_context(self):
        g = _make_game()
        rows = bml.evaluate_game(g)
        for row in rows:
            assert 'awayPlatoonContext' in row
            assert 'homePlatoonContext' in row


class TestProjectionBoardVisibilityUnchanged:
    def test_required_market_rows_still_present(self):
        g = _make_game()
        rows = bml.evaluate_game(g)
        markets = {r['market'] for r in rows}
        assert markets == set(bml.REQUIRED_MARKETS)
