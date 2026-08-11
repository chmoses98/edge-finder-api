#!/usr/bin/env python3
"""
lib/research/first_inning_context.py
=========================================
NRFI/YRFI first-inning-specific projection context.

PRIOR GAP THIS MODULE FIXES
-----------------------------
Before this module, scripts/build_market_ledger.py's NRFI/YRFI lambda
(`inning1_away`/`inning1_home`) was computed as `awayProjRuns / 9` /
`homeProjRuns / 9` -- a pure full-game-derived proxy, scaled down but
never actually first-inning-specific. `pitcherSavant.firstInningSplit`
(dedicated Statcast first-inning xERA, filtered to `hfInn=1`, already
fully implemented in api/savant.js) was read ONLY to gate confidence
tier (Rule 40: cap at PAPER when missing) -- never to move the
probability itself. Worse, no production script ever called
`/api/savant?playerIds=...&splits=true` (the only code path that
populates firstInningSplit) at all, so in every committed
data/slates/*/authoritative.json to date, firstInningSplit is absent on
every start and Rule 40's PAPER cap fires unconditionally (confirmed by
direct inspection). scripts/fetch_savant_pitchers.py now wires that
fetch in; this module is what actually uses the data once present.

WHAT "DEDICATED FIRST-INNING EVIDENCE" MEANS HERE
----------------------------------------------------
Only `pitcherSavant.firstInningSplit.firstInningXERA` (a real,
first-inning-scoped Statcast aggregate, never fabricated) is treated as
first-inning-specific pitcher evidence. Nothing here invents a "first
inning xERA" when the field is absent -- absence is reported explicitly
in the returned context's `missing` list, and the lambda for that side
falls back to the exact pre-existing generic proxy (proj/9), byte-for-
byte, so a game with no dedicated evidence produces an IDENTICAL
NRFI/YRFI probability to before this module existed.

A small secondary nudge additionally reuses
lib.research.platoon_context's already-computed offense platoon
context (top-3-weighted lineup handedness vs the opposing starter's
handedness) -- since the top of the batting order disproportionately
determines first-inning scoring, a confirmed favorable/unfavorable
platoon matchup should move the first-inning lambda a little even
though it is not itself first-inning-scoped evidence. This nudge is
capped much smaller than the dedicated-pitcher-evidence blend (see
FIRST_INNING_PLATOON_SHARE) and only ever fires off a platoon context
that is ALREADY status=OK (i.e. already passed its own sample floors in
lib.research.platoon_context) -- it never lowers the sample bar.

SCOPE / SAFETY
---------------
Every function here is pure: no file I/O, no network, no clock reads,
no printing, no mutation of any argument, deterministic given
deterministic inputs.
"""

MIN_APPEARANCES_THIN = 5     # matches api/savant.js's own `openerQualified: appearances >= 5`
MIN_APPEARANCES_ADEQUATE = 8

WEIGHT_THIN = 0.30
WEIGHT_ADEQUATE = 0.55
WEIGHT_NONE = 0.0

# Dedicated pitcher-evidence blend may move the lambda at most this
# fraction away from the generic proj/9 proxy, in either direction --
# a small-sample first-inning xERA (5-8 appearances) can be noisy, and
# this cap keeps it from ever dominating the estimate.
FIRST_INNING_ADJ_CAP_FRACTION = 0.35

# The platoon nudge (see module docstring) is capped separately and
# more tightly than the dedicated-evidence blend above, and the two
# caps are applied independently (not summed) -- the platoon nudge is
# a secondary refinement, not a second first-inning-specific signal.
FIRST_INNING_PLATOON_SHARE = 0.35
FIRST_INNING_PLATOON_ADJ_CAP_FRACTION = 0.15


def _appearance_weight(appearances):
    if not appearances or appearances < MIN_APPEARANCES_THIN:
        return WEIGHT_NONE, "none"
    if appearances < MIN_APPEARANCES_ADEQUATE:
        return WEIGHT_THIN, "thin"
    return WEIGHT_ADEQUATE, "adequate"


