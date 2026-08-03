#!/usr/bin/env python3
"""
tests/edgelab/test_sport_platform_fields.py
================================================
Coverage for the Phase 2 Milestone 1 additive schema fields (sport,
platform -- docs/EDGELAB_PHASE2_DESIGN.md §2.1): backward compatibility
with every pre-existing record that never carried these fields at all,
and that every current writer sets them to today's only real values
without requiring a historical rewrite.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab import DEFAULT_PLATFORM, DEFAULT_SPORT, schema
from lib.edgelab.bets import build_manual_bet_record, from_legacy_root_bets_record, from_legacy_session_bets_record
from lib.edgelab.market_universe import build_game_records, build_market_records, build_observations_from_snapshot
from lib.edgelab.recommendations import extend_with_full_universe
from lib.edgelab.settlement import build_settlement_record

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "kalshi_search_sample.json")


def test_defaults_are_mlb_kalshi():
    assert DEFAULT_SPORT == "MLB"
    assert DEFAULT_PLATFORM == "KALSHI"


def test_record_without_sport_or_platform_at_all_still_validates():
    """The exact shape of every record committed before this change."""
    old_bet = {
        "schemaVersion": "1", "betId": "b1", "marketTicker": "T", "selection": "x", "stake": 1.0,
        "entryPrice": 0.5, "entryTimestamp": "2026-07-31T22:00:00Z", "source": "MANUAL", "status": "pending",
        "createdAt": "2026-07-31T22:00:00Z", "validationStatus": "valid",
        "provenance": {"sourceSystem": None, "sourceFile": None, "sourceKey": None, "capturedAt": None, "ingestedAt": None},
    }
    assert "sport" not in old_bet and "platform" not in old_bet
    assert schema.validate_record("placed_bet", old_bet) == []


def test_real_pre_phase2_bets_ledger_row_still_validates():
    """
    Direct proof against a REAL row as committed before this field
    existed (frozen here, not read live off data/edgelab/bets/bets.jsonl
    -- that file is re-ingested/enriched by later milestones, e.g. the
    Canonical Placed-Bet Ledger milestone's gameDate/entryMethod backfill,
    so it no longer stays in this pre-Phase-2 shape forever; the frozen
    copy is what actually proves backward compatibility without being
    coupled to what happens to be committed right now).
    """
    row = {
        "betId": "0bcf3db0c6b9d0309904617f4baf092a6bc5a92c",
        "closingPrice": None, "clv": None, "clvQuoteId": None, "confidence": "HIGH",
        "contracts": None, "correlationGroup": None, "createdAt": "2026-07-31T23:53:10Z",
        "dataQuality": None, "entryPrice": 0.505, "entryTimestamp": "2026-06-17T22:45:46.170900+00:00",
        "estimatedEdgeAtEntry": 7.028, "estimatedPayout": None, "eventTicker": None,
        "gameId": "2026-06-17_KC_WSH", "manualFairProbability": None, "marketFamily": "KXMLBTEAMTOTAL",
        "marketTicker": "KXMLBTEAMTOTAL-26JUN171305KCWSH-WSH3", "modelFairProbability": 78.06,
        "netProfitLoss": None,
        "provenance": {
            "capturedAt": "2026-06-17T22:45:46.170900+00:00", "ingestedAt": "2026-07-31T23:53:10Z",
            "sourceFile": "bets.json", "sourceKey": "2026-06-17-110", "sourceSystem": "bets_json",
        },
        "rationale": None, "recommendationId": None, "result": None, "returnAmount": None,
        "scheduledStart": "2026-06-17T17:06:00Z", "schemaVersion": "1", "selection": "TT_Home_Over WSH",
        "seriesTicker": None, "side": "YES", "source": "MODEL", "stake": 5.0, "status": "pending",
        "thesisTags": [], "threshold": 3, "trackingType": None, "updatedAt": "2026-07-31T23:53:10Z",
        "validationStatus": "valid",
    }
    assert "sport" not in row and "platform" not in row
    assert schema.validate_record("placed_bet", row) == []


def test_explicit_null_sport_platform_also_valid():
    rec = {
        "schemaVersion": "1", "betId": "b1", "marketTicker": "T", "selection": "x", "stake": 1.0,
        "entryPrice": 0.5, "entryTimestamp": "2026-07-31T22:00:00Z", "source": "MANUAL", "status": "pending",
        "createdAt": "2026-07-31T22:00:00Z", "validationStatus": "valid", "sport": None, "platform": None,
        "provenance": {"sourceSystem": None, "sourceFile": None, "sourceKey": None, "capturedAt": None, "ingestedAt": None},
    }
    assert schema.validate_record("placed_bet", rec) == []


def test_manual_bet_record_defaults_to_mlb_kalshi_but_is_overridable():
    default_bet = build_manual_bet_record("T", "sel", 1.0, 0.5, "2026-07-31T22:00:00Z")
    assert default_bet["sport"] == "MLB"
    assert default_bet["platform"] == "KALSHI"
    assert schema.validate_record("placed_bet", default_bet) == []

    overridden = build_manual_bet_record("T", "sel", 1.0, 0.5, "2026-07-31T22:00:00Z", sport="NFL", platform="OTHER_BOOK")
    assert overridden["sport"] == "NFL"
    assert overridden["platform"] == "OTHER_BOOK"
    assert schema.validate_record("placed_bet", overridden) == []


def test_legacy_bet_converters_set_mlb_kalshi():
    root_raw = {
        "date": "2026-07-31", "game": "PIT@CIN", "market": "F5_ML_Away", "ticker": "T",
        "betSize": 1.0, "actualEntryPrice": 0.5, "entryTimestamp": "2026-07-31T22:00:00Z",
    }
    rec1 = from_legacy_root_bets_record(root_raw, 0)
    assert rec1["sport"] == "MLB"
    assert rec1["platform"] == "KALSHI"
    assert schema.validate_record("placed_bet", rec1) == []

    session_raw = {
        "date": "2026-07-31", "game": "PIT@CIN", "market": "YRFI", "ticker": "T",
        "entryPrice": 52, "stake": 1.0, "timestamp": "2026-07-31T22:00:00Z",
    }
    rec2 = from_legacy_session_bets_record(session_raw, 0)
    assert rec2["sport"] == "MLB"
    assert rec2["platform"] == "KALSHI"
    assert schema.validate_record("placed_bet", rec2) == []


def test_market_universe_writers_set_mlb_kalshi():
    observations, _ = build_observations_from_snapshot(FIXTURE, "run1", game_context={})
    assert observations
    for obs in observations:
        assert obs["sport"] == "MLB"
        assert obs["platform"] == "KALSHI"
        assert schema.validate_record("market_observation", obs) == []

    games = build_game_records(observations, {})
    for g in games:
        assert g["sport"] == "MLB"
        assert g["platform"] == "KALSHI"
        assert schema.validate_record("game", g) == []

    markets = build_market_records(observations)
    for m in markets:
        assert m["sport"] == "MLB"
        assert m["platform"] == "KALSHI"
        assert schema.validate_record("market", m) == []


def test_recommendation_extension_writer_sets_mlb_kalshi():
    observations = [{
        "marketTicker": "T", "seriesTicker": "KXMLBF5", "gameId": "g1", "marketFamily": "inning_result",
        "runId": "obs-run", "provenance": {"sourceFile": "x", "sourceKey": "y", "capturedAt": "t", "sourceSystem": "s"},
    }]
    extra = extend_with_full_universe(covered_tickers=set(), observations=observations, model_covered_series=frozenset({"KXMLBF5"}), date="2026-07-31")
    assert extra[0]["sport"] == "MLB"
    assert extra[0]["platform"] == "KALSHI"
    assert schema.validate_record("recommendation", extra[0]) == []


def test_settlement_writer_sets_mlb_kalshi():
    rec = build_settlement_record(
        market_ticker="T", game_id="g1", market_family="game_result", settlement_status="SETTLED",
        result="YES", settlement_source="test", settled_at="2026-07-31T22:00:00Z",
    )
    assert rec["sport"] == "MLB"
    assert rec["platform"] == "KALSHI"
    assert schema.validate_record("settlement", rec) == []
