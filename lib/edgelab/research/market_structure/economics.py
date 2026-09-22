"""
Fee-aware executable economics for the MRV program.  All prices in CENTS
(integers 1..99) at the interface; conversion to the dollar price the
canonical fee module expects happens inside.  Delegates every fee number to
lib.edgelab.kalshi_fees (the single fee authority) and never re-implements
the formula.
"""
from lib.edgelab import kalshi_fees as kf

MAKER_MULT_CONSERVATIVE = kf.FEE_MULTIPLIER_MAKER_DESIGNATED   # 0.0175, headline
MAKER_MULT_OPTIMISTIC = 0.0                                    # sensitivity only
STANDARD_ORDER_USD = kf.DEFAULT_RESEARCH_ORDER_SIZE            # 10


def executable_yes_cents(yes_ask):
    """Buy YES pays the ask.  None if absent or off the (0,100) open interval."""
    return _valid(yes_ask)


def executable_no_cents(yes_bid=None, no_ask=None):
    """Buy NO pays noAsk when archived, else 100 - yesBid.  Never derived from yesAsk."""
    if no_ask is not None:
        return _valid(no_ask)
    if yes_bid is None:
        return None
    return _valid(100 - yes_bid)


def _valid(c):
    if c is None:
        return None
    try:
        c = int(round(float(c)))
    except (TypeError, ValueError):
        return None
    return c if 0 < c < 100 else None


def taker_fee_cents(price_cents, contracts=1):
    """Kalshi taker fee for `contracts` at `price_cents`, in cents (rounded up per order)."""
    p = _valid(price_cents)
    if p is None:
        return None
    return round(kf.taker_fee(contracts, p / 100.0) * 100.0, 2)


def maker_fee_cents(price_cents, contracts=1, multiplier=MAKER_MULT_CONSERVATIVE):
    p = _valid(price_cents)
    if p is None:
        return None
    return round(kf.maker_fee(contracts, p / 100.0, multiplier=multiplier) * 100.0, 2)


def pair_cost_after_fees(buy_a_cents, buy_b_cents, contracts=1, fee="taker"):
    """
    Total cents paid for one contract of A and one of B including fees.  For a
    complementary pair (YES on X + NO on Y where X implies Y, or the three legs
    of a three-way), a locked profit exists iff this is < 100 * contracts.
    Returns None when either leg is not executable.
    """
    a, b = _valid(buy_a_cents), _valid(buy_b_cents)
    if a is None or b is None:
        return None
    fee_fn = taker_fee_cents if fee == "taker" else maker_fee_cents
    return a * contracts + b * contracts + fee_fn(a, contracts) + fee_fn(b, contracts)


def locked_profit_cents(legs_cents, payout_cents=100, contracts=1, fee="taker"):
    """
    Generic: cents locked by buying every leg in `legs_cents` when exactly one
    leg pays `payout_cents` per contract at settlement.  Negative means no
    arbitrage.  None if any leg is not executable.
    """
    fee_fn = taker_fee_cents if fee == "taker" else maker_fee_cents
    cost = 0.0
    for c in legs_cents:
        v = _valid(c)
        if v is None:
            return None
        cost += v * contracts + fee_fn(v, contracts)
    return round(payout_cents * contracts - cost, 2)


def settlement_order(price_cents, won, order_usd=STANDARD_ORDER_USD, fee_type=kf.FEE_TYPE_TAKER):
    """
    USD `order_usd` whole-contract taker order at `price_cents`, held to
    settlement.  Returns kalshi_fees.simulate_settlement_order()'s dict
    (netProfitLoss, actualCashConsumed, roiOnActualCashConsumed, contracts,
    entryFee ...) or None when not executable / cannot buy one contract.
    """
    p = _valid(price_cents)
    if p is None:
        return None
    return kf.simulate_settlement_order(order_usd, p / 100.0, won, fee_type=fee_type)


def break_even_probability(price_cents, fee_type=kf.FEE_TYPE_TAKER):
    p = _valid(price_cents)
    if p is None:
        return None
    return kf.fee_adjusted_break_even_probability(p / 100.0, fee_type=fee_type)


def fee_drag_cents(price_cents, multiplier=kf.FEE_MULTIPLIER_TAKER_STANDARD):
    """Per-contract fee in cents at this price before per-order rounding: 100*m*P*(1-P)."""
    p = _valid(price_cents)
    if p is None:
        return None
    q = p / 100.0
    return round(100.0 * multiplier * q * (1.0 - q), 4)
