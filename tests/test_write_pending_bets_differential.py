#!/usr/bin/env python3
"""
tests/test_write_pending_bets_differential.py
==================================================
Phase 10 differential harness for scripts/write_pending_bets.py: runs
the FROZEN ORIGINAL implementation
(tests/_legacy_snapshots/write_pending_bets_phase10_base.py, captured
from pre-Phase-10 main) side-by-side with the current (refactored)
implementation against identical fixtures, asserting identical stdout,
exit code, and bets.json content.

Loaded under a separate module name via importlib so it does not
collide with the real `write_pending_bets` import (matching the
technique established in every prior phase's hardening review). Both
sides import lib.postponed_guard the same way (via sys.path insertion
of ROOT/lib) -- the legacy side does not import any new Phase 10
helper.
"""
import importlib.util
import io
import json
import os
import re
import sys
from contextlib import redirect_stderr, redirect_stdout

import pytest

_ISO_TS_RE = re.compile(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?\+00:00')


def _normalize(text):
    return _ISO_TS_RE.sub('<TS>', text)


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(ROOT, "scripts")
LIB_DIR = os.path.join(ROOT, "lib")
LEGACY_SNAPSHOT = os.path.join(ROOT, "tests", "_legacy_snapshots", "write_pending_bets_phase10_base.py")
sys.path.insert(0, ROOT)
sys.path.insert(0, SCRIPTS_DIR)


def _load_legacy():
    # The frozen snapshot lives in tests/_legacy_snapshots/, so its own
    # `sys.path.insert(0, dirname(dirname(abspath(__file__))) + '/lib')`
    # line would resolve to tests/lib (nonexistent) rather than the real
    # ROOT/lib -- pre-seed LIB_DIR onto sys.path before exec so the
    # `from postponed_guard import ...` line at module scope succeeds
    # regardless of where the frozen file physically sits on disk.
    if LIB_DIR not in sys.path:
        sys.path.insert(0, LIB_DIR)
    spec = importlib.util.spec_from_file_location("write_pending_bets_legacy_phase10", LEGACY_SNAPSHOT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_current():
    if "write_pending_bets" in sys.modules:
        del sys.modules["write_pending_bets"]
    import write_pending_bets as current
    return current


@pytest.fixture
def legacy():
    return _load_legacy()


@pytest.fixture
def current():
    return _load_current()


def make_entry(market="ML_Away", tier="HIGH", status="Accepted", ticker="KXMLB-26JUN16KCWSH-KC",
               kalshi_price=-120, exec_price=54.5, line=None):
    return {
        "market": market, "confidenceTier": tier, "status": status,
        "ticker": ticker, "marketTicker": ticker, "seriesTicker": ticker.split("-")[0] if ticker else None,
        "kalshiPrice": kalshi_price, "executablePriceUsed": exec_price,
        "edge": 4.5, "betSize": 5.0, "line": line,
        "modelProb": 60.0, "kalshiImplied": 54.0,
        "scheduledStartTime": "2026-06-16T22:46:00Z",
        "awayProjRuns": 4.5, "homeProjRuns": 3.8,
    }


def make_game(away="KC", home="WSH", entries=None, status="Scheduled", excluded=False):
    g = {
        "away": {"abbr": away}, "home": {"abbr": home}, "status": status,
        "marketLedger": entries if entries is not None else [make_entry()],
    }
    if excluded:
        g["excludedFromSlate"] = True
    return g


def make_slate(games, date="2026-06-16"):
    return {"date": date, "games": games}


def _run(mod, root_dir, monkeypatch):
    monkeypatch.setattr(mod, "SLATE_PATH", str(root_dir / "data" / "slate.json"))
    monkeypatch.setattr(mod, "BETS_PATH", str(root_dir / "bets.json"))
    buf_out, buf_err = io.StringIO(), io.StringIO()
    exit_code = None
    try:
        with redirect_stdout(buf_out), redirect_stderr(buf_err):
            exit_code = mod.main()
    except SystemExit as e:
        exit_code = e.code
    out = _normalize(buf_out.getvalue()).replace(str(root_dir), '<SANDBOX_ROOT>')
    err = _normalize(buf_err.getvalue()).replace(str(root_dir), '<SANDBOX_ROOT>')
    return exit_code, out, err


class TestWritePendingBetsDifferential:

    def _diff(self, legacy, current, tmp_path, games, monkeypatch, date="2026-06-16"):
        legacy_root = tmp_path / "legacy"
        current_root = tmp_path / "current"
        for root in (legacy_root, current_root):
            (root / "data").mkdir(parents=True)
            with open(root / "data" / "slate.json", "w") as f:
                json.dump(make_slate(games, date=date), f)

        legacy_exit, legacy_out, legacy_err = _run(legacy, legacy_root, monkeypatch)
        current_exit, current_out, current_err = _run(current, current_root, monkeypatch)

        assert legacy_exit == current_exit
        assert legacy_err == current_err
        assert legacy_out == current_out

        legacy_bets_path = legacy_root / "bets.json"
        current_bets_path = current_root / "bets.json"
        assert legacy_bets_path.exists() == current_bets_path.exists()
        if legacy_bets_path.exists():
            legacy_bets = json.loads(_normalize(legacy_bets_path.read_text()))
            current_bets = json.loads(_normalize(current_bets_path.read_text()))
            assert legacy_bets == current_bets

        return legacy_exit

    def test_missing_slate_json(self, legacy, current, tmp_path, monkeypatch):
        legacy_root = tmp_path / "legacy"
        current_root = tmp_path / "current"
        (legacy_root / "data").mkdir(parents=True)
        (current_root / "data").mkdir(parents=True)
        legacy_exit, legacy_out, legacy_err = _run(legacy, legacy_root, monkeypatch)
        current_exit, current_out, current_err = _run(current, current_root, monkeypatch)
        assert legacy_exit == current_exit == 1
        assert legacy_out == current_out

    def test_malformed_json(self, legacy, current, tmp_path, monkeypatch):
        legacy_root = tmp_path / "legacy"
        current_root = tmp_path / "current"
        for root in (legacy_root, current_root):
            (root / "data").mkdir(parents=True)
            (root / "data" / "slate.json").write_text("{not valid")
        with pytest.raises(json.JSONDecodeError):
            _run(legacy, legacy_root, monkeypatch)
        with pytest.raises(json.JSONDecodeError):
            _run(current, current_root, monkeypatch)

    def test_full_pass_one_bet(self, legacy, current, tmp_path, monkeypatch):
        self._diff(legacy, current, tmp_path, [make_game()], monkeypatch)

    def test_empty_games(self, legacy, current, tmp_path, monkeypatch):
        self._diff(legacy, current, tmp_path, [], monkeypatch)

    def test_multiple_games_mixed_tiers(self, legacy, current, tmp_path, monkeypatch):
        games = [
            make_game(away="KC", home="WSH", entries=[make_entry(tier="HIGH"), make_entry(tier="LOW", market="TT_Away_Over", line=4)]),
            make_game(away="NYY", home="BOS", entries=[make_entry(tier="MEDIUM", market="ML_Home")]),
        ]
        self._diff(legacy, current, tmp_path, games, monkeypatch)

    def test_excluded_game(self, legacy, current, tmp_path, monkeypatch):
        self._diff(legacy, current, tmp_path, [make_game(excluded=True)], monkeypatch)

    def test_in_progress_game_blocked(self, legacy, current, tmp_path, monkeypatch):
        self._diff(legacy, current, tmp_path, [make_game(status="In Progress")], monkeypatch)

    def test_final_game_blocked(self, legacy, current, tmp_path, monkeypatch):
        self._diff(legacy, current, tmp_path, [make_game(status="Final")], monkeypatch)

    def test_postponed_game(self, legacy, current, tmp_path, monkeypatch):
        self._diff(legacy, current, tmp_path, [make_game(status="Postponed")], monkeypatch)

    def test_no_ticker_entry(self, legacy, current, tmp_path, monkeypatch):
        self._diff(legacy, current, tmp_path, [make_game(entries=[make_entry(ticker=None)])], monkeypatch)

    def test_null_entry_price(self, legacy, current, tmp_path, monkeypatch):
        entry = make_entry()
        entry["executablePriceUsed"] = None
        entry["executablePriceAtOutput"] = None
        entry["kalshiPrice"] = None
        self._diff(legacy, current, tmp_path, [make_game(entries=[entry])], monkeypatch)

    def test_rerun_is_idempotent(self, legacy, current, tmp_path, monkeypatch):
        games = [make_game()]
        exit1 = self._diff(legacy, current, tmp_path, games, monkeypatch)
        assert exit1 == 0
        # Second run against the SAME already-written bets.json + same slate.
        legacy_root = tmp_path / "legacy"
        current_root = tmp_path / "current"
        with open(legacy_root / "data" / "slate.json", "w") as f:
            json.dump(make_slate(games), f)
        with open(current_root / "data" / "slate.json", "w") as f:
            json.dump(make_slate(games), f)
        legacy_exit, legacy_out, legacy_err = _run(legacy, legacy_root, monkeypatch)
        current_exit, current_out, current_err = _run(current, current_root, monkeypatch)
        assert legacy_exit == current_exit == 0
        assert legacy_out == current_out
        assert "already present" in legacy_out

    @pytest.mark.parametrize("top_level_value,type_name", [
        ([1, 2, 3], "list"),
        ("just a string", "str"),
        (42, "int"),
        (None, "NoneType"),
    ])
    def test_non_dict_slate_json_raises_identical_exception(
        self, legacy, current, tmp_path, monkeypatch, top_level_value, type_name,
    ):
        legacy_root = tmp_path / "legacy"
        current_root = tmp_path / "current"
        for root in (legacy_root, current_root):
            (root / "data").mkdir(parents=True)
            with open(root / "data" / "slate.json", "w") as f:
                json.dump(top_level_value, f)

        monkeypatch.setattr(legacy, "SLATE_PATH", str(legacy_root / "data" / "slate.json"))
        monkeypatch.setattr(legacy, "BETS_PATH", str(legacy_root / "bets.json"))
        monkeypatch.setattr(current, "SLATE_PATH", str(current_root / "data" / "slate.json"))
        monkeypatch.setattr(current, "BETS_PATH", str(current_root / "bets.json"))

        with pytest.raises(AttributeError) as legacy_exc:
            legacy.main()
        with pytest.raises(AttributeError) as current_exc:
            current.main()

        expected = f"'{type_name}' object has no attribute 'get'"
        assert str(legacy_exc.value) == str(current_exc.value) == expected
