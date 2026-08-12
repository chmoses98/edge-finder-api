#!/usr/bin/env python3
"""
tests/test_park_geometry.py
==============================
Unit tests for lib/research/park_geometry.py -- Hitter Projection
Engine Phase 3 canonical park-geometry reference.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.research.park_geometry import resolve_park_geometry, field_relative_direction


class TestResolveParkGeometry:
    def test_known_team_resolves(self):
        entry = resolve_park_geometry("COL")
        assert entry is not None
        assert entry["venueName"] == "Coors Field"
        assert entry["altitudeFt"] == 5280

    def test_unknown_team_returns_none_not_fabricated(self):
        assert resolve_park_geometry("ZZZ") is None

    def test_deterministic_across_repeated_calls(self):
        a = resolve_park_geometry("NYY")
        b = resolve_park_geometry("NYY")
        assert a == b

    def test_all_30_teams_present(self):
        expected = {
            "NYY", "TOR", "BOS", "BAL", "TB", "CLE", "DET", "CWS", "MIN", "KC",
            "TEX", "HOU", "SEA", "LAA", "ATH", "ATL", "PHI", "NYM", "WSH", "MIA",
            "MIL", "CHC", "STL", "CIN", "PIT", "LAD", "SD", "SF", "ARI", "COL",
        }
        for team in expected:
            assert resolve_park_geometry(team) is not None, f"missing {team}"

    def test_orientation_confidence_flagged_approximate(self):
        entry = resolve_park_geometry("BOS")
        assert entry["orientationConfidence"] == "approximate_unverified"

    def test_as_of_resolves_versioned_entry(self):
        entry = resolve_park_geometry("BAL", as_of="2026-06-01")
        assert entry["effectiveFrom"] <= "2026-06-01"

    def test_as_of_before_earliest_entry_still_returns_best_available(self):
        entry = resolve_park_geometry("COL", as_of="1900-01-01")
        assert entry is not None  # falls back to earliest known entry rather than None


class TestFieldRelativeDirection:
    def test_bearing_matching_orientation_is_toward_cf(self):
        assert field_relative_direction(bearing_from_home_plate_deg=90, orientation_deg=90) == "toward_cf"

    def test_bearing_opposite_orientation_is_toward_plate(self):
        assert field_relative_direction(bearing_from_home_plate_deg=270, orientation_deg=90) == "toward_plate"

    def test_bearing_90_right_of_orientation_is_toward_rf(self):
        assert field_relative_direction(bearing_from_home_plate_deg=180, orientation_deg=90) == "toward_rf"

    def test_bearing_90_left_of_orientation_is_toward_lf(self):
        assert field_relative_direction(bearing_from_home_plate_deg=0, orientation_deg=90) == "toward_lf"

    def test_deterministic(self):
        a = field_relative_direction(45, 30)
        b = field_relative_direction(45, 30)
        assert a == b
