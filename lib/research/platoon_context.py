#!/usr/bin/env python3
"""
lib/research/platoon_context.py
====================================
Reusable confirmed-lineup vs opposing-starter handedness/platoon layer.

WHY THIS MODULE EXISTS
-----------------------
Two platoon-relevant data paths already existed in this codebase before
this module but were never actually wired into a live game:
  - api/savant.js's fetchPlatoonSplits() computes a starting pitcher's
    K%/BB%/xERA vs LHH and vs RHH from Baseball Savant, but is only
    reachable via `/api/savant?playerIds=...`, which no production
    script has ever called with playerIds set (confirmed by direct
    inspection of scripts/fetch_savant_pitchers.py and every recent
    committed data/slates/*/authoritative.json: pitcherSavant.vsLHH/
    vsRHH is null on every start in every slate on disk) -- also noted
    independently in lib/research/pitcher_workload_projection.py's own
    docstring.
  - scripts/fetch_lineups.py resolves the MLB Stats API boxscore's
    confirmed battingOrder into a single aggregate team-level xwOBA
    delta (`lineupWOBADelta`/`lineupAdj`) and then discards the
    individual batters -- no handedness, no per-batter identity, no
    batting-order position ever reaches data/slate.json. There has
    never been a way for this repo to know "the confirmed #2 hitter
    bats left" let alone compare that to the starter's throwing hand.

This module is the reusable engine that turns (now-populated) starter
handedness splits and (now-captured) per-batter lineup handedness/split
data into ONE platoon-context object per offense, safe to plug into any
market's projection (compute_projections()'s away_proj/home_proj/F5,
and, via a smaller secondary nudge, lib.research.first_inning_context)
without re-deriving the handedness logic in each market's own code.

INPUT SCHEMA THIS MODULE READS (all optional -- every field's absence
degrades gracefully, never raises, never fabricates)
------------------------------------------------------------------------
g['awayTeamStats'/'homeTeamStats']:
  lineupConfirmedOfficial: bool  -- REQUIRED True to compute any
    player-level context at all (Requirement: unconfirmed lineup never
    guesses players/order).
  confirmedLineup: list of up to 9 dicts, batting-order 1..9, each:
    {order, playerId, name, batSide ('L'/'R'/'S'/None),
     seasonWOBA (float or None), platoonSplits: {
        'vsLHP': {'woba':.., 'kPct':.., 'bbPct':.., 'iso':.., 'pa':..},
        'vsRHP': {...},
     } (each split optional/None until its own sample threshold)}
    -- written by scripts/fetch_lineups.py (batSide/order/seasonWOBA)
    and scripts/fetch_batter_platoon_splits.py (platoonSplits).
  teamSeasonWOBA: float or None -- team's own season xwOBA (already
    written by fetch_lineups.py; reused here as the no-handedness
    baseline the platoon-specific delta is measured against, so this
    module never double-counts the existing generic lineupAdj).

g['away'/'home']['pitcher']: {pitchHand: 'L'/'R'/None} -- opposing
  starter's throwing hand (written by api/pitchers.js).

g['away'/'home']['pitcherSavant']: {
    vsLHH: {'xERA':.., 'kPct':.., 'bbPct':.., 'pa':.., 'hardHitPct':..} or None,
    vsRHH: {...} or None,
  } -- opposing starter's OWN platoon splits (written by
  scripts/fetch_savant_pitchers.py's now-wired savant `splits=true` call).

SCOPE / SAFETY
---------------
Every function here is pure: no file I/O, no network, no clock reads,
no printing, no mutation of any argument, deterministic given
deterministic inputs. build_offense_platoon_context(g, side) is a pure
function of `g` alone (matching compute_projections(g)'s own single-
argument shape), so scripts/build_market_ledger.py can call it from
inside compute_projections(g) without adding a second required input
or changing compute_projections()'s call signature/count (see
tests/test_build_market_ledger_projection_boundary.py's structural
"exactly one compute_projections() call per game" guarantee, which this
module does not touch -- it is called BY compute_projections(), not
instead of it).
"""

from typing import Optional

STATUS_OK = "OK"
STATUS_LINEUP_UNCONFIRMED = "LINEUP_UNCONFIRMED"
STATUS_MISSING_DATA = "MISSING_DATA"

