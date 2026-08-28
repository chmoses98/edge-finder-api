#!/usr/bin/env python3
"""
tests/edgelab/test_prospective_snapshot.py
================================================
Coverage for lib/edgelab/prospective_snapshot.py -- the safe, research-
only intraday re-evaluation orchestrator. Every test here injects fake
evaluate_game_fn/compute_projection_context_fn/lineup_fetch_fn -- no
real network access, no real data/slate.json, no dependency on
scripts.build_market_ledger's actual model math (that reuse is proven
by import alone; this file tests the ORCHESTRATION logic around it).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab import prospective_snapshot as ps
from lib.edgelab import storage


def _game(game_id="822780", start_time="2026-08-10T23:00:00Z", away_abbr="BOS", home_abbr="NYY",
          lineup_confirmed=False, **overrides):
    g = {
        "gameId": game_id,
        "startTime": start_time,
        "away": {"abbr": away_abbr},
        "home": {"abbr": home_abbr},
        "awayTeamStats": {"lineupConfirmedOfficial": lineup_confirmed},
        "homeTeamStats": {"lineupConfirmedOfficial": lineup_confirmed},
    }
    g.update(overrides)
    return g


def _fake_evaluate_game(row_market_prob=55.0, ticker_suffix=""):
    def _fn(g, ctx):
        return [{
            "market": "ML_Away", "ticker": f"T-{g['gameId']}{ticker_suffix}",
            "modelProb": row_market_prob, "status": "Accepted",
            "kalshiVF": 50.0, "edge": row_market_prob - 50.0,
        }]
    return _fn


def _fake_projection_context(g):
    return {"fake": True}


# ── Game eligibility ──────────────────────────────────────────────────────

def test_started_game_excluded_by_clock_time():
    game = _game(start_time="2026-08-10T10:00:00Z")
    eligible, reason, _ = ps.classify_game_eligibility(game, now="2026-08-10T10:05:00Z")
    assert eligible is False
    assert reason == ps.EXCLUDED_STARTED


def test_pregame_game_eligible():
    game = _game(start_time="2026-08-10T23:00:00Z")
    eligible, reason, minutes = ps.classify_game_eligibility(game, now="2026-08-10T21:30:00Z")
    assert eligible is True
    assert abs(minutes - 90.0) < 1e-6


def test_live_status_started_excludes_even_before_clock_time():
    game = _game(start_time="2026-08-10T23:00:00Z")
    eligible, reason, _ = ps.classify_game_eligibility(game, now="2026-08-10T22:00:00Z", live_status="In Progress")
    assert eligible is False
    assert reason == ps.EXCLUDED_STARTED


def test_postponed_game_excluded():
    game = _game(start_time="2026-08-10T23:00:00Z")
    eligible, reason, _ = ps.classify_game_eligibility(game, now="2026-08-10T20:00:00Z", live_status="Postponed")
    assert eligible is False
    assert reason == ps.EXCLUDED_POSTPONED


def test_cancelled_game_excluded():
    game = _game(start_time="2026-08-10T23:00:00Z")
    eligible, reason, _ = ps.classify_game_eligibility(game, now="2026-08-10T20:00:00Z", live_status="Suspended")
    assert eligible is False
    assert reason == ps.EXCLUDED_CANCELLED_OR_SUSPENDED


def test_missing_live_status_does_not_hard_exclude():
    """A single flaky schedule fetch must never blank out a whole day's coverage."""
    game = _game(start_time="2026-08-10T23:00:00Z")
    eligible, reason, _ = ps.classify_game_eligibility(game, now="2026-08-10T21:30:00Z", live_status=None)
    assert eligible is True
    assert reason == ps.EXCLUDED_STATUS_AMBIGUOUS_BUT_PROCEEDING


def test_missing_scheduled_start_excluded():
    game = _game(start_time=None)
    eligible, reason, _ = ps.classify_game_eligibility(game, now="2026-08-10T21:30:00Z")
    assert eligible is False
    assert reason == ps.EXCLUDED_MISSING_SCHEDULED_START


# ── Checkpoint scheduling ──────────────────────────────────────────────────

def test_t_minus_90_due_when_no_prior_checkpoint():
    game = _game(start_time="2026-08-10T23:00:00Z")
    checkpoint, minutes = ps.determine_due_checkpoint(game, now="2026-08-10T21:30:00Z", already_captured=set())
    assert checkpoint == "T_MINUS_90"


def test_already_captured_checkpoint_not_due_again():
    game = _game(start_time="2026-08-10T23:00:00Z")
    checkpoint, _ = ps.determine_due_checkpoint(game, now="2026-08-10T21:30:00Z", already_captured={"T_MINUS_90"})
    assert checkpoint is None


def test_lineup_confirmation_due_when_just_confirmed():
    game = _game(start_time="2026-08-10T23:00:00Z", lineup_confirmed=True)
    checkpoint, _ = ps.determine_due_checkpoint(game, now="2026-08-10T22:10:00Z", already_captured=set())
    assert checkpoint == "LINEUP_CONFIRMATION"