def _dedicated_evidence(pitcher_savant, label):
    """
    Pure extraction of one starter's dedicated first-inning evidence.
    Returns a dict describing what was found (or explicitly not found)
    -- never raises, never fabricates a value when firstInningSplit is
    absent.
    """
    fi = (pitcher_savant or {}).get("firstInningSplit") or {}
    xera = fi.get("firstInningXERA")
    appearances = fi.get("appearances")
    weight, tier = _appearance_weight(appearances)
    available = xera is not None and weight > 0
    return {
        "pitcherLabel": label,
        "firstInningXERA": xera,
        "appearances": appearances,
        "sampleTier": tier,
        "weightApplied": weight if available else 0.0,
        "available": available,
    }


def _blended_lambda(naive_lambda, evidence):
    """
    Blend the naive (full-game-derived) first-inning lambda with
    dedicated pitcher evidence, bounded to within
    ±FIRST_INNING_ADJ_CAP_FRACTION of the naive value. Returns
    (lambda, formula_str, dedicated_evidence_applied: bool).
    """
    if naive_lambda is None:
        return None, "unavailable (no game-level projection)", False

    if not evidence["available"]:
        return naive_lambda, "naive: awayOrHomeProjRuns / 9 (no dedicated first-inning evidence)", False

    w = evidence["weightApplied"]
    xera = evidence["firstInningXERA"]
    dedicated_lambda = xera / 9.0
    blended = (1 - w) * naive_lambda + w * dedicated_lambda

    lo = naive_lambda * (1 - FIRST_INNING_ADJ_CAP_FRACTION)
    hi = naive_lambda * (1 + FIRST_INNING_ADJ_CAP_FRACTION)
    bounded = max(max(0.0, lo), min(hi, blended))

    formula = (
        f"blend: {1 - w:.2f}*naive({naive_lambda:.4f}) + {w:.2f}*dedicated(xERA={xera}/9="
        f"{dedicated_lambda:.4f}), capped to ±{FIRST_INNING_ADJ_CAP_FRACTION:.0%} of naive "
        f"[{lo:.4f}, {hi:.4f}] -> {bounded:.4f}"
    )
    return round(bounded, 5), formula, True


def _platoon_nudge(lam, platoon_ctx):
    """
    Apply the small, separately-capped platoon nudge (see module
    docstring) on top of an already-computed lambda. platoon_ctx is one
    side's lib.research.platoon_context.build_offense_platoon_context()
    output -- only applied when its own status is OK (already cleared
    its sample floors). Returns (new_lambda, applied: bool, note).
    """
    if lam is None or not platoon_ctx or platoon_ctx.get("status") != "OK":
        return lam, False, None

    adj_rpg = platoon_ctx.get("aggregatePlatoonAdvantageRPG") or 0.0
    if adj_rpg == 0.0:
        return lam, False, None

    nudge = (adj_rpg * FIRST_INNING_PLATOON_SHARE) / 9.0
    cap = lam * FIRST_INNING_PLATOON_ADJ_CAP_FRACTION
    nudge = max(-cap, min(cap, nudge))
    new_lam = max(0.0, lam + nudge)
    note = (
        f"platoon nudge: aggregatePlatoonAdvantageRPG={adj_rpg:+.4f} * "
        f"{FIRST_INNING_PLATOON_SHARE} / 9 = {nudge:+.5f} (capped ±{FIRST_INNING_PLATOON_ADJ_CAP_FRACTION:.0%} of lambda)"
    )
    return round(new_lam, 5), True, note


