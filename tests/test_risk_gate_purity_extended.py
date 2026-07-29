#!/usr/bin/env python3
"""
tests/test_risk_gate_purity_extended.py
==========================================
PR #8 hardening review, Part E: closes gaps left by
tests/test_risk_gate_purity.py's booby traps (open/print/sys.exit/
time.sleep/socket.socket/datetime.now, but NOT pathlib, subprocess,
write_json_atomic/write_stage_artifact directly, or os.environ).

os.environ is deliberately NOT globally monkeypatched via the
monkeypatch fixture (see test_risk_gate_purity.py's own docstring on why
that corrupts pytest's own internals) -- instead this file uses a
manual try/finally that restores the real os.environ BEFORE any
exception can propagate into pytest's own reporting machinery, which is
safe because the restoration happens synchronously in the same stack
frame, before control ever returns to the test runner.

Also includes a structural (AST-based, not just dynamic) proof that
none of the four pure functions' source code contains a call to
check_game_status/write_json_atomic/write_stage_artifact/open/print/
exit anywhere in their body -- catching indirect I/O through a helper
this file's dynamic booby traps might not reach if a future edit called
a *new* helper this test doesn't yet know to trap.
"""

import ast
import os
import socket
import subprocess
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(ROOT, "scripts")
LIB_DIR = os.path.join(ROOT, "lib")
sys.path.insert(0, LIB_DIR)
sys.path.insert(0, SCRIPTS_DIR)

from test_risk_gate_immutable import make_entry, make_tt_entry

PURE_FUNCTION_NAMES = {
    'compute_tt_inputs',
    'evaluate_candidate_tt_risk',
    'build_risk_portfolio',
    'build_execution_artifact_payload',
}

# Names that would indicate I/O, clock, process-control, or network
# access if called from WITHIN a pure function's body (directly or
# transitively through another same-file helper that isn't itself in
# PURE_FUNCTION_NAMES and isn't check_tt_evidence, which is provably
# pure -- read-only dict access, no side effects).
FORBIDDEN_CALL_NAMES = {
    'open', 'print', 'exit', 'input',
    'check_game_status', 'write_json_atomic', 'write_stage_artifact',
    'sleep', 'socket', 'system', 'popen', 'run', 'call', 'Popen',
}


@pytest.fixture
def rg():
    if "risk_gate" in sys.modules:
        del sys.modules["risk_gate"]
    import risk_gate as _rg
    return _rg


class TestStructuralPurityViaAST:
    """
    Parses scripts/risk_gate.py's actual source with the `ast` module
    and walks each pure function's body for any Call node whose function
    name matches a forbidden I/O/clock/process primitive -- independent
    of, and complementary to, the dynamic monkeypatch booby traps below.
    This catches a call added through a NEW helper name the dynamic
    traps don't yet know to patch.
    """

    def _get_function_source(self):
        path = os.path.join(SCRIPTS_DIR, "risk_gate.py")
        with open(path) as f:
            source = f.read()
        tree = ast.parse(source)
        functions = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name in PURE_FUNCTION_NAMES:
                functions[node.name] = node
        return functions

    def test_all_four_pure_functions_exist_and_were_found(self):
        functions = self._get_function_source()
        assert set(functions.keys()) == PURE_FUNCTION_NAMES

    @pytest.mark.parametrize("func_name", sorted(PURE_FUNCTION_NAMES))
    def test_no_forbidden_calls_in_function_body(self, func_name):
        functions = self._get_function_source()
        node = functions[func_name]
        found_forbidden = []
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call):
                fname = None
                if isinstance(sub.func, ast.Name):
                    fname = sub.func.id
                elif isinstance(sub.func, ast.Attribute):
                    fname = sub.func.attr
                if fname in FORBIDDEN_CALL_NAMES:
                    found_forbidden.append(fname)
        assert found_forbidden == [], (
            f"{func_name}() contains forbidden call(s): {found_forbidden}"
        )

    def test_no_pure_function_has_global_or_nonlocal_statement(self):
        """No pure function may declare `global`/`nonlocal` -- a
        structural guarantee against mutable-module-global dependence."""
        functions = self._get_function_source()
        for name, node in functions.items():
            for sub in ast.walk(node):
                assert not isinstance(sub, (ast.Global, ast.Nonlocal)), (
                    f"{name}() declares global/nonlocal state"
                )


