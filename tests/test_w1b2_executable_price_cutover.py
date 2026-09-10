#!/usr/bin/env python3
"""
tests/test_w1b2_executable_price_cutover.py
===========================================
WAVE 1, subwave B2. Guards the production executable-price cutover.

B2 is money-touching, so the tests that matter are the ones that prove
production CANNOT reach a price it has no right to. The single invariant:

    a candidate may be actionable ONLY IF the system can prove the contract,
    the side, a genuine executable price for that side, and the book evidence
    and age behind it.

Everything else is a refusal with a reason.
"""

import ast
import os
import sys
from decimal import Decimal

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from lib.edgelab import canonical_price as cp  # noqa: E402
from lib.edgelab import price_units as pu  # noqa: E402
from lib.edgelab import production_price as pp  # noqa: E402
from lib.kalshi_registry_market_builders import price_block, book_state  # noqa: E402

FRESH = "2026-09-10T01:30:00Z"
DECIDED = "2026-09-10T01:35:00Z"          # 5 minutes later
STALE_CAPTURE = "2026-09-10T00:00:00Z"    # 95 minutes before DECIDED


def _dollars_book(yes_bid=None, yes_ask=None, **kw):
    book = {"yes_bid": yes_bid, "yes_ask": yes_ask, "unit": "dollars"}
    book.update(kw)
    return book


def _price(side, **kw):
    from scripts_shim import executable_price_for  # noqa: F401
    raise AssertionError("unused")


# ── unit normalisation: the numeric-1 ambiguity ──────────────────────────────

def test_the_unit_comes_from_the_field_name_not_the_magnitude():
    """
    THE regression. Seven copies of `v if v <= 1.0 else v / 100.0` decided
    dollars-vs-cents from the SIZE of the number, so a genuine 1-cent quote
    became 100 cents and a $1.00 became 1 cent. One cent is where the longshots
    live, which is where a mispriced edge looks most attractive.
    """
    assert pu.to_cents(1, "cents") == 1
    assert pu.to_cents(1.0, "dollars") == 100
    assert pu.to_cents("0.0100", "dollars") == Decimal("1.0000")
    assert pu.to_cents("0.9900", "dollars") == Decimal("99.0000")


def test_explicit_dollars_and_cents_round_trip_exactly():
    assert pu.to_cents("0.2900", "dollars") == Decimal("29.0000")
    # 0.29 * 100 is 28.999999999999996 in binary float.
    assert pu.to_cents("0.2900", "dollars") - Decimal("29") == 0
    assert pu.to_dollars(29, "cents") == Decimal("0.29")


def test_an_unknown_field_refuses_rather_than_guessing():
    with pytest.raises(pu.UnknownPriceUnit):
        pu.unit_for_field("yes_bid_furlongs")


def test_zero_is_preserved_and_does_not_fall_through_to_another_unit():
    """
    `raw.get('yes_bid') or raw.get('yes_bid_dollars')` lost a genuine zero
    entirely, because zero is falsy. Presence is tested explicitly now.
    """
    assert pu.read_cents({"yes_bid": 0}, *pu.YES_BID_FIELDS) == (Decimal("0"), "yes_bid")
    assert pu.read_cents({}, *pu.YES_BID_FIELDS) == (None, None)
    # The declared preference for the richer dollars field is not a fall-through.
    value, field = pu.read_cents({"yes_bid_dollars": "0.0000", "yes_bid": 72},
                                 *pu.YES_BID_FIELDS)
    assert (value, field) == (Decimal("0.0000"), "yes_bid_dollars")


def test_sub_cent_quotes_survive_normalisation():
    assert pu.to_cents("0.0050", "dollars") == Decimal("0.5000")
    assert pu.to_cents("0.0010", "dollars") == Decimal("0.1000")


# ── the registry price block: one-sided books and grid metadata ──────────────

