#!/usr/bin/env python3
"""
lib/kalshi_probability_adapters.py
=====================================
Fair-probability adapter stage of the universal Kalshi MLB market engine
(docs/KALSHI_MLB_MARKET_COVERAGE_AUDIT.md, Phase 2).

Every adapter here computes a fair probability for ONE exact Kalshi
contract (one exact line, one exact side) from the SAME projection
inputs `scripts/build_market_ledger.py` already computes for the 6
currently-modeled market families (`compute_game_projection_context()`)
— this module adds NO new statistical model. It only:
  1. reuses the existing independent-Poisson joint run distribution
     (`poisson_pmf`, `p_team_wins`, `p_over_total`, all imported directly
     from `scripts/build_market_ledger.py`, never reimplemented) against
     every line in a market's alternate-line ladder instead of only the
     single "best" line production currently evaluates, and
  2. adds ONE genuinely new, isolated, documented probability query
     against that same joint distribution — `p_wins_by_over()` — for
     winning-margin (spread) markets, which today have NO model
     probability computed at all in production (RL_Away/RL_Home are
     unconditionally Rule-81-rejected before any modelProb is ever
     calculated; see docs/KALSHI_MLB_MARKET_COVERAGE_AUDIT.md section 2).

CRITICAL RULE (enforced structurally, not just documented): a market
family with no reusable distribution — pitcher strikeouts, pitcher outs,
pitcher hits/earned-runs allowed, hitter hits/total-bases/home-runs, and
any inning-result scope whose outcome structure is unverified (F3, F7) —
NEVER receives a fabricated probability. `adapt_contract()` returns
modelSupportStatus="UNSUPPORTED" with a precise `unsupportedReason` for
these instead.

Nothing in this module changes any EXISTING market's probability
computation for ML, F5 ML (team legs), Team Total Over, or NRFI/YRFI —
each reuses production's exact formula, verified field-for-field against
scripts/build_market_ledger.py in tests/test_kalshi_probability_adapters.py.
F5 Tie is the one newly-exposed market this module adds probability
support for (previously fetched but never evaluated); it is additive
only — it does not alter the F5 Away/Home legacy-conditional values,
which remain bit-identical to production's existing formula.
"""
from scripts.build_market_ledger import poisson_pmf, p_team_wins, p_over_total
from lib.research.three_way_projection import three_way_result_probs
from lib.research.market_taxonomy import (
    FAMILY_GAME_RESULT,
    FAMILY_INNING_RESULT,
    FAMILY_GAME_TOTAL,
    FAMILY_INNING_TOTAL,
    FAMILY_TEAM_TOTAL,
    FAMILY_WINNING_MARGIN,
    FAMILY_FIRST_INNING_RUN,
    HORIZON_MARKET_STATUS,
)

STATUS_SUPPORTED = "SUPPORTED"
STATUS_UNSUPPORTED = "UNSUPPORTED"
STATUS_MISSING_DATA = "MISSING_DATA"

_NEVER_MODELED_FAMILIES = {
    "pitcher_strikeouts": "No Kalshi MLB pitcher-strikeout market has ever been observed in this "
                           "repository's snapshot archive (355 archived files, 720-market inventory) "
                           "and no strikeout-count probability distribution exists in this codebase "
                           "-- pitcherSavant.kPct is used only as a scalar input to the run-scoring "
                           "model, never a strikeout-count distribution. See "
                           "docs/KALSHI_MLB_MARKET_COVERAGE_AUDIT.md section 2.",
    "pitcher_outs": "No Kalshi MLB pitcher-outs/workload market has ever been observed in this "
                    "repository's snapshot archive and no outs-count probability distribution "
                    "exists in this codebase. See docs/KALSHI_MLB_MARKET_COVERAGE_AUDIT.md section 2.",
    "pitcher_hits_allowed": "No Kalshi MLB pitcher-hits-allowed market has ever been observed; no "
                            "probability distribution exists for this in this codebase.",
    "pitcher_earned_runs": "No Kalshi MLB pitcher-earned-runs market has ever been observed; no "
                           "probability distribution exists for this in this codebase.",
    "hitter_hits": "No Kalshi MLB hitter-hits market has ever been observed; no per-batter hit "
                   "probability distribution exists in this codebase.",
    "hitter_total_bases": "No Kalshi MLB hitter-total-bases market has ever been observed; no "
                          "per-batter total-bases distribution exists in this codebase.",
    "hitter_home_runs": "No Kalshi MLB hitter-home-run market has ever been observed; no per-batter "
                       "home-run probability distribution exists in this codebase.",
}


