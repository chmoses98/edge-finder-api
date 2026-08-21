#!/usr/bin/env python3
"""
tests/test_hitter_phase4_engine.py
=====================================
Hitter Projection Engine Phase 4 -- FULL PITCH-AWARE PA OUTCOME + GAME
SIMULATION ENGINE. Covers: hierarchical shrinkage, the shared PA-terminal
outcome model, pitch environment/sequence models, the contact model
(including the launch-angle carry-decay calibration fix), bullpen
exposure, the lineup/game Monte Carlo simulator, market distributions +
invariants, pricing, explainability, validation, feature ablation, board
row assembly, and a leakage/as-of-filtering regression test.
"""
import math
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from lib.research.hitter_shrinkage import shrink_rate, effective_sample_size, hierarchical_shrink, ShrinkageLevel
from lib.research.hitter_pa_outcome_model import (
    OUTCOME_CATEGORIES, LEAGUE_PRIOR_RATES, build_matchup_outcome_rates,
    apply_platoon_adjustment, apply_pitcher_quality_adjustment, build_pa_outcome_distribution,
    PLATOON_ADJ_CAP, PITCHER_QUALITY_ADJ_CAP, live_simulation_resample_targets,
)
from lib.research.pitch_environment_model import derive_pitcher_pitch_mix, derive_pitcher_pitch_profile_by_family
from lib.research.pitch_shape_similarity import similarity_weight, weighted_pitches
from lib.research.pitch_sequence_model import simulate_pa_pitch_by_pitch, CATCHER_UMPIRE_ADJUSTMENT_APPLIED
from lib.research.hitter_contact_model import (
    build_contact_pool, draw_contact_event, classify_batted_ball_shape, estimate_carry_distance_ft,
    wall_distance_at_spray_angle, convert_contact_to_outcome, CARRY_K, OPTIMAL_LAUNCH_ANGLE_DEG,
)
from lib.research.bullpen_exposure_model import should_starter_continue, choose_bullpen_pitcher_hand, bullpen_pitcher_quality
from lib.research.lineup_game_simulator import simulate_game, _advance_bases
from lib.research.hitter_market_distributions import build_hitter_market_distributions, run_invariant_checks
from lib.research.hitter_pricing import fair_american_odds, american_odds_to_implied_prob, price_hitter_contract
from lib.research.hitter_explainability import explain_hitter_pa_outcome
from lib.research.hitter_validation import _load_settlement_rows, run_walk_forward_validation, real_slate_illustrative_rows
from lib.research.hitter_feature_ablation import ablate_platoon_adjustment, ablate_pitcher_quality_adjustment
from lib.research.hitter_board_builder import build_hitter_projection_rows, match_real_contracts_for_hitter
from lib.research.hitter_synthetic_ground_truth import generate_synthetic_pitches, perturb_league_rates
from lib.research.hitter_pitch_derivation import derive_pa_outcomes_by_pitch_family, _count_pa_terminal_events

PARK_GEOMETRY = {"foulLineLF": 330, "powerAlleyLF": 375, "centerField": 400, "powerAlleyRF": 375, "foulLineRF": 330}


# ---------------------------------------------------------------------------
# 1. Hierarchical shrinkage
# ---------------------------------------------------------------------------
class TestHitterShrinkage:
    def test_shrink_rate_pulls_toward_prior_with_no_data(self):
        assert shrink_rate(0, 0, 0.3, 50.0) == 0.3

    def test_shrink_rate_dominated_by_large_sample(self):
        rate = shrink_rate(900, 1000, 0.1, 10.0)
        assert 0.88 < rate < 0.90

    def test_shrink_rate_rejects_invalid_inputs(self):
        with pytest.raises(ValueError):
            shrink_rate(1, -1, 0.3, 10.0)
        with pytest.raises(ValueError):
            shrink_rate(1, 10, 1.5, 10.0)
        with pytest.raises(ValueError):
            shrink_rate(1, 10, 0.3, -5.0)

    def test_effective_sample_size_is_observed_trials(self):
        assert effective_sample_size(37, 100.0) == 37

    def test_hierarchical_shrink_no_discontinuity_across_levels(self):
        levels = [
            ShrinkageLevel("family", 3, 8, 40.0),
            ShrinkageLevel("season", 40, 150, 120.0),
        ]
        result = hierarchical_shrink(levels, 0.25)
        assert 0.0 <= result["rate"] <= 1.0
        assert result["effectiveSampleSize"] == 8 + 150
        assert len(result["chain"]) == 2

    def test_hierarchical_shrink_falls_back_to_floor_with_zero_everywhere(self):
        levels = [ShrinkageLevel("family", None, None, 40.0), ShrinkageLevel("season", None, None, 120.0)]
        result = hierarchical_shrink(levels, 0.222)
        assert result["rate"] == pytest.approx(0.222)

    def test_hierarchical_shrink_trials_known_but_successes_none_degrades_instead_of_crashing(self):
        """
        Hitter Prop Methodology Repair mission: a level with a known
        trials count but an unknown (None) successes count -- e.g. a
        season_stats dict that tracks PA but is missing one specific
        outcome's count -- must degrade to "no data at this level" per
        this function's own documented contract, never raise. Previously
        only `trials is None` was checked, so this exact shape (trials
        known, successes None) crashed with a TypeError.
        """
        levels = [
            ShrinkageLevel("family", None, None, 40.0),
            ShrinkageLevel("season", None, 400, 120.0),  # trials known, successes unknown
        ]
        result = hierarchical_shrink(levels, 0.1)
        assert result["rate"] == pytest.approx(0.1)
        assert result["levelsUsed"] == []


