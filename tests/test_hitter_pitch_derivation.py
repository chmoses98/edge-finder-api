#!/usr/bin/env python3
"""
tests/test_hitter_pitch_derivation.py
========================================
Unit tests for lib/research/hitter_pitch_derivation.py -- Hitter
Projection Engine Phase 2 derived hitter feature tables.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from lib.research.hitter_pitch_derivation import (
    window_bounds, derive_baseline_talent_window, derive_plate_discipline,
    derive_contact_quality, derive_pitch_type_breakdown, derive_velocity_breakdown,
    derive_location_summary, derive_count_state_breakdown, compare_windows,
    derive_spray_profile,
)


def _pitch(**overrides):
    base = {
        "gameDate": "2026-06-01", "batterId": "1", "batterHand": "R",
        "pitchType": "FF", "pitchName": "4-Seam Fastball", "releaseSpeed": 95.0,
        "balls": 0, "strikes": 0, "plateX": 0.0, "plateZ": 2.5, "szTop": 3.5, "szBot": 1.5,
        "pitchCallType": "ball", "events": None,
        "launchSpeed": None, "launchAngle": None, "hitCoordX": None, "hitCoordY": None,
        "battedBallType": None, "estimatedBA": None, "estimatedWOBA": None,
    }
    base.update(overrides)
    return base


class TestWindowBoundsDateCorrectness:
    def test_career_is_unbounded_below(self):
        since, until = window_bounds("2026-08-15", "career")
        assert since is None
        assert until == "2026-08-15"

    def test_current_season_starts_jan_1_same_year(self):
        since, until = window_bounds("2026-08-15", "currentSeason")
        assert since == "2026-01-01"
        assert until == "2026-08-15"

    def test_previous_season_is_full_prior_year(self):
        since, until = window_bounds("2026-08-15", "previousSeason")
        assert since == "2025-01-01"
        assert until == "2026-01-01"

    def test_rolling_windows_are_exact_day_counts(self):
        assert window_bounds("2026-08-15", "rolling30d") == ("2026-07-16", "2026-08-15")
        assert window_bounds("2026-08-15", "rolling60d") == ("2026-06-16", "2026-08-15")
        assert window_bounds("2026-08-15", "rolling90d") == ("2026-05-17", "2026-08-15")

    def test_unknown_window_raises(self):
        with pytest.raises(ValueError):
            window_bounds("2026-08-15", "rolling15d")


class TestDeriveBaselineTalentWindow:
    def test_counts_single_and_walk_correctly(self):
        pitches = [
            _pitch(events="single", pitchCallType="in_play", launchSpeed=95.0, launchAngle=10.0),
            _pitch(events="walk", pitchCallType="ball"),
        ]
        result = derive_baseline_talent_window(pitches, None, "2026-12-31")
        assert result["status"] == "AVAILABLE"
        assert result["PA"] == 2
        assert result["AB"] == 1
        assert result["H"] == 1
        assert result["1B"] == 1
        assert result["BB"] == 1
        assert result["AVG"] == 1.0

    def test_home_run_and_strikeout(self):
        pitches = [
            _pitch(events="home_run", pitchCallType="in_play", launchSpeed=108.0, launchAngle=28.0),
            _pitch(events="strikeout", pitchCallType="swinging_strike"),
        ]
        result = derive_baseline_talent_window(pitches, None, "2026-12-31")
        assert result["HR"] == 1
        assert result["K"] == 1
        assert result["PA"] == 2
        assert result["AB"] == 2
        assert result["SLG"] == 2.0  # one HR in 2 AB -> 4 total bases / 2 AB

    def test_empty_window_reports_missing_data_not_fabricated_zeroes(self):
        result = derive_baseline_talent_window([], None, "2026-12-31")
        assert result["PA"] == 0
        assert result["status"] == "MISSING_DATA"

    def test_intent_walk_counts_as_bb(self):
        pitches = [_pitch(events="intent_walk", pitchCallType="ball")]
        result = derive_baseline_talent_window(pitches, None, "2026-12-31")
        assert result["BB"] == 1
        assert result["IBB"] == 1

    def test_window_bounds_filter_pa_out_of_range(self):
        pitches = [
            _pitch(gameDate="2026-01-01", events="single", pitchCallType="in_play"),
            _pitch(gameDate="2026-08-01", events="single", pitchCallType="in_play"),
        ]
        result = derive_baseline_talent_window(pitches, "2026-07-01", "2026-12-31")
        assert result["PA"] == 1

    def test_unrecognized_event_excluded_and_reported(self):
        pitches = [_pitch(events="some_new_event_type", pitchCallType="in_play")]
        result = derive_baseline_talent_window(pitches, None, "2026-12-31")
        assert result["PA"] == 0
        assert result["status"] == "MISSING_DATA"


class TestDerivePlateDiscipline:
    def test_swing_contact_whiff_rates(self):
        pitches = [
            _pitch(pitchCallType="ball"),
            _pitch(pitchCallType="called_strike"),
            _pitch(pitchCallType="swinging_strike"),
            _pitch(pitchCallType="foul"),
            _pitch(pitchCallType="in_play"),
        ]
        result = derive_plate_discipline(pitches)
        assert result["sampleSize"] == 5
        assert result["swingPct"] == 60.0  # 3 of 5
        assert result["whiffPct"] == pytest.approx(33.3, abs=0.1)  # 1 of 3 swings
        assert result["contactPct"] == pytest.approx(66.7, abs=0.1)  # 2 of 3 swings

    def test_zone_vs_chase(self):
        in_zone_take = _pitch(plateX=0.0, plateZ=2.5, pitchCallType="called_strike")
        out_zone_swing = _pitch(plateX=2.0, plateZ=2.5, pitchCallType="swinging_strike")
        result = derive_plate_discipline([in_zone_take, out_zone_swing])
        assert result["oSwingPct"] == 100.0
        assert result["zSwingPct"] == 0.0

    def test_empty_input(self):
        assert derive_plate_discipline([]) == {"sampleSize": 0}


class TestDeriveContactQuality:
    def test_hard_hit_and_sweet_spot_thresholds(self):
        pitches = [
            _pitch(pitchCallType="in_play", launchSpeed=96.0, launchAngle=15.0),  # hard hit + sweet spot
            _pitch(pitchCallType="in_play", launchSpeed=80.0, launchAngle=45.0),  # neither
        ]
        result = derive_contact_quality(pitches)
        assert result["sampleSize"] == 2
        assert result["hardHitPct"] == 50.0
        assert result["sweetSpotPct"] == 50.0
        assert result["maxEV"] == 96.0

    def test_non_batted_balls_excluded(self):
        pitches = [_pitch(pitchCallType="ball"), _pitch(pitchCallType="called_strike")]
        result = derive_contact_quality(pitches)
        assert result == {"sampleSize": 0}

    def test_xba_and_xwobacon_averaged(self):
        pitches = [
            _pitch(pitchCallType="in_play", estimatedBA=0.500, estimatedWOBA=0.800),
            _pitch(pitchCallType="in_play", estimatedBA=0.100, estimatedWOBA=0.200),
        ]
        result = derive_contact_quality(pitches)
        assert result["xBA"] == 0.300
        assert result["xwOBAcon"] == 0.500

    def test_batted_ball_type_distribution(self):
        pitches = [
            _pitch(pitchCallType="in_play", battedBallType="ground_ball"),
            _pitch(pitchCallType="in_play", battedBallType="fly_ball"),
            _pitch(pitchCallType="in_play", battedBallType="ground_ball"),
        ]
        result = derive_contact_quality(pitches)
        dist = result["battedBallTypeDistribution"]
        assert dist["groundBallPct"] == pytest.approx(66.7, abs=0.1)
        assert dist["flyBallPct"] == pytest.approx(33.3, abs=0.1)


class TestDerivePitchTypeBreakdown:
    def test_groups_by_family_correctly(self):
        pitches = [
            _pitch(pitchType="FF", pitchCallType="swinging_strike"),
            _pitch(pitchType="SL", pitchCallType="foul"),
            _pitch(pitchType="FT", pitchCallType="ball"),  # sinker family
        ]
        breakdown = derive_pitch_type_breakdown(pitches)
        assert "four_seam" in breakdown
        assert "slider" in breakdown
        assert "sinker" in breakdown
        assert breakdown["four_seam"]["discipline"]["sampleSize"] == 1

    def test_never_mixes_families(self):
        pitches = [_pitch(pitchType="FF"), _pitch(pitchType="SL")]
        breakdown = derive_pitch_type_breakdown(pitches)
        assert breakdown["four_seam"]["discipline"]["sampleSize"] == 1
        assert breakdown["slider"]["discipline"]["sampleSize"] == 1


class TestDeriveVelocityBreakdown:
    def test_buckets_within_family_only(self):
        pitches = [
            _pitch(pitchType="FF", releaseSpeed=94.0),  # 93-95
            _pitch(pitchType="FF", releaseSpeed=98.0),  # 97-99
            _pitch(pitchType="SL", releaseSpeed=87.0),  # slider -- excluded entirely
        ]
        breakdown = derive_velocity_breakdown(pitches)
        assert "four_seam" in breakdown
        assert "93-95" in breakdown["four_seam"]
        assert "97-99" in breakdown["four_seam"]
        assert "slider" not in breakdown

    def test_no_fastball_pitches_yields_empty_breakdown(self):
        pitches = [_pitch(pitchType="CH", releaseSpeed=85.0)]
        assert derive_velocity_breakdown(pitches) == {}


class TestDeriveLocationSummary:
    def test_zone_frequency_sums_to_100(self):
        pitches = [
            _pitch(plateX=0.0, plateZ=2.5),   # Heart
            _pitch(plateX=3.0, plateZ=2.5),   # Waste
        ]
        result = derive_location_summary(pitches)
        assert result["sampleSize"] == 2
        assert sum(result["zoneFrequency"].values()) == pytest.approx(100.0, abs=0.1)

    def test_empty_when_no_location_data(self):
        pitches = [_pitch(plateX=None, plateZ=None)]
        assert derive_location_summary(pitches) == {"sampleSize": 0}


class TestDeriveCountStateBreakdown:
    def test_two_strikes_bucket_populated_correctly(self):
        pitches = [_pitch(balls=0, strikes=2), _pitch(balls=1, strikes=0)]
        breakdown = derive_count_state_breakdown(pitches)
        assert breakdown["twoStrikes"]["sampleSize"] == 1
        assert breakdown["isFirstPitch"]["sampleSize"] == 0

    def test_hitter_and_pitcher_ahead_buckets(self):
        pitches = [_pitch(balls=3, strikes=0), _pitch(balls=0, strikes=2)]
        breakdown = derive_count_state_breakdown(pitches)
        assert breakdown["hitterAhead"]["sampleSize"] == 1
        assert breakdown["pitcherAhead"]["sampleSize"] == 1


class TestCompareWindows:
    def test_computes_delta(self):
        recent = {"whiffPct": 30.0}
        baseline = {"whiffPct": 20.0}
        result = compare_windows(recent, baseline, ["whiffPct"])
        assert result["whiffPct"]["delta"] == 10.0

    def test_none_when_either_side_missing(self):
        result = compare_windows({"whiffPct": None}, {"whiffPct": 20.0}, ["whiffPct"])
        assert result["whiffPct"]["delta"] is None


def _batted_ball(hit_coord_x, hit_coord_y, batter_hand="R", **overrides):
    base = {
        "gameDate": "2026-06-01", "batterId": "1", "batterHand": batter_hand,
        "pitchType": "FF", "pitchName": "4-Seam Fastball", "releaseSpeed": 95.0,
        "balls": 0, "strikes": 0, "pitchCallType": "in_play", "events": "field_out",
        "hitCoordX": hit_coord_x, "hitCoordY": hit_coord_y,
        "launchSpeed": 90.0, "launchAngle": 15.0, "battedBallType": "fly_ball",
    }
    base.update(overrides)
    return base


class TestDeriveSprayProfile:
    def test_continuous_spray_angle_deterministic(self):
        pitches = [_batted_ball(80, 150), _batted_ball(170, 150)]
        a = derive_spray_profile(pitches)
        b = derive_spray_profile(pitches)
        assert a == b
        assert "meanSprayAngleDeg" in a
        assert isinstance(a["meanSprayAngleDeg"], float)

    def test_pull_center_oppo_respects_rhb_handedness(self):
        # RHB pulling to LF (hitCoordX well left of the 125.42 origin)
        rhb_pull = _batted_ball(80, 150, batter_hand="R")
        result = derive_spray_profile([rhb_pull])
        assert result["sprayDistribution"]["pullPct"] == 100.0

    def test_pull_center_oppo_respects_lhb_handedness(self):
        # Same physical location, but a LHB hitting the ball toward the
        # 3B/LF side (hitCoordX < origin) is going OPPOSITE field, not pull.
        lhb_oppo = _batted_ball(80, 150, batter_hand="L")
        result = derive_spray_profile([lhb_oppo])
        assert result["sprayDistribution"]["oppoPct"] == 100.0

    def test_hr_spray_distribution_only_from_home_runs(self):
        pitches = [
            _batted_ball(80, 150, events="home_run"),
            _batted_ball(170, 150, events="field_out"),
        ]
        result = derive_spray_profile(pitches)
        assert result["hrSampleSize"] == 1
        assert result["hrSprayDistribution"]["pullPct"] == 100.0

    def test_damaging_air_ball_excludes_weak_contact(self):
        pitches = [
            _batted_ball(80, 150, launchSpeed=97.0, launchAngle=20.0, battedBallType="fly_ball"),  # damaging
            _batted_ball(80, 150, launchSpeed=70.0, launchAngle=5.0, battedBallType="ground_ball"),  # not damaging, not even air
        ]
        result = derive_spray_profile(pitches)
        assert result["damagingAirBallSprayDistribution"]["pullPct"] == 100.0

    def test_ev_and_la_by_direction(self):
        pitches = [
            _batted_ball(80, 150, launchSpeed=100.0, launchAngle=25.0),  # pull
            _batted_ball(170, 150, launchSpeed=80.0, launchAngle=5.0),   # oppo
        ]
        result = derive_spray_profile(pitches)
        assert result["evByDirection"]["pull"] == 100.0
        assert result["evByDirection"]["oppo"] == 80.0

    def test_missing_hand_or_coordinates_excluded(self):
        pitches = [_batted_ball(80, 150, batter_hand=None), _batted_ball(None, None)]
        result = derive_spray_profile(pitches)
        assert result == {"sampleSize": 0}

    def test_empty_input(self):
        assert derive_spray_profile([]) == {"sampleSize": 0}
