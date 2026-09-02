#!/usr/bin/env python3
"""MLB-ALPHA-0002-MAKER-FEASIBILITY-V1 -- counterfactual passive-execution
simulator.

THE QUESTION (distinct from candidate C01-F5REV, which is unchanged):
    can a PASSIVE limit order capture enough of the predicted move to
    create positive post-fee expectancy, when crossing the spread cannot?

WHAT WE ACTUALLY KNOW, AND WHAT WE DO NOT
-----------------------------------------
Our hypothetical order was never in the book, so no fill here is
observed. Every fill this module reports is INFERRED. Two things gate an
inference:

  * QUEUE AHEAD -- the resting quantity already at our price when we
    would have joined. Kalshi's historical candlesticks carry prices but
    NO sizes, and there is no order-book history endpoint, so for the
    historical panel the queue is genuinely UNKNOWABLE. It is therefore
    a declared PARAMETER swept over a grid, never a fitted value, and
    every historical result is labelled COUNTERFACTUAL_QUEUE_UNKNOWN.
    Prospective capture records the real book, which is the only way to
    retire that parameter.
  * AGGRESSIVE FLOW -- the public trade tape tells us, per print, which
    side was the taker. A resting YES bid can only fill against a taker
    BUYING NO (i.e. hitting the YES bid) at our price. "The price was
    touched" is never treated as a fill.

FILL CLASSES (preregistered; definitions frozen before any outcome)
-------------------------------------------------------------------
  CONSERVATIVE_FILL   cumulative opposite-side taker quantity printed AT
                      our exact price level after placement must exceed
                      queueAhead + our size. We are last in the queue.
  OPTIMISTIC_BOUND    same flow requirement with queueAhead treated as 0
                      (we are first in the queue). An upper bound, never
                      a forecast.
  PRICE_IMPROVING     a separate, strictly more counterfactual class: our
                      order would have changed the book that produced the
                      tape, so it is reported apart and never pooled.

Fees: makers and takers are NOT assumed equal. See fee_config().
RESEARCH ONLY -- no order is ever placed; this module has no write path
to any ledger, recommendation, staking or risk-gate surface.
"""

import hashlib
import json

# ----------------------------------------------------------------- fees
# The repository's canonical module (lib.edgelab.kalshi_fees) carries
# FEE_MULTIPLIER_MAKER_DEFAULT = 0.0 ("most markets charge makers
# nothing") and FEE_MULTIPLIER_MAKER_DESIGNATED = 0.0175. Public
# secondary sources for the July-2026 schedule instead describe a general
# maker fee of one quarter of the taker fee, which is exactly 0.0175.
# kalshi.com (the primary PDF) is unreachable from this environment, so
# the multiplier is treated as UNVERIFIED and BOTH are carried: the
# conservative 0.0175 is the headline, 0.0 is reported as sensitivity.
MAKER_MULTIPLIER_CONSERVATIVE = 0.0175
MAKER_MULTIPLIER_OPTIMISTIC = 0.0
TAKER_MULTIPLIER = 0.07
FEE_SOURCE = "KALSHI_MAKER_2026_SECONDARY_SOURCE_UNVERIFIED_V1"


def fee_config():
    return {"takerMultiplier": TAKER_MULTIPLIER,
            "makerMultiplierHeadline": MAKER_MULTIPLIER_CONSERVATIVE,
            "makerMultiplierSensitivity": MAKER_MULTIPLIER_OPTIMISTIC,
            "source": FEE_SOURCE,
            "verification": ("primary schedule at kalshi.com is egress-blocked from the "
                             "research sandbox; multiple secondary sources state maker = 25% "
                             "of taker, which equals the repo's MAKER_DESIGNATED constant"),
            "formula": "ceil_to_cent(multiplier * contracts * price * (1 - price))"}


def _ceil_cent(x):
    import math
    return math.ceil(round(x * 100.0, 6)) / 100.0


def fee_for(contracts, price_dollars, multiplier):
    if contracts <= 0:
        return 0.0
    return _ceil_cent(multiplier * contracts * price_dollars * (1.0 - price_dollars))


# ------------------------------------------------------------- protocols
def protocol_hash(p):
    return hashlib.sha256(json.dumps(p, sort_keys=True).encode()).hexdigest()


