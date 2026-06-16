#!/usr/bin/env python3
"""
scripts/executable_price.py
============================
Phase 1A: Executable price logic for Kalshi markets.

Core Rule:
  For YES bet: executablePriceUsed = yes_ask (cents)
  For NO bet:  executablePriceUsed = no_ask  = 100 - yes_bid (cents)
  If only bids available: yesAsk = 100 - noBid; noAsk = 100 - yesBid

All prices stored in cents (0-100 scale), NOT decimal.
"""

def get_executable_prices(yes_bid, yes_ask, no_bid=None, no_ask=None):
    """
    Compute executable prices for YES and NO sides of a Kalshi market.

    Kalshi markets trade YES contracts. NO contracts are just (100 - YES).

    Args:
        yes_bid: Highest price buyers will pay for YES (cents 0-100 or decimal 0-1)
        yes_ask: Lowest price sellers accept for YES (cents 0-100 or decimal 0-1)
        no_bid:  Highest price buyers will pay for NO (optional)
        no_ask:  Lowest price sellers accept for NO (optional)

    Returns:
        dict with:
          yes_bid, yes_ask, no_bid, no_ask (all in 0-100 cent scale)
          yes_executable: price to buy YES = yes_ask
          no_executable:  price to buy NO  = no_ask  (= 100 - yes_bid if no_ask unknown)
          mid:            (yes_bid + yes_ask) / 2
    """
    def norm_to_cents(v):
        if v is None:
            return None
        f = float(v)
        return f if f > 1.0 else round(f * 100, 4)

    yes_bid_c = norm_to_cents(yes_bid)
    yes_ask_c = norm_to_cents(yes_ask)

    # Derive no-side prices from yes-side via complement
    # NO bid = 100 - YES ask; NO ask = 100 - YES bid
    no_bid_c = norm_to_cents(no_bid) if no_bid is not None else (
        round(100 - yes_ask_c, 4) if yes_ask_c is not None else None
    )
    no_ask_c = norm_to_cents(no_ask) if no_ask is not None else (
        round(100 - yes_bid_c, 4) if yes_bid_c is not None else None
    )

    # Executable prices: the ASK side = what you pay to enter
    yes_executable = yes_ask_c  # cost to buy YES
    no_executable  = no_ask_c   # cost to buy NO (= 100 - yes_bid)

    # Mid = average of bid and ask
    if yes_bid_c is not None and yes_ask_c is not None:
        mid_c = round((yes_bid_c + yes_ask_c) / 2, 4)
    elif yes_bid_c is not None:
        mid_c = yes_bid_c
    elif yes_ask_c is not None:
        mid_c = yes_ask_c
    else:
        mid_c = None

    return {
        'yes_bid':        yes_bid_c,
        'yes_ask':        yes_ask_c,
        'no_bid':         no_bid_c,
        'no_ask':         no_ask_c,
        'yes_executable': yes_executable,
        'no_executable':  no_executable,
        'mid':            mid_c,
    }


def executable_prob_from_price(price_cents):
    """
    Convert executable price in cents to an implied probability for edge calc.
    price_cents: 0-100 scale (e.g. 55 means 55% / $0.55 per contract)
    Returns: float 0-1 (e.g. 0.55)
    """
    if price_cents is None:
        return None
    return round(price_cents / 100.0, 6)


def executable_price_cents_to_american(price_cents):
    """
    Convert Kalshi executable price (cents) to American odds equivalent.
    price_cents: 0-100, e.g. 55 = 55% implied
    """
    if price_cents is None:
        return None
    prob = price_cents / 100.0
    if prob <= 0 or prob >= 1:
        return None
    if prob >= 0.5:
        return round(-(prob / (1 - prob)) * 100)
    return round(((1 - prob) / prob) * 100)


def check_max_bet_price(exec_p, max_p):
    """
    Check whether the current executable price is within the max bet price.

    For a YES bet, we want to pay LESS (lower price = better odds for us).
    So: bet is valid if exec_p <= max_p.

    Args:
        exec_p: current executable price in cents (0-100)
        max_p:  maximum acceptable price in cents (0-100)

    Returns:
        (ok: bool, reason_code: str or None)
    """
    if exec_p is None or max_p is None:
        return True, None  # cannot check, allow through
    if exec_p <= max_p:
        return True, None
    return False, 'PRICE_MOVED_BEYOND_MAX'
