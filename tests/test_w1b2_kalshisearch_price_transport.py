#!/usr/bin/env python3
"""
tests/test_w1b2_kalshisearch_price_transport.py
===============================================
W1-B2, CEO review of PR #206, BLOCKER 1.

api/kalshisearch.js is a REAL PRODUCTION PRICE INPUT, not a dashboard
convenience. The documented flow is:

    fetch-slate.yml
      -> api/kalshisearch.js
      -> data/kalshi_search.json
      -> scripts/build_kalshi_registry.py::backfill_from_search
      -> registry
      -> scripts/merge_odds.py
      -> scripts/build_market_ledger.py

and that backfill repairs missing/null ML, F5 and team-total prices, so a
number this endpoint emits can become the executable price a real-money
recommendation is gated on.

It carried the SAME magnitude heuristic B2 removed from the Python side:

    return isNaN(f) ? null : (f > 1.0 ? f / 100 : f);

which read a genuine 1-CENT quote as a $1.00 quote (1 is not > 1.0) and a
genuine HALF-CENT quote as $0.50. It also manufactured a midpoint for a
one-sided book (`mid = yesBid ?? yesAsk`), and that fabricated mid is the
first thing the Python backfill reads.

These tests drive the ACTUAL SHIPPED FUNCTIONS through node -- no network, no
serverless runtime, no reimplementation of the logic in Python. If the
assertions here and the file disagree, the file is what deploys, so the file
is what is tested.
"""
import json
import os
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENDPOINT = os.path.join(ROOT, "api", "kalshisearch.js")

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None,
    reason="node is not available in this environment",
)

SNAPSHOT_TS = "2026-09-10T18:00:00Z"


def _parse(raw_market):
    """Run api/kalshisearch.js's own parseMarketRecord over one raw market."""
    driver = (
        "import { parseMarketRecord } from %s;\n"
        "const raw = JSON.parse(process.env.W1B2_RAW_MARKET);\n"
        "process.stdout.write(JSON.stringify("
        "parseMarketRecord(raw, raw.event_ticker || '', %s)));\n"
        % (json.dumps(ENDPOINT), json.dumps(SNAPSHOT_TS))
    )
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", driver],
        capture_output=True, text=True, timeout=60,
        env=dict(os.environ, W1B2_RAW_MARKET=json.dumps(raw_market)),
    )
    assert proc.returncode == 0, "node failed: %s" % proc.stderr[-2000:]
    return json.loads(proc.stdout)


def _mkt(**fields):
    base = {"ticker": "KXMLBGAME-26SEP102140BOSNYY-BOS",
            "event_ticker": "KXMLBGAME-26SEP102140BOSNYY",
            "title": "Boston vs Yankees Winner?", "status": "active"}
    base.update(fields)
    return base


# ── units are declared, never inferred from magnitude ────────────────────────

def test_explicit_cents_fields_are_read_as_cents():
    """The bare Kalshi fields are CENTS. 44c/46c is $0.44/$0.46."""
    row = _parse(_mkt(yes_bid=44, yes_ask=46))
    assert row["yes_bid"] == 0.44
    assert row["yes_ask"] == 0.46
    assert row["unit"] == "dollars"
    assert row["price_source_fields"]["yes_bid"] == "yes_bid"


def test_explicit_dollar_fields_are_read_as_dollars():
    """The `*_dollars` fields are fixed-point dollar strings."""
    row = _parse(_mkt(yes_bid_dollars="0.4400", yes_ask_dollars="0.4600"))
    assert row["yes_bid"] == 0.44
    assert row["yes_ask"] == 0.46
    assert row["price_source_fields"]["yes_bid"] == "yes_bid_dollars"


def test_one_cent_never_becomes_one_dollar():
    """
    THE defect. `yes_bid: 1` is a ONE-CENT resting bid. Under `f > 1.0 ? f/100
    : f` the value 1 failed the test and stayed 1, i.e. $1.00 -- a hundredfold
    overstatement of a longshot's price, on precisely the contracts where a
    mispriced cent looks like an enormous edge.
    """
    row = _parse(_mkt(yes_bid=1, yes_ask=2))
    assert row["yes_bid"] == 0.01, "1 cent must be $0.01, never $1.00"
    assert row["yes_ask"] == 0.02


