#!/usr/bin/env python3
"""
lib/research/expression_group.py
====================================
Systematic Best-Expression Comparison mission: a canonical relation
between every currently-available way to express ONE team's early-game
thesis ("this team should not be trailing / should win") on Kalshi --
F3 winner, F5 winner (YES), the opponent's F5 NO (tie-protected), F7
winner, full-game moneyline, and the run-line/winning-margin side.

THE GAP THIS MODULE CLOSES
-----------------------------
Before this module, a reader had to manually correlate up to four
separate places to see all the expressions of one side's thesis:
scripts/build_market_ledger.py's F5_ML_Away/Home row (F5 YES),
lib.research.f5_tie_tax's `tieTaxComparison` sub-object (opponent F5
NO), the separate ML_Away/ML_Home row (full-game ML), plus F3/F7/
run-line data that reached data/slate.json (Market-Universe Parity
mission) but had no comparison surface at all. This module assembles
all of them into ONE ordered list per side, `expressionGroup`, attached
to that side's F5 row alongside (not replacing) the existing
`tieTaxComparison`/`fullGameMLComparison` fields.

WHAT THIS MODULE DOES NOT DO
-------------------------------
- It does not invent a model probability for F3, F7, or the run-line/
  winning-margin side. Production has no calibrated model for any of
  these today (F3/F7: lib.research.market_taxonomy.HORIZON_MARKET_STATUS
  marks them productionEnabled=False; lib.research.three_way_projection's
  own docstring calls its horizon-fraction scaling a "RESEARCH-ONLY
  placeholder... NOT a claim that naive linear scaling is the right
  model"; RL: scripts/build_market_ledger.py's RL_Away/RL_Home rows are
  suspended per Rule 81 and never receive a modelProb). Each of those
  three entries carries `modelProbability: None`,
  `supportsModelProbability: False`, `grossEdge: None`,
  `netExecutableEdge: None` -- only `feeAdjustedBreakEvenProbability`
  (a pure function of price alone, no model opinion required) is ever
  computed for them.
- It does not rank, recommend, or auto-select a "best" expression. Every
  entry is returned side by side with its own numbers; ChatGPT (or a
  human) compares them manually, matching this repository's existing
  manual-analysis philosophy (RULES.md) and lib.research.f5_tie_tax's own
  "does not automatically prefer NO contracts" precedent.
- It does not recompute any probability/edge/fee number a canonical
  source already produced -- every field is read off an already-built
  scripts/build_market_ledger.py row, an already-built
  lib.research.f5_tie_tax leg, or computed via
  lib.edgelab.kalshi_fees.fee_adjusted_break_even_probability (the same
  fee engine every other net-EV computation in this repo uses).
- It does not change bankroll sizing, staking, or which entries reach
  scripts/build_market_ledger.py's REQUIRED_MARKETS real-money gate.

CORRELATION LABELING
-----------------------
Each entry's relationship to the F5 YES entry (this side's primary,
already-production-enabled expression) is classified via
lib.edgelab.thesis_classification.classify_pair_severity() -- the
SAME DUPLICATE_THESIS/MODERATELY_CORRELATED/INDEPENDENT_THESIS engine
scripts/risk_gate.py's correlation gate already mirrors, never a new,
independently-invented vocabulary. That module's own
`_MARKET_THESIS_TAGS`/`_WIN_THESIS_FAMILIES` were extended (Systematic
Best-Expression Comparison mission) to recognize F3_ML_*/F7_ML_*/RL_*
market names -- previously absent, which silently produced a wrong
INDEPENDENT_THESIS classification for e.g. an F3 YES paired with the
same team's ML/F5 (see that module's own changelog comment).

SCOPE / SAFETY
---------------
Every function here is pure: no I/O, no network, no clock reads, no
mutation of any argument.
"""

from lib.edgelab.kalshi_fees import FEE_TYPE_TAKER, fee_adjusted_break_even_probability
from lib.edgelab.thesis_classification import classify_pair_severity

FAMILY_F3_WIN = "F3_WIN"
FAMILY_F5_WIN = "F5_WIN"
FAMILY_F5_PROTECTED_NO = "F5_PROTECTED_NO"
FAMILY_F7_WIN = "F7_WIN"
FAMILY_FULL_GAME_ML = "FULL_GAME_ML"
FAMILY_WINNING_MARGIN = "WINNING_MARGIN"

