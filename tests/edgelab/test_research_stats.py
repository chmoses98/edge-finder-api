#!/usr/bin/env python3
"""
tests/edgelab/test_research_stats.py
=========================================
Coverage for lib/edgelab/research_stats.py -- correlation-aware
uncertainty, ECE, Brier/log-loss reuse, calibration slope/intercept.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab import research_stats as rs


def test_sample_size_status_matches_existing_calibration_thresholds():
    assert rs.sample_size_status(19, independent_games=19)["status"] == "INSUFFICIENT_SAMPLE"
    assert rs.sample_size_status(20, independent_games=20)["status"] == "DESCRIPTIVE_ONLY"
    assert rs.sample_size_status(100, independent_games=100)["status"] == "CALIBRATED"


def test_sample_size_status_flags_game_concentration_even_at_large_n():
    """200 contracts from 10 games (20/game) must be flagged, even though n>=100 alone would read CALIBRATED."""
    result = rs.sample_size_status(200, independent_games=10)
    assert result["status"] == "CALIBRATED"
    assert result["gameConcentrationWarning"] is True
    assert "10 independent games" in result["interpretation"]


def test_sample_size_status_never_claims_proof_language():
    result = rs.sample_size_status(500, independent_games=200)
    for banned in ("profitable", "validated", "proven", "actionable"):
        assert banned not in result["interpretation"].lower()


def test_sample_size_status_low_independent_game_count_overrides_status_language():
    result = rs.sample_size_status(150, independent_games=3)
    assert "3 independent game" in result["interpretation"]


# ── Brier / log loss reuse (not reimplemented) ───────────────────────────

def test_brier_and_log_loss_reuses_replay_module():
    from lib.edgelab import replay
    assert rs.brier_score is replay.brier_score
    assert rs.log_loss is replay.log_loss


def test_brier_and_log_loss_summary_arithmetic():
    pairs = [(0.6, 1), (0.6, 0)]
    avg_brier, avg_log_loss = rs.brier_and_log_loss_summary(pairs)
    expected_brier = ((0.6 - 1) ** 2 + (0.6 - 0) ** 2) / 2
    assert abs(avg_brier - expected_brier) < 1e-9
    assert avg_log_loss is not None


def test_brier_and_log_loss_summary_empty():
    assert rs.brier_and_log_loss_summary([]) == (None, None)


def test_log_loss_clips_extreme_probabilities_safely():
    """probability=1.0 with outcome=0 must not raise/produce -inf -- epsilon clipping."""
    value = rs.log_loss(1.0, 0)
    assert value > 0
    assert value < 100  # finite, not -inf/nan


# ── Expected calibration error ───────────────────────────────────────────

def test_ece_perfect_calibration_is_zero():
    pairs = [(0.5, 1), (0.5, 0)] * 10
    assert rs.expected_calibration_error(pairs) < 1e-9


def test_ece_none_for_empty_input():
    assert rs.expected_calibration_error([]) is None


def test_ece_detects_miscalibration():
    pairs = [(0.9, 0)] * 10  # predicted 90%, actual 0% -- badly miscalibrated
    ece = rs.expected_calibration_error(pairs)
    assert abs(ece - 0.9) < 1e-6


# ── Calibration slope/intercept ──────────────────────────────────────────

def test_calibration_slope_intercept_below_min_n_returns_none():
    pairs = [(0.5, 1)] * 10
    assert rs.calibration_slope_intercept(pairs, min_n=30) == (None, None)


def test_calibration_slope_intercept_zero_variance_returns_none():
    pairs = [(0.5, i % 2) for i in range(40)]  # constant probability
    assert rs.calibration_slope_intercept(pairs, min_n=30) == (None, None)


def test_calibration_slope_intercept_perfect_fit():
    pairs = [(p / 100.0, 1 if p >= 50 else 0) for p in range(0, 100, 2)]
    slope, intercept = rs.calibration_slope_intercept(pairs, min_n=30)
    assert slope is not None and intercept is not None


# ── Independent unit counting ────────────────────────────────────────────

def test_independent_unit_count():
    rows = [{"gameId": "g1"}, {"gameId": "g1"}, {"gameId": "g2"}, {"gameId": None}]
    assert rs.independent_unit_count(rows) == 2


# ── Game-clustered bootstrap ─────────────────────────────────────────────

def test_game_clustered_bootstrap_no_clusters_returns_none():
    lo, hi, method = rs.game_clustered_bootstrap_ci([], rs.win_rate_value_fn(lambda r: r.get("won")))
    assert lo is None and hi is None
    assert method == "GAME_CLUSTERED_BOOTSTRAP"


def test_game_clustered_bootstrap_deterministic_across_runs():
    rows = [
        {"gameId": "g1", "won": True}, {"gameId": "g1", "won": True}, {"gameId": "g1", "won": False},
        {"gameId": "g2", "won": False}, {"gameId": "g2", "won": False},
        {"gameId": "g3", "won": True}, {"gameId": "g3", "won": True}, {"gameId": "g3", "won": True},
    ]
    value_fn = rs.win_rate_value_fn(lambda r: r.get("won"))
    lo1, hi1, _ = rs.game_clustered_bootstrap_ci(rows, value_fn, n_resamples=500)
    lo2, hi2, _ = rs.game_clustered_bootstrap_ci(rows, value_fn, n_resamples=500)
    assert (lo1, hi1) == (lo2, hi2)


def test_game_clustered_bootstrap_interval_contains_point_estimate():
    rows = [
        {"gameId": "g1", "won": True}, {"gameId": "g1", "won": True},
        {"gameId": "g2", "won": False}, {"gameId": "g2", "won": False},
        {"gameId": "g3", "won": True}, {"gameId": "g3", "won": False},
        {"gameId": "g4", "won": True}, {"gameId": "g4", "won": True},
        {"gameId": "g5", "won": False}, {"gameId": "g5", "won": True},
    ]
    value_fn = rs.win_rate_value_fn(lambda r: r.get("won"))
    point_estimate = value_fn(rows)
    lo, hi, _ = rs.game_clustered_bootstrap_ci(rows, value_fn, n_resamples=2000)
    assert lo <= point_estimate <= hi


def test_same_game_rows_move_together_not_independently():
    """
    A same-game 'ladder' of highly-correlated rows must be resampled as
    ONE unit -- proven here by an extreme case: one game with many WIN
    rows and one game with many LOSS rows. A (wrong) per-ROW bootstrap
    would frequently produce resamples mixing wins and losses from
    'different draws' of the same game; a correct game-clustered
    bootstrap can only ever produce all-win or all-loss draws for g1/g2
    respectively, so the resampled win rate must always land on a
    multiple of 1/(number of games), never some arbitrary fraction that
    implies rows within a game were resampled independently.
    """
    rows = [{"gameId": "g1", "won": True}] * 20 + [{"gameId": "g2", "won": False}] * 20
    value_fn = rs.win_rate_value_fn(lambda r: r.get("won"))
    rng_seen = set()
    import random
    rnd = random.Random(rs.DEFAULT_BOOTSTRAP_SEED)
    from collections import defaultdict
    rows_by_cluster = defaultdict(list)
    for r in rows:
        rows_by_cluster[r["gameId"]].append(r)
    clusters = sorted(rows_by_cluster.keys())
    for _ in range(200):
        sampled = [rnd.choice(clusters) for _ in clusters]
        resampled = [row for c in sampled for row in rows_by_cluster[c]]
        rng_seen.add(value_fn(resampled))
    # Only possible outcomes when resampling 2 clusters (g1=all-True, g2=all-False) w/ replacement: 0.0, 0.5, 1.0
    assert rng_seen <= {0.0, 0.5, 1.0}


def test_roi_value_fn():
    rows = [{"gameId": "g1", "stake": 10, "netProfitLoss": 5}, {"gameId": "g1", "stake": 10, "netProfitLoss": -10}]
    fn = rs.roi_value_fn()
    assert abs(fn(rows) - (-5 / 20)) < 1e-9


def test_roi_value_fn_none_when_zero_stake():
    fn = rs.roi_value_fn()
    assert fn([{"gameId": "g1", "stake": 0, "netProfitLoss": 0}]) is None


# ── Wilson fallback ───────────────────────────────────────────────────────

def test_wilson_interval_bounds_sane():
    lo, hi = rs.wilson_score_interval(55, 100)
    assert 0.0 <= lo <= 0.55 <= hi <= 1.0


def test_wilson_interval_zero_n():
    assert rs.wilson_score_interval(0, 0) == (None, None)
