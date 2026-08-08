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
family with no reusable distribution — pitcher hits/earned-runs allowed,
hitter hits/total-bases/home-runs, and any inning-result scope whose
outcome structure is unverified — NEVER receives a fabricated
probability. `adapt_contract()` returns modelSupportStatus="UNSUPPORTED"
with a precise `unsupportedReason` for these instead.

Pitcher workload/K/outs joint-modeling mission: pitcher_strikeouts and
pitcher_outs (KXMLBKS/KXMLBOUTS) are no longer in that never-modeled
set. `adapt_pitcher_strikeouts()`/`adapt_pitcher_outs()` both read from
a SINGLE lib.research.pitcher_workload_projection.project_pitcher_workload()
call for the same pitcher/game context, never two independently-derived
point estimates — see that module's docstring for the underlying
survival-curve model and its "INTENTIONALLY DEFERRED" note on the
classifier-side identity resolution these adapters still need before
they're reachable through the live discovery pipeline
(scripts/discover_kalshi_mlb_markets.py), not just through
adapt_contract() directly.

Nothing in this module changes any EXISTING market's probability
computation for full-game ML, Team Total Over, or NRFI/YRFI — each
reuses production's exact formula, verified field-for-field against
scripts/build_market_ledger.py in tests/test_kalshi_probability_adapters.py.

F3/F5/F7 three-way-outcome mission: `adapt_f5_result()` (used for all
three of F3/F5/F7's Away/Tie/Home winner-market legs — see
_VERIFIED_THREE_WAY_PERIODS below) now computes Away/Tie/Home from a
SINGLE lib.research.three_way_projection.three_way_result_probs() call,
matching scripts/build_market_ledger.py's own F5_ML_Away/F5_ML_Home rows
(F5 Three-Way Pricing Correction milestone). Previously Away/Home used
an OLDER two-way-renormalized formula (p_win / (1 - p_push)) while Tie
was priced separately from the raw joint distribution, so a game's three
sibling contracts' fairProbabilityPct values never actually summed to
100%. They do now, for F3 and F5 alike (F3's outcome structure was
independently confirmed CONFIRMED_THREE_WAY after this module was first
written — see lib.research.market_taxonomy.HORIZON_MARKET_STATUS).
"""
from scripts.build_market_ledger import poisson_pmf, p_team_wins, p_over_total
from lib.research.three_way_projection import three_way_result_probs
from lib.research.pitcher_workload_projection import project_pitcher_workload
from lib.research.market_taxonomy import (
    FAMILY_GAME_RESULT,
    FAMILY_INNING_RESULT,
    FAMILY_GAME_TOTAL,
    FAMILY_INNING_TOTAL,
    FAMILY_TEAM_TOTAL,
    FAMILY_WINNING_MARGIN,
    FAMILY_FIRST_INNING_RUN,
    FAMILY_PITCHER_STRIKEOUTS,
    FAMILY_PITCHER_OUTS,
    HORIZON_MARKET_STATUS,
)

# Periods whose winner-market outcome structure is independently
# verified as three-way (today: F5 only). Checked against the single
# taxonomy source of truth rather than a hardcoded "== F5" comparison
# so a future phase's verification of F3/F7 activates winner-market
# support here automatically, with no code change required in this
# module (spread-correction mission Part 3/6).
_VERIFIED_THREE_WAY_PERIODS = {
    scope for scope, status in HORIZON_MARKET_STATUS.items()
    if status.get("outcomeStructureStatus") == "CONFIRMED_THREE_WAY"
}

STATUS_SUPPORTED = "SUPPORTED"
STATUS_UNSUPPORTED = "UNSUPPORTED"
STATUS_MISSING_DATA = "MISSING_DATA"

_NEVER_MODELED_FAMILIES = {
    "pitcher_hits_allowed": "No Kalshi MLB pitcher-hits-allowed series has ever been observed in "
                            "the live series catalogue; no probability distribution exists for "
                            "this in this codebase.",
    "pitcher_earned_runs": "No Kalshi MLB pitcher-earned-runs series has ever been observed in "
                           "the live series catalogue; no probability distribution exists for "
                           "this in this codebase.",
    "hitter_hits": "KXMLBHIT is a CONFIRMED real Kalshi series (live series-catalogue dispatch, "
                   "Kalshi price-checker correction mission), but no per-batter hit probability "
                   "distribution exists in this codebase.",
    "hitter_total_bases": "KXMLBTB is a CONFIRMED real Kalshi series (live series-catalogue "
                          "dispatch, Kalshi price-checker correction mission), but no per-batter "
                          "total-bases distribution exists in this codebase.",
    "hitter_home_runs": "No Kalshi MLB hitter-home-run series has ever been observed in the live "
                       "series catalogue; no per-batter home-run probability distribution exists "
                       "in this codebase.",
    "hitter_rbis": "KXMLBRBI is a CONFIRMED real Kalshi series (live series-catalogue dispatch, "
                   "14 events / 119 markets observed 2026-07-30), but no per-batter RBI "
                   "probability distribution exists in this codebase.",
    "hitter_stolen_bases": "KXMLBSB is a CONFIRMED real Kalshi series (live series-catalogue "
                          "dispatch, 13 events / 43 markets observed 2026-07-30), but no "
                          "per-batter stolen-base probability distribution exists in this "
                          "codebase.",
    "hitter_hits_runs_rbis": "KXMLBHRR is a CONFIRMED real Kalshi series (live series-catalogue "
                            "dispatch, Kalshi price-checker correction mission), but no combined "
                            "hits+runs+RBIs probability distribution exists in this codebase.",
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
    Winner-market leg (Away/Tie/Home) for any horizon whose outcome
    structure is CONFIRMED_THREE_WAY -- today F5, F3, and F7 (see
    _VERIFIED_THREE_WAY_PERIODS/adapt_contract, which routes all three
    periods here under the SAME shared logic, since the underlying
    three-way combinatorics do not depend on which horizon's proj
    values are passed in).

    All three sides are read from the SAME
    lib.research.three_way_projection.three_way_result_probs() call, so
    awayWinProb + tieProb + homeWinProb sum to 1 by construction --
    Away/Home are NEVER renormalized after removing the tie. This
    matches scripts/build_market_ledger.py's own F5_ML_Away/F5_ML_Home
    rows (F5 Three-Way Pricing Correction milestone), which already
    compute Away/Home this same tie-retained way; this adapter
    previously still used the OLDER two-way-renormalized formula
    (p_win / (1 - p_push)) for Away/Home while pricing Tie separately
    from the raw joint distribution -- so a game's three sibling
    Away/Tie/Home contracts never actually summed to 100% fair
    probability. That mismatch is what this fixes; p_team_wins is no
    longer used here (still used by adapt_game_result for full-game ML,
    which has no tradable tie market on Kalshi and is unchanged).
    """
    if f5_away_proj is None or f5_home_proj is None:
        return None, STATUS_MISSING_DATA, "f5AwayProj/f5HomeProj missing from projection context"

    probs = three_way_result_probs(f5_away_proj, f5_home_proj)
    if side == "Away":
        return probs["awayWinProb"], STATUS_SUPPORTED, None
    if side == "Home":
        return probs["homeWinProb"], STATUS_SUPPORTED, None
    if side == "Tie":
        return probs["tieProb"], STATUS_SUPPORTED, None
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