def test_lineup_confirmation_not_due_twice():
    game = _game(start_time="2026-08-10T23:00:00Z", lineup_confirmed=True)
    checkpoint, _ = ps.determine_due_checkpoint(game, now="2026-08-10T22:10:00Z", already_captured={"LINEUP_CONFIRMATION"})
    assert checkpoint != "LINEUP_CONFIRMATION"


def test_closing_due_within_window():
    game = _game(start_time="2026-08-10T23:00:00Z")
    checkpoint, minutes = ps.determine_due_checkpoint(game, now="2026-08-10T22:50:00Z", already_captured=set())
    assert checkpoint == ps.MODEL_CLOSING_WINDOW
    assert 0 < minutes <= ps.CLOSING_WINDOW_MINUTES


def test_no_checkpoint_due_between_targets():
    game = _game(start_time="2026-08-10T23:00:00Z")
    # ~75 minutes to start -- not within tolerance of any T_MINUS_X target, not in closing window.
    checkpoint, _ = ps.determine_due_checkpoint(game, now="2026-08-10T21:45:00Z", already_captured=set())
    assert checkpoint is None


# ── Lineup refresh safety ───────────────────────────────────────────────────

def test_lineup_refresh_never_mutates_original_game():
    game = _game(lineup_confirmed=False)
    def fake_fetch(game_pk, away, home, bw, tw):
        return {"away": {"lineupConfirmedOfficial": True}, "home": {"lineupConfirmedOfficial": True}}
    new_game, warning = ps.refresh_lineup_fields(game, lineup_fetch_fn=fake_fetch, batter_woba_map={}, team_woba_map={})
    assert warning is None
    assert new_game["awayTeamStats"]["lineupConfirmedOfficial"] is True
    assert game["awayTeamStats"]["lineupConfirmedOfficial"] is False  # original untouched


def test_lineup_refresh_failure_keeps_original_state_and_warns():
    game = _game(lineup_confirmed=False)
    def failing_fetch(game_pk, away, home, bw, tw):
        raise RuntimeError("network down")
    new_game, warning = ps.refresh_lineup_fields(game, lineup_fetch_fn=failing_fetch, batter_woba_map={}, team_woba_map={})
    assert warning is not None
    assert new_game["awayTeamStats"]["lineupConfirmedOfficial"] is False  # never fabricated


def test_lineup_refresh_none_result_keeps_original_state():
    game = _game(lineup_confirmed=False)
    def empty_fetch(game_pk, away, home, bw, tw):
        return None
    new_game, warning = ps.refresh_lineup_fields(game, lineup_fetch_fn=empty_fetch, batter_woba_map={}, team_woba_map={})
    assert warning is not None
    assert new_game["awayTeamStats"]["lineupConfirmedOfficial"] is False


# ── Full cycle orchestration ────────────────────────────────────────────────

def test_evaluated_snapshots_carries_one_entry_per_evaluated_game():
    """
    MLB-RSCH-0011 addition: the third return value must carry exactly the
    games/checkpoints this cycle actually EVALUATED (never a skipped
    game), with the "game" field pointing at the same object
    evaluate_game_fn/compute_projection_context_fn were actually called
    against -- proving a research caller can safely recompute the same
    projection context, never a different or reconstructed one.
    """
    evaluated_game, skipped_game = _game(game_id="g1", start_time="2026-08-10T23:00:00Z"), _game(game_id="g2", start_time="2026-08-10T20:00:00Z")
    records, run_log, evaluated_snapshots = ps.run_prospective_snapshot_cycle(
        "2026-08-10", [evaluated_game, skipped_game], [], [], now="2026-08-10T21:30:00Z",
        evaluate_game_fn=_fake_evaluate_game(), compute_projection_context_fn=_fake_projection_context,
    )
    assert len(evaluated_snapshots) == 1
    entry = evaluated_snapshots[0]
    assert entry["gameId"] == "g1"
    assert entry["checkpoint"] == "T_MINUS_90"
    assert entry["game"] is evaluated_game


def test_evaluated_snapshots_uses_lineup_refreshed_game_for_lineup_confirmation_checkpoint():
    game = _game(game_id="g1", start_time="2026-08-10T15:05:00Z")  # ~5h out -- no time-distance checkpoint due

    def live_poll_confirms(game_pk, away, home, bw, tw):
        return {"away": {"lineupConfirmedOfficial": True}, "home": {"lineupConfirmedOfficial": True}}

    _, _, evaluated_snapshots = ps.run_prospective_snapshot_cycle(
        "2026-08-10", [game], [], [], now="2026-08-10T10:00:00Z",
        evaluate_game_fn=_fake_evaluate_game(), compute_projection_context_fn=_fake_projection_context,
        lineup_fetch_fn=live_poll_confirms,
    )
    assert len(evaluated_snapshots) == 1
    entry = evaluated_snapshots[0]
    assert entry["checkpoint"] == "LINEUP_CONFIRMATION"
    assert entry["game"] is not game  # the lineup-refreshed COPY, never the original object
    assert entry["game"]["awayTeamStats"]["lineupConfirmedOfficial"] is True


