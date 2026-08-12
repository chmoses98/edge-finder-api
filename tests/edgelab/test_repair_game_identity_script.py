#!/usr/bin/env python3
"""
tests/edgelab/test_repair_game_identity_script.py
======================================================
End-to-end coverage for scripts/edgelab/repair_game_identity.py's
second identity source: a live MLB schedule fetch
(lib.edgelab.mlb_schedule), tried only for whatever the existing
pipeline-slate pass (lib.edgelab.market_universe.load_game_context)
leaves unresolved -- most notably a standalone/manual-only betting day
that never had a data/pipeline/<date>/normalized_slate.json run at all
(the real 2026-08-11 case). Reuses backfill_missing_game_pks completely
unchanged; this file only exercises the CLI's new orchestration.
"""
import importlib.util
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

_spec = importlib.util.spec_from_file_location(
    "repair_game_identity_script",
    os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "edgelab", "repair_game_identity.py"),
)
repair_script = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(repair_script)

from lib.edgelab import mlb_schedule, storage

DATE = "2026-08-11"


def _game(game_id, away, home):
    return {
        "schemaVersion": "1", "gameId": game_id, "sport": "MLB", "platform": "KALSHI",
        "mlbGamePk": None, "gameDate": DATE, "scheduledStartTime": None,
        "actualStartTime": None, "awayTeam": away, "homeTeam": home, "venue": None,
        "status": None, "doubleheaderGameNumber": None, "kalshiKey": None,
        "createdAt": "2026-08-12T03:35:15Z", "updatedAt": None, "source": "kalshi_registry_snapshots",
        "validationStatus": "warning",
        "provenance": {"sourceSystem": "kalshi_registry_snapshots", "sourceFile": "x.json", "sourceKey": game_id, "capturedAt": "2026-08-12T03:35:15Z", "ingestedAt": "2026-08-12T03:35:15Z"},
    }


def _schedule_json(games):
    return {
        "dates": [{
            "games": [
                {
                    "gamePk": g[0],
                    "teams": {"away": {"team": {"id": g[1]}}, "home": {"team": {"id": g[2]}}},
                    "gameDate": g[3], "status": {"detailedState": g[4]}, "venue": {"name": g[5]},
                    "gameNumber": 1,
                }
                for g in games
            ],
        }],
    }


def test_standalone_manual_only_day_with_no_pipeline_slate_is_resolved_via_schedule(tmp_path, monkeypatch):
    """
    Scenario 1: no data/pipeline/2026-08-11/normalized_slate.json exists
    at all (never created -- not merely empty), so the existing
    load_game_context-based pass resolves nothing. The new schedule
    fallback still resolves every game the live schedule covers.
    """
    monkeypatch.chdir(tmp_path)
    games_path = storage.partition_path("games", DATE)
    storage.write_all_records(games_path, [
        _game("2026-08-11_KC_LAD_2210", "KC", "LAD"),
        _game("2026-08-11_TB_ATH_2140", "TB", "ATH"),
    ])
    assert not os.path.exists(os.path.join("data", "pipeline", DATE, "normalized_slate.json"))

    monkeypatch.setattr(
        mlb_schedule, "fetch_schedule",
        lambda date, timeout=15: _schedule_json([
            (745123, 118, 119, "2026-08-11T02:10:00Z", "Final", "Dodger Stadium"),
            (745300, 139, 133, "2026-08-11T01:40:00Z", "Final", "Sutter Health Park"),
        ]),
    )

    counts = repair_script.repair_date(DATE)
    assert counts["gamesBackfilledMlbGamePk"] == 0  # nothing resolved from the (nonexistent) pipeline slate
    assert counts["gamesBackfilledMlbGamePkViaSchedule"] == 2
    assert counts["scheduleWarnings"] == []

    rows = {r["gameId"]: r for r in storage.read_records(games_path)}
    assert rows["2026-08-11_KC_LAD_2210"]["mlbGamePk"] == "745123"
    assert rows["2026-08-11_TB_ATH_2140"]["mlbGamePk"] == "745300"
    assert rows["2026-08-11_KC_LAD_2210"]["gameId"] == "2026-08-11_KC_LAD_2210"  # never renamed


