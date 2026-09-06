#!/usr/bin/env python3
"""
tests/test_write_pending_bets_hardening.py
===============================================
Phase 10 purity, Rule 71/81 absence, bankroll/stake-passthrough,
duplicate/correlation absence, and object-ownership coverage for
scripts/write_pending_bets.py.

Findings (documented, regression-guarded, not implemented here --
write_pending_bets.py owns none of this logic):
- Rule 71/Rule 81: zero references anywhere in write_pending_bets.py.
- Bankroll/exposure/stake-SIZING: zero references. 'betSize'/'stake'
  are read verbatim from the already-computed marketLedger entry and
  copied through into the bet record -- never computed or sized here.
- Duplicate/correlation logic: zero references. The only "duplicate"
  concept in this script is deliberate idempotency (the stable
  composite key), which is dedup-of-writes, not duplicate-market/
  correlated-market betting logic.
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
sys.path.insert(0, ROOT)
sys.path.insert(0, SCRIPTS_DIR)


@pytest.fixture
def wpb():
    if "write_pending_bets" in sys.modules:
        del sys.modules["write_pending_bets"]
    import write_pending_bets as _wpb
    return _wpb


PURE_FUNCTION_NAMES = [
    "stable_key",
    "american_to_decimal_entry",
    "build_bet_record",
    "should_skip_excluded_game_pure",
    "should_block_game_for_pregame_gate_pure",
    "is_real_money_market_entry_pure",
]

FORBIDDEN_CALL_NAMES = {"open", "print", "input", "eval", "exec", "compile", "__import__"}
FORBIDDEN_ATTR_CALLS = {
    ("sys", "exit"), ("os", "system"), ("os", "popen"), ("os", "remove"),
    ("os", "unlink"), ("os", "rename"), ("os", "chdir"), ("os", "makedirs"),
    ("subprocess", "run"), ("subprocess", "call"), ("subprocess", "Popen"),
    ("time", "sleep"), ("socket", "socket"), ("os", "getenv"), ("os", "environ"),
    ("shutil", "copy2"), ("shutil", "copy"), ("shutil", "move"),
    ("logging", "info"), ("logging", "warning"), ("logging", "error"),
    ("requests", "get"), ("requests", "post"),
}


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


def _no_getenv(*a, **kw):
    raise AssertionError("pure function read an environment variable via os.getenv()")


def _no_chdir(*a, **kw):
    raise AssertionError("pure function changed the working directory via os.chdir()")


class _NoClockDatetime:
    @classmethod
    def now(cls, *a, **kw):
        raise AssertionError("pure function read the clock via datetime.now()")


@pytest.fixture
def booby_trapped(monkeypatch, wpb):
    monkeypatch.setattr("builtins.open", _NoOpenBuiltins())
    monkeypatch.setattr("builtins.print", _no_print)
    monkeypatch.setattr(sys, "exit", _no_sys_exit)
    monkeypatch.setattr(time, "sleep", _no_sleep)
    monkeypatch.setattr(socket, "socket", _NoNetworkSocket)
    monkeypatch.setattr(os, "getenv", _no_getenv)
    monkeypatch.setattr(os, "chdir", _no_chdir)
    monkeypatch.setattr(wpb, "datetime", _NoClockDatetime)
    return wpb


class TestPurity:

    def test_should_skip_excluded_game_no_side_effects(self, booby_trapped):
        assert booby_trapped.should_skip_excluded_game_pure({"excludedFromSlate": True}) is True
        assert booby_trapped.should_skip_excluded_game_pure({}) is False

    def test_should_block_pregame_gate_no_side_effects(self, booby_trapped):
        assert booby_trapped.should_block_game_for_pregame_gate_pure(
            {"shouldSkip": True, "liveGameBlocked": True}
        ) is True
        assert booby_trapped.should_block_game_for_pregame_gate_pure(
            {"shouldSkip": False}
        ) is False
        # Regression (2026-07-30 production incident): shouldSkip=True must
        # block regardless of the specific reason string -- previously this
        # was False for skipReason="postponed", allowing a Delayed Start
        # game's real-money bets to be logged.
        assert booby_trapped.should_block_game_for_pregame_gate_pure(
            {"shouldSkip": True, "liveGameBlocked": False, "skipReason": "postponed"}
        ) is True

    def test_is_real_money_market_entry_no_side_effects(self, booby_trapped):
        assert booby_trapped.is_real_money_market_entry_pure(
            {"status": "Accepted", "confidenceTier": "HIGH"}
        ) is True
        assert booby_trapped.is_real_money_market_entry_pure(
            {"status": "Accepted", "confidenceTier": "LOW"}
        ) is False
        assert booby_trapped.is_real_money_market_entry_pure(
            {"status": "Rejected", "confidenceTier": "HIGH"}
        ) is False

    def test_stable_key_no_side_effects(self, booby_trapped):
        assert booby_trapped.stable_key("2026-06-16", "KC@WSH", "ML_Away", "TICK") == "2026-06-16|KC@WSH|ML_Away|TICK"

    def test_american_to_decimal_entry_no_side_effects(self, booby_trapped):
        assert booby_trapped.american_to_decimal_entry(-120) is not None
        assert booby_trapped.american_to_decimal_entry(None) is None

    def test_build_bet_record_no_side_effects(self, booby_trapped):
        entry = {"market": "ML_Away", "confidenceTier": "HIGH", "status": "Accepted",
                  "kalshiPrice": -120, "ticker": "T-1"}
        record = booby_trapped.build_bet_record("2026-06-16", "KC@WSH", entry, "2026-06-16T00:00:00+00:00")
        assert record["date"] == "2026-06-16"

    @pytest.mark.parametrize("func_name", PURE_FUNCTION_NAMES)
    def test_no_forbidden_calls_ast(self, wpb, func_name):
        func = getattr(wpb, func_name)
        src = inspect.getsource(func)
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            callee = node.func
            if isinstance(callee, ast.Name):
                assert callee.id not in FORBIDDEN_CALL_NAMES, f"{func_name} calls {callee.id!r}"
            elif isinstance(callee, ast.Attribute) and isinstance(callee.value, ast.Name):
                pair = (callee.value.id, callee.attr)
                assert pair not in FORBIDDEN_ATTR_CALLS, f"{func_name} calls {pair}"

    @pytest.mark.parametrize("func_name", PURE_FUNCTION_NAMES)
    def test_no_local_imports(self, wpb, func_name):
        func = getattr(wpb, func_name)
        tree = ast.parse(inspect.getsource(func))
        for node in ast.walk(tree):
            assert not isinstance(node, (ast.Import, ast.ImportFrom)), f"{func_name} has a local import"

    def test_should_skip_excluded_game_deterministic(self, wpb):
        game = {"excludedFromSlate": True}
        assert wpb.should_skip_excluded_game_pure(game) == wpb.should_skip_excluded_game_pure(game)

    def test_should_block_pregame_gate_deterministic(self, wpb):
        r = {"shouldSkip": True, "liveGameBlocked": True}
        assert wpb.should_block_game_for_pregame_gate_pure(r) == wpb.should_block_game_for_pregame_gate_pure(r)

    def test_is_real_money_market_entry_deterministic(self, wpb):
        e = {"status": "Accepted", "confidenceTier": "HIGH"}
        assert wpb.is_real_money_market_entry_pure(e) == wpb.is_real_money_market_entry_pure(e)


class TestObjectOwnership:

    def test_should_skip_excluded_game_does_not_mutate(self, wpb):
        game = {"excludedFromSlate": True, "away": {"abbr": "KC"}}
        before = copy.deepcopy(game)
        wpb.should_skip_excluded_game_pure(game)
        assert game == before

    def test_should_block_pregame_gate_does_not_mutate(self, wpb):
        result = {"shouldSkip": True, "liveGameBlocked": True, "skipReason": "LIVE_GAME_BLOCKED"}
        before = copy.deepcopy(result)
        wpb.should_block_game_for_pregame_gate_pure(result)
        assert result == before

    def test_is_real_money_market_entry_does_not_mutate(self, wpb):
        entry = {"status": "Accepted", "confidenceTier": "high"}
        before = copy.deepcopy(entry)
        wpb.is_real_money_market_entry_pure(entry)
        assert entry == before

    def test_build_bet_record_does_not_mutate_entry_argument(self, wpb):
        entry = {"market": "ML_Away", "confidenceTier": "HIGH", "status": "Accepted",
                  "kalshiPrice": -120, "ticker": "T-1", "betSize": 5.0}
        before = copy.deepcopy(entry)
        wpb.build_bet_record("2026-06-16", "KC@WSH", entry, "ts")
        assert entry == before

    def test_build_bet_record_returns_fresh_dict_each_call(self, wpb):
        entry = {"market": "ML_Away", "confidenceTier": "HIGH", "status": "Accepted",
                  "kalshiPrice": -120, "ticker": "T-1"}
        r1 = wpb.build_bet_record("2026-06-16", "KC@WSH", entry, "ts")
        r2 = wpb.build_bet_record("2026-06-16", "KC@WSH", entry, "ts")
        assert r1 == r2
        r1["mutated"] = True
        assert "mutated" not in r2


class TestRule71Rule81Absence:

    def test_no_rule71_reference(self):
        src = inspect.getsource(sys.modules.get("write_pending_bets") or __import__("write_pending_bets"))
        assert "Rule 71" not in src and "rule71" not in src.lower().replace(" ", "")

    def test_no_rule81_reference(self):
        import write_pending_bets as wpb
        src = inspect.getsource(wpb)
        assert "Rule 81" not in src and "rule81" not in src.lower().replace(" ", "")

    def test_no_bankroll_or_exposure_reference(self):
        import write_pending_bets as wpb
        src = inspect.getsource(wpb).lower()
        assert "bankroll" not in src
        assert "exposure" not in src


class TestBankrollStakePassthroughOnly:

    def test_stake_field_is_passthrough_not_computed(self, wpb):
        """
        'stake'/'betSize' in the output record must come verbatim from
        the input entry's own 'betSize' -- never derived from any other
        field (edge, odds, bankroll, etc.), proving this script performs
        no stake-sizing computation of its own.
        """
        entry = {"market": "ML_Away", "confidenceTier": "HIGH", "status": "Accepted",
                  "kalshiPrice": -120, "ticker": "T-1", "betSize": 37.5, "edge": 99.9}
        record = wpb.build_bet_record("2026-06-16", "KC@WSH", entry, "ts")
        assert record["stake"] == 37.5
        assert record["betSize"] == 37.5

    def test_stake_field_none_when_entry_has_no_betsize(self, wpb):
        entry = {"market": "ML_Away", "confidenceTier": "HIGH", "status": "Accepted",
                  "kalshiPrice": -120, "ticker": "T-1"}
        record = wpb.build_bet_record("2026-06-16", "KC@WSH", entry, "ts")
        assert record["stake"] is None
        assert record["betSize"] is None


class TestDuplicateCorrelationAbsence:
    """
    'duplicate' itself legitimately appears in this script's own
    docstring ("never duplicates", describing write-idempotency, not
    duplicate-MARKET betting logic) -- so the absence check targets the
    more specific market-selection/correlation phrases instead of the
    bare word, matching the actual concept Part 2's constraint means:
    no duplicate-market or correlated-market SELECTION logic here.
    """

    @pytest.mark.parametrize("term", ["duplicatemarket", "duplicatebet", "correlat", "kalshikey"])
    def test_term_absent(self, term):
        import write_pending_bets as wpb
        src = inspect.getsource(wpb)
        normalized = src.lower().replace(" ", "").replace("_", "")
        assert term not in normalized


class TestOneDecisionMultipleOutputs:

    def test_pregame_gate_result_computed_once_reused_for_print_and_skip(self, wpb, tmp_path, monkeypatch):
        """
        check_game_status() is called exactly once per game inside
        main()'s loop; both the skip decision and the printed block
        reason/status read from that SAME dict, never a second call.
        """
        import json
        (tmp_path / "data").mkdir()
        monkeypatch.setattr(wpb, "SLATE_PATH", str(tmp_path / "data" / "slate.json"))
        monkeypatch.setattr(wpb, "BETS_PATH", str(tmp_path / "bets.json"))

        call_count = {"n": 0}
        real_check = wpb.check_game_status

        def spy(*a, **kw):
            call_count["n"] += 1
            return real_check(*a, **kw)

        monkeypatch.setattr(wpb, "check_game_status", spy)

        game = {
            "away": {"abbr": "KC"}, "home": {"abbr": "WSH"}, "status": "In Progress",
            "marketLedger": [{"market": "ML_Away", "confidenceTier": "HIGH", "status": "Accepted",
                               "ticker": "T-1", "kalshiPrice": -120}],
        }
        with open(tmp_path / "data" / "slate.json", "w") as f:
            json.dump({"date": "2026-06-16", "games": [game]}, f)

        wpb.main()
        assert call_count["n"] == 1
