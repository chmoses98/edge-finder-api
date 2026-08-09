#!/usr/bin/env python3
"""
tests/test_git_data_commit.py
=================================
Regression coverage for scripts/ci/git_data_commit.py -- the shared,
safe git commit/push path that replaces the inline `git fetch && git
rebase --autostash origin/main && git add ... && git commit && git
push` block previously copy-pasted across ~17 workflow files.

The core regression test (TestStashPopConflictReproduction) reproduces
the EXACT real-world failure, against real git (no mocking): a
concurrent upstream change collides with a local uncommitted change at
the same line, so `git rebase --autostash`'s automatic `git stash pop`
step conflicts -- and confirms `git rebase` itself still exits 0 for
that case (the actual root cause the old inline workflow scripts never
guarded against), then confirms git_data_commit.py's own conflict
detection (which never trusts that exit code) catches it anyway: no
commit is ever created, origin/main is never touched, and no file is
left holding literal conflict-marker text.
"""
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "scripts", "ci", "git_data_commit.py")
SCRIPTS_CI_DIR = os.path.join(ROOT, "scripts", "ci")
sys.path.insert(0, SCRIPTS_CI_DIR)

import git_data_commit as gdc  # noqa: E402


def _git(args, cwd, check=True):
    result = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=15)
    if check and result.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed in {cwd}: {result.stderr}")
    return result


def _run_script(cwd, message, paths, branch="main"):
    return subprocess.run(
        [sys.executable, SCRIPT, "--message", message, "--branch", branch, "--cwd", str(cwd), *paths],
        capture_output=True, text=True, timeout=30,
    )


def _init_bare_origin(tmp_path, seed_content='{"a": 1}\n'):
    """A bare 'origin' remote plus a throwaway seed clone used only to
    create+push the first commit -- pushing directly into a non-bare
    repo's checked-out branch requires extra config, so every real
    remote in this file is bare, exactly like a real GitHub remote."""
    origin = tmp_path / "origin.git"
    _git(["init", "--bare", "-b", "main", str(origin)], cwd=tmp_path)
    seed = tmp_path / "seed"
    _git(["clone", "-q", str(origin), str(seed)], cwd=tmp_path)
    _git(["config", "user.email", "a@a.com"], cwd=seed)
    _git(["config", "user.name", "a"], cwd=seed)
    (seed / "f.jsonl").write_text(seed_content)
    _git(["add", "f.jsonl"], cwd=seed)
    _git(["commit", "-q", "-m", "init"], cwd=seed)
    _git(["push", "-q", "origin", "main"], cwd=seed)
    return origin


def _clone(tmp_path, origin, name):
    work = tmp_path / name
    _git(["clone", "-q", str(origin), str(work)], cwd=tmp_path)
    _git(["config", "user.email", "a@a.com"], cwd=work)
    _git(["config", "user.name", "a"], cwd=work)
    return work


def _origin_head(origin):
    return _git(["rev-parse", "main"], cwd=origin).stdout.strip()


class TestFindConflictMarkers:

    def test_detects_all_three_marker_lines(self, tmp_path):
        f = tmp_path / "f.jsonl"
        f.write_text('{"a": 1}\n<<<<<<< Updated upstream\n{"b": 2}\n=======\n{"c": 3}\n>>>>>>> Stashed changes\n')
        hits = gdc.find_conflict_markers(["f.jsonl"], cwd=str(tmp_path))
        assert [h[1] for h in hits] == [2, 4, 6]

    def test_clean_file_has_no_hits(self, tmp_path):
        f = tmp_path / "f.jsonl"
        f.write_text('{"a": 1}\n{"b": 2}\n')
        assert gdc.find_conflict_markers(["f.jsonl"], cwd=str(tmp_path)) == []

    def test_missing_path_is_skipped_not_an_error(self, tmp_path):
        assert gdc.find_conflict_markers(["does_not_exist.jsonl"], cwd=str(tmp_path)) == []

    def test_marker_text_inside_a_json_value_is_not_a_false_positive(self, tmp_path):
        """Only a marker at the START of a line counts -- a JSON string
        value that happens to CONTAIN e.g. '=======' mid-line (already
        quoted/escaped JSON, never bare at column 0) must not trip this."""
        f = tmp_path / "f.jsonl"
        f.write_text('{"note": "score was 7=======3, not a conflict"}\n')
        assert gdc.find_conflict_markers(["f.jsonl"], cwd=str(tmp_path)) == []