def test_a_one_sided_book_has_no_midpoint():
    """
    CR-5 at the registry. `mid = ((bid or 0) + (ask or 0)) / 2` turned an
    ABSENT bid into a numeric zero, so an ask-only book published ask/2 as the
    market's price -- and `american(mid)` carried it onward.
    """
    ask_only = price_block({"yes_bid_dollars": "0.0000", "yes_ask_dollars": "0.8100"})
    assert ask_only["book_state"] == "ASK_ONLY"
    assert ask_only["mid"] is None
    assert ask_only["implied_pct"] is None
    assert ask_only["american"] is None
    assert ask_only["mid"] != 0.405, "the halved midpoint must be unreachable"

    bid_only = price_block({"yes_bid_dollars": "0.4400"})
    assert (bid_only["book_state"], bid_only["mid"]) == ("BID_ONLY", None)
    empty = price_block({})
    assert (empty["book_state"], empty["mid"]) == ("EMPTY", None)


def test_a_two_sided_book_still_has_its_midpoint_as_context():
    two = price_block({"yes_bid_dollars": "0.4400", "yes_ask_dollars": "0.4600"})
    assert two["book_state"] == "TWO_SIDED"
    assert two["mid"] == 0.45
    assert two["yes_ask"] == 0.46, "the ask is carried, which is what B2 prices from"


def test_the_price_block_carries_the_grid_metadata_it_used_to_discard():
    block = price_block({
        "yes_bid_dollars": "0.4400", "yes_ask_dollars": "0.4600",
        "price_level_structure": "linear_cent",
        "price_ranges": [{"start": "0.0000", "end": "1.0000", "step": "0.0100"}],
    })
    assert block["price_level_structure"] == "linear_cent"
    assert block["price_ranges"][0]["step"] == "0.0100"


def test_book_state_treats_zero_as_absent():
    assert book_state(0, 0.81) == "ASK_ONLY"
    assert book_state(None, 0.81) == "ASK_ONLY"
    assert book_state(0.44, 0) == "BID_ONLY"
    assert book_state(0, 0) == "EMPTY"


# ── production_price: the four proofs ────────────────────────────────────────

def test_yes_uses_the_genuine_ask():
    r = pp.price_contract(market_ticker="T", side=cp.SIDE_YES,
                          yes_bid="0.4400", yes_ask="0.4600",
                          unit=cp.UNIT_DOLLARS, captured_at=FRESH, decided_at=DECIDED)
    assert r["actionable"] is True
    assert r["executablePriceCents"] == Decimal("46.0000")
    assert r["provenance"]["priceBasis"] == cp.BASIS_YES_ASK


def test_yes_refuses_when_the_ask_is_absent():
    r = pp.price_contract(market_ticker="T", side=cp.SIDE_YES,
                          yes_bid="0.4400", yes_ask=None,
                          unit=cp.UNIT_DOLLARS, captured_at=FRESH, decided_at=DECIDED)
    assert r["actionable"] is False
    assert r["executablePriceCents"] is None
    assert r["refusalReason"] == cp.NO_YES_ASK


def test_no_is_derived_from_the_yes_bid_never_from_the_yes_ask():
    r = pp.price_contract(market_ticker="T", side=cp.SIDE_NO,
                          yes_bid="0.4400", yes_ask="0.4600",
                          unit=cp.UNIT_DOLLARS, captured_at=FRESH, decided_at=DECIDED)
    assert r["executablePriceCents"] == Decimal("56.0000")   # 100 - 44
    assert r["executablePriceCents"] != Decimal("54")        # NOT 100 - 46
    assert r["provenance"]["priceBasis"] == cp.BASIS_DERIVED_NO_ASK_FROM_YES_BID


def test_no_prefers_a_genuine_no_ask_when_one_exists():
    r = pp.price_contract(market_ticker="T", side=cp.SIDE_NO,
                          yes_bid="0.4400", yes_ask="0.4600", no_ask="0.5500",
                          unit=cp.UNIT_DOLLARS, captured_at=FRESH, decided_at=DECIDED)
    assert r["executablePriceCents"] == Decimal("55.0000")
    assert r["provenance"]["priceBasis"] == cp.BASIS_NO_ASK


def test_a_zero_yes_bid_never_becomes_an_executable_no_at_one_hundred():
    for bid in (None, 0, "0.0000"):
        r = pp.price_contract(market_ticker="T", side=cp.SIDE_NO,
                              yes_bid=bid, yes_ask="0.8100",
                              unit=cp.UNIT_DOLLARS, captured_at=FRESH, decided_at=DECIDED)
        assert r["actionable"] is False
        assert r["executablePriceCents"] is None
        assert r["refusalReason"] == cp.NO_BID_FOR_NO_SIDE