# ── Sample-size floors (conservative by design -- Requirement 7) ───────────
# Hitter platoon splits are small-sample by nature (one player's PAs vs one
# pitcher hand, within a single season) -- shrink to the player's own season
# wOBA below this floor rather than trust a noisy split.
MIN_PA_HITTER_SPLIT = 40
# Starter platoon splits already carry their own floor at the fetch layer
# (api/savant.js's fetchPlatoonSplits: `min_pas=20`); this constant documents
# that floor here too so a caller inspecting this module doesn't have to
# cross-reference the JS fetch layer to know why `pa` might already be >= 20
# whenever a split is non-null.
MIN_PA_STARTER_SPLIT = 20
# Below this many resolved lineup spots, a lineup is administratively
# "confirmed" (per fetch_lineups.py's own MIN_BATTERS_FOR_CONFIRMED=6) but
# too thin for a platoon-specific read -- fall back to MISSING_DATA for the
# hitter-side component only (the starter-side component, which does not
# depend on lineup completeness, can still apply).
MIN_LINEUP_BATTERS_FOR_PLATOON = 6

LEAGUE_AVG_WOBA = 0.318
# wOBA points -> runs/game, same scalar fetch_lineups.py already uses for
# the (handedness-agnostic) lineupAdj, so this module's adjustment is on the
# same, already-reviewed, unit scale -- not a new made-up conversion factor.
WOBA_TO_RPG_SCALAR = 4.5

# Bounded, additive, ON TOP OF (never a replacement for) the existing
# offenseBaselineAdj/lineupAdj -- this is deliberately smaller than
# fetch_lineups.py's own LINEUP_ADJ_CAP (0.25 R/G) since it is a narrower,
# handedness-specific refinement of a signal that adjustment already
# partially captures.
PLATOON_ADJ_CAP_RPG = 0.15

TOP_ORDER_SLOTS = (1, 2, 3)
TOP_ORDER_WEIGHT = 1.5  # batting order spots 1-3 weighted 1.5x in the lineup aggregate


def classify_hand(code) -> Optional[str]:
    """Normalize a handedness code ('L'/'R'/'S'/'Left'/'Right'/'Switch'/'Both') to 'L'/'R'/'S'/None."""
    if not code:
        return None
    c = str(code).strip().upper()
    if c in ("L", "LEFT"):
        return "L"
    if c in ("R", "RIGHT"):
        return "R"
    if c in ("S", "SWITCH", "B", "BOTH"):
        return "S"
    return None


def resolve_effective_hand(bat_side, pitcher_hand) -> Optional[str]:
    """
    A switch hitter always bats from the side opposite the pitcher's
    throwing hand (standard platoon convention) -- resolve to the
    effective 'L'/'R' a given matchup actually presents. Non-switch
    hitters are unaffected; returns None if bat_side is unresolvable.
    """
    hand = classify_hand(bat_side)
    if hand is None:
        return None
    if hand == "S":
        if pitcher_hand == "L":
            return "R"
        if pitcher_hand == "R":
            return "L"
        return None  # switch hitter's effective side is unknown without the pitcher's hand
    return hand


def lineup_handedness_composition(confirmed_lineup) -> dict:
    """
    Pure summary of a confirmed lineup's handedness makeup -- independent
    of any opposing pitcher. confirmed_lineup: list of {order, batSide, ...}.
    """
    counts = {"L": 0, "R": 0, "S": 0, "UNKNOWN": 0}
    for h in confirmed_lineup or []:
        hand = classify_hand(h.get("batSide"))
        counts[hand or "UNKNOWN"] += 1

    top3 = sorted(
        [h for h in (confirmed_lineup or []) if isinstance(h.get("order"), int) and 1 <= h["order"] <= 3],
        key=lambda h: h["order"],
    )
    top3_handedness = "".join(classify_hand(h.get("batSide")) or "?" for h in top3)

    return {
        "countL": counts["L"],
        "countR": counts["R"],
        "countS": counts["S"],
        "countUnknown": counts["UNKNOWN"],
        "lineupSize": len(confirmed_lineup or []),
        "top3Handedness": top3_handedness or None,
        "top3Resolved": len(top3),
    }


