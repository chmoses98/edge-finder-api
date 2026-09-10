#!/usr/bin/env python3
"""
lib/edgelab/production_price.py
===============================
WAVE 1, subwave B2. THE single seam through which production obtains an
executable price. Money-touching.

After B2 there is exactly one rule for whether a recommendation may be
actionable, and it is enforced here:

    a candidate is priceable ONLY IF the system can prove
      1. the exact Kalshi contract,
      2. the side being purchased,
      3. a genuine executable price for that side,
      4. the book evidence and its age.

If any of the four cannot be proved the answer is NO PRICE, with a named
refusal reason. There is no fallback. Not the midpoint, not the vig-free
market probability, not the last trade, not a synthesised ask.

WHY A SEAM AND NOT FIVE DERIVATIONS
-----------------------------------
Before B2, five market families each derived their own executable price, and
four of the five ended up at a midpoint by a different route (see
docs/W1B2_PRICE_PATH_TRACE.md). Every one of them also carried its own
dollars-vs-cents guess. One seam means one place to be right, one place to
test, and one place a future family must come through.

The pricing SEMANTICS are not re-implemented here either -- they live in
`lib.edgelab.canonical_price`, which B1 established and which this module
calls. This file adds only what production additionally needs: freshness, and
the provenance record that makes an executable price auditable after the fact.

QUOTE FRESHNESS -- MEASURED, NOT CHOSEN
---------------------------------------
The repository has two decision paths with very different price ages, and the
policy has to be right for the money-touching one:

  * THE AUTHORITATIVE PATH (`.github/workflows/fetch-slate.yml`) fetches the
    book SYNCHRONOUSLY inside the same job that produces recommendations:
        L374 fetch_kalshi_markets.py -> L379 build_kalshi_registry.py
        -> L429 merge_odds.py -> L435 build_market_ledger.py
        -> L463 validate_slate_final.py -> L576 risk_gate.py
        -> L582 write_pending_bets.py   (writes bets.json)
    At the moment a recommendation is produced the quote is SECONDS old.

  * THE PROSPECTIVE PATH (`model-snapshot-scheduler.yml`, every 15 minutes)
    re-evaluates games WITHOUT refreshing Kalshi first. It reads whatever
    registry the last fetch-slate run left behind, and fetch-slate is
    scheduled only at 16:00, 20:00 and 22:00 UTC. Those rows can therefore be
    priced off a book up to about four hours old.

So the ceiling is not a round number picked for comfort. It is set to admit a
synchronous in-run fetch plus job latency, and to REFUSE anything that could
only have come from a stale committed registry:

    MAX_QUOTE_AGE_SECONDS = 1800

Thirty minutes is far above the authoritative path's true age (seconds, plus
the handful of pipeline steps between the fetch and the ledger) and far below
the ~4h staleness the prospective path can reach. It also catches a specific
live hazard: fetch-slate runs `fetch_kalshi_markets.py ... || true`, so a
FAILED fetch does not fail the job -- the registry is then rebuilt from the
previous snapshot and the ledger prices off it with nothing to show that
anything went wrong. Under B2 those quotes age out and refuse.

A stale quote is still returned for research and audit. It simply cannot make
a candidate actionable.
"""

from decimal import Decimal

from lib.edgelab import canonical_price as cp
from lib.edgelab import price_units as pu

# See the module docstring for the derivation. Thirty minutes.
MAX_QUOTE_AGE_SECONDS = 1800

# Refusal vocabulary. Every non-priceable candidate carries exactly one, and
# each names what could not be PROVEN rather than merely reporting absence.
REFUSE_NO_CONTRACT = "PRICE_REFUSED_CONTRACT_NOT_IDENTIFIED"
REFUSE_NO_BOOK = "PRICE_REFUSED_NO_BOOK_EVIDENCE"
REFUSE_NO_SIDE = "PRICE_REFUSED_SIDE_NOT_PROVEN"
REFUSE_NOT_EXECUTABLE = "PRICE_REFUSED_NO_EXECUTABLE_PRICE_FOR_THIS_SIDE"
REFUSE_STALE = "PRICE_REFUSED_QUOTE_TOO_OLD_FOR_PRODUCTION_AUTHORITY"
REFUSE_NO_CAPTURE_TIME = "PRICE_REFUSED_QUOTE_AGE_UNKNOWN"

REFUSAL_REASONS = (
    REFUSE_NO_CONTRACT, REFUSE_NO_BOOK, REFUSE_NO_SIDE,
    REFUSE_NOT_EXECUTABLE, REFUSE_STALE, REFUSE_NO_CAPTURE_TIME,
)


def _age_seconds(captured_at, decided_at):
    """Quote age in seconds, or None when either instant is unreadable."""
    from lib.edgelab.observation_join import parse_ts
    captured = parse_ts(captured_at)
    decided = parse_ts(decided_at)
    if captured is None or decided is None:
        return None
    return (decided - captured).total_seconds()


