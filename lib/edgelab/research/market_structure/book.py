"""
Order-book ladder walk for archived Kalshi books (prospective corpus).

Kalshi book shape: {"yes_dollars": [[price, size], ...], "no_dollars": [[price, size], ...]}
where each side lists RESTING BIDS for that outcome.  Buying YES consumes
NO bids at price q, paying 100 - q per contract; buying NO consumes YES bids.
Sizes are contracts (fp strings).  Prices in dollars strings.
"""


def _levels(side):
    out = []
    for lvl in side or []:
        try:
            p = int(round(float(lvl[0]) * 100))
            s = float(lvl[1])
        except (TypeError, ValueError, IndexError):
            continue
        if 0 < p < 100 and s > 0:
            out.append((p, s))
    return out


def top_of_book(book):
    """-> (yes_bid, yes_ask, yes_bid_size, yes_ask_size) in cents/contracts; None entries when a side is empty."""
    yes = sorted(_levels((book or {}).get("yes_dollars")), key=lambda x: -x[0])
    no = sorted(_levels((book or {}).get("no_dollars")), key=lambda x: -x[0])
    yb, ybs = (yes[0][0], yes[0][1]) if yes else (None, None)
    ya, yas = (100 - no[0][0], no[0][1]) if no else (None, None)
    return yb, ya, ybs, yas


def walk_buy(book, side, budget_cents, fee_fn=None):
    """
    Fill a market order buying `side` ('YES'|'NO') with `budget_cents` of
    principal (fees excluded from the budget so slippage is isolated).
    Returns dict: contracts, avgPriceCents, topPriceCents, slippageCents
    (avg - top), filled (bool: budget exhausted before ladder ran out),
    levelsUsed, depthAtTop (contracts available at the top level).
    """
    resting = (book or {}).get("no_dollars" if side == "YES" else "yes_dollars")
    lv = sorted(_levels(resting), key=lambda x: -x[0])  # best (highest opposing bid) first
    if not lv:
        return {"contracts": 0.0, "avgPriceCents": None, "topPriceCents": None, "slippageCents": None,
                "filled": False, "levelsUsed": 0, "depthAtTop": 0.0}
    top = 100 - lv[0][0]
    remaining = float(budget_cents)
    got = 0.0
    cost = 0.0
    used = 0
    for q, size in lv:
        price = 100 - q
        take = min(size, remaining / price)
        if take <= 0:
            break
        got += take
        cost += take * price
        remaining -= take * price
        used += 1
        if remaining < price * 1e-9:
            break
    avg = (cost / got) if got > 0 else None
    return {"contracts": round(got, 4), "avgPriceCents": None if avg is None else round(avg, 4),
            "topPriceCents": top, "slippageCents": None if avg is None else round(avg - top, 4),
            "filled": remaining <= 1e-6, "levelsUsed": used, "depthAtTop": lv[0][1]}
