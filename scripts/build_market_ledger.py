#!/usr/bin/env python3
"""
scripts/build_market_ledger.py v1.0
=====================================
Converts allEdges (positive-filter) into a complete market evaluation ledger.

For every required market on every game, produces exactly one row with status:
  Accepted        — edge >= threshold, ready to bet
  Rejected        — evaluated, no qualifying edge (or gate blocked)
  Missing Data    — Kalshi price not in slate / required field null
  Evaluation Failed — unexpected error during evaluation

Written to g['marketLedger'] in data/slate.json.
Validates that every required market has a row before writing.

Required markets (11 per game):
  NRFI, YRFI, F5_ML_Away, F5_ML_Home,
  TT_Away_Over, TT_Home_Over,
  ML_Away, ML_Home,
  Game_Total, RL_Away, RL_Home

Run AFTER merge_odds.py and enrich_data.py.
"""

import json, math, sys, os
from datetime import datetime, timezone

# F5 Three-Way Pricing Correction milestone: the corrected F5 model
# probability (away/tie/home, never renormalized) is computed by the
# existing, tested, pure score-distribution engine in
# lib/research/three_way_projection.py -- imported here as a HARD
# dependency (no try/except ImportError fallback) deliberately: falling
# back silently to the legacy two-way-renormalized math on an import
# failure would be exactly the "silently revert to old behavior" this
# milestone's safety gates are designed to prevent (see
# docs/F5_THREE_WAY_PRICING.md). If this import ever fails, F5 pricing
# must fail loudly, not silently mis-price.
_ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)
from lib.research.three_way_projection import three_way_result_probs, assert_probabilities_valid

# Bullpen availability (recent workload) adjustment -- reads PR #51's
# bullpen.recentUsage block. Same hard-dependency convention as the F5
# import above: missing this module must fail loudly, not silently skip
# the adjustment and pretend every bullpen is at full season strength.
from lib.edgelab.bullpen_availability import compute_bullpen_workload_adjustment

# Confirmed-lineup handedness/platoon context (Baseball Input Data /
# Platoon Context mission) -- pure function of `g` alone, called from
# inside compute_projections()/compute_game_projection_context() below.
# See lib/research/platoon_context.py's module docstring for the prior
# gap this fixes and the bounded adjustment it applies.
from lib.research.platoon_context import build_offense_platoon_context

# First-inning-specific NRFI/YRFI projection context (same mission).
# See lib/research/first_inning_context.py's module docstring.
from lib.research.first_inning_context import (
    build_first_inning_context,
    FIRST_INNING_NATIVE,
    FIRST_INNING_PARTIAL,
    GENERIC_FALLBACK,
    INSUFFICIENT_DATA,
)

# F3/F5 tie tax / contract-structure comparison (THREE_WAY_YES vs
# PROTECTED_NO). See lib/research/f5_tie_tax.py's module docstring.
from lib.research.f5_tie_tax import evaluate_f5_tie_tax
from lib.research.expression_group import build_expression_group

# Production Fee-Aware Net EV Integration milestone: the SAME fee engine
# PR #88 built and validated for research/historical-reconciliation
# purposes (lib/edgelab/kalshi_fees.py) is now also the single source of
# truth for live recommendation qualification -- never a second,
# independently-maintained fee formula. Model probabilities, projection
# engines, and calibration factors are untouched by this import; it only
# supplies the fee-adjusted break-even / net-EV primitives consumed by
# build_edge_fields() and fee_aware_bet_up_to_price_cents() below. See
# docs/PRODUCTION_FEE_AWARE_NET_EV.md for the full writeup.
from lib.edgelab import kalshi_fees as kf

# Phase 1A: Executable price logic
try:
    from executable_price import get_executable_prices, executable_prob_from_price, check_max_bet_price
except ImportError:
    def get_executable_prices(yes_bid, yes_ask, no_bid=None, no_ask=None):
        def nc(v):
            if v is None: return None
            f = float(v)
            return f if f > 1.0 else round(f * 100, 4)
        yb, ya = nc(yes_bid), nc(yes_ask)
        nb = nc(no_bid) if no_bid is not None else (round(100 - ya, 4) if ya is not None else None)
        na = nc(no_ask) if no_ask is not None else (round(100 - yb, 4) if yb is not None else None)
        mid = round((yb + ya) / 2, 4) if (yb is not None and ya is not None) else (yb or ya)
        return {'yes_bid': yb, 'yes_ask': ya, 'no_bid': nb, 'no_ask': na,
                'yes_executable': ya, 'no_executable': na, 'mid': mid}
    def executable_prob_from_price(p): return round(p / 100.0, 6) if p is not None else None
    def check_max_bet_price(exec_p, max_p):
        if exec_p is None or max_p is None: return True, None
        return (True, None) if exec_p <= max_p else (False, 'PRICE_MOVED_BEYOND_MAX')

# Phase 1F: Reason codes
try:
    from reason_codes import build_reason_codes
except ImportError:
    def build_reason_codes(row_status, row_data): return []

# Rule 71 patch: import bet eligibility classifier
# bet_eligibility.py separates LIVE BET ELIGIBILITY from CLV/REVIEW INTEGRITY
# Missing CLV data NEVER blocks a live actionable bet.
try:
    from bet_eligibility import apply_eligibility
except ImportError:
    # Fallback: no-op if module is not found (safe — fields just won't be set)
    def apply_eligibility(row, clv_snapshot_captured=None):
        return row

# ── Constants ─────────────────────────────────────────────────────────────────
CAL_HIGH    = 0.187
CAL_MEDIUM  = 0.255
CAL_PAPER   = 0.18

THRESHOLD_HIGH   = 3.0   # calibrated edge %
THRESHOLD_MEDIUM = 1.5
THRESHOLD_PAPER  = 1.0

# Tier/confidence calibration mission: a market/model disagreement this
# large (|rawEdgeVsVF|, i.e. model probability vs the row's own
# mid-derived Kalshi VF, in percentage points) is treated as an audit
# flag, not evidence of a bigger edge -- a row that would otherwise
# qualify for HIGH ("Tier A") is capped at MEDIUM ("Tier B") until a
# human explains the gap (see the four confidence_from_edge() call
# sites' disagreement-cap checks below). This is deliberately smaller
# than the existing Rule71 (8pt, vs Pinnacle, ML only) and Rule71-F5
# (12pt, vs Kalshi VF, F5 only) HARD-REJECT thresholds -- those remain
# unchanged, hard-reject boundaries; this is a new, softer, Kalshi-VF-
# based ceiling on the tier itself, applied uniformly across ML/TT/F5/
# NRFI/YRFI (the only reference every market type already computes).
DISAGREEMENT_FLAG_PCT = 7.0

REQUIRED_MARKETS = [
    'NRFI', 'YRFI',
    'F5_ML_Away', 'F5_ML_Home',
    'TT_Away_Over', 'TT_Home_Over',
    'ML_Away', 'ML_Home',
    'Game_Total', 'RL_Away', 'RL_Home',
]

# ── Poisson helpers ────────────────────────────────────────────────────────────
def poisson_pmf(k, lam):
    if lam <= 0: return 0.0
    return (lam**k * math.exp(-lam)) / math.factorial(k)

def p_team_wins(team_proj, opp_proj, max_r=20):
    """Returns (p_win, p_push) for team_proj vs opp_proj."""
    pw = pp = 0
    for a in range(max_r + 1):
        for h in range(max_r + 1):
            p = poisson_pmf(a, team_proj) * poisson_pmf(h, opp_proj)
            if a > h: pw += p
            elif a == h: pp += p
    return pw, pp

def p_over_total(proj, line, max_r=30):
    """P(combined total > line) where line is an integer (Kalshi style)."""
    return sum(poisson_pmf(r, proj) for r in range(int(line) + 1, max_r + 1))

def vig_free_2way(a_american, h_american):
    """Return (vf_away, vf_home) vig-free from two American odds."""
    def imp(o):
        if o is None: return None
        return abs(o) / (abs(o) + 100) if o < 0 else 100 / (o + 100)
    ia, ih = imp(a_american), imp(h_american)
    if ia is None or ih is None: return None, None
    tot = ia + ih
    if tot == 0: return None, None
    return ia / tot, ih / tot

def vig_free_1way(american, comp_american):
    """VF for a one-sided market (e.g. NRFI) given YES and NO prices."""
    vfa, vfb = vig_free_2way(american, comp_american)
    return vfa

def vig_free_3way(a_american, t_american, h_american):
    """
    F5 Three-Way Pricing Correction milestone. Return (vf_away, vf_tie,
    vf_home) vig-free from THREE American odds -- the genuine market-side
    counterpart to three_way_result_probs() on the model side. Kalshi's
    F5 market has a real, separately tradable TIE contract (confirmed via
    a real market snapshot -- see docs/F5_THREE_WAY_PRICING.md), so
    vig-free normalization must span all three sides, exactly like
    vig_free_2way() spans both sides for a genuine two-way market. Using
    vig_free_2way() on just away/home for a three-way market silently
    discards the tie contract's own price and systematically overstates
    both team-side market-implied probabilities -- the market-side twin
    of the model-side renormalization bug this milestone fixes.

    Returns (None, None, None) if any of the three prices is missing --
    deliberately never falls back to a two-way calculation on partial
    data (see validate_f5_three_way() below, which routes a missing tie
    price to a loud Missing-Data / safety-gate failure rather than a
    silent two-way approximation).
    """
    def imp(o):
        if o is None: return None
        return abs(o) / (abs(o) + 100) if o < 0 else 100 / (o + 100)
    ia, it, ih = imp(a_american), imp(t_american), imp(h_american)
    if ia is None or it is None or ih is None: return None, None, None
    tot = ia + it + ih
    if tot == 0: return None, None, None
    return ia / tot, it / tot, ih / tot


# ── F5 Three-Way Pricing Correction: versioning, provenance, safety gates ──────
# See docs/F5_THREE_WAY_PRICING.md for the full root-cause writeup.
F5_PRICING_VERSION_LEGACY_TWO_WAY = "f5_two_way_renormalized_v0"
F5_PRICING_VERSION_THREE_WAY = "f5_three_way_v1"
# Current production F5 pricing version -- every F5_ML_Away/F5_ML_Home row
# this script writes carries this exact string in f5PricingVersion, so a
# ModelEvaluation record (or any historical bets.json/report row) can
# always distinguish legacy two-way-renormalized F5 pricing from the
# corrected three-way pricing without ambiguity, regardless of when it
# was written.
F5_PRICING_VERSION_CURRENT = F5_PRICING_VERSION_THREE_WAY


class F5PricingError(Exception):
    """
    Raised by validate_f5_three_way() when an F5 three-way market cannot
    be safely priced. Deliberately a distinct, specific exception (not a
    bare ValueError/AssertionError) so callers can tell an F5-specific
    safety-gate failure apart from any other evaluation error -- and so
    it is never accidentally caught by a broad `except Exception` further
    up the call stack without at least being logged as exactly what it
    is. Per this milestone's explicit requirement: fail loudly rather
    than silently reverting to two-way pricing.
    """


def validate_f5_three_way(p_away, p_tie, p_home, away_ticker, tie_ticker, home_ticker,
                           away_american, tie_american, home_american, tolerance=1e-6):
    """
    F5 three-way pricing safety gates (Production Reliability milestone
    item 11). Raises F5PricingError with a specific message on the first
    violation found; returns None (no return value needed) if every gate
    passes. Never silently corrects or falls back -- every violation is a
    hard stop.

    Gates enforced:
      1. away + tie + home model probabilities sum to 1 within tolerance.
      2. Tie's Kalshi price (american) is present whenever away/home
         prices are present -- a three-way F5 market missing its tie
         price is a structural problem, not a market this code
         silently routes through two-way normalization to "fill the gap."
      3. No two of the three contract tickers are identical (a genuinely
         malformed registry entry, never priced as three distinct sides).
    """
    total = p_away + p_tie + p_home
    if abs(total - 1.0) > tolerance:
        raise F5PricingError(
            f"F5 three-way model probabilities sum to {total!r}, not 1 "
            f"(away={p_away!r}, tie={p_tie!r}, home={p_home!r}, tolerance={tolerance})"
        )
    for name, v in (("away", p_away), ("tie", p_tie), ("home", p_home)):
        if v < -tolerance or v > 1 + tolerance:
            raise F5PricingError(f"F5 three-way model probability '{name}'={v!r} out of [0, 1] range")

    if (away_american is not None or home_american is not None) and tie_american is None:
        raise F5PricingError(
            "F5 market has away/home prices but no tie price -- a confirmed three-way "
            "F5 market missing its tie price must not be silently priced as two-way"
        )

    tickers = [t for t in (away_ticker, tie_ticker, home_ticker) if t]
    if len(tickers) != len(set(tickers)):
        raise F5PricingError(
            f"F5 three-way market has duplicate contract ticker(s): "
            f"away={away_ticker!r} tie={tie_ticker!r} home={home_ticker!r}"
        )


def contract_pricing(model_prob, market_vf_prob, yes_ask_cents):
    """
    F5 Three-Way Pricing Correction milestone (item 4): the uniform
    per-contract pricing block -- modelFairProbability, modelFairPrice,
    marketImpliedProbability, estimatedEdge, expectedValuePerDollar --
    used identically for the Away, Tie, and Home contracts so none of
    the three is computed by a bespoke, possibly-inconsistent formula.

    marketImpliedProbability uses the vig-free (mid-price-derived)
    probability -- the same convention `kalshiVF`/`marketProbVF` already
    use elsewhere in this file -- NOT the executable ask price; the ask
    price is used only for expectedValuePerDollar (the actual cost to
    enter), matching this file's existing executable-price convention
    (scripts/executable_price.py) unchanged.
    """
    model_fair_price = round(model_prob * 100, 2) if model_prob is not None else None
    market_implied_pct = round(market_vf_prob * 100, 2) if market_vf_prob is not None else None
    estimated_edge = raw_edge_pct(model_prob, market_vf_prob)
    ev_per_dollar = None
    if model_prob is not None and yes_ask_cents is not None and yes_ask_cents > 0:
        ev_per_dollar = round(model_prob / (yes_ask_cents / 100.0) - 1.0, 4)
    # Production Fee-Aware Net EV Integration milestone (additive,
    # informational -- this block is display-only for the F5 tie
    # contract and is not itself used for real-money qualification, see
    # build_edge_fields() for the field that is): the SAME fee engine,
    # exposed here too so the tie contract's card is never fee-blind
    # while its away/home siblings are fee-aware.
    exec_prob = round(yes_ask_cents / 100.0, 6) if yes_ask_cents is not None and yes_ask_cents > 0 else None
    net_ev_per_dollar = (
        kf.net_expected_value_per_dollar(model_prob, exec_prob, fee_type=kf.FEE_TYPE_TAKER)
        if model_prob is not None and exec_prob is not None else None
    )
    return {
        'modelFairProbability': round(model_prob * 100, 2) if model_prob is not None else None,
        'modelFairPrice': model_fair_price,
        'marketImpliedProbability': market_implied_pct,
        'estimatedEdge': estimated_edge,
        'expectedValuePerDollar': ev_per_dollar,
        'netExpectedValuePerDollar': net_ev_per_dollar,
    }

def american_to_ask_cents(prices_dict, american):
    """
    Executable YES-ask price in cents, preferring a real registry yes_ask
    (prices_dict['yes_ask']) and falling back to an implied-probability
    derivation from the American mid-price odds when no real ask is
    present -- the SAME fallback scripts/build_market_ledger.py's F5
    Away/Home evaluation has always used (prices_dict is empty in
    practice today since merge_odds.py does not currently pass the
    'prices' sub-block through for F5 -- a separate, pre-existing gap
    documented in docs/F5_THREE_WAY_PRICING.md, not fixed by this
    milestone since it is unrelated to the renormalization bug).
    """
    if prices_dict:
        v = prices_dict.get('yes_ask')
        if v is not None:
            f = float(v)
            return round(f * 100 if f <= 1.0 else f, 2)
    if american is None:
        return None
    imp = abs(american) / (abs(american) + 100) if american < 0 else 100 / (american + 100)
    return round(imp * 100, 2)

def calibrated_edge(model_prob, kalshi_vf, cal_factor):
    """Legacy function kept for backward compat. Returns calibrated edge %."""
    if model_prob is None or kalshi_vf is None: return None
    raw = model_prob - kalshi_vf
    return round(raw * cal_factor * 100, 3)  # in percent

def raw_edge_pct(model_prob, market_prob):
    """Raw edge as percent (no calibration)."""
    if model_prob is None or market_prob is None: return None
    return round((model_prob - market_prob) * 100, 3)

