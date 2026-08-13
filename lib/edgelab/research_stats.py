"""
lib/edgelab/research_stats.py
==================================
EdgeLab Research Trustworthiness milestone: correlation-aware statistics
shared by every research report in lib.edgelab.research_reports.

Deliberately does NOT reimplement Brier score / log loss -- both already
exist, correctly, in lib.edgelab.replay (see that module's "Scoring:
Brier score / log loss / calibration error" section) and are imported
from there unchanged.

This module adds what the existing calibration/replay engines do not
have: an explicit reminder that a Kalshi contract is not an independent
observation (thousands of contracts can come from a much smaller number
of games), and the machinery to report uncertainty that respects that
-- a game-clustered (block) bootstrap confidence interval, rather than a
naive per-contract interval that silently assumes independence. See
spec section 14.
"""

import math
import random
from collections import defaultdict

from lib.edgelab.calibration import calibration_status
from lib.edgelab.replay import brier_score, log_loss  # reused verbatim, not reimplemented

# A contract-to-game ratio at or above this is flagged as a real
# clustering concern -- illustrative, documented, not a hard cutoff on
# any computed value (see sample_size_status below, which always still
# reports the real numbers regardless of this flag).
CLUSTERING_WARNING_CONTRACTS_PER_GAME = 5

DEFAULT_BOOTSTRAP_RESAMPLES = 2000
DEFAULT_BOOTSTRAP_CI = 0.90
DEFAULT_BOOTSTRAP_SEED = 20260813  # fixed -- deterministic, reproducible reports (spec section 19 item: repeated-run determinism)


def independent_unit_count(rows, key="gameId"):
    """Count of distinct non-null `key` values -- the 'unique game N' (or player-game N when key='playerGameKey') spec sections 7/14 require alongside raw contract N."""
    return len({r.get(key) for r in rows if r.get(key)})


def sample_size_status(n, independent_games=None, contracts_per_game_warning_threshold=CLUSTERING_WARNING_CONTRACTS_PER_GAME):
    """
    Extends lib.edgelab.calibration.calibration_status (the existing
    n<20/20<=n<100/n>=100 -> INSUFFICIENT_SAMPLE/DESCRIPTIVE_ONLY/
    CALIBRATED scheme, reused verbatim -- not a second, competing
    threshold) with the independent-game dimension spec section 16
    explicitly requires: "no analytics report should automatically call
    a strategy profitable/validated/proven/actionable based solely on
    in-sample ROI ... add independent-game count to the interpretation."

    Returns a dict, never a bare string -- every caller gets the full
    n/independentGames/status/warning/interpretation, so a report can't
    accidentally drop the game-count context.
    """
    status = calibration_status(n)
    game_concentration_warning = False
    if independent_games is not None and independent_games > 0:
        game_concentration_warning = (n / independent_games) >= contracts_per_game_warning_threshold

    if independent_games is not None and independent_games < 5:
        interpretation = (
            f"n={n} contracts drawn from only {independent_games} independent game(s) -- "
            "far too few games for ANY claim beyond 'this is what happened in this tiny sample'. "
            "Not evidence of an edge, regardless of how large n looks."
        )
    elif game_concentration_warning:
        interpretation = (
            f"n={n} contracts but only {independent_games} independent games "
            f"({n / independent_games:.1f} contracts/game) -- raw n materially overstates independence; "
            "treat this as descriptive of a handful of games, not a large validated sample."
        )
    elif status == "INSUFFICIENT_SAMPLE":
        interpretation = f"n={n} is noise, not evidence."
    elif status == "DESCRIPTIVE_ONLY":
        interpretation = f"n={n} is a real number, not yet a calibrated statistical claim."
    else:
        interpretation = (
            f"n={n} across {independent_games} independent games is a meaningful descriptive summary -- "
            "still exploratory, not proof of a betting edge, until it survives out-of-sample validation."
        )

    return {
        "n": n,
        "independentGames": independent_games,
        "status": status,
        "gameConcentrationWarning": game_concentration_warning,
        "interpretation": interpretation,
    }


def expected_calibration_error(pairs, n_bins=10):
    """
    Standard equal-width-bin Expected Calibration Error. `pairs`: iterable
    of (probability, outcome), BOTH already 0-1 scale (probability in
    [0,1], outcome in {0,1}) -- this function does not itself convert
    scales, callers must pass already-normalized values (matching every
    other function in this module and lib.edgelab.replay). Returns None
    for an empty/all-invalid input, never a fabricated 0.0.
    """
    valid = [(p, o) for p, o in pairs if p is not None and o is not None]
    if not valid:
        return None
    bins = [[] for _ in range(n_bins)]
    for p, o in valid:
        idx = min(n_bins - 1, max(0, int(p * n_bins)))
        bins[idx].append((p, o))
    total = len(valid)
    ece = 0.0
    for b in bins:
        if not b:
            continue
        avg_p = sum(p for p, _ in b) / len(b)
        avg_o = sum(o for _, o in b) / len(b)
        ece += (len(b) / total) * abs(avg_p - avg_o)
    return round(ece, 6)


def brier_and_log_loss_summary(pairs):
    """
    Mean Brier score / mean log loss over `pairs` (probability, outcome),
    both 0-1 scale -- thin aggregate wrapper around
    lib.edgelab.replay.brier_score/log_loss (reused, not reimplemented).
    Returns (None, None) for an empty input.
    """
    valid = [(p, o) for p, o in pairs if p is not None and o is not None]
    if not valid:
        return None, None
    briers = [brier_score(p, o) for p, o in valid]
    losses = [log_loss(p, o) for p, o in valid]
    return round(sum(briers) / len(briers), 6), round(sum(losses) / len(losses), 6)


