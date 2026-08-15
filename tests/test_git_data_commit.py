#!/usr/bin/env python3
"""
tests/test_git_data_commit.py
=================================
Regression coverage for scripts/ci/git_data_commit.py -- the shared,
safe git commit/push path that replaces the inline `git fetch && git
rebase --autostash origin/main && git add ... && git commit && git
push` block previously copy-pasted across ~17 workflow files.

TestBareGitAutostashDocumentation reproduces the EXACT real-world
failure, against real git (no mocking): a concurrent upstream change
collides with a local uncommitted change to the same file, so `git
rebase --autostash`'s automatic `git stash pop` step conflicts -- and
confirms `git rebase` itself still exits 0 for that case (the actual
root cause the old inline workflow scripts never guarded against).

Kalshi Price Check (Standalone) taking ~22 minutes for hitter
projection widened the window in which other scheduled workflows
advance main and append their own rows to the SAME shared
data/edgelab/*.jsonl daily partition, which made the plain fail-closed
behavior above fire routinely for a completely benign case: two
independently-generated, valid, append-only JSONL records landing in
the same daily file. git_data_commit.py now proves -- deterministically,
never by guessing or picking a side -- whether every conflicted path is
a pure local append onto whatever origin/<branch> currently has, and
reconciles automatically ONLY when it can prove that; every other class
of conflict (an existing line edited or deleted locally, malformed
JSON, a non-JSONL conflict, an upstream file that isn't itself clean
JSONL, a local record whose stable id collides with different upstream
content) still aborts exactly as before: no commit is ever created, no
conflict-marker text is ever committed, and origin/main is never
touched.
"""
import json
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


def _init_bare_origin(tmp_path, seed_content='{"a": 1}\n', seed_name="f.jsonl"):
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
    seed_path = seed / seed_name
    seed_path.parent.mkdir(parents=True, exist_ok=True)
    seed_path.write_text(seed_content)
    _git(["add", seed_name], cwd=seed)
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


def _records(text):
    return [json.loads(line) for line in text.splitlines() if line.strip()]


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


class TestBareGitAutostashDocumentation:
    """[TEST 1] The real-world failure mode, reproduced against real git,
    independent of git_data_commit.py entirely -- documents the exact
    underlying git behavior this whole module exists to work around/
    recover from."""

    def _set_up_pure_append_collision(self, tmp_path):
        """origin advances (appends its own new line) while `work`'s
        working tree independently has an UNCOMMITTED append of a
        DIFFERENT new line -- both sides purely append, touching no
        existing line, yet `git rebase --autostash`'s automatic `stash
        pop` still collides (confirmed empirically: two concurrent
        appends at the same end-of-file position are not automatically
        reconciled by git's own 3-way merge)."""
        origin = _init_bare_origin(tmp_path)
        work = _clone(tmp_path, origin, "work")
        other = _clone(tmp_path, origin, "other")

        (other / "f.jsonl").write_text('{"a": 1}\n{"upstream": true}\n')
        _git(["commit", "-aqm", "origin advances"], cwd=other)
        _git(["push", "-q", "origin", "main"], cwd=other)

        (work / "f.jsonl").write_text('{"a": 1}\n{"local": true}\n')
        return origin, work

    def test_bare_git_rebase_autostash_exits_zero_despite_conflict(self, tmp_path):
        """Confirms the actual root cause still holds in this
        environment's git version: `git rebase --autostash` alone (no
        higher-level safety) exits 0 and leaves literal conflict-marker
        text on disk, even for two purely-additive, non-overlapping
        appends. If a future git version ever changes this behavior,
        this test (not the production fix) is the one that should start
        failing first."""
        origin, work = self._set_up_pure_append_collision(tmp_path)
        _git(["fetch", "-q", "origin", "main"], cwd=work)
        result = _git(["rebase", "--autostash", "origin/main"], cwd=work, check=False)
        assert result.returncode == 0, "documenting known git behavior: this exits 0 despite the conflict"
        content = (work / "f.jsonl").read_text()
        assert "<<<<<<< Updated upstream" in content
        # Clean up so later fixtures in the same tmp_path aren't affected.
        _git(["rebase", "--abort"], cwd=work, check=False)


