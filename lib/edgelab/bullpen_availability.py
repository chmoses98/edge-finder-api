#!/usr/bin/env python3
"""
lib/edgelab/bullpen_availability.py
=======================================
Pure, conservative bullpen-availability adjustment derived from PR #51's
recentUsage block (lib/edgelab/bullpen_usage.py). Season-long bullpen
quality (bullpen.xFIP) and this short-term workload signal are kept as
two SEPARATE inputs -- this module never reads or touches xFIP itself,
it only produces a multiplier a caller applies on top of it.

WHY A MULTIPLIER, NOT A NEW "IS RESTED" QUALITY SCORE
--------------------------------------------------------
Season-long xFIP already encodes the bullpen's baseline quality. This
module answers a narrower question -- "does today's recent workload give
a reason to trust that season baseline LESS than usual?" -- so it can
only ever push the effective pen quality WORSE than the season number
(multiplier >= 1.0), never better. A lightly-used, fully rested bullpen
is already priced at its season quality; it does not get an extra bonus
on top of that just for being rested (see `compute_bullpen_workload_
adjustment`'s docstring for the exact no-bonus guarantee).

MISSING DATA
-------------
`recentUsage` missing entirely, or present with `dataAvailable=False`
(PR #51's own explicit-never-guessed flag), ALWAYS means "no adjustment"
(multiplier 1.0, adjustmentApplied=False) -- never "the bullpen must be
rested." This mirrors the same discipline
lib/edgelab/bullpen_usage.py itself already applies to `dataAvailable`.

WHAT IS ACCOUNTED FOR (mirrors the four "at minimum" recentUsage
signals this was built to consume)
--------------------------------------------------------------------
1. `backToBackRelievers`     -- relievers appearing on consecutive days
2. `recentPitchCounts`       -- individual relievers with a heavy recent
                                 pitch load (fatigue risk independent of
                                 leverage role)
3. `highLeverageRecentUsage` -- save/hold relievers who are themselves
                                 recently taxed (the arms a team can least
                                 afford to lose)
4. `teamPitchCountWindow` /
   `teamPitchCountLastGame`  -- aggregate bullpen workload vs a generic
                                 baseline, capturing an unusually heavy
                                 recent stretch even when no single arm
                                 crosses the per-arm thresholds above

Every component is individually capped, and the combined multiplier is
capped again (MAX_TOTAL_PENALTY) -- deliberately conservative so this
stays a modest, transparent nudge rather than a large swing driven by a
single noisy box score. Thresholds are generic (pitch counts, appearance
counts) and not tuned to any specific team, date, or slate.
"""

# ── Tunable, but deliberately conservative, constants ───────────────────────
PER_BACK_TO_BACK_PENALTY = 0.02
MAX_BACK_TO_BACK_PENALTY = 0.06          # caps at 3 back-to-back relievers

HEAVY_USE_PITCH_THRESHOLD = 35           # cumulative pitches in the window
PER_HEAVY_USE_ARM_PENALTY = 0.015
MAX_RECENT_PITCH_PENALTY = 0.045         # caps at 3 heavily-used arms

HIGH_LEVERAGE_TAXED_PITCH_THRESHOLD = 20  # cumulative pitches in the window
PER_TAXED_HIGH_LEVERAGE_ARM_PENALTY = 0.03
MAX_HIGH_LEVERAGE_PENALTY = 0.06          # caps at 2 taxed leverage arms

BASELINE_TEAM_PITCHES_PER_GAME = 55       # generic "normal" bullpen workload/game
WORKLOAD_RATIO_FLOOR = 1.15               # ratios at/below this are unremarkable
WORKLOAD_PENALTY_PER_RATIO_UNIT = 0.15
MAX_WORKLOAD_PENALTY = 0.06

MAX_TOTAL_PENALTY = 0.12                  # combined multiplier never exceeds 1.12


def _empty_component_breakdown():
    return {
        "backToBackCount": 0, "backToBackPenalty": 0.0,
        "heavilyUsedRelieverCount": 0, "recentPitchWorkloadPenalty": 0.0,
        "taxedHighLeverageArmCount": 0, "highLeveragePenalty": 0.0,
        "teamWorkloadRatio": None, "overallWorkloadPenalty": 0.0,
    }


def _no_adjustment(data_available, unavailable_reason):
    return {
        "multiplier": 1.0,
        "adjustmentApplied": False,
        "dataAvailable": data_available,
        "unavailableReason": unavailable_reason,
        "components": _empty_component_breakdown(),
    }