def test_cycle_evaluates_due_game_and_produces_records():
    game = _game(game_id="g1", start_time="2026-08-10T23:00:00Z")
    records, run_log, _ = ps.run_prospective_snapshot_cycle(
        "2026-08-10", [game], [], [], now="2026-08-10T21:30:00Z",
        evaluate_game_fn=_fake_evaluate_game(), compute_projection_context_fn=_fake_projection_context,
    )
    assert len(records) == 1
    assert records[0]["checkpoint"] == "T_MINUS_90"
    assert records[0]["artifactSource"] == ps.ARTIFACT_SOURCE
    assert records[0]["pipelineRunId"] == "2026-08-10T21:30:00Z"  # actual evaluation instant, not an assumed cron time
    assert run_log[0]["action"] == "EVALUATED"
    assert run_log[0]["checkpoint"] == "T_MINUS_90"


def test_cycle_skips_started_game_with_zero_records():
    game = _game(game_id="g1", start_time="2026-08-10T20:00:00Z")
    records, run_log, _ = ps.run_prospective_snapshot_cycle(
        "2026-08-10", [game], [], [], now="2026-08-10T21:30:00Z",
        evaluate_game_fn=_fake_evaluate_game(), compute_projection_context_fn=_fake_projection_context,
    )
    assert records == []
    assert run_log[0]["action"] == "SKIPPED"
    assert run_log[0]["reason"] == ps.EXCLUDED_STARTED


def test_cycle_never_evaluates_already_captured_checkpoint_twice():
    game = _game(game_id="g1", start_time="2026-08-10T23:00:00Z")
    first_records, _, _ = ps.run_prospective_snapshot_cycle(
        "2026-08-10", [game], [], [], now="2026-08-10T21:30:00Z",
        evaluate_game_fn=_fake_evaluate_game(), compute_projection_context_fn=_fake_projection_context,
    )
    # Second cycle, same clock moment, existing_evaluations now includes the first cycle's output.
    second_records, run_log, _ = ps.run_prospective_snapshot_cycle(
        "2026-08-10", [game], first_records, [], now="2026-08-10T21:30:00Z",
        evaluate_game_fn=_fake_evaluate_game(), compute_projection_context_fn=_fake_projection_context,
    )
    assert second_records == []
    assert run_log[0]["reason"] == ps.SKIPPED_NO_CHECKPOINT_DUE


def test_multiple_checkpoints_across_cycles_all_survive():
    """T-90 then T-30: BOTH ModelEvaluation records must persist -- no 'latest row wins'."""
    game = _game(game_id="g1", start_time="2026-08-10T23:00:00Z")
    t90_records, _, _ = ps.run_prospective_snapshot_cycle(
        "2026-08-10", [game], [], [], now="2026-08-10T21:30:00Z",
        evaluate_game_fn=_fake_evaluate_game(row_market_prob=54.0), compute_projection_context_fn=_fake_projection_context,
    )
    t30_records, _, _ = ps.run_prospective_snapshot_cycle(
        "2026-08-10", [game], t90_records, [], now="2026-08-10T22:30:00Z",
        evaluate_game_fn=_fake_evaluate_game(row_market_prob=59.0), compute_projection_context_fn=_fake_projection_context,
    )
    assert len(t90_records) == 1 and len(t30_records) == 1
    assert t90_records[0]["modelEvaluationId"] != t30_records[0]["modelEvaluationId"]
    assert t90_records[0]["modelFairProbability"] == 54.0
    assert t30_records[0]["modelFairProbability"] == 59.0

    all_records = t90_records + t30_records
    assert {r["checkpoint"] for r in all_records} == {"T_MINUS_90", "T_MINUS_30"}


def test_multiple_checkpoints_persist_through_storage_append(tmp_path):
    """End-to-end: append_records (the real EdgeLab writer) must preserve both snapshots on disk."""
    game = _game(game_id="g1", start_time="2026-08-10T23:00:00Z")
    t90_records, _, _ = ps.run_prospective_snapshot_cycle(
        "2026-08-10", [game], [], [], now="2026-08-10T21:30:00Z",
        evaluate_game_fn=_fake_evaluate_game(row_market_prob=54.0), compute_projection_context_fn=_fake_projection_context,
    )
    path = storage.partition_path("model_evaluations", "2026-08-10")
    written1, skipped1 = storage.append_records(str(tmp_path / path), t90_records, "modelEvaluationId")
    assert written1 == 1 and skipped1 == 0

    t30_records, _, _ = ps.run_prospective_snapshot_cycle(
        "2026-08-10", [game], t90_records, [], now="2026-08-10T22:30:00Z",
        evaluate_game_fn=_fake_evaluate_game(row_market_prob=59.0), compute_projection_context_fn=_fake_projection_context,
    )
    written2, skipped2 = storage.append_records(str(tmp_path / path), t30_records, "modelEvaluationId")
    assert written2 == 1 and skipped2 == 0

    on_disk = list(storage.read_records(str(tmp_path / path)))
    assert len(on_disk) == 2  # both survive -- no overwrite
    probs = sorted(r["modelFairProbability"] for r in on_disk)
    assert probs == [54.0, 59.0]


