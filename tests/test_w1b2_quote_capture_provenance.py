#!/usr/bin/env python3
"""
tests/test_w1b2_quote_capture_provenance.py
===========================================
W1-B2, CEO review of PR #206, BLOCKERS 2, 4 and 5.

FRESHNESS LAUNDERING. B2's first cut propagated a REGISTRY-LEVEL snapshot
timestamp downstream and aged every quote against it. That is only safe when
the quote was genuinely captured at that instant, and one production path
guarantees it is not: scripts/build_kalshi_registry.py backfills missing and
null-priced ML / F5 / team-total markets from data/kalshi_search.json, a
document written by a separate earlier fetch. Rebuilding the registry at noon
stamped noon onto a quote captured at 10am, and the freshness gate -- the
whole point of which is to refuse quotes that have gone cold -- then saw a
ten-second-old price.

The container was refreshed. The price was not.

Every book must therefore carry the capture time of THAT PRICE SOURCE:
  * direct live Kalshi pull -> the pull's own capture instant
  * kalshi_search backfill  -> that market's own snapshot_ts, else the source
                               document's fetched_at
  * neither available       -> FAIL CLOSED, never the registry build time.

Also covered here: a quote timestamped AFTER the decision (negative age, which
the old `age > max_age` test scored as maximally fresh), and a book that does
not declare its price unit (which used to default silently to cents).
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from lib.edgelab import canonical_price as cp          # noqa: E402
from lib.edgelab import production_price as pp         # noqa: E402
from lib.kalshi_registry_market_builders import price_block  # noqa: E402

DECIDED = "2026-09-10T12:00:00Z"


def _price(**kw):
    args = dict(market_ticker="KXMLBGAME-26SEP102140BOSNYY-BOS",
                side=cp.SIDE_YES, yes_bid="0.44", yes_ask="0.46",
                unit=cp.UNIT_DOLLARS, decided_at=DECIDED)
    args.update(kw)
    return pp.price_contract(**args)


# ── BLOCKER 2: a rebuild cannot refresh someone else's quote ────────────────

def test_a_two_hour_old_source_quote_stays_two_hours_old():
    """
    THE laundering case, stated end to end.

    A quote captured at 10:00 is backfilled into a registry rebuilt at 12:00
    and priced at 12:00. Its age is two hours and it must be REFUSED as stale.
    If it ever reports ~0 again, a rebuild is refreshing prices it never
    observed.
    """
    result = _price(captured_at="2026-09-10T10:00:00Z")
    age = result["provenance"]["quoteAgeSeconds"]
    assert age == 7200.0, "the quote is two hours old, not %r" % age
    assert result["actionable"] is False
    assert result["refusalReason"] == pp.REFUSE_STALE


def test_the_registry_build_time_is_not_a_capture_time():
    """
    The same market, aged against the two candidate timestamps. Only one of
    them is a fact about the price; substituting the other flips the verdict
    from refused to actionable, which is exactly how much this distinction is
    worth.
    """
    honest = _price(captured_at="2026-09-10T10:00:00Z")
    laundered = _price(captured_at="2026-09-10T11:59:50Z")  # "registry rebuilt now"
    assert honest["actionable"] is False
    assert laundered["actionable"] is True
    assert honest["executablePriceCents"] == laundered["executablePriceCents"], (
        "same price, same book -- only the claimed capture time differs")


def test_a_quote_that_cannot_prove_its_age_fails_closed():
    """No capture time is not 'assume fresh'. It is a refusal."""
    result = _price(captured_at=None)
    assert result["actionable"] is False
    assert result["refusalReason"] == pp.REFUSE_NO_CAPTURE_TIME
    assert result["provenance"]["quoteAgeSeconds"] is None


def test_a_direct_quote_captured_seconds_ago_is_accepted():
    result = _price(captured_at="2026-09-10T11:59:30Z")
    assert result["actionable"] is True
    assert result["refusalReason"] is None
    assert result["provenance"]["quoteAgeSeconds"] == 30.0


# ── the capture time survives every hop of the transport ────────────────────

def test_price_block_records_the_capture_time_it_was_given():
    block = price_block({"yes_bid": 44, "yes_ask": 46, "status": "active"},
                        captured_at="2026-09-10T10:00:00Z")
    assert block["captured_at"] == "2026-09-10T10:00:00Z"
    assert block["unit"] == "dollars"


def test_price_block_with_no_provable_capture_time_says_so():
    """None, not a substituted 'now'. The refusal happens downstream, once,
    where the freshness rule lives."""
    block = price_block({"yes_bid": 44, "yes_ask": 46})
    assert block["captured_at"] is None


def test_merge_odds_book_carries_the_blocks_capture_time_not_the_registrys():
    """
    scripts/merge_odds.py's `_book()` is the hop where the registry timestamp
    used to be stamped on. It must copy the block's own value through, and it
    must always emit the key -- an absent key is how a downstream `or` chain
    would find its way back to the registry timestamp.
    """
    import re
    with open(os.path.join(ROOT, "scripts", "merge_odds.py")) as handle:
        source = handle.read()
    start = source.index("def _book(")
    body = source[start:source.index("\n    # W1-B2: the age of the book", start)]
    assert "'captured_at': pb.get('captured_at')" in body, (
        "_book must carry the price block's own capture time")
    assert not re.search(r"'captured_at':[^\n]*registry_snapshot_ts", body), (
        "_book must never stamp the registry's rebuild time onto a quote")


def test_the_ledger_seam_prefers_the_books_own_capture_time():
    """
    build_market_ledger.executable_price_for() is the last hop. When the book
    declares `captured_at`, that value wins outright -- including when it is
    None, which must refuse rather than fall back to the caller's
    registry-level snapshot_ts.
    """
    import build_market_ledger as bml
    honest = bml.executable_price_for(
        {"yes_bid": 0.44, "yes_ask": 0.46, "unit": "dollars",
         "captured_at": "2026-09-10T10:00:00Z"},
        cp.SIDE_YES, ticker="T", snapshot_ts="2026-09-10T11:59:55Z",
        decided_at=DECIDED)
    assert honest["provenance"]["quoteAgeSeconds"] == 7200.0, (
        "the registry-level snapshot_ts must not win over the book's own")
    assert honest["actionable"] is False

    unprovable = bml.executable_price_for(
        {"yes_bid": 0.44, "yes_ask": 0.46, "unit": "dollars",
         "captured_at": None},
        cp.SIDE_YES, ticker="T", snapshot_ts="2026-09-10T11:59:55Z",
        decided_at=DECIDED)
    assert unprovable["actionable"] is False
    assert unprovable["refusalReason"] == pp.REFUSE_NO_CAPTURE_TIME


def test_the_backfill_preserves_the_source_markets_own_timestamp():
    """
    scripts/build_kalshi_registry.py::backfill_from_search must read the
    kalshi_search market's own `snapshot_ts` (falling back to the document's
    `fetched_at`) and must not substitute SNAPSHOT_TS, which is when the
    registry script started.
    """
    with open(os.path.join(ROOT, "scripts", "build_kalshi_registry.py")) as handle:
        source = handle.read()
    start = source.index("    def price_from_market(m):")
    body = source[start:source.index("    backfilled_tt", start)]
    assert "m.get('snapshot_ts') or doc_fetched_at" in body, (
        "the backfill must preserve the source quote's own capture time")
    assert "'captured_at': captured_at" in body
    # Comments are stripped first: this block DESCRIBES the SNAPSHOT_TS defect
    # at length, and a scan that cannot tell prose from code would be
    # satisfied only by deleting the explanation.
    code = "\n".join(line for line in body.split("\n")
                     if not line.strip().startswith("#"))
    assert "SNAPSHOT_TS" not in code, (
        "the backfill must never stamp the registry build time on a quote")


# ── BLOCKER 4: a quote cannot be observed after the decision ────────────────

def test_a_future_dated_quote_is_refused():
    """
    Under `age > max_age` a capture time AFTER the decision produced a
    negative age, which is not greater than the ceiling, so the most
    suspicious timestamp possible scored as the freshest possible quote.
    """
    result = _price(captured_at="2026-09-10T12:05:00Z")
    assert result["actionable"] is False
    assert result["refusalReason"] == pp.REFUSE_FUTURE_QUOTE


def test_a_negative_age_is_reported_not_clamped():
    """Clamping to zero would hide the very discrepancy worth investigating."""
    result = _price(captured_at="2026-09-10T12:05:00Z")
    assert result["provenance"]["quoteAgeSeconds"] == -300.0


def test_the_freshness_boundaries_in_order():
    """age = 0 allowed; positive within the ceiling allowed; over refused;
    negative refused."""
    assert _price(captured_at=DECIDED)["actionable"] is True
    assert _price(captured_at="2026-09-10T11:31:00Z")["actionable"] is True
    over = _price(captured_at="2026-09-10T11:29:00Z")
    assert over["actionable"] is False and over["refusalReason"] == pp.REFUSE_STALE
    future = _price(captured_at="2026-09-10T12:00:01Z")
    assert future["actionable"] is False
    assert future["refusalReason"] == pp.REFUSE_FUTURE_QUOTE


def test_a_future_quote_refuses_even_when_freshness_is_not_required():
    """`require_fresh=False` relaxes STALENESS. It does not relax provenance:
    a capture time after the decision means we do not know when this price was
    true, which is a different and worse problem than being old."""
    result = _price(captured_at="2026-09-10T12:05:00Z", require_fresh=False)
    assert result["actionable"] is False
    assert result["refusalReason"] == pp.REFUSE_FUTURE_QUOTE


# ── BLOCKER 5: no implicit unit ─────────────────────────────────────────────

def test_a_book_that_does_not_declare_its_unit_is_refused():
    """
    The seam defaulted to cents. A dollars book read as cents understates
    every price by 100x, which makes every candidate look like a bargain --
    the single most dangerous direction for a pricing error to point.
    """
    result = pp.price_contract(
        market_ticker="T", side=cp.SIDE_YES, yes_bid="0.44", yes_ask="0.46",
        captured_at="2026-09-10T11:59:30Z", decided_at=DECIDED)
    assert result["actionable"] is False
    assert result["refusalReason"] == pp.REFUSE_UNIT_NOT_DECLARED


def test_an_unrecognised_unit_is_refused_separately():
    """'nobody said' and 'somebody said something wrong' are different
    diagnoses and get different reasons."""
    result = _price(unit="furlongs")
    assert result["actionable"] is False
    assert result["refusalReason"] == pp.REFUSE_UNIT_UNKNOWN


def test_both_declared_units_are_accepted_and_mean_different_things():
    fresh = "2026-09-10T11:59:30Z"
    dollars = _price(unit=cp.UNIT_DOLLARS, yes_ask="0.46", captured_at=fresh)
    cents = _price(unit=cp.UNIT_CENTS, yes_ask="46", captured_at=fresh)
    assert dollars["executablePriceFloat"] == cents["executablePriceFloat"] == 46.0
    mistaken = _price(unit=cp.UNIT_CENTS, yes_ask="0.46", captured_at=fresh)
    assert mistaken["executablePriceFloat"] == 0.46, (
        "0.46 CENTS is a sub-cent quote, not 46 cents -- which is why the "
        "unit may never be guessed")


def test_the_ledger_seam_passes_the_declared_unit_straight_through():
    """It must not map an unrecognised or absent declaration onto a default;
    production_price owns that decision."""
    import build_market_ledger as bml
    fresh = "2026-09-10T11:59:30Z"
    undeclared = bml.executable_price_for(
        {"yes_bid": 0.44, "yes_ask": 0.46, "captured_at": fresh},
        cp.SIDE_YES, ticker="T", decided_at=DECIDED)
    assert undeclared["refusalReason"] == pp.REFUSE_UNIT_NOT_DECLARED
    bogus = bml.executable_price_for(
        {"yes_bid": 0.44, "yes_ask": 0.46, "unit": "cents-ish",
         "captured_at": fresh},
        cp.SIDE_YES, ticker="T", decided_at=DECIDED)
    assert bogus["refusalReason"] == pp.REFUSE_UNIT_UNKNOWN


def test_norm_has_no_implicit_unit_left():
    """lib.kalshi_registry_market_builders.norm() assumed dollars when given
    neither a unit nor a field. The production executable path must have zero
    implicit unit defaults, so that call is now an error."""
    from lib.kalshi_registry_market_builders import norm
    assert norm(1, unit=cp.UNIT_CENTS) == 0.01
    assert norm(1, unit=cp.UNIT_DOLLARS) == 1.0
    assert norm(1, field="yes_bid") == 0.01
    with pytest.raises(TypeError):
        norm(1)
