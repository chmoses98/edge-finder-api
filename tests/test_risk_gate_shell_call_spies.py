#!/usr/bin/env python3
"""
tests/test_risk_gate_shell_call_spies.py
===========================================
PR #8 hardening review, Part F/O: proves the thin impure shells
(apply_tt_safety, apply_portfolio_rules) invoke their pure counterparts
(evaluate_candidate_tt_risk, build_risk_portfolio) EXACTLY the intended
number of times -- not zero (dead code), not twice (double-computed
decision, the exact anti-pattern Part O calls a blocker), using call
spies (wrapping the real function and counting invocations), not merely
comparing return values.
"""

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from test_risk_gate_immutable import make_entry, make_tt_entry, make_game, make_slate, NOW


@pytest.fixture
def rg():
    if "risk_gate" in sys.modules:
        del sys.modules["risk_gate"]
    import risk_gate as _rg
    return _rg


class TestCallSpies:

    def test_evaluate_candidate_tt_risk_called_exactly_once_per_tt_candidate(self, rg, monkeypatch):
        calls = []
        real = rg.evaluate_candidate_tt_risk

        def _spy(entry):
            calls.append(entry.get('ticker'))
            return real(entry)

        monkeypatch.setattr(rg, "evaluate_candidate_tt_risk", _spy)

        e1 = make_tt_entry(tier='HIGH', edge=4.0, ticker='T1')
        e2 = make_tt_entry(tier='HIGH', edge=1.0, ticker='T2', side='Home')
        ml = make_entry(market='ML_Away', ticker='M1')  # non-TT, must NOT trigger a call
        slate = make_slate([make_game('A', 'B', [e1, e2, ml])])

        rg.apply_tt_safety(slate, now_ts=NOW)

        assert calls == ['T1', 'T2'], f"expected exactly one call per TT candidate, got {calls}"

    def test_build_risk_portfolio_called_exactly_once_per_apply_portfolio_rules_call(self, rg, monkeypatch):
        calls = {'n': 0}
        real = rg.build_risk_portfolio

        def _spy(real_entries):
            calls['n'] += 1
            return real(real_entries)

        monkeypatch.setattr(rg, "build_risk_portfolio", _spy)

        e1 = make_entry(market='ML_Away', tier='HIGH', edge=4.0, stake=5.0)
        slate = make_slate([make_game('A', 'B', [e1])])

        decision, report = rg.apply_portfolio_rules(slate, now_ts=NOW)

        assert calls['n'] == 1, f"expected exactly 1 call to build_risk_portfolio, got {calls['n']}"

    def test_compute_tt_inputs_called_exactly_once_per_tt_candidate_via_evaluate(self, rg, monkeypatch):
        """evaluate_candidate_tt_risk() internally calls compute_tt_inputs()
        exactly once -- not twice (which would mean the ttInputs block and
        the requiredRunsToWin/reasons decision were computed from two
        DIFFERENT snapshots that could theoretically disagree, however
        unlikely given both are pure)."""
        calls = {'n': 0}
        real = rg.compute_tt_inputs

        def _spy(entry):
            calls['n'] += 1
            return real(entry)

        monkeypatch.setattr(rg, "compute_tt_inputs", _spy)

        entry = make_tt_entry(tier='HIGH', edge=4.0)
        rg.evaluate_candidate_tt_risk(entry)

        assert calls['n'] == 1

    def test_apply_tt_safety_does_not_call_build_risk_portfolio(self, rg, monkeypatch):
        """Cross-contamination guard: the TT-safety pass must never reach
        into the portfolio decision function."""
        def _boom(*a, **kw):
            raise AssertionError("apply_tt_safety must never call build_risk_portfolio")
        monkeypatch.setattr(rg, "build_risk_portfolio", _boom)
        entry = make_tt_entry(tier='HIGH', edge=4.0)
        slate = make_slate([make_game('A', 'B', [entry])])
        rg.apply_tt_safety(slate, now_ts=NOW)

    def test_apply_portfolio_rules_does_not_call_evaluate_candidate_tt_risk(self, rg, monkeypatch):
        """Cross-contamination guard: the portfolio pass must never
        re-run the TT-safety decision function on any entry."""
        def _boom(*a, **kw):
            raise AssertionError("apply_portfolio_rules must never call evaluate_candidate_tt_risk")
        monkeypatch.setattr(rg, "evaluate_candidate_tt_risk", _boom)
        entry = make_entry(market='ML_Away', tier='HIGH', edge=4.0, stake=5.0)
        slate = make_slate([make_game('A', 'B', [entry])])
        rg.apply_portfolio_rules(slate, now_ts=NOW)

    def test_main_calls_apply_tt_safety_and_apply_portfolio_rules_exactly_once_each(self, rg, tmp_path, monkeypatch):
        import json
        import pipeline_artifacts as pa
        original_root = pa.PIPELINE_ROOT
        pa.PIPELINE_ROOT = str(tmp_path / 'pipeline_root')

        calls = {'tt': 0, 'portfolio': 0}
        real_tt = rg.apply_tt_safety
        real_portfolio = rg.apply_portfolio_rules

        def _spy_tt(*a, **kw):
            calls['tt'] += 1
            return real_tt(*a, **kw)

        def _spy_portfolio(*a, **kw):
            calls['portfolio'] += 1
            return real_portfolio(*a, **kw)

        monkeypatch.setattr(rg, "apply_tt_safety", _spy_tt)
        monkeypatch.setattr(rg, "apply_portfolio_rules", _spy_portfolio)

        slate_path = str(tmp_path / 'slate.json')
        meta_path = str(tmp_path / 'meta.json')
        rg.SLATE_PATH = slate_path
        rg.META_PATH = meta_path
        entry = make_entry(market='ML_Away')
        with open(slate_path, 'w') as f:
            json.dump(make_slate([make_game('A', 'B', [entry])]), f)

        try:
            rg.main()
        finally:
            pa.PIPELINE_ROOT = original_root

        assert calls == {'tt': 1, 'portfolio': 1}
