#!/usr/bin/env python3
"""
tests/test_protect_slate_hardening.py
=========================================
Phase 9 Parts 9 (purity), 11 (Rule 71/81), 12 (bankroll/stake), 13
(duplicate/correlation), 14 (live-game), 17 (one-decision-multiple-
outputs), 18 (object ownership) coverage for scripts/protect_slate.py.

Part 11/12/13 findings (documented, regression-guarded, not
implemented here -- protect_slate.py owns none of this logic):
- Rule 71/Rule 81: zero references anywhere in protect_slate.py
  (grep-confirmed). Neither lib.slate_manager nor lib.sentinel_validator
  reference either rule either.
- Bankroll/stake: zero references in protect_slate.py itself. The
  single `stake`-adjacent field (`mkt.get("betSize") or mkt.get("stake")`)
  in lib/slate_manager.py lives inside `persist_tracked_tickers()`, a
  function protect_slate.py never imports or calls (confirmed via its
  import list: only detect_run_type, save_slate, get_authoritative_path,
  authoritative_exists, load_authoritative, plus scan_for_sentinels).
- Duplicate/correlation/live-game-status logic: zero references to
  "duplicate", "correlat", "kalshiKey", "liveGame", or "postponed"
  anywhere in protect_slate.py. The only live-game-ADJACENT logic
  reachable from this script lives entirely inside
  lib.slate_manager.detect_run_type()/validate_game_for_rerun() (a
  started-game freeze check via startTime/gameDate/scheduledStartTime
  vs now_utc) -- out of scope, already covered by
  tests/test_reliability_upgrade.py's test_07/test_08.
"""
import copy
import inspect
import ast
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
def ps():
    if "protect_slate" in sys.modules:
        del sys.modules["protect_slate"]
    import protect_slate as _ps
    return _ps


# ══════════════════════════════════════════════════════════════════════════════
# Part 9/Part 4-style purity proofs
# ══════════════════════════════════════════════════════════════════════════════

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

    @classmethod
    def utcnow(cls, *a, **kw):
        raise AssertionError("pure function read the clock via datetime.utcnow()")


class _NoPathMethods:
    """
    PR #10 hardening review (Part 6): traps pathlib.Path entirely --
    any pure function that tries to construct or touch a Path (e.g.
    Path(...).exists()/.mkdir()/.open()) fails loudly rather than
    silently performing filesystem I/O the AST/booby-trap suite
    wouldn't otherwise catch, since Path is imported at module scope
    (`from pathlib import Path`) but never used by any of the four
    pure functions today -- this proves that stays true.
    """
    def __call__(self, *a, **kw):
        raise AssertionError("pure function constructed a pathlib.Path")


@pytest.fixture
def booby_trapped(monkeypatch, ps):
    monkeypatch.setattr("builtins.open", _NoOpenBuiltins())
    monkeypatch.setattr("builtins.print", _no_print)
    monkeypatch.setattr(sys, "exit", _no_sys_exit)
    monkeypatch.setattr(time, "sleep", _no_sleep)
    monkeypatch.setattr(socket, "socket", _NoNetworkSocket)
    monkeypatch.setattr(os, "getenv", _no_getenv)
    monkeypatch.setattr(os, "chdir", _no_chdir)
    monkeypatch.setattr(ps, "datetime", _NoClockDatetime)
    monkeypatch.setattr(ps, "Path", _NoPathMethods())
    return ps


PURE_FUNCTION_NAMES = [
    "_strip_sentinel_metadata",
    "evaluate_date_mismatch_pure",
    "evaluate_sentinel_gate_pure",
    "should_sync_legacy_slate_json_pure",
    "build_protection_artifact_payload",
]