# ---------------------------------------------------------------------------
# 2. PA outcome model
# ---------------------------------------------------------------------------
class TestHitterPAOutcomeModel:
    def test_rates_sum_to_one_with_no_data(self):
        result = build_matchup_outcome_rates({}, {}, None)
        assert sum(result["rates"].values()) == pytest.approx(1.0, abs=1e-6)

    def test_rates_sum_to_one_with_real_family_data(self):
        hitter_pa_by_family = {"four_seam": {"PA": 120, "AB": 110, "1B": 25, "2B": 8, "3B": 1, "HR": 6, "BB": 8, "HBP": 1, "K": 25}}
        season_stats = {"PA": 500, "AB": 450, "1B": 90, "2B": 25, "3B": 3, "HR": 20, "BB": 45, "HBP": 5, "K": 110}
        result = build_matchup_outcome_rates(hitter_pa_by_family, season_stats, {"four_seam": 1.0})
        assert sum(result["rates"].values()) == pytest.approx(1.0, abs=1e-6)

    def test_platoon_adjustment_is_capped(self):
        rates = dict(LEAGUE_PRIOR_RATES)
        adjusted = apply_platoon_adjustment(rates, {"platoonWOBA": 0.500}, 0.100)  # extreme delta
        total_shift = sum(abs(adjusted[k] - rates[k]) for k in rates) / 2.0
        assert total_shift <= PLATOON_ADJ_CAP + 1e-6
        assert sum(adjusted.values()) == pytest.approx(1.0, abs=1e-6)

    def test_platoon_adjustment_is_noop_without_data(self):
        rates = dict(LEAGUE_PRIOR_RATES)
        assert apply_platoon_adjustment(rates, {}, None) == rates

    def test_pitcher_quality_adjustment_is_capped_and_normalized(self):
        rates = dict(LEAGUE_PRIOR_RATES)
        adjusted = apply_pitcher_quality_adjustment(rates, {"kPct": 99.0, "bbPct": 0.5})
        assert sum(adjusted.values()) == pytest.approx(1.0, abs=1e-6)
        assert adjusted["K"] <= rates["K"] + PITCHER_QUALITY_ADJ_CAP + 1e-6

    def test_build_pa_outcome_distribution_toggles_are_independent(self):
        hitter_pa_by_family = {}
        season_stats = {}
        base = build_pa_outcome_distribution(hitter_pa_by_family, season_stats,
                                              enable_platoon_adj=False, enable_pitcher_quality_adj=False)
        with_platoon = build_pa_outcome_distribution(hitter_pa_by_family, season_stats,
                                                       platoon_context={"platoonWOBA": 0.40}, season_woba=0.30,
                                                       enable_platoon_adj=True, enable_pitcher_quality_adj=False)
        assert base["rates"] != with_platoon["rates"]
        assert sum(with_platoon["rates"].values()) == pytest.approx(1.0, abs=1e-6)


# ---------------------------------------------------------------------------
# 3. Pitch environment model
# ---------------------------------------------------------------------------
class TestPitchEnvironmentModel:
    def _pitcher_pitches(self, n=100):
        rng = random.Random(1)
        families = ["FF", "SL", "CH", "SI"]
        return [{"pitchType": rng.choice(families), "pitchName": None, "batterHand": rng.choice(["R", "L"]),
                 "releaseSpeed": 92.0} for _ in range(n)]

    def test_pitch_mix_sums_to_one(self):
        result = derive_pitcher_pitch_mix(self._pitcher_pitches())
        assert result["status"] != "MISSING_DATA"
        assert sum(result["mix"].values()) == pytest.approx(1.0, abs=1e-6)

    def test_missing_data_status_on_empty(self):
        result = derive_pitcher_pitch_mix([])
        assert result["status"] == "MISSING_DATA"

    def test_hand_conditioned_mix_also_sums_to_one(self):
        result = derive_pitcher_pitch_mix(self._pitcher_pitches(), batter_hand="R")
        assert sum(result["mix"].values()) == pytest.approx(1.0, abs=1e-6)


# ---------------------------------------------------------------------------
# 4. Pitch shape similarity
# ---------------------------------------------------------------------------
class TestPitchShapeSimilarity:
    def test_identical_profile_has_weight_one(self):
        profile = {"releaseSpeed": 95.0, "inducedVertBreak": 15.0, "horizontalBreak": 5.0}
        assert similarity_weight(profile, dict(profile)) == pytest.approx(1.0)

    def test_nearer_profile_scores_higher_than_farther(self):
        query = {"releaseSpeed": 95.0, "inducedVertBreak": 15.0}
        near = {"releaseSpeed": 94.0, "inducedVertBreak": 14.0}
        far = {"releaseSpeed": 80.0, "inducedVertBreak": -5.0}
        assert similarity_weight(query, near) > similarity_weight(query, far)

    def test_invalid_bandwidth_raises(self):
        with pytest.raises(ValueError):
            similarity_weight({"releaseSpeed": 95.0}, {"releaseSpeed": 95.0}, bandwidth=0.0)

    def test_weighted_pitches_sorted_descending(self):
        query = {"releaseSpeed": 95.0}
        candidates = [{"releaseSpeed": 70.0}, {"releaseSpeed": 95.0}, {"releaseSpeed": 90.0}]
        ranked = weighted_pitches(query, candidates)
        weights = [w for _c, w in ranked]
        assert weights == sorted(weights, reverse=True)