def p_wins_by_over(team_proj, opp_proj, margin, max_r=20):
    """
    P(team's runs - opponent's runs > margin) under the same independent-
    Poisson joint distribution p_team_wins() already uses (imported
    directly from scripts/build_market_ledger.py, not reimplemented).

    This is the ONE genuinely new probability query this module adds: no
    existing production code currently computes a winning-margin
    probability at all (RL_Away/RL_Home are Rule-81-rejected before any
    modelProb is calculated). It reuses the identical joint-distribution
    primitives (poisson_pmf) that every other market in this codebase is
    built on -- not a new statistical model.
    """
    total = 0.0
    for a in range(max_r + 1):
        pa = poisson_pmf(a, team_proj)
        if pa == 0.0:
            continue
        for h in range(max_r + 1):
            if a - h > margin:
                total += pa * poisson_pmf(h, opp_proj)
    return total


def adapt_game_result(away_proj, home_proj, side):
    """
    ML (full-game moneyline). Reuses p_team_wins() and the EXACT
    renormalization formula scripts/build_market_ledger.py already uses
    for ML_Away/ML_Home (p_win / (1 - p_push)) -- bit-identical to
    production, never altered here.
    """
    if away_proj is None or home_proj is None:
        return None, STATUS_MISSING_DATA, "awayProjRuns/homeProjRuns missing from projection context"
    p_away_win, p_push = p_team_wins(away_proj, home_proj)
    p_home_win = 1 - p_away_win - p_push
    denom = 1 - p_push
    if denom <= 0:
        return None, STATUS_MISSING_DATA, "degenerate push probability (1 - p_push <= 0)"
    p_away_net = p_away_win / denom
    p_home_net = p_home_win / denom
    if side == "Away":
        return p_away_net, STATUS_SUPPORTED, None
    if side == "Home":
        return p_home_net, STATUS_SUPPORTED, None
    return None, STATUS_UNSUPPORTED, f"unrecognized side {side!r} for game_result"


def adapt_f5_result(f5_away_proj, f5_home_proj, side):
    """
    F5 winner, including the Tie leg. Away/Home reuse the EXACT legacy
    renormalized formula production's F5_ML_Away/F5_ML_Home already
    compute (bit-identical, verified in tests). Tie is NEWLY exposed
    here -- computed directly from the same joint distribution via
    lib.research.three_way_projection.three_way_result_probs(), never
    renormalized away as production currently discards it.
    """
    if f5_away_proj is None or f5_home_proj is None:
        return None, STATUS_MISSING_DATA, "f5AwayProj/f5HomeProj missing from projection context"

    if side == "Tie":
        probs = three_way_result_probs(f5_away_proj, f5_home_proj)
        return probs["tieProb"], STATUS_SUPPORTED, None

    p_away_win, p_push = p_team_wins(f5_away_proj, f5_home_proj)
    p_home_win = 1 - p_away_win - p_push
    denom = 1 - p_push
    if denom <= 0:
        return None, STATUS_MISSING_DATA, "degenerate push probability (1 - p_push <= 0)"
    p_away_net = p_away_win / denom
    p_home_net = p_home_win / denom
    if side == "Away":
        return p_away_net, STATUS_SUPPORTED, None
    if side == "Home":
        return p_home_net, STATUS_SUPPORTED, None
    return None, STATUS_UNSUPPORTED, f"unrecognized side {side!r} for F5 result"


def adapt_winning_margin(team_proj, opp_proj, line):
    """
    Full-game or F5 spread/winning-margin, ANY line in the alternate
    ladder. Uses p_wins_by_over() (see module docstring) -- evaluated
    separately for every exact line, never approximated from another
    line's price.
    """
    if team_proj is None or opp_proj is None:
        return None, STATUS_MISSING_DATA, "team/opponent projected runs missing"
    if line is None:
        return None, STATUS_MISSING_DATA, "line missing"
    return p_wins_by_over(team_proj, opp_proj, line), STATUS_SUPPORTED, None


def adapt_total(total_proj, line, side="Over"):
    """
    Full-game or F5 total, ANY line in the alternate ladder. Reuses
    p_over_total() directly (imported, not reimplemented) -- same
    formula production already uses for Game_Total's single best line,
    now applied per-line.
    """
    if total_proj is None:
        return None, STATUS_MISSING_DATA, "total projected runs missing"
    if line is None:
        return None, STATUS_MISSING_DATA, "line missing"
    p_over = p_over_total(total_proj, line)
    if side == "Over":
        return p_over, STATUS_SUPPORTED, None
    if side == "Under":
        return 1.0 - p_over, STATUS_SUPPORTED, None
    return None, STATUS_UNSUPPORTED, f"unrecognized side {side!r} for total"


def adapt_team_total(team_proj, line, side="Over"):
    """
    Team total, ANY line, EITHER side. Reuses p_over_total() exactly as
    production's TT_Away_Over/TT_Home_Over already do for the single
    best line; Under is 1 - Over of the SAME contract (Kalshi's team
    total is a single two-sided ticker, not a separate Under market).
    """
    if team_proj is None:
        return None, STATUS_MISSING_DATA, "team projected runs missing"
    if line is None:
        return None, STATUS_MISSING_DATA, "line missing"
    p_over = p_over_total(team_proj, line)
    if side == "Over":
        return p_over, STATUS_SUPPORTED, None
    if side == "Under":
        return 1.0 - p_over, STATUS_SUPPORTED, None
    return None, STATUS_UNSUPPORTED, f"unrecognized side {side!r} for team_total"


