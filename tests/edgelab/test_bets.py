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
    build_manual_bet_record,
    from_legacy_root_bets_record,
    from_legacy_session_bets_record,
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
