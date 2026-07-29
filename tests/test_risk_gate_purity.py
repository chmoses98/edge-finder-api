#!/usr/bin/env python3
"""
tests/test_risk_gate_purity.py
=================================
Booby-trap purity tests for scripts/risk_gate.py's Phase 7 Part 6 pure
decision functions: compute_tt_inputs(), evaluate_candidate_tt_risk(),
and build_risk_portfolio(). Each test monkeypatches a side-effect
primitive to raise if touched, then calls a pure function through a
representative scenario and asserts it completes without raising --
proving the function does no file I/O, no environment reads, no clock
reads, no printing, no process exit, no network, no sleeping, and no
mutation of its own arguments.

datetime.now cannot be monkeypatched directly on the built-in type
(confirmed via `dt.datetime.now = ...` raising
`TypeError: cannot set 'now' attribute of immutable type 'datetime.datetime'`
during the PR #7 review of post_fetch_gate.py) -- the _NoClockDatetime
subclass substitution technique from that review is reused here.
"""

import copy
import os
import socket
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(ROOT, "scripts")
LIB_DIR = os.path.join(ROOT, "lib")
sys.path.insert(0, LIB_DIR)
sys.path.insert(0, SCRIPTS_DIR)

from test_risk_gate_immutable import make_entry, make_tt_entry


@pytest.fixture
def rg():
    if "risk_gate" in sys.modules:
        del sys.modules["risk_gate"]
    import risk_gate as _rg
    return _rg


class _NoClockDatetime:
    @classmethod
    def now(cls, *a, **kw):
        raise AssertionError("pure function read the clock via datetime.now()")


class _NoOpenBuiltins:
    def __call__(self, *a, **kw):
        raise AssertionError("pure function performed file I/O via open()")


def _no_print(*a, **kw):
    raise AssertionError("pure function printed to stdout/stderr")


def _no_sys_exit(*a, **kw):
    raise AssertionError("pure function called sys.exit()")


def _no_sleep(*a, **kw):
    raise AssertionError("pure function called time.sleep()")


class _NoNetworkSocket:
    def __init__(self, *a, **kw):
        raise AssertionError("pure function opened a network socket")


@pytest.fixture
def booby_trapped(monkeypatch, rg):
    """
    Applies every booby trap at once (open, print, sys.exit, sleep,
    socket.socket, datetime.now) and returns rg for the test to call
    pure functions against. Any of these firing turns into a raised
    AssertionError, failing the test loudly rather than silently
    passing.

    Deliberately NOT trapping os.environ here: globally replacing
    os.environ with a raising object corrupts pytest's OWN internals
    (its terminal writer reads os.environ.get("PY_COLORS") when
    rendering any traceback), which produces a misleading pytest
    internal error instead of a clean test failure/pass. risk_gate.py's
    zero-env-var-reads finding (Phase 7 Part 2) is already verified
    statically via grep, not re-verified dynamically here.
    """
    monkeypatch.setattr("builtins.open", _NoOpenBuiltins())
    monkeypatch.setattr("builtins.print", _no_print)
    monkeypatch.setattr(sys, "exit", _no_sys_exit)
    monkeypatch.setattr(time, "sleep", _no_sleep)
    monkeypatch.setattr(socket, "socket", _NoNetworkSocket)
    monkeypatch.setattr(rg, "datetime", _NoClockDatetime)
    return rg


class TestComputeTtInputsPurity:

    def test_no_side_effects_touched(self, booby_trapped):
        entry = make_tt_entry(tier='HIGH', edge=4.0)
        result = booby_trapped.compute_tt_inputs(entry)
        assert result['requiredRunsToWin'] == 5

    def test_does_not_mutate_argument(self, booby_trapped):
        entry = make_tt_entry(tier='HIGH', edge=4.0)
        before = copy.deepcopy(entry)
        booby_trapped.compute_tt_inputs(entry)
        assert entry == before
        assert 'ttInputs' not in entry

    def test_deterministic_output_same_input_same_result(self, rg):
        entry = make_tt_entry(tier='HIGH', edge=4.0)
        r1 = rg.compute_tt_inputs(entry)
        r2 = rg.compute_tt_inputs(entry)
        assert r1 == r2


class TestEvaluateCandidateTtRiskPurity:

    def test_no_side_effects_touched_passing_candidate(self, booby_trapped):
        entry = make_tt_entry(tier='HIGH', edge=4.0)
        decision = booby_trapped.evaluate_candidate_tt_risk(entry)
        assert decision['evaluated'] is True
        assert decision['downgrade'] is False

    def test_no_side_effects_touched_failing_candidate(self, booby_trapped):
        entry = make_tt_entry(tier='HIGH', edge=1.0)
        entry['awayProjRuns'] = None
        decision = booby_trapped.evaluate_candidate_tt_risk(entry)
        assert decision['downgrade'] is True
        assert len(decision['reasons']) == 2

    def test_does_not_mutate_argument(self, booby_trapped):
        entry = make_tt_entry(tier='HIGH', edge=1.0)
        before = copy.deepcopy(entry)
        booby_trapped.evaluate_candidate_tt_risk(entry)
        assert entry == before

    def test_deterministic_output(self, rg):
        entry = make_tt_entry(tier='HIGH', edge=1.0)
        d1 = rg.evaluate_candidate_tt_risk(entry)
        d2 = rg.evaluate_candidate_tt_risk(entry)
        assert d1 == d2


class TestBuildRiskPortfolioPurity:

    def test_no_side_effects_touched(self, booby_trapped):
        entries = [
            ('A@B', make_entry(market='TT_Away_Over', stake=5.0, edge=4.0)),
            ('C@D', make_entry(market='ML_Away', stake=5.0, edge=4.0)),
        ]
        report, to_downgrade, decision = booby_trapped.build_risk_portfolio(entries)
        assert decision in ('GO', 'PAPER_ONLY')

    def test_does_not_mutate_argument_entries(self, booby_trapped):
        e1 = make_entry(market='TT_Away_Over', stake=5.0, edge=4.0)
        e2 = make_entry(market='TT_Home_Over', stake=5.0, edge=3.0, ticker='T2')
        e3 = make_entry(market='TT_Away_Over', stake=5.0, edge=2.6, ticker='T3')
        e4 = make_entry(market='TT_Home_Over', stake=5.0, edge=2.7, ticker='T4')
        e5 = make_entry(market='TT_Away_Over', stake=5.0, edge=2.8, ticker='T5')
        entries = [('A@B', e1), ('C@D', e2), ('E@F', e3), ('G@H', e4), ('I@J', e5)]
        before = [copy.deepcopy(e) for _, e in entries]
        booby_trapped.build_risk_portfolio(entries)
        for (_, entry), snapshot in zip(entries, before):
            assert entry == snapshot, "build_risk_portfolio must never mutate a candidate entry"

    def test_deterministic_output(self, rg):
        entries = [
            ('A@B', make_entry(market='TT_Away_Over', stake=5.0, edge=4.0)),
            ('C@D', make_entry(market='ML_Away', stake=5.0, edge=4.0)),
        ]
        r1 = rg.build_risk_portfolio(entries)
        r2 = rg.build_risk_portfolio(entries)
        assert r1[0] == r2[0]
        assert r1[2] == r2[2]

    def test_empty_input_produces_go_decision(self, rg):
        report, to_downgrade, decision = rg.build_risk_portfolio([])
        assert decision == 'GO'
        assert to_downgrade == []
        assert report['total_bets'] == 0
