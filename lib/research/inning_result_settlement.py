#!/usr/bin/env python3
"""
lib/research/inning_result_settlement.py
=============================================
Model Performance Phase 2A, Part 14 -- RESEARCH-ONLY settlement support
for F3/F5/F7 Away/Tie/Home results.

Settlement is implemented ONLY for F5 -- the only inning-result horizon
whose outcome structure is independently VERIFIED (see
lib.research.market_taxonomy.HORIZON_MARKET_STATUS). F3/F7 always
settle to SETTLEMENT_UNRESOLVED regardless of input, because this
repository has not independently verified their outcome structure or
official settlement rules -- guessing "F3/F7 probably settle like F5"
is exactly the assumption the mission forbids.

Settlement basis for F5 (per docs/research/KALSHI_MARKET_TAXONOMY.md,
confirmed via real ticker/title inspection, not assumed): the score
after exactly 5 complete innings, independent of what happens in the
rest of the game. This means F5 is settleable the moment 5 complete
innings have been played, EVEN IF the game is later suspended,
postponed (as a continuation), or shortened -- those events only
affect the FULL game outcome, not the already-determined F5 result.

This module does NOT modify lib/f5_settlement.py (production's
existing, real F5 ML settlement module) in any way -- that module
handles the team-side WIN/LOSS/PUSH/VOID/PENDING settlement
production actually executes on, unchanged by this phase. This module
is a separate, additive, three-way (Away/Tie/Home) result determiner
for the RESEARCH shadow ledger and historical snapshot archive only.
"""

SETTLEMENT_AWAY = "Away"
SETTLEMENT_TIE = "Tie"
SETTLEMENT_HOME = "Home"
SETTLEMENT_UNRESOLVED = "Unresolved"

GAME_STATUS_FINAL = "Final"
GAME_STATUS_SUSPENDED = "Suspended"
GAME_STATUS_POSTPONED = "Postponed"
GAME_STATUS_CANCELLED = "Cancelled"

# Statuses under which an F5 result CAN be settled, provided 5 complete
# innings were actually played -- "Suspended" is included because a game
# suspended AFTER inning 5 still has a determinate F5 result even though
# the full game's outcome remains pending (see module docstring).
_SETTLEABLE_STATUSES = {GAME_STATUS_FINAL, GAME_STATUS_SUSPENDED}


def settle_f5_result(away_f5_runs, home_f5_runs, completed_innings, game_status):
    """
    Pure. Settles the F5 Away/Tie/Home three-way result from the score
    after 5 complete innings.

    Returns (result, reason_code): result is one of SETTLEMENT_AWAY/
    TIE/HOME/UNRESOLVED; reason_code is None when result is not
    UNRESOLVED, otherwise a short machine-readable string explaining
    why settlement could not be determined. NEVER guesses -- every
    unresolved path is an explicit, named reason, not a silent default.
    """
    if away_f5_runs is None or home_f5_runs is None:
        return SETTLEMENT_UNRESOLVED, "missing_official_f5_score"
    if completed_innings is None or completed_innings < 5:
        return SETTLEMENT_UNRESOLVED, "fewer_than_5_complete_innings"
    if game_status not in _SETTLEABLE_STATUSES:
        return SETTLEMENT_UNRESOLVED, f"game_status_{game_status}_not_settleable"

    if away_f5_runs > home_f5_runs:
        return SETTLEMENT_AWAY, None
    if home_f5_runs > away_f5_runs:
        return SETTLEMENT_HOME, None
    return SETTLEMENT_TIE, None


def settle_inning_result(scope, away_f5_runs, home_f5_runs, completed_innings, game_status):
    """
    Pure. Dispatches to settle_f5_result() only for scope="F5" --
    F3/F7 ALWAYS return (SETTLEMENT_UNRESOLVED, "structure_unverified")
    regardless of the score/status inputs, since this repository has
    not independently verified their outcome structure or settlement
    rules (see module docstring). Never assumes F3/F7 settle like F5.
    """
    if scope != "F5":
        return SETTLEMENT_UNRESOLVED, "structure_unverified"
    return settle_f5_result(away_f5_runs, home_f5_runs, completed_innings, game_status)