# ---------------------------------------------------------------------------
# 5. Pitch sequence model
# ---------------------------------------------------------------------------
class TestPitchSequenceModel:
    def test_honest_catcher_umpire_flag_is_false(self):
        assert CATCHER_UMPIRE_ADJUSTMENT_APPLIED is False

    def test_simulation_is_deterministic_given_seed(self):
        mix = {"four_seam": 0.5, "slider": 0.5}
        a = simulate_pa_pitch_by_pitch([], mix, random.Random(99))
        b = simulate_pa_pitch_by_pitch([], mix, random.Random(99))
        assert a == b

    def test_outcome_is_always_one_of_four_terminal_states(self):
        mix = {"four_seam": 0.5, "slider": 0.5}
        rng = random.Random(5)
        for _ in range(200):
            result = simulate_pa_pitch_by_pitch([], mix, rng)
            assert result["outcome"] in ("BB", "K", "HBP", "IN_PLAY")

    def test_forced_zero_hbp_rate_never_returns_hbp(self):
        mix = {"four_seam": 1.0}
        rng = random.Random(3)
        for _ in range(50):
            result = simulate_pa_pitch_by_pitch([], mix, rng, hbp_rate=0.0)
            assert result["outcome"] != "HBP"


# ---------------------------------------------------------------------------
# 6. Contact model (incl. the launch-angle carry-decay calibration fix)
# ---------------------------------------------------------------------------
class TestHitterContactModel:
    def test_calibration_anchor_is_exact(self):
        assert estimate_carry_distance_ft(105.0, 28.0) == pytest.approx(420.0, abs=0.01)

    def test_carry_distance_zero_for_nonpositive_launch_angle(self):
        assert estimate_carry_distance_ft(100.0, 0.0) == 0.0
        assert estimate_carry_distance_ft(100.0, -5.0) == 0.0

    def test_high_launch_angle_carries_less_than_optimal(self):
        """Regression test for the over-carry bug this phase found and fixed: a 50deg
        can-of-corn fly ball must travel meaningfully less far than the OPTIMAL_LAUNCH_ANGLE_DEG
        anchor at the same exit velocity, not nearly as far (raw sin(2*LA) alone stays within
        ~15% of its max across the entire 25-50deg fly-ball range)."""
        optimal = estimate_carry_distance_ft(100.0, OPTIMAL_LAUNCH_ANGLE_DEG)
        high = estimate_carry_distance_ft(100.0, 50.0)
        assert high < optimal * 0.85

    def test_home_run_rate_on_fly_balls_is_realistic(self):
        """The synthetic default draw's fly-ball HR rate must land near real MLB's
        ~11-13% HR/FB average, not the ~50% the pre-fix carry formula produced."""
        rng = random.Random(7)
        fb_total = fb_hr = 0
        for _ in range(6000):
            ev = max(40.0, min(120.0, rng.gauss(88.0, 15.0)))
            la = max(-40.0, min(70.0, rng.gauss(12.0, 20.0)))
            spray = max(-45.0, min(45.0, rng.gauss(0.0, 20.0)))
            if classify_batted_ball_shape(la) != "fly_ball":
                continue
            fb_total += 1
            distance = estimate_carry_distance_ft(ev, la)
            wall = wall_distance_at_spray_angle(PARK_GEOMETRY, spray)
            if distance >= wall:
                fb_hr += 1
        rate = fb_hr / fb_total
        assert 0.05 < rate < 0.20, f"FB-HR rate {rate} outside realistic band"

    def test_wall_distance_interpolates_linearly(self):
        mid = wall_distance_at_spray_angle(PARK_GEOMETRY, -33.75)  # midpoint of foulLineLF(-45)/powerAlleyLF(-22.5)
        assert mid == pytest.approx((330 + 375) / 2, abs=0.01)

    def test_wall_distance_none_without_geometry(self):
        assert wall_distance_at_spray_angle(None, 0.0) is None

    def test_popup_hit_rate_is_low(self):
        rng = random.Random(1)
        hits = sum(1 for _ in range(2000) if convert_contact_to_outcome(
            80.0, 60.0, 0.0, "R", PARK_GEOMETRY, None, None, None, rng)["outcome"] != "OUT")
        assert hits / 2000 < 0.05

    def test_ground_ball_never_produces_extra_base_hit(self):
        rng = random.Random(2)
        for _ in range(500):
            result = convert_contact_to_outcome(95.0, 3.0, 0.0, "R", PARK_GEOMETRY, None, None, None, rng)
            assert result["outcome"] in ("OUT", "1B")

    def test_hr_only_possible_on_fly_ball_shape_with_geometry(self):
        rng = random.Random(4)
        for _ in range(500):
            result = convert_contact_to_outcome(80.0, 18.0, 0.0, "R", PARK_GEOMETRY, None, None, None, rng)
            assert result["outcome"] != "HR"  # line_drive shape (LA=18) never eligible for HR

    def test_build_contact_pool_excludes_missing_ev_la(self):
        pitches = [
            {"pitchCallType": "in_play", "launchSpeed": 95.0, "launchAngle": 20.0, "hitCoordX": None, "hitCoordY": None, "battedBallType": "line_drive", "batterHand": "R"},
            {"pitchCallType": "in_play", "launchSpeed": None, "launchAngle": None},
            {"pitchCallType": "ball"},
        ]
        pool = build_contact_pool(pitches)
        assert len(pool) == 1

    def test_draw_contact_event_falls_back_to_synthetic_when_pool_empty(self):
        rng = random.Random(1)
        ev, la, spray, bbt = draw_contact_event([], rng)
        assert ev is not None and la is not None


