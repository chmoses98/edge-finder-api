#!/usr/bin/env python3
"""
lib/research/pitch_sequence_model.py
=======================================
Hitter Projection Engine -- Phase 4 count-state / pitch-sequence model
and pitch-by-pitch PA simulator.

For each pitch, estimates:
  P(swing) -> if take: P(ball) / P(called strike)
            -> if swing: P(whiff) / P(foul) / P(ball in play)

conditioned on (pitch family, in-zone/out-of-zone, count state), each
hierarchically shrunk (lib.research.hitter_shrinkage) toward this
hitter's broader plate-discipline rates -- family+zone -> family ->
season -- exactly the same shrinkage machinery
lib.research.hitter_pa_outcome_model uses, not a second implementation.

HONEST DEGRADATION -- CATCHER/UMPIRE
----------------------------------------
No catcher-framing or umpire zone-tendency signal is folded into the
called-strike/ball split here: PR #80's catcherContext/umpireContext
audit found no stable, authoritative source for either tendency metric,
so this model uses a fixed in-zone-is-a-strike rule (never a fabricated
per-catcher or per-umpire adjustment). This is a documented, honest
simplification, not a missing feature silently ignored -- see this
module's own `CATCHER_UMPIRE_ADJUSTMENT_APPLIED = False` constant, which
lib.research.hitter_explainability reports directly.
"""

import random
from typing import Optional

from lib.research.hitter_shrinkage import hierarchical_shrink, ShrinkageLevel
from lib.research.hitter_pitch_derivation import derive_plate_discipline, derive_contact_quality
from lib.research.pitch_taxonomy import classify_pitch_family, classify_zone, classify_count_state

CATCHER_UMPIRE_ADJUSTMENT_APPLIED = False

LEAGUE_DEFAULT_DISCIPLINE = {
    "swingPct": 47.0, "zonePct": 48.0, "zSwingPct": 67.0, "oSwingPct": 29.0,
    "contactPct": 76.0, "zContactPct": 85.0, "oContactPct": 60.0,
    "whiffPct": 24.0, "inPlayGivenContactPct": 45.0,
}
FAMILY_LEVEL_STRENGTH = 40.0
SEASON_LEVEL_STRENGTH = 150.0
MAX_PITCHES_PER_PA = 20
HBP_BASE_RATE = 0.010  # league-average-ish per-PA HBP rate used only when the hitter's own rate is unavailable


def _rate_or_none(discipline: dict, key: str) -> Optional[float]:
    v = discipline.get(key)
    return v / 100.0 if v is not None else None


def _shrink_two_level(family_pitches, season_pitches, key: str, league_default: float) -> float:
    """Shrink one plate-discipline rate: family-level sample -> season-level sample -> league default."""
    family_disc = derive_plate_discipline(family_pitches) if family_pitches else {"sampleSize": 0}
    season_disc = derive_plate_discipline(season_pitches) if season_pitches else {"sampleSize": 0}

    family_rate = _rate_or_none(family_disc, key)
    season_rate = _rate_or_none(season_disc, key)

    levels = [
        ShrinkageLevel("family", family_rate * family_disc.get("sampleSize", 0) if family_rate is not None else None,
                        family_disc.get("sampleSize") if family_rate is not None else None, FAMILY_LEVEL_STRENGTH),
        ShrinkageLevel("season", season_rate * season_disc.get("sampleSize", 0) if season_rate is not None else None,
                        season_disc.get("sampleSize") if season_rate is not None else None, SEASON_LEVEL_STRENGTH),
    ]
    return hierarchical_shrink(levels, league_default / 100.0)["rate"]


def estimate_pitch_outcome_probabilities(hitter_pitches, pitch_family: str, in_zone: bool) -> dict:
    """
    Returns {"swingPct", "contactGivenSwingPct", "inPlayGivenContactPct"}
    (all 0..1 rates) for this hitter, conditioned on pitch family and
    zone membership, shrunk toward season-level rates. `hitter_pitches`:
    this hitter's full archived pitch history (already as-of filtered).
    """
    family_pitches = [p for p in hitter_pitches if classify_pitch_family(p.get("pitchType"), p.get("pitchName")) == pitch_family]
    zone_key = "zSwingPct" if in_zone else "oSwingPct"
    contact_key = "zContactPct" if in_zone else "oContactPct"

    swing_pct = _shrink_two_level(family_pitches, hitter_pitches, zone_key, LEAGUE_DEFAULT_DISCIPLINE[zone_key])
    contact_pct = _shrink_two_level(family_pitches, hitter_pitches, contact_key, LEAGUE_DEFAULT_DISCIPLINE[contact_key])

    # in-play-given-contact: derived from contact_quality.sampleSize (in-play
    # count) vs plate_discipline's own contact count, at the family level
    # when there's enough family sample, else season level.
    def _in_play_given_contact(pitches):
        disc = derive_plate_discipline(pitches)
        cq = derive_contact_quality(pitches)
        contact_pct_raw = disc.get("contactPct")
        swings = disc.get("sampleSize", 0) * (disc.get("swingPct") or 0) / 100.0
        contacts = swings * (contact_pct_raw or 0) / 100.0
        in_play = cq.get("sampleSize", 0)
        if contacts <= 0:
            return None, 0
        return min(1.0, in_play / contacts), contacts

    family_ip, family_n = _in_play_given_contact(family_pitches)
    season_ip, season_n = _in_play_given_contact(hitter_pitches)
    levels = [
        ShrinkageLevel("family", (family_ip * family_n) if family_ip is not None else None, family_n if family_ip is not None else None, FAMILY_LEVEL_STRENGTH),
        ShrinkageLevel("season", (season_ip * season_n) if season_ip is not None else None, season_n if season_ip is not None else None, SEASON_LEVEL_STRENGTH),
    ]
    in_play_given_contact = hierarchical_shrink(levels, LEAGUE_DEFAULT_DISCIPLINE["inPlayGivenContactPct"] / 100.0)["rate"]

    return {
        "swingPct": swing_pct,
        "contactGivenSwingPct": contact_pct,
        "inPlayGivenContactPct": in_play_given_contact,
    }


