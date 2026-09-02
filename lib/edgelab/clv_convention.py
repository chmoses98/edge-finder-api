"""
lib/edgelab/clv_convention.py
=============================
THE canonical closing-line-value convention for this repository.

ONE definition, stated once, positive-is-good:

    clv = side-relevant CLOSING executable price
        - side-relevant ENTRY   executable price

    POSITIVE  = you entered CHEAPER than the market closed   (GOOD)
    NEGATIVE  = you paid MORE than the market closed         (BAD)
    ZERO      = you entered exactly at the close

Universal semantic statement, true for every CLV surface in this repo:

    POSITIVE = THE MARKET MOVED TOWARD OUR PURCHASED SIDE.

SIDE-RELEVANCE, for a BUYER of either side of a Kalshi binary:

    YES : executable price = yesAsk
    NO  : executable price = noAsk when archived,
          otherwise 100 - yesBid   (Kalshi's NO ask is the complement of
                                    the YES bid)

Both legs of a CLV must be the SAME side of the SAME contract. Comparing a
YES entry against a NO close is meaningless and this module cannot express
it: the side is chosen once, by the caller, via clv_for_yes/clv_for_no.

UNITS ARE EXPLICIT AND NEVER SILENTLY MIXED. Three are supported:

    UNIT_CENTS               0-100   Kalshi contract cents
    UNIT_PROBABILITY         0-1     implied probability
    UNIT_PERCENTAGE_POINTS   0-100   probability x 100

Every helper takes and returns ONE unit, named. `convert` is the only way
to move between them, and mixing units in a single call raises rather than
silently producing a number that is wrong by 100x.

EXECUTABLE vs FAIR-MID. This module is for EXECUTABLE CLV -- prices a
taker could actually trade at. A separate, explicitly-named FAIR-MID CLV
(closing mid - entry mid) exists for characterising consensus movement;
it is NON-EXECUTABLE and must never be mixed into fill economics or ROI.
See lib.edgelab.mlb_alpha_shadow.fair_mid_clv_cents. Midpoint is never
used here.

WHY THIS MODULE EXISTS. The repository historically carried BOTH sign
conventions, sometimes in the same file, while documenting the inverted
ones as positive-is-good. Full evidence, the consumer trace, and the
migration record: docs/EDGELAB_CLV_SIGN_AUDIT.md.
"""

CONVENTION_ID = "POSITIVE_IS_GOOD_V1"
CONVENTION_DESCRIPTION = (
    "clv = side-relevant closing executable price - side-relevant entry "
    "executable price; positive means the market moved toward the "
    "purchased side")

# The legacy convention this repo used to write, kept ONLY so historical
# rows can be recognised and described. Never emit it for new data.
LEGACY_INVERTED_CONVENTION_ID = "LEGACY_ENTRY_MINUS_CLOSING"

SIDE_YES = "YES"
SIDE_NO = "NO"

UNIT_CENTS = "CENTS"
UNIT_PROBABILITY = "PROBABILITY"
UNIT_PERCENTAGE_POINTS = "PERCENTAGE_POINTS"
_UNITS = (UNIT_CENTS, UNIT_PROBABILITY, UNIT_PERCENTAGE_POINTS)

# Scale of one unit expressed in "probability" terms.
_TO_PROBABILITY = {
    UNIT_CENTS: 0.01,
    UNIT_PROBABILITY: 1.0,
    UNIT_PERCENTAGE_POINTS: 0.01,
}


def _check_unit(unit):
    if unit not in _UNITS:
        raise ValueError("unknown CLV unit %r; expected one of %s" % (unit, list(_UNITS)))
    return unit


def convert(value, from_unit, to_unit):
    """The ONLY sanctioned way to move a CLV (or a price) between units."""
    _check_unit(from_unit)
    _check_unit(to_unit)
    if value is None:
        return None
    if from_unit == to_unit:
        return float(value)
    as_probability = float(value) * _TO_PROBABILITY[from_unit]
    return as_probability / _TO_PROBABILITY[to_unit]


# ---------------------------------------------------------------------------
# Side-relevant executable prices
# ---------------------------------------------------------------------------