# ---------------------------------------------------------------------------
# 7. Bullpen exposure model
# ---------------------------------------------------------------------------
class TestBullpenExposureModel:
    def test_starter_almost_always_continues_well_below_budget(self):
        rng = random.Random(1)
        continues = sum(should_starter_continue(1.0, 6.0, rng) for _ in range(500))
        assert continues / 500 > 0.9

    def test_starter_almost_always_pulled_well_past_budget(self):
        rng = random.Random(1)
        continues = sum(should_starter_continue(9.0, 5.0, rng) for _ in range(500))
        assert continues / 500 < 0.1

    def test_starter_roughly_even_at_budget(self):
        rng = random.Random(1)
        continues = sum(should_starter_continue(5.2, 5.2, rng) for _ in range(1000))
        assert 0.4 < continues / 1000 < 0.6

    def test_bullpen_hand_choice_respects_handedness_mix(self):
        rng = random.Random(1)
        context = {"recentUsage": {"handednessMix": {"R": 9, "L": 1}}}
        r_count = sum(1 for _ in range(500) if choose_bullpen_pitcher_hand(context, rng) == "R")
        assert r_count / 500 > 0.8

    def test_bullpen_quality_flags_approximate(self):
        result = bullpen_pitcher_quality({"teamQuality": {"kPer9": 9.5, "bbPer9": 3.2}})
        assert result["approximate"] is True
        assert result["kPct"] is not None


# ---------------------------------------------------------------------------
# 8. Lineup / game Monte Carlo simulator
# ---------------------------------------------------------------------------
class TestLineupGameSimulator:
    _COMMON = dict(
        target_hitter_pitches=[], batter_hand="R",
        starter_context={"avgIPperStart": 5.2}, bullpen_context={},
        starter_pitch_mix={"four_seam": 0.5, "slider": 0.5}, bullpen_pitch_mix={"four_seam": 0.5, "curve": 0.5},
        park_geometry_entry=PARK_GEOMETRY, field_relative_wind=None, defense_snapshot=None,
        hitter_speed_snapshot=None,
    )

    def test_invalid_target_slot_raises(self):
        with pytest.raises(ValueError):
            simulate_game(target_slot=0, rng=random.Random(1), **self._COMMON)
        with pytest.raises(ValueError):
            simulate_game(target_slot=10, rng=random.Random(1), **self._COMMON)

    def test_determinism_given_same_seed(self):
        a = simulate_game(target_slot=3, rng=random.Random(42), **self._COMMON)
        b = simulate_game(target_slot=3, rng=random.Random(42), **self._COMMON)
        assert a == b

    def test_tb_and_hits_are_internally_consistent_across_many_games(self):
        for seed in range(150):
            stats = simulate_game(target_slot=4, rng=random.Random(seed), **self._COMMON)
            assert stats["TB"] == stats["1B"] + 2 * stats["2B"] + 3 * stats["3B"] + 4 * stats["HR"]
            assert stats["H"] == stats["1B"] + stats["2B"] + stats["3B"] + stats["HR"]
            assert stats["PA"] >= stats["AB"] >= 0
            if stats["HR"] >= 1:
                assert stats["H"] >= 1 and stats["TB"] >= 4

    def test_advance_bases_grand_slam_scores_four(self):
        bases, runs = _advance_bases(["a", "b", "c"], "BATTER", "HR")
        assert bases == [None, None, None]
        assert sorted(runs) == sorted(["a", "b", "c", "BATTER"])

    def test_advance_bases_walk_with_first_empty_does_not_force_others(self):
        bases, runs = _advance_bases([None, "b", "c"], "BATTER", "BB")
        assert bases == ["BATTER", "b", "c"]
        assert runs == []

    def test_advance_bases_walk_with_bases_loaded_forces_a_run(self):
        bases, runs = _advance_bases(["a", "b", "c"], "BATTER", "BB")
        assert bases == ["BATTER", "a", "b"]
        assert runs == ["c"]


# ---------------------------------------------------------------------------
# 9. Market distributions + invariants
# ---------------------------------------------------------------------------
class TestHitterMarketDistributions:
    _KW = dict(
        target_slot=3, target_hitter_pitches=[], batter_hand="R",
        starter_context={"avgIPperStart": 5.2}, bullpen_context={},
        starter_pitch_mix={"four_seam": 0.5, "slider": 0.5}, bullpen_pitch_mix={"four_seam": 0.5, "curve": 0.5},
        park_geometry_entry=PARK_GEOMETRY, field_relative_wind=None, defense_snapshot=None,
        hitter_speed_snapshot=None,
    )

    def test_determinism(self):
        a = build_hitter_market_distributions(n_sims=500, seed=11, **self._KW)
        b = build_hitter_market_distributions(n_sims=500, seed=11, **self._KW)
        assert a["distributions"]["hits"]["pmf"] == b["distributions"]["hits"]["pmf"]

    def test_all_invariant_checks_pass(self):
        result = build_hitter_market_distributions(n_sims=1500, seed=3, **self._KW)
        assert result["invariantChecks"]["allPassed"] is True

    def test_atleast_thresholds_monotonic_nonincreasing(self):
        result = build_hitter_market_distributions(n_sims=1500, seed=4, **self._KW)
        at_least = result["distributions"]["hits"]["atLeast"]
        keys = sorted(at_least)
        for i in range(len(keys) - 1):
            assert at_least[keys[i]] >= at_least[keys[i + 1]] - 1e-9

    def test_atleast_zero_is_always_one(self):
        result = build_hitter_market_distributions(n_sims=500, seed=5, **self._KW)
        for dist in result["distributions"].values():
            assert dist["atLeast"][0] == pytest.approx(1.0)

    def test_hr_two_plus_le_hr_one_plus(self):
        result = build_hitter_market_distributions(n_sims=2000, seed=6, **self._KW)
        hr = result["distributions"]["homeRuns"]["atLeast"]
        assert hr[2] <= hr[1] + 1e-9


