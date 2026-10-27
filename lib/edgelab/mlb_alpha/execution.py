"""
lib/edgelab/mlb_alpha/execution.py
==================================
What a trade would actually cost, at a stated size, against the archived book.

Rules (carried from the MRV / ALPHA programs, enforced here):
  * Midpoint is never a fill. YES is bought at the ask (100 - best NO bid),
    NO at 100 - best YES bid; each side is priced from its OWN resting orders.
  * Fees come from lib.edgelab.kalshi_fees (the single fee authority). A multi-level
    fill is charged as one aggregated order at its average price and is labelled
    ESTIMATED_AGGREGATED_ORDER: the exchange's per-fill rounding is unobservable.
  * Size is explicit. A request larger than the book returns a PARTIAL fill; it is
    never silently scaled to fit.
  * Maker quotes carry a fill probability and an adverse-selection cost as explicit
    parameters. Historical archives contain neither, so every maker number is
    COUNTERFACTUAL_QUEUE_UNKNOWN and is never mixed with taker evidence.

Prices at this interface are DOLLARS in (0, 1); book levels are Kalshi's
`yes_dollars` / `no_dollars` lists of [price, size] resting BIDS.
"""

import math

from lib.edgelab import kalshi_fees as kf

FEE_BASIS = "ESTIMATED_AGGREGATED_ORDER"
MAKER_BASIS = "COUNTERFACTUAL_QUEUE_UNKNOWN"


def _levels(side):
    out = []
    for lvl in side or []:
        try:
            p, s = float(lvl[0]), float(lvl[1])
        except (TypeError, ValueError, IndexError):
            continue
        if 0.0 < p < 1.0 and s > 0:
            out.append((round(p, 4), s))
    return out


def best_quotes(book):
    """{'yesBid','yesAsk','yesBidSize','yesAskSize','noBid','noAsk'} in dollars (None when a side is empty)."""
    yes = sorted(_levels((book or {}).get("yes_dollars")), key=lambda x: -x[0])
    no = sorted(_levels((book or {}).get("no_dollars")), key=lambda x: -x[0])
    yb, ybs = (yes[0] if yes else (None, None))
    nb, nbs = (no[0] if no else (None, None))
    return {"yesBid": yb, "yesBidSize": ybs, "yesAsk": None if nb is None else round(1.0 - nb, 4), "yesAskSize": nbs,
            "noBid": nb, "noAsk": None if yb is None else round(1.0 - yb, 4)}


def two_sided(book):
    q = best_quotes(book)
    return q["yesBid"] is not None and q["yesAsk"] is not None and q["yesAsk"] >= q["yesBid"]


def fill_buy(book, side, contracts):
    """
    Walk the book for exactly `contracts` whole contracts bought on `side` ('YES'|'NO').
    Returns {requested, filled, avgPrice, topPrice, slippage, levels, partial, cost, fee, cash, feeBasis}.
    YES consumes resting NO bids (price paid = 1 - bid); NO consumes resting YES bids.
    """
    want = int(contracts)
    resting = (book or {}).get("no_dollars" if side == "YES" else "yes_dollars")
    lv = sorted(_levels(resting), key=lambda x: -x[0])
    got, cost, used, top = 0, 0.0, 0, None
    for bid, size in lv:
        price = round(1.0 - bid, 4)
        if top is None:
            top = price
        take = min(int(size), want - got)
        if take <= 0:
            break
        got += take
        cost += take * price
        used += 1
        if got >= want:
            break
    out = {"requested": want, "filled": got, "avgPrice": None, "topPrice": top, "slippage": None, "levels": used,
           "partial": got < want, "cost": round(cost, 4), "fee": 0.0, "cash": round(cost, 4), "feeBasis": FEE_BASIS}
    if got > 0:
        avg = cost / got
        fee = kf.taker_fee(got, round(avg, 4))
        out.update(avgPrice=round(avg, 4), slippage=round(avg - top, 4), fee=fee, cash=round(cost + fee, 4))
    return out