def test_one_cent_in_the_dollar_field_means_the_same_thing():
    """`yes_bid_dollars: "0.0100"` and `yes_bid: 1` are the same quote."""
    from_cents = _parse(_mkt(yes_bid=1, yes_ask=2))
    from_dollars = _parse(_mkt(yes_bid_dollars="0.0100", yes_ask_dollars="0.0200"))
    assert from_cents["yes_bid"] == from_dollars["yes_bid"] == 0.01
    assert from_cents["yes_ask"] == from_dollars["yes_ask"] == 0.02


def test_sub_cent_fixed_point_survives():
    """
    A half-cent quote on a deci-cent grid. The heuristic read 0.5 as $0.50;
    it is $0.005. Kalshi's wire format is 4dp dollars, so 0.01c resolution is
    representable and must not be rounded away or rescaled.
    """
    assert _parse(_mkt(yes_bid=0.5, yes_ask=0.6))["yes_bid"] == 0.005
    assert _parse(_mkt(yes_bid_dollars="0.0050"))["yes_bid"] == 0.005
    assert _parse(_mkt(yes_bid_dollars="0.0001"))["yes_bid"] == 0.0001


def test_ninety_nine_cents_is_not_rescaled_into_dollars():
    row = _parse(_mkt(yes_bid=99, yes_ask=100))
    assert row["yes_bid"] == 0.99


# ── zero is a real observation, not an absent one ────────────────────────────

def test_zero_does_not_fall_through_to_a_field_in_another_unit():
    """
    `yes_bid_dollars: 0` is a genuine "no bid at any price" observation. Under
    truthiness selection it is falsy, so the reader would fall through to the
    CENTS field and return a number in the wrong unit as well as the wrong
    value. Presence is tested with `!= null`.
    """
    row = _parse(_mkt(yes_bid_dollars=0, yes_bid=44, yes_ask=46))
    assert row["yes_bid"] == 0.0
    assert row["price_source_fields"]["yes_bid"] == "yes_bid_dollars"


def test_a_zero_cents_bid_stays_zero():
    row = _parse(_mkt(yes_bid=0, yes_ask=46))
    assert row["yes_bid"] == 0.0


def test_an_absent_field_stays_null_and_is_distinguishable_from_zero():
    row = _parse(_mkt(yes_ask=46))
    assert row["yes_bid"] is None
    assert row["price_source_fields"]["yes_bid"] is None


# ── one-sided books never manufacture a midpoint ─────────────────────────────

def test_ask_only_does_not_manufacture_a_midpoint():
    """
    `mid = yesBid ?? yesAsk` reported the ASK as the midpoint of an ask-only
    book, and implied_pct and american_odds were derived from it as though it
    were real. build_kalshi_registry.py's backfill reads `m.get('mid')` first,
    so the fabrication reached the registry.
    """
    row = _parse(_mkt(yes_ask=46))
    assert row["mid"] is None
    assert row["implied_pct"] is None
    assert row["american_odds"] is None
    assert row["book_state"] == "ASK_ONLY"
    assert row["yes_ask"] == 0.46, "the genuine ask is still reported"


def test_bid_only_does_not_manufacture_a_midpoint():
    row = _parse(_mkt(yes_bid=44))
    assert row["mid"] is None
    assert row["implied_pct"] is None
    assert row["american_odds"] is None
    assert row["book_state"] == "BID_ONLY"
    assert row["yes_bid"] == 0.44


def test_an_empty_book_stays_empty():
    row = _parse(_mkt())
    assert row["mid"] is None and row["book_state"] == "EMPTY"
    assert row["yes_bid"] is None and row["yes_ask"] is None


def test_a_zero_bid_is_not_a_second_side():
    """
    A resting bid of zero is the ABSENCE of a bid, so a book with a real ask
    and a zero bid is ask-only -- it has no midpoint. Treating zero as a side
    is how `(bid or 0 + ask) / 2` produced ask/2 and called it the price.
    """
    row = _parse(_mkt(yes_bid=0, yes_ask=46))
    assert row["book_state"] == "ASK_ONLY"
    assert row["mid"] is None
    assert row["mid"] != 0.23


