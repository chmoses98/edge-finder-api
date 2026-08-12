#!/usr/bin/env python3
"""
lib/research/pitch_shape_similarity.py
=========================================
Hitter Projection Engine -- Phase 4 pitch-shape similarity.

A practical, defensible similarity method over the canonical pitch-
shape profile lib.research.pitch_taxonomy.build_pitch_shape_profile()
already produces: normalized Euclidean distance -> Gaussian kernel
weight. Not a hard cluster (this mission's spec explicitly warns
against brittle hard clusters) -- every archived pitch gets a continuous
weight in [0, 1] relative to a query profile, usable for future kernel-
weighted hitter-response estimation (e.g. "how did this hitter perform
against pitches shaped like the one he's about to see," weighted by
similarity rather than a binary in/out-of-cluster decision).

lib.research.hitter_pa_outcome_model does NOT use this for its core
PA-outcome-rate estimate in this milestone -- that model conditions at
PITCH-FAMILY granularity for tractability (see that module's own
docstring). This module exists as the real, tested, standalone
capability this mission's spec asks for, and is available for a future
milestone to fold into a finer-grained matchup model without needing to
invent the similarity method at that point.
"""

import math
from typing import Optional

# Feature -> (typical scale, used to normalize that dimension before
# computing distance, so e.g. spin-rate's ~2000-unit scale doesn't
# dominate release-side's ~2-foot scale). These are rough, documented
# normalization constants -- NOT fit/learned, consistent with this
# being "the simplest defensible method," not a trained model.
_FEATURE_SCALES = {
    "releaseSpeed": 5.0, "inducedVertBreak": 5.0, "horizontalBreak": 5.0,
    "spinRate": 300.0, "releaseHeight": 0.5, "releaseSide": 0.5,
    "extension": 0.5, "armAngle": 10.0,
}


def _normalized_distance_sq(profile_a: dict, profile_b: dict) -> Optional[float]:
    """
    Squared normalized Euclidean distance across every shared,
    non-None numeric dimension. Returns None if the two profiles share
    zero comparable dimensions (e.g. totally missing data) -- never a
    fabricated distance of 0 for incomparable pitches.
    """
    total = 0.0
    n = 0
    for feature, scale in _FEATURE_SCALES.items():
        a, b = profile_a.get(feature), profile_b.get(feature)
        if a is None or b is None:
            continue
        total += ((a - b) / scale) ** 2
        n += 1
    if n == 0:
        return None
    return total / n  # average per-dimension normalized squared distance


def similarity_weight(profile_a: dict, profile_b: dict, bandwidth: float = 1.0) -> Optional[float]:
    """
    Gaussian kernel similarity in (0, 1] -- 1.0 for identical profiles,
    decaying smoothly (not a hard cutoff) as normalized distance grows.
    Different pitch families are NOT automatically forced to 0 -- shape
    alone determines the weight, so e.g. a slider and a sweeper with
    genuinely similar movement/velocity can still score meaningfully
    similar, which is the point of a shape-based (not family-label-
    based) similarity method. Returns None when the two profiles share
    no comparable dimensions.
    """
    if bandwidth <= 0:
        raise ValueError("bandwidth must be positive")
    dist_sq = _normalized_distance_sq(profile_a, profile_b)
    if dist_sq is None:
        return None
    return math.exp(-dist_sq / (2 * bandwidth ** 2))


def weighted_pitches(query_profile: dict, candidate_profiles, bandwidth: float = 1.0):
    """
    Returns [(candidate_profile, weight), ...] sorted by weight
    descending, for candidate_profiles whose similarity_weight() to
    query_profile is resolvable (skips incomparable ones rather than
    assigning them a fabricated weight).
    """
    weighted = []
    for candidate in candidate_profiles:
        w = similarity_weight(query_profile, candidate, bandwidth=bandwidth)
        if w is not None:
            weighted.append((candidate, w))
    weighted.sort(key=lambda pair: pair[1], reverse=True)
    return weighted