class TestCleanPathStillWorks:
    """[TEST 14] The non-conflicting, everyday case must behave exactly
    like the old inline script: rebase cleanly, commit, push."""

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
        """[TEST 12] Several migrated workflows pass a whole directory
        (e.g. data/clv_snapshots/) rather than individual files --
        os.path.isfile is always False for a directory, so a naive
        existence check would silently drop it from `existing_paths`
        and never stage/commit it."""
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
        """[TEST 13] Conflict-marker defense over a directory path."""
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
        """A genuinely independent, different-file conflict (origin adds a
        DIFFERENT file, local adds another) must NOT be treated as a
        conflict at all -- git's own three-way merge already handles this
        cleanly; this proves git_data_commit.py doesn't over-fire."""
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
    """[TEST 13] Even with a perfectly clean rebase, a file that (for any
    other reason) already contains literal conflict-marker text must
    never be committed -- the independent, second layer of defense."""

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


# ── Unit-level coverage of the append-only detection primitives ─────────

class TestSplitJsonlStrict:

    def test_none_is_invalid(self):
        assert gdc._split_jsonl_strict(None) is None

    def test_empty_string_is_an_empty_valid_file(self):
        assert gdc._split_jsonl_strict("") == []

    def test_blank_lines_are_skipped(self):
        records = gdc._split_jsonl_strict('{"a": 1}\n\n  \n{"b": 2}\n')
        assert [obj for _, obj in records] == [{"a": 1}, {"b": 2}]

    def test_missing_trailing_newline_is_a_partial_line(self):
        """[TEST 9] A final line with no trailing newline is a
        partial/truncated write -- never treated as a complete record."""
        assert gdc._split_jsonl_strict('{"a": 1}\n{"b": 2}') is None

    def test_malformed_json_line_is_invalid(self):
        """[TEST 8]"""
        assert gdc._split_jsonl_strict('{"a": 1}\nnot json at all\n') is None

    def test_non_object_json_line_is_invalid(self):
        assert gdc._split_jsonl_strict('{"a": 1}\n[1, 2, 3]\n') is None

    def test_conflict_marker_text_is_invalid(self):
        assert gdc._split_jsonl_strict('{"a": 1}\n<<<<<<< HEAD\n{"b": 2}\n') is None


