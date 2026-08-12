#!/usr/bin/env python3
"""
lib/research/hitter_synthetic_ground_truth.py
=================================================
Hitter Projection Engine -- Phase 4 shared synthetic-data generator for
lib.research.hitter_validation and lib.research.hitter_feature_ablation.

WHY SYNTHETIC, NOT REAL, GROUND TRUTH: this sandbox has no raw
Statcast pitch archive (data/statcast_raw has never been populated
here -- no live ingestion has ever run in this environment) and no
point-in-time hitter_feature_context snapshots predating PR #77's
confirmedLineup capture. A genuine leakage-free walk-forward backtest
therefore cannot be built from real data in this repository today (see
docs/HITTER_SIMULATION_ENGINE.md's Validation section for the full
caveat). What CAN be built honestly is a validation harness against a
KNOWN, CONTROLLED ground-truth outcome-generating process -- this
module generates synthetic pitch-level PA-terminal events from an
explicit true-rate dict the caller supplies, so lib.research.
hitter_validation/hitter_feature_ablation can measure whether this
engine's shrinkage/adjustment machinery actually recovers the known
truth, rather than merely asserting it does.

Every synthetic pitch produced here uses the EXACT same field schema
lib.research.hitter_pitch_derivation expects (see that module's own
test fixtures, tests/test_hitter_pitch_derivation.py's `_pitch()`) --
this is not a second parallel schema.
"""
import random
from typing import Optional

_EVENT_FOR_OUTCOME = {
    "1B": "single", "2B": "double", "3B": "triple", "HR": "home_run",
    "BB": "walk", "HBP": "hit_by_pitch", "K": "strikeout", "OUT": "field_out",
}
_OUTCOME_ORDER = ("K", "BB", "HBP", "1B", "2B", "3B", "HR", "OUT")


def draw_synthetic_pa_outcome(true_rates: dict, rng: random.Random) -> str:
    weights = [max(0.0, true_rates.get(k, 0.0)) for k in _OUTCOME_ORDER]
    return rng.choices(_OUTCOME_ORDER, weights=weights, k=1)[0]


def _date_str(day_index: int, start_year: int = 2026) -> str:
    # Simple, dependency-free day-of-year -> "YYYY-MM-DD" spread across a synthetic season (April-September).
    import datetime
    base = datetime.date(start_year, 4, 1)
    return (base + datetime.timedelta(days=day_index)).isoformat()


def generate_synthetic_pitches(true_rates: dict, n_pa: int, rng: random.Random,
                                pitch_family: str = "four_seam", batter_hand: str = "R",
                                start_day_index: int = 0) -> list:
    """
    Returns a list of `n_pa` synthetic terminal pitches, one per PA,
    dated sequentially (one PA per day starting at `start_day_index`)
    so as-of/window filtering has real dates to filter on. Every pitch
    carries `events` set to the real MLB Stats API event string
    lib.research.hitter_pitch_derivation._EVENT_MAP already recognizes
    -- never a synthetic/fabricated event vocabulary.
    """
    pitches = []
    for i in range(n_pa):
        outcome = draw_synthetic_pa_outcome(true_rates, rng)
        pitches.append({
            "gameDate": _date_str(start_day_index + i),
            "batterId": "synthetic", "batterHand": batter_hand,
            "pitchType": {"four_seam": "FF", "slider": "SL", "sinker": "SI",
                          "changeup": "CH", "curve": "CU"}.get(pitch_family, "FF"),
            "pitchName": pitch_family, "releaseSpeed": 93.0,
            "balls": 0, "strikes": 0, "plateX": 0.0, "plateZ": 2.5, "szTop": 3.5, "szBot": 1.5,
            "pitchCallType": "in_play" if outcome not in ("BB", "HBP", "K") else
                             ("ball" if outcome == "BB" else "hit_by_pitch" if outcome == "HBP" else "swinging_strike"),
            "events": _EVENT_FOR_OUTCOME[outcome],
            "launchSpeed": 90.0 if outcome in ("1B", "2B", "3B", "HR") else None,
            "launchAngle": 15.0 if outcome in ("1B", "2B", "3B", "HR") else None,
            "hitCoordX": None, "hitCoordY": None, "battedBallType": None,
            "estimatedBA": None, "estimatedWOBA": None,
        })
    return pitches


def perturb_league_rates(league_rates: dict, rng: random.Random, spread: float = 0.4) -> dict:
    """
    A synthetic hitter's own "true talent" -- multiplies each
    hit/BB/K component of `league_rates` by an independent
    (1 +/- spread) factor, then renormalizes so the result still sums
    to 1.0. Used to give lib.research.hitter_validation's calibration
    table a real spread of talent levels across many synthetic
    hitters (a single fixed true-rate hitter would only ever probe one
    point on the calibration curve).
    """
    adjusted = {k: max(0.0001, v * (1.0 + rng.uniform(-spread, spread))) for k, v in league_rates.items()}
    total = sum(adjusted.values())
    return {k: v / total for k, v in adjusted.items()}
