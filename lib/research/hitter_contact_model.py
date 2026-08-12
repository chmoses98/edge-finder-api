#!/usr/bin/env python3
"""
lib/research/hitter_contact_model.py
=======================================
Hitter Projection Engine -- Phase 4 contact model: a joint
EXIT VELOCITY x LAUNCH ANGLE x SPRAY ANGLE distribution for one hitter's
batted balls, and a documented, deterministic heuristic converting one
drawn contact event into a batted-ball outcome (out/1B/2B/3B/HR) using
park geometry, field-relative wind (down-weighted -- see below), defense,
and hitter speed.

NOT a trained/fit model. This mission's spec calls for "the simplest
defensible method" -- this is a documented physical heuristic (a single
calibration anchor: EV=105mph/LA=28deg carries ~420ft, a widely-cited
real benchmark), not a regression fit to labeled data (no labeled
batted-ball-outcome training pipeline exists in this repo or session).
Every constant below is named and commented so a future milestone can
replace this with a fit/trained model without changing the function
signatures the rest of the engine depends on.

EMPIRICAL vs PHYSICAL -- NOT DOUBLE-COUNTED
------------------------------------------------
This function uses ONLY physical geometry (lib.research.park_geometry)
and physical wind (lib.research.park_wind_derivation) to decide whether
a ball clears the wall. It NEVER applies the separate EMPIRICAL park
run-factor (lib.research.park_factor_derivation /
parkContext.empiricalFactors) -- that stays a separate, standalone
diagnostic signal callers may compare against post-hoc (e.g. in
lib.research.hitter_feature_ablation), never blended into this
physical conversion step. Per this mission's own instruction, this
avoids the two signals being blended into one number a future ablation
couldn't separate again.

WIND ORIENTATION IS UNVERIFIED -- DOWN-WEIGHTED
----------------------------------------------------
config/park_geometry.json's orientationDeg is marked
orientationConfidence="approximate_unverified" (PR #80). Per this
mission's explicit instruction ("do not give strong modeling weight to
directional park/wind information until its quality is verified"), the
wind's contribution to estimated carry distance is scaled by
WIND_ORIENTATION_CONFIDENCE_WEIGHT (0.3) -- a diagnostic-strength
nudge, not a full-strength physical effect.
"""

import math
import random
from typing import Optional

HARD_HIT_EV_THRESHOLD = 95.0

# Ground ball / line drive / fly ball / popup launch-angle boundaries --
# a simple, documented partition (not Statcast's own internal
# thresholds, which aren't publicly exact) consistent with
# hitter_pitch_derivation's existing SWEET_SPOT_LA_MIN/MAX=8/32 window.
GROUND_BALL_LA_MAX = 8.0
LINE_DRIVE_LA_MAX = 25.0
FLY_BALL_LA_MAX = 50.0

# Single calibration anchor: EV=105mph, LA=28deg carries ~420ft (a
# widely-cited real HR-distance benchmark) -- CARRY_K solved from
# distance = EV * sin(radians(2*LA)) * CARRY_K.
CARRY_K = 420.0 / (105.0 * math.sin(math.radians(2 * 28.0)))
OPTIMAL_LAUNCH_ANGLE_DEG = 28.0

# The raw undamped-projectile term sin(2*LA) alone peaks at LA=45deg and
# stays within ~15% of its max for the entire 25-50deg fly-ball range --
# it has no notion of air-resistance drag, which in real batted-ball
# flight disproportionately robs distance from high, "can-of-corn" fly
# balls relative to a ball hit at the game's real optimal ~25-30deg HR
# angle. Without this correction, the calibration anchor at LA=28 was
# observed to imply a ~50% HR rate on all fly balls in simulation (vs.
# a real MLB HR/FB rate of roughly 11-13%) because every fly ball above
# 28deg was treated as carrying nearly as far as the anchor. This
# exponential decay above the anchor angle is a second documented
# heuristic term (not part of the single calibration point itself,
# which stays exact at LA=28) tuned so simulated FB-HR rate lands in
# that realistic ~11-13% band -- see
# tests/test_hitter_contact_model.py for the calibration check.
LAUNCH_ANGLE_DRAG_DECAY_PER_DEGREE = 0.04

WIND_ORIENTATION_CONFIDENCE_WEIGHT = 0.3
WIND_FT_PER_MPH_COMPONENT = 2.5  # rough ft-of-carry per mph of wind component, before the confidence down-weight

GROUND_BALL_BASE_HIT_RATE = 0.24
LINE_DRIVE_BASE_HIT_RATE = 0.68
FLY_BALL_BASE_HIT_RATE = 0.14
POPUP_HIT_RATE = 0.02