FORBIDDEN_CALL_NAMES = {
    "open", "print", "input", "eval", "exec", "compile", "__import__",
    "write_stage_artifact",
}
FORBIDDEN_ATTR_CALLS = {
    ("sys", "exit"), ("os", "system"), ("os", "popen"), ("os", "remove"),
    ("os", "unlink"), ("os", "rename"), ("subprocess", "run"),
    ("subprocess", "call"), ("subprocess", "Popen"), ("time", "sleep"),
    ("socket", "socket"), ("os", "getenv"), ("os", "environ"),
    ("shutil", "copy2"), ("shutil", "copy"), ("shutil", "move"),
    # PR #10 hardening review additions (Part 6's explicit list):
    ("os", "chdir"), ("os", "makedirs"), ("os", "mkdir"),
    ("logging", "info"), ("logging", "warning"), ("logging", "error"),
    ("logging", "debug"), ("logging", "critical"),
    ("requests", "get"), ("requests", "post"), ("requests", "request"),
    ("pipeline_artifacts", "write_stage_artifact"),
}


class TestPurity:

    def test_strip_sentinel_metadata_no_side_effects(self, booby_trapped):
        result = booby_trapped._strip_sentinel_metadata({"date": "x", "_runType": "y"})
        assert result == {"date": "x"}

    def test_evaluate_date_mismatch_no_side_effects(self, booby_trapped):
        assert booby_trapped.evaluate_date_mismatch_pure({"date": "2026-06-15"}, "2026-06-16") is not None
        assert booby_trapped.evaluate_date_mismatch_pure({"date": "2026-06-16"}, "2026-06-16") is None
        assert booby_trapped.evaluate_date_mismatch_pure({}, "2026-06-16") is None

    def test_evaluate_sentinel_gate_no_side_effects(self, booby_trapped):
        result = booby_trapped.evaluate_sentinel_gate_pure([])
        assert result["runTypeOverride"] is None
        result2 = booby_trapped.evaluate_sentinel_gate_pure(
            [{"path": "x.price", "value": 19900, "type": "sentinel_american_price"}]
        )
        assert result2["runTypeOverride"] == "REJECTED_CONTAMINATED"

    def test_should_sync_legacy_slate_json_no_side_effects(self, booby_trapped):
        assert booby_trapped.should_sync_legacy_slate_json_pure("OFFICIAL_PREGAME", True) is True
        assert booby_trapped.should_sync_legacy_slate_json_pure("REJECTED_CONTAMINATED", True) is False
        assert booby_trapped.should_sync_legacy_slate_json_pure("OFFICIAL_PREGAME", False) is False

    def test_build_protection_artifact_payload_no_side_effects(self, booby_trapped):
        """
        PR #10 hardening review: build_protection_artifact_payload was
        never exercised under the booby_trapped fixture -- the other
        three pure functions each have a dedicated no-side-effects test
        but this one, despite being named explicitly in the Phase 9
        pure-function list, was missing one.
        """
        payload = booby_trapped.build_protection_artifact_payload(
            "2026-06-16", "OFFICIAL_PREGAME", [], {"savedPaths": ["x"]}, True, True,
        )
        assert payload["date"] == "2026-06-16"
        assert payload["status"] == "ok"

    @pytest.mark.parametrize("func_name", PURE_FUNCTION_NAMES)
    def test_deterministic(self, ps, func_name):
        func = getattr(ps, func_name)
        sig = inspect.signature(func)
        args = []
        for p in sig.parameters:
            if "sentinel" in p.lower():
                args.append([])
            elif "slate_data" in p:
                args.append({"date": "2026-06-16"})
            elif "date" in p.lower():
                args.append("2026-06-16")
            elif "run_type" in p:
                args.append("OFFICIAL_PREGAME")
            elif "exists" in p:
                args.append(True)
            elif p == "result":
                args.append({})
            else:
                args.append(None)
        r1 = func(*args)
        r2 = func(*args)
        assert r1 == r2

    @pytest.mark.parametrize("func_name", PURE_FUNCTION_NAMES)
    def test_no_forbidden_calls_ast(self, ps, func_name):
        func = getattr(ps, func_name)
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
    def test_no_local_imports(self, ps, func_name):
        func = getattr(ps, func_name)
        tree = ast.parse(inspect.getsource(func))
        for node in ast.walk(tree):
            assert not isinstance(node, (ast.Import, ast.ImportFrom)), f"{func_name} has a local import"

    def test_strip_sentinel_metadata_does_not_mutate_argument(self, booby_trapped):
        slate = {"date": "x", "_runType": "y"}
        before = copy.deepcopy(slate)
        booby_trapped._strip_sentinel_metadata(slate)
        assert slate == before

    def test_evaluate_sentinel_gate_does_not_mutate_argument(self, booby_trapped):
        sentinels = [{"path": "a", "value": 19900, "type": "sentinel_american_price"}]
        before = copy.deepcopy(sentinels)
        booby_trapped.evaluate_sentinel_gate_pure(sentinels)
        assert sentinels == before

    def test_mutating_returned_dict_does_not_affect_future_calls(self, ps):
        r1 = ps.evaluate_sentinel_gate_pure([])
        r1["runTypeOverride"] = "INJECTED"
        r1["sentinelLines"].append("INJECTED")
        r2 = ps.evaluate_sentinel_gate_pure([])
        assert r2["runTypeOverride"] is None
        assert r2["sentinelLines"] == []


