#!/usr/bin/env python3
"""
lib/research/hitter_feature_ablation.py
==========================================
Hitter Projection Engine -- Phase 4 feature-group ablation.

Answers, for each toggle-able bounded adjustment
lib.research.hitter_pa_outcome_model.build_pa_outcome_distribution
exposes (enable_platoon_adj / enable_pitcher_quality_adj): does turning
it ON actually improve held-out predictive accuracy, or does the model
merely assume it helps? Per this mission's explicit instruction
("report whether held-out results actually improve rather than
assuming an advanced feature is valuable"), this module measures the
real effect using lib.research.hitter_synthetic_ground_truth's
controlled ground truth -- see that module's docstring for why real
data can't support this in this repository today (no raw Statcast
archive, no point-in-time snapshots). A synthetic future PA window is
generated from a true-rate distribution that DOES carry a real platoon
/ pitcher-quality effect of a known magnitude, and the SAME magnitude
is supplied to the model as its own platoon_context/starter_context --
this measures whether the adjustment mechanism itself (shape, cap,
direction) recovers a real signal of that size, not whether real MLB
platoon effects happen to match this magnitude.
"""
import random
import statistics

from lib.research.hitter_pa_outcome_model import (
    LEAGUE_PRIOR_RATES, build_pa_outcome_distribution,
)
from lib.research.hitter_pitch_derivation import derive_pa_outcomes_by_pitch_family, _count_pa_terminal_events
from lib.research.hitter_synthetic_ground_truth import generate_synthetic_pitches
from lib.research.hitter_validation import _multiclass_log_loss, _multiclass_brier

_EVENT_TO_OUTCOME = {
    "single": "1B", "double": "2B", "triple": "3B", "home_run": "HR",
    "walk": "BB", "hit_by_pitch": "HBP", "strikeout": "K", "field_out": "OUT",
}


def _shifted_true_rates(base_rates: dict, favorable_shift: float) -> dict:
    """Same directional shift apply_platoon_adjustment/apply_pitcher_quality_adjustment make -- mass moves between {1B,2B,3B,HR,BB} and {K,OUT} -- but computed independently here as the GROUND TRUTH the ablation scores against, not by calling those functions."""
    favorable = ("1B", "2B", "3B", "HR", "BB")
    unfavorable = ("K", "OUT")
    fav_total = sum(base_rates.get(k, 0.0) for k in favorable) or 1.0
    unfav_total = sum(base_rates.get(k, 0.0) for k in unfavorable) or 1.0
    shifted = dict(base_rates)
    for k in favorable:
        shifted[k] = max(0.0, base_rates.get(k, 0.0) + favorable_shift * (base_rates.get(k, 0.0) / fav_total))
    for k in unfavorable:
        shifted[k] = max(0.0, base_rates.get(k, 0.0) - favorable_shift * (base_rates.get(k, 0.0) / unfav_total))
    total = sum(shifted.values())
    return {k: v / total for k, v in shifted.items()}


def ablate_platoon_adjustment(n_trials: int = 400, true_shift: float = 0.025, seed: int = 3) -> dict:
    """
    Each trial: a synthetic hitter's neutral history (no true platoon
    effect baked into the history itself) followed by a held-out future
    PA window whose TRUE generating rates carry a real `true_shift`
    favorable shift (see _shifted_true_rates) -- exactly what a real
    platoon-favorable matchup would look like. Scores
    build_pa_outcome_distribution with enable_platoon_adj=True (given
    platoon_context/season_woba describing that same shift) against
    enable_platoon_adj=False (given nothing) on those held-out PAs.
    """
    rng = random.Random(seed)
    losses_on, losses_off = [], []
    briers_on, briers_off = [], []

    for trial_i in range(n_trials):
        history = generate_synthetic_pitches(LEAGUE_PRIOR_RATES, 200, rng, start_day_index=trial_i * 3)
        true_future_rates = _shifted_true_rates(LEAGUE_PRIOR_RATES, true_shift)
        future = generate_synthetic_pitches(true_future_rates, 15, rng, start_day_index=trial_i * 3 + 200)

        hitter_pa_by_family = derive_pa_outcomes_by_pitch_family(history)
        season_counts, season_pa, season_ab, _d, _u = _count_pa_terminal_events(history)
        season_stats = dict(season_counts, PA=season_pa, AB=season_ab)

        platoon_context = {"platoonWOBA": 0.360}
        season_woba = 0.360 - (true_shift * 10.0 * 0.1)  # consistent with apply_platoon_adjustment's woba->shift scaling (raw_shift = delta_woba * 10 * CAP); solved for a delta that maps back to true_shift's direction/scale

        rates_on = build_pa_outcome_distribution(
            hitter_pa_by_family, season_stats, platoon_context=platoon_context, season_woba=season_woba,
            enable_platoon_adj=True, enable_pitcher_quality_adj=False,
        )["rates"]
        rates_off = build_pa_outcome_distribution(
            hitter_pa_by_family, season_stats, enable_platoon_adj=False, enable_pitcher_quality_adj=False,
        )["rates"]

        for p in future:
            true_outcome = _EVENT_TO_OUTCOME[p["events"]]
            losses_on.append(_multiclass_log_loss(true_outcome, rates_on))
            losses_off.append(_multiclass_log_loss(true_outcome, rates_off))
            briers_on.append(_multiclass_brier(true_outcome, rates_on))
            briers_off.append(_multiclass_brier(true_outcome, rates_off))

    mean_on, mean_off = statistics.mean(losses_on), statistics.mean(losses_off)
    return {
        "featureGroup": "platoon_adjustment",
        "nTrials": n_trials,
        "nScoredPA": len(losses_on),
        "logLoss": {"adjustmentOn": round(mean_on, 4), "adjustmentOff": round(mean_off, 4),
                    "improvement": round(mean_off - mean_on, 4)},
        "brierScore": {"adjustmentOn": round(statistics.mean(briers_on), 4),
                        "adjustmentOff": round(statistics.mean(briers_off), 4),
                        "improvement": round(statistics.mean(briers_off) - statistics.mean(briers_on), 4)},
        "heldOutResultsImprove": mean_on < mean_off,
        "caveat": "Controlled synthetic ground truth (see this module's docstring) -- measures whether the adjustment MECHANISM recovers a known-magnitude signal, not real-world platoon-effect accuracy.",
    }


