#!/usr/bin/env python3
"""
tests/edgelab/test_backfill_player_prop_settlement.py
==========================================================
Coverage for scripts/edgelab/backfill_player_prop_settlement.py (GitHub
issue #43): date/date-range selection, dry-run (no writes), and
counts-by-family + unresolved-reason-breakdown aggregation.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab import mlb_boxscore, storage
from scripts.edgelab.backfill_player_prop_settlement import _date_range, run_backfill
import scripts.edgelab.settle_markets as settle_markets_module


def _feed(strikeouts=9):
    return {
        "gameData": {"status": {"detailedState": "Final"}},
        "liveData": {"boxscore": {"teams": {
            "away": {"players": {}},
            "home": {"players": {
                "ID660271": {
                    "person": {"id": 660271, "fullName": "Emmet Sheehan"},
                    "jerseyNumber": "80",
                    "stats": {"pitching": {"strikeOuts": strikeouts, "inningsPitched": "6.0"}},
                },
            }},
        }}},
    }


def _seed_date(date, game_id="12345"):
    storage.write_all_records(storage.partition_path("games", date), [
        {"gameId": game_id, "mlbGamePk": 824404, "awayTeam": "BOS", "homeTeam": "LAD", "status": "Preview"},
    ])
    storage.write_all_records(storage.partition_path("markets", date), [
        {"marketTicker": "KXMLBKS-26AUG021920BOSLAD-LADESHEEHAN80-9", "eventTicker": "KXMLBKS-26AUG021920BOSLAD",
         "title": "Emmet Sheehan: 9+ strikeouts?", "gameId": game_id, "marketFamily": "pitcher_strikeouts"},
    ])


def test_date_range_inclusive():
    assert list(_date_range("2026-08-01", "2026-08-03")) == ["2026-08-01", "2026-08-02", "2026-08-03"]


def test_date_range_single_day():
    assert list(_date_range("2026-08-01", "2026-08-01")) == ["2026-08-01"]


def test_date_range_rejects_end_before_start():
    import pytest
    with pytest.raises(ValueError):
        list(_date_range("2026-08-03", "2026-08-01"))


def test_dry_run_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    date = "2026-08-02"
    _seed_date(date)
    monkeypatch.setattr(settle_markets_module, "fetch_mlb_linescore", None)
    monkeypatch.setattr(mlb_boxscore, "fetch_game_feed", lambda game_pk, timeout=15: _feed())

    summary = run_backfill([date], dry_run=True)
    assert summary["dryRun"] is True
    assert summary["byFamily"]["pitcher_strikeouts"]["settled"] == 1

    # Nothing was actually written to disk.
    assert list(storage.read_records(storage.partition_path("settlements", date))) == []


def test_live_run_writes_settlements(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    date = "2026-08-02"
    _seed_date(date)
    monkeypatch.setattr(settle_markets_module, "fetch_mlb_linescore", None)
    monkeypatch.setattr(mlb_boxscore, "fetch_game_feed", lambda game_pk, timeout=15: _feed())

    summary = run_backfill([date], dry_run=False)
    assert summary["dryRun"] is False
    written = list(storage.read_records(storage.partition_path("settlements", date)))
    assert len(written) == 1
    assert written[0]["result"] == "YES"


def test_counts_by_family_and_unresolved_reasons_aggregate_across_dates(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    dates = ["2026-08-01", "2026-08-02"]
    for date in dates:
        _seed_date(date)
    monkeypatch.setattr(settle_markets_module, "fetch_mlb_linescore", None)
    # No network -- feed fetch fails both days -> both unresolved with the same reason.
    monkeypatch.setattr(mlb_boxscore, "fetch_game_feed", lambda game_pk, timeout=15: None)

    summary = run_backfill(dates, dry_run=True)
    assert summary["byFamily"]["pitcher_strikeouts"]["observed"] == 2
    assert summary["byFamily"]["pitcher_strikeouts"]["unresolved"] == 2
    assert summary["unresolvedReasonsByFamily"]["pitcher_strikeouts"]["game_not_final"] == 2
    assert len(summary["perDate"]) == 2
