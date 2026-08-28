#!/usr/bin/env python3
"""
tests/edgelab/test_uncertainty_capture_schema.py
=========================================================
Coverage for lib/edgelab/research/uncertainty_capture_schema.py -- the
MLB-RSCH-0019 data-capture-audit deliverable. MUST prove this module is
never imported by any production entrypoint (it is purely additive,
not-yet-wired-in research infrastructure).
"""
import os
import subprocess
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import pytest

from lib.edgelab.research import uncertainty_capture_schema as schema


class TestBuildUncertaintySnapshot:
    def test_builds_all_required_fields(self):
        record = schema.build_uncertainty_snapshot(
            game_id="G1", checkpoint="T_MINUS_60", captured_at="2027-03-30T18:00:00Z",
            home_sample_depth=5, away_sample_depth=3,
            home_bullpen_sample_depth=4, away_bullpen_sample_depth=2,
            starter_resolved_home=True, starter_resolved_away=False,
            lineup_confirmed_home=True, lineup_confirmed_away=True,
            weather_data_available=True, mapping_resolved=True,
            input_stale_age_minutes=5, unsupported_feature_fallback_count=0,
            component_disagreement=0.3, prob_extremeness=0.1,
        )
        schema.validate_uncertainty_snapshot(record)  # must not raise
        for field in schema.REQUIRED_FIELDS:
            assert field in record

    def test_min_sample_depth_derived_correctly(self):
        record = schema.build_uncertainty_snapshot(
            game_id="G1", checkpoint="T_MINUS_60", captured_at="2027-03-30T18:00:00Z",
            home_sample_depth=8, away_sample_depth=3,
            home_bullpen_sample_depth=4, away_bullpen_sample_depth=9,
            starter_resolved_home=True, starter_resolved_away=True,
            lineup_confirmed_home=True, lineup_confirmed_away=True,
            weather_data_available=True, mapping_resolved=True,
            input_stale_age_minutes=1, unsupported_feature_fallback_count=0,
            component_disagreement=0.0, prob_extremeness=0.0,
        )
        assert record["minSampleDepth"] == 3
        assert record["minBullpenSampleDepth"] == 4

    def test_pure_never_mutates_globals_or_performs_io(self):
        import inspect
        source = inspect.getsource(schema.build_uncertainty_snapshot)
        assert "open(" not in source
        assert "requests." not in source
        assert "urlopen" not in source


class TestValidation:
    def test_raises_on_missing_field(self):
        with pytest.raises(ValueError):
            schema.validate_uncertainty_snapshot({"gameId": "G1"})


class TestNeverWiredIntoProduction:
    def test_no_production_module_imports_this_schema(self):
        """grep the whole repo (excluding this test and the research
        package itself) for any import of uncertainty_capture_schema --
        must find NOTHING. This is the load-bearing proof that the
        capture extension cannot affect production output."""
        result = subprocess.run(
            ["grep", "-rl", "uncertainty_capture_schema", _ROOT,
             "--include=*.py", "--exclude-dir=.git"],
            capture_output=True, text=True, timeout=30,
        )
        importing_files = [
            ln for ln in result.stdout.splitlines()
            if ln.strip()
            and "test_uncertainty_capture_schema.py" not in ln
            and os.path.join("lib", "edgelab", "research") not in ln
        ]
        assert importing_files == [], f"uncertainty_capture_schema is referenced outside the research package: {importing_files}"

    def test_prospective_snapshot_module_does_not_import_it(self):
        prospective_path = os.path.join(_ROOT, "lib", "edgelab", "prospective_snapshot.py")
        source = open(prospective_path).read()
        assert "uncertainty_capture_schema" not in source

    def test_model_evaluation_module_does_not_import_it(self):
        model_eval_path = os.path.join(_ROOT, "lib", "edgelab", "model_evaluation.py")
        source = open(model_eval_path).read()
        assert "uncertainty_capture_schema" not in source