def compute_bullpen_workload_adjustment(recent_usage):
    """
    Pure. `recent_usage` is one team's `bullpen.recentUsage` dict exactly
    as produced by lib/edgelab/bullpen_usage.summarize_team_bullpen_usage
    (or None/{} if the block is absent from a game dict).

    Returns:
        {
          "multiplier": float,          # >= 1.0, applied to season pen xFIP
          "adjustmentApplied": bool,    # False whenever multiplier == 1.0
          "dataAvailable": bool,        # read verbatim from recentUsage
          "unavailableReason": str|None,
          "components": {
              "backToBackCount": int,
              "backToBackPenalty": float,
              "heavilyUsedRelieverCount": int,
              "recentPitchWorkloadPenalty": float,
              "taxedHighLeverageArmCount": int,
              "highLeveragePenalty": float,
              "teamWorkloadRatio": float|None,
              "overallWorkloadPenalty": float,
          },
        }

    No-bonus guarantee: every component below is either 0.0 or positive.
    There is no code path in this function that returns a multiplier
    below 1.0 -- a rested bullpen (low back-to-back count, no taxed
    arms, workload ratio at/below WORKLOAD_RATIO_FLOOR) simply returns
    the neutral multiplier 1.0, exactly like missing data does.
    """
    if not recent_usage or not recent_usage.get("dataAvailable"):
        reason = (recent_usage or {}).get("unavailableReason") or "no_recent_usage_data"
        return _no_adjustment(False, reason)

    # 1. Back-to-back relievers.
    b2b_count = len(recent_usage.get("backToBackRelievers") or [])
    back_to_back_penalty = min(MAX_BACK_TO_BACK_PENALTY, b2b_count * PER_BACK_TO_BACK_PENALTY)

    # 2. Recent pitch workload -- individual relievers heavily used
    #    across the window, regardless of leverage role.
    pitch_counts = recent_usage.get("recentPitchCounts") or []
    heavy_use_count = sum(
        1 for p in pitch_counts if (p.get("totalPitches") or 0) >= HEAVY_USE_PITCH_THRESHOLD
    )
    recent_pitch_penalty = min(MAX_RECENT_PITCH_PENALTY, heavy_use_count * PER_HEAVY_USE_ARM_PENALTY)

    # 3. High-leverage recent usage -- save/hold relievers themselves
    #    recently taxed (the arms a team can least afford to lose).
    hl_usage = recent_usage.get("highLeverageRecentUsage") or []
    taxed_hl_count = sum(
        1 for p in hl_usage if (p.get("totalPitches") or 0) >= HIGH_LEVERAGE_TAXED_PITCH_THRESHOLD
    )
    high_leverage_penalty = min(MAX_HIGH_LEVERAGE_PENALTY, taxed_hl_count * PER_TAXED_HIGH_LEVERAGE_ARM_PENALTY)

    # 4. Overall recent bullpen workload -- aggregate pitches thrown vs
    #    a generic per-game baseline, checked against both the whole
    #    window and the single most recent game (so one unusually heavy
    #    day registers even if the rest of the window was normal).
    games_considered = recent_usage.get("gamesConsidered") or 0
    team_workload_ratio = None
    overall_workload_penalty = 0.0
    if games_considered > 0:
        window_pitches = recent_usage.get("teamPitchCountWindow")
        last_game_pitches = recent_usage.get("teamPitchCountLastGame")
        baseline_window = BASELINE_TEAM_PITCHES_PER_GAME * games_considered

        window_ratio = (window_pitches / baseline_window) if (
            window_pitches is not None and baseline_window > 0
        ) else None
        last_game_ratio = (last_game_pitches / BASELINE_TEAM_PITCHES_PER_GAME) if (
            last_game_pitches is not None
        ) else None

        ratios = [r for r in (window_ratio, last_game_ratio) if r is not None]
        if ratios:
            team_workload_ratio = round(max(ratios), 3)
            if team_workload_ratio > WORKLOAD_RATIO_FLOOR:
                overall_workload_penalty = min(
                    MAX_WORKLOAD_PENALTY,
                    (team_workload_ratio - WORKLOAD_RATIO_FLOOR) * WORKLOAD_PENALTY_PER_RATIO_UNIT,
                )

    total_penalty = min(
        MAX_TOTAL_PENALTY,
        back_to_back_penalty + recent_pitch_penalty + high_leverage_penalty + overall_workload_penalty,
    )
    multiplier = round(1.0 + total_penalty, 4)

    return {
        "multiplier": multiplier,
        "adjustmentApplied": total_penalty > 0,
        "dataAvailable": True,
        "unavailableReason": None,
        "components": {
            "backToBackCount": b2b_count,
            "backToBackPenalty": round(back_to_back_penalty, 4),
            "heavilyUsedRelieverCount": heavy_use_count,
            "recentPitchWorkloadPenalty": round(recent_pitch_penalty, 4),
            "taxedHighLeverageArmCount": taxed_hl_count,
            "highLeveragePenalty": round(high_leverage_penalty, 4),
            "teamWorkloadRatio": team_workload_ratio,
            "overallWorkloadPenalty": round(overall_workload_penalty, 4),
        },
    }