def test_exact_duplicate_run_is_idempotent(tmp_path):
    """Re-running the identical cycle (same `now`, same inputs) must not create a duplicate row on disk."""
    game = _game(game_id="g1", start_time="2026-08-10T23:00:00Z")
    records, _, _ = ps.run_prospective_snapshot_cycle(
        "2026-08-10", [game], [], [], now="2026-08-10T21:30:00Z",
        evaluate_game_fn=_fake_evaluate_game(), compute_projection_context_fn=_fake_projection_context,
    )
    path = str(tmp_path / storage.partition_path("model_evaluations", "2026-08-10"))
    storage.append_records(path, records, "modelEvaluationId")

    # Re-run the exact same cycle again (e.g. a workflow retry) -- identical source_run_key -> identical modelEvaluationId.
    duplicate_records, _, _ = ps.run_prospective_snapshot_cycle(
        "2026-08-10", [game], [], [], now="2026-08-10T21:30:00Z",
        evaluate_game_fn=_fake_evaluate_game(), compute_projection_context_fn=_fake_projection_context,
    )
    written, skipped = storage.append_records(path, duplicate_records, "modelEvaluationId")
    assert written == 0 and skipped == 1
    assert len(list(storage.read_records(path))) == 1


def test_one_game_failure_does_not_abort_other_games():
    good_game = _game(game_id="g_good", start_time="2026-08-10T23:00:00Z")
    bad_game = _game(game_id="g_bad", start_time="2026-08-10T23:00:00Z")

    def flaky_evaluate(g, ctx):
        if g["gameId"] == "g_bad":
            raise ValueError("malformed input")
        return [{"market": "ML_Away", "ticker": f"T-{g['gameId']}", "modelProb": 55.0, "status": "Accepted"}]

    records, run_log, _ = ps.run_prospective_snapshot_cycle(
        "2026-08-10", [bad_game, good_game], [], [], now="2026-08-10T21:30:00Z",
        evaluate_game_fn=flaky_evaluate, compute_projection_context_fn=_fake_projection_context,
    )
    assert len(records) == 1
    assert records[0]["gameId"] == "g_good"
    bad_log = next(r for r in run_log if r["gameId"] == "g_bad")
    assert bad_log["action"] == "SKIPPED"
    assert "raised" in bad_log["reason"]


def test_lineup_checkpoint_only_evaluates_with_refreshed_lineup_state():
    """The T_MINUS_60 evaluation (no lineup refresh) must see the ORIGINAL unconfirmed lineup; only LINEUP_CONFIRMATION sees the refreshed one."""
    game = _game(game_id="g1", start_time="2026-08-10T23:00:00Z", lineup_confirmed=False)
    seen_lineup_states = []

    def recording_evaluate(g, ctx):
        seen_lineup_states.append(g["awayTeamStats"]["lineupConfirmedOfficial"])
        return [{"market": "ML_Away", "ticker": f"T-{g['gameId']}", "modelProb": 55.0, "status": "Accepted"}]

    def fake_fetch(game_pk, away, home, bw, tw):
        return {"away": {"lineupConfirmedOfficial": True}, "home": {"lineupConfirmedOfficial": True}}

    # Cycle 1: T_MINUS_60, lineup still unconfirmed on the base game object.
    t60_records, _, _ = ps.run_prospective_snapshot_cycle(
        "2026-08-10", [game], [], [], now="2026-08-10T22:00:00Z",
        evaluate_game_fn=recording_evaluate, compute_projection_context_fn=_fake_projection_context,
    )
    assert seen_lineup_states == [False]

    # Cycle 2: lineup now confirmed on the base object -- LINEUP_CONFIRMATION checkpoint fires, refresh_lineup_fields applied.
    game["awayTeamStats"]["lineupConfirmedOfficial"] = True
    game["homeTeamStats"]["lineupConfirmedOfficial"] = True
    lc_records, _, _ = ps.run_prospective_snapshot_cycle(
        "2026-08-10", [game], t60_records, [], now="2026-08-10T22:15:00Z",
        evaluate_game_fn=recording_evaluate, compute_projection_context_fn=_fake_projection_context,
        lineup_fetch_fn=fake_fetch,
    )
    assert seen_lineup_states == [False, True]
    assert lc_records[0]["checkpoint"] == "LINEUP_CONFIRMATION"


def test_provenance_fields_persist():
    game = _game(game_id="g1", start_time="2026-08-10T23:00:00Z")
    records, _, _ = ps.run_prospective_snapshot_cycle(
        "2026-08-10", [game], [], [], now="2026-08-10T21:30:00Z",
        evaluate_game_fn=_fake_evaluate_game(), compute_projection_context_fn=_fake_projection_context,
    )
    record = records[0]
    assert record["modelCommitSha"] is not None or record["modelCommitSha"] is None  # always present as a key, real value depends on git env
    assert "modelCommitSha" in record
    assert "modelConfigVersion" in record
    assert record["source"] == ps.ARTIFACT_SOURCE
    assert record["recommendationId"] is None  # never a fabricated link to a Recommendation that doesn't exist