# ---------------------------------------------------------------------------
# 9b. Hitter Prop Methodology Repair mission: platoon/pitcher-quality live
# wiring, and real-teammate-rate wiring (previously bypassed/dead in the
# live pricing path -- see lib.research.hitter_pa_outcome_model's
# live_simulation_resample_targets and scripts/build_hitter_projection_board's
# _build_team_other_hitter_rates).
# ---------------------------------------------------------------------------
class TestLiveSimulationResampleTargets:
    def test_none_when_no_context_at_all(self):
        assert live_simulation_resample_targets({}, {}, pitcher_pitch_mix=None,
                                                  platoon_context=None, season_woba=None, starter_context=None) is None

    def test_none_when_platoon_context_has_no_usable_woba(self):
        """apply_platoon_adjustment's own no-op condition (no platoonWOBA/season_woba) must propagate -- never a fabricated effect."""
        result = live_simulation_resample_targets(
            {}, {"PA": 400, "K": 80, "BB": 40, "1B": 60, "2B": 20, "3B": 2, "HR": 15},
            pitcher_pitch_mix=None, platoon_context={"status": "NO_DATA"}, season_woba=None, starter_context=None,
        )
        assert result is None

    def test_returns_bounded_multipliers_and_valid_adjusted_rates_when_platoon_favorable(self):
        season_stats = {"PA": 400, "K": 80, "BB": 40, "1B": 60, "2B": 20, "3B": 2, "HR": 15}
        result = live_simulation_resample_targets(
            {}, season_stats, pitcher_pitch_mix=None,
            platoon_context={"platoonWOBA": 0.400}, season_woba=0.320, starter_context=None,
        )
        assert result is not None
        assert set(result["multipliers"]) == set(OUTCOME_CATEGORIES)
        assert sum(result["adjustedRates"].values()) == pytest.approx(1.0, abs=1e-6)
        # Favorable platoon (platoonWOBA > season_woba) must shift mass toward hits/BB, away from K/OUT.
        assert result["multipliers"]["K"] < 1.0 + 1e-9
        assert result["multipliers"]["1B"] > 1.0 - 1e-9

    def test_pitcher_quality_only_still_produces_a_result(self):
        season_stats = {"PA": 400, "K": 80, "BB": 40, "1B": 60, "2B": 20, "3B": 2, "HR": 15}
        result = live_simulation_resample_targets(
            {}, season_stats, pitcher_pitch_mix=None,
            platoon_context=None, season_woba=None, starter_context={"kPct": 30.0, "bbPct": 5.0},
        )
        assert result is not None
        assert result["multipliers"]["K"] > 1.0 - 1e-9  # elevated-K pitcher should raise K likelihood


class TestPlatoonPitcherQualityLiveWiring:
    """Proves the live pricing path (build_hitter_market_distributions) actually reflects platoon/pitcher-quality context -- not just explainability text."""
    _BASE_KW = dict(
        target_slot=3, target_hitter_pitches=[], batter_hand="R",
        bullpen_context={}, starter_pitch_mix={"four_seam": 0.5, "slider": 0.5}, bullpen_pitch_mix={"four_seam": 0.5, "curve": 0.5},
        park_geometry_entry=PARK_GEOMETRY, field_relative_wind=None, defense_snapshot=None,
        hitter_speed_snapshot=None,
        season_stats={"PA": 400, "K": 80, "BB": 40, "1B": 60, "2B": 20, "3B": 2, "HR": 15},
    )

    def test_favorable_platoon_context_raises_hit_probability(self):
        without = build_hitter_market_distributions(
            n_sims=3000, seed=21, starter_context={"avgIPperStart": 5.2}, **self._BASE_KW,
        )
        with_favorable = build_hitter_market_distributions(
            n_sims=3000, seed=21, starter_context={"avgIPperStart": 5.2},
            platoon_context={"platoonWOBA": 0.420}, season_woba=0.310, **self._BASE_KW,
        )
        assert with_favorable["distributions"]["hits"]["atLeast"][1] > without["distributions"]["hits"]["atLeast"][1]

    def test_unfavorable_platoon_context_lowers_hit_probability(self):
        without = build_hitter_market_distributions(
            n_sims=3000, seed=22, starter_context={"avgIPperStart": 5.2}, **self._BASE_KW,
        )
        with_unfavorable = build_hitter_market_distributions(
            n_sims=3000, seed=22, starter_context={"avgIPperStart": 5.2},
            platoon_context={"platoonWOBA": 0.220}, season_woba=0.310, **self._BASE_KW,
        )
        assert with_unfavorable["distributions"]["hits"]["atLeast"][1] < without["distributions"]["hits"]["atLeast"][1]

    def test_elevated_pitcher_quality_lowers_hit_probability(self):
        without = build_hitter_market_distributions(
            n_sims=3000, seed=23, starter_context={"avgIPperStart": 5.2}, **self._BASE_KW,
        )
        with_ace = build_hitter_market_distributions(
            n_sims=3000, seed=23, starter_context={"avgIPperStart": 5.2, "kPct": 32.0, "bbPct": 5.0},
            **self._BASE_KW,
        )
        assert with_ace["distributions"]["hits"]["atLeast"][1] < without["distributions"]["hits"]["atLeast"][1]

    def test_missing_platoon_and_pitcher_quality_context_reproduces_prior_behavior_exactly(self):
        """Regression safety: when the new optional params are entirely omitted, output is byte-identical to pre-mission behavior."""
        a = build_hitter_market_distributions(n_sims=800, seed=99, starter_context={"avgIPperStart": 5.2}, **self._BASE_KW)
        b = build_hitter_market_distributions(
            n_sims=800, seed=99, starter_context={"avgIPperStart": 5.2},
            hitter_pa_by_family=None, season_woba=None, platoon_context=None, **self._BASE_KW,
        )
        assert a["distributions"]["hits"]["pmf"] == b["distributions"]["hits"]["pmf"]