class TestStashPopConflictReproduction:
    """The real-world failure mode, reproduced against real git."""

    def _set_up_colliding_state(self, tmp_path):
        """origin advances (changes line 1) while `work`'s working tree
        independently has an UNCOMMITTED change to the same file (an
        appended line, with the original line 1 still present) -- the
        exact shape that makes `git rebase --autostash`'s automatic
        `stash pop` collide with the rebased line 1."""
        origin = _init_bare_origin(tmp_path)
        work = _clone(tmp_path, origin, "work")
        other = _clone(tmp_path, origin, "other")

        (other / "f.jsonl").write_text('{"a": 999}\n')
        _git(["commit", "-aqm", "origin advances"], cwd=other)
        _git(["push", "-q", "origin", "main"], cwd=other)

        (work / "f.jsonl").write_text('{"a": 1}\n{"b": 2}\n')
        return origin, work

    def test_bare_git_rebase_autostash_exits_zero_despite_conflict(self, tmp_path):
        """Confirms the actual root cause still holds in this
        environment's git version: `git rebase --autostash` alone (no
        higher-level safety) exits 0 and leaves literal conflict-marker
        text on disk. If a future git version ever changes this
        behavior, this test (not the production fix) is the one that
        should start failing first."""
        origin, work = self._set_up_colliding_state(tmp_path)
        _git(["fetch", "-q", "origin", "main"], cwd=work)
        result = _git(["rebase", "--autostash", "origin/main"], cwd=work, check=False)
        assert result.returncode == 0, "documenting known git behavior: this exits 0 despite the conflict"
        content = (work / "f.jsonl").read_text()
        assert "<<<<<<< Updated upstream" in content
        # Clean up so later fixtures in the same tmp_path aren't affected.
        _git(["rebase", "--abort"], cwd=work, check=False)

    def test_git_data_commit_never_commits_markers_and_leaves_origin_untouched(self, tmp_path):
        origin, work = self._set_up_colliding_state(tmp_path)
        head_before = _origin_head(origin)

        result = _run_script(work, "test commit", ["f.jsonl"])

        assert result.returncode != 0, f"expected failure, got: stdout={result.stdout!r} stderr={result.stderr!r}"
        assert _origin_head(origin) == head_before, "origin/main must be completely untouched"

        # No conflict-marker text anywhere in the (reset-to-clean) work tree.
        content = (work / "f.jsonl").read_text()
        assert "<<<<<<<" not in content
        assert "Stashed changes" not in content
        assert content == '{"a": 999}\n', "work tree must be reset to origin's tip, not left half-merged"

    def test_git_data_commit_leaves_no_unmerged_state_behind(self, tmp_path):
        _origin, work = self._set_up_colliding_state(tmp_path)
        _run_script(work, "test commit", ["f.jsonl"])
        assert gdc.unmerged_paths(cwd=str(work)) == []
        status = _git(["status", "--porcelain"], cwd=work).stdout
        assert status.strip() == "", f"working tree must be clean after an aborted run, got: {status!r}"


