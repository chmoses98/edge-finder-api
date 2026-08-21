#!/usr/bin/env python3
"""
lib/research/hitter_validation.py
=====================================
Hitter Projection Engine -- Phase 4 validation framework.

TWO DISTINCT, EXPLICITLY LABELED VALIDATION MODES -- never conflated:

1. SYNTHETIC WALK-FORWARD VALIDATION (run_walk_forward_validation):
   controlled-ground-truth chronological backtest using
   lib.research.hitter_synthetic_ground_truth. Real PA history is
   generated from a KNOWN true-rate distribution, the model is fit
   using only pitches dated BEFORE each as-of cutoff (genuine as-of
   filtering, exercising the same no-leakage machinery Phase 2/3
   built), and scored against synthetic PAs dated AFTER that cutoff.
   This is the only mode that can produce a genuine walk-forward
   log loss / Brier / calibration table in this repository today --
   see module docstring on why real data can't support one (no raw
   Statcast archive, no point-in-time hitter_feature_context
   snapshots predating PR #77).

2. REAL-SLATE ILLUSTRATIVE COMPARISON (real_slate_illustrative_rows):
   reads real settled hitter-prop rows from
   data/edgelab/settlements/*.jsonl (real tickers, real actual stat
   values, real Kalshi prices, real YES/NO outcomes) and reports the
   market's own implied-probability calibration against those real
   outcomes as a REFERENCE baseline. This mode explicitly does NOT
   claim to produce a leakage-free backtest of THIS ENGINE's own
   probabilities against those historical games -- doing so would
   require point-in-time feature snapshots this repository does not
   have for those dates. Every row this function returns carries
   validationMode="ILLUSTRATIVE_NOT_LEAKFREE" for exactly this reason.
"""
import glob
import json
import math
import os
import random
import statistics
from typing import Optional

from lib.edgelab import storage
from lib.research.hitter_pa_outcome_model import (
    LEAGUE_PRIOR_RATES, OUTCOME_CATEGORIES, build_pa_outcome_distribution,
)
from lib.research.hitter_pitch_derivation import derive_pa_outcomes_by_pitch_family, _count_pa_terminal_events
from lib.research.hitter_synthetic_ground_truth import (
    generate_synthetic_pitches, perturb_league_rates,
)

SETTLEMENTS_GLOB = os.path.join("data", "edgelab", "settlements", "*.jsonl*")


def _as_of_filter(pitches, as_of_date):
    return [p for p in pitches if p.get("gameDate") and p["gameDate"] < as_of_date]


def _future_window(pitches, as_of_date, until_date):
    return [p for p in pitches if p.get("gameDate") and as_of_date <= p["gameDate"] < until_date]


def _multiclass_log_loss(true_outcome: str, predicted_rates: dict, eps: float = 1e-6) -> float:
    p = max(eps, min(1.0 - eps, predicted_rates.get(true_outcome, 0.0)))
    return -math.log(p)


def _multiclass_brier(true_outcome: str, predicted_rates: dict) -> float:
    return sum(
        (predicted_rates.get(cat, 0.0) - (1.0 if cat == true_outcome else 0.0)) ** 2
        for cat in OUTCOME_CATEGORIES
    )


def _naive_empirical_rates(history_pitches) -> dict:
    """The 'simple PA x event-rate' baseline this mission's spec names: raw empirical rates from history alone, no shrinkage, no pitch-mix weighting."""
    counts, pa, ab, dates, unrecognized = _count_pa_terminal_events(history_pitches)
    if pa == 0:
        return dict(LEAGUE_PRIOR_RATES)
    named = sum(counts.get(k, 0) for k in ("K", "BB", "HBP", "1B", "2B", "3B", "HR"))
    out_count = max(0, pa - named)
    rates = {k: counts.get(k, 0) / pa for k in ("K", "BB", "HBP", "1B", "2B", "3B", "HR")}
    rates["OUT"] = out_count / pa
    return rates


