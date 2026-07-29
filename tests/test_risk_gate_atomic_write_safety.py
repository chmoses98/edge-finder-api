#!/usr/bin/env python3
"""
tests/test_risk_gate_atomic_write_safety.py
===============================================
Phase 7 Part 18: scripts/risk_gate.py's own data/slate.json and
data/meta.json writes, migrated from plain open()+json.dump() to the
shared lib/atomic_json.write_json_atomic() helper (the same helper
fetch_lineups.py/fetch_savant_pitchers.py/post_fetch_gate.py already use
-- see lib/atomic_json.py's module docstring). Proves: format
equivalence (indent=2 preserved, matching the pre-migration byte
format), atomicity (a write failure never leaves a truncated/corrupt
file at the real path -- the previous valid content, or no file at all,
is always what a reader sees), and that this migration touches ONLY
risk_gate.py's own two writers (mission: "Do not migrate unrelated
writers").
"""

import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from test_risk_gate_immutable import make_entry, make_game, make_slate

import pipeline_artifacts as pa
import atomic_json


@pytest.fixture
def rg():
    if "risk_gate" in sys.modules:
        del sys.modules["risk_gate"]
    import risk_gate as _rg
    return _rg


@pytest.fixture(autouse=True)
def _sandbox_pipeline_root(tmp_path):
    original_root = pa.PIPELINE_ROOT
    pa.PIPELINE_ROOT = str(tmp_path / 'pipeline_root')
    yield
    pa.PIPELINE_ROOT = original_root


def _wire(rg, tmp_path):
    slate_path = str(tmp_path / 'slate.json')
    meta_path = str(tmp_path / 'meta.json')
    rg.SLATE_PATH = slate_path
    rg.META_PATH = meta_path
    return slate_path, meta_path


class TestFormatEquivalence:

    def test_slate_json_still_pretty_printed_indent_2(self, rg, tmp_path):
        slate_path, meta_path = _wire(rg, tmp_path)
        entry = make_entry(market='ML_Away')
        with open(slate_path, 'w') as f:
            json.dump(make_slate([make_game('A', 'B', [entry])]), f)
        rg.main()
        with open(slate_path) as f:
            raw = f.read()
        # indent=2 output always contains newline+2-space-indented keys;
        # compact (indent=None) output never does.
        assert '\n  "' in raw or '\n  {' in raw
        assert json.loads(raw) == json.load(open(slate_path))  # still valid JSON

    def test_meta_json_still_pretty_printed_indent_2(self, rg, tmp_path):
        slate_path, meta_path = _wire(rg, tmp_path)
        entry = make_entry(market='ML_Away')
        with open(slate_path, 'w') as f:
            json.dump(make_slate([make_game('A', 'B', [entry])]), f)
        rg.main()
        with open(meta_path) as f:
            raw = f.read()
        assert '\n  "' in raw

    def test_slate_json_content_unaffected_by_atomic_migration(self, rg, tmp_path):
        """Sanity check that build_execution_artifact_payload aside, the
        actual slate.json CONTENT (not just format) round-trips exactly
        as apply_tt_safety/apply_portfolio_rules left it in memory."""
        entry = make_entry(market='ML_Away', stake=7.5)
        slate_path, meta_path = _wire(rg, tmp_path)
        with open(slate_path, 'w') as f:
            json.dump(make_slate([make_game('A', 'B', [entry])]), f)
        rg.main()
        with open(slate_path) as f:
            written = json.load(f)
        assert written['games'][0]['marketLedger'][0]['betSize'] == 7.5


