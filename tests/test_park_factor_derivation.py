#!/usr/bin/env python3
"""
tests/test_park_factor_derivation.py
=======================================
Unit tests for lib/research/park_factor_derivation.py -- Hitter
Projection Engine Phase 3 EMPIRICAL park factors (kept structurally
separate from lib.research.park_geometry's PHYSICAL data).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.research.park_factor_derivation import derive_empirical_park_factors, MIN_PARK_PA_FOR_FACTOR


def _pa(game_pk, event, batter_hand="R"):
    return {"gamePk": game_pk, "events": event, "batterHand": batter_hand}


class TestDeriveEmpiricalParkFactors:
    def test_hr_heavy_park_gets_above_average_hr_factor(self):
        game_park_map = {}
        pitches = []
        # Park A: 600 PAs, 60 of them HR (10% HR rate)
        for i in range(540):
            pitches.append(_pa(f"A{i}", "field_out"))
        for i in range(60):
            pitches.append(_pa(f"A{i}_hr", "home_run"))
        for p in pitches:
            game_park_map[p["gamePk"]] = "PARKA"
        # Park B (league-average-ish): 600 PAs, 30 HR (5% HR rate)
        pitches_b = []
        for i in range(570):
            pitches_b.append(_pa(f"B{i}", "field_out"))
        for i in range(30):
            pitches_b.append(_pa(f"B{i}_hr", "home_run"))
        for p in pitches_b:
            game_park_map[p["gamePk"]] = "PARKB"

        result = derive_empirical_park_factors(pitches + pitches_b, game_park_map)
        assert result["PARKA"]["hrFactor"] > 100
        assert result["PARKB"]["hrFactor"] < 100
        assert result["PARKA"]["sampleSize"] == 600

    def test_small_sample_park_omitted(self):
        game_park_map = {"g1": "TINY"}
        pitches = [_pa("g1", "single") for _ in range(10)]
        assert MIN_PARK_PA_FOR_FACTOR > 10
        result = derive_empirical_park_factors(pitches, game_park_map)
        assert "TINY" not in result

    def test_no_qualifying_pitches_returns_empty(self):
        assert derive_empirical_park_factors([], {}) == {}

    def test_handedness_filter_isolates_one_hand(self):
        game_park_map = {}
        pitches = []
        for i in range(600):
            pitches.append(_pa(f"L{i}", "single", batter_hand="L"))
            game_park_map[f"L{i}"] = "PARKC"
        for i in range(600):
            pitches.append(_pa(f"R{i}", "field_out", batter_hand="R"))
            game_park_map[f"R{i}"] = "PARKC"

        result_l = derive_empirical_park_factors(pitches, game_park_map, batter_hand_filter="L")
        assert result_l["PARKC"]["sampleSize"] == 600
        assert result_l["PARKC"]["singleFactor"] > 0

    def test_unrecognized_gamePk_excluded(self):
        pitches = [_pa("unknown_game", "single")]
        result = derive_empirical_park_factors(pitches, {})  # no mapping for unknown_game
        assert result == {}


class TestEmpiricalSeparateFromGeometry:
    def test_park_factor_derivation_module_has_no_geometry_dependency(self):
        """Structural guard: this module must never IMPORT park_geometry (mentioning it in prose docs is fine) -- empirical and physical stay independently computable/ablatable."""
        import ast
        import lib.research.park_factor_derivation as mod
        tree = ast.parse(open(mod.__file__).read())
        imported_modules = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_modules.add(node.module)
        assert not any("park_geometry" in m for m in imported_modules)
        assert not hasattr(mod, "resolve_park_geometry")
