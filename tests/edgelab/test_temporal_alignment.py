#!/usr/bin/env python3
"""
tests/edgelab/test_temporal_alignment.py
=============================================
Regression coverage for lib/edgelab/temporal_alignment.py -- the
no-look-ahead ModelEvaluation<->checkpoint selector. These tests exist
specifically to prove a LATER evaluation can never leak backward into an
EARLIER checkpoint (spec section 5/19 items 2-3, 19).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab import temporal_alignment as ta


def _eval(eval_id, pipeline_run_id, selection="ML_Away", side=None, threshold=None, **overrides):
    row = {
        "modelEvaluationId": eval_id,
        "pipelineRunId": pipeline_run_id,
        "selection": selection,
        "side": side,
        "threshold": threshold,
    }
    row.update(overrides)
    return row


# ── Core no-look-ahead rule ──────────────────────────────────────────────

def test_future_evaluation_never_selected_for_earlier_checkpoint():
    """A single evaluation produced AFTER the checkpoint must never be selected."""
    evals = [_eval("future", "2026-08-07T22:00:00Z")]
    selected, candidates, reason = ta.select_temporally_valid_evaluation(evals, "2026-08-07T10:00:00Z")
    assert selected is None
    assert candidates == []
    assert reason == ta.ALL_EVALUATIONS_AFTER_CHECKPOINT


def test_evaluation_exactly_at_checkpoint_time_is_eligible():
    """pipelineRunId == capturedAt is inclusive ('at or before')."""
    evals = [_eval("same-instant", "2026-08-07T10:00:00Z")]
    selected, candidates, reason = ta.select_temporally_valid_evaluation(evals, "2026-08-07T10:00:00Z")
    assert selected["modelEvaluationId"] == "same-instant"
    assert reason is None


def test_selects_latest_eligible_not_earliest():
    """Among several evaluations all <= T for the SAME selection, picks the most recent one, not the first-in-list."""
    evals = [
        _eval("early", "2026-08-07T08:00:00Z"),
        _eval("mid", "2026-08-07T09:00:00Z"),
        _eval("late-but-still-eligible", "2026-08-07T09:59:00Z"),
        _eval("too-late", "2026-08-07T10:01:00Z"),
    ]
    selected, candidates, reason = ta.select_temporally_valid_evaluation(evals, "2026-08-07T10:00:00Z")
    assert selected["modelEvaluationId"] == "late-but-still-eligible"
    assert reason is None


def test_list_order_never_matters():
    """Same evaluations, shuffled order, must select the identical result -- proves this isn't an unordered [-1] pick."""
    evals_forward = [
        _eval("a", "2026-08-07T08:00:00Z"),
        _eval("b", "2026-08-07T09:00:00Z"),
        _eval("c", "2026-08-07T09:30:00Z"),
    ]
    evals_reversed = list(reversed(evals_forward))
    sel_fwd, _, _ = ta.select_temporally_valid_evaluation(evals_forward, "2026-08-07T10:00:00Z")
    sel_rev, _, _ = ta.select_temporally_valid_evaluation(evals_reversed, "2026-08-07T10:00:00Z")
    assert sel_fwd["modelEvaluationId"] == sel_rev["modelEvaluationId"] == "c"


# ── Missing/untrustworthy timestamps ─────────────────────────────────────

def test_no_evaluations_for_ticker():
    selected, candidates, reason = ta.select_temporally_valid_evaluation([], "2026-08-07T10:00:00Z")
    assert selected is None and candidates == [] and reason == ta.NO_EVALUATIONS_FOR_TICKER


def test_evaluation_with_no_pipeline_run_id_never_selected():
    """A NOT_EVALUATED/full-universe-extension row (pipelineRunId=None) has no provable causal timestamp -- never fabricated as eligible."""
    evals = [_eval("no-timestamp", None)]
    selected, candidates, reason = ta.select_temporally_valid_evaluation(evals, "2026-08-07T10:00:00Z")
    assert selected is None
    assert reason == ta.NO_CAUSAL_TIMESTAMP


def test_mixed_timestamped_and_untimestamped_ignores_the_untimestamped_row():
    evals = [_eval("no-timestamp", None), _eval("real", "2026-08-07T08:00:00Z")]
    selected, candidates, reason = ta.select_temporally_valid_evaluation(evals, "2026-08-07T10:00:00Z")
    assert selected["modelEvaluationId"] == "real"


def test_unparseable_observation_timestamp():
    evals = [_eval("e1", "2026-08-07T08:00:00Z")]
    selected, candidates, reason = ta.select_temporally_valid_evaluation(evals, "not-a-timestamp")
    assert selected is None and reason == ta.OBSERVATION_TIMESTAMP_UNPARSEABLE


def test_none_observation_timestamp():
    evals = [_eval("e1", "2026-08-07T08:00:00Z")]
    selected, candidates, reason = ta.select_temporally_valid_evaluation(evals, None)
    assert selected is None and reason == ta.OBSERVATION_TIMESTAMP_UNPARSEABLE


# ── Multi-selection disambiguation (real-data finding: one ticker, two selections) ──

def test_multiple_selections_same_ticker_both_returned_as_candidates():
    """A spread ticker's away/home sides both evaluated (same pipelineRunId) -- both are causally valid candidates, not silently dropped."""
    evals = [
        _eval("away", "2026-08-07T08:00:00Z", selection="RL_Away", side="NO"),
        _eval("home", "2026-08-07T08:00:00Z", selection="RL_Home", side="YES"),
    ]
    selected, candidates, reason = ta.select_temporally_valid_evaluation(evals, "2026-08-07T10:00:00Z")
    assert reason is None
    assert {c["modelEvaluationId"] for c in candidates} == {"away", "home"}
    # Deterministic primary pick: the YES-side evaluation.
    assert selected["modelEvaluationId"] == "home"


def test_multi_selection_primary_pick_is_deterministic_regardless_of_order():
    evals_a = [
        _eval("away", "2026-08-07T08:00:00Z", selection="RL_Away", side="NO"),
        _eval("home", "2026-08-07T08:00:00Z", selection="RL_Home", side="YES"),
    ]
    evals_b = list(reversed(evals_a))
    sel_a, _, _ = ta.select_temporally_valid_evaluation(evals_a, "2026-08-07T10:00:00Z")
    sel_b, _, _ = ta.select_temporally_valid_evaluation(evals_b, "2026-08-07T10:00:00Z")
    assert sel_a["modelEvaluationId"] == sel_b["modelEvaluationId"]


def test_same_selection_re_evaluated_over_time_picks_latest_not_a_second_selection_bucket():
    """Two rows sharing the SAME (selection, side, threshold) but different pipelineRunId are a re-evaluation, not two candidates."""
    evals = [
        _eval("v1", "2026-08-07T08:00:00Z", selection="ML_Away", side="YES"),
        _eval("v2", "2026-08-07T09:00:00Z", selection="ML_Away", side="YES"),
    ]
    selected, candidates, reason = ta.select_temporally_valid_evaluation(evals, "2026-08-07T10:00:00Z")
    assert len(candidates) == 1
    assert selected["modelEvaluationId"] == "v2"