def _pitcher_workload_result(ctx):
    """
    Shared plumbing for adapt_pitcher_strikeouts/adapt_pitcher_outs --
    builds the ONE joint lib.research.pitcher_workload_projection
    result both pull their threshold probability from, so a
    pitcher_outs and pitcher_strikeouts contract for the SAME
    pitcher/game can never independently drift (see that module's
    docstring for the underlying model). avgIPperStart and kPct are the
    two required inputs; every other field is optional and read only
    when the caller's projection_context actually supplies it -- see
    lib.research.pitcher_workload_projection.survival_curve's
    diagnostics for exactly which optional inputs were used.

    ctx key names (caller-populated -- see this function's own
    docstring on adapt_pitcher_strikeouts/adapt_pitcher_outs for the
    still-deferred classifier wiring that would populate these in
    production): pitcherAvgIPperStart, pitcherKPct, pitcherBBPct,
    pitcherOpenerRole, pitcherTTOSplit, pitcherTTORisk,
    pitcherRecentWorkloadRestricted, opponentWrcPlus, opponentTeamKPct.
    """
    avg_ip = ctx.get("pitcherAvgIPperStart")
    k_pct = ctx.get("pitcherKPct")
    if avg_ip is None or k_pct is None:
        return None
    return project_pitcher_workload(
        avg_ip_per_start=avg_ip, k_pct=k_pct, bb_pct=ctx.get("pitcherBBPct"),
        opener=bool(ctx.get("pitcherOpenerRole", False)),
        tto_split=ctx.get("pitcherTTOSplit"), tto_risk=ctx.get("pitcherTTORisk"),
        recent_workload_restricted=ctx.get("pitcherRecentWorkloadRestricted"),
        opponent_wrc_plus=ctx.get("opponentWrcPlus"),
        opponent_k_pct=ctx.get("opponentTeamKPct"),
    )