# ══════════════════════════════════════════════════════════════════════════════
# Part 11: Rule 71/81 lockdown
# ══════════════════════════════════════════════════════════════════════════════

class TestRule71Rule81Absence:

    def test_rule_71_not_referenced(self):
        with open(os.path.join(SCRIPTS_DIR, "protect_slate.py")) as f:
            src = f.read()
        assert "Rule 71" not in src
        assert "Rule71" not in src

    def test_rule_81_not_referenced(self):
        with open(os.path.join(SCRIPTS_DIR, "protect_slate.py")) as f:
            src = f.read()
        assert "Rule 81" not in src
        assert "Rule81" not in src

    def test_rule_71_81_not_referenced_in_direct_lib_dependencies(self):
        for lib_file in ("slate_manager.py", "sentinel_validator.py"):
            with open(os.path.join(ROOT, "lib", lib_file)) as f:
                src = f.read()
            assert "Rule 71" not in src, lib_file
            assert "Rule 81" not in src, lib_file


# ══════════════════════════════════════════════════════════════════════════════
# Part 12: bankroll/stake lockdown
# ══════════════════════════════════════════════════════════════════════════════

class TestBankrollStakeAbsence:

    def test_bankroll_not_referenced_in_protect_slate(self):
        with open(os.path.join(SCRIPTS_DIR, "protect_slate.py")) as f:
            src = f.read()
        assert "bankroll" not in src.lower()

    def test_stake_not_referenced_in_protect_slate(self):
        with open(os.path.join(SCRIPTS_DIR, "protect_slate.py")) as f:
            src = f.read()
        assert "stake" not in src.lower()

    def test_persist_tracked_tickers_not_imported_or_called(self):
        """
        The one bankroll/stake-adjacent field in lib/slate_manager.py
        lives inside persist_tracked_tickers(), which protect_slate.py
        never imports or calls -- confirmed both by the import
        statement itself and by grepping for any call site.
        """
        with open(os.path.join(SCRIPTS_DIR, "protect_slate.py")) as f:
            src = f.read()
        assert "persist_tracked_tickers" not in src


# ══════════════════════════════════════════════════════════════════════════════
# Part 13: duplicate/correlation absence
# ══════════════════════════════════════════════════════════════════════════════

class TestDuplicateCorrelationAbsence:

    @pytest.mark.parametrize("term", ["duplicate", "correlat", "kalshiKey", "liveGame", "postponed"])
    def test_term_not_referenced(self, term):
        with open(os.path.join(SCRIPTS_DIR, "protect_slate.py")) as f:
            src = f.read()
        assert term.lower() not in src.lower(), term

    def test_literal_duplicate_games_processed_without_special_casing(self, ps, tmp_path, monkeypatch):
        """
        protect_slate.py has no game-identity/deduplication logic of
        its own -- a slate with two literally-identical game entries
        must scan/route through exactly the same as any other slate
        (no crash, no special dedup behavior).
        """
        import json
        (tmp_path / "data").mkdir()
        monkeypatch.setattr(ps, "ROOT_DIR", str(tmp_path))
        monkeypatch.chdir(tmp_path)  # write_stage_artifact() is CWD-relative, no root_dir param
        game = {"gameId": "1", "away": {"abbr": "KC"}, "home": {"abbr": "WSH"},
                "markets": [{"market": "ML_Away", "price": -120, "modelProb": 55.0}]}
        with open(tmp_path / "data" / "slate.json", "w") as f:
            json.dump({"date": "2026-06-16", "games": [game, copy.deepcopy(game)]}, f)
        result = ps.main("2026-06-16")
        assert result == 0