def build_contact_pool(hitter_pitches) -> list:
    """
    Every archived batted ball for this hitter as
    (ev, la, sprayAnglePullSigned, battedBallType) tuples -- the
    empirical pool draw_contact_event() bootstraps from. Balls missing
    EV/LA/spray are excluded (never imputed).
    """
    from lib.research.hitter_pitch_derivation import _signed_pull_angle  # reuse, not duplicated

    pool = []
    for p in hitter_pitches:
        if p.get("pitchCallType") != "in_play":
            continue
        ev, la = p.get("launchSpeed"), p.get("launchAngle")
        if ev is None or la is None:
            continue
        angle = _signed_pull_angle(p)
        pool.append((ev, la, angle, p.get("battedBallType")))
    return pool


def _synthetic_league_draw(rng: random.Random):
    """
    A documented, ILLUSTRATIVE default contact draw used only when a
    hitter's own archive is too small to bootstrap from -- normal draws
    around roughly league-average EV/LA, clipped to plausible ranges.
    Explicitly NOT derived from real archived data (none exists in this
    environment) -- a future milestone should replace this with a real
    league-wide empirical pool once enough raw archive volume exists.
    """
    ev = max(40.0, min(120.0, rng.gauss(88.0, 15.0)))
    la = max(-40.0, min(70.0, rng.gauss(12.0, 20.0)))
    spray = max(-45.0, min(45.0, rng.gauss(0.0, 20.0)))
    return ev, la, spray, None


HITTER_POOL_SHRINKAGE_CONSTANT = 150.0  # pseudo-batted-balls of trust in the synthetic pool before the hitter's own archive dominates


def draw_contact_event(hitter_pool: list, rng: random.Random):
    """
    One (ev, la, sprayAnglePullSigned, battedBallType) draw -- bootstrap-
    resampled from `hitter_pool` with probability scaled by pool size
    (continuous shrinkage, same shrink_rate() spirit as
    lib.research.hitter_shrinkage, applied here to a resampling
    probability rather than a rate), else from the synthetic league
    default. A hitter with a large archive is drawn almost entirely from
    their own real batted balls; a hitter with none is drawn entirely
    from the synthetic default.
    """
    n = len(hitter_pool)
    hitter_weight = n / (n + HITTER_POOL_SHRINKAGE_CONSTANT) if n > 0 else 0.0
    if hitter_pool and rng.random() < hitter_weight:
        return rng.choice(hitter_pool)
    return _synthetic_league_draw(rng)


def classify_batted_ball_shape(la: float) -> str:
    if la <= GROUND_BALL_LA_MAX:
        return "ground_ball"
    if la <= LINE_DRIVE_LA_MAX:
        return "line_drive"
    if la <= FLY_BALL_LA_MAX:
        return "fly_ball"
    return "popup"


def estimate_carry_distance_ft(ev: float, la: float) -> float:
    """
    Physical heuristic only -- see module docstring's calibration-anchor
    note and LAUNCH_ANGLE_DRAG_DECAY_PER_DEGREE's comment above. Ground
    balls/popups carry ~0 by this formula (not meaningful for them
    anyway). Angles above OPTIMAL_LAUNCH_ANGLE_DEG get an exponential
    drag penalty so high fly balls don't over-carry relative to the
    single EV=105/LA=28 calibration point.
    """
    if la <= 0:
        return 0.0
    distance = ev * math.sin(math.radians(2 * min(la, 90))) * CARRY_K
    if la > OPTIMAL_LAUNCH_ANGLE_DEG:
        distance *= math.exp(-LAUNCH_ANGLE_DRAG_DECAY_PER_DEGREE * (la - OPTIMAL_LAUNCH_ANGLE_DEG))
    return max(0.0, distance)


def wall_distance_at_spray_angle(park_geometry_entry: dict, spray_angle_deg: float) -> Optional[float]:
    """
    Piecewise-linear interpolation of wall distance across
    foulLineLF(-45deg) -> powerAlleyLF(-22.5) -> centerField(0) ->
    powerAlleyRF(+22.5) -> foulLineRF(+45deg), using the pull-signed
    spray angle convention (positive=pull side toward the batter's own
    pull-side foul line, which lib.research.hitter_pitch_derivation
    labels "pull" -- see that module's _signed_pull_angle). Returns None
    if `park_geometry_entry` is falsy (no geometry resolved).
    """
    if not park_geometry_entry:
        return None
    points = [
        (-45.0, park_geometry_entry.get("foulLineLF")),
        (-22.5, park_geometry_entry.get("powerAlleyLF")),
        (0.0, park_geometry_entry.get("centerField")),
        (22.5, park_geometry_entry.get("powerAlleyRF")),
        (45.0, park_geometry_entry.get("foulLineRF")),
    ]
    angle = max(-45.0, min(45.0, spray_angle_deg))
    for i in range(len(points) - 1):
        x0, y0 = points[i]
        x1, y1 = points[i + 1]
        if x0 <= angle <= x1 and y0 is not None and y1 is not None:
            frac = (angle - x0) / (x1 - x0) if x1 != x0 else 0.0
            return y0 + frac * (y1 - y0)
    return park_geometry_entry.get("centerField")