HORIZON_F3 = "F3"
HORIZON_F5 = "F5"
HORIZON_F7 = "F7"
HORIZON_FULL_GAME = "FULL_GAME"

# family -> {'away': market-ledger-style name, 'home': ...}. F3_ML_*/
# F7_ML_*/RL_* are the SAME market-family-string names
# lib.edgelab.thesis_classification's _MARKET_THESIS_TAGS/
# _WIN_THESIS_FAMILIES were extended to recognize (this module's own
# _thesis_entry() below uses them directly, never a second naming
# convention). RL_Away/RL_Home are real scripts/build_market_ledger.py
# row names (Rule-81-suspended, never a real model probability); F3_ML_*/
# F7_ML_* are NOT marketLedger rows (F3/F7 are research-only,
# productionEnabled=False) -- used here purely as the identity key
# classify_pair_severity() and this module's own market label read.
_FAMILY_MARKET_NAME = {
    FAMILY_F3_WIN: {"away": "F3_ML_Away", "home": "F3_ML_Home"},
    FAMILY_F5_WIN: {"away": "F5_ML_Away", "home": "F5_ML_Home"},
    FAMILY_F7_WIN: {"away": "F7_ML_Away", "home": "F7_ML_Home"},
    FAMILY_FULL_GAME_ML: {"away": "ML_Away", "home": "ML_Home"},
    FAMILY_WINNING_MARGIN: {"away": "RL_Away", "home": "RL_Home"},
}

_FAMILY_HORIZON = {
    FAMILY_F3_WIN: HORIZON_F3,
    FAMILY_F5_WIN: HORIZON_F5,
    FAMILY_F5_PROTECTED_NO: HORIZON_F5,
    FAMILY_F7_WIN: HORIZON_F7,
    FAMILY_FULL_GAME_ML: HORIZON_FULL_GAME,
    FAMILY_WINNING_MARGIN: HORIZON_FULL_GAME,
}


def _thesis_entry(family, side, game_id, side_abbr):
    """The small dict lib.edgelab.thesis_classification.classify_pair_severity() expects."""
    market_name = _FAMILY_MARKET_NAME[family][side]
    entry = {"market": market_name, "gameId": game_id}
    if side == "away":
        entry["awayAbbr"] = side_abbr
    else:
        entry["homeAbbr"] = side_abbr
    return entry


def _correlation_vs_primary(family, side, game_id, side_abbr):
    """
    Classifies `family`'s relationship to this side's F5 YES entry (the
    primary, already-production-enabled expression) via the canonical
    lib.edgelab.thesis_classification engine. Returns
    {'severity':..., 'tags': [...]}. The F5 YES entry itself is always
    reported as DUPLICATE_THESIS of itself by convention (identical
    market), matching how a caller comparing "this expression vs the
    primary" would expect the primary's own self-relationship to read.
    """
    if family == FAMILY_F5_WIN:
        severity, tags = "DUPLICATE_THESIS", frozenset({"IDENTICAL_MARKET"})
    elif family == FAMILY_F5_PROTECTED_NO:
        # Not a registered market name in lib.edgelab.thesis_classification
        # (it trades on the OPPOSING side's own F5 ticker, NO side -- not
        # a market this repository ever stakes on its own row) -- but it
        # is, by construction (lib.research.f5_tie_tax's own module
        # docstring), literally the tie-protected alternate expression of
        # the identical "this side should not be trailing after five"
        # thesis as F5 YES, so it is always DUPLICATE_THESIS with an
        # explicit tag naming why, rather than routed through the
        # generic classifier for a market name that was never meant to
        # be registered there.
        severity, tags = "DUPLICATE_THESIS", frozenset({"TIE_PROTECTED_ALTERNATE_EXPRESSION"})
    else:
        primary = _thesis_entry(FAMILY_F5_WIN, side, game_id, side_abbr)
        candidate = _thesis_entry(family, side, game_id, side_abbr)
        severity, tags = classify_pair_severity(primary, candidate)
    return {"severity": severity, "tags": sorted(tags)}