def test_a_two_sided_book_still_has_its_midpoint():
    row = _parse(_mkt(yes_bid=44, yes_ask=46))
    assert row["book_state"] == "TWO_SIDED"
    assert row["mid"] == 0.45
    assert row["implied_pct"] == 45.0
    assert row["american_odds"] is not None


# ── the NO side is carried when the exchange supplies it ─────────────────────

def test_no_side_quotes_are_carried_through():
    """B2 derives the NO side from the YES bid, but a genuine NO quote is
    better evidence than a derivation, so it is transported when present."""
    row = _parse(_mkt(yes_bid=44, yes_ask=46, no_bid=54, no_ask=56))
    assert row["no_bid"] == 0.54
    assert row["no_ask"] == 0.56
    assert row["price_source_fields"]["no_ask"] == "no_ask"


# ── provenance travels with the price ────────────────────────────────────────

def test_every_quote_carries_its_capture_time_and_unit():
    """
    The capture time must travel WITH the quote. A registry rebuilt later must
    not be able to present this observation as a fresh one -- see the
    freshness-laundering tests in
    tests/test_w1b2_quote_capture_provenance.py.
    """
    row = _parse(_mkt(yes_bid=44, yes_ask=46))
    assert row["snapshot_ts"] == SNAPSHOT_TS
    assert row["unit"] == "dollars"


# ── the heuristic itself is gone from the file ───────────────────────────────

def test_no_magnitude_heuristic_survives_in_the_endpoint():
    """
    A source-level backstop for the behavioural tests above: the specific
    shape `> 1` / `<= 1.0` guarding a `/ 100` cannot reappear in this file
    without failing here.
    """
    import re
    with open(ENDPOINT) as handle:
        source = handle.read()
    body = "\n".join(
        line for line in source.split("\n")
        if not line.strip().startswith(("//", "*", "/*", "*/"))
    )
    assert _magnitude_heuristics(body) == [], (
        "a magnitude-based unit inference reappeared in api/kalshisearch.js: %r"
        % _magnitude_heuristics(body))


def _magnitude_heuristics(body):
    """
    The signature of the defect is a COMPARISON AGAINST 1 deciding a scale
    change. A bare `f / 100` is NOT the defect -- converting a field that was
    DECLARED to be in cents is exactly what the fix does -- so the scan looks
    for the comparison, not the division. Scanning for `/ 100` alone would
    flag the correct code and train someone to delete the guard.
    """
    import re
    return (re.findall(r"[<>]=?\s*1(?:\.0+)?\s*\)?\s*\?[^\n]*?/\s*100", body)
            + re.findall(r"/\s*100\s*[^\n]*?:\s*[^\n]*?[<>]=?\s*1(?:\.0+)?", body))


def test_the_heuristic_scan_would_actually_catch_the_old_code():
    """
    A positive control. A scan that cannot fail is worse than no scan -- it
    reports safety it never checked. Both historical spellings of the defect
    must still be detected, and the current correct conversion must not be.
    """
    # This scan guards a JavaScript file, so the controls are the JS
    # spellings. The Python spelling of the same defect is guarded separately
    # by test_no_magnitude_heuristic_survives_in_the_ledgers_pricing_code in
    # tests/test_w1b2_executable_price_cutover.py.
    assert _magnitude_heuristics(
        "return isNaN(f) ? null : (f > 1.0 ? f / 100 : f);"), "the exact old line"
    assert _magnitude_heuristics("const x = v > 1 ? v/100 : v;")
    assert _magnitude_heuristics("const x = v <= 1.0 ? v : v / 100;"), "both orders"
    assert _magnitude_heuristics("return unit === 'cents' ? f / 100 : f;") == [], (
        "the scan must not flag a conversion driven by a DECLARED unit")


# ── the backfill route, end to end: kalshisearch -> book -> executable price ──
#
# scripts/build_kalshi_registry.py::backfill_from_search builds its price block
# INSIDE backfill_from_search(), and importing that module executes a live
# Kalshi pull at import time, so it cannot be imported here. What can be tested
# without qualification is the boundary that matters: whether a market as this
# endpoint emits it produces a correct executable price when handed to
# production_price the way the backfilled book reaches it. That is the same
# journey a real backfilled quote makes, minus the file write.

from lib.edgelab import canonical_price as cp          # noqa: E402
from lib.edgelab import production_price as pp         # noqa: E402

