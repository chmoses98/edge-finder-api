#!/usr/bin/env python3
"""tests/edgelab/test_probe_starter_identity_pit_safety.py"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from scripts.edgelab.backtest.probe_starter_identity_pit_safety import (
    extract_probable_pitchers,
    compare_date,
    PLAUSIBLE_MISMATCH_RATE_FLOOR,
    PLAUSIBLE_MISMATCH_RATE_CEILING,
)


class TestExtractProbablePitchers:
    def test_extracts_home_and_away_ids(self):
        schedule = {"dates": [{"games": [{
            "gamePk": 1,
            "teams": {
                "home": {"probablePitcher": {"id": 111}},
                "away": {"probablePitcher": {"id": 222}},
            },
        }]}]}
        out = extract_probable_pitchers(schedule)
        assert out == {1: {"home": "111", "away": "222"}}

    def test_missing_probable_pitcher_is_none_not_guessed(self):
        schedule = {"dates": [{"games": [{
            "gamePk": 1,
            "teams": {"home": {}, "away": {"probablePitcher": {"id": 222}}},
        }]}]}
        out = extract_probable_pitchers(schedule)
        assert out[1]["home"] is None
        assert out[1]["away"] == "222"

    def test_empty_schedule_returns_empty_dict(self):
        assert extract_probable_pitchers(None) == {}
        assert extract_probable_pitchers({}) == {}

    def test_games_missing_gamepk_are_skipped(self):
        schedule = {"dates": [{"games": [{"teams": {}}]}]}
        assert extract_probable_pitchers(schedule) == {}


class TestCompareDate:
    def test_match_and_mismatch_rows(self, monkeypatch):
        def fake_fetch(iso_date, timeout=15):
            return {"dates": [{"games": [
                {"gamePk": 1, "teams": {
                    "home": {"probablePitcher": {"id": 111}},
                    "away": {"probablePitcher": {"id": 222}},
                }},
                {"gamePk": 2, "teams": {
                    "home": {"probablePitcher": {"id": 333}},
                    "away": {"probablePitcher": {"id": 444}},
                }},
            ]}]}
        monkeypatch.setattr(
            "scripts.edgelab.backtest.probe_starter_identity_pit_safety.fetch_probable_pitchers_for_date",
            fake_fetch,
        )
        confirmed = {
            1: {"home": "111", "away": "999"},  # away mismatches
            2: {"home": "333", "away": "444"},  # both match
        }
        rows = compare_date("2023-06-15", 2023, confirmed)
        assert len(rows) == 4
        matches = {(r["gamePk"], r["side"]): r["match"] for r in rows}
        assert matches[(1, "home")] is True
        assert matches[(1, "away")] is False
        assert matches[(2, "home")] is True
        assert matches[(2, "away")] is True

    def test_game_not_in_confirmed_cache_is_skipped_not_guessed(self, monkeypatch):
        def fake_fetch(iso_date, timeout=15):
            return {"dates": [{"games": [
                {"gamePk": 999, "teams": {
                    "home": {"probablePitcher": {"id": 1}},
                    "away": {"probablePitcher": {"id": 2}},
                }},
            ]}]}
        monkeypatch.setattr(
            "scripts.edgelab.backtest.probe_starter_identity_pit_safety.fetch_probable_pitchers_for_date",
            fake_fetch,
        )
        rows = compare_date("2023-06-15", 2023, confirmed_starters={})
        assert rows == []

    def test_none_probable_or_confirmed_ids_are_never_compared(self, monkeypatch):
        def fake_fetch(iso_date, timeout=15):
            return {"dates": [{"games": [
                {"gamePk": 1, "teams": {"home": {}, "away": {"probablePitcher": {"id": 2}}}},
            ]}]}
        monkeypatch.setattr(
            "scripts.edgelab.backtest.probe_starter_identity_pit_safety.fetch_probable_pitchers_for_date",
            fake_fetch,
        )
        confirmed = {1: {"home": "111", "away": None}}
        rows = compare_date("2023-06-15", 2023, confirmed)
        assert rows == []  # home has no probable id, away has no confirmed id


class TestPlausibleBand:
    def test_band_is_nonzero_floor_and_bounded_ceiling(self):
        # Preregistered band: rules out both "always echoes final result" (0%)
        # and "matching bug" (implausibly high) without assuming an exact rate.
        assert PLAUSIBLE_MISMATCH_RATE_FLOOR > 0.0
        assert PLAUSIBLE_MISMATCH_RATE_CEILING < 1.0
        assert PLAUSIBLE_MISMATCH_RATE_FLOOR < PLAUSIBLE_MISMATCH_RATE_CEILING