def contracts_for_budget(book, side, budget_usd, max_contracts=100000):
    """Largest whole-contract order whose CASH (principal + fee) fits `budget_usd`, honouring depth."""
    lo, hi, best = 0, max_contracts, 0
    while lo <= hi:
        mid = (lo + hi) // 2
        f = fill_buy(book, side, mid)
        if f["filled"] == mid and f["cash"] <= budget_usd + 1e-9:
            best, lo = mid, mid + 1
        else:
            hi = mid - 1
    return best


def max_size_within_slippage(book, side, max_slippage=0.01):
    """Largest order whose average price is within `max_slippage` dollars of the top of book."""
    resting = (book or {}).get("no_dollars" if side == "YES" else "yes_dollars")
    lv = sorted(_levels(resting), key=lambda x: -x[0])
    if not lv:
        return 0
    top = round(1.0 - lv[0][0], 4)
    got, cost = 0, 0.0
    for bid, size in lv:
        price = round(1.0 - bid, 4)
        for _ in range(1):
            take = int(size)
            if take <= 0:
                continue
            new_avg = (cost + take * price) / (got + take)
            if new_avg - top <= max_slippage + 1e-12:
                got += take
                cost += take * price
            else:
                # partial level: largest k with (cost + k*price)/(got+k) - top <= max_slippage
                lo, hi, k_best = 0, take, 0
                while lo <= hi:
                    k = (lo + hi) // 2
                    if (cost + k * price) / (got + k) - top <= max_slippage + 1e-12:
                        k_best, lo = k, k + 1
                    else:
                        hi = k - 1
                return got + k_best
    return got


def net_edge_per_contract(p_win, price, fee_per_contract=None):
    """Expected profit per contract bought at `price` that pays 1 with probability p_win."""
    fee = fee_per_contract if fee_per_contract is not None else kf.FEE_MULTIPLIER_TAKER_STANDARD * price * (1.0 - price)
    return p_win - price - fee


def break_even_probability(price):
    """Win probability at which a taker purchase at `price` has zero expected profit."""
    return price + kf.FEE_MULTIPLIER_TAKER_STANDARD * price * (1.0 - price)


def settle(side, outcome_yes, contracts, cash):
    """Net P/L of holding `contracts` bought on `side` to settlement (cash already includes fee)."""
    won = bool(outcome_yes) == (side == "YES")
    return round((contracts if won else 0.0) - cash, 4)


def maker_bid_ev(p_win, price, fill_probability, adverse_selection, maker_multiplier=None):
    """
    Expected profit per RESTING contract at `price` when it fills with `fill_probability` and the true win
    probability conditional on a fill is `p_win - adverse_selection` (a resting order fills when informed
    flow trades into it). Returns per-ORDER EV (unconditional on filling). All inputs are assumptions;
    the result is labelled COUNTERFACTUAL_QUEUE_UNKNOWN by callers.
    """
    mult = kf.FEE_MULTIPLIER_MAKER_DESIGNATED if maker_multiplier is None else maker_multiplier
    fee = mult * price * (1.0 - price)
    return fill_probability * ((p_win - adverse_selection) - price - fee)


def kelly_fraction(p_win, price, shrink=0.25, cap=0.05):
    """
    Fractional Kelly stake (share of bankroll) for a binary contract at `price`, using the fee-adjusted
    cost. Returns 0 when the edge is non-positive. `shrink` scales full Kelly down for model error;
    `cap` is a hard ceiling. Pure arithmetic: sizing authority lives in the caller's risk limits.
    """
    cost = price + kf.FEE_MULTIPLIER_TAKER_STANDARD * price * (1.0 - price)
    if not (0.0 < cost < 1.0) or p_win <= cost:
        return 0.0
    full = (p_win - cost) / (1.0 - cost)
    return max(0.0, min(cap, shrink * full))


def wilson_lower(wins, n, z=1.645):
    if n <= 0:
        return 0.0
    ph = wins / n
    d = 1 + z * z / n
    c = (ph + z * z / (2 * n)) / d
    h = z * math.sqrt(ph * (1 - ph) / n + z * z / (4 * n * n)) / d
    return c - h