def build_edge_fields(model_prob, kalshi_vf, yes_ask_cents, cal_factor, snapshot_ts=None, *, series_ticker=None):
    """
    Phase 1C: Build all edge fields for a market row.

    Args:
        model_prob:      model probability (0-1)
        kalshi_vf:       Kalshi VF probability (0-1) = mid-price based
        yes_ask_cents:   executable price for YES bet (0-100 cents)
        cal_factor:      calibration factor
        snapshot_ts:     price snapshot timestamp
        series_ticker:   Kalshi series ticker (e.g. 'KXMLBGAME') for
                          fee-metadata provenance -- see the "Production
                          fee-aware net EV" block below.

    Returns:
        dict with all edge fields

    ============================================================
    PRODUCTION FEE-AWARE NET EV INTEGRATION (see docs/
    PRODUCTION_FEE_AWARE_NET_EV.md for the full writeup): this function
    is the SINGLE choke point every market family (ML/TT/F5/NRFI/YRFI)
    calls to build its edge fields, so the fee-aware net-EV fields below
    are computed here exactly once and never duplicated per market.

    `calibratedEdgeVsExecutable`/`edge` remain EXACTLY what they always
    meant -- the GROSS (pre-fee) calibrated edge -- untouched, still
    computed first, still returned unchanged, so every existing
    consumer of those two fields keeps seeing identical values. The new
    fields below are purely additive:

    - `feeAdjustedBreakEvenProbability`: the executable-price-space
      probability at which this contract has zero NET EV after the
      entry fee (lib.edgelab.kalshi_fees.fee_adjusted_break_even_probability,
      the SAME function PR #88's research reports and reusable net-EV
      helpers already use -- no second fee formula).
    - `expectedFeeDrag`: the fee cost expressed in the SAME raw
      (uncalibrated) edge-percentage-point units as `rawEdgeVsExecutable`
      -- i.e. `(feeAdjustedBreakEvenProbability - executableMarketProb) * 100`.
      This is NOT the ROI-space "fee-only drag" research reports use
      (lib.edgelab.kalshi_fees.fee_only_drag_percentage_points) -- that
      is a different unit space (return-per-dollar), appropriate for
      ROI reporting; production's whole existing architecture (edge,
      calibration, thresholds, bet-up-to) is probability/edge-space, so
      the break-even-probability-shift formulation is the correct,
      consistent choice here, not a second approximation of the same
      idea.
    - `netExecutableEdge`: `(rawEdgeVsExecutable - expectedFeeDrag) *
      cal_factor` -- i.e. the exact SAME calibration methodology already
      applied to `calibratedEdgeVsExecutable`, just measured against the
      fee-adjusted break-even instead of the raw executable price. This
      is the new PRIMARY qualification metric (see
      `edgeUsedForQualification` below) -- a high gross edge with high
      fee drag naturally produces a lower net edge fed into the exact
      same threshold/tier logic, with no separate fee-vs-calibration
      double-counting: the fee shifts the reference PRICE once, then the
      SAME calibration factor is applied consistently to both gross and
      net so they remain on a comparable, consistently-shrunk scale.
    - `netExpectedValuePerDollar`: `lib.edgelab.kalshi_fees.net_expected_value_per_dollar`
      called directly (no local reimplementation) -- expected net profit
      per $1 nominally staked, after fees, using the exact same
      continuous/scale-invariant exposure PR #88's Tier B ("fee-only")
      research metric uses. Deliberately scale-invariant (independent of
      the eventual stake, which is not yet known at this point in the
      pipeline -- bet_size() runs after tier assignment) -- this avoids
      a circular dependency where the fee would depend on a stake that
      itself depends on the fee-adjusted tier.
    - `feeType`/`feeMultiplier`/`feeSource`/`feeScheduleVersion`: fee
      provenance, from `lib.edgelab.kalshi_fees.fee_rule_for_series` when
      `series_ticker` is given (else the standard taker default) --
      never silently zero. No MLB series in this repo has ANY evidence
      of a non-standard multiplier (see kalshi_fees.py's module
      docstring), so the math above always uses the standard 0.07 taker
      rate today; the metadata plumbing exists so a real per-series
      override (once authenticated API access exists) flows through
      without a code change.

    `edgeUsedForQualification` is updated to `'netExecutableEdge'` --
    this field has always existed purely as a self-documenting label of
    "which field actually gates real money"; updating its VALUE (not
    role) is exactly what it exists for, and is the intended effect of
    this milestone. `edgeUsedForDisplay` stays `'calibratedEdgeVsExecutable'`
    (gross) deliberately -- gross edge remains the primary human-facing
    number, with net edge and fee drag shown alongside it (spec section
    8's "Gross edge +6.0pp / Fee drag 3.1pp / Net executable edge
    +2.9pp" pattern), never silently replacing it.
    """
    exec_prob = executable_prob_from_price(yes_ask_cents) if yes_ask_cents is not None else kalshi_vf

    raw_vs_vf   = raw_edge_pct(model_prob, kalshi_vf)
    raw_vs_exec = raw_edge_pct(model_prob, exec_prob)
    cal_vs_vf   = round(raw_vs_vf * cal_factor, 3) if raw_vs_vf is not None else None
    cal_vs_exec = round(raw_vs_exec * cal_factor, 3) if raw_vs_exec is not None else None

    fee_meta = kf.fee_rule_for_series(series_ticker) if series_ticker else {
        'seriesTicker': None, 'feeType': 'quadratic',
        'feeMultiplier': kf.FEE_MULTIPLIER_TAKER_STANDARD,
        'feeEffectiveAt': None, 'feeRuleConfidence': 'NO_SERIES_TICKER_PROVIDED',
    }
    fee_multiplier = fee_meta['feeMultiplier']

    fee_adjusted_break_even = None
    expected_fee_drag = None
    net_raw_edge = None
    net_executable_edge = None
    net_ev_per_dollar = None
    if exec_prob is not None and 0 < exec_prob < 1:
        # FAIL-CLOSED FEE BEHAVIOR (spec section 37): a valid executable
        # price always yields a defensible fee estimate (the documented
        # standard-taker fallback, never a silent zero) -- there is no
        # "unresolved" branch here because every valid price has a
        # well-defined fee under the documented fee schedule. The only
        # way fee fields end up None is an invalid/missing executable
        # price, in which case gross edge fields are already None too
        # and no qualification decision is possible regardless.
        fee_adjusted_break_even = kf.fee_adjusted_break_even_probability(
            exec_prob, fee_type=kf.FEE_TYPE_TAKER, multiplier=fee_multiplier)
        expected_fee_drag = round((fee_adjusted_break_even - exec_prob) * 100, 3)
        if raw_vs_exec is not None:
            net_raw_edge = round(raw_vs_exec - expected_fee_drag, 3)
            net_executable_edge = round(net_raw_edge * cal_factor, 3)
        if model_prob is not None:
            net_ev_per_dollar = kf.net_expected_value_per_dollar(
                model_prob, exec_prob, fee_type=kf.FEE_TYPE_TAKER, multiplier=fee_multiplier)

    # Reference-allocation dollar decomposition (spec section 13/30): the
    # actual stake ISN'T known yet at this point in the pipeline (betSize
    # is a bankroll-UNIT multiplier assigned later by bet_size(), not a
    # dollar amount -- see docs/PRODUCTION_FEE_AWARE_NET_EV.md's "stake
    # sizing" section for why this file has never had a dollar-allocation
    # concept to attach a real contractPrincipal/actualCashConsumed to).
    # Rather than fabricate a fake dollar stake, this uses the SAME
    # standardized reference allocation PR #88's research reports already
    # use (kf.DEFAULT_RESEARCH_ORDER_SIZE = $10), clearly labeled as
    # illustrative -- a concrete, correctly-computed worked example of
    # what this contract's fee economics look like at a normal retail
    # size, for downstream/manual-analysis display only, never a claim
    # about the actual bet size.
    reference_allocation = kf.DEFAULT_RESEARCH_ORDER_SIZE
    ref_sim = (
        kf.simulate_order(
            reference_allocation, exec_prob, quantity_granularity=kf.QUANTITY_GRANULARITY_UNKNOWN,
            fee_type=kf.FEE_TYPE_TAKER, multiplier=fee_multiplier,
        )
        if exec_prob is not None and 0 < exec_prob < 1 else None
    )

    return {
        'marketProbVF':               round(kalshi_vf * 100, 3) if kalshi_vf is not None else None,
        'executablePriceUsed':        yes_ask_cents,
        'executableMarketProb':       round(exec_prob * 100, 3) if exec_prob is not None else None,
        'rawEdgeVsVF':                raw_vs_vf,
        'rawEdgeVsExecutable':        raw_vs_exec,
        'calibrationFactor':          cal_factor,
        'calibratedEdgeVsVF':         cal_vs_vf,
        'calibratedEdgeVsExecutable': cal_vs_exec,
        # Production fee-aware net EV integration (additive) --
        'feeAdjustedBreakEvenProbability': (
            round(fee_adjusted_break_even * 100, 3) if fee_adjusted_break_even is not None else None
        ),
        'expectedFeeDrag':            expected_fee_drag,
        'netRawExecutableEdge':       net_raw_edge,
        'netExecutableEdge':          net_executable_edge,
        'netExpectedValuePerDollar':  net_ev_per_dollar,
        'feeType':                    fee_meta['feeType'],
        'feeMultiplier':              fee_multiplier,
        'feeSource':                  fee_meta['feeRuleConfidence'],
        'feeScheduleVersion':         kf.FEE_SCHEDULE_VERSION,
        # Illustrative-only reference-allocation decomposition -- see the
        # comment above ref_sim. NEVER the actual bet's dollar stake.
        'referenceAllocationDollars':             reference_allocation if ref_sim else None,
        'referenceAllocationContracts':           ref_sim['contracts'] if ref_sim else None,
        'referenceAllocationContractPrincipal':   ref_sim['contractPrincipal'] if ref_sim else None,
        'referenceAllocationExpectedEntryFee':    ref_sim['entryFee'] if ref_sim else None,
        'referenceAllocationExpectedCashConsumed': ref_sim['actualCashConsumed'] if ref_sim else None,
        'referenceAllocationExpectedUnusedCash':  ref_sim['unusedCash'] if ref_sim else None,
        'referenceAllocationQuantityGranularity': ref_sim['quantityGranularity'] if ref_sim else None,
        'edgeUsedForQualification':   'netExecutableEdge',
        'edgeUsedForDisplay':         'calibratedEdgeVsExecutable',
        # Legacy 'edge' field = calibratedEdgeVsExecutable for backward compat
        'edge':                       cal_vs_exec,
        'priceSnapshotTimestamp':     snapshot_ts,
        # CLV scaffold (Phase 1D): filled at betting/settlement time
        'modelSnapshotPrice':         yes_ask_cents,
        'executablePriceAtOutput':    yes_ask_cents,
        'actualEntryPrice':           None,
        'closingPrice':               None,
        'clvVsSnapshot':              None,
        'clvVsExecutableOutput':      None,
        'clvVsActualEntry':           None,
    }

def confidence_from_edge(edge_pct, f5_amplified=False):
    if edge_pct is None: return None
    threshold = THRESHOLD_PAPER if not f5_amplified else 1.0
    if edge_pct < threshold: return None  # below floor
    if edge_pct >= THRESHOLD_HIGH: return 'HIGH'
    if edge_pct >= THRESHOLD_MEDIUM: return 'MEDIUM'
    return 'PAPER'

def cap_tier_for_disagreement(conf, raw_disagreement_pct, gates):
    """
    Tier/confidence calibration mission: a large, unexplained market/
    model disagreement must act as an audit/downgrade flag, never an
    automatic confidence booster -- so a row that already qualified for
    HIGH ("Tier A") off its own edge is capped at MEDIUM ("Tier B")
    here whenever |raw_disagreement_pct| exceeds DISAGREEMENT_FLAG_PCT.
    Never touches a row that is already MEDIUM/PAPER/None -- this is a
    ceiling on HIGH specifically, not a general re-scoring, and it never
    raises a tier, only ever lowers one.

    `gates` is the caller's own gatesFired list for this row; mutated
    in place (appended to) exactly like every other inline rule check
    in this file (Rule50-53, Rule71, Rule71-F5) -- callers pass their
    own `gates`/`gates_away`/`gates_nrfi` list directly.

    Returns the (possibly downgraded) conf. `raw_disagreement_pct` of
    None (no VF reference available) never caps anything -- this check
    only ever fires when the disagreement is actually known, never
    guessed.
    """
    if conf != 'HIGH' or raw_disagreement_pct is None:
        return conf
    if abs(raw_disagreement_pct) <= DISAGREEMENT_FLAG_PCT:
        return conf
    gates.append(
        f'Tier cap: model/market disagreement {abs(raw_disagreement_pct):.1f}pt '
        f'> {DISAGREEMENT_FLAG_PCT:.0f}pt -- Tier A withheld pending explanation'
    )
    return 'MEDIUM'


def cap_tier_for_first_inning_evidence_quality(conf, evidence_quality, gates):
    """
    First-inning evidence-quality provenance hierarchy (see
    lib.research.first_inning_context: FIRST_INNING_NATIVE/PARTIAL/
    GENERIC_FALLBACK/INSUFFICIENT_DATA). Confidence must reflect how much
    of the NRFI/YRFI lambda actually came from dedicated first-inning
    pitcher evidence, not merely whether Rule 40's raw xERA-presence check
    passed -- a too-thin appearance sample (<5 starts) is GENERIC_FALLBACK
    even when the xERA field itself is populated, a case Rule 40's binary
    presence check cannot see.

    Ceiling-only, mirrors cap_tier_for_disagreement()'s discipline: never
    raises a tier, only ever lowers one, and is a no-op on a row that
    already has no confidence (None).

    GENERIC_FALLBACK -> capped at PAPER (at least one side's lambda is the
    naive proj/9 proxy; may still be useful for research but must not
    generate an exaggerated STRONG/actionable recommendation).
    FIRST_INNING_PARTIAL -> HIGH capped at MEDIUM (both sides have
    dedicated evidence, but at least one is thin-sample; reduced, not
    absent, evidence quality).
    FIRST_INNING_NATIVE -> no cap.
    INSUFFICIENT_DATA is handled upstream as a hard block before this
    function is reached for that state.
    """
    if conf is None:
        return conf
    if evidence_quality == GENERIC_FALLBACK and conf != 'PAPER':
        gates.append(
            'First-inning evidence quality: GENERIC_FALLBACK (no first-inning-specific '
            'evidence for at least one starter, or sample below the thin-sample floor) '
            '-- capped at PAPER'
        )
        return 'PAPER'
    if evidence_quality == FIRST_INNING_PARTIAL and conf == 'HIGH':
        gates.append(
            'First-inning evidence quality: FIRST_INNING_PARTIAL (thin-sample dedicated '
            'first-inning evidence for at least one starter) -- Tier A withheld, capped at MEDIUM'
        )
        return 'MEDIUM'
    return conf

def bet_up_to_price_cents(fair_prob, threshold_pct, cal_factor):
    """
    Executable EV / bet-up-to correctness: the genuine bet-up-to ceiling
    a market's own edge requirement implies -- the WORST (highest) YES
    executable price, in cents, at which this contract's calibrated
    edge still clears `threshold_pct`. Never an echo of "whatever price
    happened to be observed when the row was built" (that was the
    pre-existing gap: every call site in this file set maxBetPrice to
    the current executablePriceUsed itself, so check_max_bet_price()
    against it was always trivially true -- a hard ceiling has to be
    DERIVED from the model's own edge requirement, not from the price
    it's supposed to be checking).

    Inverts calibrated_edge() above:
        calibrated_edge_pct = (fair_prob - kalshi_vf) * cal_factor * 100
    Solved for kalshi_vf at calibrated_edge_pct == threshold_pct:
        kalshi_vf_ceiling = fair_prob - threshold_pct / (cal_factor * 100)
    Returned as a 0-100 cents price (kalshi_vf_ceiling * 100), the same
    scale check_max_bet_price()/executablePriceUsed already use.

    Args:
        fair_prob:     model fair probability, 0-1 (never the market's
                       own implied probability -- this is the ceiling
                       the MODEL is willing to pay, independent of
                       whatever price is currently observed).
        threshold_pct: the minimum calibrated-edge percentage-point
                       floor a bet must clear to qualify (e.g.
                       THRESHOLD_PAPER above).
        cal_factor:    the same calibration factor used for the row's
                       own calibrated_edge()/build_edge_fields() call
                       (e.g. CAL_MEDIUM) -- never a different one, so
                       the ceiling and the edge it gates are always
                       mutually consistent.

    Returns None (never fabricated) if fair_prob/threshold_pct is
    missing or cal_factor is zero/None (degenerate, would divide by
    zero) -- callers must not enforce a bet-up-to ceiling they cannot
    genuinely compute.
    """
    if fair_prob is None or threshold_pct is None or not cal_factor:
        return None
    kalshi_vf_ceiling = fair_prob - threshold_pct / (cal_factor * 100.0)
    return round(kalshi_vf_ceiling * 100, 2)

