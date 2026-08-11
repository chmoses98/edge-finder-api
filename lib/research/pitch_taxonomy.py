#!/usr/bin/env python3
"""
lib/research/pitch_taxonomy.py
=================================
Hitter Projection Engine -- Phase 2 canonical pitch representation.

Pure classification/representation helpers for a single raw Statcast
pitch record (see lib.research.statcast_pitch_store's module docstring
for the raw record schema these operate on). No I/O, no network, no
clock reads, no mutation of any argument -- every function here is a
deterministic function of its inputs, safe to call from ingestion,
derivation, tests, or a future replay harness alike.

This module intentionally stops at REPRESENTATION, not modeling:
- classify_pitch_family() groups raw Statcast pitch_type codes into the
  families a hitter model will eventually condition on -- it does not
  compute any performance metric.
- velocity_bucket() only ever buckets a fastball-family pitch; it
  returns None for anything else, on purpose, so a caller can never
  accidentally average an 87mph slider into "<93" alongside genuine
  87mph fastballs (see this module's own docstring section on this).
- build_pitch_shape_profile() returns a stable, hashable representation
  of one pitch's shape -- it does not cluster or compare pitches
  against each other. That similarity/nearest-neighbor step is
  explicitly future work (see docs/HITTER_STATCAST_FOUNDATION.md).
- classify_zone()/spatial_grid_bin() bin a single continuous (plate_x,
  plate_z) coordinate -- they never discard the original coordinate;
  callers that need the raw location still have it on the pitch record.
- classify_count_state() groups a single (balls, strikes) pair -- it
  does not sequence across pitches within a PA (that is future
  pitch-sequence modeling, explicitly out of scope here).
"""

from typing import Optional

# ── Pitch-type family classification ────────────────────────────────────
# Maps Statcast's `pitch_type` two-letter code (the stable, documented
# Statcast taxonomy) to the family names this Phase 2 mission's spec
# requires support for. `pitch_name` (Statcast's own human-readable
# label, e.g. "4-Seam Fastball") is accepted as a fallback key so a
# caller who only has the descriptive name (not the code) still
# resolves correctly.
PITCH_FAMILY_FOUR_SEAM = "four_seam"
PITCH_FAMILY_SINKER = "sinker"
PITCH_FAMILY_CUTTER = "cutter"
PITCH_FAMILY_SLIDER = "slider"
PITCH_FAMILY_SWEEPER = "sweeper"
PITCH_FAMILY_CURVE = "curve"
PITCH_FAMILY_KNUCKLE_CURVE = "knuckle_curve"
PITCH_FAMILY_CHANGEUP = "changeup"
PITCH_FAMILY_SPLITTER = "splitter"
PITCH_FAMILY_OTHER = "other"

_CODE_TO_FAMILY = {
    "FF": PITCH_FAMILY_FOUR_SEAM,
    "SI": PITCH_FAMILY_SINKER, "FT": PITCH_FAMILY_SINKER,
    "FC": PITCH_FAMILY_CUTTER,
    "SL": PITCH_FAMILY_SLIDER,
    "ST": PITCH_FAMILY_SWEEPER, "SV": PITCH_FAMILY_SWEEPER,  # "sweeper"/"slurve" Statcast codes
    "CU": PITCH_FAMILY_CURVE, "CS": PITCH_FAMILY_CURVE,  # CS = slow curve
    "KC": PITCH_FAMILY_KNUCKLE_CURVE,
    "CH": PITCH_FAMILY_CHANGEUP,
    "FS": PITCH_FAMILY_SPLITTER, "FO": PITCH_FAMILY_SPLITTER,  # FO = forkball, grouped with splitter
    "KN": PITCH_FAMILY_OTHER,  # true knuckleball -- distinct mechanism, not modeled as its own family yet
    "EP": PITCH_FAMILY_OTHER,  # eephus
    "SC": PITCH_FAMILY_OTHER,  # screwball
    "PO": PITCH_FAMILY_OTHER,  # pitchout -- not a competitive pitch
    "IN": PITCH_FAMILY_OTHER,  # intentional ball
    "UN": PITCH_FAMILY_OTHER,
}

