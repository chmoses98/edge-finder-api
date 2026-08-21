#!/usr/bin/env python3
"""
lib/research/hitter_pa_outcome_model.py
==========================================
Hitter Projection Engine -- Phase 4 shared PA-terminal-outcome engine.

This is the ONE outcome model every hitter market in this engine is
priced from -- 1+ hit, HR, total bases, RBI, runs, walks, strikeouts,
and every alternate line all read from the SAME categorical distribution
over {K, BB, HBP, 1B, 2B, 3B, HR, OUT} this module produces, rather than
each market getting its own disconnected regression (explicitly
forbidden by this mission's spec).

PITCH-MIX-WEIGHTED MATCHUP
------------------------------
The finest granularity this model conditions on is PITCH FAMILY: for
each pitch family in TODAY'S OPPOSING PITCHER's own mix
(lib.research.pitch_environment_model), this hitter's own historical
PA-terminal-outcome rate AGAINST THAT SPECIFIC FAMILY
(lib.research.hitter_pitch_derivation.derive_pa_outcomes_by_pitch_family)
is shrunk (lib.research.hitter_shrinkage) toward this hitter's season
baseline, then toward a league prior -- then the per-family rates are
weighted by how often TODAY's pitcher actually throws each family. This
is the literal implementation of this mission's own example: "this
hitter performs unusually well against 95-97mph four-seamers... today's
pitcher throws that profile frequently" -- the pitch-family weighting IS
that interaction (velocity/shape condition the pitch-ENVIRONMENT model
that produces the mix weights; see that module's own docstring for why
this model doesn't ALSO re-condition on velocity/shape within a family --
a deliberate tractability decision documented there).

BOUNDED SECONDARY ADJUSTMENTS
---------------------------------
Platoon (hitter vs this specific starter's throwing hand) and pitcher-
quality (this specific pitcher's K%/BB%/hard-hit% vs league) are applied
as two SEPARATE, capped, toggle-able adjustments after the pitch-mix-
weighted shrinkage -- same bounded-adjustment convention PR #77's
platoon_context.py already established (PLATOON_ADJ_CAP_RPG), not a new
invented pattern. Both can be disabled independently
(lib.research.hitter_feature_ablation calls this with each combination)
to measure their held-out incremental value.

Never invents false precision: every outcome rate's provenance (which
shrinkage levels contributed, effective sample size) is preserved in the
returned `diagnostics` block for lib.research.hitter_explainability.
"""

from typing import Optional

from lib.research.hitter_shrinkage import hierarchical_shrink, ShrinkageLevel
from lib.research.hitter_pitch_derivation import derive_pa_outcomes_by_pitch_family
from lib.research.pitch_taxonomy import classify_pitch_family

OUTCOME_CATEGORIES = ("K", "BB", "HBP", "1B", "2B", "3B", "HR", "OUT")

# Current-era MLB league-average rates per PA -- the ultimate floor
# prior every shrinkage chain in this module bottoms out at. Sums to
# exactly 1.0 by construction (OUT absorbs the remainder).
LEAGUE_PRIOR_RATES = {
    "K": 0.225, "BB": 0.085, "HBP": 0.010, "1B": 0.145,
    "2B": 0.045, "3B": 0.004, "HR": 0.032,
}
LEAGUE_PRIOR_RATES["OUT"] = round(1.0 - sum(LEAGUE_PRIOR_RATES.values()), 4)

# Pseudo-trial "strength" of each shrinkage level -- how many real
# observations a caller would need at the finer level before it starts
# to dominate the broader prior. Family-level splits are noisiest (a
# handful of PAs against one pitch family is far less informative per-PA
# than a full season), so it gets the smallest strength (shrinks hardest);
# season-level baseline is trusted more heavily before falling back to
# the league prior.
FAMILY_LEVEL_STRENGTH = 40.0
SEASON_LEVEL_STRENGTH = 120.0