def test_never_assigns_recommendation_id():
    game = _game(game_id="g1", start_time="2026-08-10T23:00:00Z")
    records, _, _ = ps.run_prospective_snapshot_cycle(
        "2026-08-10", [game], [], [], now="2026-08-10T21:30:00Z",
        evaluate_game_fn=_fake_evaluate_game(), compute_projection_context_fn=_fake_projection_context,
    )
    assert all(r["recommendationId"] is None for r in records)


def test_module_never_imports_bet_bankroll_or_recommendation_writers():
    """Static-analysis-style safety check: this module must never be able to place bets or mutate bankroll/recommendations."""
    import ast
    import lib.edgelab.prospective_snapshot as mod
    with open(mod.__file__) as f:
        tree = ast.parse(f.read(), filename=mod.__file__)

    imported_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_names.add(node.module)
            imported_names.update(f"{node.module}.{alias.name}" for alias in node.names)

    forbidden_substrings = ("risk_gate", "write_pending_bets", "write_placed_bet", "bankroll", "build_recommendations_from_pipeline")
    for name in imported_names:
        for forbidden in forbidden_substrings:
            assert forbidden not in name, f"prospective_snapshot.py must never import {name!r}"


# ── Remaining spec-section-19 coverage ─────────────────────────────────────

def test_missing_model_support_stays_explicit():
    """A market family the model has no method for must be recorded as NO_MODEL_SUPPORT, never a fabricated probability."""
    game = _game(game_id="g1", start_time="2026-08-10T23:00:00Z")

    def evaluate_no_support(g, ctx):
        return [{"market": "SomePlayerProp", "ticker": f"T-{g['gameId']}", "status": "N/A"}]  # no modelProb at all

    records, _, _ = ps.run_prospective_snapshot_cycle(
        "2026-08-10", [game], [], [], now="2026-08-10T21:30:00Z",
        evaluate_game_fn=evaluate_no_support, compute_projection_context_fn=_fake_projection_context,
    )
    assert len(records) == 1
    assert records[0]["evaluationStatus"] == "NO_MODEL_SUPPORT"
    assert records[0]["modelFairProbability"] is None  # never fabricated


def test_model_snapshot_links_to_contemporaneous_market_observation():
    """eventTicker/seriesTicker must be enriched from the already-captured MarketObservation for this exact ticker, reused, not re-parsed."""
    game = _game(game_id="g1", start_time="2026-08-10T23:00:00Z")
    observations = [{
        "marketTicker": "T-g1", "eventTicker": "EVT-g1", "seriesTicker": "KXMLBGAME",
        "gameId": "g1", "runId": "obs-run-1",
    }]
    records, _, _ = ps.run_prospective_snapshot_cycle(
        "2026-08-10", [game], [], observations, now="2026-08-10T21:30:00Z",
        evaluate_game_fn=_fake_evaluate_game(), compute_projection_context_fn=_fake_projection_context,
    )
    assert records[0]["marketTicker"] == "T-g1"
    assert records[0]["eventTicker"] == "EVT-g1"
    assert records[0]["seriesTicker"] == "KXMLBGAME"


def test_workflow_files_are_structurally_independent():
    """capture-snapshots-scheduled.yml (raw Kalshi price capture) must share no job/step/concurrency-group with model-snapshot-scheduler.yml -- a failure in one must never be able to block the other."""
    import yaml
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    workflows_dir = os.path.join(root, ".github", "workflows")

    with open(os.path.join(workflows_dir, "capture-snapshots-scheduled.yml")) as f:
        capture_doc = yaml.safe_load(f)
    with open(os.path.join(workflows_dir, "model-snapshot-scheduler.yml")) as f:
        snapshot_doc = yaml.safe_load(f)

    capture_jobs = set(capture_doc.get("jobs", {}).keys())
    snapshot_jobs = set(snapshot_doc.get("jobs", {}).keys())
    assert capture_jobs.isdisjoint(snapshot_jobs)

    capture_group = (capture_doc.get("concurrency") or {}).get("group")
    snapshot_group = (snapshot_doc.get("concurrency") or {}).get("group")
    assert snapshot_group is not None
    assert snapshot_group != capture_group


def test_model_snapshot_workflow_not_in_shared_ledger_writer_group():
    """This workflow never writes data/slate.json/bets.json/BET_LOG.md, so per this repo's own existing concurrency convention it must NOT join the shared edge-finder-ledger-writer group."""
    import yaml
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with open(os.path.join(root, ".github", "workflows", "model-snapshot-scheduler.yml")) as f:
        doc = yaml.safe_load(f)
    assert doc["concurrency"]["group"] != "edge-finder-ledger-writer"


# ── Workflow persistence / failure visibility (reliability pass, spec section 2) ──

def _load_snapshot_workflow():
    import yaml
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with open(os.path.join(root, ".github", "workflows", "model-snapshot-scheduler.yml")) as f:
        return yaml.safe_load(f)


def test_workflow_job_has_no_continue_on_error():
    """A whole-run infrastructure (persistence) failure must surface as a red run, not be silently swallowed at the job level."""
    doc = _load_snapshot_workflow()
    job = doc["jobs"]["prospective-snapshot"]
    assert "continue-on-error" not in job