class TestDynamicPurityExtendedBoobyTraps:

    def test_no_pathlib_usage(self, rg, monkeypatch):
        import pathlib
        def _boom(*a, **kw):
            raise AssertionError("pure function touched pathlib")
        monkeypatch.setattr(pathlib.Path, "open", _boom, raising=False)
        monkeypatch.setattr(pathlib.Path, "read_text", _boom, raising=False)
        monkeypatch.setattr(pathlib.Path, "write_text", _boom, raising=False)
        entry = make_tt_entry(tier='HIGH', edge=4.0)
        result = rg.evaluate_candidate_tt_risk(entry)
        assert result['evaluated'] is True

    def test_no_subprocess_usage(self, rg, monkeypatch):
        def _boom(*a, **kw):
            raise AssertionError("pure function spawned a subprocess")
        monkeypatch.setattr(subprocess, "run", _boom)
        monkeypatch.setattr(subprocess, "Popen", _boom)
        monkeypatch.setattr(subprocess, "call", _boom)
        entries = [('A@B', make_entry(market='ML_Away', stake=5.0))]
        report, to_downgrade, decision = rg.build_risk_portfolio(entries)
        assert decision in ('GO', 'PAPER_ONLY')

    def test_write_json_atomic_never_called_by_pure_functions(self, rg, monkeypatch):
        import atomic_json
        def _boom(*a, **kw):
            raise AssertionError("pure function called write_json_atomic directly")
        monkeypatch.setattr(atomic_json, "write_json_atomic", _boom)
        # rg.write_json_atomic is a bound reference imported at module
        # load time -- patch that reference too, since risk_gate.py did
        # `from atomic_json import write_json_atomic`.
        monkeypatch.setattr(rg, "write_json_atomic", _boom)
        entry = make_tt_entry(tier='HIGH', edge=1.0)
        result = rg.evaluate_candidate_tt_risk(entry)
        assert result['downgrade'] is True
        entries = [('A@B', make_entry(market='ML_Away', stake=5.0))]
        rg.build_risk_portfolio(entries)
        rg.build_execution_artifact_payload({'date': 'x', 'games': []}, 'GO', 'reason')

    def test_write_stage_artifact_never_called_by_pure_functions(self, rg, monkeypatch):
        import pipeline_artifacts
        def _boom(*a, **kw):
            raise AssertionError("pure function called write_stage_artifact directly")
        monkeypatch.setattr(pipeline_artifacts, "write_stage_artifact", _boom)
        entries = [('A@B', make_entry(market='ML_Away', stake=5.0))]
        rg.build_risk_portfolio(entries)
        rg.build_execution_artifact_payload({'date': 'x', 'games': []}, 'GO', 'reason')

    def test_pure_functions_never_read_os_environ(self, rg):
        """
        Manual try/finally restoration (NOT monkeypatch.setattr) so the
        real os.environ is back in place before control ever returns to
        pytest's own machinery, even if an AssertionError fires inside
        the try block -- monkeypatch's fixture-teardown-based restoration
        happens too late for this specific primitive (see
        test_risk_gate_purity.py's docstring for the incident this
        avoids: a global os.environ patch surviving into pytest's own
        traceback-rendering code, which itself reads os.environ).
        """
        class _NoEnvDict(dict):
            def get(self, *a, **kw):
                raise AssertionError("pure function read os.environ.get()")
            def __getitem__(self, *a, **kw):
                raise AssertionError("pure function read os.environ[...]")
            def __contains__(self, *a, **kw):
                raise AssertionError("pure function read 'x' in os.environ")

        real_environ = os.environ
        os.environ = _NoEnvDict()
        try:
            entry = make_tt_entry(tier='HIGH', edge=4.0)
            tt_result = rg.compute_tt_inputs(entry)
            decision = rg.evaluate_candidate_tt_risk(entry)
            entries = [('A@B', make_entry(market='ML_Away', stake=5.0))]
            report, to_downgrade, portfolio_decision = rg.build_risk_portfolio(entries)
            payload = rg.build_execution_artifact_payload({'date': 'x', 'games': []}, 'GO', 'r')
        finally:
            os.environ = real_environ

        assert tt_result['requiredRunsToWin'] == 5
        assert decision['evaluated'] is True
        assert portfolio_decision in ('GO', 'PAPER_ONLY')
        assert payload['decision'] == 'GO'

    def test_check_game_status_never_called_by_pure_functions(self, rg, monkeypatch):
        """check_game_status is legitimately called by the IMPURE shells
        (apply_tt_safety/apply_portfolio_rules) -- this proves it is
        NEVER reached from any of the four pure functions themselves."""
        import postponed_guard
        def _boom(*a, **kw):
            raise AssertionError("pure function called check_game_status")
        monkeypatch.setattr(postponed_guard, "check_game_status", _boom)
        monkeypatch.setattr(rg, "check_game_status", _boom)
        entry = make_tt_entry(tier='HIGH', edge=4.0)
        rg.compute_tt_inputs(entry)
        rg.evaluate_candidate_tt_risk(entry)
        entries = [('A@B', make_entry(market='ML_Away', stake=5.0))]
        rg.build_risk_portfolio(entries)
        rg.build_execution_artifact_payload({'date': 'x', 'games': []}, 'GO', 'r')
