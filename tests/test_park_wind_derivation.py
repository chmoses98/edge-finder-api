#!/usr/bin/env python3
"""
tests/test_park_wind_derivation.py
=====================================
Unit tests for lib/research/park_wind_derivation.py -- Hitter Projection
Engine Phase 3 field-relative wind.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.research.park_wind_derivation import (
    wind_field_relative_components, build_field_relative_wind_context,
)


class TestWindFieldRelativeComponents:
    def test_wind_from_cf_side_blowing_toward_cf_is_blowing_out(self):
        # orientation 90 (CF bearing east); wind FROM the west (270) blows TOWARD east (90) -- straight out.
        result = wind_field_relative_components(wind_speed=10, wind_deg_from=270, orientation_deg=90)
        assert result["label"] == "blowing_out"
        assert result["componentTowardCF"] == 10.0

    def test_wind_from_plate_side_is_blowing_in(self):
        # wind FROM the east (90) blows TOWARD the west (270) -- straight in from CF toward plate.
        result = wind_field_relative_components(wind_speed=10, wind_deg_from=90, orientation_deg=90)
        assert result["label"] == "blowing_in"
        assert result["componentTowardCF"] == -10.0

    def test_deterministic(self):
        a = wind_field_relative_components(12, 200, 45)
        b = wind_field_relative_components(12, 200, 45)
        assert a == b

    def test_missing_inputs_return_none_not_fabricated(self):
        assert wind_field_relative_components(None, 200, 45) is None
        assert wind_field_relative_components(12, None, 45) is None
        assert wind_field_relative_components(12, 200, None) is None


class TestBuildFieldRelativeWindContext:
    def test_available_for_open_air_park_with_full_weather(self):
        weather = {"dome": False, "wind": 10, "windDeg": 270}
        result = build_field_relative_wind_context(weather, "NYY", as_of="2026-06-01")
        assert result["status"] == "AVAILABLE"
        assert "componentTowardCF" in result

    def test_dome_flag_disables_outdoor_wind(self):
        weather = {"dome": True}
        result = build_field_relative_wind_context(weather, "SEA", as_of="2026-06-01")
        assert result["status"] == "NOT_APPLICABLE"

    def test_fixed_dome_park_always_not_applicable_even_if_weather_says_open(self):
        """Tropicana Field (TB) is a fixed, permanently-closed roof -- outdoor wind never applies, regardless of the weather feed."""
        weather = {"dome": False, "wind": 15, "windDeg": 100}
        result = build_field_relative_wind_context(weather, "TB", as_of="2026-06-01")
        assert result["status"] == "NOT_APPLICABLE"
        assert result["reason"] == "fixed_dome_roof"

    def test_missing_weather_returns_missing_data(self):
        result = build_field_relative_wind_context(None, "NYY", as_of="2026-06-01")
        assert result["status"] == "MISSING_DATA"

    def test_unknown_team_returns_missing_data(self):
        weather = {"dome": False, "wind": 10, "windDeg": 270}
        result = build_field_relative_wind_context(weather, "ZZZ", as_of="2026-06-01")
        assert result["status"] == "MISSING_DATA"

    def test_orientation_confidence_surfaced(self):
        weather = {"dome": False, "wind": 10, "windDeg": 270}
        result = build_field_relative_wind_context(weather, "NYY", as_of="2026-06-01")
        assert result["orientationConfidence"] == "approximate_unverified"