def price_contract(*, market_ticker, side, yes_bid, yes_ask, no_bid=None,
                   no_ask=None, unit=cp.UNIT_CENTS, grid=cp.GRID_UNKNOWN,
                   captured_at=None, decided_at=None, event_ticker=None,
                   observation_id=None, source=None, side_basis=None,
                   side_evidence=None, max_age_seconds=MAX_QUOTE_AGE_SECONDS,
                   require_fresh=True):
    """
    The one production entry point. Returns a dict:

        {"executablePriceCents": Decimal|None,   exact, pre-fee
         "executablePriceFloat": float|None,     for legacy float signatures
         "actionable": bool,                     may this price gate real money
         "refusalReason": str|None,
         "provenance": {...}}                    always populated

    `actionable` is False whenever the price is absent OR the quote is too old.
    A stale price is still reported -- research and audit want it -- but it can
    never make a candidate actionable.

    Nothing here knows about edges, thresholds, confidence, staking or
    bankroll. It answers one question: what would this side cost, and can we
    prove it.
    """
    provenance = {
        "marketTicker": market_ticker,
        "eventTicker": event_ticker,
        "observationId": observation_id,
        "capturedAt": captured_at,
        "decidedAt": decided_at,
        "source": source,
        "side": side,
        "sideBasis": side_basis,
        "sideEvidence": side_evidence,
        "priceUnitDeclared": unit,
        "priceGridDeclared": grid,
        "quoteAgeSeconds": None,
        "maxQuoteAgeSeconds": max_age_seconds,
        "stale": None,
        "bookState": None,
        "priceBasis": None,
        "book": None,
    }

    def refuse(reason):
        return {"executablePriceCents": None, "executablePriceFloat": None,
                "actionable": False, "refusalReason": reason,
                "provenance": provenance}

    # 1. The exact contract.
    if not market_ticker:
        return refuse(REFUSE_NO_CONTRACT)

    # 2. The side. An unproven side is never defaulted to YES -- that was the
    #    B1 fail-open defect, and on a NO-side contract it prices the wrong end
    #    of the book.
    if side not in (cp.SIDE_YES, cp.SIDE_NO):
        return refuse(REFUSE_NO_SIDE)

    # 3. The book and the executable price, via the canonical object.
    price = cp.build_price(
        side, yes_bid=yes_bid, yes_ask=yes_ask, no_bid=no_bid, no_ask=no_ask,
        unit=unit, grid=grid, market_ticker=market_ticker,
        event_ticker=event_ticker, observation_id=observation_id,
        captured_at=captured_at, source=source,
        side_basis=side_basis, side_evidence=side_evidence,
    )
    provenance["bookState"] = price["book"]["bookState"]
    provenance["book"] = price["book"]
    provenance["priceBasis"] = price["priceBasis"]

    if price["book"]["bookState"] == cp.BOOK_EMPTY:
        return refuse(REFUSE_NO_BOOK)
    if not cp.is_executable(price):
        return refuse(price["refusalReason"] or REFUSE_NOT_EXECUTABLE)

    # 4. The age. Proving a price without knowing when it was true is not
    #    proving it; an unknown age refuses rather than being treated as fresh.
    age = _age_seconds(captured_at, decided_at)
    provenance["quoteAgeSeconds"] = age
    cents = price["executablePrice"]

    if require_fresh:
        if age is None:
            return refuse(REFUSE_NO_CAPTURE_TIME)
        provenance["stale"] = age > max_age_seconds
        if provenance["stale"]:
            # Reported, not discarded: the price is real, it is simply too old
            # to gate money. Callers keep it for research and audit.
            return {"executablePriceCents": cents,
                    "executablePriceFloat": pu.cents_to_float(cents),
                    "actionable": False, "refusalReason": REFUSE_STALE,
                    "provenance": provenance}
    else:
        provenance["stale"] = (age is not None and age > max_age_seconds)

    return {"executablePriceCents": cents,
            "executablePriceFloat": pu.cents_to_float(cents),
            "actionable": True, "refusalReason": None,
            "provenance": provenance}


def edge_field_provenance(result):
    """
    The flat provenance block production rows carry alongside
    `executablePriceUsed`, so an auditor can answer "where did this price come
    from and when was it true" from the row itself.
    """
    p = (result or {}).get("provenance") or {}
    book = p.get("book") or {}
    return {
        "executablePriceBasis": p.get("priceBasis"),
        "executablePriceSource": p.get("source"),
        "executablePriceMarketTicker": p.get("marketTicker"),
        "executablePriceEventTicker": p.get("eventTicker"),
        "executablePriceObservationId": p.get("observationId"),
        "executablePriceCapturedAt": p.get("capturedAt"),
        "executablePriceSide": p.get("side"),
        "executablePriceSideBasis": p.get("sideBasis"),
        "executablePriceUnitDeclared": p.get("priceUnitDeclared"),
        "executablePriceGridDeclared": p.get("priceGridDeclared"),
        "quoteAgeSeconds": p.get("quoteAgeSeconds"),
        "maxQuoteAgeSeconds": p.get("maxQuoteAgeSeconds"),
        "quoteStale": p.get("stale"),
        "bookState": p.get("bookState"),
        "bookYesBid": pu.format_cents(book.get("yesBid")),
        "bookYesAsk": pu.format_cents(book.get("yesAsk")),
        "priceRefusalReason": (result or {}).get("refusalReason"),
    }


def unpriceable(reason, **provenance_overrides):
    """
    A refusal built without a book at all -- for candidates that fail before
    pricing is even attempted, e.g. the contract or the strike is unknown.
    Shaped identically to price_contract's return so callers have one type.
    """
    provenance = {
        "marketTicker": None, "eventTicker": None, "observationId": None,
        "capturedAt": None, "decidedAt": None, "source": None, "side": None,
        "sideBasis": None, "sideEvidence": None,
        "priceUnitDeclared": None, "priceGridDeclared": None,
        "quoteAgeSeconds": None, "maxQuoteAgeSeconds": MAX_QUOTE_AGE_SECONDS,
        "stale": None, "bookState": None, "priceBasis": None, "book": None,
    }
    provenance.update(provenance_overrides)
    return {"executablePriceCents": None, "executablePriceFloat": None,
            "actionable": False, "refusalReason": reason,
            "provenance": provenance}