def test_workflow_commit_step_never_swallows_failure_with_bare_or_echo():
    """The commit step must not end with `|| echo ...` (or any other exit-code-swallowing pattern) -- a failed push must propagate as a failed step."""
    doc = _load_snapshot_workflow()
    steps = doc["jobs"]["prospective-snapshot"]["steps"]
    commit_step = next(s for s in steps if s.get("id") == "commit")
    run_script = commit_step.get("run", "")
    assert "||" not in run_script


def test_workflow_commit_step_uses_canonical_git_commit_script():
    doc = _load_snapshot_workflow()
    steps = doc["jobs"]["prospective-snapshot"]["steps"]
    commit_step = next(s for s in steps if s.get("id") == "commit")
    assert "scripts/ci/git_data_commit.py" in commit_step["run"]


def test_workflow_backs_up_generated_files_before_attempting_commit():
    """Backup must happen BEFORE the commit step, since git_data_commit.py resets the working tree to origin's tip on a failed push -- uploading the post-failure tree would upload reverted content, not the actual generated snapshot."""
    doc = _load_snapshot_workflow()
    steps = doc["jobs"]["prospective-snapshot"]["steps"]
    step_names = [s.get("name", "") for s in steps]
    backup_idx = next(i for i, n in enumerate(step_names) if "back up" in n.lower() or "backup" in n.lower())
    commit_idx = next(i for i, s in enumerate(steps) if s.get("id") == "commit")
    assert backup_idx < commit_idx


# ── Daily operating-window coverage fix (found alongside the identical bug
# in hitter-snapshot-scheduler.yml: the cron was originally 16:00-23:45 UTC
# + 00:00-05:45 UTC, so early MLB day games -- e.g. a real 12:10 PM ET
# game, T-90 = 14:40 UTC -- had their T-90/T-60/T-30 checkpoints silently
# never captured because the scheduler had not started running yet that
# day. See docs/HITTER_CHECKPOINT_COVERAGE_FIX.md Sec.9 for the full
# derivation and exhaustive full-day simulation evidence.) ──

def _read_snapshot_workflow_src():
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with open(os.path.join(root, ".github", "workflows", "model-snapshot-scheduler.yml")) as f:
        return f.read()


def test_operating_window_starts_at_13_utc_not_16():
    """Regression guard: the window's start must never silently regress back to 16:00 UTC, which left every MLB game starting before ~2:20 PM ET with an uncaptured T-90 (and, depending on exact start time, T-60/T-30 too)."""
    doc = _load_snapshot_workflow()
    schedules = doc[True]["schedule"]
    crons = [s["cron"] for s in schedules]
    daytime_cron = next(c for c in crons if c.startswith("*/15 13,"))
    assert daytime_cron == "*/15 13,14,15,16,17,18,19,20,21,22,23 * * *"
    assert not any(c.startswith("*/15 16,") for c in crons), \
        "operating window must not regress to the pre-fix 16:00 UTC start"


def test_overnight_window_unchanged():
    """The 00:00-05:45 UTC overnight block was already wide enough for the latest real West Coast starts and was intentionally left untouched by this fix."""
    doc = _load_snapshot_workflow()
    schedules = doc[True]["schedule"]
    crons = [s["cron"] for s in schedules]
    assert "*/15 0,1,2,3,4,5 * * *" in crons


def test_documentation_no_longer_overclaims_coverage_from_minute_only_simulation():
    """The header comment must document the daily-operating-window bug/fix distinctly from cadence/alignment coverage, not just restate the (separate) minute-of-hour guarantee."""
    src = _read_snapshot_workflow_src()
    assert "16:00" in src and "13:00" in src
    assert "operating-window" in src.lower() or "operating window" in src.lower()


def test_workflow_uploads_artifact_on_persistence_failure():
    doc = _load_snapshot_workflow()
    steps = doc["jobs"]["prospective-snapshot"]["steps"]
    upload_step = next(s for s in steps if s.get("uses", "").startswith("actions/upload-artifact"))
    assert "steps.commit.outcome == 'failure'" in upload_step["if"]


def test_workflow_fails_visibly_when_persistence_fails():
    doc = _load_snapshot_workflow()
    steps = doc["jobs"]["prospective-snapshot"]["steps"]
    fail_step = next(s for s in steps if "fail" in s.get("name", "").lower() and "visib" in s.get("name", "").lower())
    assert "steps.commit.outcome == 'failure'" in fail_step["if"]
    assert "exit 1" in fail_step["run"]