def test_a_zero_yes_bid_never_halves_the_ask():
    r = pp.price_contract(market_ticker="T", side=cp.SIDE_YES,
                          yes_bid="0.0000", yes_ask="0.8100",
                          unit=cp.UNIT_DOLLARS, captured_at=FRESH, decided_at=DECIDED)
    assert r["executablePriceCents"] == Decimal("81.0000")
    assert r["executablePriceCents"] != Decimal("40.5")
    assert r["provenance"]["bookState"] == cp.BOOK_ASK_ONLY


@pytest.mark.parametrize("state,side,expect_price", [
    ("ASK_ONLY", cp.SIDE_YES, True),    # genuine ask
    ("ASK_ONLY", cp.SIDE_NO, False),    # no resting bid to complement
    ("BID_ONLY", cp.SIDE_YES, False),   # no ask to buy at
    ("BID_ONLY", cp.SIDE_NO, True),     # 100 - genuine bid
    ("EMPTY", cp.SIDE_YES, False),
    ("EMPTY", cp.SIDE_NO, False),
])
def test_one_sided_book_matrix(state, side, expect_price):
    books = {"ASK_ONLY": (None, "0.8100"), "BID_ONLY": ("0.4400", None),
             "EMPTY": (None, None)}
    bid, ask = books[state]
    r = pp.price_contract(market_ticker="T", side=side, yes_bid=bid, yes_ask=ask,
                          unit=cp.UNIT_DOLLARS, captured_at=FRESH, decided_at=DECIDED)
    assert r["actionable"] is expect_price, (state, side, r["refusalReason"])


def test_an_empty_book_refuses_both_sides_with_the_book_reason():
    for side in (cp.SIDE_YES, cp.SIDE_NO):
        r = pp.price_contract(market_ticker="T", side=side, yes_bid=None, yes_ask=None,
                              unit=cp.UNIT_DOLLARS, captured_at=FRESH, decided_at=DECIDED)
        assert r["refusalReason"] == pp.REFUSE_NO_BOOK


def test_a_sub_cent_quote_is_executable_and_exact():
    r = pp.price_contract(market_ticker="T", side=cp.SIDE_YES,
                          yes_bid="0.0010", yes_ask="0.0050",
                          unit=cp.UNIT_DOLLARS, captured_at=FRESH, decided_at=DECIDED)
    assert r["actionable"] is True
    assert r["executablePriceCents"] == Decimal("0.5000")


# ── the four proofs, each refusing on its own ────────────────────────────────

def test_an_unidentified_contract_refuses():
    r = pp.price_contract(market_ticker=None, side=cp.SIDE_YES, yes_bid=44, yes_ask=46,
                          captured_at=FRESH, decided_at=DECIDED)
    assert r["refusalReason"] == pp.REFUSE_NO_CONTRACT


def test_an_unproven_side_refuses_and_is_never_defaulted_to_yes():
    for side in (None, "", "MAYBE", "yes "):
        r = pp.price_contract(market_ticker="T", side=side, yes_bid=44, yes_ask=46,
                              captured_at=FRESH, decided_at=DECIDED)
        assert r["actionable"] is False
        assert r["refusalReason"] == pp.REFUSE_NO_SIDE
        assert r["executablePriceCents"] is None


def test_a_quote_with_no_knowable_age_refuses():
    """Proving a price without knowing when it was true is not proving it."""
    r = pp.price_contract(market_ticker="T", side=cp.SIDE_YES, yes_bid=44, yes_ask=46,
                          captured_at=None, decided_at=DECIDED)
    assert r["actionable"] is False
    assert r["refusalReason"] == pp.REFUSE_NO_CAPTURE_TIME


# ── freshness ────────────────────────────────────────────────────────────────