def hitter_platoon_value(hitter, pitcher_hand):
    """
    Resolve one confirmed hitter's expected wOBA vs `pitcher_hand`
    ('L'/'R'). Prefers the hitter's own platoon split when the sample
    clears MIN_PA_HITTER_SPLIT; shrinks to the hitter's season wOBA
    (still real, still player-specific -- just not handedness-specific)
    otherwise, explicitly flagged as a fallback.

    Returns (woba, pa_or_None, used_season_fallback: bool). Returns
    (None, None, False) if nothing usable is available for this hitter
    at all -- caller excludes it from the weighted average rather than
    guessing league average.
    """
    effective_hand = resolve_effective_hand(hitter.get("batSide"), pitcher_hand)
    splits = hitter.get("platoonSplits") or {}
    split_key = {"L": "vsLHP", "R": "vsRHP"}.get(pitcher_hand)
    split = splits.get(split_key) if split_key else None

    if effective_hand is not None and split and split.get("woba") is not None:
        pa = split.get("pa") or 0
        if pa >= MIN_PA_HITTER_SPLIT:
            return split["woba"], pa, False

    season_woba = hitter.get("seasonWOBA")
    if season_woba is not None:
        return season_woba, hitter.get("seasonPA"), True

    return None, None, False


def weighted_lineup_platoon_woba(confirmed_lineup, pitcher_hand):
    """
    Weighted average (top-3 batting-order spots weighted TOP_ORDER_WEIGHT,
    the rest 1x) of every resolvable confirmed hitter's platoon-value
    wOBA vs `pitcher_hand`. Returns
    (weighted_woba_or_None, n_used, n_real_split, n_season_fallback).
    """
    if not confirmed_lineup or pitcher_hand not in ("L", "R"):
        return None, 0, 0, 0

    total_w = 0.0
    total_wv = 0.0
    n_used = 0
    n_real = 0
    n_fallback = 0
    for h in confirmed_lineup:
        woba, _pa, used_fallback = hitter_platoon_value(h, pitcher_hand)
        if woba is None:
            continue
        order = h.get("order")
        weight = TOP_ORDER_WEIGHT if (isinstance(order, int) and order in TOP_ORDER_SLOTS) else 1.0
        total_w += weight
        total_wv += weight * woba
        n_used += 1
        if used_fallback:
            n_fallback += 1
        else:
            n_real += 1

    if total_w == 0:
        return None, 0, 0, 0
    return round(total_wv / total_w, 4), n_used, n_real, n_fallback


def _weighted_hand_mix(vs_l, vs_r, l_n, r_n, field, min_pa):
    """Shared weighted-blend helper for one field (xERA, hardHitPct, ...)."""
    l_val = vs_l.get(field) if (vs_l.get("pa") or 0) >= min_pa else None
    r_val = vs_r.get(field) if (vs_r.get("pa") or 0) >= min_pa else None
    covered_n = (l_n if l_val is not None else 0) + (r_n if r_val is not None else 0)
    if covered_n == 0:
        return None
    weighted = (l_val * l_n if l_val is not None else 0.0) + (r_val * r_n if r_val is not None else 0.0)
    return round(weighted / covered_n, 3)


