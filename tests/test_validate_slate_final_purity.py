#!/usr/bin/env python3
"""
tests/test_validate_slate_final_purity.py
=============================================
Booby-trap + AST purity proofs for scripts/validate_slate_final.py's
Phase 8 Part 6/7 pure functions: _diagnostic_lines_pure(),
_validate_games_pure(), validate_final_pure(), _route_games_into_slip_buckets(),
_fmt_real_money_entry(), _format_slip_lines(), and build_execution_slip_pure().

Each booby-trap test monkeypatches a side-effect primitive to raise if
touched, then calls a pure function through a representative scenario
and asserts it completes without raising -- proving no file I/O, no
environment reads, no clock reads, no printing, no process exit, no
network, no sleeping, and no mutation of arguments.

The AST tests statically walk each pure function's source for
forbidden ast.Call nodes (open, print, sys.exit, os.system, subprocess,
socket, time.sleep, input, eval, exec) as a second, independent proof
that does not depend on a booby-trapped scenario happening to exercise
every code path -- matching the technique from the Phase 7 hardening
review.

datetime.now cannot be monkeypatched directly on the built-in type, so
the _NoClockDatetime subclass-substitution technique (established in
the PR #7 review of post_fetch_gate.py, reused across Phase 7) is used
here even though none of these pure functions currently reference
`datetime` directly -- it is cheap insurance against a future edit
silently introducing a clock read.
"""
import ast
import copy
import inspect
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
sys.path.insert(0, os.path.join(ROOT, "tests"))

from test_validate_slate_final_immutable import (  # noqa: E402
    make_good_game, make_slate, NOW,
)


@pytest.fixture
def vsf():
    if "validate_slate_final" in sys.modules:
        del sys.modules["validate_slate_final"]
    import validate_slate_final as _vsf
    return _vsf


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
def booby_trapped(monkeypatch, vsf):
    """
    Applies every booby trap at once. os.environ is deliberately NOT
    trapped here, matching test_risk_gate_purity.py's rationale:
    globally replacing os.environ with a raising object corrupts
    pytest's own internals. validate_slate_final.py's only env-var
    read (GITHUB_OUTPUT, inside write_github_output()) is outside the
    pure functions under test here and is covered elsewhere.
    """
    monkeypatch.setattr("builtins.open", _NoOpenBuiltins())
    monkeypatch.setattr("builtins.print", _no_print)
    monkeypatch.setattr(sys, "exit", _no_sys_exit)
    monkeypatch.setattr(time, "sleep", _no_sleep)
    monkeypatch.setattr(socket, "socket", _NoNetworkSocket)
    monkeypatch.setattr(vsf, "datetime", _NoClockDatetime)
    return vsf


class TestValidateFinalPurePurity:

    def test_no_side_effects_touched_valid_game(self, booby_trapped):
        slate = make_slate([make_good_game()])
        report = booby_trapped.validate_final_pure(slate, '2026-06-16')
        assert report['errors'] == []

    def test_no_side_effects_touched_failing_game(self, booby_trapped):
        g = make_good_game()
        g['marketLedger'] = []
        slate = make_slate([g])
        report = booby_trapped.validate_final_pure(slate, '2026-06-16')
        assert report['errors']

    def test_no_side_effects_touched_no_games(self, booby_trapped):
        report = booby_trapped.validate_final_pure(make_slate([]), '2026-06-16')
        assert report['errors']

    def test_does_not_mutate_slate_argument(self, booby_trapped):
        slate = make_slate([make_good_game()])
        before = copy.deepcopy(slate)
        booby_trapped.validate_final_pure(slate, '2026-06-16')
        assert slate == before

    def test_deterministic_output(self, vsf):
        slate = make_slate([make_good_game()])
        r1 = vsf.validate_final_pure(slate, '2026-06-16')
        r2 = vsf.validate_final_pure(slate, '2026-06-16')
        assert r1 == r2


