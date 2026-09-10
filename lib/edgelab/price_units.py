#!/usr/bin/env python3
"""
lib/edgelab/price_units.py
==========================
WAVE 1, subwave B2. Declared-unit price conversion, replacing magnitude
inference everywhere it touched a money path.

WHY THIS EXISTS
---------------
The pipeline carried SEVEN independent copies of the same guess:

    return v if v <= 1.0 else v / 100.0          # or the mirror image

    H1  scripts/fetch_kalshi_markets.py::norm
    H2  lib/kalshi_mlb_contract_parser.py::_price_to_pct
    H3  scripts/build_kalshi_registry.py::norm
    H4  lib/kalshi_registry_market_builders.py::norm
    H5  scripts/build_market_ledger.py::_to_cents        (defined twice)
    H6  scripts/build_market_ledger.py::american_to_ask_cents
    H7  scripts/build_market_ledger.py::_tc2

Each one decides dollars-vs-cents from the SIZE of the number, and each is
wrong in both directions at the boundary:

    a genuine 1-cent quote arriving as `1`      -> read as $1.00 -> 100 cents
    a genuine $1.00 arriving as `1.0`           -> read as 1 cent

One cent is not an exotic value. It is where the longshots live, which is
exactly where a mispriced edge looks most attractive. A heuristic that turns a
1c ask into a 99c cost, or a 100c untradeable contract into a 1c bargain, is
not an edge case on a money path -- it is the case that loses the money.

THE RULE
--------
The unit is a property of the FIELD, not of the value. Kalshi says which it is
by the field name: `yes_bid_dollars` is dollars, `yes_bid` is integer cents.
So the caller names the field, this module knows its unit, and nothing is ever
inferred from magnitude.

WHY `is not None` AND NOT `or`
------------------------------
The old code read `mkt.get('yes_bid') or mkt.get('yes_bid_dollars')`. A bid of
ZERO is falsy, so a genuine zero silently fell through to the other field and
was re-read in the wrong unit. Presence is tested explicitly here, and zero is
returned as zero -- it means "no resting quote", which is a fact worth keeping,
not an absence worth papering over.

WHAT THIS MODULE DOES NOT DO
----------------------------
It does not decide whether a quote is tradeable. Range rules, the executable
band and the zero-means-absent semantics belong to
`lib.edgelab.canonical_price`, which is the single authority on what can be
bought. This module only answers "how many cents is this number", exactly, and
hands the answer on.
"""

from decimal import Decimal, InvalidOperation

from lib.edgelab.canonical_price import UNIT_CENTS, UNIT_DOLLARS

CENTS_PER_DOLLAR = Decimal("100")

# Kalshi field name -> the unit that field is denominated in.
#
# The `_dollars` / `_fp` suffixes are Kalshi's own fixed-point naming from its
# decimal migration; the bare names are the older integer-cent fields. Both
# shapes are still observed in this repository's archives, which is precisely
# why the unit must be read from the name rather than the number.
KALSHI_FIELD_UNITS = {
    "yes_bid_dollars": UNIT_DOLLARS,
    "yes_ask_dollars": UNIT_DOLLARS,
    "no_bid_dollars": UNIT_DOLLARS,
    "no_ask_dollars": UNIT_DOLLARS,
    "last_price_dollars": UNIT_DOLLARS,
    "previous_price_dollars": UNIT_DOLLARS,
    "previous_yes_ask_dollars": UNIT_DOLLARS,
    "previous_yes_bid_dollars": UNIT_DOLLARS,
    "yes_bid": UNIT_CENTS,
    "yes_ask": UNIT_CENTS,
    "no_bid": UNIT_CENTS,
    "no_ask": UNIT_CENTS,
    "last_price": UNIT_CENTS,
}

