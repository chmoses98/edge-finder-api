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


class TestNeverWiredIntoCoreProductionBettingLogic:
    """As of the MLB-RSCH-0019 uncertainty-capture INFRASTRUCTURE PR, this
    schema IS deliberately wired in -- but ONLY through the isolated
    research-capture path (lib.edgelab.research.uncertainty_prospective_capture,
    called from scripts/edgelab/run_prospective_snapshots.py's own
    fail-safe, strictly-after-core-writes step). It must still NEVER be
    imported by any CORE production/betting module -- market ledger
    probability/edge computation, recommendations, risk gates, staking,
    bankroll, or lib.edgelab.prospective_snapshot's own orchestration
    core (which has no awareness of the capture step at all; the calling
    SCRIPT wires it in as a separate, independent step)."""

    CORE_PRODUCTION_MODULES = (
        os.path.join("lib", "edgelab", "prospective_snapshot.py"),
        os.path.join("lib", "edgelab", "model_evaluation.py"),
        os.path.join("scripts", "build_market_ledger.py"),
        os.path.join("scripts", "write_pending_bets.py"),
        os.path.join("scripts", "risk_gate.py"),
    )
    LEGITIMATE_IMPORTERS = (
        os.path.join("tests", "edgelab", "test_uncertainty_capture_schema.py"),
        os.path.join("tests", "edgelab", "test_uncertainty_prospective_capture.py"),
        # Reads REQUIRED_FIELDS only to assert the schema did NOT change while
        # the E4 sidecar PERSISTENCE defect was repaired -- a guard on this
        # module, not a consumer of it.
        os.path.join("tests", "edgelab", "test_prospective_sidecar_persistence.py"),
        os.path.join("lib", "edgelab", "research"),  # the schema itself + uncertainty_prospective_capture.py
    )

    def test_no_core_production_module_imports_this_schema(self):
        """grep the whole repo for any reference to uncertainty_capture_schema
        and confirm every hit is either this test, the research package
        itself, or its dedicated capture-wiring test -- never a core
        production/betting module."""
        result = subprocess.run(
            ["grep", "-rl", "uncertainty_capture_schema", _ROOT,
             "--include=*.py", "--exclude-dir=.git"],
            capture_output=True, text=True, timeout=30,
        )
        importing_files = [
            ln for ln in result.stdout.splitlines()
            if ln.strip() and not any(legit in ln for legit in self.LEGITIMATE_IMPORTERS)
        ]
        assert importing_files == [], f"uncertainty_capture_schema is referenced outside its authorized research-capture path: {importing_files}"

    def test_core_production_modules_never_reference_it(self):
        for rel_path in self.CORE_PRODUCTION_MODULES:
            full_path = os.path.join(_ROOT, rel_path)
            if not os.path.exists(full_path):
                continue
            source = open(full_path).read()
            assert "uncertainty_capture_schema" not in source, f"{rel_path} must never reference uncertainty_capture_schema directly"
            assert "uncertainty_prospective_capture" not in source, f"{rel_path} must never reference the capture-building module directly"

    def test_prospective_snapshot_orchestration_core_has_no_awareness_of_capture(self):
        """The wiring lives in the CALLING SCRIPT (run_prospective_snapshots.py),
        strictly after run_prospective_snapshot_cycle already returned --
        the orchestration core itself must remain totally unaware of the
        capture step's existence."""
        prospective_path = os.path.join(_ROOT, "lib", "edgelab", "prospective_snapshot.py")
        source = open(prospective_path).read()
        assert "uncertainty" not in source.lower()