class TestCleanPathStillWorks:
    """The non-conflicting, everyday case must behave exactly like the
    old inline script: rebase cleanly, commit, push."""

    def test_new_file_commits_and_pushes(self, tmp_path):
        origin = _init_bare_origin(tmp_path)
        work = _clone(tmp_path, origin, "work")
        (work / "new.jsonl").write_text('{"x": 1}\n')

        result = _run_script(work, "add new.jsonl", ["new.jsonl"])

        assert result.returncode == 0, result.stderr
        assert _git(["log", "-1", "--format=%s"], cwd=work).stdout.strip() == "add new.jsonl"
        # Actually landed on the remote, not just the local clone.
        check = tmp_path / "check"
        _git(["clone", "-q", str(origin), str(check)], cwd=tmp_path)
        assert (check / "new.jsonl").exists()

    def test_directory_path_commits_and_pushes(self, tmp_path):
        """Several migrated workflows pass a whole directory (e.g.
        data/clv_snapshots/) rather than individual files -- os.path.isfile
        is always False for a directory, so a naive existence check would
        silently drop it from `existing_paths` and never stage/commit it."""
        origin = _init_bare_origin(tmp_path)
        work = _clone(tmp_path, origin, "work")
        snap_dir = work / "snapshots"
        snap_dir.mkdir()
        (snap_dir / "a.json").write_text('{"x": 1}\n')
        (snap_dir / "b.json").write_text('{"y": 2}\n')

        result = _run_script(work, "add snapshots dir", ["snapshots/"])

        assert result.returncode == 0, result.stderr
        check = tmp_path / "check_dir"
        _git(["clone", "-q", str(origin), str(check)], cwd=tmp_path)
        assert (check / "snapshots" / "a.json").exists()
        assert (check / "snapshots" / "b.json").exists()

    def test_directory_path_with_conflict_marker_is_refused(self, tmp_path):
        origin = _init_bare_origin(tmp_path)
        work = _clone(tmp_path, origin, "work")
        snap_dir = work / "snapshots"
        snap_dir.mkdir()
        (snap_dir / "broken.json").write_text('{"ok": 1}\n<<<<<<< Updated upstream\n=======\n>>>>>>> Stashed changes\n')
        head_before = _origin_head(origin)

        result = _run_script(work, "add broken snapshots dir", ["snapshots/"])

        assert result.returncode != 0
        assert _origin_head(origin) == head_before

    def test_no_op_when_nothing_changed(self, tmp_path):
        origin = _init_bare_origin(tmp_path)
        work = _clone(tmp_path, origin, "work")
        head_before = _origin_head(origin)

        result = _run_script(work, "no-op", ["f.jsonl"])  # unchanged, already-committed content

        assert result.returncode == 0
        assert _origin_head(origin) == head_before

    def test_nonexistent_path_is_a_clean_no_op_not_an_error(self, tmp_path):
        origin = _init_bare_origin(tmp_path)
        work = _clone(tmp_path, origin, "work")
        result = _run_script(work, "no-op", ["never_written.jsonl"])
        assert result.returncode == 0

    def test_independent_non_colliding_append_rebases_and_commits_cleanly(self, tmp_path):
        """A genuinely independent append-only conflict (origin adds a
        DIFFERENT file/line, local adds another) must NOT be treated as
        a conflict at all -- git's own three-way merge already handles
        this cleanly; this proves git_data_commit.py doesn't over-fire."""
        origin = _init_bare_origin(tmp_path)
        work = _clone(tmp_path, origin, "work")
        other = _clone(tmp_path, origin, "other")

        (other / "other.jsonl").write_text('{"other": true}\n')
        _git(["add", "other.jsonl"], cwd=other)
        _git(["commit", "-qm", "other adds a file"], cwd=other)
        _git(["push", "-q", "origin", "main"], cwd=other)

        (work / "f.jsonl").write_text('{"a": 1}\n{"appended": true}\n')

        result = _run_script(work, "work appends", ["f.jsonl"])

        assert result.returncode == 0, result.stderr
        assert (work / "other.jsonl").exists(), "the concurrent, non-conflicting commit must still be present"
        assert '{"appended": true}' in (work / "f.jsonl").read_text()


class TestRefusesPreexistingMarkersRegardlessOfGitState:
    """Even with a perfectly clean rebase, a file that (for any other
    reason) already contains literal conflict-marker text must never be
    committed -- the independent, second layer of defense."""

    def test_file_with_markers_but_no_git_conflict_is_still_refused(self, tmp_path):
        origin = _init_bare_origin(tmp_path)
        work = _clone(tmp_path, origin, "work")
        head_before = _origin_head(origin)

        (work / "broken.jsonl").write_text('{"ok": 1}\n<<<<<<< Updated upstream\n{"x": 1}\n=======\n{"y": 2}\n>>>>>>> Stashed changes\n')

        result = _run_script(work, "should be refused", ["broken.jsonl"])

        assert result.returncode != 0
        assert "conflict marker" in (result.stdout + result.stderr).lower()
        assert _origin_head(origin) == head_before


class TestUnmergedPathsHelper:

    def test_empty_when_repo_is_clean(self, tmp_path):
        origin = _init_bare_origin(tmp_path)
        work = _clone(tmp_path, origin, "work")
        assert gdc.unmerged_paths(cwd=str(work)) == []