_FRESH_DECISION = "2026-09-10T18:00:30Z"   # 30s after SNAPSHOT_TS


def _executable(raw_market, side, decided_at=_FRESH_DECISION):
    """Emit via the real JS, then price it exactly as the backfilled book is."""
    row = _parse(raw_market)
    return pp.price_contract(
        market_ticker=row["market_ticker"], side=side,
        yes_bid=row["yes_bid"], yes_ask=row["yes_ask"],
        no_bid=row["no_bid"], no_ask=row["no_ask"],
        unit=row["unit"], captured_at=row["snapshot_ts"], decided_at=decided_at,
    )


@pytest.mark.parametrize("state,yes_bid,yes_ask,side,actionable,price", [
    ("TWO_SIDED", 44, 46, cp.SIDE_YES, True, 46.0),
    ("TWO_SIDED", 44, 46, cp.SIDE_NO, True, 56.0),   # 100 - the genuine bid
    ("ASK_ONLY", None, 46, cp.SIDE_YES, True, 46.0),
    ("ASK_ONLY", None, 46, cp.SIDE_NO, False, None),
    ("BID_ONLY", 44, None, cp.SIDE_YES, False, None),
    ("BID_ONLY", 44, None, cp.SIDE_NO, True, 56.0),
    ("EMPTY", None, None, cp.SIDE_YES, False, None),
    ("EMPTY", None, None, cp.SIDE_NO, False, None),
    # A resting bid of ZERO is the absence of a bid, not a bid at zero.
    ("ZERO_BID", 0, 46, cp.SIDE_YES, True, 46.0),
    ("ZERO_BID", 0, 46, cp.SIDE_NO, False, None),
])
def test_one_sided_matrix_through_the_backfill_route(
        state, yes_bid, yes_ask, side, actionable, price):
    fields = {}
    if yes_bid is not None:
        fields["yes_bid"] = yes_bid
    if yes_ask is not None:
        fields["yes_ask"] = yes_ask
    result = _executable(_mkt(**fields), side)
    assert result["actionable"] is actionable, (
        "%s + BUY %s -> %r (%s)" % (state, side, result["actionable"],
                                    result["refusalReason"]))
    assert result["executablePriceFloat"] == price


def test_a_zero_bid_never_becomes_an_executable_no_at_one_hundred():
    """
    100 - 0 = 100, and a contract can never rest at 100c. The zero is the
    ABSENCE of a resting bid, so there is nothing to fill and no NO price to
    derive -- which is why the complement is taken only from a genuine
    POSITIVE bid.
    """
    result = _executable(_mkt(yes_bid=0, yes_ask=46), cp.SIDE_NO)
    assert result["executablePriceFloat"] != 100.0
    assert result["actionable"] is False


def test_a_one_sided_backfilled_book_never_produces_a_halved_midpoint():
    """
    CR-5, end to end on this route: `(bid or 0 + ask) / 2` turned an ask-only
    book into ask/2. The YES side of an ask-only book is the genuine ask, and
    the endpoint reports no midpoint at all for it.
    """
    row = _parse(_mkt(yes_ask=46))
    assert row["mid"] is None
    result = _executable(_mkt(yes_ask=46), cp.SIDE_YES)
    assert result["executablePriceFloat"] == 46.0
    assert result["executablePriceFloat"] != 23.0


def test_a_one_cent_backfilled_quote_prices_at_one_cent():
    """The transport defect, carried all the way to the executable price: a
    1-cent ask must cost 1 cent, not 100."""
    result = _executable(_mkt(yes_bid=1, yes_ask=2), cp.SIDE_YES)
    assert result["executablePriceFloat"] == 2.0
    assert result["actionable"] is True


def test_a_backfilled_quote_carries_its_own_age_not_the_registrys():
    """
    The freshness half, on this route. The market's snapshot_ts is the
    endpoint's live fetch instant; pricing it two hours later must yield a
    two-hour-old quote and a refusal, no matter when a registry containing it
    was rebuilt.
    """
    stale = _executable(_mkt(yes_bid=44, yes_ask=46), cp.SIDE_YES,
                        decided_at="2026-09-10T20:00:00Z")
    assert stale["provenance"]["quoteAgeSeconds"] == 7200.0
    assert stale["actionable"] is False
    assert stale["refusalReason"] == pp.REFUSE_STALE
