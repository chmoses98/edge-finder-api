#!/usr/bin/env python3
"""
lib/edgelab/canonical_price.py
==============================
WAVE 1, subwave B1. THE canonical executable-price representation.

One object, one vocabulary, one set of execution rules. Every production and
research surface that needs to answer "what price can actually be bought?"
should eventually consume this rather than deriving its own answer.

B1 IS SHADOW ONLY. Nothing here drives actionable, qualification, confidence,
Bet Up To, stake, bankroll or recommendation eligibility. B2 performs that
cutover after the blast radius is measured.

WHY THIS EXISTS -- THE DEFECT, TRACED END TO END
------------------------------------------------
The decision layer never saw an executable ask at all. The loss is upstream of
build_market_ledger.py, in two steps:

  1. lib/kalshi_registry_market_builders.py::price_block (~line 44) reads the
     real book and then collapses it:

         mid = round(((bid or 0) + (ask or 0)) / 2, 4) if (bid or ask) else None
         'american': american(mid)

     `bid or 0` turns an ABSENT bid into a numeric zero, so an ask-only book
     yields ask/2 -- audit CR-5. The American odds are then derived from that
     mid, so every downstream consumer of `american` is consuming a midpoint.

  2. scripts/merge_odds.py (~line 242) builds odds.kalshi.ml from that block
     but keeps ONLY `american`:

         kalshi_books['ml'] = {'away': a_am, 'home': h_am,
                               'away_ticker': ..., 'home_ticker': ...}

     yes_bid and yes_ask exist in the registry and are discarded here.

By the time build_market_ledger.py asks `ml.get('yes_ask_cents')` the field has
never existed, so `yes_ask_cents` is None and build_edge_fields falls back to
`exec_prob = kalshi_vf` -- the vig-free MIDPOINT -- while still emitting a field
named `executablePriceUsed`. Verified live: 15/15 games on the 2026-09-09 slate.

THE TRADEABLE RANGE, AND WHY ZERO IS NOT A PRICE
-------------------------------------------------
Kalshi binary contracts trade in whole cents strictly between 0 and 100: a
resting quote is 1..99. A `yes_bid` of 0 therefore is not "someone will buy at
zero" -- it means NO RESTING BID EXISTS. Evidence from this repository's own
archive (542,716 observations):

  * spreadCents == yesAsk on 28,330 of 28,330 rows where yesBid == 0, i.e. the
    archive computes ask-minus-bid and uses the zero arithmetically. That is
    precisely the mechanism that produces the halved mid.
  * The ask paired with a zero bid spans the entire range (median 22c, 5,724
    rows at >=90c). A contract the market prices at 90c with genuinely nobody
    willing to bid is not a plausible book; an empty bid side is.
  * 15,944 of the 22,077 tickers that ever show a zero bid also show a positive
    bid at another capture, so zero is a transient book state.

This module therefore treats 0 as ABSENT for a bid, and never lets an absent
side become a number.

WHAT IS AND IS NOT DERIVABLE
----------------------------
noBid/noAsk are archived as null on 100% of observations in this repository, so
a genuine NO ask is never available and NO must always be derived. The
derivation is justified by contract mechanics, not by archived data:

    On a binary contract, a resting YES bid at p is an offer to buy YES at p.
    Filling it means selling YES at p, which is the same trade as buying NO at
    (100 - p). So the best executable NO ask == 100 - best YES bid, exactly, in
    cents, before fees.

The complement of the YES ASK is the NO BID, not the NO ask -- deriving a NO ask
from the YES ask would quote the wrong side of the book, so it is prohibited.
"""

# Kalshi binary contracts rest strictly inside 0..100 cents.
MIN_TRADEABLE_CENTS = 1
MAX_TRADEABLE_CENTS = 99
CONTRACT_SETTLEMENT_CENTS = 100

# Sides a caller may purchase.
SIDE_YES = "YES"
SIDE_NO = "NO"

# Price-basis vocabulary. Every executable price carries exactly one.
BASIS_YES_ASK = "YES_ASK"
BASIS_NO_ASK = "NO_ASK"
BASIS_DERIVED_NO_ASK_FROM_YES_BID = "DERIVED_NO_ASK_FROM_YES_BID"

VALID_BASES = (BASIS_YES_ASK, BASIS_NO_ASK, BASIS_DERIVED_NO_ASK_FROM_YES_BID)

# Deliberately defined so it can be asserted against, never produced. A midpoint
# is not a price anyone can trade at; naming it here makes "we never emit this"
# a testable statement rather than a comment.
BASIS_FORBIDDEN_MIDPOINT = "MIDPOINT_AS_EXECUTABLE"

# Book state, describing what the order book actually offered.
BOOK_TWO_SIDED = "TWO_SIDED"
BOOK_ASK_ONLY = "ASK_ONLY"
BOOK_BID_ONLY = "BID_ONLY"
BOOK_EMPTY = "EMPTY"

# Refusal reasons. An executable price is either a real number with a basis, or
# it is absent with one of these.
NO_YES_ASK = "NO_EXECUTABLE_PRICE_YES_ASK_ABSENT"
NO_BID_FOR_NO_SIDE = "NO_EXECUTABLE_PRICE_NO_SIDE_REQUIRES_A_RESTING_YES_BID"
NO_BOOK = "NO_EXECUTABLE_PRICE_NO_BOOK_OBSERVED"
UNKNOWN_SIDE = "NO_EXECUTABLE_PRICE_UNRECOGNISED_SIDE"