def adapt_first_inning_run(away_proj, home_proj):
    """
    NRFI/YRFI. Reuses production's exact naive first-inning scaling
    (proj / 9) and poisson_pmf(0, ...) formula from
    scripts/build_market_ledger.py's NRFI/YRFI section, bit-identical.
    Returns P(YRFI) = P(a run scores); NRFI = 1 - YRFI (same contract,
    opposite side).
    """
    if away_proj is None or home_proj is None:
        return None, STATUS_MISSING_DATA, "awayProjRuns/homeProjRuns missing"
    inning1_away = away_proj / 9
    inning1_home = home_proj / 9
    p_nrfi_away = poisson_pmf(0, inning1_home)
    p_nrfi_home = poisson_pmf(0, inning1_away)
    p_nrfi = p_nrfi_away * p_nrfi_home
    p_yrfi = 1.0 - p_nrfi
    return p_yrfi, STATUS_SUPPORTED, None


def adapt_contract(market_family, period, side, line, projection_context):
    """
    Top-level dispatcher: given a classified contract's marketFamily,
    period, side, and line, plus the game's
    compute_game_projection_context() output, returns
    (fair_probability_or_None, modelSupportStatus, unsupportedReason).

    Never raises. Never fabricates a probability for a family this
    module cannot support -- returns STATUS_UNSUPPORTED with a precise
    reason instead, and the contract is still retained upstream (this
    function's caller is responsible for keeping it in the discovery/
    audit output; this function only decides whether it can be priced).
    """
    ctx = projection_context or {}
    away_proj = ctx.get("awayProjRuns")
    home_proj = ctx.get("homeProjRuns")
    f5_away_proj = ctx.get("f5AwayProj")
    f5_home_proj = ctx.get("f5HomeProj")
    total_proj = ctx.get("totalProj")

    if market_family == FAMILY_GAME_RESULT and period == "full_game":
        return adapt_game_result(away_proj, home_proj, side)

    if market_family == FAMILY_INNING_RESULT and period == "F5":
        return adapt_f5_result(f5_away_proj, f5_home_proj, side)

    if market_family == FAMILY_INNING_RESULT and period in ("F3", "F7"):
        status = HORIZON_MARKET_STATUS.get(period, {})
        return None, STATUS_UNSUPPORTED, (
            f"{period} outcome structure is UNVERIFIED (existence is user-confirmed on Kalshi, "
            f"but this repository has never independently verified whether it is a two-way or "
            f"three-way contract) -- {status.get('rootCauseOfNonDiscovery', '')}"
        )

    if market_family == FAMILY_WINNING_MARGIN:
        # Which projection is "team" vs "opponent" depends on which team
        # this specific contract's side refers to -- the caller resolves
        # that (via the classified contract's subjectId/team abbreviation
        # against the game's away/home projections) and passes the two
        # projections in the right order via projection_context, since
        # this function has no independent way to know team identity.
        team_proj = ctx.get("teamProj")
        opp_proj = ctx.get("oppProj")
        return adapt_winning_margin(team_proj, opp_proj, line)

    if market_family == FAMILY_GAME_TOTAL and period == "full_game":
        return adapt_total(total_proj, line, side)

    if market_family == FAMILY_INNING_TOTAL and period == "F5":
        f5_total_proj = (f5_away_proj + f5_home_proj) if (f5_away_proj is not None and f5_home_proj is not None) else None
        return adapt_total(f5_total_proj, line, side)

    if market_family == FAMILY_TEAM_TOTAL:
        team_proj = ctx.get("teamProj")
        return adapt_team_total(team_proj, line, side)

    if market_family == FAMILY_FIRST_INNING_RUN:
        prob, status, reason = adapt_first_inning_run(away_proj, home_proj)
        if prob is None:
            return None, status, reason
        if side == "Yes":
            return prob, STATUS_SUPPORTED, None
        if side == "No":
            return 1.0 - prob, STATUS_SUPPORTED, None
        return None, STATUS_UNSUPPORTED, f"unrecognized side {side!r} for first_inning_run"

    if market_family in _NEVER_MODELED_FAMILIES:
        return None, STATUS_UNSUPPORTED, _NEVER_MODELED_FAMILIES[market_family]

    if market_family is None:
        return None, STATUS_UNSUPPORTED, (
            "contract's series ticker was not recognized by lib.kalshi_mlb_market_classifier "
            "(unclassified series) -- retained in discovery output but no probability can be "
            "computed for an unclassified market family"
        )

    return None, STATUS_UNSUPPORTED, (
        f"marketFamily={market_family!r} period={period!r} has no registered probability adapter"
    )