PLATOON_ADJ_CAP = 0.03   # +/- 3 percentage points of total probability mass, same bounded-adjustment spirit as PLATOON_ADJ_CAP_RPG
PITCHER_QUALITY_ADJ_CAP = 0.03


def _default_pitch_mix() -> dict:
    """Generic league-average-ish family mix, used only when today's pitcher has no derivable pitch mix at all."""
    return {
        "four_seam": 0.33, "sinker": 0.13, "cutter": 0.07, "slider": 0.17,
        "sweeper": 0.03, "curve": 0.08, "knuckle_curve": 0.02,
        "changeup": 0.12, "splitter": 0.03, "other": 0.02,
    }


def _with_out_count(counts: Optional[dict]) -> Optional[dict]:
    """
    Derives the "OUT" bucket (PA minus every other named outcome) for a
    counts dict that only tracks the named outcomes directly -- neither
    derive_baseline_talent_window() nor derive_pa_outcomes_by_pitch_family()
    persist an explicit OUT count (they weren't built with this model's
    8-category split in mind), so this model derives it here rather than
    asking those functions to carry a field only this caller needs.
    """
    if not counts or counts.get("PA") is None:
        return counts
    named = sum(counts.get(k) or 0 for k in ("K", "BB", "HBP", "1B", "2B", "3B", "HR"))
    out = dict(counts)
    out["OUT"] = max(0, counts["PA"] - named)
    return out


def _shrink_family_outcome(family_counts: Optional[dict], season_stats: dict, outcome: str) -> dict:
    """One outcome category's hierarchical_shrink() result for one pitch family."""
    family_pa = (family_counts or {}).get("PA")
    family_successes = (family_counts or {}).get(outcome) if family_counts else None
    season_pa = season_stats.get("PA")
    season_successes = season_stats.get(outcome)

    levels = [
        ShrinkageLevel("pitchFamily", family_successes, family_pa, FAMILY_LEVEL_STRENGTH),
        ShrinkageLevel("season", season_successes, season_pa, SEASON_LEVEL_STRENGTH),
    ]
    return hierarchical_shrink(levels, LEAGUE_PRIOR_RATES[outcome])


def build_matchup_outcome_rates(hitter_pa_by_family: dict, season_stats: dict, pitcher_pitch_mix: Optional[dict]) -> dict:
    """
    Pure function. `hitter_pa_by_family`: this hitter's
    derive_pa_outcomes_by_pitch_family() output (or {} if no raw archive).
    `season_stats`: this hitter's baselineTalent.horizons.currentSeason.stats
    dict (PA/K/BB/1B/2B/3B/HR -- may be all-None if unavailable, which
    hierarchical_shrink handles gracefully by falling through to the
    league prior). `pitcher_pitch_mix`: {family: usageShare 0..1} for
    today's opposing pitcher, or None to use a generic league-average mix.

    Returns {"rates": {outcome: rate, ...} (sums to ~1.0),
    "diagnostics": {outcome: {"byFamily": {family: hierarchical_shrink
    result}, "weightedChain": [...]}}}.
    """
    pitch_mix = pitcher_pitch_mix or _default_pitch_mix()
    total_weight = sum(pitch_mix.values()) or 1.0
    season_stats = _with_out_count(season_stats) or {}

    rates = {}
    diagnostics = {}
    for outcome in OUTCOME_CATEGORIES:
        weighted_rate = 0.0
        by_family = {}
        for family, weight in pitch_mix.items():
            family_counts = _with_out_count(hitter_pa_by_family.get(family))
            result = _shrink_family_outcome(family_counts, season_stats, outcome)
            by_family[family] = result
            weighted_rate += (weight / total_weight) * result["rate"]
        rates[outcome] = weighted_rate
        diagnostics[outcome] = {"byFamily": by_family, "pitchMixUsed": dict(pitch_mix)}

    # Renormalize -- each outcome was shrunk independently (they don't
    # structurally sum to 1 by construction the way a single categorical
    # draw would), so proportional renormalization preserves relative
    # ratios while guaranteeing a valid probability distribution.
    total = sum(rates.values())
    if total > 0:
        rates = {k: v / total for k, v in rates.items()}

    return {"rates": rates, "diagnostics": diagnostics}