def adapt_pitcher_strikeouts(ctx, threshold, side="Yes"):
    """
    P(K >= threshold) for a pitcher_strikeouts "N+" contract
    (lib.edgelab.player_prop_settlement: AT_LEAST, no push, no half
    line) -- YES side. NO is the complementary probability, same
    single two-sided-ticker convention as adapt_total/adapt_team_total/
    adapt_first_inning_run above.

    Requires the contract's own pitcher/team identity already resolved
    into ctx by the caller -- same "caller resolves identity, this
    function only prices" convention as adapt_winning_margin/
    adapt_team_total (this function has no independent way to know
    which pitcher a contract refers to). As of this function's
    introduction nothing populates that resolution end-to-end yet --
    lib.kalshi_mlb_market_classifier.classify_contract()'s
    _PITCHER_FAMILIES branch still leaves subjectId/side/line
    unresolved for a real contract (see that module's own comment) --
    so this adapter is reachable today via adapt_contract() directly
    and via tests, not yet via the live discovery pipeline. See
    lib.research.pitcher_workload_projection's module docstring,
    "INTENTIONALLY DEFERRED" section, for the exact remaining wiring.
    """
    if threshold is None:
        return None, STATUS_MISSING_DATA, "threshold missing"
    result = _pitcher_workload_result(ctx)
    if result is None:
        return None, STATUS_MISSING_DATA, "pitcherAvgIPperStart/pitcherKPct missing from projection context"
    if result["insufficientWorkloadData"]:
        return None, STATUS_MISSING_DATA, "insufficient starter workload data for a joint projection"
    prob = result["pStrikeoutsAtLeast"](int(threshold))
    if prob is None:
        return None, STATUS_MISSING_DATA, "strikeout probability could not be computed from the supplied context"
    if side == "Yes":
        return prob, STATUS_SUPPORTED, None
    if side == "No":
        return 1.0 - prob, STATUS_SUPPORTED, None
    return None, STATUS_UNSUPPORTED, f"unrecognized side {side!r} for pitcher_strikeouts"


def adapt_pitcher_outs(ctx, threshold, side="Yes"):
    """
    P(Outs >= threshold) for a pitcher_outs "N+" contract -- see
    adapt_pitcher_strikeouts for the shared identity-resolution
    precondition and deferred-wiring note. Uses the SAME
    _pitcher_workload_result() call (and therefore the same survival
    curve) as adapt_pitcher_strikeouts for this pitcher/game, never an
    independently-derived outs figure.
    """
    if threshold is None:
        return None, STATUS_MISSING_DATA, "threshold missing"
    result = _pitcher_workload_result(ctx)
    if result is None:
        return None, STATUS_MISSING_DATA, "pitcherAvgIPperStart/pitcherKPct missing from projection context"
    if result["insufficientWorkloadData"]:
        return None, STATUS_MISSING_DATA, "insufficient starter workload data for a joint projection"
    prob = result["pOutsAtLeast"](int(threshold))
    if side == "Yes":
        return prob, STATUS_SUPPORTED, None
    if side == "No":
        return 1.0 - prob, STATUS_SUPPORTED, None
    return None, STATUS_UNSUPPORTED, f"unrecognized side {side!r} for pitcher_outs"


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
        # Winner-market (Away/Tie/Home) support is gated on independently
        # VERIFIED outcome structure (see _VERIFIED_THREE_WAY_PERIODS
        # above) -- unlike spread/total below, a winner contract's fair
        # probability formula depends on whether it is two-way or
        # three-way, which is exactly what is unverified for F3/F7. This
        # activates automatically (no code change here) the moment a
        # future phase independently confirms the structure.
        if period in _VERIFIED_THREE_WAY_PERIODS:
            period_proj = ctx.get(f"{period.lower()}AwayProj"), ctx.get(f"{period.lower()}HomeProj")
            return adapt_f5_result(*period_proj, side)
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

    if market_family == FAMILY_INNING_TOTAL and period in ("F3", "F7"):
        # Reuses the SAME p_over_total() primitive as every other total
        # market -- the only new input is the period-scaled projection
        # sum (lib.kalshi_period_projections), computed by the caller
        # and threaded in via projection_context as "<period>AwayProj"/
        # "<period>HomeProj" (spread-correction mission Part 3).
        p_away = ctx.get(f"{period.lower()}AwayProj")
        p_home = ctx.get(f"{period.lower()}HomeProj")
        period_total_proj = (p_away + p_home) if (p_away is not None and p_home is not None) else None
        return adapt_total(period_total_proj, line, side)

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

    if market_family == FAMILY_PITCHER_STRIKEOUTS:
        return adapt_pitcher_strikeouts(ctx, line, side or "Yes")

    if market_family == FAMILY_PITCHER_OUTS:
        return adapt_pitcher_outs(ctx, line, side or "Yes")

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
