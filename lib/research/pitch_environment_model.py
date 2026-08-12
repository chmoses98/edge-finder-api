#!/usr/bin/env python3
"""
lib/research/pitch_environment_model.py
==========================================
Hitter Projection Engine -- Phase 4 pitch-environment model: P(pitch
type | state) for TODAY'S opposing pitcher, plus conditional velocity/
movement/release/location summaries per pitch family.

Reuses lib.research.statcast_pitch_store.load_pitches_for_pitcher()
(Phase 4's new symmetric pitcher index) and lib.research.pitch_taxonomy's
classify_pitch_family()/velocity_bucket() -- no new classification logic
is duplicated here.

CONDITIONAL MIX (batter handedness / count bucket), NOT ASSUMED CONSTANT
----------------------------------------------------------------------------
Per this mission's spec ("do not assume a pitcher's overall arsenal mix
is constant across counts or hitter handedness when sufficient data
exists"), derive_pitcher_pitch_mix() can be conditioned on
`batter_hand` and/or `count_bucket` -- each conditioned slice is
hierarchically shrunk (lib.research.hitter_shrinkage) toward the
pitcher's own OVERALL mix before being returned, so a small conditioned
sample degrades gracefully toward the always-available overall mix
rather than reporting a noisy conditional estimate at full confidence.
"""

from typing import Optional

from lib.research.pitch_taxonomy import classify_pitch_family, classify_count_state
from lib.research.hitter_shrinkage import hierarchical_shrink, ShrinkageLevel

CONDITIONED_MIX_STRENGTH = 80.0   # pseudo-pitches of trust in the overall mix before a conditioned slice dominates it


def _family_counts(pitches) -> dict:
    counts = {}
    for p in pitches:
        family = classify_pitch_family(p.get("pitchType"), p.get("pitchName"))
        counts[family] = counts.get(family, 0) + 1
    return counts


def derive_pitcher_pitch_mix(pitcher_pitches, batter_hand: Optional[str] = None, count_bucket: Optional[str] = None) -> dict:
    """
    pitcher_pitches: this pitcher's archived pitches (already as-of
    filtered by the caller -- see lib.research.statcast_pitch_store.
    load_pitches_for_pitcher). batter_hand: 'L'/'R', or None for the
    unconditioned overall mix. count_bucket: one of
    pitch_taxonomy.classify_count_state()'s boolean keys (e.g.
    'twoStrikes', 'hitterAhead'), or None.

    Returns {"status", "mix": {family: share, ...sums to ~1...},
    "sampleSize", "conditionedSampleSize", "shrinkageApplied": bool}.
    status=MISSING_DATA (empty mix) when there is no archive at all for
    this pitcher -- callers (hitter_pa_outcome_model) already fall back
    to a generic league-average mix in that case, so this function never
    fabricates one itself.
    """
    if not pitcher_pitches:
        return {"status": "MISSING_DATA", "mix": {}, "sampleSize": 0, "conditionedSampleSize": 0, "shrinkageApplied": False}

    overall_counts = _family_counts(pitcher_pitches)
    overall_total = sum(overall_counts.values())
    overall_mix = {f: c / overall_total for f, c in overall_counts.items()}

    if batter_hand is None and count_bucket is None:
        return {"status": "AVAILABLE", "mix": overall_mix, "sampleSize": overall_total,
                "conditionedSampleSize": overall_total, "shrinkageApplied": False}

    conditioned = pitcher_pitches
    if batter_hand is not None:
        conditioned = [p for p in conditioned if p.get("batterHand") == batter_hand]
    if count_bucket is not None:
        conditioned = [p for p in conditioned if classify_count_state(p.get("balls"), p.get("strikes")).get(count_bucket)]

    conditioned_counts = _family_counts(conditioned)
    conditioned_total = sum(conditioned_counts.values())

    shrunk_mix = {}
    all_families = set(overall_mix) | set(conditioned_counts)
    for family in all_families:
        levels = [
            ShrinkageLevel("conditioned", conditioned_counts.get(family, 0), conditioned_total, CONDITIONED_MIX_STRENGTH),
        ]
        result = hierarchical_shrink(levels, overall_mix.get(family, 0.0))
        shrunk_mix[family] = result["rate"]

    total = sum(shrunk_mix.values())
    if total > 0:
        shrunk_mix = {f: v / total for f, v in shrunk_mix.items()}

    return {
        "status": "AVAILABLE",
        "mix": shrunk_mix,
        "sampleSize": overall_total,
        "conditionedSampleSize": conditioned_total,
        "shrinkageApplied": True,
    }


def derive_pitcher_pitch_profile_by_family(pitcher_pitches) -> dict:
    """
    Per-family average shape/velocity/release summary for this pitcher
    -- the same representation-only approach
    hitter_feature_context._pitch_shape_context() already uses on the
    hitter side, mirrored here for the pitcher side. No clustering, no
    similarity scoring -- see lib.research.pitch_shape_similarity for that.
    """
    by_family = {}
    for p in pitcher_pitches:
        family = classify_pitch_family(p.get("pitchType"), p.get("pitchName"))
        by_family.setdefault(family, []).append(p)

    def _avg(values):
        vals = [v for v in values if v is not None]
        return round(sum(vals) / len(vals), 2) if vals else None

    return {
        family: {
            "sampleSize": len(pitches),
            "avgReleaseSpeed": _avg(p.get("releaseSpeed") for p in pitches),
            "avgInducedVertBreak": _avg(p.get("inducedVertBreak") for p in pitches),
            "avgHorizontalBreak": _avg(p.get("horizontalBreak") for p in pitches),
            "avgSpinRate": _avg(p.get("spinRate") for p in pitches),
            "avgReleaseHeight": _avg(p.get("releaseHeight") for p in pitches),
            "avgReleaseSide": _avg(p.get("releaseSide") for p in pitches),
            "avgExtension": _avg(p.get("extension") for p in pitches),
            "avgArmAngle": _avg(p.get("armAngle") for p in pitches),
        }
        for family, pitches in by_family.items()
    }