def price_only_reference(family, side, game_id, side_abbr, price_cents, payoff_condition,
                          *, ticker=None, fee_type=FEE_TYPE_TAKER):
    """
    One reference for a family with NO supported model probability today
    (F3, F7, run-line/winning-margin). Never fabricates
    modelProbability/grossEdge/netExecutableEdge -- only the
    fee-adjusted break-even (a pure function of price alone) is
    computed. Returns None when `price_cents` is None (nothing to show,
    never a fabricated placeholder).
    """
    if price_cents is None:
        return None
    break_even = fee_adjusted_break_even_probability(price_cents / 100.0, fee_type=fee_type)
    return {
        "family": family,
        "market": _FAMILY_MARKET_NAME[family][side],
        "horizon": _FAMILY_HORIZON[family],
        "ticker": ticker,
        "priceCents": round(price_cents, 2),
        "modelProbability": None,
        "supportsModelProbability": False,
        "feeAdjustedBreakEvenProbability": round(break_even * 100, 3) if break_even is not None else None,
        "grossEdge": None,
        "netExecutableEdge": None,
        "betUpToPriceGross": None,
        "betUpToPriceNet": None,
        "tieProtected": None,
        "payoffCondition": payoff_condition,
        "correlationWithPrimary": _correlation_vs_primary(family, side, game_id, side_abbr),
    }


def reference_from_ledger_row(family, side, game_id, side_abbr, row, *, tie_protected=None,
                               payoff_condition=None, ticker=None):
    """
    One reference built by cross-referencing an ALREADY-COMPUTED
    scripts/build_market_ledger.py row (F5_ML_Away/Home, ML_Away/Home)
    -- reads build_edge_fields()'s already-computed fields verbatim,
    never recomputes a probability/edge/fee number a second time.
    Returns None when `row` is None.
    """
    if row is None:
        return None
    return {
        "family": family,
        "market": _FAMILY_MARKET_NAME[family][side],
        "horizon": _FAMILY_HORIZON[family],
        "ticker": ticker,
        "priceCents": row.get("executablePriceUsed"),
        "modelProbability": row.get("modelProb"),
        "supportsModelProbability": row.get("modelProb") is not None,
        "feeAdjustedBreakEvenProbability": row.get("feeAdjustedBreakEvenProbability"),
        "grossEdge": row.get("calibratedEdgeVsExecutable"),
        "netExecutableEdge": row.get("netExecutableEdge"),
        "betUpToPriceGross": row.get("betUpToPriceGross"),
        "betUpToPriceNet": row.get("betUpToPriceNet"),
        "tieProtected": tie_protected,
        "payoffCondition": payoff_condition,
        "status": row.get("status"),
        "correlationWithPrimary": _correlation_vs_primary(family, side, game_id, side_abbr),
    }


def reference_from_tie_tax_leg(family, side, game_id, side_abbr, leg, *, tie_protected, ticker=None):
    """
    One reference built from a leg (threeWayYes or protectedNo) of
    lib.research.f5_tie_tax.evaluate_f5_tie_tax()'s output. Rescales
    that module's 0-1-fraction probabilities to the 0-100 percent scale
    every other entry in this module (and the rest of
    scripts/build_market_ledger.py) uses, for internal consistency
    within one output object -- never a second probability computation.
    grossEdge/netExecutableEdge are derived the SAME way
    build_edge_fields() derives rawEdgeVsExecutable/netExecutableEdge
    (model probability minus the executable-price-implied probability,
    and minus the fee-adjusted break-even, respectively) so this entry
    is directly comparable to the row-sourced entries above -- never a
    calibrated figure (f5_tie_tax applies no calibration factor, so
    neither does this).
    """
    if leg is None:
        return None
    model_p = leg.get("trueProbability")
    price_cents = leg.get("priceCents")
    break_even = leg.get("feeAdjustedBreakEvenProbability")
    exec_prob = (price_cents / 100.0) if price_cents is not None else None
    gross_edge = round((model_p - exec_prob) * 100, 3) if (model_p is not None and exec_prob is not None) else None
    net_edge = round((model_p - break_even) * 100, 3) if (model_p is not None and break_even is not None) else None
    return {
        "family": family,
        "market": _FAMILY_MARKET_NAME[FAMILY_F5_WIN][side] if family == FAMILY_F5_WIN else None,
        "horizon": _FAMILY_HORIZON[family],
        "ticker": ticker,
        "priceCents": price_cents,
        "modelProbability": round(model_p * 100, 3) if model_p is not None else None,
        "supportsModelProbability": model_p is not None,
        "feeAdjustedBreakEvenProbability": round(break_even * 100, 3) if break_even is not None else None,
        "grossEdge": gross_edge,
        "netExecutableEdge": net_edge,
        "netExpectedValuePerDollar": leg.get("netExpectedValuePerDollar"),
        "betUpToPriceGross": None,
        "betUpToPriceNet": None,
        "tieProtected": tie_protected,
        "payoffCondition": leg.get("payoffCondition"),
        "reasonCode": leg.get("reasonCode"),
        "correlationWithPrimary": _correlation_vs_primary(family, side, game_id, side_abbr),
    }


