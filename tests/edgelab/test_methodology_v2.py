#!/usr/bin/env python3
"""
tests/edgelab/test_methodology_v2.py
=========================================================
Coverage for lib/edgelab/research/methodology_v2.py -- the V2
selection-gate contract for future expected-run mean-model experiments.
"""
import os
import subprocess
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import pytest

from lib.edgelab.research import methodology_v2 as m2


class TestGateLogic:
    def test_passes_when_all_gates_met(self):
        passes, reasons = m2.mean_candidate_gates_v2(
            dev_mse_delta=-0.01, dev_nll_delta=-0.001, dev_brier_delta=-0.0001,
            val_mse_delta=-0.005, val_brier_delta=-0.0001,
        )
        assert passes
        assert reasons == []

    def test_fails_when_dev_mse_not_improved(self):
        passes, reasons = m2.mean_candidate_gates_v2(dev_mse_delta=0.01, dev_nll_delta=-0.001, dev_brier_delta=-0.0001)
        assert not passes
        assert any("gate 1" in r for r in reasons)

    def test_fails_when_nll_exceeds_tolerance(self):
        passes, reasons = m2.mean_candidate_gates_v2(dev_mse_delta=-0.01, dev_nll_delta=0.002, dev_brier_delta=-0.0001)
        assert not passes
        assert any("gate 2" in r for r in reasons)

    def test_fails_when_brier_exceeds_tolerance(self):
        passes, reasons = m2.mean_candidate_gates_v2(dev_mse_delta=-0.01, dev_nll_delta=-0.001, dev_brier_delta=0.001)
        assert not passes
        assert any("gate 3" in r for r in reasons)

    def test_val_gates_only_checked_when_provided(self):
        passes, _ = m2.mean_candidate_gates_v2(dev_mse_delta=-0.01, dev_nll_delta=-0.001, dev_brier_delta=-0.0001)
        assert passes  # no VAL values supplied -- gates 4/5 vacuous

    def test_val_mse_flip_fails(self):
        passes, reasons = m2.mean_candidate_gates_v2(
            dev_mse_delta=-0.01, dev_nll_delta=-0.001, dev_brier_delta=-0.0001, val_mse_delta=0.002,
        )
        assert not passes
        assert any("gate 4" in r for r in reasons)

    def test_none_deltas_fail_never_pass_silently(self):
        passes, reasons = m2.mean_candidate_gates_v2(dev_mse_delta=None, dev_nll_delta=None, dev_brier_delta=None)
        assert not passes
        assert len(reasons) == 3


class TestMaeCannotQualifyOrDisqualify:
    def test_favorable_mae_with_unfavorable_mse_still_fails(self):
        """The exact S1/B3 signature: MAE improves, everything
        mean-consistent worsens -- must FAIL under v2."""
        passes, _ = m2.mean_candidate_gates_v2(
            dev_mse_delta=0.035, dev_nll_delta=0.0017, dev_brier_delta=0.0006, dev_mae_delta=-0.0075,
        )
        assert not passes

    def test_unfavorable_mae_with_favorable_everything_else_still_passes(self):
        passes, _ = m2.mean_candidate_gates_v2(
            dev_mse_delta=-0.01, dev_nll_delta=-0.001, dev_brier_delta=-0.0001, dev_mae_delta=+0.05,
        )
        assert passes

    def test_gate_source_never_reads_dev_mae_delta(self):
        import ast, inspect
        source = inspect.getsource(m2.mean_candidate_gates_v2)
        tree = ast.parse(source)
        reads = [n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)]
        assert "dev_mae_delta" not in reads


class TestRegistrationGuard:
    def test_raises_on_mae_primary_text(self):
        with pytest.raises(ValueError):
            m2.assert_not_mae_primary("paired MAE delta on next-game team runs scored")

    def test_allows_mse_primary_text(self):
        m2.assert_not_mae_primary("paired MSE delta (primary), MAE reported as secondary interpretability metric")

    def test_allows_nll_primary_text(self):
        m2.assert_not_mae_primary("frozen-NB negative log-likelihood delta, with MAE secondary")

    def test_allows_text_without_mae_at_all(self):
        m2.assert_not_mae_primary("Brier score delta on derived market probabilities")


class TestProductionIsolation:
    def test_module_never_imported_outside_research_and_tests(self):
        result = subprocess.run(
            ["grep", "-rl", "methodology_v2", _ROOT, "--include=*.py", "--exclude-dir=.git", "--exclude-dir=__pycache__"],
            capture_output=True, text=True, timeout=30,
        )
        allowed = (os.path.join("lib", "edgelab", "research"), os.path.join("tests", "edgelab"), os.path.join("scripts", "edgelab"))
        offenders = [ln for ln in result.stdout.splitlines() if ln.strip() and not any(a in ln for a in allowed)]
        assert offenders == [], f"methodology_v2 referenced outside research/experiment scope: {offenders}"
