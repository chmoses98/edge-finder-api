#!/usr/bin/env python3
"""
tests/edgelab/test_import_bet_batch.py
==========================================
Timestamp-Optional Manual Imports milestone: end-to-end coverage for
scripts/edgelab/import_bet_batch.py -- the bulk import surface for a
"normal bet list" with no exact placement timestamp required.
"""
import importlib.util
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab import storage


def _load_script(name):
    path = os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "edgelab", name)
    spec = importlib.util.spec_from_file_location(name.replace(".py", ""), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


import_script = _load_script("import_bet_batch.py")

BETS_PATH = os.path.join("data", "edgelab", "bets", "bets.jsonl")


def _seed_corpus(game_date="2026-08-03"):
    games = [{"gameId": "9001", "gameDate": game_date, "awayTeam": "SF", "homeTeam": "LAD", "scheduledStartTime": "2026-08-03T23:00:00Z"}]
    markets = [
        {"marketTicker": "SF-F5-ML", "gameId": "9001", "marketFamily": "game_result", "marketHorizon": "F5", "team": "SF", "threshold": None},
        {"marketTicker": "SF-TT-3.5", "gameId": "9001", "marketFamily": "team_total", "marketHorizon": "FULL_GAME", "team": "SF", "threshold": 3.5},
        {"marketTicker": "SF-TT-4.5", "gameId": "9001", "marketFamily": "team_total", "marketHorizon": "FULL_GAME", "team": "SF", "threshold": 4.5},
    ]
    storage.append_records(storage.partition_path("games", game_date), games, "gameId")
    storage.append_records(storage.partition_path("markets", game_date), markets, "marketTicker")


def test_timestamp_free_bet_with_explicit_ticker_writes_successfully(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    payload = [{
        "gameDate": "2026-08-03", "away": "SF", "home": "LAD", "marketTicker": "SF-F5-ML",
        "stake": 12.0, "entryOdds": 128,
    }]
    monkeypatch.setattr(sys, "argv", ["import_bet_batch.py", "--json", json.dumps(payload)])
    exit_code = import_script.main()
    assert exit_code == 0
    rows = list(storage.read_records(BETS_PATH))
    assert len(rows) == 1
    assert rows[0]["timestampStatus"] == "NOT_PROVIDED"
    assert rows[0]["entryTimestamp"] is None
    assert rows[0]["recordedAt"] is not None
    assert rows[0]["entryPrice"] is not None  # converted from American odds


def test_repeated_identical_batch_is_a_noop(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    payload = [{"gameDate": "2026-08-03", "away": "SF", "home": "LAD", "marketTicker": "SF-F5-ML", "stake": 12.0, "entryPrice": 0.55}]
    raw = json.dumps(payload)
    monkeypatch.setattr(sys, "argv", ["import_bet_batch.py", "--json", raw])
    import_script.main()
    rows_first = list(storage.read_records(BETS_PATH))
    capsys.readouterr()  # discard first run's stdout so only the rerun's receipt is inspected below

    monkeypatch.setattr(sys, "argv", ["import_bet_batch.py", "--json", raw])
    exit_code = import_script.main()
    rows_second = list(storage.read_records(BETS_PATH))
    assert exit_code == 0
    assert len(rows_second) == len(rows_first) == 1

    out = capsys.readouterr().out
    receipts = json.loads(out)
    assert receipts[0]["duplicateStatus"] == "DUPLICATE_NOOP"


def test_two_real_tranches_in_one_batch_remain_distinct(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    payload = [
        {"gameDate": "2026-08-03", "away": "SF", "home": "LAD", "marketTicker": "SF-F5-ML", "stake": 12.0, "entryPrice": 0.55},
        {"gameDate": "2026-08-03", "away": "SF", "home": "LAD", "marketTicker": "SF-F5-ML", "stake": 12.0, "entryPrice": 0.55},
    ]
    monkeypatch.setattr(sys, "argv", ["import_bet_batch.py", "--json", json.dumps(payload)])
    exit_code = import_script.main()
    assert exit_code == 0
    rows = list(storage.read_records(BETS_PATH))
    assert len(rows) == 2
    assert rows[0]["betId"] != rows[1]["betId"]
    assert {r["sourceRow"] for r in rows} == {0, 1}


def test_ambiguous_ticker_resolution_refuses_to_write(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _seed_corpus()
    payload = [{
        "gameDate": "2026-08-03", "away": "SF", "home": "LAD",
        "marketFamily": "team_total", "team": "SF", "stake": 10.0, "entryPrice": 0.5,
    }]
    monkeypatch.setattr(sys, "argv", ["import_bet_batch.py", "--json", json.dumps(payload)])
    exit_code = import_script.main()
    assert exit_code == 1
    assert not os.path.exists(BETS_PATH)


def test_ticker_resolved_from_corpus_when_threshold_given(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _seed_corpus()
    payload = [{
        "gameDate": "2026-08-03", "away": "SF", "home": "LAD",
        "marketFamily": "team_total", "team": "SF", "threshold": 4.5, "stake": 10.0, "entryPrice": 0.5,
    }]
    monkeypatch.setattr(sys, "argv", ["import_bet_batch.py", "--json", json.dumps(payload)])
    exit_code = import_script.main()
    assert exit_code == 0
    rows = list(storage.read_records(BETS_PATH))
    assert rows[0]["marketTicker"] == "SF-TT-4.5"


def test_missing_market_observation_produces_unlinked_valid_bet(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    payload = [{"gameDate": "2026-08-03", "away": "SF", "home": "LAD", "marketTicker": "SF-F5-ML", "stake": 12.0, "entryPrice": 0.55}]
    monkeypatch.setattr(sys, "argv", ["import_bet_batch.py", "--json", json.dumps(payload)])
    exit_code = import_script.main()
    assert exit_code == 0
    rows = list(storage.read_records(BETS_PATH))
    assert rows[0]["marketObservationLinkage"]["linkageStatus"] == "UNLINKED"


def test_missing_stake_is_reported_and_not_written(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    payload = [{"gameDate": "2026-08-03", "away": "SF", "home": "LAD", "marketTicker": "SF-F5-ML", "entryPrice": 0.5}]
    monkeypatch.setattr(sys, "argv", ["import_bet_batch.py", "--json", json.dumps(payload)])
    exit_code = import_script.main()
    assert exit_code == 1
    assert not os.path.exists(BETS_PATH)


def test_import_bet_batch_never_writes_bets_ledger_directly():
    """Structural guarantee: the only write into bets.jsonl goes through write_placed_bet."""
    with open(os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "edgelab", "import_bet_batch.py")) as f:
        source = f.read()
    assert "storage.append_records" not in source
    assert "storage.upsert_records" not in source
    assert "storage.write_all_records" not in source
    assert "write_placed_bet" in source
