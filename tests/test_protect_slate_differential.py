#!/usr/bin/env python3
"""
tests/test_protect_slate_differential.py
=============================================
Differential harness for the Phase 9 conversion of
scripts/protect_slate.py: runs the FROZEN ORIGINAL implementation
(tests/_legacy_snapshots/protect_slate_phase9_base.py, captured from
post-Phase-8 main before this phase's refactor) side-by-side with the
current (refactored) implementation against identical fixtures, and
asserts identical results -- stdout, stderr, exit code, and every
emitted file's bytes.

Loaded under a separate module name via importlib so it does not
collide with the real `protect_slate` import (matching the technique
established in Phase 7/8's hardening reviews). The frozen legacy
module imports lib.slate_manager / lib.sentinel_validator the SAME way
the current implementation does (both resolve `lib` via `ROOT_DIR`
inserted onto sys.path at import time) -- it does NOT import any new
production helper Phase 9 introduces.
"""
import importlib.util
import io
import json
import os
import re
import sys
from contextlib import redirect_stderr, redirect_stdout

import pytest

# main() has no injectable clock (confirmed in the Phase 9 behavior map --
# a genuine pre-existing gap, not introduced by this differential harness).
# Two real, separate calls to main() -- one for legacy, one for current --
# necessarily read the real clock at very slightly different instants, so
# ISO-8601-with-microseconds strings and the compact filename timestamp
# format must be normalized to a placeholder before any byte/text
# comparison, or every comparison would flake on timing alone.
_ISO_TS_RE = re.compile(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?\+00:00')
_COMPACT_TS_RE = re.compile(r'\d{8}T\d{6}Z')


def _normalize_timestamps(text):
    text = _ISO_TS_RE.sub('<TS>', text)
    text = _COMPACT_TS_RE.sub('<COMPACT_TS>', text)
    return text


# The ONE intentional, documented new line the current implementation
# prints that the frozen legacy snapshot never did: Phase 9 Part 16's
# additive protection.json artifact publication. Stripped before
# stdout comparison, matching the exact same pattern the Phase 8
# hardening review used for validation.json's own new artifact line.
_ARTIFACT_LINE_RE = re.compile(r'^\[protect_slate\] protection pipeline artifact written for .*\n', re.MULTILINE)


def _strip_artifact_line(text):
    return _ARTIFACT_LINE_RE.sub('', text)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(ROOT, "scripts")
LEGACY_SNAPSHOT = os.path.join(ROOT, "tests", "_legacy_snapshots", "protect_slate_phase9_base.py")
sys.path.insert(0, ROOT)
sys.path.insert(0, SCRIPTS_DIR)


def _load_legacy():
    spec = importlib.util.spec_from_file_location("protect_slate_legacy_phase9", LEGACY_SNAPSHOT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_current():
    if "protect_slate" in sys.modules:
        del sys.modules["protect_slate"]
    import protect_slate as current
    return current


@pytest.fixture
def legacy():
    return _load_legacy()


@pytest.fixture
def current():
    return _load_current()


def make_game(away="KC", home="WSH", game_id="12345", price=-120, model_prob=55.0):
    return {
        "gameId": game_id,
        "away": {"abbr": away, "pitcher": {"id": "p1", "name": "A"}, "lineup": list(range(9))},
        "home": {"abbr": home, "pitcher": {"id": "p2", "name": "B"}, "lineup": list(range(9))},
        "markets": [{"market": "ML_Away", "price": price, "modelProb": model_prob}],
    }


def make_slate(games, date="2026-06-16"):
    return {"date": date, "games": games}


def _run(mod, root_dir, date_str, monkeypatch):
    """
    Returns (exit_code, stdout, stderr) with stdout/stderr already
    normalized for BOTH timestamps and the sandbox root path itself --
    `_write_json()` prints the full absolute path it wrote, which
    embeds the "legacy"/"current" sandbox directory name distinguishing
    the two runs. That's expected sandboxing noise (same class of
    normalization the Phase 8 hardening review needed for its own
    dual-sandbox subprocess comparison), not a real behavioral
    difference -- masked here so callers never need to think about it.
    """
    monkeypatch.setattr(mod, "ROOT_DIR", str(root_dir))
    # write_stage_artifact() (Phase 9's new protection.json artifact) is
    # CWD-relative with no root_dir parameter -- chdir is required in
    # addition to the ROOT_DIR monkeypatch to keep it sandboxed too.
    monkeypatch.chdir(root_dir)
    buf_out, buf_err = io.StringIO(), io.StringIO()
    exit_code = None
    try:
        with redirect_stdout(buf_out), redirect_stderr(buf_err):
            exit_code = mod.main(date_str)
    except SystemExit as e:
        exit_code = e.code
    out = _normalize_timestamps(buf_out.getvalue().replace(str(root_dir), '<SANDBOX_ROOT>'))
    err = _normalize_timestamps(buf_err.getvalue().replace(str(root_dir), '<SANDBOX_ROOT>'))
    return exit_code, out, err


def _snapshot_tree(root_dir):
    """
    Returns {relative_path: text} for every file under root_dir/data,
    with BOTH the path itself and the file's content timestamp-
    normalized -- filenames embed a compact timestamp
    (official_<ts>.json etc.) that must be masked before comparing
    the two sandboxes' file sets, same reasoning as stdout.
    """
    result = {}
    data_dir = root_dir / "data"
    if not data_dir.exists():
        return result
    for dirpath, _, filenames in os.walk(data_dir):
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, root_dir)
            rel_normalized = _normalize_timestamps(rel)
            with open(full, "r") as f:
                content = f.read()
            result[rel_normalized] = _normalize_timestamps(content)
    return result


class TestProtectSlateDifferential:

    def _diff(self, legacy, current, tmp_path, games, date_str="2026-06-16", monkeypatch=None):
        legacy_root = tmp_path / "legacy"
        current_root = tmp_path / "current"
        for root in (legacy_root, current_root):
            (root / "data").mkdir(parents=True)
            with open(root / "data" / "slate.json", "w") as f:
                json.dump(make_slate(games, date=date_str), f)

        legacy_exit, legacy_out, legacy_err = _run(legacy, legacy_root, date_str, monkeypatch)
        current_exit, current_out, current_err = _run(current, current_root, date_str, monkeypatch)

        assert legacy_exit == current_exit
        assert _normalize_timestamps(legacy_err) == _normalize_timestamps(current_err)
        assert _normalize_timestamps(legacy_out) == _strip_artifact_line(_normalize_timestamps(current_out))

        legacy_files = _snapshot_tree(legacy_root)
        current_files = _snapshot_tree(current_root)
        # data/pipeline/<date>/protection.json is the ONE intentional new
        # file Phase 9 introduces (Part 16) -- excluded from the file-set
        # comparison, same reasoning as the stdout artifact-line strip.
        current_files_excl_artifact = {
            k: v for k, v in current_files.items() if "pipeline" not in k
        }
        assert set(legacy_files.keys()) == set(current_files_excl_artifact.keys()), (
            f"file sets differ: {set(legacy_files) ^ set(current_files_excl_artifact)}"
        )
        for rel in legacy_files:
            assert legacy_files[rel] == current_files_excl_artifact[rel], f"content mismatch in {rel}"

        return legacy_exit

    def test_missing_slate_json(self, legacy, current, tmp_path, monkeypatch):
        legacy_root = tmp_path / "legacy"
        current_root = tmp_path / "current"
        (legacy_root / "data").mkdir(parents=True)
        (current_root / "data").mkdir(parents=True)
        legacy_exit, legacy_out, legacy_err = _run(legacy, legacy_root, "2026-06-16", monkeypatch)
        current_exit, current_out, current_err = _run(current, current_root, "2026-06-16", monkeypatch)
        assert legacy_exit == current_exit == 1
        assert _normalize_timestamps(legacy_err) == _normalize_timestamps(current_err)
        assert _normalize_timestamps(legacy_out) == _normalize_timestamps(current_out)

    def test_malformed_json(self, legacy, current, tmp_path, monkeypatch):
        legacy_root = tmp_path / "legacy"
        current_root = tmp_path / "current"
        for root in (legacy_root, current_root):
            (root / "data").mkdir(parents=True)
            (root / "data" / "slate.json").write_text("{not valid")
        legacy_exit, legacy_out, legacy_err = _run(legacy, legacy_root, "2026-06-16", monkeypatch)
        current_exit, current_out, current_err = _run(current, current_root, "2026-06-16", monkeypatch)
        assert legacy_exit == current_exit == 1
        assert _normalize_timestamps(legacy_err) == _normalize_timestamps(current_err)

    def test_official_pregame_first_run(self, legacy, current, tmp_path, monkeypatch):
        self._diff(legacy, current, tmp_path, [make_game()], monkeypatch=monkeypatch)

    def test_lineup_recheck_second_run(self, legacy, current, tmp_path, monkeypatch):
        legacy_root = tmp_path / "legacy"
        current_root = tmp_path / "current"
        games = [make_game()]
        for root, mod in ((legacy_root, legacy), (current_root, current)):
            (root / "data").mkdir(parents=True)
            with open(root / "data" / "slate.json", "w") as f:
                json.dump(make_slate(games), f)
            _run(mod, root, "2026-06-16", monkeypatch)
            with open(root / "data" / "slate.json", "w") as f:
                json.dump(make_slate(games), f)

        legacy_exit, legacy_out, legacy_err = _run(legacy, legacy_root, "2026-06-16", monkeypatch)
        current_exit, current_out, current_err = _run(current, current_root, "2026-06-16", monkeypatch)
        assert legacy_exit == current_exit == 0
        assert _normalize_timestamps(legacy_err) == _normalize_timestamps(current_err)
        assert "LINEUP_RECHECK" in legacy_out
        assert "LINEUP_RECHECK" in current_out

    def test_sentinel_quarantine(self, legacy, current, tmp_path, monkeypatch):
        self._diff(legacy, current, tmp_path, [make_game(price=19900)], monkeypatch=monkeypatch)

    def test_sentinel_metadata_stripped(self, legacy, current, tmp_path, monkeypatch):
        legacy_root = tmp_path / "legacy"
        current_root = tmp_path / "current"
        slate = make_slate([make_game()])
        slate["_runType"] = "REJECTED_CONTAMINATED"
        slate["_quarantined"] = True
        for root in (legacy_root, current_root):
            (root / "data").mkdir(parents=True)
            with open(root / "data" / "slate.json", "w") as f:
                json.dump(slate, f)
        legacy_exit, legacy_out, legacy_err = _run(legacy, legacy_root, "2026-06-16", monkeypatch)
        current_exit, current_out, current_err = _run(current, current_root, "2026-06-16", monkeypatch)
        assert legacy_exit == current_exit == 0
        assert "SENTINEL PRICES DETECTED" not in legacy_out
        assert "SENTINEL PRICES DETECTED" not in current_out
        assert _normalize_timestamps(legacy_out) == _strip_artifact_line(_normalize_timestamps(current_out))

    def test_date_mismatch_warning(self, legacy, current, tmp_path, monkeypatch):
        self._diff(legacy, current, tmp_path, [make_game()], date_str="2026-06-16", monkeypatch=monkeypatch)

    def test_multiple_games_mixed(self, legacy, current, tmp_path, monkeypatch):
        games = [make_game(away="KC", home="WSH"), make_game(away="NYY", home="BOS", price=19900)]
        self._diff(legacy, current, tmp_path, games, monkeypatch=monkeypatch)

    def test_empty_games_list(self, legacy, current, tmp_path, monkeypatch):
        self._diff(legacy, current, tmp_path, [], monkeypatch=monkeypatch)

    def test_reordered_games(self, legacy, current, tmp_path, monkeypatch):
        g1 = make_game(away="AAA", home="BBB", game_id="1")
        g2 = make_game(away="CCC", home="DDD", game_id="2")
        self._diff(legacy, current, tmp_path, [g2, g1], monkeypatch=monkeypatch)

    def test_non_dict_slate_json_raises_identical_uncaught_exception(self, legacy, current, tmp_path, monkeypatch):
        """
        Regression guard for a real ordering hazard found while writing
        the Phase 9 refactor: an early version of evaluate_protection_pure()
        computed the sentinel scan BEFORE the date-mismatch check,
        reversing the original statement order. On a non-dict top-level
        slate.json (e.g. a bare JSON array), that reordering would have
        changed WHERE the resulting AttributeError originates (still
        uncaught either way -- no try/except wraps this section in
        either implementation -- but from a different line, which this
        test could not have detected without asserting on the raised
        exception directly). Both implementations must raise the
        IDENTICAL AttributeError, not merely "an AttributeError."
        """
        legacy_root = tmp_path / "legacy"
        current_root = tmp_path / "current"
        for root in (legacy_root, current_root):
            (root / "data").mkdir(parents=True)
            with open(root / "data" / "slate.json", "w") as f:
                json.dump([1, 2, 3], f)

        monkeypatch.setattr(legacy, "ROOT_DIR", str(legacy_root))
        monkeypatch.setattr(current, "ROOT_DIR", str(current_root))

        with pytest.raises(AttributeError) as legacy_exc:
            legacy.main("2026-06-16")
        with pytest.raises(AttributeError) as current_exc:
            current.main("2026-06-16")

        assert str(legacy_exc.value) == str(current_exc.value) == "'list' object has no attribute 'get'"