def calibration_slope_intercept(pairs, min_n=30):
    """
    Simple ordinary-least-squares slope/intercept of outcome ~
    probability -- a documented, deliberately simple LINEAR
    approximation of a calibration curve (not a real logistic
    regression fit), gated on min_n so a tiny sample never reports a
    numerically-real-but-meaningless slope. Returns (None, None) below
    min_n or when probability has zero variance in-sample (a flat OLS
    fit has no defined slope there).
    """
    valid = [(p, o) for p, o in pairs if p is not None and o is not None]
    n = len(valid)
    if n < min_n:
        return None, None
    mean_p = sum(p for p, _ in valid) / n
    mean_o = sum(o for _, o in valid) / n
    var_p = sum((p - mean_p) ** 2 for p, _ in valid)
    if var_p == 0:
        return None, None
    cov = sum((p - mean_p) * (o - mean_o) for p, o in valid)
    slope = cov / var_p
    intercept = mean_o - slope * mean_p
    return round(slope, 4), round(intercept, 4)


def wilson_score_interval(wins, n, z=1.645):
    """
    Naive (non-clustered) Wilson score confidence interval around a win
    rate -- documented as a per-CONTRACT interval, i.e. it assumes every
    row is an independent observation, which spec section 14 explicitly
    warns is usually false for this corpus. Used only as a fast fallback
    when game_clustered_bootstrap_ci can't run (e.g. no gameId on the
    rows at all); every report that offers both must label which one it
    used. z=1.645 -> ~90% interval (matches this module's bootstrap
    default). Returns (None, None) when n==0.
    """
    if not n:
        return None, None
    phat = wins / n
    denom = 1 + z * z / n
    center = phat + z * z / (2 * n)
    margin = z * math.sqrt((phat * (1 - phat) + z * z / (4 * n)) / n)
    return round((center - margin) / denom, 4), round((center + margin) / denom, 4)


def game_clustered_bootstrap_ci(
    rows, value_fn, cluster_key="gameId",
    n_resamples=DEFAULT_BOOTSTRAP_RESAMPLES, ci=DEFAULT_BOOTSTRAP_CI, seed=DEFAULT_BOOTSTRAP_SEED,
):
    """
    PREFERRED uncertainty method for any statistic (win rate, ROI,
    calibration error, ...) computed over rows that can share a game
    (spec section 14: "Preferred approach: game-clustered bootstrap or
    another defensible game-clustered confidence interval"). Resamples
    whole CLUSTERS (default: games, via `cluster_key`) with replacement
    -- never individual rows -- so every row belonging to a resampled
    game moves together, preserving within-game correlation instead of
    treating same-game ladder rows as independent (spec section 19 item
    16). Use cluster_key="playerGameKey" (or any per-row key a caller
    has already attached) for player-ladder analyses where that
    clustering is more appropriate than game-level, per spec section 14.

    `value_fn(rows_subset) -> float or None` computes the statistic on
    one resampled row-set (e.g. a win-rate or ROI function); a None
    return (e.g. zero stake in that resample) is dropped from the
    bootstrap distribution rather than crashing or contributing a
    fabricated 0.

    Deterministic: same rows + same seed always produces the same
    interval (DEFAULT_BOOTSTRAP_SEED is fixed, not wall-clock-based) --
    required for reproducible committed reports.

    Returns (low, high, method) where method is always the literal
    string "GAME_CLUSTERED_BOOTSTRAP" so a report can record which
    method actually produced the interval; (None, None, method) when
    there are no valid clusters or the resamples never produced a
    computable value.
    """
    rows_by_cluster = defaultdict(list)
    for r in rows:
        key = r.get(cluster_key)
        if key is not None:
            rows_by_cluster[key].append(r)
    clusters = sorted(rows_by_cluster.keys(), key=str)
    if not clusters:
        return None, None, "GAME_CLUSTERED_BOOTSTRAP"

    rng = random.Random(seed)
    estimates = []
    for _ in range(n_resamples):
        sampled_clusters = [rng.choice(clusters) for _ in clusters]
        resampled_rows = [row for c in sampled_clusters for row in rows_by_cluster[c]]
        value = value_fn(resampled_rows)
        if value is not None:
            estimates.append(value)

    if not estimates:
        return None, None, "GAME_CLUSTERED_BOOTSTRAP"
    estimates.sort()
    alpha = (1.0 - ci) / 2.0
    lo_idx = max(0, min(len(estimates) - 1, round(alpha * (len(estimates) - 1))))
    hi_idx = max(0, min(len(estimates) - 1, round((1.0 - alpha) * (len(estimates) - 1))))
    return round(estimates[lo_idx], 4), round(estimates[hi_idx], 4), "GAME_CLUSTERED_BOOTSTRAP"


def win_rate_value_fn(win_predicate):
    """Builds a value_fn for game_clustered_bootstrap_ci that computes a win rate: win_predicate(row) -> True/False/None (None rows excluded from both numerator and denominator, e.g. PUSH/VOID/unresolved)."""
    def _value_fn(rows_subset):
        decided = [win_predicate(r) for r in rows_subset]
        decided = [d for d in decided if d is not None]
        return (sum(1 for d in decided if d) / len(decided)) if decided else None
    return _value_fn


def roi_value_fn(stake_key="stake", pl_key="netProfitLoss"):
    """Builds a value_fn for game_clustered_bootstrap_ci that computes ROI = sum(P/L) / sum(stake)."""
    def _value_fn(rows_subset):
        total_stake = sum(r.get(stake_key) or 0 for r in rows_subset)
        if not total_stake:
            return None
        total_pl = sum(r.get(pl_key) or 0 for r in rows_subset)
        return total_pl / total_stake
    return _value_fn
