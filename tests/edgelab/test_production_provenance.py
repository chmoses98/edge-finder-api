#!/usr/bin/env python3
"""
tests/edgelab/test_production_provenance.py
================================================
Forward Replay Corpus and Production Provenance milestone: coverage for
scripts/capture_production_provenance.py, lib/edgelab/snapshot.py's
PRODUCTION_PROVENANCE component + expanded capture_effective_config(),
scripts/run_forward_replay.py, scripts/corpus_health_report.py,
scripts/snapshot_storage_report.py's replay-runs bucket, and
scripts/check_snapshot_capture.py's per-current-run detection fix.

Every test runs inside an isolated tmp_path (monkeypatch.chdir), never
against the real repository's data/ tree -- same discipline as
tests/edgelab/test_snapshot.py / test_replay.py.
"""
import gzip
import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import lib.pipeline_artifacts as pipeline_artifacts  # noqa: E402
from lib.edgelab import replay  # noqa: E402
from lib.edgelab import snapshot as snap  # noqa: E402
import scripts.capture_production_provenance as capture_provenance  # noqa: E402

DATE = "2026-07-31"


def _write(path, obj_or_bytes):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if isinstance(obj_or_bytes, (bytes, bytearray)):
        with open(path, "wb") as f:
            f.write(obj_or_bytes)
    else:
        with open(path, "w") as f:
            json.dump(obj_or_bytes, f)


def _write_pipeline_artifact(stage, date, data, produced_by, created_at=None):
    if created_at is None:
        pipeline_artifacts.write_stage_artifact(stage, date, data, produced_by=produced_by)
        return
    path = pipeline_artifacts.artifact_path(stage, date)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump({
            "meta": {"stage": stage, "slateDate": date, "createdAt": created_at,
                     "schemaVersion": "1.0", "producedBy": produced_by,
                     "status": "transitional", "sourceStage": None},
            "data": data,
        }, f)


def _wire_full_pregame_fixture(tmp_path, monkeypatch, recommendations_created_at=None, provenance_commit_sha=None,
                                working_tree_dirty=False, provenance_created_at=None):
    """Same convention as tests/edgelab/test_snapshot.py's fixture of the
    same name. provenance_commit_sha=False omits the provenance artifact
    entirely (MISSING case); a string pins a specific commitSha; None uses
    a default real-shaped value. working_tree_dirty controls the honesty
    signal capture_production_provenance.py records for its scoped
    scripts/lib/config dirty-tree check (maintainer review of PR #37, item
    1) -- defaults to False (clean) so this fixture represents the normal,
    trustworthy production case unless a test deliberately exercises the
    AMBIGUOUS path. provenance_created_at defaults to
    recommendations_created_at (same convention as every other stage
    artifact here) but can be set independently to exercise temporal-skew
    detection against the provenance artifact specifically."""
    monkeypatch.chdir(tmp_path)

    _write_pipeline_artifact(
        "recommendations", DATE, {"games": [{"gameId": "1", "marketLedger": []}]},
        "scripts/build_market_ledger.py", created_at=recommendations_created_at,
    )
    _write_pipeline_artifact("projections", DATE, {"games": []}, "scripts/build_market_ledger.py", created_at=recommendations_created_at)
    _write_pipeline_artifact("normalized_slate", DATE, {"games": []}, "scripts/enrich_data.py", created_at=recommendations_created_at)
    _write_pipeline_artifact("execution", DATE, {"rulesVersion": "1.0", "candidates": []}, "scripts/risk_gate.py", created_at=recommendations_created_at)
    _write_pipeline_artifact("validation", DATE, {"errors": []}, "scripts/validate_slate_final.py", created_at=recommendations_created_at)
    _write_pipeline_artifact("protection", DATE, {"runType": "OFFICIAL_PREGAME"}, "scripts/protect_slate.py", created_at=recommendations_created_at)
    if provenance_commit_sha is not False:
        _write_pipeline_artifact(
            "provenance", DATE,
            {"commitSha": provenance_commit_sha or ("deadbeef" * 5),
             "gitHeadShaAtCapture": provenance_commit_sha or ("deadbeef" * 5),
             "workingTreeDirty": working_tree_dirty,
             "workflowRunId": "123456",
             "workflowRunAttempt": "1", "ref": "refs/heads/main", "refName": "main",
             "repository": "chmoses98/edge-finder-api", "workflow": "Fetch Slate Data",
             "job": "fetch", "eventName": "push"},
            "scripts/capture_production_provenance.py",
            created_at=provenance_created_at if provenance_created_at is not None else recommendations_created_at,
        )

    _write(os.path.join("data", "slates", DATE, "authoritative.json"), {"date": DATE, "games": []})
    _write(os.path.join("data", "kalshi_registry_snapshots", f"kalshi_search_{DATE}.json"), {"markets": []})
    _write(os.path.join("data", "weather.json"), {"parks": [{"team": "SD", "temp": 72}]})
    _write(os.path.join("data", "bullpen.json"), {"bullpens": {"SD": {"era": 4.0}}})
    observations_path = os.path.join("data", "edgelab", "observations", f"{DATE}.jsonl.gz")
    os.makedirs(os.path.dirname(observations_path), exist_ok=True)
    with gzip.open(observations_path, "wt", encoding="utf-8") as f:
        f.write(json.dumps({"marketTicker": "KXMLBGAME-TEST-AAA", "capturedAt": f"{DATE}T20:00:00Z"}) + "\n")
    _write(
        os.path.join("config", "rules.json"),
        {"_version": "1.0", "calibration": {}, "edge_thresholds": {}, "base_sizes": {"High": 4.0},
         "multipliers": {}, "market_list": [], "validation": {"required_per_game": [], "required_per_market_row": [],
                                                                "rejection_required_if_no_bet": True,
                                                                "min_qualifying_bets_full_slate": 12}},
    )


# ── Item 2: authoritative productionCommitSha capture ────────────────────