class TestOtherHitterRatesTeammateWiring:
    """Proves real teammate PA-outcome rates (vs the prior always-league-average default) actually move RBI probability."""
    _KW = dict(
        target_slot=5, target_hitter_pitches=[], batter_hand="R",
        starter_context={"avgIPperStart": 5.2}, bullpen_context={},
        starter_pitch_mix={"four_seam": 0.5, "slider": 0.5}, bullpen_pitch_mix={"four_seam": 0.5, "curve": 0.5},
        park_geometry_entry=PARK_GEOMETRY, field_relative_wind=None, defense_snapshot=None,
        hitter_speed_snapshot=None,
    )
    _WEAK_LINEUP = [dict(LEAGUE_PRIOR_RATES, K=0.35, BB=0.04, **{k: LEAGUE_PRIOR_RATES[k] * 0.5 for k in ("1B", "2B", "3B", "HR")})] * 9
    _STRONG_LINEUP = [dict(LEAGUE_PRIOR_RATES, K=0.12, BB=0.15, **{k: LEAGUE_PRIOR_RATES[k] * 1.6 for k in ("1B", "2B", "3B", "HR")})] * 9

    def test_stronger_teammates_raise_target_hitters_rbi_probability(self):
        weak = build_hitter_market_distributions(n_sims=3000, seed=31, other_hitter_rates=self._WEAK_LINEUP, **self._KW)
        strong = build_hitter_market_distributions(n_sims=3000, seed=31, other_hitter_rates=self._STRONG_LINEUP, **self._KW)
        assert strong["distributions"]["rbis"]["atLeast"][1] > weak["distributions"]["rbis"]["atLeast"][1]

    def test_stronger_teammates_raise_hits_runs_rbis_probability(self):
        weak = build_hitter_market_distributions(n_sims=3000, seed=32, other_hitter_rates=self._WEAK_LINEUP, **self._KW)
        strong = build_hitter_market_distributions(n_sims=3000, seed=32, other_hitter_rates=self._STRONG_LINEUP, **self._KW)
        assert strong["distributions"]["hitsRunsRbis"]["atLeast"][2] > weak["distributions"]["hitsRunsRbis"]["atLeast"][2]

    def test_teammate_strength_does_not_fabricate_a_large_shift_in_the_target_hitters_own_hits(self):
        """Hits is the target hitter's OWN outcome -- teammate strength should not swing it nearly as much as RBI (sanity bound, not a strict no-op, since inning-length/PA-count effects are real but secondary)."""
        weak = build_hitter_market_distributions(n_sims=4000, seed=33, other_hitter_rates=self._WEAK_LINEUP, **self._KW)
        strong = build_hitter_market_distributions(n_sims=4000, seed=33, other_hitter_rates=self._STRONG_LINEUP, **self._KW)
        hits_delta = abs(strong["distributions"]["hits"]["atLeast"][1] - weak["distributions"]["hits"]["atLeast"][1])
        rbi_delta = abs(strong["distributions"]["rbis"]["atLeast"][1] - weak["distributions"]["rbis"]["atLeast"][1])
        assert hits_delta < rbi_delta

    def test_omitted_other_hitter_rates_reproduces_prior_league_average_behavior(self):
        default = build_hitter_market_distributions(n_sims=500, seed=41, **self._KW)
        explicit_league_avg = build_hitter_market_distributions(
            n_sims=500, seed=41, other_hitter_rates=[LEAGUE_PRIOR_RATES] * 9, **self._KW,
        )
        assert default["distributions"]["rbis"]["pmf"] == explicit_league_avg["distributions"]["rbis"]["pmf"]


# ---------------------------------------------------------------------------
# 10. Pricing
# ---------------------------------------------------------------------------
class TestHitterPricing:
    def test_fair_odds_evens_case(self):
        assert fair_american_odds(0.5) == -100

    def test_fair_odds_none_for_degenerate_probabilities(self):
        assert fair_american_odds(0.0) is None
        assert fair_american_odds(1.0) is None
        assert fair_american_odds(None) is None

    def test_fair_odds_round_trip(self):
        for p in (0.05, 0.25, 0.5, 0.75, 0.95):
            odds = fair_american_odds(p)
            back = american_odds_to_implied_prob(odds)
            assert back == pytest.approx(p, abs=0.01)

    def test_price_contract_no_executable_price(self):
        result = price_hitter_contract(0.4, None)
        assert result["pricingStatus"] == "NO_EXECUTABLE_PRICE"
        assert result["rawProbabilityEdge"] is None

    def test_price_contract_positive_edge(self):
        result = price_hitter_contract(0.5, 0.3)
        assert result["pricingStatus"] == "PRICED"
        assert result["rawProbabilityEdge"] == pytest.approx(0.2)
        assert result["expectedValuePerDollar"] > 0

    def test_price_contract_negative_edge(self):
        result = price_hitter_contract(0.2, 0.5)
        assert result["rawProbabilityEdge"] == pytest.approx(-0.3)
        assert result["expectedValuePerDollar"] < 0


# ---------------------------------------------------------------------------
# 11. Explainability
# ---------------------------------------------------------------------------
class TestHitterExplainability:
    def test_every_step_sums_to_one(self):
        result = explain_hitter_pa_outcome({}, {}, pitcher_pitch_mix={"four_seam": 1.0},
                                            platoon_context={"platoonWOBA": 0.35}, season_woba=0.31,
                                            starter_context={"kPct": 25.0, "bbPct": 7.0})
        for step in result["steps"]:
            assert sum(step["rates"].values()) == pytest.approx(1.0, abs=1e-6)

    def test_feature_groups_reflect_missing_data_honestly(self):
        result = explain_hitter_pa_outcome({}, {}, pitcher_pitch_mix=None, platoon_context=None,
                                            season_woba=None, starter_context=None)
        flags = result["featureGroupsApplied"]
        assert flags["platoonAdjustment"] is False
        assert flags["pitcherQualityAdjustment"] is False
        assert flags["pitcherPitchMix"] is False

    def test_first_step_delta_is_none(self):
        result = explain_hitter_pa_outcome({}, {})
        assert result["steps"][0]["deltaFromPrevious"] is None


