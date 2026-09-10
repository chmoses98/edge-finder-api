#!/usr/bin/env python3
"""
lib/edgelab/canonical_price.py
==============================
WAVE 1, subwave B1. THE canonical executable-price representation.

One object, one vocabulary, one set of execution rules. Every production and
research surface that needs to answer "what price can actually be bought?"
should eventually consume this rather than deriving its own answer.

B1 IS SHADOW ONLY. Nothing here drives qualification, confidence, Bet Up To,
stake, bankroll or recommendation eligibility. B2 performs that cutover after
the blast radius is measured.

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

THE TRADEABLE RANGE -- BOUNDS, NOT A CENT LATTICE
--------------------------------------------------
An earlier draft of this module hard-coded MIN_TRADEABLE_CENTS = 1 and
MAX_TRADEABLE_CENTS = 99 and rejected everything outside that INTEGER band.
That was wrong, and dangerously so: it silently discards any genuine quote
finer than one cent. Kalshi's own market metadata carries an explicit price
grid, and the platform supports sub-cent grids (`deci_cent`,
`tapered_deci_cent`) alongside the whole-cent `linear_cent`.

The rule here is therefore a BOUND, not a lattice:

    a quote is executable iff   0 < price < 100 cents

strictly on both ends. 0 means no resting quote exists; 100 is the settlement
value of a winning contract, not a purchase anyone can make. Everything
strictly between is a real price, INCLUDING 0.1c, 0.5c and 99.5c.

WHAT THE ARCHIVE ACTUALLY SAYS ABOUT THE GRID (measured, not assumed)
---------------------------------------------------------------------
  * Raw Kalshi market metadata is preserved for 3,255 MLB markets in
    data/kalshi/discovery/*_f3_f7_search.json. EVERY one of them declares
        price_level_structure = "linear_cent"
        price_ranges = [{start: "0.0000", end: "1.0000", step: "0.0100"}]
    i.e. a 1-cent grid over the full range. Series covered: KXMLBF3, KXMLBF7.
  * Prices arrive on the wire as FIXED-POINT DECIMAL STRINGS with four decimal
    places of dollars ("0.5900"), i.e. the wire format resolves to 0.01 CENTS.
    16,275 such values measured. The transport can express sub-cent; today's
    MLB markets simply do not use it.
  * 542,716 archived observations and 573,035 archived raw registry records
    contain ZERO non-integer cent values. Smallest positive quote seen: 1c.

So: MLB is empirically all-linear-cent today, and this module records that
finding -- but it does NOT encode it as an invariant, because a grid change on
Kalshi's side would otherwise silently delete real quotes from our book.

WHY DECIMAL AND NOT FLOAT
-------------------------
Money is exact. Binary floats are not: 0.1 + 0.2 != 0.3, and a dollars->cents
conversion done as `0.29 * 100` yields 28.999999999999996. On a whole-cent grid
that rounds away harmlessly, which is exactly why it survives unnoticed until
the grid gets finer -- at which point the error lands in the same decimal place
as the price itself. Every money value here is a `decimal.Decimal` built from
the STRING form of the input, so a quote that arrives as "0.5900" is exactly
59 cents and the derived NO ask is exactly 100 - yesBid with no residue.

Decimals do not serialise to JSON, so `to_jsonable()` renders them as decimal
STRINGS, preserving precision through the artifact. Floats appear only in
`*Float` mirror fields, clearly named, for consumers that cannot take a string.

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

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation


def parse_instant(value):
    """
    ISO-8601 -> aware UTC datetime, or None. Tolerates 'Z' and offsets.

    The ONE implementation of observation-time parsing. It lives here, beside
    the canonical price, because quote age is part of whether a price may
    gate money: production_price needs it to answer "is this quote too old to
    trade on", and lib.edgelab.observation_join re-exports it as `parse_ts`
    for the audit path.

    W1-B2 note on direction. production_price originally imported `parse_ts`
    FROM observation_join, which pointed the real-money pricing seam at a
    600-line gzip/archive scanner -- an audit tool. That is backwards: the
    money path must not be able to break because an audit module grew a
    dependency, and it made the pricing seam unusable anywhere the archive
    tooling is absent (it broke the end-to-end sandbox chain outright).
    Anything both paths need belongs in the smaller, lower module.
    """
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


# The executable band. EXCLUSIVE on both ends, and deliberately NOT a lattice:
# a quote is a real price if it lies strictly inside, whatever its precision.
EXCLUSIVE_MIN_CENTS = Decimal("0")
EXCLUSIVE_MAX_CENTS = Decimal("100")
CONTRACT_SETTLEMENT_CENTS = Decimal("100")

# Kalshi price grids, as named by `price_level_structure` in market metadata.
# GRID_UNKNOWN is the default and means "no metadata was supplied": the value is
# range-checked but never grid-checked, so an unfamiliar grid can never cause a
# genuine quote to be dropped.
GRID_UNKNOWN = None
GRID_LINEAR_CENT = "linear_cent"
GRID_DECI_CENT = "deci_cent"
GRID_TAPERED_DECI_CENT = "tapered_deci_cent"

# Tick size per grid, where the grid has a single uniform tick. tapered grids
# vary their tick by price region, so they are deliberately absent: a tapered
# quote is range-checked and reported as unverified rather than measured
# against a tick this module would have to invent.
GRID_TICK_CENTS = {
    GRID_LINEAR_CENT: Decimal("1"),
    GRID_DECI_CENT: Decimal("0.1"),
}

# The finest resolution the Kalshi wire format can express: dollar strings carry
# four decimal places, so 0.0001 dollars = 0.01 cents. Anything finer than this
# did not come from the exchange, and is recorded as such rather than trusted.
WIRE_CENT_QUANTUM = Decimal("0.01")

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

# Why a single raw value was not accepted as a quote. Recorded per side so a
# reader can tell "there was no bid" from "there was a bid we could not read".
QUOTE_ABSENT_NULL = "ABSENT_NULL"
QUOTE_ABSENT_ZERO = "ABSENT_ZERO_MEANS_NO_RESTING_QUOTE"
QUOTE_REJECTED_RANGE = "REJECTED_OUTSIDE_EXECUTABLE_BAND"
QUOTE_REJECTED_UNPARSEABLE = "REJECTED_NOT_A_NUMBER"
QUOTE_ACCEPTED = "ACCEPTED"

UNIT_CENTS = "cents"
UNIT_DOLLARS = "dollars"

_CENTS_PER_DOLLAR = Decimal("100")


def _to_decimal(value):
    """
    Exact Decimal from whatever arrived, or None.

    The conversion goes through `str(value)` on purpose. `Decimal(0.59)` is
    0.58999999999999996891375531049561686813831329345703125 -- the binary float
    that was handed in -- whereas `Decimal("0.59")` is exactly 59/100. Anything
    that has already been through a float has lost what it lost, but this at
    least stops the loss from compounding, and a value that arrives as a string
    (which is how Kalshi sends it) stays exact end to end.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        dec = Decimal(str(value).strip())
    except (InvalidOperation, ValueError, AttributeError, TypeError):
        return None
    return None if not dec.is_finite() else dec


