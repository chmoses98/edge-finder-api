"""
Order-book normalisation for the MRV collector.

Kalshi's public orderbook endpoint returns {"orderbook_fp": {"yes_dollars":
[[price, qty], ...], "no_dollars": [[price, qty], ...]}} (fixed-point
migration observed 2026-09-02; the legacy key "orderbook" with integer
cents is kept as a fallback).  Each side lists RESTING BIDS for that
outcome, so the best YES ask is 100 minus the best NO bid.  The full ladder
is stored verbatim in cents with the source key recorded; nothing is
truncated by this module (if Kalshi ever truncates, the row says so via
`levels` only, never by dropping data silently).
"""


def _levels(side, unit):
    out = []
    for lvl in side or []:
        try:
            p = float(lvl[0])
            q = float(lvl[1])
        except (TypeError, ValueError, IndexError):
            continue
        cents = int(round(p * 100)) if unit == "dollars" else int(round(p))
        if 0 < cents < 100 and q > 0:
            out.append([cents, q])
    return sorted(out, key=lambda x: -x[0])


def normalize_book(payload):
    """
    payload: the JSON body of GET /markets/{ticker}/orderbook.
    -> dict {sourceKey, unit, yesBids:[[cents, qty]...] best first, noBids:[...],
             bestYesBid, bestYesAsk, bestNoBid, bestNoAsk, yesBidQtyTop, yesAskQtyTop,
             levelsYes, levelsNo, twoSided} or None when the payload carries no book.
    """
    if not isinstance(payload, dict):
        return None
    if payload.get("orderbook_fp") is not None:
        key, unit, book = "orderbook_fp", "dollars", payload["orderbook_fp"]
    elif payload.get("orderbook") is not None:
        key, unit, book = "orderbook", "cents", payload["orderbook"]
    else:
        return None
    if not isinstance(book, dict):
        return None
    yes = _levels(book.get("yes_dollars") if unit == "dollars" else book.get("yes"), unit)
    no = _levels(book.get("no_dollars") if unit == "dollars" else book.get("no"), unit)
    best_yes_bid = yes[0][0] if yes else None
    best_no_bid = no[0][0] if no else None
    return {"sourceKey": key, "unit": unit, "yesBids": yes, "noBids": no,
            "bestYesBid": best_yes_bid, "bestYesAsk": (100 - best_no_bid) if best_no_bid is not None else None,
            "bestNoBid": best_no_bid, "bestNoAsk": (100 - best_yes_bid) if best_yes_bid is not None else None,
            "yesBidQtyTop": yes[0][1] if yes else None, "yesAskQtyTop": no[0][1] if no else None,
            "levelsYes": len(yes), "levelsNo": len(no), "twoSided": bool(yes and no)}


def book_fingerprint_payload(nb):
    """The content that defines 'unchanged': the two ladders only (not timestamps)."""
    return {"y": nb["yesBids"], "n": nb["noBids"]} if nb else None


def quote_from_market(m):
    """Normalised quote row from a /markets item (dollars-era fields preferred, cents fallback; unit recorded)."""
    def pick(dkey, ckey):
        if m.get(dkey) is not None:
            try:
                return int(round(float(m[dkey]) * 100)), "dollars"
            except (TypeError, ValueError):
                return None, None
        if m.get(ckey) is not None:
            try:
                return int(round(float(m[ckey]))), "cents"
            except (TypeError, ValueError):
                return None, None
        return None, None
    yb, unit = pick("yes_bid_dollars", "yes_bid")
    ya, _ = pick("yes_ask_dollars", "yes_ask")
    nb, _ = pick("no_bid_dollars", "no_bid")
    na, _ = pick("no_ask_dollars", "no_ask")
    lp, _ = pick("last_price_dollars", "last_price")
    def num(*keys):
        for k in keys:
            v = m.get(k)
            if v is not None:
                try:
                    return float(v)
                except (TypeError, ValueError):
                    pass
        return None
    return {"marketTicker": m.get("ticker"), "eventTicker": m.get("event_ticker"), "status": m.get("status"),
            "yesBid": yb, "yesAsk": ya, "noBid": nb, "noAsk": na, "lastPrice": lp, "priceUnitSource": unit,
            "volume": num("volume_fp", "volume"), "volume24h": num("volume_24h_fp", "volume_24h"),
            "openInterest": num("open_interest_fp", "open_interest"), "liquidity": num("liquidity_dollars", "liquidity"),
            "closeTime": m.get("close_time"), "openTime": m.get("open_time"), "expectedExpirationTime": m.get("expected_expiration_time"),
            "title": m.get("title"), "subtitle": m.get("subtitle") or m.get("yes_sub_title")}


def quote_fingerprint_payload(q):
    return {k: q.get(k) for k in ("yesBid", "yesAsk", "noBid", "noAsk", "lastPrice", "status", "volume", "openInterest")}
