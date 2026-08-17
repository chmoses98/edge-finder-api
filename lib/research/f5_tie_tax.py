#!/usr/bin/env python3
"""
lib/research/f5_tie_tax.py
=============================
F3/F5 "tie tax" / contract-structure comparison.

THE GAP THIS MODULE CLOSES
-----------------------------
A starter-driven F5 thesis is usually phrased as "Team A should not be
trailing after five" -- but the production system has only ever offered
one expression of that thesis: the three-way F5 winner market's YES
contract on Team A (F5_ML_Away/F5_ML_Home in
scripts/build_market_ledger.py). That contract pays 1 only if Team A
OUTRIGHT LEADS after five; it pays ZERO on a tie, exactly like every
other three-way leg (see lib/research/three_way_projection.py and
docs/F5_THREE_WAY_PRICING.md -- the tie-mass-retention fix that milestone
made is a prerequisite for this module, not a duplicate of it).

The economically distinct alternative is: buy NO on the OPPOSING team's
F5 winner contract. "Team B F5 winner NO" wins if Team A leads OR the
game is tied after five -- it is a materially different, and sometimes
better, expression of the exact same "Team A should not be trailing"
thesis, because it captures the tie-probability mass the three-way YES
contract forfeits.

This module distinguishes the three probabilities the task spec calls
out explicitly:
    A. probability Team A leads after five (the three-way YES prob)
    B. probability Team A is NOT trailing after five (A + tie)
    C. probability of a tie
and compares the two contract expressions on fee-adjusted net EV, using
the SAME fee engine (lib.edgelab.kalshi_fees) every other net-EV
computation in this repo uses -- never a second, independently
maintained fee/EV formula.

WHAT THIS MODULE DOES NOT DO
-------------------------------
- It does not automatically prefer NO contracts. THREE_WAY_YES wins the
  comparison whenever its own fee-adjusted net EV is higher -- price
  alone can make it the better expression even though it forfeits tie
  protection (see test coverage for both directions).
- It does not place, size, or recommend an actual wager. It returns a
  structured comparison; scripts/build_market_ledger.py attaches it to
  the existing F5_ML_Away/F5_ML_Home rows as an ADDITIONAL, informational
  field (`tieTaxComparison`) -- the accepted/rejected market and its own
  confidence tier are computed exactly as before this module existed.
- No NO-side price feed exists anywhere in this repo's data model today
  (see american_to_ask_cents()'s own docstring: F5 `prices` sub-blocks
  are empty in practice, so even the YES ask is itself a mid-derived
  proxy from the American mid-price odds, not a real order-book price).
  The PROTECTED_NO price used here is therefore the same kind of
  mid-derived proxy (100 - the opposing side's own YES ask), consistent
  with -- not a new approximation beyond -- what american_to_ask_cents()
  already does for every F5 YES price in production.

SCOPE / SAFETY
---------------
Pure function: no I/O, no network, no mutation of any argument,
deterministic given deterministic inputs.
"""

from lib.edgelab.kalshi_fees import (
    FEE_TYPE_TAKER,
    fee_adjusted_break_even_probability,
    net_expected_value_per_dollar,
)

THREE_WAY_YES = "THREE_WAY_YES"
PROTECTED_NO = "PROTECTED_NO"

# Reused verbatim from lib.edgelab.market_comparison's STATUS_* vocabulary
# where the concept is identical (BEST_EXPRESSION) -- see that module's
# docstring; this module adds only the two reasons specific to a
# tie-crossing two-way comparison that market_comparison.py's general
# same-side/cross-horizon clustering does not itself compute.
BEST_EXPRESSION = "BEST_EXPRESSION"
INFERIOR_NET_EV = "INFERIOR_NET_EV"
TIE_PROTECTION_ADVANTAGE = "TIE_PROTECTION_ADVANTAGE"
NO_QUALIFYING_EXPRESSION = "NO_QUALIFYING_EXPRESSION"


def _cents_to_dollars(cents):
    if cents is None:
        return None
    return cents / 100.0


