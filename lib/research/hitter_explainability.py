#!/usr/bin/env python3
"""
lib/research/hitter_explainability.py
========================================
Hitter Projection Engine -- Phase 4 approximate, defensible feature-
group attribution for one hitter's PA-outcome rates.

WHY A WATERFALL, NOT A TRUE SHAP/DECOMPOSITION: this engine's downstream
markets (hits/HR/TB/RBI/runs/walks/K) come from a full Monte Carlo game
simulation, not a closed-form additive model -- there is no exact,
mathematically defensible per-feature decomposition of, say, "how many
of these hits are attributable to platoon vs. pitch-mix" (this
mission's own instruction: "not fabricated exact decomposition when the
architecture can't support it"). What IS defensible is a SEQUENTIAL
waterfall: start from the league-average PA-outcome distribution, add
one feature group at a time (in the SAME order
lib.research.hitter_pa_outcome_model.build_pa_outcome_distribution
actually applies them), and report the marginal shift in each outcome
rate after each addition. This is honest about being ORDER-DEPENDENT
(the marginal contribution of platoon adjustment when added AFTER
pitch-mix shrinkage is not necessarily identical to its contribution in
isolation) -- documented here rather than hidden.

This module recomputes lib.research.hitter_pa_outcome_model at each
step (cheap -- no Monte Carlo simulation involved, just the shrinkage
arithmetic) rather than re-running lib.research.hitter_market_distributions'
full game simulation four times; RBI/runs explainability inherits from
the same PA-level waterfall since lib.research.lineup_game_simulator
resolves the target hitter's own PA via this exact rate chain (contact-
model batted-ball shape/park/wind/defense effects are a separate,
later stage of the pipeline this module does not re-decompose -- see
`contactModelNote` in the returned dict).
"""
from typing import Optional

from lib.research.hitter_pa_outcome_model import (
    LEAGUE_PRIOR_RATES,
    build_matchup_outcome_rates,
    apply_platoon_adjustment,
    apply_pitcher_quality_adjustment,
)

HIT_CATEGORIES = ("1B", "2B", "3B", "HR")


def _summary(rates: dict) -> dict:
    return {
        "hitRate": round(sum(rates.get(k, 0.0) for k in HIT_CATEGORIES), 4),
        "hrRate": round(rates.get("HR", 0.0), 4),
        "kRate": round(rates.get("K", 0.0), 4),
        "bbRate": round(rates.get("BB", 0.0), 4),
    }


def _delta(rates_after: dict, rates_before: dict) -> dict:
    keys = set(rates_after) | set(rates_before)
    return {k: round(rates_after.get(k, 0.0) - rates_before.get(k, 0.0), 4) for k in sorted(keys)}


def explain_hitter_pa_outcome(
    hitter_pa_by_family: dict,
    season_stats: dict,
    pitcher_pitch_mix: Optional[dict] = None,
    platoon_context: Optional[dict] = None,
    season_woba: Optional[float] = None,
    starter_context: Optional[dict] = None,
) -> dict:
    """
    Pure function. Returns a "steps" list (league prior -> hitter/
    pitch-mix matchup shrinkage -> + platoon -> + pitcher quality),
    each carrying its own full rate distribution, a rounded summary
    (hitRate/hrRate/kRate/bbRate), and the delta vs. the immediately
    preceding step. Also reports which feature groups actually had
    live data to work with (never claims a feature contributed
    anything when its own inputs were all missing/None).
    """
    step0_rates = dict(LEAGUE_PRIOR_RATES)

    base = build_matchup_outcome_rates(hitter_pa_by_family, season_stats, pitcher_pitch_mix)
    step1_rates = base["rates"]

    step2_rates = apply_platoon_adjustment(step1_rates, platoon_context or {}, season_woba) if platoon_context else dict(step1_rates)
    step3_rates = apply_pitcher_quality_adjustment(step2_rates, starter_context or {}) if starter_context else dict(step2_rates)

    steps = [
        {"label": "league_prior", "rates": {k: round(v, 4) for k, v in step0_rates.items()},
         "summary": _summary(step0_rates), "deltaFromPrevious": None},
        {"label": "hitter_pitch_mix_matchup_shrinkage", "rates": {k: round(v, 4) for k, v in step1_rates.items()},
         "summary": _summary(step1_rates), "deltaFromPrevious": _delta(step1_rates, step0_rates)},
        {"label": "platoon_adjustment", "rates": {k: round(v, 4) for k, v in step2_rates.items()},
         "summary": _summary(step2_rates), "deltaFromPrevious": _delta(step2_rates, step1_rates)},
        {"label": "pitcher_quality_adjustment", "rates": {k: round(v, 4) for k, v in step3_rates.items()},
         "summary": _summary(step3_rates), "deltaFromPrevious": _delta(step3_rates, step2_rates)},
    ]

    feature_groups_applied = {
        "hitterPitchByFamilyArchive": bool(hitter_pa_by_family),
        "seasonBaselineStats": bool(season_stats and season_stats.get("PA")),
        "pitcherPitchMix": pitcher_pitch_mix is not None,
        "platoonAdjustment": bool(platoon_context and platoon_context.get("platoonWOBA") is not None and season_woba is not None),
        "pitcherQualityAdjustment": bool(starter_context and (starter_context.get("kPct") is not None or starter_context.get("bbPct") is not None)),
    }

    return {
        "steps": steps,
        "finalRates": {k: round(v, 4) for k, v in step3_rates.items()},
        "featureGroupsApplied": feature_groups_applied,
        "contactModelNote": (
            "This waterfall covers PA-terminal-outcome rates only (K/BB/HBP/1B/2B/3B/HR/OUT). "
            "Once an outcome resolves IN_PLAY in the full pitch-by-pitch simulation "
            "(lib.research.pitch_sequence_model), lib.research.hitter_contact_model's EV/LA/spray "
            "draw and park/wind/defense conversion is a separate, later modeling stage not "
            "decomposed here -- see lib.research.hitter_feature_ablation for that stage's "
            "held-out incremental-value measurement instead of a per-feature attribution."
        ),
    }
