#!/usr/bin/env python3
"""
tests/test_validate_slate_final_atomic_writes.py
=====================================================
Phase 8 Part 14 coverage: verifies the migration of
scripts/validate_slate_final.py's two JSON writes (execution_slip_*.json
and the slate.json patch) from plain `open()+json.dump()` to
lib.atomic_json.write_json_atomic() preserves byte-identical output
while adding crash-safety (no truncated file, no leaked temp file).

Not migrated (documented, not tested as a defect): the plain
execution_slip_*.txt write. write_json_atomic() only handles JSON
payloads, and this repo has no generic atomic-text-write helper --
introducing one for this single call site would be new abstraction the
mission doesn't ask for. "Do not introduce cross-file transactional
behavior unless it already exists" -- the mission also does not ask
for the .txt and .json writes (or the slate.json patch) to become one
atomic transaction across files; each remains its own independent
best-effort write, exactly as before.
"""
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(ROOT, "scripts")
LIB_DIR = os.path.join(ROOT, "lib")
sys.path.insert(0, LIB_DIR)
sys.path.insert(0, SCRIPTS_DIR)
sys.path.insert(0, os.path.join(ROOT, "tests"))

from test_validate_slate_final_immutable import make_good_game, make_slate  # noqa: E402


@pytest.fixture
def vsf():
    if "validate_slate_final" in sys.modules:
        del sys.modules["validate_slate_final"]
    import validate_slate_final as _vsf
    return _vsf


def _wire(vsf, tmp_path, monkeypatch, date='2026-06-16'):
    data_dir = tmp_path / 'data'
    data_dir.mkdir()
    (tmp_path / 'scripts').mkdir(exist_ok=True)
    monkeypatch.setattr(vsf, '__file__', str(tmp_path / 'scripts' / 'validate_slate_final.py'))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, 'argv', ['validate_slate_final.py', date])
    return data_dir


class TestByteFormatEquivalence:

    def test_execution_slip_json_uses_indent_2_no_sort_keys(self, vsf, tmp_path, monkeypatch):
        """
        write_json_atomic(payload, path, indent=2) must reproduce the
        exact byte format json.dump(payload, f, indent=2) (no
        sort_keys) always produced -- key insertion order preserved,
        two-space indent, no trailing content beyond the JSON itself.
        """
        data_dir = _wire(vsf, tmp_path, monkeypatch)
        g = make_good_game()
        with open(data_dir / 'slate.json', 'w') as f:
            json.dump(make_slate([g]), f)
        with pytest.raises(SystemExit):
            vsf.main()

        raw = (data_dir / 'execution_slip_2026-06-16.json').read_text()
        # Manually re-serialize with the exact call site's own semantics
        # and confirm the on-disk bytes match a plain json.dumps(..., indent=2).
        parsed = json.loads(raw)
        expected = json.dumps(parsed, indent=2)
        assert raw == expected
        assert list(parsed.keys())[:2] == ['generatedAt', 'date']  # insertion order preserved

    def test_slate_json_patch_uses_indent_2(self, vsf, tmp_path, monkeypatch):
        data_dir = _wire(vsf, tmp_path, monkeypatch)
        g = make_good_game()
        with open(data_dir / 'slate.json', 'w') as f:
            json.dump(make_slate([g]), f)
        with pytest.raises(SystemExit):
            vsf.main()

        raw = (data_dir / 'slate.json').read_text()
        parsed = json.loads(raw)
        expected = json.dumps(parsed, indent=2)
        assert raw == expected


class TestNoLeakedTempFiles:

    def test_successful_run_leaves_no_tmp_files_in_data_dir(self, vsf, tmp_path, monkeypatch):
        data_dir = _wire(vsf, tmp_path, monkeypatch)
        g = make_good_game()
        with open(data_dir / 'slate.json', 'w') as f:
            json.dump(make_slate([g]), f)
        with pytest.raises(SystemExit):
            vsf.main()
        leftover = [p for p in data_dir.iterdir() if '.tmp' in p.name or p.name.startswith('.')]
        assert leftover == []

    def test_write_json_atomic_failure_does_not_leak_a_tmp_file(self, vsf, tmp_path, monkeypatch):
        """
        Simulates a crash inside write_json_atomic() (e.g. disk full
        during json.dump) via a monkeypatched os.fsync that always
        raises -- the temp file must be removed by write_json_atomic()'s
        own except-and-cleanup path, and the failure must be caught by
        main()'s outer try/except as a [SLIP] Warning, never
        propagating and never leaving a stray file in data/. os.fsync
        is patched unconditionally (not just the first call) since
        main() also calls into lib.pipeline_artifacts.write_stage_artifact()
        for the validation.json artifact earlier in the same run, which
        shares the same os.fsync -- both writes are expected to fail
        the same way here, each caught by its own independent
        try/except (Part 10's failure isolation).
        """
        data_dir = _wire(vsf, tmp_path, monkeypatch)
        g = make_good_game()
        with open(data_dir / 'slate.json', 'w') as f:
            json.dump(make_slate([g]), f)

        import atomic_json

        def _always_fails(fd):
            raise OSError('simulated disk-full during fsync')

        monkeypatch.setattr(atomic_json.os, 'fsync', _always_fails)

        with pytest.raises(SystemExit) as exc_info:
            vsf.main()
        assert exc_info.value.code == 0  # slip-persistence failure never changes exit code

        leftover = [p for p in data_dir.glob('**/*.tmp')]
        assert leftover == []
        assert not (data_dir / 'execution_slip_2026-06-16.json').exists()


class TestDestinationAbsentAndPreExisting:

    def test_execution_slip_json_written_when_destination_absent(self, vsf, tmp_path, monkeypatch):
        data_dir = _wire(vsf, tmp_path, monkeypatch)
        g = make_good_game()
        with open(data_dir / 'slate.json', 'w') as f:
            json.dump(make_slate([g]), f)
        assert not (data_dir / 'execution_slip_2026-06-16.json').exists()
        with pytest.raises(SystemExit):
            vsf.main()
        assert (data_dir / 'execution_slip_2026-06-16.json').exists()

    def test_execution_slip_json_overwritten_when_destination_pre_exists(self, vsf, tmp_path, monkeypatch):
        data_dir = _wire(vsf, tmp_path, monkeypatch)
        g = make_good_game()
        with open(data_dir / 'slate.json', 'w') as f:
            json.dump(make_slate([g]), f)
        stale_path = data_dir / 'execution_slip_2026-06-16.json'
        stale_path.write_text('{"stale": true}')
        with pytest.raises(SystemExit):
            vsf.main()
        content = json.loads(stale_path.read_text())
        assert 'stale' not in content
        assert content['date'] == '2026-06-16'

    def test_slate_json_patch_overwrites_pre_existing_content_via_atomic_replace(self, vsf, tmp_path, monkeypatch):
        data_dir = _wire(vsf, tmp_path, monkeypatch)
        g = make_good_game()
        with open(data_dir / 'slate.json', 'w') as f:
            json.dump(make_slate([g]), f)
        inode_before = os.stat(data_dir / 'slate.json').st_ino
        with pytest.raises(SystemExit):
            vsf.main()
        # os.replace() on the same filesystem always changes the inode
        # (new temp file swapped in) -- confirms the atomic path was
        # actually used, not an in-place truncate+write.
        inode_after = os.stat(data_dir / 'slate.json').st_ino
        assert inode_after != inode_before