def test_a_stale_quote_is_reported_but_cannot_be_actionable():
    r = pp.price_contract(market_ticker="T", side=cp.SIDE_YES,
                          yes_bid="0.4400", yes_ask="0.4600", unit=cp.UNIT_DOLLARS,
                          captured_at=STALE_CAPTURE, decided_at=DECIDED)
    assert r["executablePriceCents"] == Decimal("46.0000"), "still reported for research"
    assert r["actionable"] is False
    assert r["refusalReason"] == pp.REFUSE_STALE
    assert r["provenance"]["stale"] is True
    assert r["provenance"]["quoteAgeSeconds"] == 5700


def test_a_fresh_quote_inside_the_window_is_actionable():
    r = pp.price_contract(market_ticker="T", side=cp.SIDE_YES,
                          yes_bid="0.4400", yes_ask="0.4600", unit=cp.UNIT_DOLLARS,
                          captured_at=FRESH, decided_at=DECIDED)
    assert r["actionable"] is True
    assert r["provenance"]["quoteAgeSeconds"] == 300
    assert r["provenance"]["stale"] is False


def test_the_freshness_ceiling_matches_the_measured_capture_reality():
    """
    Not a round number chosen for comfort. The authoritative path
    (fetch-slate.yml) fetches the book synchronously in the same job that
    writes bets.json, so its true age is seconds; the prospective path re-runs
    every 15 minutes WITHOUT refreshing Kalshi and can reach ~4h. 1800s sits
    well above the former and well below the latter.
    """
    assert pp.MAX_QUOTE_AGE_SECONDS == 1800


# ── the legacy fallbacks are gone ────────────────────────────────────────────

def _ledger_source():
    with open(os.path.join(ROOT, "scripts", "build_market_ledger.py")) as fh:
        return fh.read()


def test_build_edge_fields_no_longer_falls_back_to_the_vig_free_midpoint():
    """
    The single `else kalshi_vf` is what made `executablePriceUsed` a lie. It is
    asserted gone by BEHAVIOUR, not by reading the source.
    """
    import importlib
    bml = importlib.import_module("scripts.build_market_ledger")
    fields = bml.build_edge_fields(0.55, 0.50, None, bml.CAL_MEDIUM)
    assert fields["executablePriceUsed"] is None
    assert fields["executableMarketProb"] is None, "no midpoint substitution"
    assert fields["netExecutableEdge"] is None, "no edge without an executable price"
    assert fields["marketProbVF"] == 50.0, "the midpoint survives as CONTEXT only"


def test_an_executable_price_still_produces_a_full_edge():
    import importlib
    bml = importlib.import_module("scripts.build_market_ledger")
    fields = bml.build_edge_fields(0.55, 0.50, 46.0, bml.CAL_MEDIUM)
    assert fields["executablePriceUsed"] == 46.0
    assert fields["executableMarketProb"] == 46.0
    assert fields["netExecutableEdge"] is not None


def test_the_midpoint_derived_helper_is_quarantined_behind_an_honest_name():
    import importlib
    bml = importlib.import_module("scripts.build_market_ledger")
    assert not hasattr(bml, "american_to_ask_cents")
    assert hasattr(bml, "midpoint_derived_display_price_cents")


def test_no_magnitude_heuristic_survives_in_the_ledgers_pricing_code():
    """
    The literal shape of the seven heuristics, absent from the executable path.
    Comments are stripped first -- the file legitimately DESCRIBES the defect it
    removed, and scanning prose would flag the very sentence that documents it.
    """
    tree = ast.parse(_ledger_source())
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef)) and \
                node.body and isinstance(node.body[0], ast.Expr) and \
                isinstance(node.body[0].value, ast.Constant) and \
                isinstance(node.body[0].value.value, str):
            node.body.pop(0)
    source = ast.unparse(tree)
    assert "f * 100 if f <= 1.0" not in source
    assert "f if f <= 1.0" not in source
    assert "(bid or 0)" not in source


def test_production_pricing_modules_contain_no_qualification_logic():
    for name in ("price_units.py", "production_price.py"):
        path = os.path.join(ROOT, "lib", "edgelab", name)
        tree = ast.parse(open(path).read())
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef)) and \
                    node.body and isinstance(node.body[0], ast.Expr) and \
                    isinstance(node.body[0].value, ast.Constant) and \
                    isinstance(node.body[0].value.value, str):
                node.body.pop(0)
        source = ast.unparse(tree)
        for forbidden in ("confidence", "betUpTo", "bet_up_to", "stake",
                          "bankroll", "kelly", "threshold"):
            assert forbidden not in source, (name, forbidden)


