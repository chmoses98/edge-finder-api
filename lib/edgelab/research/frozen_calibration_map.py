"""
lib/edgelab/research/frozen_calibration_map.py
===============================================
Frozen, reviewable calibration artifact for the MLB probability engine.
RESEARCH ONLY -- NOT wired into any production path. It exists so that a
production change, if authorised, is a one-line call to a pure function
whose parameters live in a committed JSON artifact with full provenance.

The artifact (data/edgelab/analytics/frozen_calibration_map_v1.json) holds
two alternative recipes fit on the point-in-time research dataset
(settled slate dates 2026-08-02 .. 2026-08-31):

  "drop_in"      family logit-affine maps applied to production's EXISTING
                 Poisson probability:  p' = sigmoid(a_f + b_f * logit(p)).
  "structural"   the same family maps applied AFTER re-pricing run-based
                 contracts with the frozen negative-binomial distribution
                 (dispersion 0.281513, MLB-RSCH-0010) and a mean shift.

Both were validated walk-forward (expanding window by slate date) and on
two frozen pseudo-holdouts before the all-data parameters here were fit;
see docs/EDGELAB_MLB_CALIBRATION_RESEARCH_2026_09.md.  Families listed
under "quarantine" are ones where even the calibrated probability is no
better than a walk-forward base rate; the map is still provided for them
but a caller should treat their probabilities as uninformative.

Rollback: the artifact carries "productionActive": false; a caller that
gates on that flag (or simply does not call apply_calibrated_probability)
gets production's unchanged probability.  No state, no I/O beyond the
one-time JSON read.
"""
import json
import math
import os

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
ARTIFACT_PATH = os.path.join(_ROOT, "data", "edgelab", "analytics", "frozen_calibration_map_v1.json")
_EPS = 1e-4
_cache = {}


def load_artifact(path=ARTIFACT_PATH):
    if path not in _cache:
        with open(path) as f:
            _cache[path] = json.load(f)
    return _cache[path]


def _logit(p):
    p = min(max(float(p), _EPS), 1 - _EPS)
    return math.log(p / (1 - p))


def _sigmoid(z):
    return 1.0 / (1.0 + math.exp(-z))


def apply_calibrated_probability(family, p, recipe="drop_in", artifact=None):
    """
    Pure. Returns the calibrated YES probability (0-1) for `p` (0-1) in
    `family`, or `p` unchanged when the family has no map (never fabricates
    a map). `recipe` selects "drop_in" (apply to production's Poisson
    probability) or "structural" (apply to the NB/mean-shift re-priced
    probability -- caller must have re-priced first).
    """
    if p is None:
        return None
    art = artifact or load_artifact()
    fam = art["recipes"][recipe]["families"].get(family)
    if fam is None:
        fam = art["recipes"][recipe].get("global")
    if fam is None:
        return float(p)
    return _sigmoid(fam["a"] + fam["b"] * _logit(p))


def is_quarantined(family, artifact=None):
    art = artifact or load_artifact()
    return family in set(art.get("quarantine", {}).get("families", []))