# ══════════════════════════════════════════════════════════════════════════════
# Part 14: live-game / time field audit
# ══════════════════════════════════════════════════════════════════════════════

class TestLiveGameFieldAudit:

    def test_protect_slate_itself_reads_no_game_level_timestamp_fields(self):
        """
        protect_slate.py's own code never reads startTime/gameDate/
        scheduledStartTime -- those are read only inside
        lib.slate_manager.detect_run_type()/validate_game_for_rerun(),
        out of scope for Phase 9. The only clock/time usage in
        protect_slate.py itself is the ET-approximation date fallback
        and the now_utc value threaded (as an opaque argument) into
        detect_run_type().
        """
        with open(os.path.join(SCRIPTS_DIR, "protect_slate.py")) as f:
            src = f.read()
        assert "startTime" not in src
        assert "gameDate" not in src
        assert "scheduledStartTime" not in src

    def test_now_utc_threaded_into_detect_run_type_not_recomputed(self, ps, tmp_path, monkeypatch):
        """
        main() computes now_utc exactly once and passes it explicitly
        to detect_run_type() -- confirmed by source inspection (one
        `now_utc = datetime.now(timezone.utc)` assignment, reused at
        both the print() call site and the detect_run_type() call
        site). This test proves detect_run_type() is actually called
        WITH that value via a spy, not with its own default clock read.
        """
        import json
        (tmp_path / "data").mkdir()
        monkeypatch.setattr(ps, "ROOT_DIR", str(tmp_path))
        monkeypatch.chdir(tmp_path)  # write_stage_artifact() is CWD-relative, no root_dir param
        game = {"gameId": "1", "away": {"abbr": "KC"}, "home": {"abbr": "WSH"},
                "markets": [{"market": "ML_Away", "price": -120, "modelProb": 55.0}]}
        with open(tmp_path / "data" / "slate.json", "w") as f:
            json.dump({"date": "2026-06-16", "games": [game]}, f)

        seen_now_utc = []
        original = ps.detect_run_type

        def _spy(date_str, root_dir, now_utc=None):
            seen_now_utc.append(now_utc)
            return original(date_str, root_dir, now_utc)

        monkeypatch.setattr(ps, "detect_run_type", _spy)
        ps.main("2026-06-16")
        assert len(seen_now_utc) == 1
        assert seen_now_utc[0] is not None


# ══════════════════════════════════════════════════════════════════════════════
# Part 17: one decision, multiple outputs
# ══════════════════════════════════════════════════════════════════════════════