def grid_tick_cents(grid):
    """The uniform tick for a named grid, or None when it has none/is unknown."""
    return GRID_TICK_CENTS.get(grid)


def on_grid(cents, grid):
    """
    True/False when the grid has a uniform tick and the value can be checked,
    None when there is nothing to check against (unknown or tapered grid).

    Being off-grid is REPORTED, never corrected. Snapping a quote to a tick we
    believe in would replace an exchange fact with our own assumption, and the
    whole point of this module is to stop doing that.
    """
    tick = grid_tick_cents(grid)
    if cents is None or tick is None or tick == 0:
        return None
    return (cents % tick) == 0


def normalize_quote(value, unit=UNIT_CENTS, grid=GRID_UNKNOWN):
    """
    Returns (cents_or_None, status). The status says WHY when the answer is None.

    The unit is DECLARED by the caller and never guessed. The repository carries
    both conventions -- the Kalshi registry stores dollars (0.72), the
    observation archive stores cents (73) -- and an earlier draft of this
    function tried to infer which from the magnitude. That heuristic was wrong
    in both directions and this is a money path, so it is gone:

      * it read a legitimate 1-cent quote as $1.00 and discarded it. There are
        3,810 such quotes in ten partitions alone (1,589 bids, 2,221 asks) --
        longshot contracts, exactly where a mispriced edge is most tempting.
      * symmetrically, a registry value of 1.0 ($1.00, i.e. an untradeable
        100c contract) would have been read as a 1-cent bargain.

    The same heuristic is still live upstream in this repository (see the
    B1 report's subpenny-audit section); it is reported there, not fixed here,
    because changing what production captures is not a shadow-mode change.

    ZERO IS NOT A PRICE, BUT 0.5 CENTS IS. Zero means no resting quote exists --
    that is the single rule which stops `(bid or 0)` arithmetic at the source --
    and it is reported with its own status precisely so it stays distinguishable
    from a genuine sub-cent quote, which is accepted in full precision.
    """
    if unit not in (UNIT_CENTS, UNIT_DOLLARS):
        raise ValueError("unit must be %r or %r, got %r"
                         % (UNIT_CENTS, UNIT_DOLLARS, unit))
    if value is None:
        return None, QUOTE_ABSENT_NULL

    dec = _to_decimal(value)
    if dec is None:
        return None, QUOTE_REJECTED_UNPARSEABLE

    cents = dec * _CENTS_PER_DOLLAR if unit == UNIT_DOLLARS else dec

    if cents == EXCLUSIVE_MIN_CENTS:
        return None, QUOTE_ABSENT_ZERO
    if cents <= EXCLUSIVE_MIN_CENTS or cents >= EXCLUSIVE_MAX_CENTS:
        return None, QUOTE_REJECTED_RANGE
    return cents, QUOTE_ACCEPTED