def executable_price_cents(quote, side):
    """
    The side-relevant price a BUYER of `side` would pay, in CENTS, from a
    quote carrying yesBid/yesAsk (and optionally noBid/noAsk).

    Returns None -- never a guess -- when the needed side of the book is
    absent. Midpoint is NEVER substituted: it is not tradable.
    """
    if quote is None:
        return None
    if side == SIDE_NO:
        no_ask = quote.get("noAsk")
        if no_ask is not None:
            return float(no_ask)
        yes_bid = quote.get("yesBid")
        return (100.0 - float(yes_bid)) if yes_bid is not None else None
    if side == SIDE_YES:
        yes_ask = quote.get("yesAsk")
        return float(yes_ask) if yes_ask is not None else None
    return None


# ---------------------------------------------------------------------------
# THE formula -- one implementation, side-aware wrappers
# ---------------------------------------------------------------------------

def _clv(entry, closing, unit):
    _check_unit(unit)
    if entry is None or closing is None:
        return None
    return round(float(closing) - float(entry), 6)


def clv_for_yes(entry_price, closing_price, unit=UNIT_CENTS):
    """CLV for a purchased YES. Both prices are executable YES prices in
    `unit`. Positive = bought below the close."""
    return _clv(entry_price, closing_price, unit)


def clv_for_no(entry_price, closing_price, unit=UNIT_CENTS):
    """CLV for a purchased NO. Both prices are executable NO prices in
    `unit` (noAsk, or 100 - yesBid in cents). Positive = bought below the
    close."""
    return _clv(entry_price, closing_price, unit)


def clv_for_side(entry_price, closing_price, side, unit=UNIT_CENTS):
    """Dispatch by side. Raises on an unknown side rather than guessing."""
    if side == SIDE_YES:
        return clv_for_yes(entry_price, closing_price, unit)
    if side == SIDE_NO:
        return clv_for_no(entry_price, closing_price, unit)
    raise ValueError("unknown side %r; expected %r or %r" % (side, SIDE_YES, SIDE_NO))


def good_clv_cents(entry_price_cents, closing_price_cents):
    """Back-compatible cents helper (side already resolved by the caller)."""
    return _clv(entry_price_cents, closing_price_cents, UNIT_CENTS)


def good_clv_from_quotes(entry_quote, closing_quote, side, unit=UNIT_CENTS):
    """Side-relevant executable prices from two quotes, then the canonical
    formula. Returns None if either side of either book is unavailable."""
    entry = executable_price_cents(entry_quote, side)
    closing = executable_price_cents(closing_quote, side)
    value = _clv(entry, closing, UNIT_CENTS)
    return convert(value, UNIT_CENTS, unit) if value is not None else None


def good_clv_from_implied(entry_implied, closing_implied,
                          unit=UNIT_PERCENTAGE_POINTS):
    """Canonical CLV from 0-1 side-relevant implied probabilities, returned
    in `unit` (percentage points by default). `closing - entry`, never the
    reverse."""
    value = _clv(entry_implied, closing_implied, UNIT_PROBABILITY)
    return convert(value, UNIT_PROBABILITY, unit) if value is not None else None


def is_good(clv_value):
    """True when the CLV is favourable under the canonical convention."""
    return clv_value is not None and clv_value > 0


def convention_marker(unit=UNIT_CENTS):
    """The provenance stamp new canonical CLV outputs should carry."""
    return {"clvConvention": CONVENTION_ID, "clvUnit": _check_unit(unit)}


def invert_legacy_entry_minus_closing(legacy_clv_value):
    """
    Convert a value stored under the LEGACY inverted convention
    (`entry - closing`) into the canonical one.

    Provided so a migration has ONE audited place to call rather than
    scattering `* -1` through the codebase. Callers MUST first establish
    that the specific row really was written under the legacy convention:
    a zero-CLV row is sign-ambiguous (it matches both formulas) and must
    never be "converted" on the strength of its sign alone. Preferred
    practice is to RECOMPUTE from side/entry/closing instead of negating.
    """
    if legacy_clv_value is None:
        return None
    return round(-float(legacy_clv_value), 6)