def starter_hand_mix_split(pitcher_savant, hand_counts):
    """
    Blend a starter's OWN vsLHH/vsRHH splits, weighted by the OFFENSE's
    actual handedness mix facing him (hand_counts: {'L': n, 'R': n} --
    switch hitters already pre-resolved into L/R counts by the caller).
    Returns (blended_xera_or_None, coverage, extras) where coverage is
    the fraction of `hand_counts` total covered by a non-null,
    adequately-sampled xERA split (1.0 = both hands available, 0.0 =
    neither), and extras is a dict of any other hand-mix-weighted
    fields available on the same splits (currently hardHitPct/
    barrelPct -- HR/quality-of-contact indicators, reported for context
    only, never blended into the RPG adjustment itself).
    """
    ps = pitcher_savant or {}
    vs_l = ps.get("vsLHH") or {}
    vs_r = ps.get("vsRHH") or {}
    l_n = hand_counts.get("L", 0)
    r_n = hand_counts.get("R", 0)
    total_n = l_n + r_n
    if total_n == 0:
        return None, 0.0, {}

    xera = _weighted_hand_mix(vs_l, vs_r, l_n, r_n, "xERA", MIN_PA_STARTER_SPLIT)
    if xera is None:
        return None, 0.0, {}

    l_xera = vs_l.get("xERA") if (vs_l.get("pa") or 0) >= MIN_PA_STARTER_SPLIT else None
    r_xera = vs_r.get("xERA") if (vs_r.get("pa") or 0) >= MIN_PA_STARTER_SPLIT else None
    covered_n = (l_n if l_xera is not None else 0) + (r_n if r_xera is not None else 0)

    extras = {}
    hard_hit = _weighted_hand_mix(vs_l, vs_r, l_n, r_n, "hardHitPct", MIN_PA_STARTER_SPLIT)
    barrel = _weighted_hand_mix(vs_l, vs_r, l_n, r_n, "barrelPct", MIN_PA_STARTER_SPLIT)
    if hard_hit is not None:
        extras["hardHitPctVsHandMix"] = hard_hit
    if barrel is not None:
        extras["barrelPctVsHandMix"] = barrel

    return xera, round(covered_n / total_n, 3), extras


def _resolved_hand_counts(confirmed_lineup, pitcher_hand):
    counts = {"L": 0, "R": 0}
    unresolved = 0
    for h in confirmed_lineup or []:
        eff = resolve_effective_hand(h.get("batSide"), pitcher_hand)
        if eff in ("L", "R"):
            counts[eff] += 1
        else:
            unresolved += 1
    return counts, unresolved