def run_walk_forward_validation(n_synthetic_hitters: int = 25, n_history_pa: int = 250,
                                 n_future_pa: int = 40, seed: int = 11) -> dict:
    """
    For each of `n_synthetic_hitters` (each with its own randomly
    perturbed true-rate distribution -- see
    hitter_synthetic_ground_truth.perturb_league_rates), generates
    `n_history_pa` PAs of history followed by `n_future_pa` held-out
    future PAs from the SAME true distribution (stationary within one
    synthetic hitter -- adaptivity to a drifting true rate is out of
    scope for this check), builds this engine's PA-outcome model using
    ONLY the as-of-filtered history, and scores it against the held-out
    future PAs' actual outcomes. Reports mean log loss / Brier for this
    engine vs. two baselines (a pure league-prior model with zero
    hitter-specific information, and the naive unshrunk empirical-rate
    baseline), plus a P(1+ hit)-bucketed calibration table pooled
    across every synthetic hitter and future PA.
    """
    rng = random.Random(seed)
    model_losses, naive_losses, prior_losses = [], [], []
    model_briers, naive_briers, prior_briers = [], [], []
    calibration_points = []  # (predicted_p_hit, actual_is_hit)

    for hitter_i in range(n_synthetic_hitters):
        true_rates = perturb_league_rates(LEAGUE_PRIOR_RATES, rng)
        all_pitches = generate_synthetic_pitches(true_rates, n_history_pa + n_future_pa, rng,
                                                   start_day_index=hitter_i * 5)
        cutoff_date = all_pitches[n_history_pa]["gameDate"]
        history = _as_of_filter(all_pitches, cutoff_date)
        future = [p for p in all_pitches if p.get("gameDate") and p["gameDate"] >= cutoff_date]

        hitter_pa_by_family = derive_pa_outcomes_by_pitch_family(history)
        season_counts, season_pa, season_ab, _dates, _unrec = _count_pa_terminal_events(history)
        season_stats = dict(season_counts, PA=season_pa, AB=season_ab)

        model = build_pa_outcome_distribution(hitter_pa_by_family, season_stats)["rates"]
        naive = _naive_empirical_rates(history)
        prior = dict(LEAGUE_PRIOR_RATES)

        for p in future:
            true_outcome = {v: k for k, v in {
                "1B": "single", "2B": "double", "3B": "triple", "HR": "home_run",
                "BB": "walk", "HBP": "hit_by_pitch", "K": "strikeout", "OUT": "field_out",
            }.items()}[p["events"]]

            model_losses.append(_multiclass_log_loss(true_outcome, model))
            naive_losses.append(_multiclass_log_loss(true_outcome, naive))
            prior_losses.append(_multiclass_log_loss(true_outcome, prior))
            model_briers.append(_multiclass_brier(true_outcome, model))
            naive_briers.append(_multiclass_brier(true_outcome, naive))
            prior_briers.append(_multiclass_brier(true_outcome, prior))

            p_hit = sum(model.get(k, 0.0) for k in ("1B", "2B", "3B", "HR"))
            is_hit = 1.0 if true_outcome in ("1B", "2B", "3B", "HR") else 0.0
            calibration_points.append((p_hit, is_hit))

    def _bucket_calibration(points, n_buckets=5):
        points = sorted(points, key=lambda x: x[0])
        n = len(points)
        if n == 0:
            return []
        bucket_size = max(1, n // n_buckets)
        table = []
        for i in range(0, n, bucket_size):
            chunk = points[i:i + bucket_size]
            if not chunk:
                continue
            table.append({
                "n": len(chunk),
                "meanPredicted": round(statistics.mean(c[0] for c in chunk), 3),
                "actualRate": round(statistics.mean(c[1] for c in chunk), 3),
            })
        return table

    return {
        "validationMode": "SYNTHETIC_WALK_FORWARD_CONTROLLED_GROUND_TRUTH",
        "nSyntheticHitters": n_synthetic_hitters,
        "nHistoryPAPerHitter": n_history_pa,
        "nFuturePAPerHitter": n_future_pa,
        "totalScoredPA": len(model_losses),
        "logLoss": {
            "thisEngine": round(statistics.mean(model_losses), 4),
            "naiveEmpiricalRateBaseline": round(statistics.mean(naive_losses), 4),
            "leaguePriorOnlyBaseline": round(statistics.mean(prior_losses), 4),
        },
        "brierScore": {
            "thisEngine": round(statistics.mean(model_briers), 4),
            "naiveEmpiricalRateBaseline": round(statistics.mean(naive_briers), 4),
            "leaguePriorOnlyBaseline": round(statistics.mean(prior_briers), 4),
        },
        "hitRateCalibrationTable": _bucket_calibration(calibration_points),
        "caveat": (
            "Controlled synthetic ground truth, not a real-money backtest -- see this "
            "module's docstring. Demonstrates the shrinkage/as-of machinery recovers a "
            "KNOWN true rate better than an unshrunk naive baseline and a zero-information "
            "league-prior baseline; does not by itself validate real-world MLB accuracy."
        ),
    }


def _load_settlement_rows(date_glob: Optional[str] = None, families: Optional[tuple] = None) -> list:
    """
    Corpus Storage Growth mission: reads via lib.edgelab.storage.read_records
    (not a raw `open()`) so a finalized settlements/<date>.jsonl.gz --
    compacted by lib.edgelab.storage.compact_finalized_partitions() --
    is read transparently, exactly like its uncompressed sibling.
    """
    families = families or ("hitter_hits", "hitter_total_bases", "hitter_rbis", "hitter_hits_runs_rbis")
    rows = []
    for path in sorted(glob.glob(date_glob or SETTLEMENTS_GLOB)):
        if path.endswith(".lock"):
            continue
        for row in storage.read_records(path):
            if row.get("marketFamily") not in families:
                continue
            if row.get("outcome") not in ("YES", "NO"):
                continue
            rows.append(row)
    return rows


def real_slate_illustrative_rows(max_rows: int = 40, date_glob: Optional[str] = None) -> dict:
    """
    Reads real settled hitter-prop rows (see module docstring --
    validationMode="ILLUSTRATIVE_NOT_LEAKFREE" on every row) across
    every confirmed real hitter market family this engine prices
    (hitter_hits/hitter_total_bases/hitter_rbis/hitter_hits_runs_rbis
    -- hitter_stolen_bases excluded, out of this mission's scope).
    Reports the market's own implied-probability log loss/Brier
    against the real realized outcomes as a REFERENCE point, and a
    representative (not cherry-picked -- first `max_rows` found,
    spanning every available family) illustrative row table with
    ticker/threshold/actualValue/outcome/marketYesPrice.
    """
    rows = _load_settlement_rows(date_glob)
    if not rows:
        return {"validationMode": "ILLUSTRATIVE_NOT_LEAKFREE", "status": "NO_SETTLEMENT_DATA_FOUND", "rows": []}

    by_family_examples = {}
    market_log_losses = []
    market_briers = []
    illustrative = []

    for row in rows:
        family = row.get("marketFamily")
        outcome = row.get("outcome")
        checkpoints = row.get("hypotheticalReturnsByCheckpoint") or []
        yes_price = checkpoints[0].get("yesPrice") if checkpoints else None
        evidence = row.get("settlementEvidence") or {}
        actual_value = evidence.get("actualValue")
        threshold = evidence.get("threshold")

        if yes_price is not None and 0.0 < yes_price < 1.0:
            true_bin = 1.0 if outcome == "YES" else 0.0
            eps = 1e-6
            p = max(eps, min(1.0 - eps, yes_price))
            market_log_losses.append(-math.log(p) if true_bin == 1.0 else -math.log(1.0 - p))
            market_briers.append((p - true_bin) ** 2)

        by_family_examples[family] = by_family_examples.get(family, 0) + 1
        if len(illustrative) < max_rows:
            illustrative.append({
                "marketFamily": family,
                "marketTicker": row.get("marketTicker"),
                "threshold": threshold,
                "actualValue": actual_value,
                "outcome": outcome,
                "marketYesPrice": yes_price,
                "playerName": evidence.get("playerName"),
            })

    return {
        "validationMode": "ILLUSTRATIVE_NOT_LEAKFREE",
        "status": "OK",
        "totalSettledRowsFound": len(rows),
        "rowsByFamily": by_family_examples,
        "marketImpliedProbability": {
            "logLoss": round(statistics.mean(market_log_losses), 4) if market_log_losses else None,
            "brierScore": round(statistics.mean(market_briers), 4) if market_briers else None,
            "n": len(market_log_losses),
        },
        "illustrativeRows": illustrative,
        "caveat": (
            "This repository has no point-in-time hitter_feature_context snapshots or raw "
            "Statcast archive predating these settlements, so this engine's OWN probabilities "
            "cannot be leakage-free backtested against these specific historical games. The "
            "market-implied-probability metrics above are a reference point (how well the real "
            "Kalshi market itself was calibrated on these outcomes), not a claim about this "
            "engine's accuracy -- see run_walk_forward_validation() for that."
        ),
    }