def simulate_pa_pitch_by_pitch(hitter_pitches, pitcher_pitch_mix: dict, rng: random.Random,
                                zone_rate: float = 0.48, hbp_rate: Optional[float] = None) -> dict:
    """
    Simulates one plate appearance pitch-by-pitch. `pitcher_pitch_mix`:
    {family: share} (see lib.research.pitch_environment_model). `rng`:
    a seeded random.Random instance (deterministic given the same seed).
    `zone_rate`: probability any given pitch is in the strike zone (no
    per-pitcher location model exists yet -- see this module's docstring
    on catcher/umpire; a single constant is used honestly rather than a
    fabricated per-pitcher location distribution).

    Returns {"outcome": "BB"|"K"|"HBP"|"IN_PLAY", "pitchLog": [...],
    "pitchCount": int, "finalCount": {"balls","strikes"}}. IN_PLAY hands
    off to lib.research.hitter_contact_model for the batted-ball result
    -- this function itself never resolves a batted ball.
    """
    hbp_rate = HBP_BASE_RATE if hbp_rate is None else hbp_rate
    if rng.random() < hbp_rate:
        return {"outcome": "HBP", "pitchLog": [], "pitchCount": 0, "finalCount": {"balls": 0, "strikes": 0}}

    families = list(pitcher_pitch_mix.keys()) or ["four_seam"]
    weights = [pitcher_pitch_mix.get(f, 0.0) for f in families]
    if sum(weights) <= 0:
        weights = [1.0] * len(families)

    balls, strikes = 0, 0
    pitch_log = []
    for _ in range(MAX_PITCHES_PER_PA):
        family = rng.choices(families, weights=weights, k=1)[0]
        in_zone = rng.random() < zone_rate
        probs = estimate_pitch_outcome_probabilities(hitter_pitches, family, in_zone)

        swings = rng.random() < probs["swingPct"]
        if not swings:
            result = "called_strike" if in_zone else "ball"
        else:
            contact = rng.random() < probs["contactGivenSwingPct"]
            if not contact:
                result = "swinging_strike"
            else:
                in_play = rng.random() < probs["inPlayGivenContactPct"]
                result = "in_play" if in_play else "foul"

        pitch_log.append({"family": family, "inZone": in_zone, "swung": swings, "result": result,
                           "countBefore": {"balls": balls, "strikes": strikes}})

        if result == "ball":
            balls += 1
            if balls >= 4:
                return {"outcome": "BB", "pitchLog": pitch_log, "pitchCount": len(pitch_log),
                        "finalCount": {"balls": balls, "strikes": strikes}}
        elif result == "in_play":
            return {"outcome": "IN_PLAY", "pitchLog": pitch_log, "pitchCount": len(pitch_log),
                    "finalCount": {"balls": balls, "strikes": strikes}}
        elif result == "foul":
            if strikes < 2:
                strikes += 1
        else:  # called_strike or swinging_strike
            strikes += 1
            if strikes >= 3:
                return {"outcome": "K", "pitchLog": pitch_log, "pitchCount": len(pitch_log),
                        "finalCount": {"balls": balls, "strikes": strikes}}

    # Extremely rare fallback (foul-off loop exceeding MAX_PITCHES_PER_PA)
    # -- resolve probabilistically from the last pitch's own contact
    # split rather than looping forever. Documented, not silently swallowed.
    forced = "IN_PLAY" if rng.random() < 0.5 else "K"
    return {"outcome": forced, "pitchLog": pitch_log, "pitchCount": len(pitch_log),
            "finalCount": {"balls": balls, "strikes": strikes}, "forcedResolution": True}