# ── away / home separation ───────────────────────────────────────────────────

def test_away_and_home_are_priced_from_their_own_books():
    """
    Measured live: HOU 1c/2c against PHI 98c/99c on the same game. Before B2
    both sides fell back to the SAME key, so plumbing the book through without
    fixing that would have priced one contract from the other's quote.
    """
    away = pp.price_contract(market_ticker="KXMLBGAME-X-HOU", side=cp.SIDE_YES,
                             yes_bid="0.0100", yes_ask="0.0200", unit=cp.UNIT_DOLLARS,
                             captured_at=FRESH, decided_at=DECIDED)
    home = pp.price_contract(market_ticker="KXMLBGAME-X-PHI", side=cp.SIDE_YES,
                             yes_bid="0.9800", yes_ask="0.9900", unit=cp.UNIT_DOLLARS,
                             captured_at=FRESH, decided_at=DECIDED)
    assert away["executablePriceCents"] == Decimal("2.0000")
    assert home["executablePriceCents"] == Decimal("99.0000")
    assert away["executablePriceCents"] != home["executablePriceCents"]
    assert away["provenance"]["marketTicker"] != home["provenance"]["marketTicker"]


def test_swapping_the_books_changes_the_answer():
    """Regression: proves the test above is actually book-sensitive."""
    swapped = pp.price_contract(market_ticker="KXMLBGAME-X-HOU", side=cp.SIDE_YES,
                                yes_bid="0.9800", yes_ask="0.9900",
                                unit=cp.UNIT_DOLLARS, captured_at=FRESH, decided_at=DECIDED)
    assert swapped["executablePriceCents"] == Decimal("99.0000")


def test_a_missing_sibling_quote_cannot_contaminate_the_other_side():
    away = pp.price_contract(market_ticker="KXMLBGAME-X-HOU", side=cp.SIDE_YES,
                             yes_bid="0.0100", yes_ask=None, unit=cp.UNIT_DOLLARS,
                             captured_at=FRESH, decided_at=DECIDED)
    home = pp.price_contract(market_ticker="KXMLBGAME-X-PHI", side=cp.SIDE_YES,
                             yes_bid="0.9800", yes_ask="0.9900", unit=cp.UNIT_DOLLARS,
                             captured_at=FRESH, decided_at=DECIDED)
    assert away["actionable"] is False and away["executablePriceCents"] is None
    assert home["actionable"] is True and home["executablePriceCents"] == Decimal("99.0000")


# ── NRFI / YRFI: the side inversion class ────────────────────────────────────

def test_yrfi_and_nrfi_price_opposite_ends_of_the_same_book():
    """
    One contract, whose YES is "a run scores in the 1st". YRFI buys YES at the
    ask; NRFI buys NO at 100 - the genuine bid. B1 caught the inverse of this,
    where NRFI was priced at the YES ask -- on a ~60c contract, a ~20c error in
    the wrong direction.
    """
    book = dict(yes_bid="0.5800", yes_ask="0.6100", unit=cp.UNIT_DOLLARS)
    yrfi = pp.price_contract(market_ticker="KXMLBRFI-X", side=cp.SIDE_YES,
                             captured_at=FRESH, decided_at=DECIDED, **book)
    nrfi = pp.price_contract(market_ticker="KXMLBRFI-X", side=cp.SIDE_NO,
                             captured_at=FRESH, decided_at=DECIDED, **book)
    assert yrfi["executablePriceCents"] == Decimal("61.0000")
    assert nrfi["executablePriceCents"] == Decimal("42.0000")     # 100 - 58
    assert nrfi["executablePriceCents"] != yrfi["executablePriceCents"]
    # Not a complementary pair: ask and 100-bid, never a midpoint reflection.
    assert (yrfi["executablePriceCents"] + nrfi["executablePriceCents"]) != 100


# ── fees applied exactly once ────────────────────────────────────────────────