class TestCaptureAppendOnlyDeltas:
    """Direct, real-git coverage of capture_append_only_deltas -- the
    pre-rebase local-delta proof every later recovery attempt depends
    on. Uses a plain single-repo history (commit at HEAD, then edit the
    working tree) rather than a full origin/clone pair, since this
    function only ever looks at "current HEAD" vs "current disk"."""

    def _repo_at_head(self, tmp_path, name="f.jsonl", head_content='{"a": 1}\n'):
        repo = tmp_path / "repo"
        repo.mkdir()
        _git(["init", "-q", "-b", "main"], cwd=repo)
        _git(["config", "user.email", "a@a.com"], cwd=repo)
        _git(["config", "user.name", "a"], cwd=repo)
        (repo / name).write_text(head_content)
        _git(["add", name], cwd=repo)
        _git(["commit", "-qm", "init"], cwd=repo)
        return repo

    def test_pure_append_is_a_candidate(self, tmp_path):
        repo = self._repo_at_head(tmp_path)
        (repo / "f.jsonl").write_text('{"a": 1}\n{"b": 2}\n{"c": 3}\n')
        deltas = gdc.capture_append_only_deltas(["f.jsonl"], cwd=str(repo))
        assert list(deltas.keys()) == ["f.jsonl"]
        assert [obj for _, obj in deltas["f.jsonl"]] == [{"b": 2}, {"c": 3}]

    def test_no_local_change_is_not_a_candidate(self, tmp_path):
        repo = self._repo_at_head(tmp_path)
        deltas = gdc.capture_append_only_deltas(["f.jsonl"], cwd=str(repo))
        assert deltas == {}

    def test_brand_new_file_with_no_head_version_is_not_a_candidate(self, tmp_path):
        repo = self._repo_at_head(tmp_path)
        (repo / "new.jsonl").write_text('{"x": 1}\n')
        deltas = gdc.capture_append_only_deltas(["new.jsonl"], cwd=str(repo))
        assert deltas == {}

    def test_edit_of_an_existing_line_is_not_a_candidate(self, tmp_path):
        """[TEST 6]"""
        repo = self._repo_at_head(tmp_path, head_content='{"a": 1}\n{"b": 2}\n')
        (repo / "f.jsonl").write_text('{"a": 1}\n{"b": 999}\n')
        deltas = gdc.capture_append_only_deltas(["f.jsonl"], cwd=str(repo))
        assert deltas == {}

    def test_deletion_of_an_existing_line_is_not_a_candidate(self, tmp_path):
        """[TEST 7]"""
        repo = self._repo_at_head(tmp_path, head_content='{"a": 1}\n{"b": 2}\n')
        (repo / "f.jsonl").write_text('{"a": 1}\n')
        deltas = gdc.capture_append_only_deltas(["f.jsonl"], cwd=str(repo))
        assert deltas == {}

    def test_reordering_existing_lines_is_not_a_candidate(self, tmp_path):
        """Not a strict byte prefix even though it contains the same
        records -- must never be treated as append-only."""
        repo = self._repo_at_head(tmp_path, head_content='{"a": 1}\n{"b": 2}\n')
        (repo / "f.jsonl").write_text('{"b": 2}\n{"a": 1}\n{"c": 3}\n')
        deltas = gdc.capture_append_only_deltas(["f.jsonl"], cwd=str(repo))
        assert deltas == {}

    def test_malformed_appended_line_is_not_a_candidate(self, tmp_path):
        """[TEST 8]"""
        repo = self._repo_at_head(tmp_path)
        (repo / "f.jsonl").write_text('{"a": 1}\nnot valid json\n')
        deltas = gdc.capture_append_only_deltas(["f.jsonl"], cwd=str(repo))
        assert deltas == {}

    def test_partial_trailing_line_is_not_a_candidate(self, tmp_path):
        """[TEST 9]"""
        repo = self._repo_at_head(tmp_path)
        (repo / "f.jsonl").write_text('{"a": 1}\n{"b": 2')  # no closing brace/newline
        deltas = gdc.capture_append_only_deltas(["f.jsonl"], cwd=str(repo))
        assert deltas == {}

    def test_jsonl_gz_is_never_a_candidate(self, tmp_path):
        """Binary/compressed content has no meaningful text-prefix
        relationship to its appended form -- never considered, even if
        it happens to satisfy a byte-prefix check incidentally."""
        repo = self._repo_at_head(tmp_path, name="obs.jsonl.gz", head_content='{"a": 1}\n')
        (repo / "obs.jsonl.gz").write_text('{"a": 1}\n{"b": 2}\n')
        deltas = gdc.capture_append_only_deltas(["obs.jsonl.gz"], cwd=str(repo))
        assert deltas == {}

    def test_directory_argument_is_expanded(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        _git(["init", "-q", "-b", "main"], cwd=repo)
        _git(["config", "user.email", "a@a.com"], cwd=repo)
        _git(["config", "user.name", "a"], cwd=repo)
        (repo / "runs").mkdir()
        (repo / "runs" / "2026-08-14.jsonl").write_text('{"runId": "r0"}\n')
        _git(["add", "runs"], cwd=repo)
        _git(["commit", "-qm", "init"], cwd=repo)

        (repo / "runs" / "2026-08-14.jsonl").write_text('{"runId": "r0"}\n{"runId": "r1"}\n')
        deltas = gdc.capture_append_only_deltas(["runs/"], cwd=str(repo))
        assert os.path.join("runs", "2026-08-14.jsonl") in deltas


class TestReconcileAppendOnly:
    """Direct, real-git coverage of _reconcile_append_only's union/dedup/
    identity-conflict logic, against a real origin/<branch> ref."""

    def _repo_with_origin_ref(self, tmp_path, upstream_content):
        """A single local repo with a `origin/main` ref manually planted
        (via a real commit + ref, no network) so _reconcile_append_only's
        `git show origin/main:path` lookups resolve exactly like they
        would after a real `git fetch`."""
        repo = tmp_path / "repo"
        repo.mkdir()
        _git(["init", "-q", "-b", "main"], cwd=repo)
        _git(["config", "user.email", "a@a.com"], cwd=repo)
        _git(["config", "user.name", "a"], cwd=repo)
        (repo / "f.jsonl").write_text(upstream_content)
        _git(["add", "f.jsonl"], cwd=repo)
        _git(["commit", "-qm", "upstream state"], cwd=repo)
        _git(["update-ref", "refs/remotes/origin/main", "HEAD"], cwd=repo)
        return repo

    def test_unions_local_records_onto_upstream_preserving_order(self, tmp_path):
        repo = self._repo_with_origin_ref(tmp_path, '{"a": 1}\n{"upstream": true}\n')
        local_records = [('{"local1": true}', {"local1": True}), ('{"local2": true}', {"local2": True})]

        ok, count = gdc._reconcile_append_only("f.jsonl", local_records, "main", cwd=str(repo))

        assert ok is True
        assert count == 2
        reconciled = _records((repo / "f.jsonl").read_text())
        assert reconciled == [{"a": 1}, {"upstream": True}, {"local1": True}, {"local2": True}]

    def test_exact_duplicate_of_upstream_record_is_skipped(self, tmp_path):
        """[TEST 4]"""
        repo = self._repo_with_origin_ref(tmp_path, '{"a": 1}\n{"dup": true}\n')
        local_records = [('{"dup": true}', {"dup": True}), ('{"new": true}', {"new": True})]

        ok, count = gdc._reconcile_append_only("f.jsonl", local_records, "main", cwd=str(repo))

        assert ok is True
        assert count == 1  # only the genuinely new record counted/appended
        reconciled = _records((repo / "f.jsonl").read_text())
        assert reconciled == [{"a": 1}, {"dup": True}, {"new": True}]
        assert reconciled.count({"dup": True}) == 1

    def test_duplicate_within_local_records_itself_is_also_collapsed(self, tmp_path):
        repo = self._repo_with_origin_ref(tmp_path, '{"a": 1}\n')
        local_records = [('{"x": 1}', {"x": 1}), ('{"x": 1}', {"x": 1})]

        ok, count = gdc._reconcile_append_only("f.jsonl", local_records, "main", cwd=str(repo))

        assert ok is True
        assert count == 1
        reconciled = _records((repo / "f.jsonl").read_text())
        assert reconciled.count({"x": 1}) == 1

    def test_same_stable_id_different_payload_hard_fails(self, tmp_path):
        """[TEST 5]"""
        repo = self._repo_with_origin_ref(tmp_path, '{"runId": "r1", "status": "upstream"}\n')
        local_records = [('{"runId": "r1", "status": "local"}', {"runId": "r1", "status": "local"})]

        ok, reason = gdc._reconcile_append_only("f.jsonl", local_records, "main", cwd=str(repo))

        assert ok is False
        assert "runId" in reason and "r1" in reason
        # Never wrote anything on failure.
        assert (repo / "f.jsonl").read_text() == '{"runId": "r1", "status": "upstream"}\n'

    def test_upstream_malformed_jsonl_hard_fails(self, tmp_path):
        """[TEST 10] The origin/<branch> blob itself is not clean JSONL
        (simulating some other, unrelated form of corruption already on
        main) -- must never be trusted as a reconciliation base."""
        repo = self._repo_with_origin_ref(tmp_path, '{"a": 1}\nnot valid json at all\n')
        local_records = [('{"local": true}', {"local": True})]

        ok, reason = gdc._reconcile_append_only("f.jsonl", local_records, "main", cwd=str(repo))

        assert ok is False
        assert "not clean valid JSONL" in reason

    def test_upstream_with_conflict_markers_hard_fails(self, tmp_path):
        repo = self._repo_with_origin_ref(tmp_path, '{"a": 1}\n<<<<<<< HEAD\n{"b": 2}\n')
        local_records = [('{"local": true}', {"local": True})]

        ok, reason = gdc._reconcile_append_only("f.jsonl", local_records, "main", cwd=str(repo))

        assert ok is False

    def test_no_origin_version_hard_fails(self, tmp_path):
        repo = self._repo_with_origin_ref(tmp_path, '{"a": 1}\n')
        local_records = [('{"local": true}', {"local": True})]

        ok, reason = gdc._reconcile_append_only("missing.jsonl", local_records, "main", cwd=str(repo))

        assert ok is False
        assert "no origin/main version" in reason


# ── End-to-end append-only recovery, over real bare-git remotes ─────────

class TestAppendOnlyAutostashRecoveryEndToEnd:
    """[TESTS 2, 3, 5, 6, 11] The full script (subprocess, real bare
    origin, real autostash conflict) for both the safe-recovery and the
    still-must-fail-closed cases."""

    def test_safe_concurrent_append_succeeds(self, tmp_path):
        """[TEST 2] Initial {"a":1}; upstream concurrently appends
        {"upstream":true}; local appends {"local":true}. Expected: final
        remote file contains all three valid records exactly once, no
        conflict markers, commit succeeds."""
        origin = _init_bare_origin(tmp_path, seed_content='{"a": 1}\n')
        work = _clone(tmp_path, origin, "work")
        other = _clone(tmp_path, origin, "other")

        (other / "f.jsonl").write_text('{"a": 1}\n{"upstream": true}\n')
        _git(["commit", "-aqm", "upstream appends"], cwd=other)
        _git(["push", "-q", "origin", "main"], cwd=other)

        (work / "f.jsonl").write_text('{"a": 1}\n{"local": true}\n')
        result = _run_script(work, "local appends", ["f.jsonl"])

        assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        assert "safely reconciled" in result.stdout

        check = tmp_path / "check"
        _git(["clone", "-q", str(origin), str(check)], cwd=tmp_path)
        content = (check / "f.jsonl").read_text()
        records = _records(content)
        assert records == [{"a": 1}, {"upstream": True}, {"local": True}]
        assert "<<<<<<<" not in content
        assert gdc.unmerged_paths(cwd=str(work)) == []
        assert _git(["status", "--porcelain"], cwd=work).stdout.strip() == ""

    def test_production_shaped_research_runs_race(self, tmp_path):
        """[TEST 3] Records shaped like data/edgelab/research_runs/*.jsonl,
        with unique runId values. Upstream adds one research run while
        local adds another. Both must be retained, valid JSONL, no
        duplicates, remote main updated."""
        seed = '{"runId": "seed-run", "runType": "SEED", "status": "ok"}\n'
        origin = _init_bare_origin(tmp_path, seed_content=seed, seed_name="research_runs/2026-08-14.jsonl")
        work = _clone(tmp_path, origin, "work")
        other = _clone(tmp_path, origin, "other")

        upstream_run = '{"runId": "run-upstream-1", "runType": "PROSPECTIVE_SNAPSHOT", "status": "ok"}\n'
        (other / "research_runs" / "2026-08-14.jsonl").write_text(seed + upstream_run)
        _git(["commit", "-aqm", "edgelab capture: upstream run"], cwd=other)
        _git(["push", "-q", "origin", "main"], cwd=other)

        local_run = '{"runId": "run-local-1", "runType": "PROSPECTIVE_SNAPSHOT", "status": "ok"}\n'
        (work / "research_runs" / "2026-08-14.jsonl").write_text(seed + local_run)
        result = _run_script(work, "standalone price-check corpus archive", ["research_runs/2026-08-14.jsonl"])

        assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"

        check = tmp_path / "check"
        _git(["clone", "-q", str(origin), str(check)], cwd=tmp_path)
        content = (check / "research_runs" / "2026-08-14.jsonl").read_text()
        records = _records(content)
        run_ids = [r["runId"] for r in records]
        assert run_ids == ["seed-run", "run-upstream-1", "run-local-1"]
        assert len(run_ids) == len(set(run_ids)), "no duplicate runs"
        assert "<<<<<<<" not in content

    def test_duplicate_identical_record_is_a_clean_no_op_not_a_corruption(self, tmp_path):
        """[TEST 4] The exact same local record already landed upstream
        (e.g. via another retry/race). Git's own merge recognizes the
        identical addition and never even conflicts here -- confirming
        the end-to-end result is exactly what it should be: no
        duplicate line, no corruption, a successful commit (or, if there
        were truly nothing left to add, a clean no-op)."""
        origin = _init_bare_origin(tmp_path, seed_content='{"a": 1}\n')
        work = _clone(tmp_path, origin, "work")
        other = _clone(tmp_path, origin, "other")

        (other / "f.jsonl").write_text('{"a": 1}\n{"dup": true}\n')
        _git(["commit", "-aqm", "other lands the record first"], cwd=other)
        _git(["push", "-q", "origin", "main"], cwd=other)

        (work / "f.jsonl").write_text('{"a": 1}\n{"dup": true}\n')
        result = _run_script(work, "work retries the same append", ["f.jsonl"])

        assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        check = tmp_path / "check"
        _git(["clone", "-q", str(origin), str(check)], cwd=tmp_path)
        content = (check / "f.jsonl").read_text()
        records = _records(content)
        assert records == [{"a": 1}, {"dup": True}]
        assert "<<<<<<<" not in content

    def test_same_stable_identity_different_payload_hard_fails(self, tmp_path):
        """[TEST 5] Same runId, differing content -- must hard fail, no
        remote mutation, even though the local delta is itself a pure
        append."""
        origin = _init_bare_origin(tmp_path, seed_content='{"a": 1}\n')
        work = _clone(tmp_path, origin, "work")
        other = _clone(tmp_path, origin, "other")
        head_before = _origin_head(origin)

        (other / "f.jsonl").write_text('{"a": 1}\n{"runId": "r1", "status": "upstream"}\n')
        _git(["commit", "-aqm", "other lands r1 first"], cwd=other)
        _git(["push", "-q", "origin", "main"], cwd=other)
        head_before = _origin_head(origin)

        (work / "f.jsonl").write_text('{"a": 1}\n{"runId": "r1", "status": "local"}\n')
        result = _run_script(work, "work also lands r1, different content", ["f.jsonl"])

        assert result.returncode != 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        assert _origin_head(origin) == head_before
        content = (work / "f.jsonl").read_text()
        assert "<<<<<<<" not in content
        assert "Stashed changes" not in content

    def test_local_edit_of_existing_record_hard_fails(self, tmp_path):
        """[TEST 6] Local changes an OLD JSONL line (not merely appends).
        Must never be treated as append-only; on a real conflict this
        still hard-fails exactly as before, origin untouched."""
        origin = _init_bare_origin(tmp_path, seed_content='{"a": 1}\n{"b": 2}\n')
        work = _clone(tmp_path, origin, "work")
        other = _clone(tmp_path, origin, "other")

        (other / "f.jsonl").write_text('{"a": 1}\n{"b": 999}\n')
        _git(["commit", "-aqm", "other edits line 2"], cwd=other)
        _git(["push", "-q", "origin", "main"], cwd=other)
        head_before = _origin_head(origin)

        (work / "f.jsonl").write_text('{"a": 1}\n{"b": 3}\n')  # edits line 2 locally too, doesn't append
        result = _run_script(work, "local edits an existing line", ["f.jsonl"])

        assert result.returncode != 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        assert _origin_head(origin) == head_before
        content = (work / "f.jsonl").read_text()
        assert "<<<<<<<" not in content
        assert content == '{"a": 1}\n{"b": 999}\n', "work tree must be reset to origin's tip, not left half-merged"
        assert gdc.unmerged_paths(cwd=str(work)) == []
        assert _git(["status", "--porcelain"], cwd=work).stdout.strip() == ""

    def test_local_deletion_hard_fails(self, tmp_path):
        """[TEST 7] Local deletes an existing line instead of appending."""
        origin = _init_bare_origin(tmp_path, seed_content='{"a": 1}\n{"b": 2}\n')
        work = _clone(tmp_path, origin, "work")
        other = _clone(tmp_path, origin, "other")

        (other / "f.jsonl").write_text('{"a": 1}\n{"b": 999}\n')
        _git(["commit", "-aqm", "other edits line 2"], cwd=other)
        _git(["push", "-q", "origin", "main"], cwd=other)
        head_before = _origin_head(origin)

        (work / "f.jsonl").write_text('{"a": 1}\n')  # deletes line 2 locally
        result = _run_script(work, "local deletes an existing line", ["f.jsonl"])

        assert result.returncode != 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        assert _origin_head(origin) == head_before

    def test_malformed_appended_json_hard_fails(self, tmp_path):
        """[TEST 8]"""
        origin = _init_bare_origin(tmp_path, seed_content='{"a": 1}\n')
        work = _clone(tmp_path, origin, "work")
        other = _clone(tmp_path, origin, "other")

        (other / "f.jsonl").write_text('{"a": 1}\n{"upstream": true}\n')
        _git(["commit", "-aqm", "upstream appends"], cwd=other)
        _git(["push", "-q", "origin", "main"], cwd=other)
        head_before = _origin_head(origin)

        (work / "f.jsonl").write_text('{"a": 1}\nthis is not json\n')
        result = _run_script(work, "local appends garbage", ["f.jsonl"])

        assert result.returncode != 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        assert _origin_head(origin) == head_before

    def test_partial_final_json_line_hard_fails(self, tmp_path):
        """[TEST 9]"""
        origin = _init_bare_origin(tmp_path, seed_content='{"a": 1}\n')
        work = _clone(tmp_path, origin, "work")
        other = _clone(tmp_path, origin, "other")

        (other / "f.jsonl").write_text('{"a": 1}\n{"upstream": true}\n')
        _git(["commit", "-aqm", "upstream appends"], cwd=other)
        _git(["push", "-q", "origin", "main"], cwd=other)
        head_before = _origin_head(origin)

        with open(work / "f.jsonl", "w") as f:
            f.write('{"a": 1}\n{"partial": tr')  # truncated, no closing/newline
        result = _run_script(work, "local write got cut off", ["f.jsonl"])

        assert result.returncode != 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        assert _origin_head(origin) == head_before

    def test_non_jsonl_conflict_still_uses_existing_fail_safe(self, tmp_path):
        """[TEST 11] A conflict on a non-.jsonl file must never be
        eligible for append-only recovery at all -- the pre-existing
        fail-closed path is the only path available."""
        origin = _init_bare_origin(tmp_path, seed_content='{"a": 1}\n', seed_name="registry.json")
        work = _clone(tmp_path, origin, "work")
        other = _clone(tmp_path, origin, "other")

        (other / "registry.json").write_text('{"a": 999}\n')
        _git(["commit", "-aqm", "other edits registry.json"], cwd=other)
        _git(["push", "-q", "origin", "main"], cwd=other)
        head_before = _origin_head(origin)

        (work / "registry.json").write_text('{"a": 1}\n{"b": 2}\n')
        result = _run_script(work, "local edits registry.json too", ["registry.json"])

        assert result.returncode != 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        assert _origin_head(origin) == head_before
        content = (work / "registry.json").read_text()
        assert "<<<<<<<" not in content


class TestPushRetryRace:
    """[TEST 15] A real bare-git fixture where another clone advances the
    remote AFTER this run's own local commit already exists but BEFORE
    its push succeeds -- the exact race scripts/ci/git_data_commit.py's
    push-retry loop exists to survive. Uses a real client-side
    `pre-push` git hook to land the concurrent commit at exactly that
    moment (between work's own commit and its first successful push),
    so the full script (subprocess, no monkeypatching) is exercised
    end-to-end."""

    def test_concurrent_append_after_local_commit_survives_push_retry(self, tmp_path):
        origin = _init_bare_origin(tmp_path, seed_content='{"a": 1}\n')
        work = _clone(tmp_path, origin, "work")
        racer = _clone(tmp_path, origin, "racer")

        (racer / "f.jsonl").write_text('{"a": 1}\n{"racer": true}\n')
        _git(["commit", "-aqm", "racer appends concurrently"], cwd=racer)
        # Deliberately NOT pushed yet -- the hook below pushes it at
        # exactly the moment work's own first push attempt fires, i.e.
        # strictly AFTER work has already committed locally.

        hooks_dir = work / ".git" / "hooks"
        hooks_dir.mkdir(parents=True, exist_ok=True)
        marker = work / ".git" / ".race_fired"
        hook = hooks_dir / "pre-push"
        hook.write_text(
            "#!/bin/sh\n"
            f'MARKER="{marker}"\n'
            'if [ -f "$MARKER" ]; then\n'
            "  exit 0\n"
            "fi\n"
            'touch "$MARKER"\n'
            f'git -C "{racer}" push -q origin main\n'
            "exit 1\n"
        )
        hook.chmod(0o755)

        (work / "f.jsonl").write_text('{"a": 1}\n{"local": true}\n')
        result = _run_script(work, "work appends, races the push", ["f.jsonl"])

        assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        assert marker.exists(), "the race hook must actually have fired for this test to mean anything"
        assert "push-retry" in result.stdout

        check = tmp_path / "check"
        _git(["clone", "-q", str(origin), str(check)], cwd=tmp_path)
        content = (check / "f.jsonl").read_text()
        records = _records(content)
        assert records == [{"a": 1}, {"racer": True}, {"local": True}]
        assert "<<<<<<<" not in content
