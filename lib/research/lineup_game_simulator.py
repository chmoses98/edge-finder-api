#!/usr/bin/env python3
"""
lib/research/lineup_game_simulator.py
========================================
Hitter Projection Engine -- Phase 4 lineup/game-state Monte Carlo
simulator. RBI and runs require lineup state, not an isolated PA
distribution -- this module is the ONE simulator every game-state-
dependent market (RBI, runs, and the PA-count distribution itself) reads
from, per this mission's explicit instruction not to build disconnected
RBI/run regressions when a coherent state-based calculation is feasible.

TARGET HITTER vs OTHER HITTERS
-----------------------------------
The target hitter's every PA is resolved by the full pitch-aware chain:
lib.research.pitch_sequence_model.simulate_pa_pitch_by_pitch() ->
(if ball in play) lib.research.hitter_contact_model. The other 8 lineup
spots use a simpler calibrated categorical draw from a supplied PA-
outcome-rate dict (their own season baseline if supplied, else the
league prior) -- this mission's own spec explicitly allows this
asymmetry ("surrounding hitters may use a simpler but calibrated event
model... for tractability").

BASERUNNING MODEL -- DOCUMENTED SIMPLIFICATION
----------------------------------------------------
Every runner (and the batter) advances EXACTLY as many bases as the hit
type implies (a single = +1 base for existing runners, a double = +2,
etc.); BB/HBP force-advance only. This is a standard, well-understood
simplified baserunning convention (not real productive-out/tag-up/
close-play nuance, which would require far more state than this
foundation tracks) -- explicitly NOT claimed to be exact, just internally
consistent (a HR always scores every runner + the batter; RBI is always
exactly the count of runners who scored on that PA).

STARTER -> BULLPEN TRANSITION
----------------------------------
Re-evaluated at the start of each inning (never mid-inning) via
lib.research.bullpen_exposure_model.should_starter_continue() -- once
the bullpen enters, it never reverts to the starter (irreversible,
matching a real game).
"""

import random
from typing import Optional

from lib.research.pitch_sequence_model import simulate_pa_pitch_by_pitch
from lib.research.hitter_contact_model import build_contact_pool, draw_contact_event, convert_contact_to_outcome
from lib.research.bullpen_exposure_model import should_starter_continue, choose_bullpen_pitcher_hand
from lib.research.hitter_pa_outcome_model import OUTCOME_CATEGORIES, LEAGUE_PRIOR_RATES

TERMINAL_OUT_CATEGORIES = ("K", "OUT")
HIT_CATEGORIES = ("1B", "2B", "3B", "HR")


def _categorical_draw(rates: dict, rng: random.Random) -> str:
    categories = list(OUTCOME_CATEGORIES)
    weights = [max(0.0, rates.get(c, 0.0)) for c in categories]
    if sum(weights) <= 0:
        weights = [LEAGUE_PRIOR_RATES[c] for c in categories]
    return rng.choices(categories, weights=weights, k=1)[0]


def _resample_outcome(outcome, resample_targets, rng):
    """
    Platoon/pitcher-quality live-pricing wiring (Hitter Prop Methodology
    Repair mission): bounded accept/reject step applying
    lib.research.hitter_pa_outcome_model.live_simulation_resample_targets()'s
    already-computed, already-bounded distributional shift to one
    simulated PA outcome. Standard technique, not new modeling: with
    probability min(1, multiplier[outcome]) the pitch-by-pitch/contact-
    model outcome is kept as-is; otherwise a fresh outcome is drawn from
    the platoon/pitcher-quality-adjusted categorical distribution (the
    exact same `_categorical_draw` helper the other 8 lineup spots
    already use). `resample_targets` is None whenever
    live_simulation_resample_targets() had no adjustment to apply
    (missing platoon/pitcher-quality inputs) -- the outcome passes
    through completely unchanged in that case.
    """
    if resample_targets is None:
        return outcome
    multiplier = resample_targets["multipliers"].get(outcome, 1.0)
    if multiplier >= 1.0 or rng.random() < multiplier:
        return outcome
    return _categorical_draw(resample_targets["adjustedRates"], rng)


def _resolve_target_pa(target_hitter_pitches, pitch_mix, batter_hand, park_geometry_entry,
                        field_relative_wind, defense_snapshot, hitter_speed_snapshot, rng,
                        resample_targets=None) -> str:
    seq = simulate_pa_pitch_by_pitch(target_hitter_pitches, pitch_mix or {}, rng)
    if seq["outcome"] in ("BB", "K", "HBP"):
        return _resample_outcome(seq["outcome"], resample_targets, rng)
    # IN_PLAY -> contact model
    pool = build_contact_pool(target_hitter_pitches)
    ev, la, spray, _bbt = draw_contact_event(pool, rng)
    result = convert_contact_to_outcome(ev, la, spray, batter_hand, park_geometry_entry,
                                         field_relative_wind, defense_snapshot, hitter_speed_snapshot, rng)
    return _resample_outcome(result["outcome"], resample_targets, rng)