def convert_contact_to_outcome(
    ev: float, la: float, spray_angle_deg: float, batter_hand: Optional[str],
    park_geometry_entry: Optional[dict], field_relative_wind: Optional[dict],
    defense_snapshot: Optional[dict], hitter_speed_snapshot: Optional[dict],
    rng: random.Random,
) -> dict:
    """
    Deterministic-given-rng conversion of one contact event into a
    batted-ball outcome. Returns {"outcome": "OUT"|"1B"|"2B"|"3B"|"HR",
    "shape", "estimatedDistanceFt", "wallDistanceFt", "windAdjustmentFt",
    "diagnostics": {...}}.
    """
    shape = classify_batted_ball_shape(la)

    if shape == "popup":
        outcome = "OUT" if rng.random() > POPUP_HIT_RATE else "1B"
        return {"outcome": outcome, "shape": shape, "estimatedDistanceFt": 0.0,
                "wallDistanceFt": None, "windAdjustmentFt": 0.0, "diagnostics": {}}

    if shape == "ground_ball":
        infield_oaa = (defense_snapshot or {}).get("infieldOAA")
        speed = (hitter_speed_snapshot or {}).get("sprintSpeedFtPerSec")
        hit_rate = GROUND_BALL_BASE_HIT_RATE
        hit_rate += max(-0.05, min(0.05, (ev - 88.0) / 300.0))  # harder grounders sneak through more
        if infield_oaa is not None:
            hit_rate -= max(-0.05, min(0.05, infield_oaa / 200.0))  # better infield defense suppresses hits
        if speed is not None:
            hit_rate += max(0.0, min(0.03, (speed - 27.0) / 300.0))  # faster hitters beat out more infield hits
        hit_rate = max(0.05, min(0.45, hit_rate))
        outcome = "1B" if rng.random() < hit_rate else "OUT"
        return {"outcome": outcome, "shape": shape, "estimatedDistanceFt": 0.0,
                "wallDistanceFt": None, "windAdjustmentFt": 0.0,
                "diagnostics": {"hitRateUsed": round(hit_rate, 3)}}

    # line_drive / fly_ball
    raw_distance = estimate_carry_distance_ft(ev, la)
    wind_component_ft = 0.0
    if field_relative_wind and field_relative_wind.get("status") == "AVAILABLE":
        wind_component_ft = (field_relative_wind.get("componentTowardCF") or 0.0) * WIND_FT_PER_MPH_COMPONENT * WIND_ORIENTATION_CONFIDENCE_WEIGHT
    distance = raw_distance + wind_component_ft
    wall_distance = wall_distance_at_spray_angle(park_geometry_entry, spray_angle_deg)

    if wall_distance is not None and distance >= wall_distance and shape == "fly_ball":
        return {"outcome": "HR", "shape": shape, "estimatedDistanceFt": round(distance, 1),
                "wallDistanceFt": wall_distance, "windAdjustmentFt": round(wind_component_ft, 1), "diagnostics": {}}

    base_hit_rate = LINE_DRIVE_BASE_HIT_RATE if shape == "line_drive" else FLY_BALL_BASE_HIT_RATE
    outfield_oaa = (defense_snapshot or {}).get("outfieldOAA")
    hit_rate = base_hit_rate
    if outfield_oaa is not None:
        hit_rate -= max(-0.08, min(0.08, outfield_oaa / 150.0))
    hit_rate = max(0.02, min(0.95, hit_rate))

    if rng.random() >= hit_rate:
        return {"outcome": "OUT", "shape": shape, "estimatedDistanceFt": round(distance, 1),
                "wallDistanceFt": wall_distance, "windAdjustmentFt": round(wind_component_ft, 1),
                "diagnostics": {"hitRateUsed": round(hit_rate, 3)}}

    # A hit that isn't a HR -- extra-base chance grows with distance and hitter speed.
    speed = (hitter_speed_snapshot or {}).get("sprintSpeedFtPerSec") or 27.0
    speed_bonus = max(0.0, (speed - 27.0) / 10.0) * 0.05
    if distance >= 330:
        outcome = "3B" if rng.random() < (0.06 + speed_bonus) else "2B"
    elif distance >= 250:
        outcome = "2B" if rng.random() < (0.35 + speed_bonus) else "1B"
    else:
        outcome = "1B"
    return {"outcome": outcome, "shape": shape, "estimatedDistanceFt": round(distance, 1),
            "wallDistanceFt": wall_distance, "windAdjustmentFt": round(wind_component_ft, 1),
            "diagnostics": {"hitRateUsed": round(hit_rate, 3)}}