def apply_platoon_adjustment(rates: dict, platoon_context: dict, season_woba: Optional[float]) -> dict:
    """
    Bounded nudge from PR #77's existing platoonContext (platoonWOBA vs
    this hitter's own season wOBA) -- shifts probability mass between
    {1B,2B,3B,HR,BB} (favorable) and {K,OUT} (unfavorable) by at most
    PLATOON_ADJ_CAP total, proportionally split among the favorable/
    unfavorable groups by their own current share. A no-op (returns
    `rates` unchanged) when platoonContext has no usable wOBA delta --
    never fabricates an effect from missing data.
    """
    platoon_woba = platoon_context.get("platoonWOBA") if platoon_context else None
    if platoon_woba is None or season_woba is None:
        return dict(rates)

    delta_woba = platoon_woba - season_woba
    # Same wOBA-points -> RPG-ish scaling factor PR #77's platoon_context.py
    # uses, then compressed into a probability-mass shift capped at
    # PLATOON_ADJ_CAP -- reusing the sign/scale convention, not the exact
    # runs formula (that formula is for team runs/game, not one hitter's
    # PA-outcome mix).
    raw_shift = max(-1.0, min(1.0, delta_woba * 10.0)) * PLATOON_ADJ_CAP

    favorable = ("1B", "2B", "3B", "HR", "BB")
    unfavorable = ("K", "OUT")
    fav_total = sum(rates.get(k, 0.0) for k in favorable) or 1.0
    unfav_total = sum(rates.get(k, 0.0) for k in unfavorable) or 1.0

    adjusted = dict(rates)
    for k in favorable:
        adjusted[k] = max(0.0, rates.get(k, 0.0) + raw_shift * (rates.get(k, 0.0) / fav_total))
    for k in unfavorable:
        adjusted[k] = max(0.0, rates.get(k, 0.0) - raw_shift * (rates.get(k, 0.0) / unfav_total))

    total = sum(adjusted.values())
    return {k: v / total for k, v in adjusted.items()} if total > 0 else dict(rates)


def apply_pitcher_quality_adjustment(rates: dict, starter_context: dict) -> dict:
    """
    Bounded nudge from this specific opposing pitcher's own K%/BB%
    relative to league average (starterContext.kPct/bbPct, already
    reused pregame data from api/savant.js) -- shifts K/BB mass toward
    this pitcher's own tendency, capped at PITCHER_QUALITY_ADJ_CAP. A
    no-op when starterContext has no usable kPct/bbPct.
    """
    k_pct = starter_context.get("kPct") if starter_context else None
    bb_pct = starter_context.get("bbPct") if starter_context else None
    adjusted = dict(rates)

    if k_pct is not None:
        league_k = LEAGUE_PRIOR_RATES["K"] * 100
        shift = max(-1.0, min(1.0, (k_pct - league_k) / league_k)) * PITCHER_QUALITY_ADJ_CAP
        adjusted["K"] = max(0.0, adjusted.get("K", 0.0) + shift)
        adjusted["OUT"] = max(0.0, adjusted.get("OUT", 0.0) - shift * 0.5)
        adjusted["1B"] = max(0.0, adjusted.get("1B", 0.0) - shift * 0.5)

    if bb_pct is not None:
        league_bb = LEAGUE_PRIOR_RATES["BB"] * 100
        shift = max(-1.0, min(1.0, (bb_pct - league_bb) / league_bb)) * PITCHER_QUALITY_ADJ_CAP
        adjusted["BB"] = max(0.0, adjusted.get("BB", 0.0) + shift)
        adjusted["OUT"] = max(0.0, adjusted.get("OUT", 0.0) - shift)

    total = sum(adjusted.values())
    return {k: v / total for k, v in adjusted.items()} if total > 0 else dict(rates)


