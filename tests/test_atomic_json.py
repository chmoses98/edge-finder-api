#!/usr/bin/env python3
"""
tests/test_atomic_json.py
============================
Standalone unit tests for lib/atomic_json.write_json_atomic() (Phase 6
Part 5) -- the shared plain-JSON atomic writer that
scripts/fetch_lineups.py's, scripts/fetch_savant_pitchers.py's, and
scripts/post_fetch_gate.py's own _write_slate_atomic()/write-back calls
all now delegate to, replacing what were three (two pre-existing plus
one new) independently-inlined copies of the same pattern.

These tests exercise the helper directly, independent of any specific
caller script -- the callers' own existing golden atomic-write test
suites (tests/test_fetch_lineups_immutable.py::TestAtomicWrite,
tests/test_fetch_savant_pitchers_immutable.py::TestAtomicWrite,
tests/test_post_fetch_gate_immutable.py) already prove each caller's
behavior is unchanged after migrating to this shared helper.
"""

import json
import os
import stat
import sys
import tempfile
import shutil

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from lib.atomic_json import write_json_atomic
import lib.atomic_json as atomic_json_module


class AtomicJsonHarness:

    def setup_method(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "data", "example.json")
        os.makedirs(os.path.dirname(self.path))

    def teardown_method(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestBasicWrite(AtomicJsonHarness):

    def test_no_prior_file_writes_successfully(self):
        write_json_atomic({"a": 1}, self.path)
        with open(self.path) as f:
            assert json.load(f) == {"a": 1}

    def test_output_matches_plain_json_dump_byte_for_byte(self):
        payload = {"date": "2026-07-27", "games": [{"a": 1}, {"b": 2}]}
        write_json_atomic(payload, self.path)
        with open(self.path) as f:
            written = f.read()
        assert written == json.dumps(payload)

    def test_repeated_writes_are_predictable_no_stray_files(self):
        for i in range(3):
            write_json_atomic({"run": i}, self.path)
        with open(self.path) as f:
            assert json.load(f) == {"run": 2}
        assert os.listdir(os.path.dirname(self.path)) == ["example.json"]

    def test_temp_file_created_in_destination_directory(self):
        created_dirs = []
        real_mkstemp = atomic_json_module.tempfile.mkstemp

        def _tracking_mkstemp(*a, **k):
            result = real_mkstemp(*a, **k)
            created_dirs.append(k.get("dir"))
            return result

        atomic_json_module.tempfile.mkstemp = _tracking_mkstemp
        try:
            write_json_atomic({"a": 1}, self.path)
        finally:
            atomic_json_module.tempfile.mkstemp = real_mkstemp

        assert [os.path.abspath(d) for d in created_dirs] == [
            os.path.abspath(os.path.dirname(self.path))
        ]


class TestFailureLeavesFileUntouched(AtomicJsonHarness):

    def _write_prior(self, content):
        with open(self.path, "w") as f:
            json.dump(content, f)

    def test_serialization_failure_leaves_prior_file_untouched(self):
        prior = {"marker": "prior-good-content"}
        self._write_prior(prior)

        class Unserializable:
            pass

        with pytest.raises(TypeError):
            write_json_atomic({"bad": Unserializable()}, self.path)

        with open(self.path) as f:
            assert json.load(f) == prior
        assert os.listdir(os.path.dirname(self.path)) == ["example.json"]

    def test_fdopen_failure_leaves_prior_file_untouched(self):
        prior = {"marker": "prior-good-content"}
        self._write_prior(prior)

        real_fdopen = atomic_json_module.os.fdopen

        def _boom(*a, **k):
            raise OSError("simulated disk write error")

        atomic_json_module.os.fdopen = _boom
        try:
            with pytest.raises(OSError):
                write_json_atomic({"a": 1}, self.path)
        finally:
            atomic_json_module.os.fdopen = real_fdopen

        with open(self.path) as f:
            assert json.load(f) == prior
        assert os.listdir(os.path.dirname(self.path)) == ["example.json"]

    def test_fsync_failure_leaves_prior_file_untouched(self):
        prior = {"marker": "prior-good-content"}
        self._write_prior(prior)

        real_fsync = atomic_json_module.os.fsync

        def _boom(*a, **k):
            raise OSError("simulated fsync failure")

        atomic_json_module.os.fsync = _boom
        try:
            with pytest.raises(OSError):
                write_json_atomic({"a": 1}, self.path)
        finally:
            atomic_json_module.os.fsync = real_fsync

        with open(self.path) as f:
            assert json.load(f) == prior
        assert os.listdir(os.path.dirname(self.path)) == ["example.json"]

    def test_chmod_failure_leaves_prior_file_untouched(self):
        prior = {"marker": "prior-good-content"}
        self._write_prior(prior)

        real_chmod = atomic_json_module.os.chmod

        def _boom(*a, **k):
            raise OSError("simulated chmod failure")

        atomic_json_module.os.chmod = _boom
        try:
            with pytest.raises(OSError):
                write_json_atomic({"a": 1}, self.path)
        finally:
            atomic_json_module.os.chmod = real_chmod

        with open(self.path) as f:
            assert json.load(f) == prior
        assert os.listdir(os.path.dirname(self.path)) == ["example.json"]

    def test_rename_failure_leaves_prior_file_untouched(self):
        prior = {"marker": "prior-good-content"}
        self._write_prior(prior)

        real_replace = atomic_json_module.os.replace

        def _boom(*a, **k):
            raise OSError("simulated rename failure (e.g. cross-device link)")

        atomic_json_module.os.replace = _boom
        try:
            with pytest.raises(OSError):
                write_json_atomic({"a": 1}, self.path)
        finally:
            atomic_json_module.os.replace = real_replace

        with open(self.path) as f:
            assert json.load(f) == prior
        assert os.listdir(os.path.dirname(self.path)) == ["example.json"]


class TestFilePermissions(AtomicJsonHarness):

    def test_permissions_match_umask_default_not_mkstemp_default(self):
        write_json_atomic({"a": 1}, self.path)
        actual_mode = stat.S_IMODE(os.stat(self.path).st_mode)
        current_umask = os.umask(0o022)
        os.umask(current_umask)
        expected_mode = 0o666 & ~current_umask
        assert actual_mode == expected_mode, (
            f"expected mode {oct(expected_mode)} (umask-default), got {oct(actual_mode)} "
            f"(tempfile.mkstemp()'s own default is 0o600 and os.replace() preserves it -- "
            f"the write must explicitly chmod before renaming)"
        )

    def test_preexisting_destination_permissions_are_not_inherited(self):
        """
        A successful atomic write always lands at the umask-default mode,
        regardless of what mode a PRE-EXISTING destination file had --
        os.replace() swaps the inode, it does not merge/preserve the old
        file's permission bits onto the new content.
        """
        with open(self.path, "w") as f:
            json.dump({"marker": "old"}, f)
        os.chmod(self.path, 0o600)

        write_json_atomic({"a": 1}, self.path)

        actual_mode = stat.S_IMODE(os.stat(self.path).st_mode)
        current_umask = os.umask(0o022)
        os.umask(current_umask)
        expected_mode = 0o666 & ~current_umask
        assert actual_mode == expected_mode