# ---------------------------------------------------------------------------
# 12. Validation (synthetic walk-forward + real-slate illustrative)
# ---------------------------------------------------------------------------
class TestHitterValidation:
    def test_walk_forward_beats_or_matches_league_prior_baseline(self):
        result = run_walk_forward_validation(n_synthetic_hitters=12, n_history_pa=200, n_future_pa=30, seed=1)
        assert result["logLoss"]["thisEngine"] <= result["logLoss"]["leaguePriorOnlyBaseline"] + 0.02

    def test_walk_forward_reports_calibration_table(self):
        result = run_walk_forward_validation(n_synthetic_hitters=8, n_history_pa=150, n_future_pa=20, seed=2)
        assert len(result["hitRateCalibrationTable"]) > 0
        assert result["totalScoredPA"] == 8 * 20

    def test_real_slate_rows_are_labeled_illustrative_not_leakfree(self):
        result = real_slate_illustrative_rows(max_rows=5)
        if result["status"] == "OK":
            assert result["validationMode"] == "ILLUSTRATIVE_NOT_LEAKFREE"
            assert len(result["illustrativeRows"]) <= 5
            for row in result["illustrativeRows"]:
                assert row["marketFamily"] in ("hitter_hits", "hitter_total_bases", "hitter_rbis", "hitter_hits_runs_rbis")


# ---------------------------------------------------------------------------
# 13. Feature ablation
# ---------------------------------------------------------------------------
class TestHitterFeatureAblation:
    def test_platoon_ablation_reports_a_direction(self):
        result = ablate_platoon_adjustment(n_trials=80, seed=1)
        assert result["featureGroup"] == "platoon_adjustment"
        assert isinstance(result["heldOutResultsImprove"], bool)
        assert result["nScoredPA"] > 0

    def test_pitcher_quality_ablation_reports_a_direction(self):
        result = ablate_pitcher_quality_adjustment(n_trials=80, seed=2)
        assert result["featureGroup"] == "pitcher_quality_adjustment"
        assert isinstance(result["heldOutResultsImprove"], bool)


# ---------------------------------------------------------------------------
# 14. Board row assembly
# ---------------------------------------------------------------------------
class TestHitterBoardBuilder:
    RAW_MARKETS = [
        {"event_ticker": "KXMLBHIT-26AUG102140COLAZ", "market_ticker": "KXMLBHIT-26AUG102140COLAZ-COLWCASTRO3-1",
         "title": "Willi Castro: 1+ hits?", "subtitle": "", "yes_bid": 0.60, "yes_ask": 0.64, "mid": 0.62},
        {"event_ticker": "KXMLBHIT-26AUG102140COLAZ", "market_ticker": "KXMLBHIT-26AUG102140COLAZ-AZOTHER5-1",
         "title": "Some Other Player: 1+ hits?", "subtitle": "", "yes_bid": 0.5, "yes_ask": 0.5, "mid": 0.5},
    ]

    def test_match_real_contracts_filters_by_name(self):
        matched = match_real_contracts_for_hitter(self.RAW_MARKETS, "Willi Castro", "COL", "AZ")
        assert len(matched) == 1
        assert matched[0]["marketTicker"].endswith("COLWCASTRO3-1")

    def test_match_real_contracts_empty_for_unknown_name(self):
        matched = match_real_contracts_for_hitter(self.RAW_MARKETS, "Nobody Real", "COL", "AZ")
        assert matched == []

    def test_no_lineup_slot_status(self):
        result = build_hitter_projection_rows(
            player_id="1", player_name="Willi Castro", batter_hand="R", target_slot=None,
            matchup_label="COL @ AZ", raw_pitches=[], season_stats={}, starter_pitches=None,
            starter_context={}, bullpen_context={}, park_geometry_entry=PARK_GEOMETRY,
            field_relative_wind=None, defense_snapshot=None, hitter_speed_snapshot=None,
            platoon_context=None, season_woba=None, raw_markets_for_game=self.RAW_MARKETS,
            away_abbr="COL", home_abbr="AZ", n_sims=200, seed=1,
        )
        assert result["status"] == "NO_LINEUP_SLOT"
        assert result["rows"] == []

    def test_no_archived_contracts_status(self):
        result = build_hitter_projection_rows(
            player_id="1", player_name="Nobody Real", batter_hand="R", target_slot=3,
            matchup_label="COL @ AZ", raw_pitches=[], season_stats={}, starter_pitches=None,
            starter_context={}, bullpen_context={}, park_geometry_entry=PARK_GEOMETRY,
            field_relative_wind=None, defense_snapshot=None, hitter_speed_snapshot=None,
            platoon_context=None, season_woba=None, raw_markets_for_game=self.RAW_MARKETS,
            away_abbr="COL", home_abbr="AZ", n_sims=200, seed=1,
        )
        assert result["status"] == "NO_ARCHIVED_CONTRACTS"

    def test_projected_rows_have_required_board_fields(self):
        result = build_hitter_projection_rows(
            player_id="1", player_name="Willi Castro", batter_hand="R", target_slot=2,
            matchup_label="COL @ AZ", raw_pitches=[], season_stats={}, starter_pitches=None,
            starter_context={"avgIPperStart": 5.2}, bullpen_context={}, park_geometry_entry=PARK_GEOMETRY,
            field_relative_wind=None, defense_snapshot=None, hitter_speed_snapshot=None,
            platoon_context=None, season_woba=None, raw_markets_for_game=self.RAW_MARKETS,
            away_abbr="COL", home_abbr="AZ", n_sims=500, seed=1,
        )
        assert result["status"] == "PROJECTED"
        assert len(result["rows"]) == 1
        row = result["rows"][0]
        for field in ("marketTicker", "naturalLanguageMarket", "player", "matchup", "modelProbability",
                      "fairAmericanOdds", "executableKalshiPrice", "executableAmericanOdds",
                      "rawProbabilityEdge", "expectedValuePerDollar", "projectionStatus",
                      "sampleSizeDiagnostics", "modelLimitations"):
            assert field in row


