#!/usr/bin/env python3
"""
tests/edgelab/test_backfill_scheduled_start.py
===================================================
scheduledStart/CLV metadata fix, requirement 8/regression coverage:
scripts/edgelab/backfill_scheduled_start.py -- the historical catch-up
for a date whose Game/MarketObservation rows were already ingested with
scheduledStart(Time)=null.
"""
import importlib.util
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab import mlb_schedule, storage

DATE = "2026-08-16"


def _load_script(name):
    path = os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "edgelab", name)
    spec = importlib.util.spec_from_file_location(name.replace(".py", ""), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


backfill_script = _load_script("backfill_scheduled_start.py")


def _game(game_id, away, home, *, mlb_game_pk=None, scheduled_start_time=None):
    return {
        "schemaVersion": "1", "gameId": game_id, "sport": "MLB", "platform": "KALSHI",
        "mlbGamePk": mlb_game_pk, "gameDate": DATE, "scheduledStartTime": scheduled_start_time,
        "actualStartTime": None, "awayTeam": away, "homeTeam": home, "venue": None, "status": None,
        "doubleheaderGameNumber": None, "kalshiKey": None, "createdAt": "2026-08-16T16:44:43Z",
        "updatedAt": None, "source": "kalshi_registry_snapshots",
        "validationStatus": "valid" if mlb_game_pk else "warning",
        "provenance": {"sourceSystem": "kalshi_registry_snapshots", "sourceFile": "x.json", "sourceKey": game_id, "capturedAt": "2026-08-16T16:44:43Z", "ingestedAt": "2026-08-16T16:44:43Z"},
    }


def _observation(ticker, away, home, *, captured_at="2026-08-16T16:33:00.000Z", scheduled_start=None):
    return {
        "schemaVersion": "1", "marketObservationId": f"{ticker}|{captured_at}", "runId": "r1",
        "capturedAt": captured_at, "gameId": f"{DATE}_{away}_{home}_1335", "sport": "MLB", "platform": "KALSHI",
        "mlbGameId": None, "scheduledStart": scheduled_start, "awayTeam": away, "homeTeam": home,
        "seriesTicker": ticker.split("-", 1)[0], "eventTicker": ticker.split("-", 1)[0], "marketTicker": ticker,
        "marketFamily": "first_inning_run", "marketHorizon": None, "title": "t", "subtitle": None,
        "player": None, "team": None, "outcomeLabel": None, "threshold": None, "comparisonOperator": None,
        "yesBid": 46, "yesAsk": 48, "noBid": None, "noAsk": None, "lastPrice": None, "volume": None,
        "openInterest": None, "spreadCents": 2.0, "marketStatus": "active", "validationStatus": "valid",
        "parserStatus": "parsed", "lineupConfirmationState": None, "checkpoint": "INTERMEDIATE" if scheduled_start is None else "FIRST_DAILY",
        "isClosingCandidate": None, "gameStartedAtCapture": None, "isValidPregameObservation": None,
        "registryClassificationStatus": "CLASSIFIED", "githubRunId": None, "commitSha": None,
        "createdAt": captured_at, "source": "standalone_price_check",
        "provenance": {"sourceSystem": "standalone_price_check", "sourceFile": "x.json", "sourceKey": ticker, "capturedAt": captured_at, "ingestedAt": captured_at},
    }


def _write_observations(rows):
    path = storage.partition_path("observations", DATE, compressed=True)
    storage.append_records(path, rows, "marketObservationId")


def test_backfills_game_and_observations_offline_from_pipeline_slate(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    storage.write_all_records(storage.partition_path("games", DATE), [
        _game("2026-08-16_BOS_PIT_1335", "BOS", "PIT", mlb_game_pk="823344"),  # mlbGamePk already resolved, schedule still null
    ])
    _write_observations([_observation("KXMLBRFI-26AUG161335BOSPIT", "BOS", "PIT")])

    os.makedirs(os.path.join("data", "pipeline", DATE), exist_ok=True)
    with open(os.path.join("data", "pipeline", DATE, "normalized_slate.json"), "w") as f:
        json.dump({"data": {"games": [{
            "away": {"abbr": "BOS"}, "home": {"abbr": "PIT"}, "gameId": 823344,
            "startTime": "2026-08-16T17:35:00Z", "status": "Pre-Game", "venue": "PNC Park", "kalshiKey": "BOSPIT",
        }]}}, f)

    calls = []
    monkeypatch.setattr(mlb_schedule, "fetch_schedule", lambda date, timeout=15: calls.append(date) or None)

    result = backfill_script.backfill_date(DATE)
    assert calls == []  # pipeline slate alone resolves everything -- no live fetch needed
    assert result["gamesBackfilled"] == 1
    assert result["observationsBackfilled"] == 1
    assert result["unresolvedTeamPairs"] == []

    games = list(storage.read_records(storage.partition_path("games", DATE)))
    assert games[0]["scheduledStartTime"] == "2026-08-16T17:35:00Z"
    assert games[0]["scheduledStartBackfill"]["method"] == "DATE_AWAY_HOME_UNIQUE_MATCH"

    observations = list(storage.read_records(storage.partition_path("observations", DATE, compressed=True)))
    obs = observations[0]
    assert obs["scheduledStart"] == "2026-08-16T17:35:00Z"
    assert obs["gameStartedAtCapture"] is False
    assert obs["isValidPregameObservation"] is True
    assert obs["isClosingCandidate"] is True
    assert obs["checkpoint"] != "INTERMEDIATE"


def test_falls_back_to_live_schedule_when_pipeline_slate_never_existed(tmp_path, monkeypatch):
    """The real 2026-08-15 shape: a genuinely standalone-only day, no normalized_slate.json at all."""
    monkeypatch.chdir(tmp_path)
    storage.write_all_records(storage.partition_path("games", DATE), [_game("2026-08-16_BOS_PIT_1335", "BOS", "PIT")])
    _write_observations([_observation("KXMLBRFI-26AUG161335BOSPIT", "BOS", "PIT")])
    assert not os.path.exists(os.path.join("data", "pipeline", DATE, "normalized_slate.json"))

    monkeypatch.setattr(
        mlb_schedule, "fetch_schedule",
        lambda date, timeout=15: {
            "dates": [{"games": [{
                "gamePk": 823344, "teams": {"away": {"team": {"id": 111}}, "home": {"team": {"id": 134}}},  # BOS, PIT
                "gameDate": "2026-08-16T17:35:00Z", "status": {"detailedState": "Pre-Game"},
                "venue": {"name": "PNC Park"}, "gameNumber": 1,
            }]}],
        },
    )

    result = backfill_script.backfill_date(DATE)
    assert result["gamesBackfilled"] == 1
    assert result["observationsBackfilled"] == 1

    games = list(storage.read_records(storage.partition_path("games", DATE)))
    assert games[0]["scheduledStartTime"] == "2026-08-16T17:35:00Z"
    observations = list(storage.read_records(storage.partition_path("observations", DATE, compressed=True)))
    assert observations[0]["scheduledStart"] == "2026-08-16T17:35:00Z"


def test_unresolved_pair_is_reported_and_left_null_never_guessed():
    """scheduledStart/CLV fail-safe carried through to the backfill: a pair with no canonical match is left exactly as-is."""
    rows = [_observation("KXMLBRFI-26AUG161335XXXYYY", "XXX", "YYY")]
    updated_rows, updated_count, unresolved = backfill_script._backfill_observations(rows, {("BOS", "PIT"): {"scheduledStart": "2026-08-16T17:35:00Z"}})
    assert updated_count == 0
    assert unresolved == [("XXX", "YYY")]
    assert updated_rows[0]["scheduledStart"] is None


def test_dry_run_never_writes(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    storage.write_all_records(storage.partition_path("games", DATE), [_game("2026-08-16_BOS_PIT_1335", "BOS", "PIT")])
    _write_observations([_observation("KXMLBRFI-26AUG161335BOSPIT", "BOS", "PIT")])
    os.makedirs(os.path.join("data", "pipeline", DATE), exist_ok=True)
    with open(os.path.join("data", "pipeline", DATE, "normalized_slate.json"), "w") as f:
        json.dump({"data": {"games": [{
            "away": {"abbr": "BOS"}, "home": {"abbr": "PIT"}, "gameId": 823344,
            "startTime": "2026-08-16T17:35:00Z", "status": "Pre-Game", "venue": "PNC Park", "kalshiKey": "BOSPIT",
        }]}}, f)

    result = backfill_script.backfill_date(DATE, dry_run=True)
    assert result["gamesBackfilled"] == 1  # computed...
    assert result["observationsBackfilled"] == 1

    games = list(storage.read_records(storage.partition_path("games", DATE)))
    assert games[0]["scheduledStartTime"] is None  # ...but never written to disk
    observations = list(storage.read_records(storage.partition_path("observations", DATE, compressed=True)))
    assert observations[0]["scheduledStart"] is None


def test_idempotent_across_two_full_runs(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    storage.write_all_records(storage.partition_path("games", DATE), [_game("2026-08-16_BOS_PIT_1335", "BOS", "PIT")])
    _write_observations([_observation("KXMLBRFI-26AUG161335BOSPIT", "BOS", "PIT")])
    os.makedirs(os.path.join("data", "pipeline", DATE), exist_ok=True)
    with open(os.path.join("data", "pipeline", DATE, "normalized_slate.json"), "w") as f:
        json.dump({"data": {"games": [{
            "away": {"abbr": "BOS"}, "home": {"abbr": "PIT"}, "gameId": 823344,
            "startTime": "2026-08-16T17:35:00Z", "status": "Pre-Game", "venue": "PNC Park", "kalshiKey": "BOSPIT",
        }]}}, f)

    first = backfill_script.backfill_date(DATE)
    assert first["gamesBackfilled"] == 1
    assert first["observationsBackfilled"] == 1

    second = backfill_script.backfill_date(DATE)
    assert second["gamesBackfilled"] == 0
    assert second["observationsBackfilled"] == 0

    observations = list(storage.read_records(storage.partition_path("observations", DATE, compressed=True)))
    assert len(observations) == 1  # never duplicated