def fee_aware_bet_up_to_price_cents(fair_prob, threshold_pct, cal_factor, *, fee_multiplier=None):
    """
    Production Fee-Aware Net EV Integration milestone: the fee-aware
    counterpart to bet_up_to_price_cents() above -- the highest
    executable YES price at which the wager still clears `threshold_pct`
    of NET (fee-adjusted) calibrated edge, never a naive "subtract a
    cent or two" adjustment (spec section 10's explicit requirement).

    Solves the exact same threshold equation bet_up_to_price_cents()
    solves -- `(fair_prob - referencePrice) * cal_factor * 100 ==
    threshold_pct` -- but for `referencePrice` equal to the FEE-ADJUSTED
    break-even at the ceiling price P (i.e.
    fee_adjusted_break_even_probability(P) == target_prob) rather than P
    itself. Since fee_adjusted_break_even_probability(P) = P + f(P) >= P
    for any valid P (the fee is never negative), the resulting ceiling
    is always <= bet_up_to_price_cents()'s gross ceiling whenever the fee
    is nonzero -- proven algebraically, and pinned down by
    test_net_bet_up_to_never_exceeds_gross_bet_up_to.

    Delegates the actual fee-aware price inversion to
    lib.edgelab.kalshi_fees.fee_adjusted_bet_up_to_price -- the SAME
    function research's reusable net-EV helpers use, never a second fee
    formula. `threshold_pct`/`cal_factor` (production-specific
    calibration/threshold algebra) stay local to this file, exactly like
    bet_up_to_price_cents() -- lib/edgelab/kalshi_fees.py owns fee math
    only, not this file's calibration conventions.

    Returns None (never fabricated) under the same degenerate conditions
    bet_up_to_price_cents() does, or when no price at all clears the
    fee-adjusted bar.
    """
    if fair_prob is None or threshold_pct is None or not cal_factor:
        return None
    target_prob = fair_prob - threshold_pct / (cal_factor * 100.0)
    if target_prob <= 0:
        return None
    price = kf.fee_adjusted_bet_up_to_price(
        target_prob, fee_type=kf.FEE_TYPE_TAKER, multiplier=fee_multiplier)
    if price is None:
        return None
    return round(price * 100, 2)

def enforce_bet_up_to(model_p, exec_price_cents, conf, gates,
                       threshold=THRESHOLD_PAPER, cal_factor=CAL_MEDIUM, *, fee_multiplier=None):
    """
    Executable EV / bet-up-to correctness: hard ceiling enforcement.

    Wires the previously dead-code check_max_bet_price() (imported above,
    never called anywhere before this) against a REAL, model-derived
    ceiling from bet_up_to_price_cents() -- never an echo of the current
    executable price, which is what every call site in this file did
    before (e.g. `max_bet = ef.get('executablePriceUsed')`), making the
    "check" trivially always-true.

    PRODUCTION FEE-AWARE NET EV INTEGRATION: this function now computes
    and returns BOTH ceilings -- `betUpToPriceGross` (the original,
    unchanged formula) purely for display/backward-compat, and
    `betUpToPriceNet` (fee_aware_bet_up_to_price_cents(), see its
    docstring) which is the ceiling ACTUALLY ENFORCED against
    `exec_price_cents` going forward. This keeps the enforcement gate
    consistent with confidence_from_edge()'s own decision metric at
    every call site in this file (both now net-edge-based) -- enforcing
    the OLD gross ceiling here while gating tier assignment on the NEW
    net edge would be exactly the kind of two-different-metrics
    inconsistency spec section 33 ("apply fee exactly once") forbids.

    If the executable price this row was built with is already worse
    than the fee-aware price ceiling the model's own net-edge
    requirement implies, the row is force-downgraded to non-actionable
    (conf=None) right here -- never silently widened to keep a
    stale/moved-price row Accepted. A PRICE_MOVED_BEYOND_MAX-tagged
    message is appended to gatesFired so scripts/reason_codes.py's
    existing pattern match (which already looks for this exact
    substring) fires correctly.

    A conf that is already None (rejected for some other reason) or a
    missing exec_price_cents/max_bet_price (nothing to check) pass
    through unchanged except for the computed ceilings, which are always
    returned so they can be recorded on the row for later re-checks
    against a freshly-fetched price (e.g. at execution time).

    Returns (conf, gates, max_bet_price_gross_cents, max_bet_price_net_cents).
    """
    max_bet_price_gross = bet_up_to_price_cents(model_p, threshold, cal_factor)
    max_bet_price_net = fee_aware_bet_up_to_price_cents(
        model_p, threshold, cal_factor, fee_multiplier=fee_multiplier)
    max_bet_price = max_bet_price_net if max_bet_price_net is not None else max_bet_price_gross
    if conf is None or max_bet_price is None or exec_price_cents is None:
        return conf, gates, max_bet_price_gross, max_bet_price_net
    ok, reason = check_max_bet_price(exec_price_cents, max_bet_price)
    if ok:
        return conf, gates, max_bet_price_gross, max_bet_price_net
    new_gates = list(gates) + [
        f'Executable EV: price {exec_price_cents}¢ exceeds fee-aware bet-up-to '
        f'{max_bet_price}¢ (gross ceiling was {max_bet_price_gross}¢) -- {reason}'
    ]
    return None, new_gates, max_bet_price_gross, max_bet_price_net

# ── Row builder ────────────────────────────────────────────────────────────────
def make_row(market, **kwargs):
    """Base row structure. Caller fills in status and relevant fields.
    
    Phase 1 additions:
      executablePriceUsed      — yes_ask for YES bets, no_ask for NO bets (cents 0-100)
      executableMarketProb     — probability derived from executablePriceUsed
      rawEdgeVsVF              — modelProb - marketProbVF (no calibration)
      rawEdgeVsExecutable      — modelProb - executableMarketProb (no calibration)
      calibrationFactor        — calibration multiplier applied
      calibratedEdgeVsVF       — rawEdgeVsVF * calibrationFactor * 100 (percent)
      calibratedEdgeVsExecutable — rawEdgeVsExecutable * calibrationFactor * 100 (percent)
      edgeUsedForQualification — which edge field gates real-money
      edgeUsedForDisplay       — which edge field to show in output
      maxBetPrice              — maximum acceptable executable price (cents); reject if worse
      priceSnapshotTimestamp   — when this price was captured
      reasonCodes              — list of structured reason codes
      
      CLV fields (Phase 1D):
      modelSnapshotPrice       — model's price at analysis time (cents)
      executablePriceAtOutput  — executable price when slip was generated (cents)
      actualEntryPrice         — filled by user after bet placed (null until then)
      closingPrice             — null until settlement
      clvVsSnapshot            — CLV vs model snapshot (null until settlement)
      clvVsExecutableOutput    — CLV vs executable price at output (null until settlement)
      clvVsActualEntry         — CLV vs actual entry price (null until settlement)
    """
    row = {
        'market':             market,
        'status':             kwargs.get('status', 'Evaluation Failed'),
        'kalshiPrice':        kwargs.get('kalshiPrice'),
        'kalshiImplied':      kwargs.get('kalshiImplied'),
        'kalshiVF':           kwargs.get('kalshiVF'),
        'pinnacleVF':         kwargs.get('pinnacleVF'),
        'modelProb':          kwargs.get('modelProb'),
        # Phase 1C: Raw vs calibrated edge transparency
        'marketProbVF':                kwargs.get('marketProbVF'),
        'executablePriceUsed':         kwargs.get('executablePriceUsed'),
        'executableMarketProb':        kwargs.get('executableMarketProb'),
        'rawEdgeVsVF':                 kwargs.get('rawEdgeVsVF'),
        'rawEdgeVsExecutable':         kwargs.get('rawEdgeVsExecutable'),
        'calibrationFactor':           kwargs.get('calibrationFactor'),
        'calibratedEdgeVsVF':          kwargs.get('calibratedEdgeVsVF'),
        'calibratedEdgeVsExecutable':  kwargs.get('calibratedEdgeVsExecutable'),
        # Production Fee-Aware Net EV Integration milestone (additive) --
        # see build_edge_fields()'s docstring for exact definitions.
        'feeAdjustedBreakEvenProbability': kwargs.get('feeAdjustedBreakEvenProbability'),
        'expectedFeeDrag':             kwargs.get('expectedFeeDrag'),
        'netRawExecutableEdge':        kwargs.get('netRawExecutableEdge'),
        'netExecutableEdge':           kwargs.get('netExecutableEdge'),
        'netExpectedValuePerDollar':   kwargs.get('netExpectedValuePerDollar'),
        'feeType':                     kwargs.get('feeType'),
        'feeMultiplier':               kwargs.get('feeMultiplier'),
        'feeSource':                   kwargs.get('feeSource'),
        'feeScheduleVersion':          kwargs.get('feeScheduleVersion'),
        'betUpToPriceGross':           kwargs.get('betUpToPriceGross'),
        'betUpToPriceNet':             kwargs.get('betUpToPriceNet'),
        # Illustrative-only reference-allocation decomposition (see
        # build_edge_fields()'s docstring) -- never the actual bet stake.
        'referenceAllocationDollars':              kwargs.get('referenceAllocationDollars'),
        'referenceAllocationContracts':            kwargs.get('referenceAllocationContracts'),
        'referenceAllocationContractPrincipal':    kwargs.get('referenceAllocationContractPrincipal'),
        'referenceAllocationExpectedEntryFee':     kwargs.get('referenceAllocationExpectedEntryFee'),
        'referenceAllocationExpectedCashConsumed': kwargs.get('referenceAllocationExpectedCashConsumed'),
        'referenceAllocationExpectedUnusedCash':   kwargs.get('referenceAllocationExpectedUnusedCash'),
        'referenceAllocationQuantityGranularity':  kwargs.get('referenceAllocationQuantityGranularity'),
        'edgeUsedForQualification':    kwargs.get('edgeUsedForQualification', 'calibratedEdgeVsExecutable'),
        'edgeUsedForDisplay':          kwargs.get('edgeUsedForDisplay', 'calibratedEdgeVsExecutable'),
        # Legacy: keep 'edge' = calibratedEdgeVsExecutable for backward compat
        'edge':               kwargs.get('edge'),
        'confidence':         kwargs.get('confidence'),
        'confidenceTier':     kwargs.get('confidenceTier'),
        'confidenceReasons':  kwargs.get('confidenceReasons', []),
        'betSize':            kwargs.get('betSize'),
        # Phase 1A: max bet price -- now the NET (fee-aware) ceiling, the
        # one actually enforced; betUpToPriceGross/betUpToPriceNet above
        # preserve both explicitly.
        'maxBetPrice':        kwargs.get('maxBetPrice'),
        'priceSnapshotTimestamp': kwargs.get('priceSnapshotTimestamp'),
        # Phase 1D: CLV fields
        'modelSnapshotPrice':      kwargs.get('modelSnapshotPrice'),
        'executablePriceAtOutput': kwargs.get('executablePriceAtOutput'),
        'actualEntryPrice':        kwargs.get('actualEntryPrice', None),
        'closingPrice':            kwargs.get('closingPrice', None),
        'clvVsSnapshot':           kwargs.get('clvVsSnapshot', None),
        'clvVsExecutableOutput':   kwargs.get('clvVsExecutableOutput', None),
        'clvVsActualEntry':        kwargs.get('clvVsActualEntry', None),
        # Projections
        'awayProjRuns':       kwargs.get('awayProjRuns'),
        'homeProjRuns':       kwargs.get('homeProjRuns'),
        'totalProj':          kwargs.get('totalProj'),
        'f5AwayProj':         kwargs.get('f5AwayProj'),
        'f5HomeProj':         kwargs.get('f5HomeProj'),
        # Bullpen availability (recent workload) adjustment debug/audit
        # fields -- the multiplier actually applied to each side's
        # season-long pen xFIP inside compute_projections(), plus the
        # component breakdown, so a row's modelProb can always be traced
        # back to why it moved. See lib/edgelab/bullpen_availability.py.
        'awayBullpenAvailability': kwargs.get('awayBullpenAvailability'),
        'homeBullpenAvailability':  kwargs.get('homeBullpenAvailability'),
        # Baseball Input Data / Platoon Context mission: confirmed-lineup
        # handedness/platoon context (lib.research.platoon_context), shared
        # across every market row via proj_context -- see evaluate_game().
        'awayPlatoonContext': kwargs.get('awayPlatoonContext'),
        'homePlatoonContext':  kwargs.get('homePlatoonContext'),
        # NRFI/YRFI-only: first-inning-specific projection debug block
        # (lib.research.first_inning_context) -- None on every other market.
        'firstInningContext': kwargs.get('firstInningContext'),
        'rejectionReason':    kwargs.get('rejectionReason'),
        'missingFields':      kwargs.get('missingFields'),
        'evaluationError':    kwargs.get('evaluationError'),
        'gatesFired':         kwargs.get('gatesFired', []),
        'notes':              kwargs.get('notes'),
        'ticker':             kwargs.get('ticker'),
        'marketTicker':       kwargs.get('marketTicker'),
        'seriesTicker':       kwargs.get('seriesTicker'),
        'eventTicker':        kwargs.get('eventTicker'),
        'scheduledStartTime': kwargs.get('scheduledStartTime'),
        'line':               kwargs.get('line'),
        # Rule 71 patch: bet eligibility / CLV / review status
        'bet_eligibility_status':  kwargs.get('bet_eligibility_status'),
        'clv_capture_status':      kwargs.get('clv_capture_status'),
        'review_integrity_status': kwargs.get('review_integrity_status'),
        'eligibility_reason':      kwargs.get('eligibility_reason'),
        # Phase 1F: structured reason codes (populated after row is fully built)
        'reasonCodes':        kwargs.get('reasonCodes', []),
        # Phase 1B: Lineup fields — must flow from awayTeamStats/homeTeamStats via evaluate_game
        'lineupPosted':              kwargs.get('lineupPosted'),
        'lineupStatus':              kwargs.get('lineupStatus'),
        'lineupConfirmedOfficial':   kwargs.get('lineupConfirmedOfficial'),
        'lineupSource':              kwargs.get('lineupSource'),
        'lineupBattersExpected':     kwargs.get('lineupBattersExpected'),
        'lineupBattersFound':        kwargs.get('lineupBattersFound'),
        'lineupBattersResolved':     kwargs.get('lineupBattersResolved'),
        'lineupAdjAvailable':        kwargs.get('lineupAdjAvailable'),
        'lineupAdjApplied':          kwargs.get('lineupAdjApplied'),
        'lineupDataQuality':         kwargs.get('lineupDataQuality'),
        'lineupStatusReason':        kwargs.get('lineupStatusReason'),
    }
    return row

def missing_row(market, missing_fields):
    return make_row(market, status='Missing Data', missingFields=missing_fields)

def rejected_row(market, reason, **kwargs):
    return make_row(market, status='Rejected', rejectionReason=reason, **kwargs)

def accepted_row(market, **kwargs):
    return make_row(market, status='Accepted', **kwargs)

def failed_row(market, error):
    return make_row(market, status='Evaluation Failed', evaluationError=str(error)[:200])

# ── Size lookup ────────────────────────────────────────────────────────────────
MARKET_MULTIPLIERS = {
    'F5_ML_Away': 1.5, 'F5_ML_Home': 1.5,
    'TT_Away_Over': 1.25, 'TT_Home_Over': 1.25,
    'YRFI': 1.25,
    'ML_Away': 1.0, 'ML_Home': 1.0,
    'NRFI': 1.0,
    'RL_Away': 0.0, 'RL_Home': 0.0,  # suspended
    'Game_Total': 0.0,                 # paper only
}

def bet_size(conf, market):
    mult = MARKET_MULTIPLIERS.get(market, 1.0)
    if mult == 0.0: return 1.0  # paper
    base = {'HIGH': 4.0, 'MEDIUM': 3.0, 'PAPER': 1.0}.get(conf, 1.0)
    return min(8.0, round(base * mult * 2) / 2)