UNIT_CENTS = "cents"
UNIT_DOLLARS = "dollars"


def normalize_cents(value, unit=UNIT_CENTS):
    """
    Returns a quote in cents, or None if it is not a tradeable quote.

    The unit is DECLARED by the caller and never guessed. The repository carries
    both conventions -- the Kalshi registry stores dollars (0.12), the
    observation archive stores cents (73.0) -- and an earlier draft of this
    function tried to infer which from the magnitude. That heuristic was wrong
    in both directions and this is a money path, so it is gone:

      * it read a legitimate 1-cent quote as $1.00 and discarded it. There are
        3,810 such quotes in ten partitions alone (1,589 bids, 2,221 asks) --
        longshot contracts, exactly where a mispriced edge is most tempting.
      * symmetrically, a registry value of 1.0 ($1.00, i.e. an untradeable
        100c contract) would have been read as a 1-cent bargain.

    A value outside the 1..99c resting range -- including 0 -- is ABSENT, never
    a number. That is the single rule which stops `(bid or 0)` arithmetic at the
    source.
    """
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if unit == UNIT_DOLLARS:
        cents = f * 100.0
    elif unit == UNIT_CENTS:
        cents = f
    else:
        raise ValueError("unit must be %r or %r, got %r"
                         % (UNIT_CENTS, UNIT_DOLLARS, unit))
    if cents < MIN_TRADEABLE_CENTS or cents > MAX_TRADEABLE_CENTS:
        return None
    return round(cents, 4)


def classify_book(yes_bid_cents, yes_ask_cents):
    if yes_bid_cents is not None and yes_ask_cents is not None:
        return BOOK_TWO_SIDED
    if yes_ask_cents is not None:
        return BOOK_ASK_ONLY
    if yes_bid_cents is not None:
        return BOOK_BID_ONLY
    return BOOK_EMPTY


def build_price(side, *, yes_bid=None, yes_ask=None, no_bid=None, no_ask=None,
                unit=UNIT_CENTS, market_ticker=None, event_ticker=None,
                observation_id=None, captured_at=None, spread_cents=None,
                quote_age_seconds=None, join_method=None, source=None):
    """
    THE canonical price object. Raw book and derived execution semantics are
    kept strictly separate: the `book` sub-object is what was observed, and the
    top-level executable fields are what can actually be purchased.

    Returns a dict rather than a class so it serialises into JSONL artifacts
    unchanged and can be diffed field by field in a receipt.
    """
    yb = normalize_cents(yes_bid, unit)
    ya = normalize_cents(yes_ask, unit)
    nb = normalize_cents(no_bid, unit)
    na = normalize_cents(no_ask, unit)

    book_state = classify_book(yb, ya)

    # Preserve exactly what arrived, including values this module rejects, so a
    # reader can always see WHY a quote was refused rather than only that it was.
    price = {
        "side": side,
        "marketTicker": market_ticker,
        "eventTicker": event_ticker,
        "observationId": observation_id,
        "capturedAt": captured_at,
        "quoteAgeSeconds": quote_age_seconds,
        "joinMethod": join_method,
        "source": source,
        "book": {
            "yesBid": yb,
            "yesAsk": ya,
            "noBid": nb,
            "noAsk": na,
            "yesBidRaw": yes_bid,
            "yesAskRaw": yes_ask,
            "bookState": book_state,
            "spreadCents": (round(ya - yb, 4)
                            if (ya is not None and yb is not None) else spread_cents),
        },
        "executablePrice": None,
        "priceBasis": None,
        "derivation": None,
        "refusalReason": None,
    }

    if book_state == BOOK_EMPTY:
        price["refusalReason"] = NO_BOOK
        return price

    if side == SIDE_YES:
        if ya is None:
            price["refusalReason"] = NO_YES_ASK
            return price
        price["executablePrice"] = ya
        price["priceBasis"] = BASIS_YES_ASK
        price["derivation"] = "genuine YES ask as observed"
        return price

    if side == SIDE_NO:
        if na is not None:
            price["executablePrice"] = na
            price["priceBasis"] = BASIS_NO_ASK
            price["derivation"] = "genuine NO ask as observed"
            return price
        # No genuine NO ask was archived. Derive from the YES bid -- and ONLY
        # from the YES bid: the complement of the YES ask is the NO bid, which
        # is the wrong side of the book to buy at.
        if yb is None:
            price["refusalReason"] = NO_BID_FOR_NO_SIDE
            return price
        price["executablePrice"] = round(CONTRACT_SETTLEMENT_CENTS - yb, 4)
        price["priceBasis"] = BASIS_DERIVED_NO_ASK_FROM_YES_BID
        price["derivation"] = (
            "buying NO is the other side of filling a resting YES bid, so the "
            "executable NO ask is exactly %d - yesBid(%s)"
            % (CONTRACT_SETTLEMENT_CENTS, yb))
        return price

    price["refusalReason"] = UNKNOWN_SIDE
    return price


def executable_probability(price):
    """Executable price as a probability in [0,1], or None. Never a midpoint."""
    p = (price or {}).get("executablePrice")
    return round(p / 100.0, 6) if p is not None else None


def is_executable(price):
    return (price or {}).get("executablePrice") is not None