def ablate_pitcher_quality_adjustment(n_trials: int = 400, true_shift: float = 0.02, seed: int = 5) -> dict:
    """Same design as ablate_platoon_adjustment, for the pitcher-quality (K%/BB%) adjustment -- true future PAs generated with an UNFAVORABLE shift (elevated strikeout true rate), scored with a starter_context describing an elevated kPct vs without one."""
    rng = random.Random(seed)
    losses_on, losses_off = [], []
    briers_on, briers_off = [], []

    for trial_i in range(n_trials):
        history = generate_synthetic_pitches(LEAGUE_PRIOR_RATES, 200, rng, start_day_index=trial_i * 3)
        true_future_rates = _shifted_true_rates(LEAGUE_PRIOR_RATES, -true_shift)  # unfavorable to hitter (elevated K)
        future = generate_synthetic_pitches(true_future_rates, 15, rng, start_day_index=trial_i * 3 + 200)

        hitter_pa_by_family = derive_pa_outcomes_by_pitch_family(history)
        season_counts, season_pa, season_ab, _d, _u = _count_pa_terminal_events(history)
        season_stats = dict(season_counts, PA=season_pa, AB=season_ab)

        starter_context = {"kPct": 30.0, "bbPct": 8.5}  # elevated K vs league (22.5)

        rates_on = build_pa_outcome_distribution(
            hitter_pa_by_family, season_stats, starter_context=starter_context,
            enable_platoon_adj=False, enable_pitcher_quality_adj=True,
        )["rates"]
        rates_off = build_pa_outcome_distribution(
            hitter_pa_by_family, season_stats, enable_platoon_adj=False, enable_pitcher_quality_adj=False,
        )["rates"]

        for p in future:
            true_outcome = _EVENT_TO_OUTCOME[p["events"]]
            losses_on.append(_multiclass_log_loss(true_outcome, rates_on))
            losses_off.append(_multiclass_log_loss(true_outcome, rates_off))
            briers_on.append(_multiclass_brier(true_outcome, rates_on))
            briers_off.append(_multiclass_brier(true_outcome, rates_off))

    mean_on, mean_off = statistics.mean(losses_on), statistics.mean(losses_off)
    return {
        "featureGroup": "pitcher_quality_adjustment",
        "nTrials": n_trials,
        "nScoredPA": len(losses_on),
        "logLoss": {"adjustmentOn": round(mean_on, 4), "adjustmentOff": round(mean_off, 4),
                    "improvement": round(mean_off - mean_on, 4)},
        "brierScore": {"adjustmentOn": round(statistics.mean(briers_on), 4),
                        "adjustmentOff": round(statistics.mean(briers_off), 4),
                        "improvement": round(statistics.mean(briers_off) - statistics.mean(briers_on), 4)},
        "heldOutResultsImprove": mean_on < mean_off,
        "caveat": "Controlled synthetic ground truth (see this module's docstring) -- measures whether the adjustment MECHANISM recovers a known-magnitude signal, not real-world pitcher-quality accuracy.",
    }


def run_full_ablation_report(seed: int = 9) -> dict:
    return {
        "platoonAdjustment": ablate_platoon_adjustment(seed=seed),
        "pitcherQualityAdjustment": ablate_pitcher_quality_adjustment(seed=seed + 1),
    }