# ── Projection engine (simplified — reads from enrich_data output) ────────────
def compute_projections(g):
    """
    Returns (away_proj, home_proj, f5_away, f5_home) using offenseBaselineAdj.
    Returns None tuple if critical data is missing.
    """
    away_stats = g.get('awayTeamStats', {})
    home_stats  = g.get('homeTeamStats', {})
    away_ps     = g.get('away', {}).get('pitcherSavant') or {}
    home_ps     = g.get('home', {}).get('pitcherSavant') or {}
    away_bp     = g.get('away', {}).get('bullpen', {})
    home_bp     = g.get('home', {}).get('bullpen', {})

    away_baseline = away_stats.get('offenseBaselineAdj')
    home_baseline  = home_stats.get('offenseBaselineAdj')

    # Use xFIP; fall back to seasonFIP if xFIP null
    away_xfip = away_ps.get('xFIP') or away_ps.get('seasonFIP')
    home_xfip  = home_ps.get('xFIP') or home_ps.get('seasonFIP')

    missing = []
    if away_baseline is None: missing.append('awayTeamStats.offenseBaselineAdj')
    if home_baseline is None:  missing.append('homeTeamStats.offenseBaselineAdj')
    if away_xfip is None:      missing.append('away.pitcherSavant.xFIP')
    if home_xfip is None:      missing.append('home.pitcherSavant.xFIP')
    if missing:
        return None, None, None, None, missing

    # Clamp xFIP
    away_xfip = max(2.80, min(5.50, away_xfip))
    home_xfip  = max(2.80, min(5.50, home_xfip))

    # recentFIP sanity gate — if negative, use seasonFIP or xFIP only
    # (pipeline bug: negative recentFIP on <3 starts)
    away_recent = away_ps.get('recentFIP')
    home_recent  = home_ps.get('recentFIP')
    if away_recent is not None and away_recent < 0:
        away_xfip = away_xfip  # already clamped to xFIP, skip recentFIP
    if home_recent is not None and home_recent < 0:
        home_xfip = home_xfip

    away_ip     = min(away_ps.get('avgIPperStart') or 6.0, 9.0)
    home_ip      = min(home_ps.get('avgIPperStart') or 6.0, 9.0)
    away_pen_xfip = away_bp.get('xFIP') or 4.0
    home_pen_xfip  = home_bp.get('xFIP') or 4.0

    # Bullpen availability (recent workload) adjustment -- separate
    # short-term signal from PR #51's bullpen.recentUsage block, applied
    # ONLY to the season-long pen xFIP used for full-game runs allowed.
    # Never touches away_xfip/home_xfip (starter quality, which also
    # drives F5/F3 below) -- see lib/edgelab/bullpen_availability.py for
    # the conservative, capped, missing-data-safe multiplier this
    # produces (>= 1.0 always; missing/unavailable recentUsage always
    # yields multiplier 1.0, never a fabricated "rested" bonus).
    away_pen_xfip = away_pen_xfip * compute_bullpen_workload_adjustment(away_bp.get('recentUsage'))['multiplier']
    home_pen_xfip  = home_pen_xfip  * compute_bullpen_workload_adjustment(home_bp.get('recentUsage'))['multiplier']

    # Clamp pen xFIP
    away_pen_xfip = max(2.5, min(6.0, away_pen_xfip))
    home_pen_xfip  = max(2.5, min(6.0, home_pen_xfip))

    # Park factor
    park_factor = g.get('park', {}).get('parkFactor', 100)
    park_adj = (park_factor - 100) / 100 * 0.5

    away_off_factor = (away_baseline or 4.5) / 4.5
    home_off_factor  = (home_baseline  or 4.5) / 4.5

    home_starter_ip = home_ip
    home_pen_ip     = max(0, 9.0 - home_starter_ip)
    away_starter_ip = away_ip
    away_pen_ip     = max(0, 9.0 - away_starter_ip)

    away_proj = away_off_factor * (home_starter_ip * home_xfip / 9 + home_pen_ip * home_pen_xfip / 9) + park_adj
    home_proj  = home_off_factor  * (away_starter_ip * away_xfip / 9 + away_pen_ip  * away_pen_xfip / 9) + park_adj

    # Confirmed-lineup handedness/platoon adjustment (lib.research.platoon_context) --
    # bounded (±PLATOON_ADJ_CAP_RPG), additive on top of the offense_baseline/lineupAdj
    # already folded into away_baseline/home_baseline above, and 0.0 (no-op) whenever
    # the lineup is unconfirmed or platoon evidence is missing, so a game with no new
    # data produces an identical away_proj/home_proj to before this adjustment existed.
    away_platoon_rpg = build_offense_platoon_context(g, 'away')['aggregatePlatoonAdvantageRPG']
    home_platoon_rpg  = build_offense_platoon_context(g, 'home')['aggregatePlatoonAdvantageRPG']
    away_proj = away_proj + away_platoon_rpg
    home_proj  = home_proj  + home_platoon_rpg

    # Clamp
    away_proj = max(2.5, min(7.0, away_proj))
    home_proj  = max(2.5, min(7.0, home_proj))

    # F5: starter only, 5/8.5 ratio, opener cap
    away_opener = away_ps.get('openerRole', False)
    home_opener  = home_ps.get('openerRole', False)

    f5_away_starter_ip = min(away_ip, 5.0) if not away_opener else 0
    f5_home_starter_ip  = min(home_ip,  5.0) if not home_opener  else 0

    # TTO adjustment
    away_tto = away_ps.get('ttoSplit')
    home_tto  = home_ps.get('ttoSplit')
    away_tto_adj = 1.0 - (away_tto * 0.15) if (away_tto and away_tto > 0.5) else 1.0
    home_tto_adj  = 1.0 - (home_tto  * 0.15) if (home_tto  and home_tto  > 0.5) else 1.0

    f5_away = away_off_factor * (f5_home_starter_ip * home_xfip / 9 * home_tto_adj) + park_adj * (5/9)
    f5_home  = home_off_factor  * (f5_away_starter_ip  * away_xfip / 9 * away_tto_adj)  + park_adj * (5/9)

    # Same shared platoon context as away_proj/home_proj above, scaled to F5's 5/9
    # share -- matches park_adj's own (5/9) scaling immediately above. This is the
    # ONLY way first-inning-only evidence could otherwise leak into F5: it can't,
    # because first-inning-specific context (lib.research.first_inning_context) is
    # never read here -- only the platoon context both horizons explicitly share.
    f5_away = f5_away + away_platoon_rpg * (5/9)
    f5_home  = f5_home  + home_platoon_rpg  * (5/9)

    f5_away = max(1.2, min(4.1, f5_away))
    f5_home  = max(1.2, min(4.1, f5_home))

    return (
        round(away_proj, 3),
        round(home_proj, 3),
        round(f5_away, 3),
        round(f5_home, 3),
        []  # no missing fields
    )


def compute_game_projection_context(g):
    """
    Pure (Phase 6 Part 7): wraps compute_projections(g)'s positional
    tuple return into the single canonical dict shape used both for the
    data/pipeline/<date>/projections.json artifact and for
    evaluate_game()'s row generation — the one projection result per
    game per run this phase requires. Computing this dict is the ONLY
    place compute_projections(g) is called in the normal (main())
    path; main() calls this once per game and threads the same object
    into both the artifact writer and evaluate_game(), instead of the
    pre-Phase-6 pattern of two independent compute_projections(g) calls
    (one in main()'s artifact-building loop, one inside evaluate_game()
    itself) that happened to always agree only because nothing mutates
    a game's projection-relevant fields between them.
    """
    away_proj, home_proj, f5_away, f5_home, missing = compute_projections(g)
    # Matches evaluate_game()'s original `if away_proj else None` exactly
    # (not just `is not None`) -- behaviorally identical today since
    # compute_projections() clamps away_proj to >=2.5 whenever it isn't
    # None, so it can never be falsy-but-present, but written this way
    # to carry zero risk of divergence from the pre-Phase-6 expression.
    total_proj = round(away_proj + home_proj, 3) if away_proj else None

    # Debug/audit only: recompute the SAME pure bullpen-availability
    # multiplier compute_projections() already applied internally to the
    # pen xFIP it used, so every downstream row can show WHY a fair
    # probability moved without changing compute_projections()'s
    # existing 5-item tuple return (see
    # tests/test_build_market_ledger_projection_boundary.py's single-
    # call-per-game guarantee, which this does not affect -- it's a call
    # to compute_bullpen_workload_adjustment(), not compute_projections()).
    away_bp = g.get('away', {}).get('bullpen', {}) or {}
    home_bp  = g.get('home', {}).get('bullpen', {}) or {}
    away_bullpen_availability = compute_bullpen_workload_adjustment(away_bp.get('recentUsage'))
    home_bullpen_availability  = compute_bullpen_workload_adjustment(home_bp.get('recentUsage'))

    # Debug/audit only, same pattern as awayBullpenAvailability above: a
    # second, pure, cheap re-call of build_offense_platoon_context(g, side)
    # (already called once inside compute_projections(g) to derive the
    # adjustment folded into away_proj/home_proj/f5_away/f5_home) so every
    # downstream row can show WHY a fair probability moved, without
    # changing compute_projections()'s 5-item tuple return or its
    # single-call-per-game guarantee (see the comment above).
    away_platoon_context = build_offense_platoon_context(g, 'away')
    home_platoon_context  = build_offense_platoon_context(g, 'home')

    # First-inning-specific NRFI/YRFI context -- consumes the same
    # away_proj/home_proj this function already computed (for the naive
    # proj/9 fallback) plus the platoon contexts above (for the small,
    # separately-capped top-3-handedness nudge). Computed once here, then
    # threaded through evaluate_game()'s projection_context exactly like
    # every other field on this dict.
    first_inning_context = build_first_inning_context(
        g, away_proj, home_proj, away_platoon_context, home_platoon_context
    )

    return {
        'awayProjRuns': away_proj,
        'homeProjRuns': home_proj,
        'totalProj': total_proj,
        'f5AwayProj': f5_away,
        'f5HomeProj': f5_home,
        'missingFields': missing,
        'awayBullpenAvailability': away_bullpen_availability,
        'homeBullpenAvailability': home_bullpen_availability,
        'awayPlatoonContext': away_platoon_context,
        'homePlatoonContext': home_platoon_context,
        'firstInningContext': first_inning_context,
    }


def game_projection_identity(g, index):
    """
    Pure (Phase 6 Part 9): best-available stable identity for associating
    a game with its projection record. Prefers gameId — a unique
    per-game-instance identifier (see scripts/fetch_lineups.py/
    scripts/fetch_savant_pitchers.py, Phase 5) immune to the
    kalshiKey-based doubleheader collision found in merge_odds.py during
    Phase 4 — when present; falls back to kalshiKey (this script's
    pre-existing identity concept, already present on projections.json's
    records via a direct `_g.get('kalshiKey')`) when gameId is absent;
    falls back to the game's own position in the games list when neither
    is present, so every game always has SOME identity. This does not
    redesign global game identity or fix the kalshiKey collision issue
    (out of scope for Phase 6) — it only decides which already-present
    field this one script prefers when both are available.

    NOT CURRENTLY CALLED from main() (PR #7 review, Section M finding):
    main()'s projections.json artifact block adds `gameId` via a plain
    `_g.get('gameId')`, independent of this function's preference logic
    -- an earlier draft of this docstring claimed this function was used
    for that labeling, which was inaccurate and has been corrected here.
    This function exists as a documented, independently tested (see
    tests/test_build_market_ledger_projection_boundary.py's
    TestGameProjectionIdentity) policy for what identity a FUTURE phase
    should prefer if it ever needs a keyed (not positional) lookup --
    e.g. reading projections.json back from disk (see Part 8) — rather
    than wiring in a new artifact field no current consumer needs, per
    the mission's "do not broaden the schema merely for convenience."
    The actual projection-to-evaluate_game() wiring in main() is, and
    remains, positional (the same `games` list, same order, single
    pass), which needs no keyed lookup for correctness regardless.
    """
    gid = g.get('gameId')
    if gid:
        return ('gameId', gid)
    kk = g.get('kalshiKey')
    if kk:
        return ('kalshiKey', kk)
    return ('index', index)