def build_first_inning_context(g, away_proj_runs, home_proj_runs, away_platoon_ctx=None, home_platoon_ctx=None):
    """
    Top-level entry point. Returns the full firstInningContext/debug
    block for a game, including the two lambdas
    (`awayLambda1st`/`homeLambda1st`) callers should use in place of
    the naive `awayProjRuns/9`/`homeProjRuns/9` for NRFI/YRFI's Poisson
    calculation.

    away's 1st-inning lambda is driven by the HOME starter's dedicated
    first-inning evidence (away bats in the top of the 1st, facing the
    home starter) -- and vice versa for home, mirroring how
    scripts/build_market_ledger.py's compute_projections() already
    derives awayProjRuns from the HOME pitcher's quality.

    Never raises. When neither side has dedicated evidence or a
    platoon nudge available, returns lambdas byte-identical to the
    pre-existing naive proj/9 proxy -- regression-safe by construction.
    """
    away_ps = (g.get("away") or {}).get("pitcherSavant") or {}
    home_ps = (g.get("home") or {}).get("pitcherSavant") or {}

    naive_away = (away_proj_runs / 9.0) if away_proj_runs is not None else None
    naive_home = (home_proj_runs / 9.0) if home_proj_runs is not None else None

    # away's dedicated evidence source = HOME starter's firstInningSplit
    home_evidence = _dedicated_evidence(home_ps, "home_starter")
    away_evidence = _dedicated_evidence(away_ps, "away_starter")

    away_lambda, away_formula, away_dedicated_applied = _blended_lambda(naive_away, home_evidence)
    home_lambda, home_formula, home_dedicated_applied = _blended_lambda(naive_home, away_evidence)

    away_lambda, away_nudge_applied, away_nudge_note = _platoon_nudge(away_lambda, away_platoon_ctx)
    home_lambda, home_nudge_applied, home_nudge_note = _platoon_nudge(home_lambda, home_platoon_ctx)

    available = {}
    missing = []
    generic_fallbacks_used = []

    if home_evidence["available"]:
        available["homeStarterFirstInningXERA"] = home_evidence["firstInningXERA"]
    else:
        missing.append("home.pitcherSavant.firstInningSplit.firstInningXERA")
        generic_fallbacks_used.append("away lambda uses generic awayProjRuns/9 (no home-starter first-inning evidence)")

    if away_evidence["available"]:
        available["awayStarterFirstInningXERA"] = away_evidence["firstInningXERA"]
    else:
        missing.append("away.pitcherSavant.firstInningSplit.firstInningXERA")
        generic_fallbacks_used.append("home lambda uses generic homeProjRuns/9 (no away-starter first-inning evidence)")

    if away_nudge_applied:
        available["awayPlatoonNudge"] = away_nudge_note
    if home_nudge_applied:
        available["homePlatoonNudge"] = home_nudge_note
    if not away_nudge_applied:
        missing.append("away offense platoon context (top-3 handedness) not applied to 1st-inning lambda")
    if not home_nudge_applied:
        missing.append("home offense platoon context (top-3 handedness) not applied to 1st-inning lambda")

    dedicated_evidence_applied = bool(away_dedicated_applied or home_dedicated_applied)

    return {
        "available": available,
        "missing": missing,
        "genericFallbacksUsed": generic_fallbacks_used,
        "dedicatedEvidenceApplied": dedicated_evidence_applied,
        "awayLambda1st": away_lambda,
        "homeLambda1st": home_lambda,
        "awayLambdaFormula": away_formula,
        "homeLambdaFormula": home_formula,
        "sampleThresholds": {
            "minAppearancesThin": MIN_APPEARANCES_THIN,
            "minAppearancesAdequate": MIN_APPEARANCES_ADEQUATE,
            "weightThin": WEIGHT_THIN,
            "weightAdequate": WEIGHT_ADEQUATE,
            "adjCapFraction": FIRST_INNING_ADJ_CAP_FRACTION,
            "platoonAdjCapFraction": FIRST_INNING_PLATOON_ADJ_CAP_FRACTION,
        },
    }