# Field pairs, best-evidence first. The dollar form is preferred because it is
# what the current API emits and it carries four decimal places; the cent form
# is read as an explicit fallback for archived rows and any future rollback.
YES_BID_FIELDS = ("yes_bid_dollars", "yes_bid")
YES_ASK_FIELDS = ("yes_ask_dollars", "yes_ask")
NO_BID_FIELDS = ("no_bid_dollars", "no_bid")
NO_ASK_FIELDS = ("no_ask_dollars", "no_ask")
LAST_PRICE_FIELDS = ("last_price_dollars", "last_price")


class UnknownPriceUnit(ValueError):
    """Raised when a caller names a field this module has no unit for."""


def unit_for_field(field_name):
    try:
        return KALSHI_FIELD_UNITS[field_name]
    except KeyError:
        raise UnknownPriceUnit(
            "no declared unit for Kalshi field %r -- add it to KALSHI_FIELD_UNITS "
            "rather than guessing from the value" % (field_name,))


def to_decimal(value):
    """
    Exact Decimal, via the STRING form, or None.

    `Decimal(0.59)` is the binary float that was handed in, to fifty digits.
    `Decimal("0.59")` is exactly 59/100. Kalshi sends these as strings; going
    through `str()` keeps them exact when they arrive that way and stops the
    loss compounding when they do not.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        dec = Decimal(str(value).strip())
    except (InvalidOperation, ValueError, AttributeError, TypeError):
        return None
    return dec if dec.is_finite() else None


def to_cents(value, unit):
    """
    Exact cents as a Decimal, or None if the value is not a number.

    ZERO IS PRESERVED as zero. Range and tradeability are not this module's
    business -- `canonical_price` owns those -- so 0 and 100 come back as 0 and
    100 rather than being turned into None here.
    """
    if unit not in (UNIT_CENTS, UNIT_DOLLARS):
        raise UnknownPriceUnit("unit must be %r or %r, got %r"
                               % (UNIT_CENTS, UNIT_DOLLARS, unit))
    dec = to_decimal(value)
    if dec is None:
        return None
    return dec * CENTS_PER_DOLLAR if unit == UNIT_DOLLARS else dec


def to_dollars(value, unit):
    """Exact dollars as a Decimal, or None. The inverse framing of to_cents."""
    cents = to_cents(value, unit)
    return None if cents is None else cents / CENTS_PER_DOLLAR


def read_cents(raw, *field_names):
    """
    (cents_or_None, source_field_or_None) from the first field that is PRESENT.

    Presence, not truthiness: a zero is a value and stops the search, because a
    zero bid is a real statement about the book. `or`-chaining these fields --
    which is what the pipeline did -- let a genuine 0 fall through to a field in
    a different unit and be re-scaled.
    """
    for field_name in field_names:
        if not isinstance(raw, dict):
            break
        if field_name in raw and raw[field_name] is not None:
            return to_cents(raw[field_name], unit_for_field(field_name)), field_name
    return None, None


def read_book_cents(raw):
    """
    The whole book for one contract, in exact cents, with its provenance.

    Returns {yesBid, yesAsk, noBid, noAsk, lastPrice, sourceFields} where every
    price is a Decimal or None and `sourceFields` names the field each value
    actually came from -- so a reader can tell a dollar-sourced quote from a
    cent-sourced one without re-deriving it.
    """
    book = {}
    sources = {}
    for key, fields in (("yesBid", YES_BID_FIELDS), ("yesAsk", YES_ASK_FIELDS),
                        ("noBid", NO_BID_FIELDS), ("noAsk", NO_ASK_FIELDS),
                        ("lastPrice", LAST_PRICE_FIELDS)):
        value, source = read_cents(raw, *fields)
        book[key] = value
        sources[key] = source
    book["sourceFields"] = sources
    return book


def cents_to_float(value):
    """
    Decimal cents -> float, for the one legitimate use: handing a price to an
    existing function whose signature takes a float. Never for storage or
    comparison of a canonical money value.
    """
    return None if value is None else float(value)


def format_cents(value):
    """Exact decimal STRING for serialisation, or None. Never a float."""
    if value is None:
        return None
    return format(Decimal(value).normalize(), "f")