def maker_protocols():
    """The small frozen set of execution protocols. Parameters come from
    exchange mechanics and the measured spread regime, NEVER from
    retrospective settlement P/L."""
    a = {
        "protocolId": "MAKER-A-JOIN-BEST",
        "class": "JOIN_EXISTING_BEST",
        "action": ("rest a limit buy on the signalled side at the CURRENT best price on that "
                   "side (join the queue; never improve, so the historical book is unchanged)"),
        "priceRule": "buy YES at the prevailing yesBid; buy NO at the prevailing noBid (= 100 - yesAsk)",
        "maxWaitMinutes": 30,
        "cancelRules": ["signal invalidation (the triggering move fully retraces)",
                        "maxWaitMinutes elapsed", "T-5 minutes to scheduled start",
                        "scheduled start"],
        "modelledContracts": 25,
        "queueDiscipline": "strict FIFO, we are LAST at our price at placement time",
        "rationale": ("joining the best price is the only passive action that leaves the observed "
                      "book and tape valid, so its fill inference is the least counterfactual; "
                      "30 minutes is the horizon over which the reversal signal was measured"),
    }
    b = {
        "protocolId": "MAKER-B-IMPROVE-ONE-CENT",
        "class": "PRICE_IMPROVING_STRICTLY_COUNTERFACTUAL",
        "action": "rest one cent better than the best price on the signalled side",
        "priceRule": "buy YES at yesBid + 1 (only when the spread is >= 3 cents)",
        "minSpreadCents": 3,
        "maxWaitMinutes": 30,
        "cancelRules": a["cancelRules"],
        "modelledContracts": 25,
        "queueDiscipline": "we would be FIRST at a price that did not previously exist",
        "rationale": ("adding a better price changes the book that generated the tape, so this "
                      "class is reported separately and never pooled with MAKER-A"),
    }
    for p in (a, b):
        p["protocolSha256"] = protocol_hash({k: v for k, v in p.items() if k != "protocolSha256"})
    return [a, b]


QUEUE_AHEAD_GRID = (0, 10, 25, 50, 100, 250)


# ------------------------------------------------------------ fill logic
def opposite_taker_side(buy_side):
    """Which taker_side prints can fill OUR resting buy.

    Buying YES passively = resting a YES bid; it fills when an aggressor
    BUYS NO (taker_side == "no"), i.e. hits the YES bid. Symmetrically a
    resting NO bid fills against a taker BUYING YES.
    """
    return "no" if buy_side == "YES" else "yes"


def cents(v):
    try:
        return round(float(v) * 100.0)
    except (TypeError, ValueError):
        return None


def simulate_passive_fill(trades, placed_minute, deadline_minute, buy_side,
                          limit_price_cents, queue_ahead, size):
    """Pure. `trades` is an iterable of dicts with created_minute, taker_side,
    yes_price_cents, quantity -- already restricted to one contract.

    Returns a dict describing whether the order would have filled, when,
    and how much aggressive flow it took. Never claims certainty.
    """
    need_side = opposite_taker_side(buy_side)
    cum = 0.0
    filled_at = None
    flow_at_level = 0.0
    for tr in trades:
        m = tr["created_minute"]
        if m < placed_minute or m > deadline_minute:
            continue
        if tr["taker_side"] != need_side:
            continue
        # A print fills our level only if it trades AT our price (for a
        # resting YES bid, a YES price equal to our limit). Prints that
        # trade through us are also fills; prints away from us are not.
        yp = tr["yes_price_cents"]
        if yp is None:
            continue
        if buy_side == "YES":
            if yp > limit_price_cents:
                continue
        else:
            no_price = 100 - yp
            if no_price > limit_price_cents:
                continue
        flow_at_level += tr["quantity"]
        cum += tr["quantity"]
        if filled_at is None and cum >= queue_ahead + size:
            filled_at = m
    return {"filled": filled_at is not None, "filledAtMinute": filled_at,
            "aggressiveFlowAtOrThroughLevel": flow_at_level,
            "queueAheadAssumed": queue_ahead, "sizeModelled": size,
            "flowRequired": queue_ahead + size}


def fill_economics(buy_side, limit_price_cents, contracts, settled_result,
                   maker_multiplier):
    """Post-fee settlement economics for a hypothetical PASSIVE fill."""
    price = limit_price_cents / 100.0
    fee = fee_for(contracts, price, maker_multiplier)
    cash = contracts * price + fee
    won = (settled_result == "YES") == (buy_side == "YES")
    payout = contracts * 1.0 if won else 0.0
    return {"contracts": contracts, "limitPriceCents": limit_price_cents,
            "cashDeployed": round(cash, 4), "fees": round(fee, 4),
            "classification": "MAKER", "won": won,
            "netProfitLoss": round(payout - cash, 4),
            "returnOnCash": round((payout - cash) / cash, 6) if cash > 0 else None}