def test_fees_are_applied_exactly_once_between_ask_and_net_edge():
    """
    raw ask -> canonical executable price -> fee engine -> netExecutableEdge,
    with a worked example. The fee must shift the BREAK-EVEN once, and must not
    alter the displayed raw market price.
    """
    import importlib
    from lib.edgelab import kalshi_fees as kf
    bml = importlib.import_module("scripts.build_market_ledger")

    ask_cents, model_prob, vf = 46.0, 0.55, 0.50
    fields = bml.build_edge_fields(model_prob, vf, ask_cents, bml.CAL_MEDIUM,
                                   series_ticker='KXMLBGAME')

    # The raw price is untouched by the fee calculation.
    assert fields["executablePriceUsed"] == 46.0
    assert fields["executableMarketProb"] == 46.0

    exec_prob = 0.46
    expected_be = kf.fee_adjusted_break_even_probability(
        exec_prob, fee_type=kf.FEE_TYPE_TAKER, multiplier=fields["feeMultiplier"])
    expected_drag = round((expected_be - exec_prob) * 100, 3)
    assert fields["expectedFeeDrag"] == expected_drag

    gross = fields["rawEdgeVsExecutable"]          # 55 - 46 = 9.0
    assert gross == pytest.approx(9.0, abs=1e-6)
    # Exactly once: net raw = gross - drag, and the calibration factor is
    # applied to that, not a second fee.
    assert fields["netRawExecutableEdge"] == pytest.approx(gross - expected_drag, abs=1e-6)
    assert fields["netExecutableEdge"] == pytest.approx(
        round((gross - expected_drag) * bml.CAL_MEDIUM, 3), abs=1e-3)
    # And the gross calibrated edge carries NO fee at all.
    assert fields["calibratedEdgeVsExecutable"] == pytest.approx(
        round(gross * bml.CAL_MEDIUM, 3), abs=1e-3)


def test_no_executable_price_means_no_fee_and_no_edge():
    import importlib
    bml = importlib.import_module("scripts.build_market_ledger")
    fields = bml.build_edge_fields(0.55, 0.50, None, bml.CAL_MEDIUM,
                                   series_ticker='KXMLBGAME')
    assert fields["expectedFeeDrag"] is None
    assert fields["netExecutableEdge"] is None
    assert fields["feeAdjustedBreakEvenProbability"] is None


# ── provenance is always emitted ─────────────────────────────────────────────

def test_every_priced_row_carries_its_book_provenance():
    r = pp.price_contract(market_ticker="KXMLBGAME-X-HOU", side=cp.SIDE_YES,
                          yes_bid="0.4400", yes_ask="0.4600", unit=cp.UNIT_DOLLARS,
                          grid="linear_cent", captured_at=FRESH, decided_at=DECIDED,
                          event_ticker="KXMLBGAME-X", source="kalshi_registry.ml.away")
    flat = pp.edge_field_provenance(r)
    assert flat["executablePriceBasis"] == cp.BASIS_YES_ASK
    assert flat["executablePriceMarketTicker"] == "KXMLBGAME-X-HOU"
    assert flat["executablePriceSide"] == cp.SIDE_YES
    assert flat["executablePriceCapturedAt"] == FRESH
    assert flat["quoteAgeSeconds"] == 300
    assert flat["quoteStale"] is False
    assert flat["bookState"] == cp.BOOK_TWO_SIDED
    assert flat["bookYesAsk"] == "46"
    assert flat["executablePriceGridDeclared"] == "linear_cent"
    assert flat["priceRefusalReason"] is None


def test_a_refusal_also_carries_provenance_and_names_its_reason():
    r = pp.price_contract(market_ticker="T", side=cp.SIDE_YES, yes_bid="0.4400",
                          yes_ask=None, unit=cp.UNIT_DOLLARS,
                          captured_at=FRESH, decided_at=DECIDED)
    flat = pp.edge_field_provenance(r)
    assert flat["priceRefusalReason"] == cp.NO_YES_ASK
    assert flat["bookState"] == cp.BOOK_BID_ONLY
    assert flat["executablePriceBasis"] is None


def test_unpriceable_shapes_like_a_priced_result():
    r = pp.unpriceable(pp.REFUSE_NO_CONTRACT)
    assert set(r) == {"executablePriceCents", "executablePriceFloat",
                      "actionable", "refusalReason", "provenance"}
    assert r["actionable"] is False
