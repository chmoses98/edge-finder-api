#!/usr/bin/env python3
"""
tests/edgelab/test_bets.py
==============================
Coverage for lib/edgelab/bets.py: manual bet logging, exact-ticker
requirement, multiple bets on one market, recommendation-linked bets,
unrecommended bets, stake/payout handling, legacy-ledger reconciliation.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab import schema, storage
from lib.edgelab.bets import (
    _classify_price_value,
    _normalize_kalshi_native_price,
    _normalize_session_bets_price,
    _odds_to_implied_probability,
    build_manual_bet_record,
    from_legacy_root_bets_record,
    from_legacy_session_bets_record,
    reconcile_with_existing,
)


def test_manual_bet_minimal_fields_are_sufficient():
    rec = build_manual_bet_record(
        "KXMLBF5-26JUL312140DETATH-DET", "DET F5 moneyline", 5.0, 0.505,
        "2026-07-31T22:38:09Z",
    )
    assert schema.validate_record("placed_bet", rec) == []
    assert rec["status"] == "pending"
    assert rec["source"] == "MANUAL"


def test_manual_bet_with_recommendation_link():
    rec = build_manual_bet_record(
        "KXMLBGAME-26JUL311810PITCIN-PIT", "PIT moneyline", 10.0, 0.51,
        "2026-07-31T20:00:00Z", source="MODEL", recommendation_id="rec-abc-123",
    )
    assert rec["recommendationId"] == "rec-abc-123"
    assert schema.validate_record("placed_bet", rec) == []


def test_unrecommended_manual_bet_has_no_recommendation_id():
    rec = build_manual_bet_record(
        "KXMLBTOTAL-26JUL311810PITCIN-T8", "Game total over 8", 3.0, 0.4,
        "2026-07-31T20:00:00Z",
    )
    assert rec["recommendationId"] is None
    assert schema.validate_record("placed_bet", rec) == []


def test_multiple_bets_on_one_market_get_distinct_ids(tmp_path):
    rec1 = build_manual_bet_record(
        "KXMLBTOTAL-26JUL311810PITCIN-T8", "Game total over 8, tranche 1", 3.0, 0.4,
        "2026-07-31T20:00:00Z",
    )
    rec2 = build_manual_bet_record(
        "KXMLBTOTAL-26JUL311810PITCIN-T8", "Game total over 8, tranche 2", 2.0, 0.42,
        "2026-07-31T20:05:00Z",
    )
    assert rec1["betId"] != rec2["betId"]
    path = str(tmp_path / "bets.jsonl")
    updated, inserted = storage.upsert_records(path, [rec1, rec2], "betId")
    assert inserted == 2
    assert updated == 0
    rows = list(storage.read_records(path))
    assert len(rows) == 2


def test_stake_and_payout_are_passthrough_never_computed():
    rec = build_manual_bet_record(
        "KXMLBF5-26JUL312140DETATH-DET", "DET F5 moneyline", 7.25, 0.505,
        "2026-07-31T22:38:09Z", estimated_payout=14.36,
    )
    assert rec["stake"] == 7.25
    assert rec["estimatedPayout"] == 14.36


def test_legacy_root_bets_record_requires_ticker_to_ingest():
    no_ticker = {"date": "2026-06-01", "game": "NYY@BOS", "market": "ML_Home", "betSize": 5}
    rec = from_legacy_root_bets_record(no_ticker, 0)
    assert rec["marketTicker"] is None  # caller must skip this record, never fabricate a ticker

    with_ticker = {
        "date": "2026-07-31", "game": "DET@ATH", "market": "F5_ML_Away",
        "ticker": "KXMLBF5-26JUL312140DETATH-DET", "betSize": 4.5,
        "actualEntryPrice": 0.505, "entryTimestamp": "2026-07-31T22:38:09Z",
        "modelProb": 63.62, "edgePct": 3.345, "confidenceTier": "MEDIUM",
        "createdBy": "write_pending_bets.py", "result": None,
    }
    rec2 = from_legacy_root_bets_record(with_ticker, 1)
    assert rec2["marketTicker"] == "KXMLBF5-26JUL312140DETATH-DET"
    assert rec2["source"] == "MODEL"
    assert rec2["status"] == "pending"
    assert schema.validate_record("placed_bet", rec2) == []


def test_legacy_session_bets_record_maps_tracking_type():
    raw = {
        "date": "2026-06-18", "game": "STL@KC", "market": "YRFI",
        "ticker": "KXMLBRFI-26JUN181940STLKC", "entryPrice": 52, "stake": 1.0,
        "type": "probe", "confidence": "PAPER", "status": "open", "result": None,
        "origin": "session_analysis", "timestamp": "2026-06-18T21:30:00Z",
    }
    rec = from_legacy_session_bets_record(raw, 0)
    assert rec["trackingType"] == "REAL_PROBE"
    assert rec["source"] == "MANUAL"
    assert rec["entryPrice"] == 0.52  # 52 cents normalized to a 0-1 fraction
    assert schema.validate_record("placed_bet", rec) == []


# ---------------------------------------------------------------------------
# PR #39 maintainer review: entryPrice normalization must be SOURCE-AWARE,
# not a bare numeric heuristic -- the same magnitude (e.g. -111) means
# something different depending on which field/source it came from, and a
# genuinely ambiguous value (e.g. a non-integer between 1 and 100) must be
# refused, never guessed. See _classify_price_value's docstring for the
# full contract; these tests exercise every case in that contract plus the
# two source-scoped wrappers built on it.
# ---------------------------------------------------------------------------

def test_odds_to_implied_probability_matches_standard_formula():
    assert _odds_to_implied_probability(100) == 0.5
    assert _odds_to_implied_probability(-100) == 0.5
    assert _odds_to_implied_probability(-135) == round(135 / 235, 4)
    assert _odds_to_implied_probability(135) == round(100 / 235, 4)


class TestClassifyPriceValueSourceAware:
    """Direct unit coverage of the low-level classifier -- the single source of truth for every normalization decision."""

    def test_valid_kalshi_fraction_always_accepted_regardless_of_source(self):
        for allow_odds in (True, False):
            assert _classify_price_value(0.53, allow_american_odds=allow_odds) == (0.53, "FRACTION", None)
            assert _classify_price_value(0.01, allow_american_odds=allow_odds) == (0.01, "FRACTION", None)
            assert _classify_price_value(0.99, allow_american_odds=allow_odds) == (0.99, "FRACTION", None)
            assert _classify_price_value(1.0, allow_american_odds=allow_odds) == (1.0, "FRACTION", None)

    def test_whole_number_kalshi_cents_always_accepted_regardless_of_source(self):
        for allow_odds in (True, False):
            assert _classify_price_value(53, allow_american_odds=allow_odds) == (0.53, "KALSHI_CENTS", None)
            assert _classify_price_value(99, allow_american_odds=allow_odds) == (0.99, "KALSHI_CENTS", None)
            assert _classify_price_value(2, allow_american_odds=allow_odds) == (0.02, "KALSHI_CENTS", None)

    def test_american_odds_only_resolved_when_source_declares_it(self):
        assert _classify_price_value(-111, allow_american_odds=True) == (0.5261, "AMERICAN_ODDS", -111.0)
        assert _classify_price_value(135, allow_american_odds=True)[1] == "AMERICAN_ODDS"
        # The SAME numeric value, from a source that hasn't earned that
        # latitude, is refused -- never silently reinterpreted as odds.
        assert _classify_price_value(-111, allow_american_odds=False) == (None, "AMBIGUOUS", None)
        assert _classify_price_value(135, allow_american_odds=False) == (None, "AMBIGUOUS", None)

    def test_odds_boundary_at_exactly_100(self):
        assert _classify_price_value(100, allow_american_odds=True) == (0.5, "AMERICAN_ODDS", 100.0)
        assert _classify_price_value(-100, allow_american_odds=True) == (0.5, "AMERICAN_ODDS", -100.0)

    def test_non_integer_value_between_1_and_100_is_always_ambiguous(self):
        """
        1.35 could be +135 American odds with a lost sign/digit, genuine
        decimal odds, or malformed Kalshi input -- there is no way to tell
        from the number alone. Must be refused (None), never guessed at
        either as cents or as odds, regardless of allow_american_odds.
        """
        for allow_odds in (True, False):
            assert _classify_price_value(1.35, allow_american_odds=allow_odds) == (None, "AMBIGUOUS", None)
            assert _classify_price_value(1.01, allow_american_odds=allow_odds) == (None, "AMBIGUOUS", None)

    def test_whole_number_2_00_is_unambiguous_cents_not_swept_into_the_ambiguous_bucket(self):
        """2.00 (a Python float) equals the whole number 2 -- unambiguous Kalshi cents (2 cents), not confused with the genuinely fractional 1.35/1.01 above."""
        assert _classify_price_value(2.00, allow_american_odds=True) == (0.02, "KALSHI_CENTS", None)

    def test_zero_is_ambiguous_not_a_valid_price(self):
        """0 is not >0 (the FRACTION branch requires 0 < v <= 1) and not a valid cents value -- refused, not silently treated as 0% probability."""
        assert _classify_price_value(0, allow_american_odds=True) == (None, "AMBIGUOUS", None)

    def test_negative_non_american_magnitude_is_ambiguous(self):
        """A negative value with |v| < 100 (e.g. -50) is not valid American odds (odds are always |v|>=100) and not a valid Kalshi price -- refused."""
        assert _classify_price_value(-50, allow_american_odds=True) == (None, "AMBIGUOUS", None)
        assert _classify_price_value(-1, allow_american_odds=True) == (None, "AMBIGUOUS", None)

    def test_null_is_malformed_never_guessed(self):
        assert _classify_price_value(None, allow_american_odds=True) == (None, "MALFORMED", None)

    def test_string_formatted_odds_are_parsed_as_numbers(self):
        """A numeric string (as legacy JSON sometimes stores) is parsed, not rejected outright -- but a non-numeric string is MALFORMED."""
        assert _classify_price_value("-111", allow_american_odds=True) == (0.5261, "AMERICAN_ODDS", -111.0)
        assert _classify_price_value("0.53", allow_american_odds=True) == (0.53, "FRACTION", None)
        assert _classify_price_value("Under 8.0 -107 [lowvig]", allow_american_odds=True) == (None, "MALFORMED", None)


def test_normalize_kalshi_native_price_never_interprets_american_odds():
    """
    root bets.json's actualEntryPrice/kalshiPrice/closingLine* fields are
    contractually pure Kalshi (verified against the real committed file --
    every value there is already a clean fraction) -- an odds-shaped value
    here is corruption of a differently-typed source, not a valid encoding,
    so it is refused exactly like any other ambiguous value, never guessed.
    """
    assert _normalize_kalshi_native_price(0.53) == 0.53
    assert _normalize_kalshi_native_price(53) == 0.53
    assert _normalize_kalshi_native_price(-111) is None
    assert _normalize_kalshi_native_price(135) is None
    assert _normalize_kalshi_native_price(1.35) is None
    assert _normalize_kalshi_native_price("Under 8.0 -107 [lowvig]") is None
    assert _normalize_kalshi_native_price(None) is None


def test_normalize_session_bets_price_resolves_american_odds():
    assert _normalize_session_bets_price(-111) == (0.5261, "AMERICAN_ODDS", -111.0)
    assert _normalize_session_bets_price(0.53) == (0.53, "FRACTION", None)
    assert _normalize_session_bets_price(52) == (0.52, "KALSHI_CENTS", None)
    assert _normalize_session_bets_price(1.35) == (None, "AMBIGUOUS", None)


def test_legacy_session_bets_record_normalizes_american_odds_and_preserves_raw_value():
    """
    Every one of these 13 (odds, expected_probability) pairs is one of the
    real 19 affected historical rows from data/bets.json (the other 6 are
    duplicates of the same odds value on a different game). Each is
    independently cross-validated against that SAME row's own clv/pl
    fields (computed by the original legacy system using the raw odds
    directly) reproducing exactly under EdgeLab's own CLV and
    realized-return formulas once entryPrice is corrected -- see PR #39's
    review notes for the full per-row verification. entryOdds preserves
    the original raw value -- never silently discarded once converted.
    """
    cases = [
        (-135, 0.5745), (-120, 0.5455), (-102, 0.505), (-167, 0.6255), (-111, 0.5261),
        (115, 0.4651), (135, 0.4255), (106, 0.4854), (130, 0.4348), (102, 0.495),
        (141, 0.4149), (217, 0.3155), (111, 0.4739),
        (100, 0.5), (-100, 0.5),  # boundary: American odds' own minimum magnitude
    ]
    for raw_odds, expected in cases:
        raw = {
            "date": "2026-06-12", "game": "MIA@PIT", "market": "ML",
            "ticker": "KXMLBGAME-TEST", "entryPrice": raw_odds, "stake": 5.0,
            "timestamp": "2026-06-12T20:00:00Z",
        }
        rec = from_legacy_session_bets_record(raw, 0)
        assert rec["entryPrice"] == expected, f"odds={raw_odds}"
        assert 0 < rec["entryPrice"] < 1
        assert rec["entryOdds"] == float(raw_odds)  # original raw value preserved, never lost
        assert rec["validationStatus"] == "valid"  # unambiguous -- not flagged
        assert schema.validate_record("placed_bet", rec) == []


def test_legacy_session_bets_record_kalshi_cents_unaffected_by_odds_fix():
    """Values in the ordinary Kalshi-cents range (1-99) must still divide by 100, exactly as before, with no entryOdds fabricated."""
    raw = {
        "date": "2026-06-12", "game": "MIA@PIT", "market": "ML",
        "ticker": "KXMLBGAME-TEST", "entryPrice": 52, "stake": 5.0,
        "timestamp": "2026-06-12T20:00:00Z",
    }
    rec = from_legacy_session_bets_record(raw, 0)
    assert rec["entryPrice"] == 0.52
    assert rec["entryOdds"] is None


def test_legacy_session_bets_record_ambiguous_entry_price_is_never_guessed():
    """
    A raw entryPrice of 1.35 -- if it ever occurred -- must NOT be
    silently converted (not as cents, not as odds): entryPrice ends up
    None, the row fails schema validation (entryPrice is required) and is
    excluded/warned by ingest_existing_bets.py rather than written with a
    guessed number, and validationStatus is flagged so a direct caller
    sees the ambiguity even outside that pipeline.
    """
    raw = {
        "date": "2026-06-12", "game": "MIA@PIT", "market": "ML",
        "ticker": "KXMLBGAME-TEST", "entryPrice": 1.35, "stake": 5.0,
        "timestamp": "2026-06-12T20:00:00Z",
    }
    rec = from_legacy_session_bets_record(raw, 0)
    assert rec["entryPrice"] is None
    assert rec["validationStatus"] == "warning"
    errors = schema.validate_record("placed_bet", rec)
    assert any("entryPrice" in e for e in errors)


def test_legacy_root_bets_record_never_reinterprets_odds_shaped_value_as_price():
    """
    root bets.json's actualEntryPrice is contractually a pure Kalshi price
    -- an odds-shaped value there is flagged as corruption (validationStatus
    warning, entryPrice None -> excluded by schema validation), never
    silently converted the way data/bets.json's entryPrice is.
    """
    raw = {
        "date": "2026-07-31", "game": "DET@ATH", "market": "F5_ML_Away",
        "ticker": "KXMLBF5-26JUL312140DETATH-DET", "betSize": 4.5,
        "actualEntryPrice": -111, "entryTimestamp": "2026-07-31T22:38:09Z",
    }
    rec = from_legacy_root_bets_record(raw, 0)
    assert rec["entryPrice"] is None
    assert rec["validationStatus"] == "warning"


def test_legacy_root_bets_record_ordinary_fraction_still_valid():
    raw = {
        "date": "2026-07-31", "game": "DET@ATH", "market": "F5_ML_Away",
        "ticker": "KXMLBF5-26JUL312140DETATH-DET", "betSize": 4.5,
        "actualEntryPrice": 0.505, "entryTimestamp": "2026-07-31T22:38:09Z",
    }
    rec = from_legacy_root_bets_record(raw, 0)
    assert rec["entryPrice"] == 0.505
    assert rec["validationStatus"] == "valid"


# ---------------------------------------------------------------------------
# Modern-entry regression: the canonical write path (build_manual_bet_record)
# never touches _classify_price_value / _normalize_*_price at all -- entryPrice
# is a direct passthrough, exactly as entered. These pin that down explicitly.
# ---------------------------------------------------------------------------

def test_modern_manual_entry_053_is_never_touched_by_legacy_normalization():
    rec = build_manual_bet_record("KXMLBF5-MODERN-TEST", "x", 25.0, 0.53, "2026-08-03T18:04:00Z")
    assert rec["entryPrice"] == 0.53
    assert schema.validate_record("placed_bet", rec) == []


def test_modern_manual_entry_boundary_prices_001_and_099():
    rec_low = build_manual_bet_record("KXMLBF5-MODERN-LOW", "x", 5.0, 0.01, "2026-08-03T18:04:00Z")
    rec_high = build_manual_bet_record("KXMLBF5-MODERN-HIGH", "x", 5.0, 0.99, "2026-08-03T18:04:00Z")
    assert rec_low["entryPrice"] == 0.01
    assert rec_high["entryPrice"] == 0.99
    assert schema.validate_record("placed_bet", rec_low) == []
    assert schema.validate_record("placed_bet", rec_high) == []


def test_modern_manual_entry_valid_second_tranche_unaffected():
    rec1 = build_manual_bet_record("KXMLBF5-MODERN-TRANCHE", "x", 25.0, 0.53, "2026-08-03T18:04:00Z")
    rec2 = build_manual_bet_record("KXMLBF5-MODERN-TRANCHE", "x", 10.0, 0.55, "2026-08-03T18:30:00Z")
    assert rec1["betId"] != rec2["betId"]
    assert rec1["entryPrice"] == 0.53
    assert rec2["entryPrice"] == 0.55


def test_legacy_nrfi_bet_is_no_side_yrfi_is_yes_side():
    nrfi_raw = {
        "date": "2026-06-18", "game": "STL@KC", "market": "NRFI",
        "ticker": "KXMLBRFI-26JUN181940STLKC", "entryPrice": 48, "stake": 1.0,
        "timestamp": "2026-06-18T21:30:00Z",
    }
    yrfi_raw = dict(nrfi_raw, market="YRFI", entryPrice=52)
    assert from_legacy_session_bets_record(nrfi_raw, 0)["side"] == "NO"
    assert from_legacy_session_bets_record(yrfi_raw, 0)["side"] == "YES"

    root_nrfi = {
        "date": "2026-06-18", "game": "STL@KC", "market": "NRFI",
        "ticker": "KXMLBRFI-26JUN181940STLKC", "betSize": 1.0,
        "actualEntryPrice": 0.48, "entryTimestamp": "2026-06-18T21:30:00Z",
    }
    assert from_legacy_root_bets_record(root_nrfi, 0)["side"] == "NO"


def test_reconcile_with_existing_is_a_true_noop_when_content_unchanged():
    raw = {
        "date": "2026-06-18", "game": "STL@KC", "market": "YRFI",
        "ticker": "KXMLBRFI-26JUN181940STLKC", "entryPrice": 52, "stake": 1.0,
        "timestamp": "2026-06-18T21:30:00Z",
    }
    first = from_legacy_session_bets_record(raw, 0)
    existing_by_id = {first["betId"]: first}
    second = from_legacy_session_bets_record(raw, 0)  # simulates a rerun later, fresh createdAt/updatedAt
    reconciled = reconcile_with_existing(second, existing_by_id)
    assert reconciled == first  # byte-identical: timestamps must not churn on an unchanged rerun


def test_reconcile_with_existing_preserves_created_at_on_real_change():
    raw = {
        "date": "2026-06-18", "game": "STL@KC", "market": "YRFI",
        "ticker": "KXMLBRFI-26JUN181940STLKC", "entryPrice": 52, "stake": 1.0,
        "timestamp": "2026-06-18T21:30:00Z", "result": None,
    }
    first = from_legacy_session_bets_record(raw, 0)
    existing_by_id = {first["betId"]: first}
    raw2 = dict(raw, result="WIN", pl=0.96)
    second = from_legacy_session_bets_record(raw2, 0)
    reconciled = reconcile_with_existing(second, existing_by_id)
    assert reconciled["result"] == "WIN"
    assert reconciled["createdAt"] == first["createdAt"]
    assert reconciled["updatedAt"] == second["updatedAt"]


def test_legacy_result_win_loss_push_void_mapped():
    for raw_result, expected_status in (("WIN", "settled"), ("LOSS", "settled"), ("PUSH", "settled"), ("VOID", "void"), (None, "pending")):
        raw = {
            "date": "2026-06-18", "game": "STL@KC", "market": "YRFI",
            "ticker": "KXMLBRFI-26JUN181940STLKC", "entryPrice": 52, "stake": 1.0,
            "result": raw_result, "timestamp": "2026-06-18T21:30:00Z",
        }
        rec = from_legacy_session_bets_record(raw, 0)
        assert rec["result"] == raw_result
        assert rec["status"] == expected_status