def build_offense_platoon_context(g, offense_side) -> dict:
    """
    Top-level entry point. Pure function of (g, offense_side) --
    offense_side is 'away' or 'home'; the OPPOSING side's starter is
    read automatically. Returns the full platoon-context/debug object
    for that offense, including the bounded run-rate adjustment
    (`aggregatePlatoonAdvantageRPG`) callers may add directly to that
    side's projected runs.

    Never raises. Never fabricates player-level context for an
    unconfirmed lineup -- returns status=LINEUP_UNCONFIRMED with
    aggregatePlatoonAdvantageRPG=0.0 instead (Requirement 2).
    """
    opp_side = "home" if offense_side == "away" else "away"
    off_ts = (g.get(f"{offense_side}TeamStats") or {})
    opp_pitcher = (g.get(opp_side) or {}).get("pitcher") or {}
    opp_ps = (g.get(opp_side) or {}).get("pitcherSavant") or {}

    lineup_confirmed = bool(off_ts.get("lineupConfirmedOfficial"))
    confirmed_lineup = off_ts.get("confirmedLineup") or []
    handedness = lineup_handedness_composition(confirmed_lineup)
    opposing_starter_hand = classify_hand(opp_pitcher.get("pitchHand"))

    base = {
        "offenseSide": offense_side,
        "lineupConfirmed": lineup_confirmed,
        "handedness": handedness,
        "opposingStarterHand": opposing_starter_hand,
        "aggregatePlatoonAdvantageRPG": 0.0,
        "sampleThresholds": {
            "minPAHitterSplit": MIN_PA_HITTER_SPLIT,
            "minPAStarterSplit": MIN_PA_STARTER_SPLIT,
            "minLineupBattersForPlatoon": MIN_LINEUP_BATTERS_FOR_PLATOON,
        },
        "fallbacksUsed": [],
        "components": {"lineupWobaComponent": None, "starterHandComponent": None},
    }

    if not lineup_confirmed or not confirmed_lineup:
        base["status"] = STATUS_LINEUP_UNCONFIRMED
        base["hitterSplitAvailability"] = "0/0 (lineup unconfirmed)"
        base["starterSplitAvailability"] = False
        base["reason"] = "Lineup not officially confirmed — no player-level platoon context computed"
        return base

    if len(confirmed_lineup) < MIN_LINEUP_BATTERS_FOR_PLATOON:
        base["fallbacksUsed"].append(
            f"only {len(confirmed_lineup)}/{MIN_LINEUP_BATTERS_FOR_PLATOON} lineup spots resolved — "
            f"hitter-side platoon component skipped"
        )

    if opposing_starter_hand is None:
        base["status"] = STATUS_MISSING_DATA
        base["hitterSplitAvailability"] = "0/9 (opposing starter handedness unknown)"
        base["starterSplitAvailability"] = False
        base["reason"] = "Opposing starter pitchHand missing — cannot resolve any platoon matchup"
        return base

    # ── Hitter-side component ───────────────────────────────────────────
    lineup_component_rpg = None
    if len(confirmed_lineup) >= MIN_LINEUP_BATTERS_FOR_PLATOON:
        weighted_woba, n_used, n_real, n_fallback = weighted_lineup_platoon_woba(
            confirmed_lineup, opposing_starter_hand
        )
        team_baseline = off_ts.get("teamSeasonWOBA")
        base["hitterSplitAvailability"] = f"{n_real}/{len(confirmed_lineup)} real platoon splits ({n_fallback} season-wOBA fallback)"
        if weighted_woba is not None and team_baseline is not None:
            lineup_woba_delta = round(weighted_woba - team_baseline, 4)
            lineup_component_rpg = lineup_woba_delta * WOBA_TO_RPG_SCALAR
            base["components"]["lineupWobaComponent"] = {
                "weightedLineupWOBAvsHand": weighted_woba,
                "teamSeasonWOBA": team_baseline,
                "deltaWOBA": lineup_woba_delta,
                "deltaRPG": round(lineup_component_rpg, 4),
                "nHittersUsed": n_used,
            }
        elif weighted_woba is not None:
            base["fallbacksUsed"].append("teamSeasonWOBA unavailable — hitter-side component skipped")
    else:
        base["hitterSplitAvailability"] = f"0/{len(confirmed_lineup)} (below {MIN_LINEUP_BATTERS_FOR_PLATOON}-batter platoon floor)"

    # ── Starter-side component ──────────────────────────────────────────
    hand_counts, _unresolved = _resolved_hand_counts(confirmed_lineup, opposing_starter_hand)
    starter_xera, starter_coverage, starter_extras = starter_hand_mix_split(opp_ps, hand_counts)
    starter_baseline = opp_ps.get("xFIP") or opp_ps.get("seasonFIP") or opp_ps.get("xERA")
    base["starterSplitAvailability"] = starter_xera is not None
    starter_component_rpg = None
    if starter_xera is not None and starter_baseline is not None:
        # Starter xERA differential vs his own season baseline, in the
        # SAME runs-per-9-innings unit as xFIP/xERA everywhere else in
        # this codebase -- a positive delta means he is more hittable
        # against this lineup's specific handedness mix than usual,
        # which favors the offense (same sign convention as
        # lineupComponent above: positive = favors the offense).
        starter_component_rpg = round((starter_xera - starter_baseline) / 9.0 * 9.0, 4)
        base["components"]["starterHandComponent"] = {
            "starterXERAvsHandMix": starter_xera,
            "starterSeasonBaseline": starter_baseline,
            "handMixCoverage": starter_coverage,
            "deltaRPG": starter_component_rpg,
            **starter_extras,
        }
    elif starter_xera is None:
        base["fallbacksUsed"].append(
            f"opposing starter vsLHH/vsRHH split unavailable or below {MIN_PA_STARTER_SPLIT}-PA floor — "
            f"starter-side component skipped"
        )

    components_available = [c for c in (lineup_component_rpg, starter_component_rpg) if c is not None]
    if not components_available:
        base["status"] = STATUS_MISSING_DATA
        base["reason"] = "Neither hitter-side nor starter-side platoon evidence cleared its sample floor"
        return base

    raw_adj = sum(components_available) / len(components_available)
    bounded_adj = max(-PLATOON_ADJ_CAP_RPG, min(PLATOON_ADJ_CAP_RPG, raw_adj))
    base["status"] = STATUS_OK
    base["aggregatePlatoonAdvantageRPG"] = round(bounded_adj, 4)
    base["reason"] = (
        f"{len(components_available)}/2 platoon components available "
        f"(raw={round(raw_adj, 4)}, capped at ±{PLATOON_ADJ_CAP_RPG})"
    )
    return base
