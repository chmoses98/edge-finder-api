#!/usr/bin/env python3
"""
tests/test_pitch_taxonomy.py
================================
Unit tests for lib/research/pitch_taxonomy.py -- Hitter Projection
Engine Phase 2 canonical pitch representation.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.research.pitch_taxonomy import (
    classify_pitch_family, velocity_bucket, build_pitch_shape_profile,
    classify_zone, spatial_grid_bin, classify_count_state,
    FASTBALL_FAMILIES, VELOCITY_BUCKETS,
    PITCH_FAMILY_FOUR_SEAM, PITCH_FAMILY_SINKER, PITCH_FAMILY_CUTTER,
    PITCH_FAMILY_SLIDER, PITCH_FAMILY_SWEEPER, PITCH_FAMILY_CURVE,
    PITCH_FAMILY_KNUCKLE_CURVE, PITCH_FAMILY_CHANGEUP, PITCH_FAMILY_SPLITTER,
    PITCH_FAMILY_OTHER,
)


class TestClassifyPitchFamily:
    def test_all_required_families_map_from_code(self):
        assert classify_pitch_family(pitch_type="FF") == PITCH_FAMILY_FOUR_SEAM
        assert classify_pitch_family(pitch_type="SI") == PITCH_FAMILY_SINKER
        assert classify_pitch_family(pitch_type="FC") == PITCH_FAMILY_CUTTER
        assert classify_pitch_family(pitch_type="SL") == PITCH_FAMILY_SLIDER
        assert classify_pitch_family(pitch_type="ST") == PITCH_FAMILY_SWEEPER
        assert classify_pitch_family(pitch_type="CU") == PITCH_FAMILY_CURVE
        assert classify_pitch_family(pitch_type="KC") == PITCH_FAMILY_KNUCKLE_CURVE
        assert classify_pitch_family(pitch_type="CH") == PITCH_FAMILY_CHANGEUP
        assert classify_pitch_family(pitch_type="FS") == PITCH_FAMILY_SPLITTER

    def test_unrecognized_falls_to_other_not_none(self):
        assert classify_pitch_family(pitch_type="ZZ") == PITCH_FAMILY_OTHER
        assert classify_pitch_family() == PITCH_FAMILY_OTHER

    def test_falls_back_to_pitch_name_when_code_missing(self):
        assert classify_pitch_family(pitch_name="Sweeper") == PITCH_FAMILY_SWEEPER
        assert classify_pitch_family(pitch_type=None, pitch_name="Changeup") == PITCH_FAMILY_CHANGEUP

    def test_code_preferred_over_name(self):
        assert classify_pitch_family(pitch_type="FF", pitch_name="Slider") == PITCH_FAMILY_FOUR_SEAM


class TestVelocityBucket:
    def test_fastball_family_boundaries_correct(self):
        assert velocity_bucket(PITCH_FAMILY_FOUR_SEAM, 92.9) == "<93"
        assert velocity_bucket(PITCH_FAMILY_FOUR_SEAM, 93.0) == "93-95"
        assert velocity_bucket(PITCH_FAMILY_FOUR_SEAM, 94.9) == "93-95"
        assert velocity_bucket(PITCH_FAMILY_FOUR_SEAM, 95.0) == "95-97"
        assert velocity_bucket(PITCH_FAMILY_FOUR_SEAM, 96.9) == "95-97"
        assert velocity_bucket(PITCH_FAMILY_FOUR_SEAM, 97.0) == "97-99"
        assert velocity_bucket(PITCH_FAMILY_FOUR_SEAM, 98.9) == "97-99"
        assert velocity_bucket(PITCH_FAMILY_FOUR_SEAM, 99.0) == "99+"
        assert velocity_bucket(PITCH_FAMILY_FOUR_SEAM, 105.0) == "99+"

    def test_all_bucket_names_match_module_constant(self):
        assert VELOCITY_BUCKETS == ("<93", "93-95", "95-97", "97-99", "99+")

    def test_sinker_and_cutter_also_bucketed(self):
        assert velocity_bucket(PITCH_FAMILY_SINKER, 94.0) == "93-95"
        assert velocity_bucket(PITCH_FAMILY_CUTTER, 90.0) == "<93"

    def test_non_fastball_family_never_bucketed_even_at_fastball_speed(self):
        """An 87mph slider must never join an 87mph fastball's bucket -- returns None, not a mis-bucket."""
        assert velocity_bucket(PITCH_FAMILY_SLIDER, 87.0) is None
        assert velocity_bucket(PITCH_FAMILY_CURVE, 94.0) is None
        assert velocity_bucket(PITCH_FAMILY_CHANGEUP, 96.0) is None
        assert velocity_bucket(PITCH_FAMILY_OTHER, 95.0) is None

    def test_missing_or_non_numeric_speed_returns_none(self):
        assert velocity_bucket(PITCH_FAMILY_FOUR_SEAM, None) is None
        assert velocity_bucket(PITCH_FAMILY_FOUR_SEAM, "not_a_number") is None

    def test_fastball_families_frozenset_matches_bucketed_families(self):
        assert FASTBALL_FAMILIES == frozenset({PITCH_FAMILY_FOUR_SEAM, PITCH_FAMILY_SINKER, PITCH_FAMILY_CUTTER})