_NAME_TO_FAMILY = {
    "4-seam fastball": PITCH_FAMILY_FOUR_SEAM,
    "sinker": PITCH_FAMILY_SINKER,
    "cutter": PITCH_FAMILY_CUTTER,
    "slider": PITCH_FAMILY_SLIDER,
    "sweeper": PITCH_FAMILY_SWEEPER, "slurve": PITCH_FAMILY_SWEEPER,
    "curveball": PITCH_FAMILY_CURVE, "slow curve": PITCH_FAMILY_CURVE,
    "knuckle curve": PITCH_FAMILY_KNUCKLE_CURVE,
    "changeup": PITCH_FAMILY_CHANGEUP,
    "split-finger": PITCH_FAMILY_SPLITTER, "forkball": PITCH_FAMILY_SPLITTER,
    "knuckleball": PITCH_FAMILY_OTHER,
    "eephus": PITCH_FAMILY_OTHER,
    "screwball": PITCH_FAMILY_OTHER,
}

# Only these families are a fastball-velocity-bucket-eligible group --
# see velocity_bucket()'s own docstring for why offspeed/breaking pitches
# never get bucketed by the same <93/93-95/... scale.
FASTBALL_FAMILIES = frozenset({PITCH_FAMILY_FOUR_SEAM, PITCH_FAMILY_SINKER, PITCH_FAMILY_CUTTER})

VELOCITY_BUCKETS = ("<93", "93-95", "95-97", "97-99", "99+")


def classify_pitch_family(pitch_type: Optional[str] = None, pitch_name: Optional[str] = None) -> str:
    """
    Resolve a raw Statcast pitch_type code (preferred) or pitch_name
    (fallback) to one of this mission's canonical families. Never
    raises -- an unrecognized/missing code resolves to "other" rather
    than None, so every raw pitch is always groupable.
    """
    if pitch_type:
        family = _CODE_TO_FAMILY.get(str(pitch_type).strip().upper())
        if family:
            return family
    if pitch_name:
        family = _NAME_TO_FAMILY.get(str(pitch_name).strip().lower())
        if family:
            return family
    return PITCH_FAMILY_OTHER


def velocity_bucket(pitch_family: str, release_speed) -> Optional[str]:
    """
    Bucket a pitch's release_speed into one of VELOCITY_BUCKETS --
    ONLY for pitch_family in FASTBALL_FAMILIES (four-seam/sinker/cutter).
    Returns None for every other family and for a missing/non-numeric
    release_speed, on purpose: an 87mph slider and an 87mph fastball are
    mechanically and outcome-wise unrelated pitches that happen to share
    a radar-gun number, and this mission's spec explicitly forbids
    mixing them into the same bucket. A caller building a velocity-based
    hitter response profile for breaking/offspeed pitches must key on
    (pitch_family, release_speed) directly rather than this bucket.
    """
    if pitch_family not in FASTBALL_FAMILIES:
        return None
    try:
        speed = float(release_speed)
    except (TypeError, ValueError):
        return None
    if speed < 93:
        return "<93"
    if speed < 95:
        return "93-95"
    if speed < 97:
        return "95-97"
    if speed < 99:
        return "97-99"
    return "99+"


# ── Pitch-shape profile ─────────────────────────────────────────────────
_SHAPE_FIELDS = (
    "pitchFamily", "releaseSpeed", "inducedVertBreak", "horizontalBreak",
    "spinRate", "releaseHeight", "releaseSide", "extension", "armAngle",
)


def build_pitch_shape_profile(pitch: dict) -> dict:
    """
    Stable, deterministic shape representation of one raw pitch record
    -- every field either the raw Statcast value or None (never
    fabricated). This is REPRESENTATION only: it does not cluster, does
    not compute similarity, does not rank pitches against each other.
    Two calls with equal input always produce an equal (and
    dict-equality-comparable) output.
    """
    family = classify_pitch_family(pitch.get("pitchType"), pitch.get("pitchName"))
    return {
        "pitchFamily": family,
        "releaseSpeed": pitch.get("releaseSpeed"),
        "inducedVertBreak": pitch.get("inducedVertBreak"),
        "horizontalBreak": pitch.get("horizontalBreak"),
        "spinRate": pitch.get("spinRate"),
        "releaseHeight": pitch.get("releaseHeight"),
        "releaseSide": pitch.get("releaseSide"),
        "extension": pitch.get("extension"),
        "armAngle": pitch.get("armAngle"),
    }


