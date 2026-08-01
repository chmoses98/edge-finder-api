#!/usr/bin/env python3
"""
tests/edgelab/test_market_universe.py
=========================================
Fixture-based coverage for lib/edgelab/market_universe.py: full eligible
market capture, no forbidden market leakage, dedup, immutable history.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab import schema, storage
from lib.edgelab.market_universe import (
    build_game_records,
    build_market_records,
    build_observations_from_snapshot,
)
from lib.kalshi_mlb_single_game_registry import detect_new_unclassified_mlb_series

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "kalshi_search_sample.json")


def _build():
    return build_observations_from_snapshot(FIXTURE, run_id="TEST_RUN_1", game_context={})


def test_full_eligible_market_capture():
    observations, excluded = _build()
    assert len(observations) == 30  # every legitimate market in the fixture, none dropped
    families = {o["marketFamily"] for o in observations}
    assert "game_result" in families
    assert "winning_margin" in families
    assert "hitter_hits" in families


def test_no_forbidden_market_leakage():
    observations, excluded = _build()
    tickers = {o["marketTicker"] for o in observations}
    assert "KXMLBALCY-26-JVERLANDER" not in tickers
    reasons = {e["exclusionReason"] for e in excluded}
    assert "FUTURES_OR_AWARD" in reasons


def test_new_unclassified_series_flagged_not_included():
    observations, excluded = _build()
    tickers = {o["marketTicker"] for o in observations}
    assert "KXMLBNEWFAM-26JUL311810PITCIN-PIT" not in tickers
    warnings = detect_new_unclassified_mlb_series(excluded)
    assert any(w["seriesTicker"] == "KXMLBNEWFAM" for w in warnings)


def test_every_observation_is_schema_valid():
    observations, _ = _build()
    for obs in observations:
        errors = schema.validate_record("market_observation", obs)
        assert errors == [], errors


def test_deterministic_ids_enable_dedup_across_reruns(tmp_path):
    observations, _ = _build()
    path = str(tmp_path / "observations.jsonl")
    written1, skipped1 = storage.append_records(path, observations, "marketObservationId")
    assert written1 == len(observations)
    assert skipped1 == 0

    # Re-running ingestion against the exact same snapshot must be a pure no-op.
    observations_again, _ = _build()
    written2, skipped2 = storage.append_records(path, observations_again, "marketObservationId")
    assert written2 == 0
    assert skipped2 == len(observations)


def test_multiple_same_day_snapshots_preserve_time_series(tmp_path):
    """A later snapshot with a moved price must add a new row, never overwrite the earlier one."""
    observations, _ = _build()
    one_ticker_obs = [o for o in observations if o["marketTicker"] == observations[0]["marketTicker"]]
    later = dict(one_ticker_obs[0])
    later["capturedAt"] = "2026-07-31T23:00:00.000Z"
    later["yesBid"] = (later["yesBid"] or 0) + 1
    from lib.edgelab.ids import build_market_observation_id
    later["marketObservationId"] = build_market_observation_id(later["marketTicker"], later["capturedAt"])

    path = str(tmp_path / "observations.jsonl")
    storage.append_records(path, [one_ticker_obs[0]], "marketObservationId")
    storage.append_records(path, [later], "marketObservationId")

    rows = [r for r in storage.read_records(path) if r["marketTicker"] == later["marketTicker"]]
    assert len(rows) == 2
    assert {r["capturedAt"] for r in rows} == {one_ticker_obs[0]["capturedAt"], later["capturedAt"]}


def test_raw_normalized_linkage():
    observations, _ = _build()
    for obs in observations:
        assert obs["provenance"]["sourceFile"] == FIXTURE
        assert obs["provenance"]["sourceKey"] == obs["marketTicker"]


def test_game_and_market_dimension_records_dedup_by_key():
    observations, _ = _build()
    games = build_game_records(observations, {})
    markets = build_market_records(observations)
    assert len(markets) == len(observations)  # every fixture market is a distinct ticker
    game_ids = [g["gameId"] for g in games]
    assert len(game_ids) == len(set(game_ids))
    for m in markets:
        assert schema.validate_record("market", m) == []
    for g in games:
        assert schema.validate_record("game", g) == []