# ── Main evaluation ────────────────────────────────────────────────────────────
def evaluate_game(g, projection_context=None):
    """
    Returns list of market rows (one per REQUIRED_MARKETS entry).
    Every required market gets exactly one row.

    projection_context (Phase 6 Part 10, transitional adapter): optional
    pre-computed dict from compute_game_projection_context(g) — pass this
    explicitly to reuse a projection already computed elsewhere (main()
    always does, so the same object backs both projections.json and the
    recommendation rows below — see Part 7/12). Defaults to None, in
    which case this function computes it internally exactly as it always
    did before this phase, so every existing direct caller (tests and any
    future one) that calls evaluate_game(g) with just one argument keeps
    working completely unchanged. This function no longer "secretly
    owns" projection computation in the normal main() path — it only
    falls back to owning it when no context is supplied.
    """
    rows = {}
    kalshi = (g.get('odds') or {}).get('kalshi') or {}
    pvf     = g.get('pinnacleVF', {}) or {}
    away_ps = g.get('away', {}).get('pitcherSavant') or {}
    home_ps  = g.get('home', {}).get('pitcherSavant') or {}
    away_ts = g.get('awayTeamStats', {}) or {}
    home_ts  = g.get('homeTeamStats', {}) or {}

    away_opener = away_ps.get('openerRole', False)
    home_opener  = home_ps.get('openerRole', False)
    total_line  = (kalshi.get('total') or {}).get('line')
    # Legacy gate field (backward compat) — True only when >=6/9 batters resolved
    away_lineup = away_ts.get('lineupConfirmed', False)
    home_lineup  = home_ts.get('lineupConfirmed', False)

    # Phase 1B: Separated lineup fields — read from awayTeamStats / homeTeamStats
    away_lineup_official    = away_ts.get('lineupConfirmedOfficial', False)
    home_lineup_official     = home_ts.get('lineupConfirmedOfficial', False)
    away_lineup_adj_avail   = away_ts.get('lineupAdjAvailable', False)
    home_lineup_adj_avail    = home_ts.get('lineupAdjAvailable', False)
    away_lineup_adj_applied  = away_ts.get('lineupAdjApplied', False)
    home_lineup_adj_applied   = home_ts.get('lineupAdjApplied', False)
    away_lineup_status       = away_ts.get('lineupStatus', 'unknown')
    home_lineup_status        = home_ts.get('lineupStatus', 'unknown')
    away_lineup_source       = away_ts.get('lineupSource', 'mlb_stats_api')
    home_lineup_source        = home_ts.get('lineupSource', 'mlb_stats_api')
    away_lineup_posted       = away_ts.get('lineupPosted', False)
    home_lineup_posted        = home_ts.get('lineupPosted', False)
    away_batters_expected    = away_ts.get('lineupBattersExpected', 9)
    home_batters_expected     = home_ts.get('lineupBattersExpected', 9)
    away_batters_found       = away_ts.get('lineupBattersFound', 0)
    home_batters_found        = home_ts.get('lineupBattersFound', 0)
    away_batters_resolved    = away_ts.get('lineupBattersResolved', 0)
    home_batters_resolved     = home_ts.get('lineupBattersResolved', 0)
    away_lineup_quality      = away_ts.get('lineupDataQuality', 'none')
    home_lineup_quality       = home_ts.get('lineupDataQuality', 'none')
    away_lineup_reason       = away_ts.get('lineupStatusReason', '')
    home_lineup_reason        = home_ts.get('lineupStatusReason', '')

    if 'lineupConfirmedOfficial' not in away_ts:
        import sys as _sys
        print(f'DATA-HEALTH WARNING: awayTeamStats missing lineupConfirmedOfficial — '
              f'fetch_lineups.py may not have run for this game', file=_sys.stderr)
    if 'lineupConfirmedOfficial' not in home_ts:
        import sys as _sys
        print(f'DATA-HEALTH WARNING: homeTeamStats missing lineupConfirmedOfficial — '
              f'fetch_lineups.py may not have run for this game', file=_sys.stderr)

    # ── Game-level identity (shared across all market rows) ───────────────
    # eventTicker is derived from kalshiKey + game time
    game_event_ticker = None
    kalshi_key = g.get('kalshiKey', '')
    game_time_et = g.get('kalshiGameTime', '')
    # The event_ticker is NOT the game key directly; rows use market-level tickers.
    # scheduledStartTime comes from the Odds API commence time
    scheduled_start = g.get('oddsApiCommenceTime')
    
    # Phase 1A: helper to normalize prices to cents scale
    def _to_cents(v):
        if v is None: return None
        f = float(v)
        return round(f * 100 if f <= 1.0 else f, 2)
    
    # Phase 1A: price snapshot timestamp
    snapshot_ts = g.get('kalshiSnapshotTs') or g.get('snapshot_ts')

    # Compute projections once (Phase 6 Part 10: reuse the caller-supplied
    # context if given -- main() always supplies one, computed exactly
    # once per game; only a direct call with no context falls back to
    # computing it here, exactly as this function always did before).
    if projection_context is None:
        projection_context = compute_game_projection_context(g)
    away_proj    = projection_context['awayProjRuns']
    home_proj    = projection_context['homeProjRuns']
    total_proj   = projection_context['totalProj']
    f5_away      = projection_context['f5AwayProj']
    f5_home      = projection_context['f5HomeProj']
    proj_missing = projection_context['missingFields']
    # .get(), not [...] -- unlike the five original keys above, an older-
    # shaped or hand-built projection_context (see
    # TestEvaluateGameBackwardCompatibilityEdgeCases) is not required to
    # carry these; absence degrades to "no adjustment info available",
    # never a KeyError, since these are debug/audit fields only and never
    # feed the actual model math.
    away_bullpen_availability = projection_context.get('awayBullpenAvailability')
    home_bullpen_availability  = projection_context.get('homeBullpenAvailability')
    # .get() (not [...]), same backward-compat rationale as
    # awayBullpenAvailability/homeBullpenAvailability above -- debug/audit
    # fields only, never feed the actual model math themselves (the
    # adjustment they describe is already folded into away_proj/home_proj/
    # f5_away/f5_home by compute_projections()).
    away_platoon_context = projection_context.get('awayPlatoonContext')
    home_platoon_context  = projection_context.get('homePlatoonContext')
    first_inning_context = projection_context.get('firstInningContext')

    proj_context = dict(
        awayProjRuns=away_proj, homeProjRuns=home_proj,
        totalProj=total_proj, f5AwayProj=f5_away, f5HomeProj=f5_home,
        awayBullpenAvailability=away_bullpen_availability,
        homeBullpenAvailability=home_bullpen_availability,
        awayPlatoonContext=away_platoon_context,
        homePlatoonContext=home_platoon_context,
    )

    # Phase 1B: per-game lineup context dicts — injected into every row
    away_lineup_ctx = dict(
        lineupPosted=away_lineup_posted,
        lineupStatus=away_lineup_status,
        lineupConfirmedOfficial=away_lineup_official,
        lineupSource=away_lineup_source,
        lineupBattersExpected=away_batters_expected,
        lineupBattersFound=away_batters_found,
        lineupBattersResolved=away_batters_resolved,
        lineupAdjAvailable=away_lineup_adj_avail,
        lineupAdjApplied=away_lineup_adj_applied,
        lineupDataQuality=away_lineup_quality,
        lineupStatusReason=away_lineup_reason,
    )
    home_lineup_ctx = dict(
        lineupPosted=home_lineup_posted,
        lineupStatus=home_lineup_status,
        lineupConfirmedOfficial=home_lineup_official,
        lineupSource=home_lineup_source,
        lineupBattersExpected=home_batters_expected,
        lineupBattersFound=home_batters_found,
        lineupBattersResolved=home_batters_resolved,
        lineupAdjAvailable=home_lineup_adj_avail,
        lineupAdjApplied=home_lineup_adj_applied,
        lineupDataQuality=home_lineup_quality,
        lineupStatusReason=home_lineup_reason,
    )

    # ── Identity context helper: returns identity kwargs for a market ─────
    def identity(market_ticker=None, series_ticker=None, event_ticker=None):
        return dict(
            marketTicker=market_ticker,
            ticker=market_ticker,
            seriesTicker=series_ticker,
            eventTicker=event_ticker,
            scheduledStartTime=scheduled_start,
        )

    # ── Helper: pinnacle gap check (Rule 71) ──────────────────────────────
    def pin_gap_ok_ml(model_prob, pvf_prob, market_label):
        """Returns (ok, gates_fired)"""
        if pvf_prob is None:
            return True, []  # can't check — no block
        gap = abs(model_prob - pvf_prob) * 100
        if gap > 8.0:
            return False, [f'Rule71: model {model_prob*100:.1f}% vs PinVF {pvf_prob*100:.1f}% = {gap:.1f}% gap > 8%']
        return True, []

    # ── ML_Away ───────────────────────────────────────────────────────────
    ml = kalshi.get('ml', {}) or {}
    ml_away_am = ml.get('away')
    ml_home_am = ml.get('home')
    pvf_away = (pvf.get('away') or 0) / 100 if pvf.get('away') else None
    pvf_home  = (pvf.get('home')  or 0) / 100 if pvf.get('home')  else None
    # Phase 1A: extract executable prices (yes_ask) from registry
    ml_away_yes_ask = ml.get('yes_ask_cents') or ml.get('yes_ask')  # may be None if registry lacks it
    ml_home_yes_ask = ml.get('yes_ask_cents') or ml.get('yes_ask')
    snapshot_ts = g.get('kalshiSnapshotTs') or g.get('snapshot_ts')

    if ml_away_am is None or ml_home_am is None:
        rows['ML_Away'] = missing_row('ML_Away', ['odds.kalshi.ml.away', 'odds.kalshi.ml.home'])
        rows['ML_Home']  = missing_row('ML_Home',  ['odds.kalshi.ml.away', 'odds.kalshi.ml.home'])
    elif away_proj is None:
        rows['ML_Away'] = missing_row('ML_Away', proj_missing)
        rows['ML_Home']  = missing_row('ML_Home',  proj_missing)
    else:
        try:
            vf_away, vf_home = vig_free_2way(ml_away_am, ml_home_am)
            p_away_win, p_push = p_team_wins(away_proj, home_proj)
            # Exclude push for ML
            p_away_net = p_away_win / (1 - p_push) if (1 - p_push) > 0 else p_away_win
            p_home_net = 1 - p_away_net

            # Extra-inning blend for close games
            margin = abs(away_proj - home_proj)
            if margin < 1.5:
                p_away_net = p_away_net * 0.90 + 0.50 * 0.10
                p_home_net = p_home_net * 0.90 + 0.50 * 0.10

            # Win prob ceiling
            p_away_net = min(p_away_net, 0.72)
            p_home_net = min(p_home_net, 0.72)

            # Phase 1C: build full edge fields using executable price (yes_ask)
            # yes_ask for the away YES market; for home we take the home yes_ask
            # Registry price_block stores yes_ask at decimal scale — convert to cents
            def _to_cents(v):
                if v is None: return None
                f = float(v)
                return round(f * 100 if f <= 1.0 else f, 2)
            away_yes_ask_c = _to_cents(ml.get('away_yes_ask') or ml.get('yes_ask'))
            home_yes_ask_c = _to_cents(ml.get('home_yes_ask') or ml.get('yes_ask'))
            # Fallback: derive from american odds if yes_ask not in registry
            if away_yes_ask_c is None and ml_away_am is not None:
                # Convert american to implied prob cents (approximate)
                imp = abs(ml_away_am)/(abs(ml_away_am)+100) if ml_away_am < 0 else 100/(ml_away_am+100)
                away_yes_ask_c = round(imp * 100, 2)
            if home_yes_ask_c is None and ml_home_am is not None:
                imp = abs(ml_home_am)/(abs(ml_home_am)+100) if ml_home_am < 0 else 100/(ml_home_am+100)
                home_yes_ask_c = round(imp * 100, 2)

            ef_away = build_edge_fields(p_away_net, vf_away, away_yes_ask_c, CAL_MEDIUM, snapshot_ts, series_ticker='KXMLBGAME')
            ef_home  = build_edge_fields(p_home_net,  vf_home,  home_yes_ask_c, CAL_MEDIUM, snapshot_ts, series_ticker='KXMLBGAME')

            # Executable EV / bet-up-to correctness: eligibility gates on
            # netExecutableEdge (fee-aware, post-friction, ask-based edge
            # MINUS the expected Kalshi trading fee) -- Production
            # Fee-Aware Net EV Integration milestone. Previously gated on
            # calibratedEdgeVsExecutable (fee-blind); that field is still
            # computed and preserved unchanged for display (see
            # edgeUsedForDisplay), it just no longer drives qualification.
            edge_away = ef_away['netExecutableEdge']
            edge_home  = ef_home['netExecutableEdge']

            conf_away = confidence_from_edge(edge_away)
            conf_home  = confidence_from_edge(edge_home)

            gates_away = []
            gates_home  = []

            # Tier/confidence calibration: a large, unexplained gap
            # between model and Kalshi VF caps HIGH ("Tier A") at MEDIUM
            # ("Tier B") -- see cap_tier_for_disagreement()'s docstring.
            conf_away = cap_tier_for_disagreement(conf_away, ef_away.get('rawEdgeVsVF'), gates_away)
            conf_home = cap_tier_for_disagreement(conf_home, ef_home.get('rawEdgeVsVF'), gates_home)

            # Rule 51: ML lineup gate — uses lineupConfirmedOfficial per Phase 1B spec.
            if not (away_lineup_official and home_lineup_official):
                missing_sides = []
                if not away_lineup_official: missing_sides.append('away')
                if not home_lineup_official: missing_sides.append('home')
                gate_msg = f'Rule 51: lineupConfirmedOfficial=False ({", ".join(missing_sides)}) — ML downgraded to PAPER'
                gates_away.append(gate_msg)
                gates_home.append(gate_msg)
                if conf_away not in (None,): conf_away = 'PAPER'
                if conf_home  not in (None,): conf_home  = 'PAPER'

            # Rule 71 gate
            ok_away, rule71_away = pin_gap_ok_ml(p_away_net, pvf_away, 'ML_Away')
            ok_home,  rule71_home  = pin_gap_ok_ml(p_home_net, pvf_home,  'ML_Home')
            if not ok_away:
                gates_away.extend(rule71_away)
                conf_away = None  # blocked

            if not ok_home:
                gates_home.extend(rule71_home)
                conf_home = None

            # Executable EV / bet-up-to correctness: hard ceiling enforcement.
            # maxBetPrice below is now the genuine price ceiling this
            # market's own edge requirement implies, never an echo of the
            # current executable price -- and a row whose current
            # executable price is already worse than that ceiling is
            # force-downgraded here rather than left Accepted.
            conf_away, gates_away, max_bet_away_gross, max_bet_away_net = enforce_bet_up_to(
                p_away_net, away_yes_ask_c, conf_away, gates_away,
                fee_multiplier=ef_away.get('feeMultiplier'))
            conf_home, gates_home, max_bet_home_gross, max_bet_home_net = enforce_bet_up_to(
                p_home_net, home_yes_ask_c, conf_home, gates_home,
                fee_multiplier=ef_home.get('feeMultiplier'))

            for market, model_p, vf, am, conf, gates, ml_lineup_ctx, max_bet_gross, max_bet_net in [
                ('ML_Away', p_away_net, vf_away, ml_away_am, conf_away, gates_away, away_lineup_ctx, max_bet_away_gross, max_bet_away_net),
                ('ML_Home',  p_home_net,  vf_home,  ml_home_am,  conf_home,  gates_home,  home_lineup_ctx, max_bet_home_gross, max_bet_home_net),
            ]:
                pvf_val = pvf_away if market == 'ML_Away' else pvf_home
                ef = ef_away if market == 'ML_Away' else ef_home
                max_bet = max_bet_net if max_bet_net is not None else max_bet_gross
                if conf is None:
                    if gates:
                        row = rejected_row(
                            market,
                            reason='; '.join(gates),
                            kalshiPrice=am, kalshiVF=round(vf*100,2),
                            pinnacleVF=round(pvf_val*100,2) if pvf_val else None,
                            modelProb=round(model_p*100,2),
                            gatesFired=gates,
                            **ef,
                            maxBetPrice=max_bet, betUpToPriceGross=max_bet_gross, betUpToPriceNet=max_bet_net,
                            **proj_context,
                            **ml_lineup_ctx,
                        )
                    else:
                        row = rejected_row(
                            market,
                            reason=f"edge {ef['netExecutableEdge']}% below {THRESHOLD_PAPER}% floor",
                            kalshiPrice=am, kalshiVF=round(vf*100,2),
                            pinnacleVF=round(pvf_val*100,2) if pvf_val else None,
                            modelProb=round(model_p*100,2),
                            **ef,
                            maxBetPrice=max_bet, betUpToPriceGross=max_bet_gross, betUpToPriceNet=max_bet_net,
                            **proj_context,
                            **ml_lineup_ctx,
                        )
                    row['reasonCodes'] = build_reason_codes('Rejected', row)
                    rows[market] = row
                else:
                    ml_ticker = ml.get('away_ticker') if market == 'ML_Away' else ml.get('home_ticker')
                    row = accepted_row(
                        market,
                        kalshiPrice=am, kalshiImplied=round(vf*100,2), kalshiVF=round(vf*100,2),
                        pinnacleVF=round(pvf_val*100,2) if pvf_val else None,
                        modelProb=round(model_p*100,2),
                        confidence=conf, betSize=bet_size(conf, market),
                        gatesFired=gates,
                        **ef,
                        maxBetPrice=max_bet, betUpToPriceGross=max_bet_gross, betUpToPriceNet=max_bet_net,
                        confidenceTier=conf,
                        **identity(ml_ticker, 'KXMLBGAME'),
                        **proj_context,
                        **ml_lineup_ctx,
                    )
                    row['reasonCodes'] = build_reason_codes('Accepted', row)
                    rows[market] = row
        except Exception as e:
            import traceback as _tb
            _tbstr = _tb.format_exc()
            for _mkt, _lctx in [('ML_Away', away_lineup_ctx), ('ML_Home', home_lineup_ctx)]:
                if _mkt not in rows:
                    _row = failed_row(_mkt, f'{type(e).__name__}: {e}')
                    _row['evaluationError'] = f'{type(e).__name__}: {e}' + '\n' + _tbstr[:400]
                    _row.update(_lctx)
                    rows[_mkt] = _row

    # ── RL_Away / RL_Home ─────────────────────────────────────────────────
    # Suspended per Rule 81 — always Rejected with documented reason
    rl = kalshi.get('rl', {}) or {}
    rl_ticker = rl.get('best_ticker')
    for market in ['RL_Away', 'RL_Home']:
        rows[market] = rejected_row(
            market,
            reason='Rule 81: RL suspended — WR 36%, CLV -4.09%. Paper until WR>=48% N>=20 AND CLV>=0% N>=15',
            kalshiPrice=rl.get('american'),
            **identity(rl_ticker, 'KXMLBSPREAD'),
            **proj_context
        )

    # ── Game_Total ────────────────────────────────────────────────────────
    tot = kalshi.get('total', {}) or {}
    tot_line = tot.get('line')
    tot_am   = tot.get('american')
    if tot_line is None:
        rows['Game_Total'] = missing_row('Game_Total', ['odds.kalshi.total.line'])
    elif away_proj is None:
        rows['Game_Total'] = missing_row('Game_Total', proj_missing)
    else:
        try:
            # Paper only per Rule 71 market suspension (WR 41%)
            rows['Game_Total'] = rejected_row(
                'Game_Total',
                reason=f'Rule 71 market suspension: Game Total WR 41%, CLV -1.43%. Paper only until WR>=52% N>=30',
                kalshiPrice=tot_am, line=tot_line,
                modelProb=round(p_over_total(total_proj, tot_line)*100, 2) if total_proj else None,
                **identity(tot.get('best_ticker'), 'KXMLBTOTAL'),
                **proj_context
            )
        except Exception as e:
            rows['Game_Total'] = failed_row('Game_Total', e)

    # ── TT_Away_Over / TT_Home_Over ───────────────────────────────────────
    tt = kalshi.get('team_totals', {}) or {}
    for market, side_key, lineup_ok_official, lineup_ctx, proj in [
        ('TT_Away_Over', 'away', away_lineup_official, away_lineup_ctx, away_proj),
        ('TT_Home_Over', 'home', home_lineup_official,  home_lineup_ctx,  home_proj),
    ]:
        tt_side = tt.get(side_key, {}) or {}
        tt_ticker = tt_side.get('best_ticker')
        tt_line   = tt_side.get('line')
        tt_am     = tt_side.get('american')
        tt_implied = tt_side.get('implied_pct')

        if tt_ticker is None:
            _r = missing_row(market, [f'odds.kalshi.team_totals.{side_key}.best_ticker'])
            _r.update(lineup_ctx)
            rows[market] = _r
        elif proj is None:
            _r = missing_row(market, proj_missing)
            _r.update(lineup_ctx)
            rows[market] = _r
        else:
            try:
                gates = []
                # Rule 50: TT lineup gate — uses lineupConfirmedOfficial per Phase 1B spec.
                if not lineup_ok_official:
                    gates.append('Rule 50: lineupConfirmedOfficial=False → TT Paper only')

                if tt_line is not None and tt_implied is not None:
                    kalshi_vf = tt_implied / 100
                    # FIX (v1.2): tt_line is Kalshi's raw ticker-suffix digit
                    # `over_n` (scripts/merge_odds.py: 'line': bl.get('over_n')),
                    # NOT a plain "greater than N" integer line like Game_Total
                    # uses. Team-total tickers follow the SAME suffix convention
                    # as winning_margin: digit N encodes "over (N-0.5)", i.e. the
                    # contract is YES iff team_runs >= N (see
                    # scripts/build_kalshi_registry.py's own note: 'over_n=4
                    # means "scores over 3.5"', and the canonical parser
                    # lib/research/market_taxonomy.py::_team_and_margin_from_suffix,
                    # which stores threshold = N - 0.5 for this exact reason).
                    # The authoritative settlement grader
                    # (lib/edgelab/settlement.py::settle_market, FAMILY_TEAM_TOTAL)
                    # pays YES iff team_runs > (N - 0.5), i.e. team_runs >= N.
                    # p_over_total(proj, L) = P(runs > L) = P(runs >= L+1), so
                    # matching that contract requires L = tt_line - 1, NOT
                    # tt_line directly. The prior "v1.1" change here passed
                    # tt_line unadjusted, silently excluding the entire
                    # PMF(tt_line) mass from the Over side (~15-20 ppts for
                    # typical team-total projections) -- this was the root
                    # cause of team_total's measured +0.1754 calibration gap.
                    model_p = p_over_total(proj, tt_line - 1)
                    model_p = min(model_p, 0.95)

                    # FIX 3: TT executable price — derive from yes_ask if present,
                    # else implied_pct, else American odds conversion
                    tt_yes_ask_c = _to_cents(tt_side.get('yes_ask'))
                    if tt_yes_ask_c is None and tt_implied is not None:
                        tt_yes_ask_c = round(float(tt_implied), 4)
                    if tt_yes_ask_c is None and tt_am is not None:
                        _imp = abs(tt_am)/(abs(tt_am)+100) if tt_am < 0 else 100/(tt_am+100)
                        tt_yes_ask_c = round(_imp * 100, 2)

                    ef_tt = build_edge_fields(model_p, kalshi_vf, tt_yes_ask_c, CAL_MEDIUM, snapshot_ts, series_ticker='KXMLBTEAMTOTAL')

                    # Executable EV / bet-up-to correctness: eligibility
                    # gates on netExecutableEdge (fee-aware) -- Production
                    # Fee-Aware Net EV Integration milestone. See the ML
                    # block above for the full rationale.
                    edge_val = ef_tt['netExecutableEdge']
                    conf = confidence_from_edge(edge_val)

                    # Tier/confidence calibration: see cap_tier_for_disagreement().
                    conf = cap_tier_for_disagreement(conf, ef_tt.get('rawEdgeVsVF'), gates)

                    if not lineup_ok_official:
                        conf = 'PAPER'

                    # Hard bet-up-to ceiling enforcement (see enforce_bet_up_to).
                    conf, gates, tt_max_bet_gross, tt_max_bet_net = enforce_bet_up_to(
                        model_p, tt_yes_ask_c, conf, gates, fee_multiplier=ef_tt.get('feeMultiplier'))
                    tt_max_bet = tt_max_bet_net if tt_max_bet_net is not None else tt_max_bet_gross

                    if conf is None:
                        row = rejected_row(
                            market,
                            reason='; '.join(gates) if gates else f'edge {edge_val}% below {THRESHOLD_PAPER}% floor',
                            kalshiPrice=tt_am, kalshiVF=round(kalshi_vf*100,2),
                            modelProb=round(model_p*100,2),
                            line=tt_line, gatesFired=gates,
                            **ef_tt,
                            maxBetPrice=tt_max_bet, betUpToPriceGross=tt_max_bet_gross, betUpToPriceNet=tt_max_bet_net,
                            **identity(tt_ticker, 'KXMLBTEAMTOTAL'),
                            **proj_context,
                            **lineup_ctx,
                        )
                        row['reasonCodes'] = build_reason_codes('Rejected', row)
                        rows[market] = row
                    else:
                        row = accepted_row(
                            market,
                            kalshiPrice=tt_am, kalshiImplied=tt_implied,
                            kalshiVF=round(kalshi_vf*100,2),
                            modelProb=round(model_p*100,2),
                            confidence=conf, betSize=bet_size(conf, market),
                            line=tt_line, gatesFired=gates,
                            **ef_tt,
                            maxBetPrice=tt_max_bet, betUpToPriceGross=tt_max_bet_gross, betUpToPriceNet=tt_max_bet_net,
                            confidenceTier=conf,
                            **identity(tt_ticker, 'KXMLBTEAMTOTAL'),
                            **proj_context,
                            **lineup_ctx,
                        )
                        row['reasonCodes'] = build_reason_codes('Accepted', row)
                        rows[market] = row
                else:
                    _r = missing_row(market, [f'odds.kalshi.team_totals.{side_key}.line'])
                    _r.update(lineup_ctx)
                    rows[market] = _r
            except Exception as e:
                import traceback as _tb
                _tbstr = _tb.format_exc()
                _r = failed_row(market, f'{type(e).__name__}: {e}')
                _r['evaluationError'] = f'{type(e).__name__}: {e}' + '\n' + _tbstr[:400]
                _r.update(lineup_ctx)
                rows[market] = _r

    # ── F5_ML_Away / F5_ML_Home (F5 Three-Way Pricing Correction) ──────────
    # Kalshi's F5 market has a real, separately tradable TIE contract
    # (confirmed via a live market snapshot -- see
    # docs/F5_THREE_WAY_PRICING.md). The model side (three_way_result_probs)
    # and market side (vig_free_3way) are both computed ONCE here, shared
    # by both F5_ML_Away and F5_ML_Home below, so the two rows can never
    # drift from a single, internally-consistent three-way computation.
    # Away/home model probabilities are NEVER renormalized after removing
    # the tie -- p_f5_away + p_f5_tie + p_f5_home sum to 1 by construction
    # (see validate_f5_three_way()'s sum-to-one gate below).
    f5ml = kalshi.get('f5ml', {}) or {}
    f5_away_am = f5ml.get('away')
    f5_home_am  = f5ml.get('home')
    f5_tie_am   = f5ml.get('tie_american')
    f5_away_ticker = f5ml.get('away_ticker')
    f5_home_ticker = f5ml.get('home_ticker')
    f5_tie_ticker  = f5ml.get('tie_ticker')

    f5_three_way_error = None
    p_f5_away = p_f5_tie = p_f5_home = None
    vf_f5_away = vf_f5_tie = vf_f5_home = None
    if f5_away is not None and f5_home is not None and (f5_away_am is not None or f5_home_am is not None):
        try:
            _f5_probs = three_way_result_probs(f5_away, f5_home, max_runs=20)
            p_f5_away = _f5_probs['awayWinProb']
            p_f5_tie  = _f5_probs['tieProb']
            p_f5_home = _f5_probs['homeWinProb']
            validate_f5_three_way(
                p_f5_away, p_f5_tie, p_f5_home,
                f5_away_ticker, f5_tie_ticker, f5_home_ticker,
                f5_away_am, f5_tie_am, f5_home_am,
            )
            vf_f5_away, vf_f5_tie, vf_f5_home = vig_free_3way(f5_away_am, f5_tie_am, f5_home_am)
            if vf_f5_away is None:
                # vig_free_3way() already returns (None, None, None) rather
                # than silently falling back to a two-way calc on partial
                # market data -- surfaced here as the same explicit,
                # named error the F5PricingError path uses, not a generic
                # missing_row with no explanation.
                f5_three_way_error = (
                    "F5 three-way vig-free market pricing unavailable "
                    f"(away_american={f5_away_am!r}, tie_american={f5_tie_am!r}, home_american={f5_home_am!r})"
                )
        except F5PricingError as _e:
            f5_three_way_error = str(_e)

    # Tie contract pricing (informational -- see docs/F5_THREE_WAY_PRICING.md
    # for why this is exposed alongside F5_ML_Away/F5_ML_Home rather than
    # added as a new REQUIRED_MARKETS entry: adding a 12th real-money-
    # eligible market would be a market-selection-philosophy change, which
    # this milestone is explicitly scoped to avoid). Computed once, shared
    # identically by both rows below.
    f5_tie_contract = None
    if f5_three_way_error is None and p_f5_tie is not None:
        f5_tie_ask_c = american_to_ask_cents((f5ml.get('prices') or {}).get('tie') or {}, f5_tie_am)
        f5_tie_contract = dict(
            contract_pricing(p_f5_tie, vf_f5_tie, f5_tie_ask_c),
            ticker=f5_tie_ticker,
            americanOdds=f5_tie_am,
            pricingVersion=F5_PRICING_VERSION_CURRENT,
        )

    for market, side_opener, proj_val, am_val, opp_proj_val, opp_am in [
        ('F5_ML_Away', away_opener, f5_away, f5_away_am, f5_home, f5_home_am),
        ('F5_ML_Home',  home_opener,  f5_home,  f5_home_am,  f5_away, f5_away_am),
    ]:
        if am_val is None:
            rows[market] = missing_row(market, [f'odds.kalshi.f5ml.{market.split("_")[-1].lower()}'])
        elif f5_away is None or f5_home is None:
            rows[market] = missing_row(market, proj_missing)
        elif f5_three_way_error is not None:
            rows[market] = missing_row(market, [f5_three_way_error])
        else:
            try:
                gates = []
                # Rule 53: F5 lineup gate — uses lineupConfirmedOfficial per Phase 1B spec.
                if not (away_lineup_official and home_lineup_official):
                    missing_sides_f5 = []
                    if not away_lineup_official: missing_sides_f5.append('away')
                    if not home_lineup_official: missing_sides_f5.append('home')
                    gates.append(f'Rule 53: F5 requires confirmed lineups for both teams — {", ".join(missing_sides_f5)} unconfirmed (lineupConfirmedOfficial=False) → PAPER')
                # Rule 24: opener blocks F5 entirely for that side
                # (opener is the pitcher throwing for the OPPONENT when we evaluate the offense side)
                # F5_ML_Away = away wins F5. If HOME is opener, away faces opener → F5 unqualified.
                home_is_opener = home_opener
                away_is_opener = away_opener
                if market == 'F5_ML_Away' and away_is_opener:
                    rows[market] = rejected_row(
                        market,
                        reason=f'Rule 24: away pitcher is opener (avgIP={away_ps.get("avgIPperStart",0):.1f}) — F5 UNQUALIFIED',
                        kalshiPrice=am_val, gatesFired=['Rule24'],
                        **proj_context
                    )
                    continue
                if market == 'F5_ML_Home' and home_is_opener:
                    rows[market] = rejected_row(
                        market,
                        reason=f'Rule 24: home pitcher is opener (avgIP={home_ps.get("avgIPperStart",0):.1f}) — F5 UNQUALIFIED',
                        kalshiPrice=am_val, gatesFired=['Rule24'],
                        **proj_context
                    )
                    continue

                # F5 Three-Way Pricing Correction: model_p/kalshi_vf come
                # from the shared, pre-validated three-way computation
                # above (p_f5_away/p_f5_tie/p_f5_home,
                # vf_f5_away/vf_f5_tie/vf_f5_home) -- NEVER renormalized
                # to exclude the tie. See f5PricingVersion on the row for
                # an explicit, unambiguous version marker.
                model_p = p_f5_away if market == 'F5_ML_Away' else p_f5_home
                kalshi_vf = vf_f5_away if market == 'F5_ML_Away' else vf_f5_home

                # f5Amplified: xERAGap >= 1.5
                away_xfip = away_ps.get('xFIP') or away_ps.get('seasonFIP') or 4.0
                home_xfip  = home_ps.get('xFIP') or home_ps.get('seasonFIP') or 4.0
                xera_gap    = abs(away_xfip - home_xfip)
                f5_amplified = xera_gap >= 1.5

                f5_ticker = f5_away_ticker if market == 'F5_ML_Away' else f5_home_ticker
                f5_prices = (f5ml.get('prices') or {}).get('away' if market == 'F5_ML_Away' else 'home') or {}
                f5_yes_ask_c = american_to_ask_cents(f5_prices, am_val)
                own_contract_pricing = contract_pricing(model_p, kalshi_vf, f5_yes_ask_c)
                ef_f5 = build_edge_fields(model_p, kalshi_vf, f5_yes_ask_c, CAL_MEDIUM, snapshot_ts, series_ticker='KXMLBF5')

                # F3/F5 tie tax comparison (informational only -- never
                # changes this row's own accept/reject/confidence decision
                # above or below). Compares THIS row's side's three-way YES
                # against the OPPOSING side's protected NO as two
                # expressions of the same "favored_side not trailing after
                # five" thesis. The opposing side's ask is the same
                # mid-derived American-odds proxy american_to_ask_cents()
                # already uses for every F5 YES price -- see
                # lib.research.f5_tie_tax's module docstring for why no
                # better NO-side price feed exists yet.
                _opp_prices = (f5ml.get('prices') or {}).get('home' if market == 'F5_ML_Away' else 'away') or {}
                _opp_yes_ask_c = american_to_ask_cents(_opp_prices, opp_am)
                _protected_no_price_c = round(100 - _opp_yes_ask_c, 2) if _opp_yes_ask_c is not None else None
                tie_tax_comparison = evaluate_f5_tie_tax(
                    'away' if market == 'F5_ML_Away' else 'home',
                    model_p, p_f5_tie,
                    f5_yes_ask_c, _protected_no_price_c,
                )

                # Systematic Best-Expression Comparison mission: expose the
                # SAME-SIDE full-game moneyline row's own already-computed
                # price/edge alongside this F5 row's tieTaxComparison, so a
                # reader has all three expressions of a "favored_side should
                # not be trailing" thesis in one place -- (A) F5 three-way
                # YES [tieTaxComparison.threeWayYes], (B) opposing side's F5
                # NO [tieTaxComparison.protectedNo], and (C) extending the
                # SAME side's exposure to the full game via ML_Away/ML_Home
                # [this field]. Pure data exposure, computed by
                # ML_Away/ML_Home's own block above (which always runs
                # first) -- never a new probability/EV formula, and never a
                # merged "best of three" verdict: the manual analysis
                # philosophy (RULES.md) is that ChatGPT compares these
                # itself, not that this pipeline picks for it.
                _same_side_ml_key = 'ML_Away' if market == 'F5_ML_Away' else 'ML_Home'
                _ml_row = rows.get(_same_side_ml_key)
                full_game_ml_comparison = None
                if _ml_row is not None:
                    full_game_ml_comparison = {
                        'market': _same_side_ml_key,
                        'status': _ml_row.get('status'),
                        'kalshiPrice': _ml_row.get('kalshiPrice'),
                        'modelProb': _ml_row.get('modelProb'),
                        'netExecutableEdge': _ml_row.get('netExecutableEdge'),
                        'confidence': _ml_row.get('confidence') or _ml_row.get('confidenceTier'),
                        'payoffCondition': f"{'away' if market == 'F5_ML_Away' else 'home'} wins the full game "
                                           f"(extends exposure through the bullpen, past the F5 window)",
                    }

                # Systematic Best-Expression Comparison mission: gather the
                # research-only F3/F7/run-line price references here (no
                # model probability exists for any of them -- see
                # lib.research.expression_group's module docstring); the
                # full expressionGroup list itself is assembled just below,
                # once this row (the F5 YES source) is actually built.
                _side = 'away' if market == 'F5_ML_Away' else 'home'
                _side_abbr = g.get(_side, {}).get('abbr')
                _f3ml = kalshi.get('f3ml') or {}
                _f7ml = kalshi.get('f7ml') or {}
                _f3_am = _f3ml.get(_side)
                _f7_am = _f7ml.get(_side)
                _f3_price_c = american_to_ask_cents({}, _f3_am) if _f3_am is not None else None
                _f7_price_c = american_to_ask_cents({}, _f7_am) if _f7_am is not None else None
                _rl_price_c = None
                _rl_ticker = None
                if rl.get('team') and rl.get('team') == g.get(_side, {}).get('abbr'):
                    _rl_am = rl.get('american')
                    _rl_price_c = american_to_ask_cents({}, _rl_am) if _rl_am is not None else None
                    _rl_ticker = rl.get('best_ticker')

                # Executable EV / bet-up-to correctness: eligibility gates
                # on netExecutableEdge (fee-aware) -- Production Fee-Aware
                # Net EV Integration milestone. model_p/kalshi_vf/
                # f5_yes_ask_c above are the SAME tie-aware three-way
                # quantities already used for this exact F5_ML contract
                # event (tie is its own separately-priced, never-
                # renormalized outcome, unchanged by this milestone) --
                # the fee-adjusted break-even shift operates on the same
                # executable price, so gross edge, fee drag, and net edge
                # all refer to the identical contract event throughout.
                edge_val = ef_f5['netExecutableEdge']
                conf = confidence_from_edge(edge_val, f5_amplified=f5_amplified)

                # Tier/confidence calibration: see cap_tier_for_disagreement().
                # Note this reads the SAME model-vs-KalshiF5VF quantity as
                # the Rule71-F5 hard-reject check below (12pt) -- a single
                # disagreement signal driving an escalating ladder: <=7pt
                # fine, 7-12pt Tier A withheld (capped at MEDIUM), >12pt
                # fully rejected.
                conf = cap_tier_for_disagreement(conf, ef_f5.get('rawEdgeVsVF'), gates)

                # Apply Rule 53 lineup downgrade if gate fired
                if any('Rule 53' in g for g in gates) and conf not in (None,):
                    conf = 'PAPER'
                _f5_lineup_ctx = away_lineup_ctx.copy()

                # Rule 71 F5: block if model vs Kalshi F5 VF > 12%
                gap = abs(model_p - kalshi_vf) * 100
                if gap > 12.0:
                    gates.append(f'Rule71-F5: model {model_p*100:.1f}% vs KalshiF5VF {kalshi_vf*100:.1f}% = {gap:.1f}% > 12%')
                    conf = None

                # Hard bet-up-to ceiling enforcement (see enforce_bet_up_to).
                conf, gates, max_bet_gross, max_bet_net = enforce_bet_up_to(
                    model_p, f5_yes_ask_c, conf, gates, fee_multiplier=ef_f5.get('feeMultiplier'))
                max_bet = max_bet_net if max_bet_net is not None else max_bet_gross

                if conf is None:
                    row = rejected_row(
                        market,
                        reason=gates[0] if gates else f'edge {edge_val}% below threshold',
                        kalshiPrice=am_val, kalshiVF=round(kalshi_vf*100,2),
                        modelProb=round(model_p*100,2), gatesFired=gates,
                        notes=f'f5Amplified={f5_amplified}, xERAGap={xera_gap:.2f}',
                        **ef_f5,
                        maxBetPrice=max_bet, betUpToPriceGross=max_bet_gross, betUpToPriceNet=max_bet_net,
                        **proj_context
                    )
                    row['reasonCodes'] = build_reason_codes('Rejected', row)
                    row['f5PricingVersion'] = F5_PRICING_VERSION_CURRENT
                    row['f5ThreeWay'] = {'awayWinProbability': round(p_f5_away*100,2), 'tieProbability': round(p_f5_tie*100,2), 'homeWinProbability': round(p_f5_home*100,2)}
                    row['f5ContractPricing'] = own_contract_pricing
                    row['f5TieContract'] = f5_tie_contract
                    row['tieTaxComparison'] = tie_tax_comparison
                    row['fullGameMLComparison'] = full_game_ml_comparison
                    row['expressionGroup'] = build_expression_group(
                        _side, g.get('gameId'), _side_abbr,
                        f5_row=row, f5_protected_no_leg=(tie_tax_comparison or {}).get('protectedNo'),
                        full_game_ml_row=_ml_row,
                        f3_price_cents=_f3_price_c, f3_ticker=_f3ml.get(f'{_side}_ticker'),
                        f7_price_cents=_f7_price_c, f7_ticker=_f7ml.get(f'{_side}_ticker'),
                        run_line_price_cents=_rl_price_c, run_line_ticker=_rl_ticker,
                    )
                    rows[market] = row
                else:
                    row = accepted_row(
                        market,
                        kalshiPrice=am_val, kalshiImplied=round(kalshi_vf*100,2),
                        kalshiVF=round(kalshi_vf*100,2),
                        modelProb=round(model_p*100,2),
                        confidence=conf, betSize=bet_size(conf, market),
                        gatesFired=gates,
                        notes=f'f5Amplified={f5_amplified}, xERAGap={xera_gap:.2f}',
                        **ef_f5,
                        maxBetPrice=max_bet, betUpToPriceGross=max_bet_gross, betUpToPriceNet=max_bet_net,
                        confidenceTier=conf,
                        **identity(f5_ticker, 'KXMLBF5'),
                        **proj_context,
                        **_f5_lineup_ctx,
                    )
                    row['reasonCodes'] = build_reason_codes('Accepted', row)
                    row['f5PricingVersion'] = F5_PRICING_VERSION_CURRENT
                    row['f5ThreeWay'] = {'awayWinProbability': round(p_f5_away*100,2), 'tieProbability': round(p_f5_tie*100,2), 'homeWinProbability': round(p_f5_home*100,2)}
                    row['f5ContractPricing'] = own_contract_pricing
                    row['f5TieContract'] = f5_tie_contract
                    row['tieTaxComparison'] = tie_tax_comparison
                    row['fullGameMLComparison'] = full_game_ml_comparison
                    row['expressionGroup'] = build_expression_group(
                        _side, g.get('gameId'), _side_abbr,
                        f5_row=row, f5_protected_no_leg=(tie_tax_comparison or {}).get('protectedNo'),
                        full_game_ml_row=_ml_row,
                        f3_price_cents=_f3_price_c, f3_ticker=_f3ml.get(f'{_side}_ticker'),
                        f7_price_cents=_f7_price_c, f7_ticker=_f7ml.get(f'{_side}_ticker'),
                        run_line_price_cents=_rl_price_c, run_line_ticker=_rl_ticker,
                    )
                    rows[market] = row
            except Exception as e:
                import traceback as _tb
                _tbstr = _tb.format_exc()
                _r = failed_row(market, f'{type(e).__name__}: {e}')
                _r['evaluationError'] = f'{type(e).__name__}: {e}' + '\n' + _tbstr[:400]
                rows[market] = _r

    # ── NRFI / YRFI ───────────────────────────────────────────────────────
    rfi = kalshi.get('nrfi_yrfi', {}) or {}
    nrfi_am = rfi.get('nrfi_american')
    yrfi_am = rfi.get('yrfi_american')
    nrfi_implied = rfi.get('nrfi_implied')
    yrfi_implied = rfi.get('yrfi_implied')

    if nrfi_am is None or yrfi_am is None:
        rows['NRFI'] = missing_row('NRFI', ['odds.kalshi.nrfi_yrfi.nrfi_american'])
        rows['YRFI'] = missing_row('YRFI', ['odds.kalshi.nrfi_yrfi.yrfi_american'])
    elif away_proj is None:
        rows['NRFI'] = missing_row('NRFI', proj_missing)
        rows['YRFI'] = missing_row('YRFI', proj_missing)
    else:
        try:
            gates_nrfi = []
            gates_yrfi = []

            # Rule 34: NRFI blocked when game total >= 8.0
            if total_line is not None and total_line >= 8:
                gates_nrfi.append(f'Rule 34: NRFI blocked — Kalshi total line={total_line} >= 8.0')

            # Four-factor composite (simplified from what we have)
            # Factor 1: both pitchers' 1st-inning xERA
            away_fi = away_ps.get('firstInningSplit', {}) or {}
            home_fi  = home_ps.get('firstInningSplit', {}) or {}
            away_fi_xera = away_fi.get('firstInningXERA')
            home_fi_xera  = home_fi.get('firstInningXERA')

            fi_data_missing = []
            if away_fi_xera is None:
                fi_data_missing.append(f'away.pitcherSavant.firstInningSplit.firstInningXERA')
            if home_fi_xera is None:
                fi_data_missing.append(f'home.pitcherSavant.firstInningSplit.firstInningXERA')

            # P(NRFI) = P(away scores 0 in 1st) * P(home scores 0 in 1st)
            # lambda source: lib.research.first_inning_context blends in
            # dedicated first-inning pitcher evidence (firstInningXERA) plus
            # a small shared-platoon-context nudge when available, bounded,
            # and falls back to the exact pre-existing naive proxy
            # (proj / 9 per team) whenever neither is available -- see
            # firstInningContext's own awayLambdaFormula/homeLambdaFormula
            # for exactly which formula produced each value on this game.
            fi_ctx = first_inning_context or {}
            inning1_away = fi_ctx.get('awayLambda1st')
            inning1_home  = fi_ctx.get('homeLambda1st')
            if inning1_away is None:
                inning1_away = (away_proj / 9) if away_proj else None
            if inning1_home is None:
                inning1_home = (home_proj / 9) if home_proj else None

            # First-inning evidence-quality provenance hierarchy (see
            # lib.research.first_inning_context module docstring). When
            # neither dedicated evidence nor a game-level projection is
            # available for a side, no lambda can be derived at all --
            # INSUFFICIENT_DATA means no actionable recommendation, not a
            # PAPER-capped one. Fall back to the raw evidenceQuality the
            # context module already computed; if a lambda is missing but
            # the context somehow disagrees (defensive only), treat it as
            # INSUFFICIENT_DATA rather than crash the Poisson calc below.
            evidence_quality = fi_ctx.get('evidenceQuality')
            first_inning_insufficient_data = (inning1_away is None or inning1_home is None)
            if first_inning_insufficient_data:
                evidence_quality = INSUFFICIENT_DATA

            # Safe-for-computation lambdas only -- when insufficient, the
            # Poisson outputs below are never surfaced as a recommendation
            # (conf is force-blocked further down), they only need to not
            # raise.
            inning1_away_calc = inning1_away if inning1_away is not None else 0.5
            inning1_home_calc  = inning1_home if inning1_home is not None else 0.5

            p_nrfi_away = poisson_pmf(0, inning1_home_calc)  # away scores 0 against home pitcher
            p_nrfi_home  = poisson_pmf(0, inning1_away_calc)   # home scores 0 against away pitcher
            p_nrfi = p_nrfi_away * p_nrfi_home
            p_yrfi = 1.0 - p_nrfi

            # VF from NRFI/YRFI single binary market (mid-derived; kept
            # only for display/audit -- see marketProbVF).
            vf_nrfi = (nrfi_implied or 50) / 100
            vf_yrfi = (yrfi_implied or 50) / 100

            def _tc2(v):
                if v is None: return None
                f = float(v); return round(f * 100 if f <= 1.0 else f, 2)
            rfi_yes_bid = rfi.get('yrfi_bid')
            rfi_yes_ask = rfi.get('yrfi_ask')

            # NRFI has no yes_ask of its own on this market -- it is priced
            # as the complement of the YRFI market's bid (100 - yrfi_bid),
            # the same executable-side derivation the row builder below
            # always used.
            nrfi_executable = round(100 - _tc2(rfi_yes_bid), 2) if rfi_yes_bid is not None else None
            yrfi_yes_ask_c = _tc2(rfi.get('yrfi_ask')) if rfi.get('yrfi_ask') is not None else None

            ef_nrfi = build_edge_fields(p_nrfi, vf_nrfi, nrfi_executable, CAL_MEDIUM, snapshot_ts, series_ticker='KXMLBRFI')
            ef_yrfi = build_edge_fields(p_yrfi, vf_yrfi, yrfi_yes_ask_c, CAL_MEDIUM, snapshot_ts, series_ticker='KXMLBRFI')

            # Executable EV / bet-up-to correctness: eligibility gates on
            # netExecutableEdge (fee-aware) -- Production Fee-Aware Net EV
            # Integration milestone. NRFI and YRFI are audited and gated
            # fully independently (own executable price, own fee-adjusted
            # break-even, own net edge) -- never derived from one another.
            edge_nrfi = ef_nrfi['netExecutableEdge']
            edge_yrfi = ef_yrfi['netExecutableEdge']

            conf_nrfi = confidence_from_edge(edge_nrfi)
            conf_yrfi = confidence_from_edge(edge_yrfi)

            if gates_nrfi:
                conf_nrfi = None

            # Tier/confidence calibration: see cap_tier_for_disagreement().
            # Placed AFTER the Rule 34 check above (which uses gates_nrfi's
            # truthiness as its own "did Rule 34 fire" signal) so appending
            # here never corrupts that check.
            conf_nrfi = cap_tier_for_disagreement(conf_nrfi, ef_nrfi.get('rawEdgeVsVF'), gates_nrfi)
            conf_yrfi = cap_tier_for_disagreement(conf_yrfi, ef_yrfi.get('rawEdgeVsVF'), gates_yrfi)

            # First-inning evidence-quality provenance hierarchy.
            # INSUFFICIENT_DATA is a hard block (no actionable recommendation
            # at all, mirroring Rule 34's NRFI block above) -- distinct from
            # GENERIC_FALLBACK/FIRST_INNING_PARTIAL, which only cap the tier
            # (see cap_tier_for_first_inning_evidence_quality).
            if evidence_quality == INSUFFICIENT_DATA:
                insuff_msg = (
                    'First-inning evidence quality: INSUFFICIENT_DATA — no game-level run '
                    'projection available for at least one side; no NRFI/YRFI probability '
                    'can be computed'
                )
                gates_nrfi.append(insuff_msg)
                gates_yrfi.append(insuff_msg)
                conf_nrfi = None
                conf_yrfi = None
            else:
                conf_nrfi = cap_tier_for_first_inning_evidence_quality(conf_nrfi, evidence_quality, gates_nrfi)
                conf_yrfi = cap_tier_for_first_inning_evidence_quality(conf_yrfi, evidence_quality, gates_yrfi)

            # Rule 52: YRFI/NRFI lineup gate — uses lineupConfirmedOfficial per Phase 1B.
            if not (away_lineup_official and home_lineup_official):
                missing_sides_rfi = []
                if not away_lineup_official: missing_sides_rfi.append('away')
                if not home_lineup_official: missing_sides_rfi.append('home')
                rfi_gate_msg = f'Rule 52: YRFI/NRFI requires confirmed lineups for both teams — {", ".join(missing_sides_rfi)} unconfirmed (lineupConfirmedOfficial=False) → PAPER'
                gates_nrfi.append(rfi_gate_msg)
                gates_yrfi.append(rfi_gate_msg)
                if conf_nrfi not in (None,): conf_nrfi = 'PAPER'
                if conf_yrfi not in (None,): conf_yrfi  = 'PAPER'

            # Build NRFI / YRFI rows
            # Rule 40: four-factor composite required for NRFI/YRFI.
            # If Factor 1 (both pitchers' 1st-inning xERA) is missing, the composite is
            # incomplete — maximum allowed status is PAPER for BOTH NRFI and YRFI.
            # This is enforced here in the ledger (not just in explanatory text) so that
            # no YRFI or NRFI can be classified as MEDIUM or HIGH when 1st-inning xERA
            # data is absent.  Same gate fires for both sides of the binary market.
            nrfi_notes = f'1st-inn approx: away={inning1_away:.3f} home={inning1_home:.3f} R/inn'
            yrfi_notes_extra = ''
            if fi_data_missing:
                rule40_msg = (
                    f'Rule 40 incomplete — first-inning xERA missing for: '
                    f'{fi_data_missing}; paper cap applied'
                )
                gates_nrfi.append(rule40_msg)
                gates_yrfi.append(rule40_msg)
                nrfi_notes += f' | Missing Factor 1 (1st-inn xERA): {fi_data_missing} — Paper cap'
                yrfi_notes_extra = f' | Missing Factor 1 (1st-inn xERA): {fi_data_missing} — Paper cap'
                if conf_nrfi not in (None,): conf_nrfi = 'PAPER'
                if conf_yrfi not in (None,): conf_yrfi = 'PAPER'

            # Hard bet-up-to ceiling enforcement (see enforce_bet_up_to).
            conf_nrfi, gates_nrfi, nrfi_max_bet_gross, nrfi_max_bet_net = enforce_bet_up_to(
                p_nrfi, nrfi_executable, conf_nrfi, gates_nrfi, fee_multiplier=ef_nrfi.get('feeMultiplier'))
            conf_yrfi, gates_yrfi, yrfi_max_bet_gross, yrfi_max_bet_net = enforce_bet_up_to(
                p_yrfi, yrfi_yes_ask_c, conf_yrfi, gates_yrfi, fee_multiplier=ef_yrfi.get('feeMultiplier'))
            nrfi_max_bet = nrfi_max_bet_net if nrfi_max_bet_net is not None else nrfi_max_bet_gross
            yrfi_max_bet = yrfi_max_bet_net if yrfi_max_bet_net is not None else yrfi_max_bet_gross

            if conf_nrfi is None:
                row = rejected_row(
                    'NRFI',
                    reason=gates_nrfi[0] if gates_nrfi else f'edge {edge_nrfi}% below {THRESHOLD_PAPER}% floor',
                    kalshiPrice=nrfi_am, kalshiVF=round(vf_nrfi*100,2),
                    modelProb=round(p_nrfi*100,2), gatesFired=gates_nrfi,
                    notes=nrfi_notes,
                    **ef_nrfi,
                    maxBetPrice=nrfi_max_bet, betUpToPriceGross=nrfi_max_bet_gross, betUpToPriceNet=nrfi_max_bet_net,
                    **proj_context, **away_lineup_ctx,
                    firstInningContext=fi_ctx,
                )
                row['reasonCodes'] = build_reason_codes('Rejected', row)
                rows['NRFI'] = row
            else:
                row = accepted_row(
                    'NRFI',
                    kalshiPrice=nrfi_am, kalshiImplied=nrfi_implied, kalshiVF=round(vf_nrfi*100,2),
                    modelProb=round(p_nrfi*100,2),
                    confidence=conf_nrfi, betSize=bet_size(conf_nrfi, 'NRFI'),
                    notes=nrfi_notes,
                    gatesFired=gates_nrfi,
                    **ef_nrfi,
                    maxBetPrice=nrfi_max_bet, betUpToPriceGross=nrfi_max_bet_gross, betUpToPriceNet=nrfi_max_bet_net,
                    confidenceTier=conf_nrfi,
                    **identity(rfi.get('ticker'), 'KXMLBRFI'),
                    **proj_context,
                    **away_lineup_ctx,
                    firstInningContext=fi_ctx,
                )
                row['reasonCodes'] = build_reason_codes('Accepted', row)
                rows['NRFI'] = row

            yrfi_notes = f'P(YRFI)={p_yrfi*100:.1f}% (1-NRFI)' + yrfi_notes_extra
            if conf_yrfi is None:
                row = rejected_row(
                    'YRFI',
                    reason=gates_yrfi[0] if gates_yrfi else f'edge {edge_yrfi}% below {THRESHOLD_PAPER}% floor',
                    kalshiPrice=yrfi_am, kalshiVF=round(vf_yrfi*100,2),
                    modelProb=round(p_yrfi*100,2), gatesFired=gates_yrfi,
                    notes=yrfi_notes,
                    **ef_yrfi,
                    maxBetPrice=yrfi_max_bet, betUpToPriceGross=yrfi_max_bet_gross, betUpToPriceNet=yrfi_max_bet_net,
                    **proj_context, **away_lineup_ctx,
                    firstInningContext=fi_ctx,
                )
                row['reasonCodes'] = build_reason_codes('Rejected', row)
                rows['YRFI'] = row
            else:
                row = accepted_row(
                    'YRFI',
                    kalshiPrice=yrfi_am, kalshiImplied=yrfi_implied, kalshiVF=round(vf_yrfi*100,2),
                    modelProb=round(p_yrfi*100,2),
                    confidence=conf_yrfi, betSize=bet_size(conf_yrfi, 'YRFI'),
                    notes=yrfi_notes,
                    gatesFired=gates_yrfi,
                    **ef_yrfi,
                    maxBetPrice=yrfi_max_bet, betUpToPriceGross=yrfi_max_bet_gross, betUpToPriceNet=yrfi_max_bet_net,
                    confidenceTier=conf_yrfi,
                    **identity(rfi.get('ticker'), 'KXMLBRFI'),
                    **proj_context,
                    **away_lineup_ctx,
                    firstInningContext=fi_ctx,
                )
                row['reasonCodes'] = build_reason_codes('Accepted', row)
                rows['YRFI'] = row
        except Exception as e:
            import traceback as _tb
            _tbstr = _tb.format_exc()
            for _mkt in ('NRFI', 'YRFI'):
                if _mkt not in rows:
                    _r = failed_row(_mkt, f'{type(e).__name__}: {e}')
                    _r['evaluationError'] = f'{type(e).__name__}: {e}' + '\n' + _tbstr[:400]
                    rows[_mkt] = _r

    # ── Ensure all required markets have a row ─────────────────────────────
    for mkt in REQUIRED_MARKETS:
        if mkt not in rows:
            rows[mkt] = failed_row(
                mkt,
                f'Market not evaluated — missing from evaluation logic (programming error)'
            )

    # Rule 71 patch: apply bet_eligibility_status, clv_capture_status, review_integrity_status
    # to every row AFTER all edge/confidence/price logic is complete.
    # apply_eligibility() NEVER changes status/edge/confidence/betSize.
    # Missing CLV data does NOT block a live actionable bet.
    result_rows = [rows[m] for m in REQUIRED_MARKETS]
    for row in result_rows:
        apply_eligibility(row, clv_snapshot_captured=None)
    return result_rows