def test_snapshot_coverage_report_reflects_real_run_record_shape():
    """Integration: a run_record shaped exactly like scripts/edgelab/run_prospective_snapshots.py actually produces must be correctly aggregated by snapshot_coverage_report."""
    from lib.edgelab.research_reports import snapshot_coverage_report

    game = _game(game_id="g1", start_time="2026-08-10T23:00:00Z")
    records, run_log, _ = ps.run_prospective_snapshot_cycle(
        "2026-08-10", [game], [], [], now="2026-08-10T21:30:00Z",
        evaluate_game_fn=_fake_evaluate_game(), compute_projection_context_fn=_fake_projection_context,
    )
    evaluated = [r for r in run_log if r["action"] == "EVALUATED"]
    skipped = [r for r in run_log if r["action"] == "SKIPPED"]
    skip_reason_counts = {}
    for entry in skipped:
        skip_reason_counts[entry["reason"]] = skip_reason_counts.get(entry["reason"], 0) + 1

    run_record = {
        "runType": "PROSPECTIVE_SNAPSHOT", "status": "success", "errors": [],
        "counts": {
            "gamesEvaluated": len(evaluated), "gamesSkipped": len(skipped),
            "gamesSkippedByReason": skip_reason_counts, "modelEvaluationsSkippedDuplicate": 0,
        },
    }
    report = snapshot_coverage_report([], records, research_runs=[run_record])
    assert report["duplicateOrIdempotencyCount"] == 0
    assert report["modelEvaluationsCapturedProspective"] == len(records)


# ── LINEUP_CONFIRMATION discovery bug fix (reliability pass, spec section 1) ──

def test_stale_slate_discovers_newly_confirmed_lineup_via_live_poll():
    """
    THE BUG: data/slate.json still says unconfirmed (as it would for
    hours after the morning fetch), but the LIVE poll now says
    confirmed. Before the fix, refresh_lineup_fields() was only ever
    called AFTER determine_due_checkpoint() had already decided
    LINEUP_CONFIRMATION was due -- which it could never decide from
    stale-unconfirmed data alone. This must now fire.
    """
    game = _game(game_id="g1", start_time="2026-08-10T23:00:00Z", lineup_confirmed=False)  # slate.json: stale, unconfirmed

    def live_poll_says_confirmed(game_pk, away, home, bw, tw):
        return {"away": {"lineupConfirmedOfficial": True}, "home": {"lineupConfirmedOfficial": True}}

    records, run_log, _ = ps.run_prospective_snapshot_cycle(
        "2026-08-10", [game], [], [], now="2026-08-10T21:30:00Z",
        evaluate_game_fn=_fake_evaluate_game(), compute_projection_context_fn=_fake_projection_context,
        lineup_fetch_fn=live_poll_says_confirmed,
    )
    assert len(records) == 1
    assert records[0]["checkpoint"] == "LINEUP_CONFIRMATION"
    assert run_log[0]["action"] == "EVALUATED"
    assert run_log[0]["lineupPollAttempted"] is True
    assert run_log[0]["lineupNewlyConfirmed"] is True


def test_stale_slate_still_unconfirmed_after_live_poll_no_snapshot():
    """Slate says unconfirmed, live poll ALSO still says unconfirmed -- no LINEUP_CONFIRMATION snapshot, and no other checkpoint due either at this clock time."""
    game = _game(game_id="g1", start_time="2026-08-10T23:00:00Z", lineup_confirmed=False)

    def live_poll_still_unconfirmed(game_pk, away, home, bw, tw):
        return {"away": {"lineupConfirmedOfficial": False}, "home": {"lineupConfirmedOfficial": False}}

    records, run_log, _ = ps.run_prospective_snapshot_cycle(
        "2026-08-10", [game], [], [], now="2026-08-10T21:45:00Z",  # ~75 min to start -- not near any T_MINUS_X target either
        evaluate_game_fn=_fake_evaluate_game(), compute_projection_context_fn=_fake_projection_context,
        lineup_fetch_fn=live_poll_still_unconfirmed,
    )
    assert records == []
    assert run_log[0]["action"] == "SKIPPED"
    assert run_log[0]["lineupPollAttempted"] is True
    assert run_log[0]["lineupNewlyConfirmed"] is False


def test_lineup_api_failure_never_fabricates_confirmation():
    game = _game(game_id="g1", start_time="2026-08-10T23:00:00Z", lineup_confirmed=False)

    def failing_poll(game_pk, away, home, bw, tw):
        raise RuntimeError("MLB Stats API unavailable")

    records, run_log, _ = ps.run_prospective_snapshot_cycle(
        "2026-08-10", [game], [], [], now="2026-08-10T21:45:00Z",
        evaluate_game_fn=_fake_evaluate_game(), compute_projection_context_fn=_fake_projection_context,
        lineup_fetch_fn=failing_poll,
    )
    assert records == []  # no fabricated LINEUP_CONFIRMATION snapshot
    assert run_log[0]["lineupPollAttempted"] is True
    assert run_log[0]["lineupPollFailed"] is True
    assert run_log[0]["lineupNewlyConfirmed"] is False
    assert any("failed" in w for w in run_log[0]["warnings"])


