#!/usr/bin/env python3
"""
lib/research/hitter_pricing.py
=================================
Hitter Projection Engine -- Phase 4 fair-odds / edge pricing for
archived hitter Kalshi contracts.

Every hitter-prop market this repository has confirmed as a real, live
Kalshi series (lib.research.market_taxonomy: KXMLBHIT/hitter_hits,
KXMLBTB/hitter_total_bases, KXMLBHRR/hitter_hits_runs_rbis,
KXMLBRBI/hitter_rbis) is a literal "N+" AT_LEAST contract
(lib.edgelab.player_prop_settlement's own docstring, 46,784-row real-
data audit) -- so pricing here is always "model P(stat >= N)" against
"market-implied P(stat >= N)" for the SAME N, never a half-point
Over/Under framing. hitter_stolen_bases is a fifth confirmed real
series but is explicitly out of this mission's scope (no stolen-base
projection).

AMERICAN ODDS AS PRIMARY (per this mission's explicit instruction).
The conversion below is the same standard formula this repository
already uses at ingestion time (scripts/build_kalshi_registry.py's own
`american()`, cross-checked here rather than imported directly since
that module's import chain triggers live Kalshi-registry side effects
this pure pricing function must not carry).

THIS MODULE NEVER TOUCHES STAKING, BANKROLL, THE RECOMMENDATION GATE,
THE DISAGREEMENT CAP, THE CORRELATION GATE, THE CANONICAL BET LEDGER,
OR SETTLEMENT/POSTMORTEM LOGIC -- it supplies a fair price and a raw
edge/EV number only. Per this mission's explicit instruction, manual
analysis remains the final betting decision-maker; nothing here is
wired into scripts/risk_gate.py, scripts/write_pending_bets.py, or any
other production betting-decision path.
"""
from typing import Optional


def fair_american_odds(prob: float) -> Optional[int]:
    """
    Standard American-odds conversion from a probability in (0, 1).
    Mirrors scripts/build_kalshi_registry.py's own `american()`
    formula exactly (prob>=0.5 -> negative/favorite odds, prob<0.5 ->
    positive/underdog odds). Returns None for a prob outside (0, 1)
    (never a fabricated odds number for a certain/impossible outcome).
    """
    if prob is None or prob <= 0.0 or prob >= 1.0:
        return None
    if prob >= 0.5:
        return round(-(prob / (1 - prob)) * 100)
    return round(((1 - prob) / prob) * 100)


def american_odds_to_implied_prob(odds: Optional[int]) -> Optional[float]:
    """Inverse of fair_american_odds -- used to convert an executable Kalshi price's own American-odds framing back to a probability for edge comparison."""
    if odds is None or odds == 0:
        return None
    if odds < 0:
        return -odds / (-odds + 100.0)
    return 100.0 / (odds + 100.0)


def price_hitter_contract(model_prob: float, executable_yes_price: Optional[float]) -> dict:
    """
    `model_prob`: this engine's own P(stat >= N) for one contract (from
    lib.research.hitter_market_distributions's `atLeast` map).
    `executable_yes_price`: the real tradable YES price in dollars
    (0..1 -- e.g. a Kalshi mid/ask already resolved by the caller from
    an archived market snapshot), or None if no executable price is
    available for this rung right now.

    Returns every field this mission's board schema requires for one
    contract: exact model probability, fair American odds, the
    executable Kalshi price and its own American-odds framing, raw
    probability edge, and expected value of a $1 YES stake at the
    executable price using the MODEL probability (never the market's
    own probability -- that would trivially always show zero edge).
    """
    fair_odds = fair_american_odds(model_prob)
    result = {
        "modelProbability": round(model_prob, 4),
        "fairAmericanOdds": fair_odds,
        "executableYesPrice": executable_yes_price,
        "executableAmericanOdds": None,
        "rawProbabilityEdge": None,
        "expectedValuePerDollar": None,
        "pricingStatus": "NO_EXECUTABLE_PRICE",
    }
    if executable_yes_price is None or not (0.0 < executable_yes_price < 1.0):
        return result

    market_implied_prob = executable_yes_price
    executable_odds = fair_american_odds(market_implied_prob)
    edge = model_prob - market_implied_prob

    # EV per $1 YES stake at `executable_yes_price`: win $((1/price)-1)
    # with probability model_prob, lose $1 with probability (1-model_prob).
    win_payout = (1.0 / executable_yes_price) - 1.0
    ev_per_dollar = model_prob * win_payout - (1.0 - model_prob) * 1.0

    result.update({
        "executableAmericanOdds": executable_odds,
        "rawProbabilityEdge": round(edge, 4),
        "expectedValuePerDollar": round(ev_per_dollar, 4),
        "pricingStatus": "PRICED",
    })
    return result