def _check_accepted_identity_and_concentration(games):
    """
    Post-build checks:
    1. Warn if any Accepted row has null marketTicker.
    2. Non-blocking portfolio concentration warning (real bets only).
    """
    import sys
    from collections import Counter

    accepted_bets = []

    for g in games:
        away_abbr = g.get('away', {}).get('abbr', '?')
        home_abbr = g.get('home', {}).get('abbr', '?')
        game_id   = f'{away_abbr}@{home_abbr}'
        for row in g.get('marketLedger', []):
            if row.get('status') == 'Accepted':
                if not row.get('marketTicker'):
                    print(
                        f'DATA-HEALTH WARNING: Accepted row has null marketTicker '
                        f'for market {row.get("market")} game {game_id}',
                        file=sys.stderr
                    )
                accepted_bets.append({
                    'market':  row.get('market'),
                    'betType': row.get('betType', 'REAL'),
                })

    # Portfolio concentration warning (non-blocking)
    real_bets = [b for b in accepted_bets if b.get('betType') != 'PAPER']
    if real_bets:
        market_counts = Counter(b['market'] for b in real_bets)
        total = len(real_bets)
        for market, count in market_counts.items():
            pct = count / total
            if pct > 0.45:
                print(
                    f'PORTFOLIO WARNING: {count}/{total} accepted real bets are {market}. '
                    f'Review concentration before placing full card.'
                )


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    path = os.path.join(os.path.dirname(__file__), '..', 'data', 'slate.json')
    with open(path) as f:
        slate = json.load(f)

    games = slate.get('games', [])

    # ── Phase 6 Part 7: compute each game's canonical projection context
    # exactly ONCE, before either consumer below. This list is the single
    # source of truth for both the projections.json artifact and
    # evaluate_game()'s row generation further down -- computed
    # unconditionally here, outside and ahead of the artifact-write
    # try/except below, so an artifact-publication failure (I/O, disk,
    # whatever) can never prevent or alter recommendation generation,
    # which depends only on this list, never on the artifact write
    # succeeding (Part 13 failure isolation). Before this phase, main()
    # called compute_projections() independently here AND evaluate_game()
    # called it again internally on the same game object -- the two
    # calls always agreed (compute_projections() is pure, and nothing
    # mutates a game's projection-input fields between them) but were
    # not structurally guaranteed to; this list makes that guarantee
    # exact by construction; see compute_game_projection_context()'s and
    # evaluate_game()'s docstrings.
    game_contexts = [compute_game_projection_context(g) for g in games]

    # ── Phase 4 immutable pipeline: Projection Layer artifact ──────────────
    # Snapshotting projection values here, before any market row is built,
    # captures the earliest point in this script where every game's
    # projection values are fully available but no recommendation
    # decision has been made — see docs/IMMUTABLE_PIPELINE.md's
    # Projection Layer section for the code-derived reasoning behind this
    # boundary.
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'lib'))
        from pipeline_artifacts import write_stage_artifact as _write_projections_artifact
        _proj_games = []
        for _g, _ctx in zip(games, game_contexts):
            _proj_games.append({
                'away': _g.get('away', {}).get('abbr', '?'),
                'home': _g.get('home', {}).get('abbr', '?'),
                'kalshiKey': _g.get('kalshiKey'),
                'gameId': _g.get('gameId'),
                'awayProjRuns': _ctx['awayProjRuns'],
                'homeProjRuns': _ctx['homeProjRuns'],
                'totalProj': _ctx['totalProj'],
                'f5AwayProj': _ctx['f5AwayProj'],
                'f5HomeProj': _ctx['f5HomeProj'],
                'missingFields': _ctx['missingFields'],
                'excludedFromSlate': bool(_g.get('excludedFromSlate', False)),
            })
        _write_projections_artifact(
            'projections', slate.get('date', ''),
            {'date': slate.get('date', ''), 'games': _proj_games},
            produced_by='scripts/build_market_ledger.py',
            status='canonical',
            source_stage='normalized_slate',
        )
    except Exception as _e:
        print(f'WARNING: could not write projections pipeline artifact: {_e}')

    total_rows = 0
    status_counts = {s: 0 for s in ['Accepted', 'Rejected', 'Missing Data', 'Evaluation Failed']}

    for g, ctx in zip(games, game_contexts):
        away = g.get('away', {}).get('abbr', '?')
        home  = g.get('home', {}).get('abbr', '?')

        # Skip quarantined games — all their markets are excluded from real-money
        if g.get('excludedFromSlate'):
            reason = g.get('exclusionReason', 'QUARANTINED')
            ledger = [
                rejected_row(m, f'EXCLUDED: {reason}')
                for m in REQUIRED_MARKETS
            ]
            g['marketLedger'] = ledger
            print(f'{away}@{home}: EXCLUDED (quarantined) — {reason[:80]}')
            continue

        try:
            # Phase 6 Part 10: pass the already-computed context so
            # evaluate_game() does not recompute compute_projections(g)
            # a second time -- see game_contexts' construction above.
            ledger = evaluate_game(g, projection_context=ctx)
        except Exception as e:
            import traceback as _tb
            _tbstr = _tb.format_exc()
            print(f'ERROR evaluating {away}@{home}: {type(e).__name__}: {e}', file=sys.stderr)
            print(_tbstr, file=sys.stderr)
            _errmsg = f'Game-level error: {type(e).__name__}: {e}' + '\n' + _tbstr[:600]
            ledger = [failed_row(m, _errmsg) for m in REQUIRED_MARKETS]

        # Validate completeness before writing
        ledger_markets = {row['market'] for row in ledger}
        for req in REQUIRED_MARKETS:
            if req not in ledger_markets:
                ledger.append(failed_row(req, 'Not evaluated — missing from ledger'))

        g['marketLedger'] = ledger
        total_rows += len(ledger)

        for row in ledger:
            s = row.get('status', 'Evaluation Failed')
            status_counts[s] = status_counts.get(s, 0) + 1

        # Print game summary
        accepted = [r['market'] for r in ledger if r['status'] == 'Accepted']
        missing  = [r['market'] for r in ledger if r['status'] == 'Missing Data']
        failed   = [r['market'] for r in ledger if r['status'] == 'Evaluation Failed']
        print(f'{away}@{home}: {len(ledger)} rows | '
              f'Accepted={len(accepted)} Rejected={len(ledger)-len(accepted)-len(missing)-len(failed)} '
              f'MissingData={len(missing)} Failed={len(failed)}')
        if accepted: print(f'  ACCEPTED: {accepted}')
        if missing:  print(f'  MISSING:  {missing}')
        if failed:   print(f'  FAILED:   {failed}')

    with open(path, 'w') as f:
        json.dump(slate, f)

    # ── Phase 3 immutable pipeline: also publish this stage's output as
    # its own artifact (data/pipeline/<date>/recommendations.json).
    # build_market_ledger.py is what populates marketLedger (the
    # Recommendation Layer, see docs/IMMUTABLE_PIPELINE.md), before
    # risk_gate.py's portfolio decisions run. Purely additive —
    # best-effort, never allowed to affect the primary data/slate.json
    # write above, which is already complete by this point.
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'lib'))
        from pipeline_artifacts import write_stage_artifact as _write_stage_artifact
        _write_stage_artifact(
            'recommendations', slate.get('date', ''), slate,
            produced_by='scripts/build_market_ledger.py',
            status='transitional',
            source_stage='projections',
        )
    except Exception as _e:
        print(f'WARNING: could not write recommendations pipeline artifact: {_e}')

    print(f'\nTotal: {len(games)} games, {total_rows} market rows')
    for s, c in status_counts.items():
        print(f'  {s}: {c}')
    print(f'Written marketLedger to all games in {path}')

    # F5 moneyline visibility check — final pipeline stage
    # Counts F5_ML rows in the completed ledger by status.
    # Missing Data = price never reached slate. Rejected/Accepted = price present.
    _f5_accepted   = 0
    _f5_rejected   = 0
    _f5_missing    = 0
    _f5_failed     = 0
    for g in games:
        for row in g.get('marketLedger', []):
            if row.get('market') in ('F5_ML_Away', 'F5_ML_Home'):
                s = row.get('status', '')
                if s == 'Accepted':      _f5_accepted += 1
                elif s == 'Rejected':    _f5_rejected += 1
                elif s == 'Missing Data': _f5_missing += 1
                else:                    _f5_failed   += 1
    _f5_with_price = _f5_accepted + _f5_rejected  # price present = evaluated (not missing)
    _f5_total_rows = _f5_accepted + _f5_rejected + _f5_missing + _f5_failed
    _games_with_f5 = _f5_with_price // 2  # 2 rows per game (Away + Home)
    print(f'\n[F5-VISIBILITY] F5_ML rows in ledger: {_f5_total_rows} total '
          f'(Accepted={_f5_accepted} Rejected={_f5_rejected} MissingData={_f5_missing} Failed={_f5_failed})')
    print(f'[F5-VISIBILITY] Games with F5 moneyline price in final slate: {_games_with_f5}/{len(games)}')
    if _f5_missing > 0 and _f5_with_price == 0:
        print('[F5-VISIBILITY] WARNING: F5 moneyline discovery succeeded but mapping into the slate failed.')
        print('[F5-VISIBILITY] All F5_ML rows show Missing Data — price never reached odds.kalshi.f5ml.')
        print('[F5-VISIBILITY] Root cause: check parse_suffix() in build_kalshi_registry.py (June 8 bug pattern).')
    elif _f5_missing > 0:
        print(f'[F5-VISIBILITY] NOTE: {_f5_missing} F5_ML rows still Missing Data '
              f'(partial — {_games_with_f5} game(s) have prices, {_f5_missing // 2} do not).')
    elif _f5_with_price > 0:
        print(f'[F5-VISIBILITY] OK: F5 moneyline prices present in final slate for all {_games_with_f5} game(s) evaluated.')

    # ── F5 sportsbook vs Kalshi distinction ──────────────────────────────────
    # If sportsbook F5 odds are present but Kalshi F5 is missing, emit targeted warnings.
    # Never say "F5 not offered on any book" when FD/DK/MGM F5 data is present.
    _sb_f5_games = 0
    _kal_f5_games = 0
    for _gf in games:
        _odds = _gf.get('odds') or {}
        _sb_f5 = (
            (_odds.get('fanduel') or {}).get('f5ml') or
            (_odds.get('draftkings') or {}).get('f5ml') or
            (_odds.get('betmgm') or {}).get('f5ml')
        )
        _kal_f5 = (_odds.get('kalshi') or {}).get('f5ml') or {}
        if _sb_f5:
            _sb_f5_games += 1
        if _kal_f5.get('away') is not None:
            _kal_f5_games += 1

    if _sb_f5_games > 0 and _kal_f5_games == 0:
        print(f'[F5-VISIBILITY] Sportsbook F5 available ({_sb_f5_games} game(s) on FD/DK/MGM); '
              f'Kalshi KXMLBF5 missing — cannot log Kalshi F5 bet.')
        print('[F5-VISIBILITY] Rule 25 NOTE: F5 analysis uses sportsbook odds but Kalshi KXMLBF5 price unavailable.')
        print('[F5-VISIBILITY] Sportsbook F5 odds detected: FD/DK/MGM.')
    elif _sb_f5_games > 0 and _kal_f5_games > 0:
        print(f'[F5-VISIBILITY] OK: Sportsbook F5 ({_sb_f5_games} games) AND Kalshi F5 ({_kal_f5_games} games) both present.')
    elif _sb_f5_games == 0:
        print('[F5-VISIBILITY] NOTE: No sportsbook F5 odds detected (FD/DK/MGM all absent).')

    # Post-build: identity check and portfolio concentration warning
    _check_accepted_identity_and_concentration(games)


if __name__ == '__main__':
    main()