# ── Spatial location ────────────────────────────────────────────────────
# Savant's own "Heart/Shadow/Chase/Waste" grouping (Attack Zones), keyed
# off plate_x/plate_z relative to a batter's own sz_top/sz_bot when
# available, else a league-average strike zone. This module never
# discards the underlying continuous plate_x/plate_z -- classify_zone()
# and spatial_grid_bin() are additive derived views, not replacements.
LEAGUE_AVG_SZ_TOP = 3.5
LEAGUE_AVG_SZ_BOT = 1.5
ZONE_HEART = "Heart"
ZONE_SHADOW = "Shadow"
ZONE_CHASE = "Chase"
ZONE_WASTE = "Waste"


def classify_zone(plate_x, plate_z, sz_top=None, sz_bot=None) -> Optional[str]:
    """
    Savant-style Heart/Shadow/Chase/Waste classification. Returns None
    if plate_x/plate_z is missing (never guesses a zone for a pitch
    whose location wasn't tracked). sz_top/sz_bot default to
    league-average when this specific batter's own zone isn't supplied
    -- callers that have the batter's real zone should pass it for a
    more accurate read.
    """
    if plate_x is None or plate_z is None:
        return None
    try:
        x, z = float(plate_x), float(plate_z)
    except (TypeError, ValueError):
        return None
    top = float(sz_top) if sz_top is not None else LEAGUE_AVG_SZ_TOP
    bot = float(sz_bot) if sz_bot is not None else LEAGUE_AVG_SZ_BOT
    zone_height = top - bot
    zone_center_z = (top + bot) / 2.0
    # Normalize to zone-widths: half-width of the real zone is 0.83ft
    # (17in plate / 2 / 12), used as the unit for Heart/Shadow/Chase
    # bands, matching Savant's own attack-zone definition.
    half_width = 0.83
    nx = x / half_width
    nz = (z - zone_center_z) / (zone_height / 2.0) if zone_height else 0.0
    dist = max(abs(nx), abs(nz))
    if dist <= 0.67:
        return ZONE_HEART
    if dist <= 1.1:
        return ZONE_SHADOW
    if dist <= 1.6:
        return ZONE_CHASE
    return ZONE_WASTE


def spatial_grid_bin(plate_x, plate_z, grid_size: float = 0.5):
    """
    Finer, non-hardcoded spatial bin: snaps (plate_x, plate_z) to a
    grid_size-foot grid, returned as an (col, row) integer tuple. This
    is deliberately NOT limited to the traditional nine strike-zone
    boxes -- grid_size is caller-controlled so a future heat-map model
    can request arbitrarily fine resolution. Returns None if the
    coordinate is missing.
    """
    if plate_x is None or plate_z is None:
        return None
    try:
        x, z = float(plate_x), float(plate_z)
    except (TypeError, ValueError):
        return None
    if grid_size <= 0:
        raise ValueError("grid_size must be positive")
    return (int(round(x / grid_size)), int(round(z / grid_size)))


# ── Count state ──────────────────────────────────────────────────────────
def classify_count_state(balls, strikes) -> dict:
    """
    Group a single (balls, strikes) pair into the count-state buckets
    this mission's spec requires reusable helpers for. Returns a dict
    (not a single label) since a count can belong to more than one
    meaningful bucket at once (e.g. 3-2 is both "twoStrikes" and
    "threeBallCount"). balls/strikes outside the normal 0-3/0-2 MLB
    range (e.g. a foul-extended at-bat is still capped at 2 strikes by
    rule) are preserved as-is and simply won't match any named bucket
    beyond exactCount.
    """
    b = balls if isinstance(balls, int) else None
    s = strikes if isinstance(strikes, int) else None
    exact = f"{b}-{s}" if b is not None and s is not None else None
    return {
        "exactCount": exact,
        "balls": b,
        "strikes": s,
        "isEven": (b == s) if (b is not None and s is not None) else None,
        "hitterAhead": (b is not None and s is not None and b > s),
        "pitcherAhead": (b is not None and s is not None and s > b),
        "twoStrikes": (s is not None and s >= 2),
        "threeBallCount": (b is not None and b >= 3),
        "isFirstPitch": (b == 0 and s == 0) if (b is not None and s is not None) else None,
        "is02": (b == 0 and s == 2),
        "is12": (b == 1 and s == 2),
    }