class TestBuildExecutionSlipPurePurity:

    def test_no_side_effects_touched(self, booby_trapped):
        lines, slip_dict = booby_trapped.build_execution_slip_pure(
            [make_good_game()], '2026-06-16', current_utc=NOW,
        )
        assert slip_dict['summary']['realMoneyCount'] >= 0

    def test_no_side_effects_touched_live_game(self, booby_trapped):
        lines, slip_dict = booby_trapped.build_execution_slip_pure(
            [make_good_game(status='In Progress')], '2026-06-16', current_utc=NOW,
        )
        assert slip_dict['liveGameBlockedGames']

    def test_no_side_effects_touched_empty_games(self, booby_trapped):
        lines, slip_dict = booby_trapped.build_execution_slip_pure(
            [], '2026-06-16', current_utc=NOW,
        )
        assert slip_dict['summary']['realMoneyCount'] == 0

    def test_does_not_mutate_games_argument(self, booby_trapped):
        games = [make_good_game()]
        before = copy.deepcopy(games)
        booby_trapped.build_execution_slip_pure(games, '2026-06-16', current_utc=NOW)
        assert games == before

    def test_deterministic_output(self, vsf):
        games = [make_good_game()]
        lines1, dict1 = vsf.build_execution_slip_pure(games, '2026-06-16', current_utc=NOW)
        lines2, dict2 = vsf.build_execution_slip_pure(games, '2026-06-16', current_utc=NOW)
        assert lines1 == lines2
        assert dict1 == dict2


# ══════════════════════════════════════════════════════════════════════════════
# AST static purity checks
# ══════════════════════════════════════════════════════════════════════════════

FORBIDDEN_CALL_NAMES = {
    'open', 'print', 'input', 'eval', 'exec', 'compile',
    '__import__',
}
FORBIDDEN_ATTR_CALLS = {
    ('sys', 'exit'), ('os', 'system'), ('os', 'popen'), ('os', 'remove'),
    ('os', 'unlink'), ('os', 'rename'), ('subprocess', 'run'),
    ('subprocess', 'call'), ('subprocess', 'Popen'), ('time', 'sleep'),
    ('socket', 'socket'), ('urllib', 'urlopen'), ('requests', 'get'),
    ('requests', 'post'),
}

PURE_FUNCTION_NAMES = [
    '_diagnostic_lines_pure',
    '_validate_games_pure',
    'validate_final_pure',
    '_route_games_into_slip_buckets',
    '_fmt_real_money_entry',
    '_format_slip_lines',
    'build_execution_slip_pure',
]


def _walk_calls(func):
    src = inspect.getsource(func)
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        callee = node.func
        if isinstance(callee, ast.Name):
            yield callee.id
        elif isinstance(callee, ast.Attribute) and isinstance(callee.value, ast.Name):
            yield f'{callee.value.id}.{callee.attr}'


@pytest.mark.parametrize('func_name', PURE_FUNCTION_NAMES)
def test_pure_function_contains_no_forbidden_calls(vsf, func_name):
    func = getattr(vsf, func_name)
    called = list(_walk_calls(func))
    for name in called:
        assert name not in FORBIDDEN_CALL_NAMES, (
            f'{func_name} calls forbidden primitive {name!r}'
        )
        if '.' in name:
            mod, attr = name.split('.', 1)
            assert (mod, attr) not in FORBIDDEN_ATTR_CALLS, (
                f'{func_name} calls forbidden primitive {name!r}'
            )


@pytest.mark.parametrize('func_name', PURE_FUNCTION_NAMES)
def test_pure_function_body_has_no_import_statements(vsf, func_name):
    """
    A local `import` inside a pure function is not automatically a
    purity violation, but every documented I/O escape hatch in this
    file's legacy code (json/io/builtins/datetime local imports inside
    the old generate_execution_slip()) happened via exactly this
    pattern -- guard against it recurring inside the pure core.
    """
    func = getattr(vsf, func_name)
    src = inspect.getsource(func)
    tree = ast.parse(src)
    for node in ast.walk(tree):
        assert not isinstance(node, (ast.Import, ast.ImportFrom)), (
            f'{func_name} contains a local import statement'
        )