class TestProductionProvenanceCapture:
    def test_capture_script_writes_pipeline_artifact(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("GITHUB_SHA", "abc123def456")
        monkeypatch.setenv("GITHUB_RUN_ID", "999")
        monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "2")
        monkeypatch.setenv("GITHUB_REF", "refs/heads/main")
        monkeypatch.setenv("GITHUB_REF_NAME", "main")
        monkeypatch.setenv("GITHUB_REPOSITORY", "chmoses98/edge-finder-api")
        payload = capture_provenance.capture_provenance(DATE)
        assert payload["commitSha"] == "abc123def456"
        assert payload["workflowRunId"] == "999"
        assert payload["workflowRunAttempt"] == "2"
        assert payload["ref"] == "refs/heads/main"
        assert payload["repository"] == "chmoses98/edge-finder-api"
        envelope = pipeline_artifacts.read_stage_artifact("provenance", DATE)
        assert envelope["data"]["commitSha"] == "abc123def456"

    def test_capture_script_falls_back_to_local_git_when_no_github_sha(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("GITHUB_SHA", raising=False)
        monkeypatch.setattr(capture_provenance, "_local_git_commit_sha", lambda: "local-fallback-sha")
        payload = capture_provenance.capture_provenance(DATE)
        assert payload["commitSha"] == "local-fallback-sha"

    def test_snapshot_captures_real_production_commit_sha(self, tmp_path, monkeypatch):
        _wire_full_pregame_fixture(tmp_path, monkeypatch, provenance_commit_sha="realcommitsha123")
        monkeypatch.setattr(snap, "_git_commit_sha", lambda: "realcommitsha123")
        result = snap.build_snapshot(snap.STAGE_PRE_GAME_DECISION, DATE)
        manifest = result["manifest"]
        assert manifest["productionCommitSha"] == "realcommitsha123"
        assert manifest["productionProvenance"]["status"] == "CAPTURED"
        assert manifest["productionProvenance"]["workflowRunId"] == "123456"
        assert manifest["productionProvenance"]["ref"] == "refs/heads/main"
        provenance_component = next(c for c in manifest["components"] if c["componentType"] == "PRODUCTION_PROVENANCE")
        assert provenance_component["availabilityStatus"] == snap.AVAILABLE

    def test_missing_provenance_downgrades_completeness_to_missing_required_input(self, tmp_path, monkeypatch):
        _wire_full_pregame_fixture(tmp_path, monkeypatch, provenance_commit_sha=False)
        result = snap.build_snapshot(snap.STAGE_PRE_GAME_DECISION, DATE)
        manifest = result["manifest"]
        assert manifest["productionCommitSha"] is None
        assert manifest["productionProvenance"]["status"] == "MISSING"
        assert manifest["completenessStatus"] == snap.MISSING_REQUIRED_INPUT
        provenance_component = next(c for c in manifest["components"] if c["componentType"] == "PRODUCTION_PROVENANCE")
        assert provenance_component["availabilityStatus"] == snap.MISSING

    def test_ambiguous_commit_sha_never_trusted(self, tmp_path, monkeypatch):
        """Maintainer-review-grade honesty check (PR #37 review, item 1):
        the OLD mechanism cross-checked captured commitSha against the
        snapshot writer's own live `git rev-parse HEAD` -- found to be
        structurally inert in real CI, since both values are computed via
        the same GITHUB_SHA-preferring path and can never actually
        disagree inside a real GitHub Actions job. The REPLACEMENT
        mechanism is workingTreeDirty: a dirty CODE tree (scripts/lib/
        config) at capture time means the recorded commit SHA does not
        honestly describe what is about to execute, so it must never be
        trusted as CAPTURED regardless of what the SHA string itself
        says."""
        _wire_full_pregame_fixture(tmp_path, monkeypatch, provenance_commit_sha="stale-commit-sha", working_tree_dirty=True)
        result = snap.build_snapshot(snap.STAGE_PRE_GAME_DECISION, DATE)
        manifest = result["manifest"]
        assert manifest["productionCommitSha"] is None
        assert manifest["productionProvenance"]["status"] == "AMBIGUOUS"
        assert manifest["productionProvenance"]["workingTreeDirty"] is True
        assert manifest["completenessStatus"] in (snap.PARTIAL_REPLAY, snap.MISSING_REQUIRED_INPUT)
        provenance_component = next(c for c in manifest["components"] if c["componentType"] == "PRODUCTION_PROVENANCE")
        assert provenance_component["availabilityStatus"] == snap.PARTIAL
        assert provenance_component["limitationReason"] == "PRODUCTION_COMMIT_AMBIGUOUS"

    def test_unknown_dirty_state_is_treated_as_ambiguous_not_clean(self, tmp_path, monkeypatch):
        """workingTreeDirty=None (git itself failed, or an artifact written
        by code that predates this field) must never be silently treated
        as clean -- an unproven-clean code tree is exactly the case this
        mechanism exists to catch."""
        _wire_full_pregame_fixture(tmp_path, monkeypatch, provenance_commit_sha="some-sha", working_tree_dirty=None)
        result = snap.build_snapshot(snap.STAGE_PRE_GAME_DECISION, DATE)
        manifest = result["manifest"]
        assert manifest["productionCommitSha"] is None
        assert manifest["productionProvenance"]["status"] == "AMBIGUOUS"

    def test_never_reconstructed_from_ingestion_time_git_state_alone(self, tmp_path, monkeypatch):
        """Even if the live checkout's git state DOES resolve a real SHA,
        productionCommitSha must come from the EARLY-captured provenance
        artifact, never be silently synthesized from
        snapshotWriterCommitSha when the artifact itself is missing."""
        _wire_full_pregame_fixture(tmp_path, monkeypatch, provenance_commit_sha=False)
        monkeypatch.setattr(snap, "_git_commit_sha", lambda: "some-real-git-sha")
        result = snap.build_snapshot(snap.STAGE_PRE_GAME_DECISION, DATE)
        assert result["manifest"]["productionCommitSha"] is None


# ── Multiple runs / rerun-from-changed-commit ─────────────────────────────

class TestMultipleProductionRunsAndReruns:
    def test_two_production_runs_same_date_get_distinct_snapshots_and_commits(self, tmp_path, monkeypatch):
        _wire_full_pregame_fixture(tmp_path, monkeypatch, recommendations_created_at="2026-07-31T20:00:00Z", provenance_commit_sha="commitA")
        result1 = snap.build_snapshot(snap.STAGE_PRE_GAME_DECISION, DATE)

        # A genuine rerun (lineup recheck): new recommendations.json
        # createdAt (-> new productionRunKey) AND a real commit change
        # (e.g. a hotfix deployed between runs).
        _write_pipeline_artifact(
            "recommendations", DATE, {"games": [{"gameId": "1", "marketLedger": []}]},
            "scripts/build_market_ledger.py", created_at="2026-07-31T21:00:00Z",
        )
        _write_pipeline_artifact(
            "provenance", DATE,
            {"commitSha": "commitB", "gitHeadShaAtCapture": "commitB", "workingTreeDirty": False,
             "workflowRunId": "999999", "workflowRunAttempt": "1",
             "ref": "refs/heads/main", "refName": "main", "repository": "chmoses98/edge-finder-api",
             "workflow": "Fetch Slate Data", "job": "fetch", "eventName": "push"},
            "scripts/capture_production_provenance.py", created_at="2026-07-31T21:00:00Z",
        )
        monkeypatch.setattr(snap, "_git_commit_sha", lambda: "commitB")
        result2 = snap.build_snapshot(snap.STAGE_PRE_GAME_DECISION, DATE)

        assert result1["manifest"]["snapshotId"] != result2["manifest"]["snapshotId"]
        # Each run's own workingTreeDirty=False signal (recorded at capture
        # time by capture_production_provenance.py, not derived here) is
        # what makes both trustworthy -- not any comparison between them.
        assert result1["manifest"]["productionCommitSha"] == "commitA"
        assert result2["manifest"]["productionCommitSha"] == "commitB"
        assert len(snap.list_pregame_run_dirs(DATE)) == 2

    def test_hours_later_partial_rerun_mixing_stale_component_is_downgraded(self, tmp_path, monkeypatch):
        """Item 4 (maintainer review of PR #37): a genuine, documented
        production scenario -- fetch-slate.yml's own "starters not yet
        posted -- re-run after starters post (~3pm ET)" path (BLOCK 3b) --
        can leave an EARLIER partial attempt's stage artifacts (e.g. a
        provisional projections.json written before the not-ready exit)
        sitting next to a LATER, real run's fresh recommendations.json.
        Before this review tightened MAX_RUN_SKEW_HOURS from 6.0 to 1.0,
        a same-day mismatch several hours apart (a realistic gap for this
        exact rerun pattern) was silently accepted as
        COMPLETE_FOR_PRODUCTION_REPLAY. It must now be caught."""
        _wire_full_pregame_fixture(tmp_path, monkeypatch, recommendations_created_at="2026-07-31T15:00:00Z")

        # Only recommendations.json (and its co-produced provenance) get
        # a fresh, later timestamp -- projections.json is left over from
        # the earlier, not-ready attempt at 2026-07-31T11:00:00Z (a
        # 4-hour gap: below the OLD 6h threshold, above the NEW 1h one).
        stale_projections_path = pipeline_artifacts.artifact_path("projections", DATE)
        with open(stale_projections_path) as f:
            env = json.load(f)
        env["meta"]["createdAt"] = "2026-07-31T11:00:00Z"
        with open(stale_projections_path, "w") as f:
            json.dump(env, f)

        result = snap.build_snapshot(snap.STAGE_PRE_GAME_DECISION, DATE)
        manifest = result["manifest"]
        assert manifest["temporalConsistency"]["skewDetected"] is True
        assert manifest["completenessStatus"] in (snap.PARTIAL_REPLAY, snap.MISSING_REQUIRED_INPUT, snap.APPROXIMATE_ONLY)
        assert manifest["completenessStatus"] != snap.COMPLETE_FOR_PRODUCTION_REPLAY
        # ... and this specific scenario is REJECTED for replay purposes,
        # not merely downgraded to an approximate tier.
        eligibility = replay.assess_replay_eligibility(manifest)
        assert eligibility["eligibilityStatus"] == replay.INELIGIBLE_TEMPORAL_SKEW


# ── Item 1 (maintainer review of PR #37): adversarial real-git tests for
# scripts/capture_production_provenance.py's authenticity signals. Unlike
# every other test in this module, these run against a REAL git repository
# (git init'd inside tmp_path) rather than pipeline-artifact fixtures --
# workingTreeDirty, detached HEAD, and shallow-clone behavior are git
# mechanics that cannot be honestly exercised through mocked artifacts. ──

def _run_git(args, cwd, check=True, env=None):
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=15, env=env)
    if check and result.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {result.stderr}")
    return result


