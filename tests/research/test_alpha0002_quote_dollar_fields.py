"""Regression: the ALPHA-0002 collector must read Kalshi's `*_dollars` market fields.

Evidence of the defect: every quote row persisted from 2026-09-02 to 2026-10-26 had null prices
(0 of 190,780) because quote_of() read only the legacy integer-cent fields."""
import importlib.util
import os

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_spec = importlib.util.spec_from_file_location(
    "alpha0002_prospective_capture", os.path.join(REPO, "scripts/research/mlb_alpha_0002/prospective_capture.py"))
cap = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cap)

DOLLAR_ERA = {"ticker": "KXMLBGAME-26OCT152000MILLAD-LAD", "event_ticker": "KXMLBGAME-26OCT152000MILLAD",
              "yes_bid_dollars": "0.4500", "yes_ask_dollars": "0.4700", "no_bid_dollars": "0.5300", "no_ask_dollars": "0.5500",
              "last_price_dollars": "0.4600", "volume_fp": "1234.50", "open_interest_fp": "999.00", "liquidity_dollars": "0.0000",
              "status": "active", "close_time": "2026-10-30T00:00:00Z"}
LEGACY = {"ticker": "T", "event_ticker": "E", "yes_bid": 45, "yes_ask": 47, "no_bid": 53, "no_ask": 55, "last_price": 46,
          "volume": 10, "open_interest": 7, "liquidity": 0, "status": "active"}


def test_dollar_era_fields_are_read_as_cents():
    q = cap.quote_of(DOLLAR_ERA)
    assert (q["yesBid"], q["yesAsk"], q["noBid"], q["noAsk"], q["lastPrice"]) == (45, 47, 53, 55, 46)
    assert q["priceUnitSource"] == "dollars"
    assert q["volume"] == 1234.5 and q["openInterest"] == 999.0


def test_legacy_cent_fields_still_work():
    q = cap.quote_of(LEGACY)
    assert (q["yesBid"], q["yesAsk"]) == (45, 47) and q["priceUnitSource"] == "cents"


def test_one_cent_is_one_cent_in_both_units():
    # the failure mode lib.edgelab.price_units warns about: magnitude must never decide the unit
    assert cap.quote_of({"ticker": "T", "yes_ask_dollars": "0.0100"})["yesAsk"] == 1
    assert cap.quote_of({"ticker": "T", "yes_ask": 1})["yesAsk"] == 1
    assert cap.quote_of({"ticker": "T", "yes_ask_dollars": "0.9900"})["yesAsk"] == 99


def test_zero_bid_is_kept_not_treated_as_absent():
    q = cap.quote_of({"ticker": "T", "yes_bid_dollars": "0.0000", "yes_bid": 55})
    assert q["yesBid"] == 0                       # an empty bid is a fact; it must not fall through to the other field


def test_missing_prices_stay_null_and_do_not_raise():
    q = cap.quote_of({"ticker": "T", "status": "active"})
    assert q["yesBid"] is None and q["yesAsk"] is None and q["priceUnitSource"] is None


def test_fingerprint_changes_when_price_appears():
    assert cap.fingerprint(cap.quote_of(DOLLAR_ERA)) != cap.fingerprint(cap.quote_of({"ticker": DOLLAR_ERA["ticker"]}))