# ---------------------------------------------------------------------------
# 15. Leakage / as-of-filtering regression
# ---------------------------------------------------------------------------
class TestNoLeakage:
    def test_future_pa_outcomes_are_not_visible_to_history_only_model(self):
        """A hitter who is a pure .000 hitter in the past but goes on a hot streak
        AFTER the as-of cutoff must not have that hot streak leak into a model
        built from only the pre-cutoff pitches."""
        rng = random.Random(1)
        cold_rates = {"K": 0.9, "BB": 0.0, "HBP": 0.0, "1B": 0.0, "2B": 0.0, "3B": 0.0, "HR": 0.0, "OUT": 0.1}
        hot_rates = {"K": 0.0, "BB": 0.0, "HBP": 0.0, "1B": 0.0, "2B": 0.0, "3B": 0.0, "HR": 1.0, "OUT": 0.0}

        history = generate_synthetic_pitches(cold_rates, 100, rng, start_day_index=0)
        future = generate_synthetic_pitches(hot_rates, 30, rng, start_day_index=100)

        history_by_family = derive_pa_outcomes_by_pitch_family(history)
        counts, pa, ab, _dates, _unrec = _count_pa_terminal_events(history)
        season_stats = dict(counts, PA=pa, AB=ab)
        history_only_rates = build_pa_outcome_distribution(history_by_family, season_stats)["rates"]

        # Leaking the future HR-only streak into the model would push HR rate
        # far above what a cold-hitter-only history could ever justify.
        assert history_only_rates["HR"] < 0.10

        leaked_by_family = derive_pa_outcomes_by_pitch_family(history + future)
        leaked_counts, leaked_pa, leaked_ab, _d, _u = _count_pa_terminal_events(history + future)
        leaked_season_stats = dict(leaked_counts, PA=leaked_pa, AB=leaked_ab)
        leaked_rates = build_pa_outcome_distribution(leaked_by_family, leaked_season_stats)["rates"]

        # Proves the future PAs really would have changed the outcome if leaked
        # -- confirming the history-only call above genuinely excluded them.
        assert leaked_rates["HR"] > history_only_rates["HR"]

    def test_board_builder_uses_only_supplied_raw_pitches(self):
        """build_hitter_projection_rows must never reach outside the raw_pitches
        list it's given (e.g. via a live fetch or a module-level cache) --
        passing an empty list must fall back to season/league-level shrinkage,
        never silently pull in unrelated data."""
        result = build_hitter_projection_rows(
            player_id="1", player_name="Willi Castro", batter_hand="R", target_slot=2,
            matchup_label="COL @ AZ", raw_pitches=[], season_stats={}, starter_pitches=None,
            starter_context={}, bullpen_context={}, park_geometry_entry=PARK_GEOMETRY,
            field_relative_wind=None, defense_snapshot=None, hitter_speed_snapshot=None,
            platoon_context=None, season_woba=None,
            raw_markets_for_game=TestHitterBoardBuilder.RAW_MARKETS,
            away_abbr="COL", home_abbr="AZ", n_sims=300, seed=1,
        )
        assert result["rows"][0]["sampleSizeDiagnostics"]["hitterArchivedPACount"] == 0


class TestLoadSettlementRowsReadsCompactedPartitions:
    """
    Corpus Storage Growth mission: _load_settlement_rows previously used a
    raw open() + a `*.jsonl`-only glob, so a settlements/<date>.jsonl.gz
    file (produced by lib.edgelab.storage.compact_finalized_partitions())
    would have been silently invisible to this reader -- neither matched
    by the glob nor readable as plain text if it somehow were. Proves the
    fix reads a compacted date transparently, identically to a plain one.
    """

    def _settlement_row(self, row_id, family="hitter_hits", outcome="YES"):
        return {"settlementId": row_id, "marketTicker": f"T-{row_id}", "marketFamily": family, "outcome": outcome}

    def test_reads_gzipped_settlement_partition(self, tmp_path):
        import gzip
        import json
        path = tmp_path / "2026-07-30.jsonl.gz"
        with gzip.open(path, "wt") as f:
            f.write(json.dumps(self._settlement_row("s1")) + "\n")

        rows = _load_settlement_rows(date_glob=str(tmp_path / "*.jsonl*"))

        assert len(rows) == 1
        assert rows[0]["settlementId"] == "s1"

    def test_reads_mixed_plain_and_gzipped_partitions_together(self, tmp_path):
        import gzip
        import json
        with open(tmp_path / "2026-07-29.jsonl", "w") as f:
            f.write(json.dumps(self._settlement_row("s-plain")) + "\n")
        with gzip.open(tmp_path / "2026-07-30.jsonl.gz", "wt") as f:
            f.write(json.dumps(self._settlement_row("s-gz")) + "\n")

        rows = _load_settlement_rows(date_glob=str(tmp_path / "*.jsonl*"))

        assert {r["settlementId"] for r in rows} == {"s-plain", "s-gz"}

    def test_ignores_lock_sidecar_files(self, tmp_path):
        (tmp_path / "2026-07-30.jsonl.lock").write_bytes(b"")
        with open(tmp_path / "2026-07-30.jsonl", "w") as f:
            import json
            f.write(json.dumps(self._settlement_row("s1")) + "\n")

        rows = _load_settlement_rows(date_glob=str(tmp_path / "*.jsonl*"))

        assert len(rows) == 1