def _init_repo_with_code_dirs(root):
    _run_git(["init", "-q"], cwd=root)
    _run_git(["config", "user.email", "test@example.com"], cwd=root)
    _run_git(["config", "user.name", "Test"], cwd=root)
    os.makedirs(os.path.join(root, "scripts"), exist_ok=True)
    os.makedirs(os.path.join(root, "lib"), exist_ok=True)
    os.makedirs(os.path.join(root, "config"), exist_ok=True)
    os.makedirs(os.path.join(root, "data"), exist_ok=True)
    _write(os.path.join(root, "scripts", "build_market_ledger.py"), "SENTINEL = 1\n")
    _write(os.path.join(root, "data", "slate.json"), {"date": DATE})
    _run_git(["add", "-A"], cwd=root)
    _run_git(["commit", "-q", "-m", "initial"], cwd=root)
    return _run_git(["rev-parse", "HEAD"], cwd=root).stdout.strip()


class TestCaptureScriptRealGitAdversarial:
    def test_clean_code_tree_reports_not_dirty(self, tmp_path, monkeypatch):
        _init_repo_with_code_dirs(str(tmp_path))
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("GITHUB_SHA", raising=False)
        payload = capture_provenance.capture_provenance(DATE)
        assert payload["workingTreeDirty"] is False

    def test_dirty_code_path_is_detected(self, tmp_path, monkeypatch):
        """A local, uncommitted edit to a CODE file (scripts/) must flip
        workingTreeDirty True -- this is the real authenticity gap the
        maintainer review flagged: the code about to execute does not
        match any committed SHA."""
        _init_repo_with_code_dirs(str(tmp_path))
        with open(os.path.join(tmp_path, "scripts", "build_market_ledger.py"), "a") as f:
            f.write("# uncommitted local edit\n")
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("GITHUB_SHA", raising=False)
        payload = capture_provenance.capture_provenance(DATE)
        assert payload["workingTreeDirty"] is True

    def test_dirty_data_path_alone_does_not_flag_code_dirty(self, tmp_path, monkeypatch):
        """The dirty check is scoped to scripts/lib/config specifically --
        a legitimate uncommitted change to data/ (routine at this capture
        position in fetch-slate.yml, from earlier-in-job fetch steps) must
        NOT false-positive as an untrustworthy code tree."""
        _init_repo_with_code_dirs(str(tmp_path))
        with open(os.path.join(tmp_path, "data", "slate.json"), "w") as f:
            json.dump({"date": DATE, "modified": True}, f)
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("GITHUB_SHA", raising=False)
        payload = capture_provenance.capture_provenance(DATE)
        assert payload["workingTreeDirty"] is False

    def test_detached_head_still_resolves_commit_sha_honestly(self, tmp_path, monkeypatch):
        """Item 1's explicit adversarial case: detached HEAD must not
        break or fabricate provenance -- `git rev-parse HEAD` is robust to
        detached HEAD by construction, and this workflow's real trigger
        surface (push/workflow_dispatch, never pull_request) never
        actually produces one in production, but the capture logic itself
        must not assume an attached branch."""
        sha = _init_repo_with_code_dirs(str(tmp_path))
        _run_git(["checkout", "-q", sha], cwd=str(tmp_path))  # detaches HEAD
        status = _run_git(["status", "--branch", "--porcelain=v2"], cwd=str(tmp_path)).stdout
        assert "branch.head (detached)" in status or "detached" in status.lower()
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("GITHUB_SHA", raising=False)
        payload = capture_provenance.capture_provenance(DATE)
        assert payload["commitSha"] == sha
        assert payload["gitHeadShaAtCapture"] == sha
        assert payload["workingTreeDirty"] is False

    def test_shallow_clone_still_resolves_commit_sha_honestly(self, tmp_path, monkeypatch):
        """Item 1's explicit adversarial case: a shallow clone (as
        actions/checkout@v4 performs by default, fetch-depth: 1) must
        still resolve a real, honest commit SHA -- `git rev-parse HEAD`
        does not require full history."""
        origin = tmp_path / "origin"
        os.makedirs(origin, exist_ok=True)
        sha = _init_repo_with_code_dirs(str(origin))
        # A second commit so the shallow clone has real history to omit.
        _write(os.path.join(str(origin), "scripts", "risk_gate.py"), "X = 1\n")
        _run_git(["add", "-A"], cwd=str(origin))
        _run_git(["commit", "-q", "-m", "second"], cwd=str(origin))
        second_sha = _run_git(["rev-parse", "HEAD"], cwd=str(origin)).stdout.strip()
        assert second_sha != sha

        clone_dir = tmp_path / "shallow_clone"
        # --no-local forces git to honor --depth even for a same-filesystem
        # path (its default "local clone" optimization otherwise silently
        # hardlinks full history and ignores --depth).
        _run_git(["clone", "-q", "--no-local", "--depth", "1", str(origin), str(clone_dir)], cwd=str(tmp_path))
        log_count = _run_git(["rev-list", "--count", "HEAD"], cwd=str(clone_dir)).stdout.strip()
        assert log_count == "1"  # confirms the clone really is shallow

        monkeypatch.chdir(clone_dir)
        monkeypatch.delenv("GITHUB_SHA", raising=False)
        payload = capture_provenance.capture_provenance(DATE)
        assert payload["commitSha"] == second_sha
        assert payload["workingTreeDirty"] is False

    def test_conflicting_recorded_sha_vs_live_git_state_surfaced_not_hidden(self, tmp_path, monkeypatch):
        """Item 1's explicit adversarial case: GITHUB_SHA (trusted,
        authoritative -- the Actions runner's own record of what it
        checked out) can legitimately diverge from live HEAD by the time
        this script runs, e.g. if something else in the job moved HEAD.
        commitSha must stay GITHUB_SHA (never silently overridden by live
        git state), while gitHeadShaAtCapture honestly records the live
        value so the divergence is visible to a human auditor rather than
        silently discarded."""
        _init_repo_with_code_dirs(str(tmp_path))
        real_head = _run_git(["rev-parse", "HEAD"], cwd=str(tmp_path)).stdout.strip()
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("GITHUB_SHA", "0" * 40)  # deliberately conflicts with real_head
        payload = capture_provenance.capture_provenance(DATE)
        assert payload["commitSha"] == "0" * 40
        assert payload["gitHeadShaAtCapture"] == real_head
        assert payload["commitSha"] != payload["gitHeadShaAtCapture"]

    def test_incorrect_empty_github_sha_falls_back_to_live_git_not_left_blank(self, tmp_path, monkeypatch):
        """Item 1's explicit adversarial case: an incorrectly-set empty-string
        GITHUB_SHA (a malformed/incorrect env var, distinct from truly
        unset) must not be trusted verbatim -- `"" or x` falls through to
        the live-git fallback in Python, same as unset, so this is
        exercised explicitly rather than assumed."""
        sha = _init_repo_with_code_dirs(str(tmp_path))
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("GITHUB_SHA", "")
        payload = capture_provenance.capture_provenance(DATE)
        assert payload["commitSha"] == sha

    def test_git_binary_failure_reports_unknown_dirty_state_not_false_clean(self, tmp_path, monkeypatch):
        """If git itself cannot be invoked, workingTreeDirty must be None
        (unknown), never False (silently trusted clean) -- an unproven
        state must read as untrustworthy, matching
        lib.edgelab.snapshot._production_provenance's handling of None."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(capture_provenance.subprocess, "run",
                             lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError("no git binary")))
        assert capture_provenance._code_tree_dirty() is None


# ── Item 3: effective-config capture ──────────────────────────────────────

class TestEffectiveConfigCapture:
    def test_live_constants_introspected_from_real_modules(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        record = snap.capture_effective_config(DATE, "sha1", production_commit_sha="prodsha", production_run_id="runkey")
        assert record["liveConstants"]["THRESHOLD_HIGH"] == 3.0
        assert record["liveConstants"]["REAL_MONEY_TIERS"] == ["HIGH", "MEDIUM"]
        assert "REQUIRED_MARKETS" in record["liveConstants"]
        # Added under the PR #37 maintainer review (item 3): these were
        # real, decision-driving, directly-introspectable constants this
        # milestone had previously left uncaptured.
        assert record["liveConstants"]["MARKET_MULTIPLIERS"]["TT_Away_Over"] == 1.25
        assert record["liveConstants"]["TT_CRITICAL_FIELDS"] == [
            "awayProjRuns", "homeProjRuns", "kalshiPrice", "modelProb", "line",
        ]
        assert record["liveConstants"]["TT_CRITICAL_SIDE_FIELDS"] == {
            "TT_Away_Over": "awayProjRuns", "TT_Home_Over": "homeProjRuns",
        }
        assert record["productionCommitSha"] == "prodsha"
        assert record["productionRunId"] == "runkey"

    def test_hardcoded_logic_source_hashes_present_and_stable(self, tmp_path, monkeypatch):
        # Deliberately NOT chdir'd to tmp_path here: the source files this
        # hashes (scripts/build_market_ledger.py etc.) only exist relative
        # to the real repo root -- this proves the real-checkout behavior.
        record1 = snap.capture_effective_config(DATE, "sha1")
        record2 = snap.capture_effective_config(DATE, "sha1")
        assert record1["hardcodedLogicSourceHashes"] == record2["hardcodedLogicSourceHashes"]
        assert all(h is not None for h in record1["hardcodedLogicSourceHashes"].values())

    def test_source_hash_honestly_null_when_file_does_not_exist_at_cwd(self, tmp_path, monkeypatch):
        """CWD-relative by the same convention as every other path in this
        module -- a chdir'd-away cwd (no scripts/ directory present) must
        report None, never crash or silently resolve against ROOT_DIR."""
        monkeypatch.chdir(tmp_path)
        record = snap.capture_effective_config(DATE, "sha1")
        assert all(h is None for h in record["hardcodedLogicSourceHashes"].values())

    def test_unrepresented_logic_explicitly_classified(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        record = snap.capture_effective_config(DATE, "sha1")
        assert len(record["unrepresentedLogic"]) > 0
        for entry in record["unrepresentedLogic"]:
            assert "description" in entry and "location" in entry

    def test_effective_config_hash_is_deterministic_and_content_bound(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        record1 = snap.capture_effective_config(DATE, "sha1", production_commit_sha="p1")
        record2 = snap.capture_effective_config(DATE, "sha1", production_commit_sha="p1")
        record3 = snap.capture_effective_config(DATE, "sha1", production_commit_sha="p2")
        assert record1["effectiveConfigHash"] == record2["effectiveConfigHash"]
        assert record1["effectiveConfigHash"] != record3["effectiveConfigHash"]


# ── Item 6: automatic candidate replay ────────────────────────────────────

class TestAutomaticForwardReplay:
    def test_no_snapshot_yet_is_skipped_honestly(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = subprocess.run(
            [sys.executable, os.path.join(ROOT, "scripts", "run_forward_replay.py"), DATE],
            cwd=tmp_path, capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0
        status_path = os.path.join(tmp_path, "data", "edgelab", "forward_replay_status.json")
        with open(status_path) as f:
            status = json.load(f)
        assert status[DATE]["outcome"] == "no_snapshot"

    def test_valid_snapshot_produces_completed_replay(self, tmp_path, monkeypatch):
        game = _make_minimal_game()
        _wire_full_pregame_fixture_with_game(tmp_path, monkeypatch, game)
        snap.build_snapshot(snap.STAGE_PRE_GAME_DECISION, DATE)

        result = subprocess.run(
            [sys.executable, os.path.join(ROOT, "scripts", "run_forward_replay.py"), DATE],
            cwd=tmp_path, capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, result.stderr
        output = json.loads(result.stdout)
        assert output["runStatus"] == "COMPLETED"
        assert output["eligibilityStatus"] == "ELIGIBLE_LEVEL_2"
        status_path = os.path.join(tmp_path, "data", "edgelab", "forward_replay_status.json")
        with open(status_path) as f:
            status = json.load(f)
        assert status[DATE]["runStatus"] == "COMPLETED"

    def test_forward_replay_never_writes_production_files(self, tmp_path, monkeypatch):
        """Item 13 (maintainer review of PR #37): sentinel-content,
        filesystem-diff proof -- not mere import inspection -- run through
        the ACTUAL automatic entrypoint fetch-slate.yml invokes
        (scripts/run_forward_replay.py as a real subprocess), covering
        every live/production file item 13 names: data/slate.json,
        bets.json, BET_LOG.md, pending-bet-adjacent files
        (data/pending_bets.json), live recommendations
        (data/pipeline/<date>/recommendations.json), risk-gate output
        (data/pipeline/<date>/execution.json), an execution slip
        (data/execution_slip.json), the settlement ledger
        (data/edgelab/settlements/<date>.jsonl), CLV production records
        (data/edgelab/clv_quotes/<date>.jsonl), and model configuration
        (config/rules.json)."""
        game = _make_minimal_game()
        _wire_full_pregame_fixture_with_game(tmp_path, monkeypatch, game)
        snap.build_snapshot(snap.STAGE_PRE_GAME_DECISION, DATE)

        sentinels = {
            "data/slate.json": b'{"date": "SENTINEL_SLATE", "games": []}',
            "data/bets.json": b'{"bets": ["SENTINEL_BET_LEDGER"]}',
            "BET_LOG.md": b"# SENTINEL_BET_LOG\n",
            "data/pending_bets.json": b'{"pending": ["SENTINEL_PENDING"]}',
            os.path.join("data", "pipeline", DATE, "recommendations.json"): b'{"data": {"games": ["SENTINEL_RECS"]}}',
            os.path.join("data", "pipeline", DATE, "execution.json"): b'{"data": {"candidates": ["SENTINEL_EXEC"]}}',
            "data/execution_slip.json": b'{"slip": "SENTINEL_SLIP"}',
            os.path.join("data", "edgelab", "settlements", f"{DATE}.jsonl"): b'{"marketTicker": "SENTINEL_SETTLEMENT"}\n',
            os.path.join("data", "edgelab", "clv_quotes", f"{DATE}.jsonl"): b'{"marketTicker": "SENTINEL_CLV"}\n',
            "config/rules.json": b'{"_version": "SENTINEL_CONFIG"}',
        }
        for path, content in sentinels.items():
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(path, "wb") as f:
                f.write(content)

        result = subprocess.run(
            [sys.executable, os.path.join(ROOT, "scripts", "run_forward_replay.py"), DATE],
            cwd=tmp_path, capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, result.stderr

        for path, content in sentinels.items():
            with open(path, "rb") as f:
                assert f.read() == content, f"{path} was modified by run_forward_replay.py"

    def test_ineligible_snapshot_is_rejected_not_downgraded(self, tmp_path, monkeypatch):
        _wire_full_pregame_fixture(tmp_path, monkeypatch, provenance_commit_sha=False)
        os.remove(os.path.join("data", "weather.json"))
        snap.build_snapshot(snap.STAGE_PRE_GAME_DECISION, DATE)

        result = subprocess.run(
            [sys.executable, os.path.join(ROOT, "scripts", "run_forward_replay.py"), DATE],
            cwd=tmp_path, capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0  # never fails the workflow
        output = json.loads(result.stdout)
        assert output["runStatus"] == "REJECTED_INELIGIBLE"


def _make_minimal_game():
    return {
        'gameId': '555555',
        'away': {'abbr': 'AAA', 'team': 'Away Team', 'pitcher': {'name': 'SP Away'},
                  'pitcherSavant': {'xFIP': 5.5, 'seasonFIP': 5.5, 'recentFIP': 5.4, 'avgIPperStart': 5.0,
                                     'openerRole': False, 'ttoSplit': 0.3, 'ttoAvailable': True,
                                     'tto1': {'fip': 5.5, 'gamesUsed': 5}, 'tto3': {'fip': 5.2, 'gamesUsed': 3}},
                  'bullpen': {'xFIP': 4.5, 'hlGrade': 'AVERAGE', 'hlAvailable': True, 'hlXFIP': 4.5}},
        'home': {'abbr': 'HHH', 'team': 'Home Team', 'pitcher': {'name': 'SP Home'},
                  'pitcherSavant': {'xFIP': 3.5, 'seasonFIP': 3.5, 'recentFIP': 3.4, 'avgIPperStart': 6.0,
                                     'openerRole': False, 'ttoSplit': 0.1, 'ttoAvailable': True,
                                     'tto1': {'fip': 3.5, 'gamesUsed': 5}, 'tto3': {'fip': 3.4, 'gamesUsed': 3}},
                  'bullpen': {'xFIP': 3.8, 'hlGrade': 'ABOVE_AVERAGE', 'hlAvailable': True, 'hlXFIP': 3.7}},
        'awayTeamStats': {'offenseBaselineAdj': 5.2, 'lineupConfirmed': True, 'lineupConfirmedOfficial': True,
                           'lineupPosted': True, 'lineupStatus': 'confirmed', 'lineupSource': 'mlb_stats_api',
                           'lineupBattersExpected': 9, 'lineupBattersFound': 9, 'lineupBattersResolved': 9,
                           'lineupAdjAvailable': True, 'lineupAdjApplied': True, 'lineupDataQuality': 'official',
                           'lineupStatusReason': '', 'lineupAdj': 0.05},
        'homeTeamStats': {'offenseBaselineAdj': 4.0, 'lineupConfirmed': True, 'lineupConfirmedOfficial': True,
                           'lineupPosted': True, 'lineupStatus': 'confirmed', 'lineupSource': 'mlb_stats_api',
                           'lineupBattersExpected': 9, 'lineupBattersFound': 9, 'lineupBattersResolved': 9,
                           'lineupAdjAvailable': True, 'lineupAdjApplied': True, 'lineupDataQuality': 'official',
                           'lineupStatusReason': '', 'lineupAdj': 0.02},
        'park': {'parkFactor': 100}, 'pinnacleVF': {'away': 48.0, 'home': 52.0},
        'oddsApiCommenceTime': '2026-07-31T19:45:00Z', 'kalshiKey': 'AAAHH', 'kalshiGameTime': '1545',
        'odds': {'kalshi': {
            'ml': {'away': -130, 'home': 120, 'away_ticker': 'KXMLBGAME-26JUL311545AAAHH-AAA',
                   'home_ticker': 'KXMLBGAME-26JUL311545AAAHH-HHH', 'source': 'kalshi_registry'},
            'nrfi_yrfi': {'ticker': 'KXMLBRFI-26JUL311545AAAHH', 'nrfi_american': -115, 'yrfi_american': 108,
                          'nrfi_implied': 53.0, 'yrfi_implied': 47.0, 'source': 'kalshi_registry'},
            'f5ml': {'away': -120, 'home': 110, 'away_ticker': 'KXMLBF5-26JUL311545AAAHH-AAA',
                     'home_ticker': 'KXMLBF5-26JUL311545AAAHH-HHH', 'source': 'kalshi_registry'},
            'team_totals': {
                'away': {'best_ticker': 'KXMLBTEAMTOTAL-26JUL311545AAAHH-AAA5', 'line': 5, 'american': 120, 'implied_pct': 44.0},
                'home': {'best_ticker': 'KXMLBTEAMTOTAL-26JUL311545AAAHH-HHH4', 'line': 4, 'american': 130, 'implied_pct': 43.0},
            },
            'rl': {'best_ticker': 'KXMLBSPREAD-26JUL311545AAAHH-HHH2', 'american': 133, 'implied_pct': 43.0, 'team': 'HHH'},
            'total': {'best_ticker': 'KXMLBTOTAL-26JUL311545AAAHH-9', 'line': 8, 'american': -105},
        }},
    }


def _wire_full_pregame_fixture_with_game(tmp_path, monkeypatch, game):
    from scripts.build_market_ledger import compute_game_projection_context, evaluate_game
    from scripts import risk_gate as _risk_gate
    from lib.edgelab import replay as _replay

    _wire_full_pregame_fixture(tmp_path, monkeypatch)
    projection_context = compute_game_projection_context(game)
    ledger = evaluate_game(game, projection_context)
    slate = {"date": DATE, "games": [{**game, "marketLedger": ledger}]}
    _risk_gate.apply_tt_safety(slate)
    decision, report = _risk_gate.apply_portfolio_rules(slate)
    if decision == "PAPER_ONLY":
        _replay._apply_paper_only_downgrade(slate, report["decision_reason"])
    execution_payload = _risk_gate.build_execution_artifact_payload(slate, decision, report["decision_reason"])

    _write_pipeline_artifact("execution", DATE, execution_payload, "scripts/risk_gate.py")
    _write_pipeline_artifact(
        "recommendations", DATE,
        {"games": [{"gameId": game["gameId"], "away": game["away"], "home": game["home"], "marketLedger": ledger}]},
        "scripts/build_market_ledger.py",
    )
    _write_pipeline_artifact("normalized_slate", DATE, {"games": [game]}, "scripts/enrich_data.py")


# ── Item 8: CLV closing-quote disambiguation ──────────────────────────────

class TestClosingClvDisambiguation:
    def test_only_isclosingquote_row_is_used(self):
        rows = [
            {"marketTicker": "T1", "checkpoint": "T_MINUS_90", "isClosingQuote": False, "yesBid": 10, "yesAsk": 15},
            {"marketTicker": "T1", "checkpoint": "T_MINUS_5", "isClosingQuote": True, "yesBid": 60, "yesAsk": 64},
        ]
        resolved, ambiguous = replay._closing_clv_by_ticker(rows)
        assert resolved["T1"]["yesBid"] == 60
        assert ambiguous == []

    def test_no_closing_flagged_row_is_unresolved_not_guessed(self):
        rows = [
            {"marketTicker": "T1", "checkpoint": "T_MINUS_90", "isClosingQuote": False, "yesBid": 10, "yesAsk": 15},
            {"marketTicker": "T1", "checkpoint": "FIRST_DAILY", "isClosingQuote": False, "yesBid": 5, "yesAsk": 8},
        ]
        resolved, ambiguous = replay._closing_clv_by_ticker(rows)
        assert "T1" not in resolved
        assert ambiguous == []
        linkage = replay._clv_linkage_for_ticker(resolved, None, "T1", 50.0)
        assert linkage["status"] == "UNRESOLVED"
        assert linkage["reason"] == "NO_CLV_QUOTE_FOR_THIS_MARKET"

    def test_multiple_closing_flagged_rows_is_ambiguous(self):
        """A genuine upstream data-quality issue -- never expected in
        practice, but must be reported, never silently resolved by
        picking whichever one happens to iterate last."""
        rows = [
            {"marketTicker": "T1", "checkpoint": "T_MINUS_90", "isClosingQuote": True, "yesBid": 10, "yesAsk": 15},
            {"marketTicker": "T1", "checkpoint": "T_MINUS_5", "isClosingQuote": True, "yesBid": 60, "yesAsk": 64},
        ]
        resolved, ambiguous = replay._closing_clv_by_ticker(rows)
        assert "T1" not in resolved
        assert ambiguous == ["T1"]

    def test_last_row_in_file_order_is_not_silently_preferred(self):
        """Direct regression test for the real defect found against
        2026-08-02 data: 620/4844 tickers had multiple rows, and the OLD
        dict-comprehension logic silently kept whichever was last in the
        file -- here the LAST row is deliberately the non-closing one."""
        rows = [
            {"marketTicker": "T1", "checkpoint": "T_MINUS_5", "isClosingQuote": True, "yesBid": 60, "yesAsk": 64},
            {"marketTicker": "T1", "checkpoint": "POST_START", "isClosingQuote": False, "yesBid": 1, "yesAsk": 2},
        ]
        resolved, _ambiguous = replay._closing_clv_by_ticker(rows)
        assert resolved["T1"]["yesBid"] == 60


# ── Item 4: missing-snapshot detection distinguishes reruns ───────────────

class TestPerRunSnapshotDetection:
    def test_current_production_run_key_matches_latest_recommendations(self, tmp_path, monkeypatch):
        _wire_full_pregame_fixture(tmp_path, monkeypatch, recommendations_created_at="2026-07-31T20:00:00Z")
        assert snap.current_production_run_key(DATE) == "2026-07-31T20:00:00Z"

    def test_second_run_without_its_own_snapshot_is_detected_as_missing(self, tmp_path, monkeypatch):
        """Maintainer-review-grade fix: an EARLIER run's snapshot existing
        must not hide a LATER (current) run's own missing snapshot."""
        sys.path.insert(0, os.path.join(ROOT, "scripts"))
        import check_snapshot_capture as checker
        import importlib
        importlib.reload(checker)

        _wire_full_pregame_fixture(tmp_path, monkeypatch, recommendations_created_at="2026-07-31T20:00:00Z")
        snap.build_snapshot(snap.STAGE_PRE_GAME_DECISION, DATE)
        assert checker._has_pregame_snapshot_for_current_run(DATE) is True

        # A second, later run overwrites recommendations.json with a new
        # run key -- its OWN snapshot has not been captured yet.
        _write_pipeline_artifact(
            "recommendations", DATE, {"games": [{"gameId": "1", "marketLedger": []}]},
            "scripts/build_market_ledger.py", created_at="2026-07-31T21:30:00Z",
        )
        assert checker._has_pregame_snapshot_for_current_run(DATE) is False

    def test_two_runs_one_missing_snapshot_is_flagged_via_full_check_and_recover(self, tmp_path, monkeypatch):
        """Item 9's explicit scenario, run through the actual entry point
        (check_and_recover), not just the lower-level detector."""
        sys.path.insert(0, os.path.join(ROOT, "scripts"))
        import check_snapshot_capture as checker
        import importlib
        importlib.reload(checker)

        _wire_full_pregame_fixture(tmp_path, monkeypatch, recommendations_created_at="2026-07-31T20:00:00Z")
        snap.build_snapshot(snap.STAGE_PRE_GAME_DECISION, DATE)
        # Second run's recommendations exist (evidence of a real attempt)
        # but nothing captured its own snapshot -- and its own source
        # inputs (kalshi registry etc.) are still on disk, so recovery
        # should succeed rather than merely detect the gap.
        _write_pipeline_artifact(
            "recommendations", DATE, {"games": [{"gameId": "1", "marketLedger": []}]},
            "scripts/build_market_ledger.py", created_at="2026-07-31T21:30:00Z",
        )
        report = checker.check_and_recover(lookback_days=14)
        pregame = report["checkedStages"][snap.STAGE_PRE_GAME_DECISION]
        assert DATE in pregame["missingBeforeRecovery"]
        assert any(r["date"] == DATE for r in pregame["recovered"])
        assert report["anyUnrecoveredGaps"] is False

    def test_failed_run_with_no_recommendations_creates_no_expectation(self, tmp_path, monkeypatch):
        """Item 9: a run that failed before ever writing recommendations.json
        (e.g. pre-validation hard-fail) leaves no evidence a decision was
        made -- it must not be treated as an expected-but-missing snapshot."""
        sys.path.insert(0, os.path.join(ROOT, "scripts"))
        import check_snapshot_capture as checker
        import importlib
        importlib.reload(checker)
        monkeypatch.chdir(tmp_path)
        # Only a bare pipeline directory exists (e.g. left by an earlier,
        # partial write); no recommendations.json.
        os.makedirs(os.path.join("data", "pipeline", DATE), exist_ok=True)

        report = checker.check_and_recover(lookback_days=14)
        pregame = report["checkedStages"][snap.STAGE_PRE_GAME_DECISION]
        assert DATE not in pregame["expectedDates"]
        assert report["anyUnrecoveredGaps"] is False

    def test_no_slate_day_produces_empty_report_not_a_failure(self, tmp_path, monkeypatch):
        """Item 9: a genuine no-slate/no-market day (no data/ dirs at all)
        must never be mistaken for a capture failure."""
        sys.path.insert(0, os.path.join(ROOT, "scripts"))
        import check_snapshot_capture as checker
        import importlib
        importlib.reload(checker)
        monkeypatch.chdir(tmp_path)

        report = checker.check_and_recover(lookback_days=14)
        for stage_detail in report["checkedStages"].values():
            assert stage_detail["expectedDates"] == []
        assert report["anyUnrecoveredGaps"] is False

    def test_recovery_honestly_fails_when_mutable_inputs_already_overwritten(self, tmp_path, monkeypatch):
        """Item 9: recovery must never fabricate data that's since been
        overwritten/pruned -- if a REQUIRED input (here: the Kalshi market
        universe snapshot) is gone by the time recovery runs, the rebuilt
        manifest must honestly report MISSING_REQUIRED_INPUT, and the
        check script must NOT count that as a silently-successful
        recovery."""
        sys.path.insert(0, os.path.join(ROOT, "scripts"))
        import check_snapshot_capture as checker
        import importlib
        importlib.reload(checker)

        _wire_full_pregame_fixture(tmp_path, monkeypatch, recommendations_created_at="2026-07-31T20:00:00Z")
        # Simulate the required market-universe input having since been
        # overwritten/pruned before recovery ever runs.
        os.remove(os.path.join("data", "kalshi_registry_snapshots", f"kalshi_search_{DATE}.json"))

        report = checker.check_and_recover(lookback_days=14)
        pregame = report["checkedStages"][snap.STAGE_PRE_GAME_DECISION]
        assert DATE in pregame["missingBeforeRecovery"]
        recovered_entry = next((r for r in pregame["recovered"] if r["date"] == DATE), None)
        assert recovered_entry is not None  # build_snapshot() still succeeds structurally...
        assert recovered_entry["completenessStatus"] == snap.MISSING_REQUIRED_INPUT  # ...but honestly degraded, never fabricated


# ── Item 10: corpus health report ─────────────────────────────────────────

class TestCorpusHealthReport:
    def test_report_runs_and_reflects_real_captured_state(self, tmp_path, monkeypatch):
        game = _make_minimal_game()
        _wire_full_pregame_fixture_with_game(tmp_path, monkeypatch, game)
        snap.build_snapshot(snap.STAGE_PRE_GAME_DECISION, DATE)

        result = subprocess.run(
            [sys.executable, os.path.join(ROOT, "scripts", "run_forward_replay.py"), DATE],
            cwd=tmp_path, capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, result.stderr

        result = subprocess.run(
            [sys.executable, os.path.join(ROOT, "scripts", "corpus_health_report.py")],
            cwd=tmp_path, capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, result.stderr
        report = json.loads(result.stdout)
        assert report["productionRuns"] == 1
        assert report["snapshotsSuccessfullyCaptured"] == 1
        assert report["candidateReplays"]["completed"] == 1
        assert report["missingSnapshots"] == []
        report_path = os.path.join(tmp_path, "data", "edgelab", "reports", "corpus_health_report.json")
        assert os.path.exists(report_path)
        md_path = os.path.join(tmp_path, "data", "edgelab", "reports", "corpus_health_report.md")
        assert os.path.exists(md_path)

    def test_missing_snapshot_is_flagged_degraded(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        os.makedirs(os.path.join("data", "pipeline", DATE), exist_ok=True)
        _write(os.path.join("data", "pipeline", DATE, "recommendations.json"),
               {"meta": {"createdAt": "2026-07-31T20:00:00Z"}, "data": {"games": []}})
        result = subprocess.run(
            [sys.executable, os.path.join(ROOT, "scripts", "corpus_health_report.py")],
            cwd=tmp_path, capture_output=True, text=True, timeout=30,
        )
        report = json.loads(result.stdout)
        assert DATE in report["missingSnapshots"]
        report_path = os.path.join(tmp_path, "data", "edgelab", "reports", "corpus_health_report.json")
        with open(report_path) as f:
            full_report = json.load(f)
        rec = next(r for r in full_report["perDate"] if r["date"] == DATE)
        assert rec["gateStatus"] == "DEGRADED_MISSING_SNAPSHOT"

    def test_three_consecutive_degraded_dates_fails_the_check(self, tmp_path, monkeypatch):
        """Item 10 (maintainer review of PR #37): before this review, this
        script computed consecutiveDegradedRuns and printed an ALERT: line
        but ALWAYS exited 0 -- and no workflow in this repository ever ran
        it at all. A dedicated check that can never fail is not a check.
        Confirms the script's own exit code is now meaningful."""
        monkeypatch.chdir(tmp_path)
        for date in ("2026-07-29", "2026-07-30", "2026-07-31"):
            os.makedirs(os.path.join("data", "pipeline", date), exist_ok=True)
            _write(os.path.join("data", "pipeline", date, "recommendations.json"),
                   {"meta": {"createdAt": f"{date}T20:00:00Z"}, "data": {"games": []}})
        result = subprocess.run(
            [sys.executable, os.path.join(ROOT, "scripts", "corpus_health_report.py")],
            cwd=tmp_path, capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 1
        assert "ALERT" in result.stderr

    def test_healthy_corpus_exits_zero(self, tmp_path, monkeypatch):
        """The exit-code fix must not turn an ordinary healthy report red."""
        game = _make_minimal_game()
        _wire_full_pregame_fixture_with_game(tmp_path, monkeypatch, game)
        snap.build_snapshot(snap.STAGE_PRE_GAME_DECISION, DATE)
        subprocess.run(
            [sys.executable, os.path.join(ROOT, "scripts", "run_forward_replay.py"), DATE],
            cwd=tmp_path, capture_output=True, text=True, timeout=30,
        )
        result = subprocess.run(
            [sys.executable, os.path.join(ROOT, "scripts", "corpus_health_report.py")],
            cwd=tmp_path, capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, result.stderr


# ── Item 12: storage report replay-runs bucket ────────────────────────────

class TestStorageReportReplayBucket:
    def test_replay_bucket_reflects_real_replay_output(self, tmp_path, monkeypatch):
        game = _make_minimal_game()
        _wire_full_pregame_fixture_with_game(tmp_path, monkeypatch, game)
        snap.build_snapshot(snap.STAGE_PRE_GAME_DECISION, DATE)
        subprocess.run(
            [sys.executable, os.path.join(ROOT, "scripts", "run_forward_replay.py"), DATE],
            cwd=tmp_path, capture_output=True, text=True, timeout=30,
        )
        result = subprocess.run(
            [sys.executable, os.path.join(ROOT, "scripts", "snapshot_storage_report.py")],
            cwd=tmp_path, capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, result.stderr
        report = json.loads(result.stdout)
        assert report["replayRuns"]["runs"] == 1
        assert report["replayRuns"]["totalBytes"] > 0
        assert "1Season" in report["totalProjectedBytes"]
        assert "3Seasons" in report["totalProjectedBytes"]
        assert "5Seasons" in report["totalProjectedBytes"]
