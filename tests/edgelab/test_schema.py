#!/usr/bin/env python3
"""
tests/edgelab/test_schema.py
================================
Coverage for lib/edgelab/schema.py + the schema_v1 JSON Schema files
themselves: versioning, stable IDs, required fields, missing optional
fields, and migration/additive-field compatibility.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab import SCHEMA_VERSION, schema
from lib.edgelab.ids import build_bet_id, build_market_observation_id, build_recommendation_id, build_settlement_id

VALID_OBSERVATION = {
    "schemaVersion": "1", "marketObservationId": "x", "runId": "r", "capturedAt": "2026-07-31T22:00:00Z",
    "marketTicker": "T", "eventTicker": "E", "seriesTicker": "S", "marketFamily": "game_result",
    "validationStatus": "valid", "parserStatus": "parsed", "createdAt": "2026-07-31T22:00:00Z",
    "source": "test", "provenance": {"sourceSystem": None, "sourceFile": None, "sourceKey": None, "capturedAt": None, "ingestedAt": None},
}


def test_schema_version_constant_is_1():
    assert SCHEMA_VERSION == "1"


def test_valid_record_has_no_errors():
    assert schema.validate_record("market_observation", VALID_OBSERVATION) == []


def test_missing_required_field_is_an_error():
    bad = dict(VALID_OBSERVATION)
    del bad["marketTicker"]
    errors = schema.validate_record("market_observation", bad)
    assert any("marketTicker" in e for e in errors)


def test_required_field_present_but_none_is_still_an_error():
    bad = dict(VALID_OBSERVATION, marketTicker=None)
    errors = schema.validate_record("market_observation", bad)
    assert any("marketTicker" in e for e in errors)


def test_missing_optional_field_is_not_an_error():
    # gameId is optional on market_observation -- a record simply omitting it must validate cleanly.
    ok = dict(VALID_OBSERVATION)
    assert "gameId" not in ok
    assert schema.validate_record("market_observation", ok) == []


def test_unknown_field_is_rejected():
    bad = dict(VALID_OBSERVATION, notARealField="oops")
    errors = schema.validate_record("market_observation", bad)
    assert any("notARealField" in e for e in errors)


def test_enum_violation_is_rejected():
    bad = dict(VALID_OBSERVATION, validationStatus="not_a_real_status")
    errors = schema.validate_record("market_observation", bad)
    assert any("validationStatus" in e for e in errors)


def test_recommendation_marker_ticker_optional_for_not_evaluated_rows():
    """A market the pipeline couldn't map to any ticker at all must still validate (Phase 1 section G)."""
    rec = {
        "schemaVersion": "1", "recommendationId": "x", "runId": "r", "status": "NOT_EVALUATED",
        "createdAt": "2026-07-31T22:00:00Z", "source": "test", "validationStatus": "valid",
        "provenance": {"sourceSystem": None, "sourceFile": None, "sourceKey": None, "capturedAt": None, "ingestedAt": None},
    }
    assert schema.validate_record("recommendation", rec) == []


def test_placed_bet_requires_market_ticker():
    bet = {
        "schemaVersion": "1", "betId": "b1", "marketTicker": None, "selection": "x", "stake": 1.0,
        "entryPrice": 0.5, "entryTimestamp": "2026-07-31T22:00:00Z", "source": "MANUAL", "status": "pending",
        "createdAt": "2026-07-31T22:00:00Z", "validationStatus": "valid",
        "provenance": {"sourceSystem": None, "sourceFile": None, "sourceKey": None, "capturedAt": None, "ingestedAt": None},
    }
    errors = schema.validate_record("placed_bet", bet)
    assert any("marketTicker" in e for e in errors)


def test_load_schema_rejects_unknown_entity():
    try:
        schema.load_schema("not_a_real_entity")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_all_nine_entities_load_and_are_valid_json_schema_documents():
    for entity in ("game", "market", "market_observation", "model_evaluation", "recommendation",
                   "placed_bet", "clv_quote", "settlement", "research_run"):
        doc = schema.load_schema(entity)
        assert doc["type"] == "object"
        assert "properties" in doc
        assert "schemaVersion" in doc["required"]


def test_ids_are_deterministic_for_the_same_inputs():
    a = build_market_observation_id("TICKER", "2026-07-31T22:00:00Z")
    b = build_market_observation_id("TICKER", "2026-07-31T22:00:00Z")
    assert a == b
    c = build_market_observation_id("TICKER", "2026-07-31T22:05:00Z")
    assert a != c


def test_ids_differ_by_entity_type_even_with_identical_string_inputs():
    """recommendationId and settlementId must not collide just because they hash the same underlying strings."""
    rec_id = build_recommendation_id("run1", "TICKER")
    settle_id = build_settlement_id("run1", "TICKER")
    assert rec_id != settle_id


def test_bet_id_deterministic_when_derivable_else_unique_fallback():
    a = build_bet_id("g1", "TICKER", "2026-07-31T22:00:00Z")
    b = build_bet_id("g1", "TICKER", "2026-07-31T22:00:00Z")
    assert a == b
    fallback1 = build_bet_id(None, None, None)
    fallback2 = build_bet_id(None, None, None)
    assert fallback1 != fallback2  # no derivable inputs -> unique token each time, never a collision