def _advance_bases(bases, batter_tag, outcome: str):
    """Returns (new_bases, runs_scored_tags) -- see module docstring's baserunning-model note."""
    b = list(bases)
    if outcome in ("BB", "HBP"):
        runs = []
        if b[0] is not None:
            if b[1] is not None:
                if b[2] is not None:
                    runs.append(b[2])
                b[2] = b[1]
            b[1] = b[0]
        b[0] = batter_tag
        return b, runs
    if outcome == "1B":
        runs = [b[2]] if b[2] is not None else []
        return [batter_tag, b[0], b[1]], runs
    if outcome == "2B":
        runs = [x for x in (b[2], b[1]) if x is not None]
        return [None, batter_tag, b[0]], runs
    if outcome == "3B":
        runs = [x for x in b if x is not None]
        return [None, None, batter_tag], runs
    if outcome == "HR":
        runs = [x for x in b if x is not None] + [batter_tag]
        return [None, None, None], runs
    return b, []  # K / OUT -- no base change


def simulate_game(
    target_slot: int,
    target_hitter_pitches: list,
    batter_hand: Optional[str],
    starter_context: dict,
    bullpen_context: dict,
    starter_pitch_mix: Optional[dict],
    bullpen_pitch_mix: Optional[dict],
    park_geometry_entry: Optional[dict],
    field_relative_wind: Optional[dict],
    defense_snapshot: Optional[dict],
    hitter_speed_snapshot: Optional[dict],
    rng: random.Random,
    other_hitter_rates: Optional[list] = None,
    n_innings: int = 9,
    resample_targets: Optional[dict] = None,
) -> dict:
    """
    Simulates one full game and returns the TARGET hitter's stat line:
    {"PA","AB","H","1B","2B","3B","HR","BB","HBP","K","RBI","R","TB"}.
    `target_slot`: 1-9 batting order position. `other_hitter_rates`:
    optional list of 8 PA-outcome-rate dicts (see
    lib.research.hitter_pa_outcome_model's OUTCOME_CATEGORIES) for the
    other lineup spots, in batting-order order excluding the target slot
    is NOT required -- pass a length-9 list indexed by slot-1; the
    target slot's own entry is ignored. Falls back to
    hitter_pa_outcome_model.LEAGUE_PRIOR_RATES for any slot not supplied.

    `resample_targets` (Hitter Prop Methodology Repair mission): optional
    output of lib.research.hitter_pa_outcome_model.live_simulation_resample_targets()
    -- when present, the TARGET hitter's own simulated outcome (from the
    full pitch-by-pitch/contact chain, unchanged) is nudged toward the
    platoon/pitcher-quality-adjusted distribution via bounded accept/
    reject resampling (see _resample_outcome). None (the default)
    reproduces this function's exact pre-mission behavior.
    """
    if not (1 <= target_slot <= 9):
        raise ValueError("target_slot must be 1..9")
    other_hitter_rates = other_hitter_rates or [LEAGUE_PRIOR_RATES] * 9

    stats = {k: 0 for k in ("PA", "AB", "1B", "2B", "3B", "HR", "BB", "HBP", "K", "OUT", "RBI", "R")}
    starter_innings_pitched = 0.0
    starter_active = True
    avg_ip = starter_context.get("avgIPperStart") if starter_context else None

    target_slot_idx = target_slot - 1
    lineup_index = 0

    for inning in range(1, n_innings + 1):
        if inning > 1 and starter_active:
            if not should_starter_continue(starter_innings_pitched, avg_ip, rng):
                starter_active = False

        bases = [None, None, None]
        outs = 0
        while outs < 3:
            slot = lineup_index % 9
            is_target = (slot == target_slot_idx)

            if is_target:
                pitch_mix = starter_pitch_mix if starter_active else bullpen_pitch_mix
                outcome = _resolve_target_pa(
                    target_hitter_pitches, pitch_mix, batter_hand, park_geometry_entry,
                    field_relative_wind, defense_snapshot, hitter_speed_snapshot, rng,
                    resample_targets=resample_targets,
                )
                batter_tag = "TARGET"
            else:
                outcome = _categorical_draw(other_hitter_rates[slot], rng)
                batter_tag = "OTHER"

            if is_target:
                stats["PA"] += 1
                if outcome not in ("BB", "HBP"):
                    stats["AB"] += 1

            if outcome in TERMINAL_OUT_CATEGORIES:
                outs += 1
                if is_target:
                    stats[outcome] += 1
                lineup_index += 1
                continue

            bases, runs = _advance_bases(bases, batter_tag, outcome)
            for tag in runs:
                if tag == "TARGET":
                    stats["R"] += 1
            if is_target:
                stats[outcome] += 1
                stats["RBI"] += len(runs)

            lineup_index += 1

        if starter_active:
            starter_innings_pitched += 1.0

    stats["H"] = stats["1B"] + stats["2B"] + stats["3B"] + stats["HR"]
    stats["TB"] = stats["1B"] + 2 * stats["2B"] + 3 * stats["3B"] + 4 * stats["HR"]
    return stats
