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

Spread-correction mission addendum: `settle_inning_result()` is now
PARAMETRIC on `lib.research.market_taxonomy.HORIZON_MARKET_STATUS`'s
`outcomeStructureStatus` rather than hardcoded to "only F5" -- the
moment a future phase independently verifies F3 or F7's outcome
structure and flips that status to CONFIRMED_THREE_WAY, this function
starts settling that scope automatically, with no code change required
here. Until then F3/F7 keep returning SETTLEMENT_UNRESOLVED exactly as
before. `extract_period_score_from_linescore()` generalizes
lib/f5_settlement.py's `extract_f5_score_from_linescore()` inning-sum
logic to an arbitrary inning boundary (re-implemented here, not
imported, for the same reason lib/kalshi_period_projections.py
duplicates rather than imports from an execution-layer module).
"""
from lib.research.market_taxonomy import HORIZON_MARKET_STATUS

SETTLEMENT_AWAY = "Away"
SETTLEMENT_TIE = "Tie"
SETTLEMENT_HOME = "Home"
SETTLEMENT_UNRESOLVED = "Unresolved"

GAME_STATUS_FINAL = "Final"
GAME_STATUS_SUSPENDED = "Suspended"
GAME_STATUS_POSTPONED = "Postponed"
GAME_STATUS_CANCELLED = "Cancelled"

HORIZON_INNINGS = {"F3": 3, "F5": 5, "F7": 7}

# Statuses under which a period result CAN be settled, provided that
# many complete innings were actually played -- "Suspended" is included
# because a game suspended after the period boundary still has a
# determinate period result even though the full game's outcome
# remains pending (see module docstring).
_SETTLEABLE_STATUSES = {GAME_STATUS_FINAL, GAME_STATUS_SUSPENDED}


def extract_period_score_from_linescore(linescore_data, through_inning):
    """
    Pure. Generalizes lib/f5_settlement.py's
    extract_f5_score_from_linescore() to sum innings 1..through_inning
    from the MLB linescore API's `innings` array. Returns (away_runs,
    home_runs) or (None, None) if fewer than through_inning innings
    have been completed, exactly mirroring the F5 function's contract.
    """
    innings = (linescore_data or {}).get("innings", [])
    if not innings:
        return None, None

    away_runs = 0
    home_runs = 0
    max_inning = 0

    for inning in innings:
        inning_num = inning.get("num") or inning.get("ordinalNum")
        if inning_num is None:
            continue
        try:
            num = int(inning_num)
        except (TypeError, ValueError):
            continue
        if num > through_inning:
            continue
        if num > max_inning:
            max_inning = num

        away = inning.get("away", {}) or {}
        home = inning.get("home", {}) or {}
        away_r = away.get("runs")
        home_r = home.get("runs")
        if away_r is not None:
            try:
                away_runs += int(away_r)
            except (TypeError, ValueError):
                pass
        if home_r is not None:
            try:
                home_runs += int(home_r)
            except (TypeError, ValueError):
                pass

    if max_inning < through_inning:
        return None, None
    return away_runs, home_runs


def settle_f5_result(away_f5_runs, home_f5_runs, completed_innings, game_status):
    """
    Pure. Settles the F5 Away/Tie/Home three-way result from the score
    after 5 complete innings. Thin wrapper over
    settle_period_result(5, ...) kept for backwards-compatible callers.

    Returns (result, reason_code): result is one of SETTLEMENT_AWAY/
    TIE/HOME/UNRESOLVED; reason_code is None when result is not
    UNRESOLVED, otherwise a short machine-readable string explaining
    why settlement could not be determined. NEVER guesses -- every
    unresolved path is an explicit, named reason, not a silent default.
    """
    result, reason = settle_period_result(5, away_f5_runs, home_f5_runs, completed_innings, game_status)
    # Preserve the exact legacy reason-code strings this function has
    # always returned (callers/tests match on them verbatim).
    legacy_reasons = {
        "missing_official_score_through_inning_5": "missing_official_f5_score",
        "fewer_than_5_complete_innings": "fewer_than_5_complete_innings",
    }
    if reason in legacy_reasons:
        reason = legacy_reasons[reason]
    return result, reason


def settle_period_result(through_inning, away_runs, home_runs, completed_innings, game_status):
    """
    Pure. Settles an Away/Tie/Home three-way result from the score
    after `through_inning` complete innings -- the scope-generic core
    settle_f5_result() now delegates to.
    """
    if away_runs is None or home_runs is None:
        return SETTLEMENT_UNRESOLVED, f"missing_official_score_through_inning_{through_inning}"
    if completed_innings is None or completed_innings < through_inning:
        return SETTLEMENT_UNRESOLVED, f"fewer_than_{through_inning}_complete_innings"
    if game_status not in _SETTLEABLE_STATUSES:
        return SETTLEMENT_UNRESOLVED, f"game_status_{game_status}_not_settleable"

    if away_runs > home_runs:
        return SETTLEMENT_AWAY, None
    if home_runs > away_runs:
        return SETTLEMENT_HOME, None
    return SETTLEMENT_TIE, None


def settle_inning_result(scope, away_runs, home_runs, completed_innings, game_status):
    """
    Pure. Dispatches to settle_period_result() only when
    HORIZON_MARKET_STATUS[scope]["outcomeStructureStatus"] ==
    "CONFIRMED_THREE_WAY" -- today that is F5 only, but this check is
    against the single source of truth (lib.research.market_taxonomy),
    not a hardcoded "== F5" comparison, so F3/F7 settlement activates
    automatically the moment a future phase independently verifies
    their outcome structure, with no change required in this function.
    Until then, F3/F7 ALWAYS return (SETTLEMENT_UNRESOLVED,
    "structure_unverified") regardless of the score/status inputs.
    Never assumes an unverified scope settles like a verified one.
    """
    status = HORIZON_MARKET_STATUS.get(scope, {})
    if status.get("outcomeStructureStatus") != "CONFIRMED_THREE_WAY" or scope not in HORIZON_INNINGS:
        return SETTLEMENT_UNRESOLVED, "structure_unverified"
    return settle_period_result(HORIZON_INNINGS[scope], away_runs, home_runs, completed_innings, game_status)