def normalize_cents(value, unit=UNIT_CENTS, grid=GRID_UNKNOWN):
    """normalize_quote's value alone, for callers that do not need the status."""
    return normalize_quote(value, unit=unit, grid=grid)[0]


def classify_book(yes_bid_cents, yes_ask_cents):
    if yes_bid_cents is not None and yes_ask_cents is not None:
        return BOOK_TWO_SIDED
    if yes_ask_cents is not None:
        return BOOK_ASK_ONLY
    if yes_bid_cents is not None:
        return BOOK_BID_ONLY
    return BOOK_EMPTY


def build_price(side, *, yes_bid=None, yes_ask=None, no_bid=None, no_ask=None,
                unit=UNIT_CENTS, grid=GRID_UNKNOWN, market_ticker=None,
                event_ticker=None, observation_id=None, captured_at=None,
                spread_cents=None, quote_age_seconds=None, join_method=None,
                source=None, side_basis=None, side_evidence=None):
    """
    THE canonical price object. Raw book and derived execution semantics are
    kept strictly separate: the `book` sub-object is what was observed, and the
    top-level executable fields are what can actually be purchased.

    Returns a dict rather than a class so it serialises into JSONL artifacts
    (via to_jsonable) and can be diffed field by field in a receipt. Money
    values are Decimal; `to_jsonable` renders them as exact decimal strings.

    `side_basis`/`side_evidence` carry the provenance of HOW the purchased side
    was determined. They are pass-through: this module never decides a side, and
    a caller that cannot prove one must pass side=None, which refuses.
    """
    yb, yb_status = normalize_quote(yes_bid, unit, grid)
    ya, ya_status = normalize_quote(yes_ask, unit, grid)
    nb, nb_status = normalize_quote(no_bid, unit, grid)
    na, na_status = normalize_quote(no_ask, unit, grid)

    book_state = classify_book(yb, ya)

    # Preserve exactly what arrived, including values this module rejects, so a
    # reader can always see WHY a quote was refused rather than only that it was.
    price = {
        "side": side,
        "sideBasis": side_basis,
        "sideEvidence": side_evidence,
        "marketTicker": market_ticker,
        "eventTicker": event_ticker,
        "observationId": observation_id,
        "capturedAt": captured_at,
        "quoteAgeSeconds": quote_age_seconds,
        "joinMethod": join_method,
        "source": source,
        "priceUnitDeclared": unit,
        "priceGridDeclared": grid,
        "book": {
            "yesBid": yb,
            "yesAsk": ya,
            "noBid": nb,
            "noAsk": na,
            "yesBidRaw": yes_bid,
            "yesAskRaw": yes_ask,
            "yesBidStatus": yb_status,
            "yesAskStatus": ya_status,
            "noBidStatus": nb_status,
            "noAskStatus": na_status,
            "yesBidOnGrid": on_grid(yb, grid),
            "yesAskOnGrid": on_grid(ya, grid),
            "bookState": book_state,
            "spreadCents": ((ya - yb) if (ya is not None and yb is not None)
                            else _to_decimal(spread_cents)),
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
        price["executablePrice"] = CONTRACT_SETTLEMENT_CENTS - yb
        price["priceBasis"] = BASIS_DERIVED_NO_ASK_FROM_YES_BID
        price["derivation"] = (
            "buying NO is the other side of filling a resting YES bid, so the "
            "executable NO ask is exactly %s - yesBid(%s)"
            % (CONTRACT_SETTLEMENT_CENTS, yb))
        return price

    price["refusalReason"] = UNKNOWN_SIDE
    return price


def executable_probability(price):
    """
    Executable price as an exact Decimal probability in (0,1), or None.

    Never a midpoint. Exact division: cents/100 is a decimal shift, so no
    rounding is required and none is applied.
    """
    p = (price or {}).get("executablePrice")
    return (p / _CENTS_PER_DOLLAR) if p is not None else None


def is_executable(price):
    return (price or {}).get("executablePrice") is not None


def to_jsonable(value):
    """
    Recursively render Decimals as exact decimal STRINGS for serialisation.

    A string, not a float: `float(Decimal("0.1"))` is 0.1000000000000000055...
    and writing that into an audit artifact would mean the artifact no longer
    says what the exchange said. Readers that want arithmetic can Decimal() it
    back losslessly.
    """
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    if isinstance(value, dict):
        return {k: to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(v) for v in value]
    return value


def as_float(value):
    """
    Decimal -> float, for the one place it is legitimate: handing a price to
    an existing production function whose signature takes a float. Never used
    to store or compare a canonical money value.
    """
    return None if value is None else float(value)
