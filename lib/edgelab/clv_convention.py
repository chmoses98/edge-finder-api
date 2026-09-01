"""
lib/edgelab/clv_convention.py
=============================
THE canonical closing-line-value convention for this repository.

ONE definition, stated once, positive-is-good:

    good_clv_cents = closing side-relevant executable price
                     - entry side-relevant executable price

    POSITIVE  = you entered CHEAPER than the market closed  (GOOD)
    NEGATIVE  = you paid MORE than the market closed        (BAD)
    ZERO      = you entered exactly at the close

Side-relevance, for a BUYER of either side of a Kalshi binary:

    YES side : executable price = yesAsk            (what you pay to buy YES)
    NO  side : executable price = noAsk if archived,
               otherwise 100 - yesBid               (Kalshi's NO ask is the
                                                     complement of the YES bid)

WHY THIS MODULE EXISTS. The repository historically carried BOTH sign
conventions, sometimes in the same file:

  * positive-is-good  -- clv_update.py, scripts/fetch_kalshi_clv_v2.py,
                         scripts/clv_from_snapshot.py's clvMidPct/clvAskPct
  * inverted (entry - closing)
                      -- scripts/clv_from_snapshot.py's clv_pp,
                         lib/edgelab/clv.py::compute_clv_for_bet

while the documentation of the inverted ones described a positive value as
"entered at a better (cheaper) price than the close" -- which is the
OPPOSITE of what their own arithmetic produces. Consumers that read the
sign (lib/promotion_engine.py's "avgCLV >= 0 promotes", its
repeated-negative demotion rule, the Rule 71 tracker, market-intelligence
reports) therefore read the canonical ledger's stored `clv` backwards.

Audited evidence, read-only, in docs/EDGELAB_CLV_SIGN_AUDIT.md: of 385
canonical bets, 184 are decisively INVERTED, 0 are decisively
positive-is-good, 97 are sign-ambiguous (entry == closing, so CLV is 0 and
matches both), 104 have a null `clv`, and 0 are otherwise inconsistent --
ONE clean convention, uniformly applied.

SCOPE OF THIS MODULE. It supplies the definition and the helper. It does
NOT rewrite any historical row, does not multiply any stored value by -1,
and changes no existing writer's behaviour. Migrating historical rows is a
separate, explicitly authorized action -- see the audit document.
"""

CONVENTION_ID = "POSITIVE_IS_GOOD_CLOSING_MINUS_ENTRY_V1"

SIDE_YES = "YES"
SIDE_NO = "NO"


def executable_price_cents(quote, side):
    """
    The side-relevant price a BUYER of `side` would pay, in cents, from a
    quote dict carrying `yesBid`/`yesAsk` (and optionally `noBid`/`noAsk`).

    Returns None -- never a guess -- when the needed side of the book is
    absent. Midpoint is NEVER used: it is not a price anyone can trade at.
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


def good_clv_cents(entry_price_cents, closing_price_cents):
    """
    THE canonical formula. Both inputs are side-relevant EXECUTABLE prices
    in cents for the SAME side of the SAME contract.

    Positive result = entered cheaper than the close = good.
    Returns None if either price is missing.
    """
    if entry_price_cents is None or closing_price_cents is None:
        return None
    return round(float(closing_price_cents) - float(entry_price_cents), 4)


def good_clv_from_quotes(entry_quote, closing_quote, side):
    """Convenience wrapper: side-relevant executable prices from two quotes,
    then the canonical formula. Returns None if either side is unavailable."""
    entry = executable_price_cents(entry_quote, side)
    closing = executable_price_cents(closing_quote, side)
    return good_clv_cents(entry, closing)


def good_clv_from_implied(entry_implied, closing_implied):
    """Same convention for 0-1 side-relevant implied probabilities, scaled to
    cents. `closing - entry`, never the reverse."""
    if entry_implied is None or closing_implied is None:
        return None
    return round((float(closing_implied) - float(entry_implied)) * 100.0, 4)


def is_good(clv_cents):
    """True when the CLV is favourable under the canonical convention."""
    return clv_cents is not None and clv_cents > 0


def invert_legacy_entry_minus_closing(legacy_clv_cents):
    """
    Convert a value stored under the LEGACY inverted convention
    (`entry - closing`) into the canonical one.

    Provided so a future, explicitly authorized migration has ONE audited
    place to call rather than scattering `* -1` through the codebase. It is
    deliberately NOT invoked anywhere in production today, and callers must
    first establish that the specific row really was written under the
    legacy convention -- see docs/EDGELAB_CLV_SIGN_AUDIT.md, which shows a
    zero-CLV row is sign-ambiguous and must never be "converted" blindly.
    """
    if legacy_clv_cents is None:
        return None
    return round(-float(legacy_clv_cents), 4)