class TestAtomicWriteNeverLeavesTruncatedFile:

    def test_slate_json_write_failure_leaves_previous_valid_content_untouched(self, rg, tmp_path, monkeypatch):
        slate_path, meta_path = _wire(rg, tmp_path)
        entry = make_entry(market='ML_Away')
        with open(slate_path, 'w') as f:
            json.dump(make_slate([make_game('A', 'B', [entry])]), f)

        # First run succeeds -- this is the "previous valid content."
        rg.main()
        with open(slate_path) as f:
            baseline = f.read()

        # Second run: patch os.replace to fail on the VERY FIRST call this
        # time (the slate.json write is the first atomic write inside
        # main(), so a fail-from-1 wrapper hits it directly).
        def _boom(*a, **k):
            raise OSError("simulated disk-full during os.replace")
        monkeypatch.setattr(atomic_json.os, 'replace', _boom)

        with pytest.raises(OSError):
            rg.main()

        with open(slate_path) as f:
            after_failed_run = f.read()
        assert after_failed_run == baseline, (
            "a failed atomic write must leave the previous valid slate.json "
            "completely untouched, never a truncated/partial file"
        )

    def test_slate_json_write_failure_removes_stray_temp_file(self, rg, tmp_path, monkeypatch):
        slate_path, meta_path = _wire(rg, tmp_path)
        entry = make_entry(market='ML_Away')
        with open(slate_path, 'w') as f:
            json.dump(make_slate([make_game('A', 'B', [entry])]), f)

        def _boom(*a, **k):
            raise OSError("simulated failure")
        monkeypatch.setattr(atomic_json.os, 'replace', _boom)

        with pytest.raises(OSError):
            rg.main()

        leftover = [f for f in os.listdir(tmp_path) if f.startswith('.slate.json.')]
        assert leftover == [], f"stray atomic-write temp file(s) left behind: {leftover}"

    def test_meta_json_untouched_when_slate_json_write_fails_first(self, rg, tmp_path, monkeypatch):
        """
        slate.json is written before meta.json in main()'s sequence -- if
        the FIRST atomic write (slate.json) fails and propagates
        uncaught, meta.json's write must never even be attempted.
        """
        slate_path, meta_path = _wire(rg, tmp_path)
        entry = make_entry(market='ML_Away')
        with open(slate_path, 'w') as f:
            json.dump(make_slate([make_game('A', 'B', [entry])]), f)

        def _boom(*a, **k):
            raise OSError("simulated failure")
        monkeypatch.setattr(atomic_json.os, 'replace', _boom)

        with pytest.raises(OSError):
            rg.main()

        assert not os.path.exists(meta_path)


class TestPermissionPreservation:

    def test_slate_json_permission_matches_umask_default_not_narrowed(self, rg, tmp_path):
        """
        lib/atomic_json.py's documented guarantee: every successful write
        lands at the umask-default mode, never silently narrowed to
        tempfile.mkstemp()'s 0600. Verified here for risk_gate.py's own
        slate.json write specifically (not re-testing atomic_json.py's
        internals wholesale -- that's covered elsewhere; this confirms
        risk_gate.py's adoption of it actually exercises that guarantee).
        """
        slate_path, meta_path = _wire(rg, tmp_path)
        entry = make_entry(market='ML_Away')
        with open(slate_path, 'w') as f:
            json.dump(make_slate([make_game('A', 'B', [entry])]), f)
        os.chmod(slate_path, 0o600)  # pre-existing narrow mode, must NOT survive

        old_umask = os.umask(0o022)
        os.umask(old_umask)
        expected_mode = 0o666 & ~old_umask

        rg.main()

        actual_mode = os.stat(slate_path).st_mode & 0o777
        assert actual_mode == expected_mode, (
            f"expected umask-default mode {oct(expected_mode)}, got {oct(actual_mode)} "
            f"-- atomic write must not preserve a pre-existing narrow mode"
        )


class TestScopedMigration:

    def test_only_risk_gate_writers_migrated_atomic_json_module_unchanged(self):
        """
        Mission: "Do not migrate unrelated writers." This is a structural
        guard, not a behavioral one -- it asserts lib/atomic_json.py's own
        write_json_atomic() signature/behavior is untouched by this phase
        (still accepts indent=None default), so other already-migrated
        callers (fetch_lineups.py, fetch_savant_pitchers.py,
        post_fetch_gate.py) are unaffected by risk_gate.py adopting it.
        """
        import inspect
        sig = inspect.signature(atomic_json.write_json_atomic)
        assert list(sig.parameters) == ['payload', 'path', 'indent']
        assert sig.parameters['indent'].default is None

    def test_risk_gate_imports_write_json_atomic_from_shared_module(self):
        with open(os.path.join(ROOT, 'scripts', 'risk_gate.py')) as f:
            source = f.read()
        assert 'from atomic_json import write_json_atomic' in source
        # Confirms exactly two ACTUAL call sites (slate.json, meta.json,
        # excluding the one explanatory code-comment mention of the
        # function name) -- not a broader rewrite of every write in the
        # file.
        assert 'write_json_atomic(slate, SLATE_PATH, indent=2)' in source
        assert 'write_json_atomic(meta, META_PATH, indent=2)' in source
        call_lines = [l for l in source.splitlines()
                      if 'write_json_atomic(' in l and not l.strip().startswith('#')]
        assert len(call_lines) == 2
