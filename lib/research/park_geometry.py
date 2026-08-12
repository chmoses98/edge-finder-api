#!/usr/bin/env python3
"""
lib/research/park_geometry.py
================================
Hitter Projection Engine -- Phase 3 canonical park-geometry reference.

Loads config/park_geometry.json (see that file's own "note" field for
full provenance/confidence caveats) -- static, versioned reference data,
never refetched per slate (there is no live source for stadium
dimensions; this is stable, publicly documented fact, same category as
api/slate.js's PARK_WEATHER team/name mapping).

EMPIRICAL vs PHYSICAL -- KEPT SEPARATE ON PURPOSE
----------------------------------------------------
This module returns ONLY physical geometry (distances, wall heights,
altitude, roof type, field orientation). It has no opinion about how
many runs/HRs/hits a park actually produces -- that is
lib.research.park_factor_derivation's job (the EMPIRICAL side, derived
from real outcomes). A caller that wants both combines them explicitly;
neither module imports the other, so double-counting the same signal
under two names is a caller-level mistake this design makes visible,
not something baked into either module.

VERSIONING
------------
Each team maps to a LIST of dated entries (sorted by effectiveFrom in
the source file); resolve_park_geometry(team, as_of) returns the latest
entry whose effectiveFrom <= as_of (falls back to the earliest entry if
as_of predates every entry, since that's still the best available
representation for an early date rather than returning nothing). Today
every team has exactly one entry -- the list shape exists so a future
dimension change can be added as a second entry with no code change.
"""

import json
import os
from typing import Optional

# Resolved relative to this module's own file location (repo_root/lib/research/
# -> repo_root/config/park_geometry.json), NOT the caller's current working
# directory -- this is static, versioned reference data shipped with the
# code (like a constants file), not runtime data, so it must resolve the
# same way regardless of what directory a script or test happens to be
# running from (unlike data/slate.json etc., which are genuinely CWD-relative
# per-run state).
_MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(os.path.dirname(_MODULE_DIR))
PARK_GEOMETRY_PATH = os.path.join(_REPO_ROOT, "config", "park_geometry.json")

_cache = None


def _load_raw():
    global _cache
    if _cache is None:
        with open(PARK_GEOMETRY_PATH) as f:
            _cache = json.load(f)
    return _cache


def resolve_park_geometry(team_abbr: str, as_of: Optional[str] = None) -> Optional[dict]:
    """
    Returns the geometry entry for `team_abbr` effective as of `as_of`
    (ISO 'YYYY-MM-DD'; None means "latest"), or None if the team isn't
    in the reference table. Never fabricates a value for an unknown team.
    """
    doc = _load_raw()
    versions = doc.get("parks", {}).get(team_abbr)
    if not versions:
        return None
    if as_of is None:
        return versions[-1]
    eligible = [v for v in versions if v.get("effectiveFrom", "") <= as_of]
    return eligible[-1] if eligible else versions[0]


def field_relative_direction(bearing_from_home_plate_deg: float, orientation_deg: float) -> str:
    """
    Classify a compass bearing (e.g. wind direction, or a spray angle
    already converted to compass degrees) relative to this park's own
    center-field orientation, into 'toward_plate' / 'toward_cf' /
    'toward_lf' / 'toward_rf' quadrants. Both inputs are plain compass
    degrees (0=N/90=E/180=S/270=W) -- this function does no unit
    conversion of its own.
    """
    relative = (bearing_from_home_plate_deg - orientation_deg) % 360
    if relative <= 45 or relative >= 315:
        return "toward_cf"
    if 45 < relative < 135:
        return "toward_rf"
    if 225 <= relative < 315:
        return "toward_lf"
    return "toward_plate"