def test_pipeline_slate_source_still_tried_first_schedule_never_called_when_unnecessary(tmp_path, monkeypatch):
    """Requirement 1 ('reuse already-persisted authoritative IDs whenever available'): when the pipeline slate already resolves everything, the live schedule is never fetched at all."""
    monkeypatch.chdir(tmp_path)
    games_path = storage.partition_path("games", DATE)
    storage.write_all_records(games_path, [_game("2026-08-11_KC_LAD_2210", "KC", "LAD")])

    slate_dir = os.path.join("data", "pipeline", DATE)
    os.makedirs(slate_dir, exist_ok=True)
    import json
    with open(os.path.join(slate_dir, "normalized_slate.json"), "w") as f:
        json.dump({"data": {"games": [{
            "gameId": 745123, "away": {"abbr": "KC"}, "home": {"abbr": "LAD"},
            "startTime": "2026-08-11T02:10:00Z", "status": "Final", "venue": "Dodger Stadium", "kalshiKey": "KCLAD",
        }]}}, f)

    calls = []
    monkeypatch.setattr(mlb_schedule, "fetch_schedule", lambda date, timeout=15: calls.append(date) or None)

    counts = repair_script.repair_date(DATE)
    assert counts["gamesBackfilledMlbGamePk"] == 1
    assert counts["gamesBackfilledMlbGamePkViaSchedule"] == 0
    assert calls == []  # schedule fetch never even attempted -- nothing was left missing


def test_repair_date_is_idempotent_across_two_full_runs(tmp_path, monkeypatch):
    """Scenario 4, via the real CLI entry point: a second full repair_date() call is a true no-op."""
    monkeypatch.chdir(tmp_path)
    games_path = storage.partition_path("games", DATE)
    storage.write_all_records(games_path, [_game("2026-08-11_KC_LAD_2210", "KC", "LAD")])

    calls = []
    monkeypatch.setattr(
        mlb_schedule, "fetch_schedule",
        lambda date, timeout=15: calls.append(date) or _schedule_json([
            (745123, 118, 119, "2026-08-11T02:10:00Z", "Final", "Dodger Stadium"),
        ]),
    )

    first = repair_script.repair_date(DATE)
    assert first["gamesBackfilledMlbGamePkViaSchedule"] == 1
    assert len(calls) == 1

    second = repair_script.repair_date(DATE)
    assert second["gamesBackfilledMlbGamePk"] == 0
    assert second["gamesIdentitySuperseded"] == 0
    assert second["gamesBackfilledMlbGamePkViaSchedule"] == 0  # already resolved -- schedule never re-fetched
    assert len(calls) == 1  # no second network call

    rows = list(storage.read_records(games_path))
    assert len(rows) == 1  # never duplicated
    assert rows[0]["mlbGamePk"] == "745123"


def test_repair_date_dry_run_never_writes(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    games_path = storage.partition_path("games", DATE)
    storage.write_all_records(games_path, [_game("2026-08-11_KC_LAD_2210", "KC", "LAD")])
    monkeypatch.setattr(
        mlb_schedule, "fetch_schedule",
        lambda date, timeout=15: _schedule_json([(745123, 118, 119, "2026-08-11T02:10:00Z", "Final", "Dodger Stadium")]),
    )

    counts = repair_script.repair_date(DATE, dry_run=True)
    assert counts["gamesBackfilledMlbGamePkViaSchedule"] == 1  # computed...
    rows = list(storage.read_records(games_path))
    assert rows[0]["mlbGamePk"] is None  # ...but never written to disk


def test_repair_date_reports_schedule_fetch_failure_without_crashing(tmp_path, monkeypatch):
    """The real 2026-08-11 sandboxed-session shape: the live fetch fails (network policy) and the run still completes cleanly with an explicit reason."""
    monkeypatch.chdir(tmp_path)
    games_path = storage.partition_path("games", DATE)
    storage.write_all_records(games_path, [_game("2026-08-11_KC_LAD_2210", "KC", "LAD")])
    monkeypatch.setattr(mlb_schedule, "fetch_schedule", lambda date, timeout=15: None)

    counts = repair_script.repair_date(DATE)
    assert counts["gamesBackfilledMlbGamePkViaSchedule"] == 0
    assert len(counts["scheduleWarnings"]) == 1
    assert "MLB schedule fetch failed" in counts["scheduleWarnings"][0]
    rows = list(storage.read_records(games_path))
    assert rows[0]["mlbGamePk"] is None  # never guessed