class TestPitchShapeProfile:
    def _pitch(self, **overrides):
        base = {
            "pitchType": "FF", "pitchName": "4-Seam Fastball",
            "releaseSpeed": 96.4, "inducedVertBreak": 16.2, "horizontalBreak": 8.1,
            "spinRate": 2350, "releaseHeight": 6.1, "releaseSide": -1.8,
            "extension": 6.5, "armAngle": 42.0,
        }
        base.update(overrides)
        return base

    def test_deterministic_for_equal_input(self):
        p = self._pitch()
        assert build_pitch_shape_profile(dict(p)) == build_pitch_shape_profile(dict(p))

    def test_includes_classified_family_and_raw_shape_fields(self):
        profile = build_pitch_shape_profile(self._pitch())
        assert profile["pitchFamily"] == PITCH_FAMILY_FOUR_SEAM
        assert profile["releaseSpeed"] == 96.4
        assert profile["spinRate"] == 2350
        assert profile["armAngle"] == 42.0

    def test_missing_fields_are_none_not_fabricated(self):
        profile = build_pitch_shape_profile({"pitchType": "SL"})
        assert profile["pitchFamily"] == PITCH_FAMILY_SLIDER
        assert profile["releaseSpeed"] is None
        assert profile["armAngle"] is None


class TestClassifyZone:
    def test_center_of_zone_is_heart(self):
        assert classify_zone(0.0, 2.5) == "Heart"

    def test_far_outside_is_waste(self):
        assert classify_zone(3.0, 2.5) == "Waste"

    def test_missing_coordinate_returns_none(self):
        assert classify_zone(None, 2.5) is None
        assert classify_zone(0.0, None) is None

    def test_custom_sz_top_bot_used_when_provided(self):
        # a batter with an unusually high sz_top should have the same
        # relative-center point still classified Heart
        assert classify_zone(0.0, 4.0, sz_top=5.0, sz_bot=3.0) == "Heart"


class TestSpatialGridBin:
    def test_snaps_to_grid(self):
        assert spatial_grid_bin(1.0, 2.0, grid_size=0.5) == (2, 4)
        assert spatial_grid_bin(0.24, 0.24, grid_size=0.5) == (0, 0)

    def test_missing_coordinate_returns_none(self):
        assert spatial_grid_bin(None, 2.0) is None

    def test_does_not_mutate_or_lose_original_coordinates(self):
        """Binning is additive -- the original continuous coordinate is
        never discarded or altered by computing a bin from it."""
        pitch = {"plateX": 0.734, "plateZ": 2.113}
        _ = spatial_grid_bin(pitch["plateX"], pitch["plateZ"])
        _ = classify_zone(pitch["plateX"], pitch["plateZ"])
        assert pitch["plateX"] == 0.734
        assert pitch["plateZ"] == 2.113

    def test_rejects_non_positive_grid_size(self):
        import pytest
        with pytest.raises(ValueError):
            spatial_grid_bin(1.0, 1.0, grid_size=0)


class TestClassifyCountState:
    def test_0_0_is_first_pitch(self):
        state = classify_count_state(0, 0)
        assert state["isFirstPitch"] is True
        assert state["exactCount"] == "0-0"
        assert state["hitterAhead"] is False
        assert state["pitcherAhead"] is False

    def test_0_2_and_1_2(self):
        s02 = classify_count_state(0, 2)
        assert s02["is02"] is True
        assert s02["twoStrikes"] is True
        assert s02["pitcherAhead"] is True

        s12 = classify_count_state(1, 2)
        assert s12["is12"] is True
        assert s12["twoStrikes"] is True

    def test_three_ball_count(self):
        state = classify_count_state(3, 1)
        assert state["threeBallCount"] is True
        assert state["hitterAhead"] is True

    def test_even_count(self):
        state = classify_count_state(2, 2)
        assert state["isEven"] is True
        assert state["hitterAhead"] is False
        assert state["pitcherAhead"] is False

    def test_missing_values_degrade_gracefully(self):
        state = classify_count_state(None, None)
        assert state["exactCount"] is None
        assert state["hitterAhead"] is False
        assert state["isFirstPitch"] is None