def build_pa_outcome_distribution(
    hitter_pa_by_family: dict, season_stats: dict, pitcher_pitch_mix: Optional[dict] = None,
    platoon_context: Optional[dict] = None, season_woba: Optional[float] = None,
    starter_context: Optional[dict] = None, enable_platoon_adj: bool = True,
    enable_pitcher_quality_adj: bool = True,
) -> dict:
    """
    Top-level entry point. Pure function. Returns {"rates": {...sums to
    ~1.0...}, "diagnostics": {...}} -- see build_matchup_outcome_rates()
    for the core mechanism; enable_platoon_adj/enable_pitcher_quality_adj
    let lib.research.hitter_feature_ablation toggle each bounded layer
    independently.
    """
    base = build_matchup_outcome_rates(hitter_pa_by_family, season_stats, pitcher_pitch_mix)
    rates = base["rates"]
    if enable_platoon_adj and platoon_context:
        rates = apply_platoon_adjustment(rates, platoon_context, season_woba)
    if enable_pitcher_quality_adj and starter_context:
        rates = apply_pitcher_quality_adjustment(rates, starter_context)
    return {"rates": rates, "diagnostics": base["diagnostics"]}


def live_simulation_resample_targets(
    hitter_pa_by_family: dict, season_stats: dict, pitcher_pitch_mix: Optional[dict] = None,
    platoon_context: Optional[dict] = None, season_woba: Optional[float] = None,
    starter_context: Optional[dict] = None,
) -> Optional[dict]:
    """
    Live-pricing-path wiring (Hitter Prop Methodology Repair mission):
    bridges this module's platoon/pitcher-quality adjustment (previously
    only reachable via lib.research.hitter_explainability's diagnostic
    waterfall, never applied to the actual simulated probability) into
    lib.research.lineup_game_simulator's Monte Carlo target-hitter PA
    resolution, via a standard bounded accept/reject resampling scheme
    -- not a new statistical model, just a mechanism for applying an
    already-computed, already-bounded distributional shift to an
    existing simulator's categorical output.

    Returns None when neither adjustment has anything to work with
    (both platoon_context and starter_context are empty/insufficient --
    see apply_platoon_adjustment/apply_pitcher_quality_adjustment's own
    no-op conditions) -- the caller then runs the simulator exactly as
    before this mission, never a fabricated adjustment from missing data.

    Otherwise returns {"multipliers": {outcome: adjusted_rate/base_rate,
    ...}, "adjustedRates": {...sums to ~1.0...}}. `multipliers[outcome]`
    is how the caller should scale its ACCEPTANCE probability for a
    pitch-by-pitch-simulated outcome landing in that category: a value
    > 1 means the adjusted distribution favors this outcome MORE than
    the hitter's own unconditioned pitch-mix-shrunk baseline, < 1 means
    less. Both PLATOON_ADJ_CAP and PITCHER_QUALITY_ADJ_CAP bound each
    adjustment to +/-3 percentage points of an outcome whose typical
    magnitude is itself 5-30%, so these ratios stay in a modest range
    (never a wild multiplier) -- the same boundedness
    apply_platoon_adjustment/apply_pitcher_quality_adjustment already
    guarantee, simply expressed as a ratio instead of an absolute shift.
    """
    if not platoon_context and not starter_context:
        return None
    base_rates = build_matchup_outcome_rates(hitter_pa_by_family, season_stats, pitcher_pitch_mix)["rates"]
    adjusted = build_pa_outcome_distribution(
        hitter_pa_by_family, season_stats, pitcher_pitch_mix,
        platoon_context=platoon_context, season_woba=season_woba, starter_context=starter_context,
    )
    adjusted_rates = adjusted["rates"]
    if adjusted_rates == base_rates:
        return None  # both adjustments were no-ops (missing wOBA/kPct/bbPct data) -- never a fabricated effect
    multipliers = {
        cat: (adjusted_rates[cat] / base_rates[cat]) if base_rates.get(cat, 0.0) > 0 else 1.0
        for cat in OUTCOME_CATEGORIES
    }
    return {"multipliers": multipliers, "adjustedRates": adjusted_rates}
