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

    def test_default_indent_is_none_compact_output(self):
        """
        No `indent` argument -> byte-identical to plain json.dump(payload, f)
        with no indent -- matches fetch_lineups.py/fetch_savant_pitchers.py/
        post_fetch_gate.py's slate.json writers' pre-existing format.
        """
        payload = {"a": 1, "b": [1, 2, 3]}
        write_json_atomic(payload, self.path)
        with open(self.path) as f:
            written = f.read()
        assert written == json.dumps(payload, indent=None)
        assert "\n" not in written

    def test_explicit_indent_matches_plain_json_dump_with_indent(self):
        """
        indent=2 (added for post_fetch_gate.py's write_fetch_status(),
        which pretty-prints data/fetch_status.json) must produce
        byte-identical output to plain json.dump(payload, f, indent=2).
        """
        payload = {"status": "OK", "quarantinedGames": []}
        write_json_atomic(payload, self.path, indent=2)
        with open(self.path) as f:
            written = f.read()
        assert written == json.dumps(payload, indent=2)
        assert "\n" in written

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

    def test_flush_failure_leaves_prior_file_untouched(self):
        """
        PR #7 review, Section G: f.flush() can raise independently of
        os.fsync() (e.g. a buffered write error surfacing on flush) --
        not previously exercised. Wraps the real file object returned by
        os.fdopen() so json.dump()'s own writes still work normally, but
        .flush() raises.
        """
        prior = {"marker": "prior-good-content"}
        self._write_prior(prior)

        real_fdopen = atomic_json_module.os.fdopen

        class _FlushBoomFile:
            def __init__(self, real_file):
                self._real = real_file
            def write(self, *a, **k):
                return self._real.write(*a, **k)
            def flush(self):
                raise OSError("simulated flush failure")
            def fileno(self):
                return self._real.fileno()
            def __enter__(self):
                return self
            def __exit__(self, *a):
                self._real.close()
                return False

        def _wrapping_fdopen(fd, mode):
            return _FlushBoomFile(real_fdopen(fd, mode))

        atomic_json_module.os.fdopen = _wrapping_fdopen
        try:
            with pytest.raises(OSError):
                write_json_atomic({"a": 1}, self.path)
        finally:
            atomic_json_module.os.fdopen = real_fdopen

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

    @pytest.mark.parametrize("umask", [0o022, 0o002, 0o077])
    @pytest.mark.parametrize("preexisting_mode", [None, 0o644, 0o664, 0o600])
    def test_intended_rule_umask_default_wins_over_preexisting_mode(self, umask, preexisting_mode):
        """
        PR #7 review, Section H: the documented, intended rule is
        "umask-default permissions, always" -- NOT "preserve whatever
        mode the destination already had." Verified across all 3
        representative umasks x all 4 destination states (absent, 0644,
        0664, 0600) = 12 combinations: the final mode always equals
        0o666 & ~umask for the umask active AT CALL TIME, regardless of
        what mode (if any) a pre-existing destination file had. Code
        (lib/atomic_json.py's docstring) and this test agree on the same
        rule -- there is no ambiguity left where an existing file's mode
        could unexpectedly survive or unexpectedly change to something
        other than the umask-default.
        """
        if preexisting_mode is not None:
            with open(self.path, "w") as f:
                json.dump({"marker": "old"}, f)
            os.chmod(self.path, preexisting_mode)

        old_umask = os.umask(umask)
        try:
            write_json_atomic({"a": 1}, self.path)
        finally:
            os.umask(old_umask)

        actual_mode = stat.S_IMODE(os.stat(self.path).st_mode)
        expected_mode = 0o666 & ~umask
        assert actual_mode == expected_mode, (
            f"umask={oct(umask)} preexisting={preexisting_mode and oct(preexisting_mode)}: "
            f"expected {oct(expected_mode)}, got {oct(actual_mode)}"
        )