def evaluate_f5_tie_tax(
    favored_side,
    p_favored_lead,
    p_tie,
    three_way_yes_price_cents,
    protected_no_price_cents,
    *,
    fee_type=FEE_TYPE_TAKER,
):
    """
    Compare the two economic expressions of a starter-driven F5 thesis
    favoring `favored_side` ('away' or 'home'):

      THREE_WAY_YES: buy `favored_side`'s F5 three-way winner YES
                      contract. True win probability = p_favored_lead
                      (loses on a tie, exactly like every other
                      three-way leg).
      PROTECTED_NO:   buy NO on the OPPOSING side's F5 three-way winner
                      contract. True win probability =
                      p_favored_lead + p_tie (wins on a `favored_side`
                      lead OR a tie -- loses only if the opposing side
                      outright wins).

    Both `p_favored_lead` and `p_tie` are 0-1 probabilities (the same
    awayWinProb/tieProb/homeWinProb three_way_result_probs() already
    computes -- this function does not recompute them, only consumes
    them, so it can never independently drift from the shared,
    validated three-way probability calculation). Prices are in cents
    (0-100), matching every other executable price in
    scripts/build_market_ledger.py.

    Returns a dict with each expression's own probability/price/
    fee-adjusted-breakeven/net-EV, the tie probability itself (surfaced
    explicitly so a caller can see how much of the comparison's outcome
    the tie contributed), which expression is preferred, and a
    human-readable explanation. Returns None if any required input is
    missing (never fabricates a comparison from incomplete data).
    """
    if favored_side not in ("away", "home"):
        return None
    if p_favored_lead is None or p_tie is None:
        return None
    if three_way_yes_price_cents is None or protected_no_price_cents is None:
        return None

    p_favored_not_trailing = p_favored_lead + p_tie

    yes_price = _cents_to_dollars(three_way_yes_price_cents)
    no_price = _cents_to_dollars(protected_no_price_cents)

    yes_ev = net_expected_value_per_dollar(p_favored_lead, yes_price, fee_type=fee_type)
    no_ev = net_expected_value_per_dollar(p_favored_not_trailing, no_price, fee_type=fee_type)

    yes_break_even = fee_adjusted_break_even_probability(yes_price, fee_type=fee_type)
    no_break_even = fee_adjusted_break_even_probability(no_price, fee_type=fee_type)

    three_way_yes = {
        "priceCents": round(three_way_yes_price_cents, 2),
        "trueProbability": round(p_favored_lead, 6),
        "feeAdjustedBreakEvenProbability": yes_break_even,
        "netExpectedValuePerDollar": yes_ev,
        "payoffCondition": f"{favored_side} leads after five (loses on a tie)",
    }
    protected_no = {
        "priceCents": round(protected_no_price_cents, 2),
        "trueProbability": round(p_favored_not_trailing, 6),
        "feeAdjustedBreakEvenProbability": no_break_even,
        "netExpectedValuePerDollar": no_ev,
        "payoffCondition": f"{favored_side} leads after five OR tie (loses only if opponent leads)",
    }

    yes_qualifies = yes_ev is not None and yes_ev > 0
    no_qualifies = no_ev is not None and no_ev > 0

    if not yes_qualifies and not no_qualifies:
        preferred = None
        reason = NO_QUALIFYING_EXPRESSION
        explanation = (
            "Neither expression clears fee-adjusted breakeven at current prices -- "
            "no preferred expression."
        )
    elif no_ev is not None and (yes_ev is None or no_ev > yes_ev):
        preferred = PROTECTED_NO
        reason = TIE_PROTECTION_ADVANTAGE
        three_way_yes["reasonCode"] = INFERIOR_NET_EV
        protected_no["reasonCode"] = BEST_EXPRESSION
        explanation = (
            f"PROTECTED_NO (opposing side F5 NO) preferred: net EV/$ "
            f"{no_ev:+.4f} vs THREE_WAY_YES's {yes_ev if yes_ev is not None else 'N/A'} -- "
            f"tie probability {p_tie*100:.1f}% is captured by the NO contract's payoff "
            f"condition but forfeited by the three-way YES contract."
        )
    else:
        preferred = THREE_WAY_YES
        reason = BEST_EXPRESSION
        three_way_yes["reasonCode"] = BEST_EXPRESSION
        protected_no["reasonCode"] = INFERIOR_NET_EV
        explanation = (
            f"THREE_WAY_YES preferred: net EV/$ {yes_ev:+.4f} vs PROTECTED_NO's "
            f"{no_ev if no_ev is not None else 'N/A'} -- despite forfeiting tie "
            f"protection, {favored_side}'s three-way price is favorable enough to "
            f"still be the better expression."
        )

    return {
        "favoredSide": favored_side,
        "pFavoredLeads": round(p_favored_lead, 6),
        "pTie": round(p_tie, 6),
        "pFavoredNotTrailing": round(p_favored_not_trailing, 6),
        "threeWayYes": three_way_yes,
        "protectedNo": protected_no,
        "preferredExpression": preferred,
        "reasonCode": reason,
        "explanation": explanation,
    }