class TestOneDecisionMultipleOutputs:

    def test_evaluate_sentinel_gate_called_exactly_once(self, ps, tmp_path, monkeypatch):
        import json
        (tmp_path / "data").mkdir()
        monkeypatch.setattr(ps, "ROOT_DIR", str(tmp_path))
        monkeypatch.chdir(tmp_path)  # write_stage_artifact() is CWD-relative, no root_dir param
        game = {"gameId": "1", "away": {"abbr": "KC"}, "home": {"abbr": "WSH"},
                "markets": [{"market": "ML_Away", "price": 19900, "modelProb": 55.0}]}
        with open(tmp_path / "data" / "slate.json", "w") as f:
            json.dump({"date": "2026-06-16", "games": [game]}, f)

        call_count = {"n": 0}
        original = ps.evaluate_sentinel_gate_pure

        def _spy(*a, **kw):
            call_count["n"] += 1
            return original(*a, **kw)

        monkeypatch.setattr(ps, "evaluate_sentinel_gate_pure", _spy)
        ps.main("2026-06-16")
        assert call_count["n"] == 1

    def test_evaluate_date_mismatch_called_exactly_once(self, ps, tmp_path, monkeypatch):
        import json
        (tmp_path / "data").mkdir()
        monkeypatch.setattr(ps, "ROOT_DIR", str(tmp_path))
        monkeypatch.chdir(tmp_path)  # write_stage_artifact() is CWD-relative, no root_dir param
        with open(tmp_path / "data" / "slate.json", "w") as f:
            json.dump({"date": "2026-06-15", "games": []}, f)

        call_count = {"n": 0}
        original = ps.evaluate_date_mismatch_pure

        def _spy(*a, **kw):
            call_count["n"] += 1
            return original(*a, **kw)

        monkeypatch.setattr(ps, "evaluate_date_mismatch_pure", _spy)
        ps.main("2026-06-16")
        assert call_count["n"] == 1

    def test_sentinel_scan_computed_once_drives_both_print_and_run_type(self, ps, tmp_path, monkeypatch):
        """
        scan_for_sentinels() itself must be called exactly once -- its
        single result feeds BOTH the printed SENTINEL DETECTED lines
        and the run_type decision, never recomputed for either output.
        """
        import json
        (tmp_path / "data").mkdir()
        monkeypatch.setattr(ps, "ROOT_DIR", str(tmp_path))
        monkeypatch.chdir(tmp_path)  # write_stage_artifact() is CWD-relative, no root_dir param
        game = {"gameId": "1", "away": {"abbr": "KC"}, "home": {"abbr": "WSH"},
                "markets": [{"market": "ML_Away", "price": 19900, "modelProb": 55.0}]}
        with open(tmp_path / "data" / "slate.json", "w") as f:
            json.dump({"date": "2026-06-16", "games": [game]}, f)

        call_count = {"n": 0}
        original = ps.scan_for_sentinels

        def _spy(*a, **kw):
            call_count["n"] += 1
            return original(*a, **kw)

        monkeypatch.setattr(ps, "scan_for_sentinels", _spy)
        ps.main("2026-06-16")
        assert call_count["n"] == 1


# ══════════════════════════════════════════════════════════════════════════════
# Part 18: object ownership
# ══════════════════════════════════════════════════════════════════════════════

class TestObjectOwnership:

    def test_evaluate_sentinel_gate_sentinel_lines_not_aliased_to_input(self, ps):
        sentinels = [{"path": "a.price", "value": 19900, "type": "sentinel_american_price"}]
        result = ps.evaluate_sentinel_gate_pure(sentinels)
        result["sentinelLines"].append("INJECTED")
        result2 = ps.evaluate_sentinel_gate_pure(sentinels)
        assert "INJECTED" not in result2["sentinelLines"]

    def test_two_calls_produce_independent_result_objects(self, ps):
        r1 = ps.evaluate_sentinel_gate_pure([])
        r2 = ps.evaluate_sentinel_gate_pure([])
        assert r1 == r2
        assert r1 is not r2
        assert r1["sentinelLines"] is not r2["sentinelLines"]

    def test_main_does_not_mutate_the_slate_dict_object_it_reads(self, ps, tmp_path, monkeypatch):
        """
        main() re-reads slate.json fresh each call (no caller-supplied
        dict) -- this proves the ON-DISK bytes are never rewritten
        during the pure-decision phase (before save_slate() runs),
        i.e. reading + scanning never itself writes anything back.
        """
        import json
        (tmp_path / "data").mkdir()
        monkeypatch.setattr(ps, "ROOT_DIR", str(tmp_path))
        monkeypatch.chdir(tmp_path)  # write_stage_artifact() is CWD-relative, no root_dir param
        game = {"gameId": "1", "away": {"abbr": "KC"}, "home": {"abbr": "WSH"},
                "markets": [{"market": "ML_Away", "price": 19900, "modelProb": 55.0}]}
        with open(tmp_path / "data" / "slate.json", "w") as f:
            json.dump({"date": "2026-06-16", "games": [game]}, f, indent=2)
        before = (tmp_path / "data" / "slate.json").read_bytes()
        ps.main("2026-06-16")
        after = (tmp_path / "data" / "slate.json").read_bytes()
        assert before == after  # sentinel-quarantined run never syncs slate.json