class TestByteIdenticalToPreConsolidationInlineImplementations(AtomicJsonHarness):
    """
    PR #7 review, Section G: reimplements the OLD inline
    _write_slate_atomic() (verbatim, as it existed independently in both
    scripts/fetch_lineups.py and scripts/fetch_savant_pitchers.py before
    this phase consolidated them onto lib/atomic_json.write_json_atomic --
    see commit 934bef1) as a local reference function, and proves the
    shared helper's success-path output is byte-identical to it. The
    only textual difference between the old inline code and the new
    helper is the temp-file prefix ('.slate.' vs '.{basename}.') -- a
    filename that never appears in the final destination file's content,
    so it cannot affect byte-identical-ness.
    """

    def _legacy_write_slate_atomic(self, slate, path):
        import json as _json
        import tempfile as _tempfile
        dest_dir = os.path.dirname(path) or '.'
        umask = os.umask(0o022)
        os.umask(umask)
        default_mode = 0o666 & ~umask
        fd, tmp_path = _tempfile.mkstemp(prefix='.slate.', suffix='.json.tmp', dir=dest_dir)
        try:
            os.chmod(tmp_path, default_mode)
            with os.fdopen(fd, 'w') as f:
                _json.dump(slate, f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, path)
        except BaseException:
            try:
                os.remove(tmp_path)
            except OSError:
                pass
            raise

    def test_success_path_output_byte_identical_to_legacy_inline_implementation(self):
        payload = {"date": "2026-07-27", "games": [{"a": 1, "nested": {"b": [1, 2, 3]}}]}
        legacy_path = os.path.join(self.tmp, "data", "legacy.json")
        new_path = os.path.join(self.tmp, "data", "new.json")

        self._legacy_write_slate_atomic(payload, legacy_path)
        write_json_atomic(payload, new_path)

        with open(legacy_path, "rb") as f:
            legacy_bytes = f.read()
        with open(new_path, "rb") as f:
            new_bytes = f.read()
        assert legacy_bytes == new_bytes

    def test_permissions_identical_to_legacy_inline_implementation(self):
        payload = {"a": 1}
        legacy_path = os.path.join(self.tmp, "data", "legacy.json")
        new_path = os.path.join(self.tmp, "data", "new.json")

        self._legacy_write_slate_atomic(payload, legacy_path)
        write_json_atomic(payload, new_path)

        assert stat.S_IMODE(os.stat(legacy_path).st_mode) == stat.S_IMODE(os.stat(new_path).st_mode)


class TestAdditionalContractProperties(AtomicJsonHarness):

    def test_no_caller_relies_on_a_private_write_slate_atomic_helper(self):
        """
        Confirms fetch_lineups.py's and fetch_savant_pitchers.py's own
        _write_slate_atomic() wrapper functions are now thin one-line
        delegates to write_json_atomic(), not independent
        implementations any caller could accidentally depend on for
        behavior the shared helper doesn't provide (e.g. a different
        temp-file prefix, a different exception type). This is a static
        source-shape check, not a behavior test -- the behavior
        equivalence is proven directly above and in each script's own
        golden-equivalence suite.
        """
        import inspect
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        scripts_dir = os.path.join(root, "scripts")
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        if "fetch_lineups" in sys.modules:
            del sys.modules["fetch_lineups"]
        if "fetch_savant_pitchers" in sys.modules:
            del sys.modules["fetch_savant_pitchers"]
        import fetch_lineups
        import fetch_savant_pitchers
        for mod in (fetch_lineups, fetch_savant_pitchers):
            src = inspect.getsource(mod._write_slate_atomic)
            assert "write_json_atomic(" in src, (
                f"{mod.__name__}._write_slate_atomic() must delegate to the shared helper"
            )
            assert "tempfile.mkstemp" not in src, (
                f"{mod.__name__}._write_slate_atomic() must not reimplement the atomic-write "
                f"logic independently anymore"
            )

    def test_unicode_payload_round_trips_correctly(self):
        payload = {"team": "Häkkinen éè", "emoji": "⚾", "cjk": "中文"}
        write_json_atomic(payload, self.path)
        with open(self.path, encoding="utf-8") as f:
            assert json.load(f) == payload

    def test_no_trailing_newline_added(self):
        """Matches plain json.dump()'s behavior: no trailing newline is ever appended."""
        write_json_atomic({"a": 1}, self.path)
        with open(self.path, "rb") as f:
            content = f.read()
        assert not content.endswith(b"\n")

    def test_symlinked_destination_is_replaced_not_followed_into(self):
        """
        os.replace() on a path that is a symlink replaces the symlink
        itself (atomically repoints or removes it), matching POSIX
        rename(2) semantics -- it does not follow the symlink and
        overwrite whatever it points to. This is standard os.replace()
        behavior, verified directly here since a write helper silently
        writing through a symlink to some other file would be a
        surprising, unintended side effect.
        """
        real_target = os.path.join(self.tmp, "data", "real_target.json")
        with open(real_target, "w") as f:
            json.dump({"marker": "original-target-content"}, f)
        symlink_path = os.path.join(self.tmp, "data", "slate_link.json")
        os.symlink(real_target, symlink_path)

        write_json_atomic({"a": 1}, symlink_path)

        assert not os.path.islink(symlink_path), (
            "os.replace() must replace the symlink itself, not write through it"
        )
        with open(real_target) as f:
            assert json.load(f) == {"marker": "original-target-content"}, (
                "the real target the symlink pointed to must remain untouched"
            )
        with open(symlink_path) as f:
            assert json.load(f) == {"a": 1}

    def test_simultaneous_writers_last_replace_wins_no_corruption(self):
        """
        Two "writers" targeting the same path: since each writes to its
        own uniquely-named temp file (tempfile.mkstemp guarantees
        uniqueness) before an atomic os.replace(), there is no possible
        interleaving that produces a corrupted/partial file at the
        destination -- the destination is always either the first
        writer's complete content or the second's, never a mix.
        """
        write_json_atomic({"writer": 1}, self.path)
        write_json_atomic({"writer": 2}, self.path)
        with open(self.path) as f:
            result = json.load(f)
        assert result in ({"writer": 1}, {"writer": 2})
        assert result == {"writer": 2}, "the later write must win when they don't overlap in time"
