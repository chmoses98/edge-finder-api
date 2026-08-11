#!/usr/bin/env python3
"""
lib/research/park_factor_derivation.py
=========================================
Hitter Projection Engine -- Phase 3 EMPIRICAL park factors.

Deliberately separate from lib.research.park_geometry (PHYSICAL wall
distances/heights/orientation) -- see that module's own docstring for
why. This module only ever produces a factor from real observed
outcomes; it never looks at a wall distance. A caller that wants both
combines them explicitly (e.g. lib.research.hitter_feature_context's
parkContext exposes `geometry` and `empiricalFactors` as two clearly
separate sub-blocks) rather than either module silently blending the two
signals into one number a future ablation study couldn't separate again.

WHY THIS RETURNS NOT_COMPUTED TODAY
--------------------------------------
This repo's only existing empirical park signal is the single overall
run-scoring index already in api/slate.js's PARK_WEATHER (reused as-is
by lib.research.hitter_feature_context._park_context, unchanged from
Phase 1/2). No event-specific (1B/2B/3B/HR) or handedness-specific
empirical factor has ever been computed anywhere in this repo -- doing
so for real requires many games' worth of raw pitch archive per park
(lib.research.statcast_pitch_store), which does not exist yet in this
environment (no live ingestion has been run here -- see PR #79/#80's
own storage/performance notes). derive_empirical_park_factors() below
is real, working, pure code -- it activates automatically the moment a
caller has enough archived games to feed it; it is not itself the
reason this stays NOT_COMPUTED in the live schema today.
"""

from typing import Optional

# Same well-known, unambiguous events derive_baseline_talent_window()
# already classifies -- reused here rather than re-declared with a
# different spelling. Kept small and local (not importing
# hitter_pitch_derivation's private _EVENT_MAP) since this module only
# needs the PA-terminal-event distinction, not full counting-stat logic.
_HIT_EVENT_TO_BASES = {"single": 1, "double": 2, "triple": 3, "home_run": 4}
_PA_TERMINAL_EVENTS = {
    "single", "double", "triple", "home_run", "walk", "intent_walk", "hit_by_pitch",
    "strikeout", "strikeout_double_play", "field_out", "force_out",
    "grounded_into_double_play", "double_play", "triple_play",
    "fielders_choice", "fielders_choice_out", "field_error",
    "sac_fly", "sac_fly_double_play", "sac_bunt", "sac_bunt_double_play",
}

MIN_PARK_PA_FOR_FACTOR = 500  # conservative -- a single small sample must not produce a confident-looking index


def _index(rate, league_rate):
    if league_rate is None or league_rate == 0 or rate is None:
        return None
    return round(100.0 * rate / league_rate, 1)


def derive_empirical_park_factors(pitches, game_park_map: dict, batter_hand_filter: Optional[str] = None) -> dict:
    """
    pitches: raw pitch records (see lib.research.hitter_pitch_derivation's
      module docstring for the schema) spanning many games/parks.
    game_park_map: {gamePk: teamAbbr} -- the HOME team for each game
      (the park a game was played at), supplied by the caller (this
      module does no I/O and does not know which team is home for a
      given gamePk on its own).
    batter_hand_filter: optional 'L'/'R' -- when given, only PAs by
      batters of that hand are counted (for a handedness-specific factor).

    Returns {teamAbbr: {sampleSize, singleFactor, doubleFactor,
    tripleFactor, hrFactor, runFactor}} -- 100 = league average, same
    index convention as api/slate.js's existing parkFactor. A park with
    fewer than MIN_PARK_PA_FOR_FACTOR qualifying PAs is omitted entirely
    (never returns a confident-looking index off a tiny sample).
    """
    by_park = {}
    league = {"pa": 0, "1B": 0, "2B": 0, "3B": 0, "HR": 0, "bases": 0}

    for p in pitches:
        event = p.get("events")
        if event not in _PA_TERMINAL_EVENTS:
            continue
        if batter_hand_filter and p.get("batterHand") != batter_hand_filter:
            continue
        park = game_park_map.get(p.get("gamePk"))
        if park is None:
            continue
        bucket = by_park.setdefault(park, {"pa": 0, "1B": 0, "2B": 0, "3B": 0, "HR": 0, "bases": 0})
        bucket["pa"] += 1
        league["pa"] += 1
        if event in _HIT_EVENT_TO_BASES:
            bases = _HIT_EVENT_TO_BASES[event]
            key = {1: "1B", 2: "2B", 3: "3B", 4: "HR"}[bases]
            bucket[key] += 1
            bucket["bases"] += bases
            league[key] += 1
            league["bases"] += bases

    if league["pa"] == 0:
        return {}

    league_rates = {k: league[k] / league["pa"] for k in ("1B", "2B", "3B", "HR", "bases")}

    result = {}
    for park, bucket in by_park.items():
        if bucket["pa"] < MIN_PARK_PA_FOR_FACTOR:
            continue
        park_rates = {k: bucket[k] / bucket["pa"] for k in ("1B", "2B", "3B", "HR", "bases")}
        result[park] = {
            "sampleSize": bucket["pa"],
            "singleFactor": _index(park_rates["1B"], league_rates["1B"]),
            "doubleFactor": _index(park_rates["2B"], league_rates["2B"]),
            "tripleFactor": _index(park_rates["3B"], league_rates["3B"]),
            "hrFactor": _index(park_rates["HR"], league_rates["HR"]),
            "runFactor": _index(park_rates["bases"], league_rates["bases"]),
        }
    return result
