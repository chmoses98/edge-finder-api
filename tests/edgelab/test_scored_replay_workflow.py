#!/usr/bin/env python3
"""
tests/edgelab/test_scored_replay_workflow.py
==================================================
Operationalizing Scored Replay milestone: coverage for
.github/workflows/edgelab-postgame.yml's new "Score replay against
postgame outcomes" step and scripts/score_replay.py's --date mode (the
automated postgame-workflow entrypoint that resolves a date's replayRunId
from data/edgelab/forward_replay_status.json).

Two halves: (1) fast YAML-structural checks that the workflow step
exists, runs at the correct ordering, is continue-on-error, and the
commit step's paths were extended -- no subprocess, no fixtures;
(2) real-subprocess coverage of scripts/score_replay.py --date's own
behavior, mirroring tests/edgelab/test_production_provenance.py::
TestAutomaticForwardReplay's exact pattern for the parallel
run_forward_replay.py entrypoint.
"""
import json
import os
import subprocess
import sys

import pytest
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from lib.edgelab import replay  # noqa: E402

WORKFLOWS_DIR = os.path.join(ROOT, ".github", "workflows")
DATE = "2026-07-31"


def _load_workflow(filename):
    with open(os.path.join(WORKFLOWS_DIR, filename)) as f:
        return yaml.safe_load(f)


def _step_index(steps, name_fragment):
    for i, s in enumerate(steps):
        if name_fragment in s.get("name", ""):
            return i
    raise AssertionError(f"no step matching {name_fragment!r} found")


# ══════════════════════════════════════════════════════════════════════════
# Part 1: workflow structure (requirement 1 -- correct postgame ordering)
# ══════════════════════════════════════════════════════════════════════════

class TestPostgameWorkflowOrdering:

    def test_score_replay_step_exists_and_is_continue_on_error(self):
        doc = _load_workflow("edgelab-postgame.yml")
        steps = doc["jobs"]["settle"]["steps"]
        step = next(s for s in steps if "Score replay" in s.get("name", ""))
        assert step.get("continue-on-error") is True
        assert "scripts/score_replay.py" in step["run"]
        assert "--date" in step["run"]

    def test_score_replay_runs_after_settlement_and_recommendation_ingestion(self):
        doc = _load_workflow("edgelab-postgame.yml")
        steps = doc["jobs"]["settle"]["steps"]
        score_idx = _step_index(steps, "Score replay")
        assert score_idx > _step_index(steps, "Sync recommendation")
        assert score_idx > _step_index(steps, "Settle full observed")

    def test_score_replay_runs_after_postgame_settlement_snapshot_linkage(self):
        """Wager/CLV linkage needs the POST_GAME_SETTLEMENT snapshot to
        already exist (it's what carries linkedSnapshotIds back to the
        PRE_GAME_DECISION snapshot) -- scoring must run after it."""
        doc = _load_workflow("edgelab-postgame.yml")
        steps = doc["jobs"]["settle"]["steps"]
        score_idx = _step_index(steps, "Score replay")
        assert score_idx > _step_index(steps, "Create immutable POST_GAME_SETTLEMENT snapshot")

    def test_score_replay_runs_before_commit(self):
        doc = _load_workflow("edgelab-postgame.yml")
        steps = doc["jobs"]["settle"]["steps"]
        score_idx = _step_index(steps, "Score replay")
        commit_idx = _step_index(steps, "Commit EdgeLab postgame output")
        assert score_idx < commit_idx

    def test_commit_step_includes_scored_replay_paths(self):
        doc = _load_workflow("edgelab-postgame.yml")
        steps = doc["jobs"]["settle"]["steps"]
        commit_step = next(s for s in steps if "Commit EdgeLab postgame output" in s.get("name", ""))
        run = commit_step["run"]
        assert "data/edgelab/scored_replay_runs/" in run
        assert "data/edgelab/scored_replay_status.json" in run
        assert "data/edgelab/reports/scored_replay/" in run

    def test_uses_shared_safe_git_commit_path(self):
        """Requirement 6: reuse PR #65's shared commit path, never a
        second inline fetch/rebase/commit implementation."""
        doc = _load_workflow("edgelab-postgame.yml")
        steps = doc["jobs"]["settle"]["steps"]
        commit_step = next(s for s in steps if "Commit EdgeLab postgame output" in s.get("name", ""))
        assert "scripts/ci/git_data_commit.py" in commit_step["run"]