def build_expression_group(side, game_id, side_abbr, *, f5_row, f5_protected_no_leg,
                            full_game_ml_row, f3_price_cents=None, f3_ticker=None,
                            f7_price_cents=None, f7_ticker=None,
                            run_line_price_cents=None, run_line_ticker=None,
                            fee_type=FEE_TYPE_TAKER):
    """
    Assembles the full ordered list of currently-available expressions
    of `side`'s early-game thesis for one game. `side` is 'away' or
    'home'; `side_abbr` is that side's team abbreviation (used for
    correlation identity, matching lib.edgelab.thesis_classification's
    awayAbbr/homeAbbr convention).

    `f5_row` is this side's ALREADY-BUILT F5_ML_Away/Home marketLedger
    row (the canonical source for F5 YES's modelProb/netExecutableEdge/
    betUpToPrice*/status) -- deliberately NOT re-derived from
    lib.research.f5_tie_tax's threeWayYes leg, which recomputes the same
    underlying probability via a separate code path lacking
    calibration/bet-up-to; using the row directly avoids maintaining two
    parallel sources of truth for the identical F5 YES contract.
    `f5_protected_no_leg` (tieTaxComparison['protectedNo']) has no such
    row to read from -- the opponent's F5 NO is never itself a
    marketLedger entry -- so it remains sourced from f5_tie_tax directly.

    Entries are included only when their underlying data is present
    (never a fabricated placeholder for a market Kalshi doesn't list
    today) -- the returned list can be shorter than 6 entries. Order:
    F5 YES (primary), opponent F5 NO (protected), full-game ML, F3
    winner, F7 winner, run-line/winning-margin.
    """
    entries = []

    f5_yes = reference_from_ledger_row(
        FAMILY_F5_WIN, side, game_id, side_abbr, f5_row,
        tie_protected=False,
        payoff_condition=f"{side} leads after five innings (loses on a tie)",
    )
    if f5_yes is not None:
        entries.append(f5_yes)

    f5_no = reference_from_tie_tax_leg(
        FAMILY_F5_PROTECTED_NO, side, game_id, side_abbr, f5_protected_no_leg,
        tie_protected=True,
    )
    if f5_no is not None:
        entries.append(f5_no)

    ml = reference_from_ledger_row(
        FAMILY_FULL_GAME_ML, side, game_id, side_abbr, full_game_ml_row,
        tie_protected=False,
        payoff_condition=f"{side} wins the full game (extends exposure through the bullpen, past the F5 window)",
    )
    if ml is not None:
        entries.append(ml)

    f3 = price_only_reference(
        FAMILY_F3_WIN, side, game_id, side_abbr, f3_price_cents,
        payoff_condition=f"{side} leads after three innings (loses on a tie) -- research-only, no production model probability",
        ticker=f3_ticker, fee_type=fee_type,
    )
    if f3 is not None:
        entries.append(f3)

    f7 = price_only_reference(
        FAMILY_F7_WIN, side, game_id, side_abbr, f7_price_cents,
        payoff_condition=f"{side} leads after seven innings (loses on a tie) -- research-only, no production model probability",
        ticker=f7_ticker, fee_type=fee_type,
    )
    if f7 is not None:
        entries.append(f7)

    rl = price_only_reference(
        FAMILY_WINNING_MARGIN, side, game_id, side_abbr, run_line_price_cents,
        payoff_condition=f"{side} wins by more than the contract's own run margin -- Rule 81 suspended, no production model probability",
        ticker=run_line_ticker, fee_type=fee_type,
    )
    if rl is not None:
        entries.append(rl)

    return entries