def test_already_captured_lineup_confirmation_does_not_poll_again():
    """Do not repeatedly fetch lineups once LINEUP_CONFIRMATION has already been captured for this game."""
    game = _game(game_id="g1", start_time="2026-08-10T23:00:00Z", lineup_confirmed=True)
    already_captured_eval = {
        "gameId": "g1", "artifactSource": ps.ARTIFACT_SOURCE, "checkpoint": "LINEUP_CONFIRMATION",
    }
    poll_calls = []

    def counting_poll(game_pk, away, home, bw, tw):
        poll_calls.append(1)
        return {"away": {"lineupConfirmedOfficial": True}, "home": {"lineupConfirmedOfficial": True}}

    records, run_log, _ = ps.run_prospective_snapshot_cycle(
        "2026-08-10", [game], [already_captured_eval], [], now="2026-08-10T21:45:00Z",
        evaluate_game_fn=_fake_evaluate_game(), compute_projection_context_fn=_fake_projection_context,
        lineup_fetch_fn=counting_poll,
    )
    assert poll_calls == []  # never called
    assert run_log[0]["lineupPollAttempted"] is False
    assert all(r["checkpoint"] != "LINEUP_CONFIRMATION" for r in records)


def test_lineup_refresh_in_memory_never_mutates_slate_game_object():
    """End-to-end through the full cycle (not just refresh_lineup_fields() in isolation): the caller's game dict must be byte-identical after the cycle runs."""
    import copy
    game = _game(game_id="g1", start_time="2026-08-10T23:00:00Z", lineup_confirmed=False)
    original_snapshot = copy.deepcopy(game)

    def live_poll_says_confirmed(game_pk, away, home, bw, tw):
        return {"away": {"lineupConfirmedOfficial": True}, "home": {"lineupConfirmedOfficial": True}}

    ps.run_prospective_snapshot_cycle(
        "2026-08-10", [game], [], [], now="2026-08-10T21:30:00Z",
        evaluate_game_fn=_fake_evaluate_game(), compute_projection_context_fn=_fake_projection_context,
        lineup_fetch_fn=live_poll_says_confirmed,
    )
    assert game == original_snapshot  # the exact object passed in, never mutated


def test_t_minus_30_snapshot_keeps_its_own_earlier_lineup_state_even_when_lineup_confirms_same_cycle():
    """
    A game at T-30 whose lineup ALSO happens to confirm in this same
    cycle: LINEUP_CONFIRMATION takes priority and is evaluated with the
    refreshed lineup; T-30 is simply not evaluated this cycle (never
    evaluated with a lineup state it didn't causally have). A later
    cycle's T-30-equivalent-timed re-check (if it were to happen) must
    never retroactively use the newer lineup either -- proven here by
    confirming the single EVALUATED record this cycle produces is
    LINEUP_CONFIRMATION, never a T_MINUS_30 record carrying the new
    lineup state.
    """
    game = _game(game_id="g1", start_time="2026-08-10T21:30:00Z", lineup_confirmed=False)  # now == T-30 exactly

    def live_poll_says_confirmed(game_pk, away, home, bw, tw):
        return {"away": {"lineupConfirmedOfficial": True}, "home": {"lineupConfirmedOfficial": True}}

    seen_lineup_states = []

    def recording_evaluate(g, ctx):
        seen_lineup_states.append((g["awayTeamStats"]["lineupConfirmedOfficial"]))
        return [{"market": "ML_Away", "ticker": f"T-{g['gameId']}", "modelProb": 55.0, "status": "Accepted", "kalshiVF": 50.0, "edge": 5.0}]

    records, run_log, _ = ps.run_prospective_snapshot_cycle(
        "2026-08-10", [game], [], [], now="2026-08-10T21:00:00Z",
        evaluate_game_fn=recording_evaluate, compute_projection_context_fn=_fake_projection_context,
        lineup_fetch_fn=live_poll_says_confirmed,
    )
    assert len(records) == 1
    assert records[0]["checkpoint"] == "LINEUP_CONFIRMATION"
    assert seen_lineup_states == [True]  # only ever evaluated once, with the refreshed (confirmed) state


def test_input_freshness_note_distinguishes_lineup_refresh_from_persisted_inputs():
    game_lineup = _game(game_id="g1", start_time="2026-08-10T23:00:00Z", lineup_confirmed=False)
    game_t90 = _game(game_id="g2", start_time="2026-08-10T23:00:00Z", lineup_confirmed=False)

    def poll_confirms_only_g1(game_pk, away, home, bw, tw):
        confirmed = (game_pk == "g1")
        return {"away": {"lineupConfirmedOfficial": confirmed}, "home": {"lineupConfirmedOfficial": confirmed}}

    records, _, _ = ps.run_prospective_snapshot_cycle(
        "2026-08-10", [game_lineup, game_t90], [], [], now="2026-08-10T21:30:00Z",
        evaluate_game_fn=_fake_evaluate_game(), compute_projection_context_fn=_fake_projection_context,
        lineup_fetch_fn=poll_confirms_only_g1,
    )
    by_game = {r["gameId"]: r for r in records}
    assert by_game["g1"]["checkpoint"] == "LINEUP_CONFIRMATION"
    assert by_game["g1"]["inputFreshnessNote"] == ps.INPUT_FRESHNESS_LINEUP_REFRESHED
    assert by_game["g2"]["checkpoint"] == "T_MINUS_90"
    assert by_game["g2"]["inputFreshnessNote"] == ps.INPUT_FRESHNESS_ALL_PERSISTED