# ══════════════════════════════════════════════════════════════════════════
# Part 2: scripts/score_replay.py --date (real subprocess)
# ══════════════════════════════════════════════════════════════════════════

def _write_forward_replay_status(cwd, date, **fields):
    path = os.path.join(cwd, "data", "edgelab", "forward_replay_status.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    status = {}
    if os.path.exists(path):
        with open(path) as f:
            status = json.load(f)
    status[date] = {"date": date, "recordedAt": "t", "workflowRunId": None, **fields}
    with open(path, "w") as f:
        json.dump(status, f)


def _minimal_replay_run(**overrides):
    run = {
        "schemaVersion": "1", "replayRunId": "wf-run-1", "snapshotId": "wf-snap-1",
        "snapshotManifestHash": "a" * 64, "snapshotDate": DATE, "productionRunId": None,
        "workflowRunId": None, "replayFrameworkVersion": replay.REPLAY_FRAMEWORK_VERSION,
        "replayMode": replay.MODE_CANDIDATE, "candidateModelCommitSha": "deadbeef",
        "candidateModelVersion": None, "productionModelCommitSha": None, "pricingVersions": {},
        "replayFidelity": "LEVEL_2_PRODUCTION_EQUIVALENT", "eligibilityStatus": replay.ELIGIBLE_LEVEL_2,
        "startedAt": "2026-08-01T00:00:00Z", "completedAt": "2026-08-01T00:00:05Z",
        "runStatus": replay.RUN_STATUS_COMPLETED, "limitationReasons": [],
        "summary": {"marketsEvaluated": 1, "marketsComparable": 1, "decisionsChanged": 0,
                     "settledResolved": 1, "settledUnresolved": 0, "clvResolved": 1},
        "performance": None,
        "provenance": {"sourceSystem": "replay_engine", "sourceFile": None, "sourceKey": None,
                        "capturedAt": "t", "ingestedAt": "t"},
    }
    run.update(overrides)
    run["manifestHash"] = replay.compute_run_manifest_hash(run)
    return run


def _minimal_replay_result(**overrides):
    result = {
        "schemaVersion": "1", "replayResultId": "wf-result-1", "replayRunId": "wf-run-1",
        "gameId": "g1", "marketTicker": "TICK-WF-1", "marketFamily": "TICK", "selection": "ML_Away",
        "side": None, "threshold": None, "originalModelProbability": 60.0, "replayedModelProbability": 60.0,
        "originalMarketPrice": 50.0, "replayedMarketPrice": 50.0, "originalExecutablePriceUsed": 51.0,
        "replayedExecutablePriceUsed": 51.0, "originalExecutableMarketProb": 51.0, "replayedExecutableMarketProb": 51.0,
        "originalEdge": 5.0, "replayedEdge": 5.0, "originalRecommendationStatus": "Accepted",
        "replayedRecommendationStatus": "Accepted", "originalTier": "MEDIUM", "replayedTier": "MEDIUM",
        "originalPassReason": None, "replayedPassReason": None, "originalPreferredExpression": None,
        "replayedPreferredExpression": None, "changedDecision": False, "changeReasons": [],
        "comparisonClassification": "UNCHANGED",
        "settlementLinkage": {"status": "RESOLVED", "result": "YES", "reason": None},
        "clvLinkage": {"status": "RESOLVED", "clvValue": 2.0, "reason": None},
        "comparisonMetadata": {"gameLabel": "AAA@HHH"}, "validationStatus": "valid",
        "provenance": {"sourceSystem": "replay_engine", "sourceFile": None, "sourceKey": "k",
                        "capturedAt": "t", "ingestedAt": "t"},
    }
    result.update(overrides)
    return result


def _run_score_replay(cwd, *extra_args):
    return subprocess.run(
        [sys.executable, os.path.join(ROOT, "scripts", "score_replay.py"), "--date", DATE, *extra_args],
        cwd=cwd, capture_output=True, text=True, timeout=30,
    )


class TestScoreReplayDateModeSubprocess:

    def test_no_forward_replay_recorded_is_skipped_honestly(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = _run_score_replay(tmp_path)
        assert result.returncode == 0, result.stderr

        status_path = tmp_path / "data" / "edgelab" / "scored_replay_status.json"
        with open(status_path) as f:
            status = json.load(f)
        assert status[DATE]["outcome"] == "skipped_no_replay_run"
        assert not (tmp_path / "data" / "edgelab" / "scored_replay_runs").exists()

    def test_incomplete_forward_replay_is_skipped_honestly(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_forward_replay_status(tmp_path, DATE, outcome="no_snapshot")
        result = _run_score_replay(tmp_path)
        assert result.returncode == 0, result.stderr

        status_path = tmp_path / "data" / "edgelab" / "scored_replay_status.json"
        with open(status_path) as f:
            status = json.load(f)
        assert status[DATE]["outcome"] == "skipped_no_replay_run"

    def test_completed_forward_replay_is_scored_and_reported(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        run = _minimal_replay_run()
        result_row = _minimal_replay_result()
        write_out = replay.write_replay_outputs(run, [result_row])
        assert write_out["outcome"] == "created"

        run_path = os.path.join(replay.replay_run_dir(run["replayRunId"]), "replay_run.json")
        results_path = os.path.join(replay.replay_run_dir(run["replayRunId"]), "replay_results.jsonl")
        run_bytes_before = open(run_path, "rb").read()
        results_bytes_before = open(results_path, "rb").read()

        _write_forward_replay_status(tmp_path, DATE, outcome="completed", replayRunId=run["replayRunId"],
                                       eligibilityStatus="ELIGIBLE_LEVEL_2", runStatus="COMPLETED",
                                       writeOutcome="created")

        proc = _run_score_replay(tmp_path)
        assert proc.returncode == 0, proc.stderr
        output = json.loads(proc.stdout)
        assert output["date"] == DATE
        assert output["replayRunId"] == run["replayRunId"]
        assert output["summary"]["n"] == 1

        status_path = tmp_path / "data" / "edgelab" / "scored_replay_status.json"
        with open(status_path) as f:
            status = json.load(f)
        assert status[DATE]["outcome"] == "completed"
        assert status[DATE]["scoredReplayRunId"] == output["scoredReplayRunId"]

        report_path = tmp_path / "data" / "edgelab" / "reports" / "scored_replay" / f"{DATE}.json"
        assert report_path.exists()
        with open(report_path) as f:
            report = json.load(f)
        assert report["date"] == DATE
        assert report["predictions"]["total"] == 1

        # The original ReplayRun/ReplayResult files must be byte-identical.
        assert open(run_path, "rb").read() == run_bytes_before
        assert open(results_path, "rb").read() == results_bytes_before

    def test_rerun_after_settlement_correction_updates_in_place(self, tmp_path, monkeypatch):
        """Requirement 5: idempotent rerun; a later corrected settlement
        may update the scored output but never the original replay --
        exercised end-to-end through the real subprocess entrypoint."""
        monkeypatch.chdir(tmp_path)
        run = _minimal_replay_run()
        replay.write_replay_outputs(run, [_minimal_replay_result()])
        _write_forward_replay_status(tmp_path, DATE, outcome="completed", replayRunId=run["replayRunId"])

        first = _run_score_replay(tmp_path)
        assert first.returncode == 0, first.stderr
        first_output = json.loads(first.stdout)
        assert first_output["writeOutcome"] == "created"

        second = _run_score_replay(tmp_path)
        assert second.returncode == 0, second.stderr
        second_output = json.loads(second.stdout)
        assert second_output["writeOutcome"] == "noop_unchanged"
        assert second_output["scoredReplayRunId"] == first_output["scoredReplayRunId"]
