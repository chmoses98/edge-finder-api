#!/usr/bin/env python3
"""
tests/edgelab/test_query_research_script.py
================================================
Part 6 (Research Query Surface): end-to-end wiring for
scripts/edgelab/query_research.py, plus a static guarantee that it never
calls a write function -- it is read-only by construction.
"""
import importlib.util
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab import storage
from lib.edgelab.bets import build_manual_bet_record, write_placed_bet


def _load_script(name):
    path = os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "edgelab", name)
    spec = importlib.util.spec_from_file_location(name.replace(".py", ""), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


query_script = _load_script("query_research.py")


def test_query_research_is_read_only_by_construction():
    with open(os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "edgelab", "query_research.py")) as f:
        source = f.read()
    assert "storage.append_records(" not in source
    assert "storage.upsert_records(" not in source
    assert "storage.write_all_records(" not in source
    assert "write_placed_bet(" not in source
    assert "write_postmortem(" not in source


def test_observed_markets_command(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    observations = [{"marketTicker": "T1", "gameId": "g1", "marketFamily": "team_total"}]
    storage.append_records(storage.partition_path("observations", "2026-08-03", compressed=True), observations, "marketTicker")
    monkeypatch.setattr(sys, "argv", ["query_research.py", "observed-markets", "--date", "2026-08-03", "--game-id", "g1"])
    exit_code = query_script.main()
    assert exit_code == 0
    result = json.loads(capsys.readouterr().out)
    assert len(result) == 1
    assert result[0]["marketTicker"] == "T1"


def test_capture_for_bet_command(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    record = build_manual_bet_record(
        "T1", "sel", 5.0, 0.5, "2026-08-03T18:00:00Z", game_date="2026-08-03",
        market_observation_linkage={"linkageStatus": "LINKED", "marketCorpusRunId": "RUN1", "observationId": "obs1"},
    )
    receipt = write_placed_bet(record)
    monkeypatch.setattr(sys, "argv", ["query_research.py", "capture-for-bet", "--bet-id", receipt["betId"]])
    exit_code = query_script.main()
    assert exit_code == 0
    result = json.loads(capsys.readouterr().out)
    assert result["marketCorpusRunId"] == "RUN1"


def test_capture_for_bet_unknown_bet_id_fails_closed(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["query_research.py", "capture-for-bet", "--bet-id", "doesnotexist"])
    exit_code = query_script.main()
    assert exit_code == 1


def _seed_research_day(date, ticker, market_family, result, yes_price):
    storage.append_records(
        storage.partition_path("observations", date, compressed=True),
        [{"marketTicker": ticker, "gameId": "g1", "marketFamily": market_family, "marketHorizon": "FULL_GAME",
          "threshold": 3.5, "capturedAt": f"{date}T22:00:00Z"}],
        "marketTicker",
    )
    storage.append_records(
        storage.partition_path("settlements", date),
        [{"marketTicker": ticker, "marketFamily": market_family, "settlementStatus": "SETTLED", "result": result,
          "wasRecommended": False, "wasPlaced": False,
          "hypotheticalReturnsByCheckpoint": [
              {"checkpoint": "CLOSING", "yesPrice": yes_price,
               "hypotheticalYesReturn": (1.0 - yes_price) / yes_price if result == "YES" else -1.0},
          ]}],
        "marketTicker",
    )


def test_research_query_command_single_date_and_family_filter(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _seed_research_day("2026-08-03", "T1", "team_total", "YES", 0.5)
    _seed_research_day("2026-08-03", "T2", "game_total", "NO", 0.4)
    monkeypatch.setattr(sys, "argv", [
        "query_research.py", "research-query", "--date", "2026-08-03", "--market-family", "team_total",
    ])
    exit_code = query_script.main()
    assert exit_code == 0
    result = json.loads(capsys.readouterr().out)
    assert result["observedCount"] == 1
    assert result["sampleSize"] == 1
    assert result["wins"] == 1
    assert result["hypotheticalRoiPct"] == 100.0


def test_research_query_command_date_range_combines_partitions(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _seed_research_day("2026-08-03", "T1", "team_total", "YES", 0.5)
    _seed_research_day("2026-08-04", "T2", "team_total", "NO", 0.5)
    monkeypatch.setattr(sys, "argv", [
        "query_research.py", "research-query", "--start-date", "2026-08-03", "--end-date", "2026-08-04",
        "--market-family", "team_total",
    ])
    exit_code = query_script.main()
    assert exit_code == 0
    result = json.loads(capsys.readouterr().out)
    assert result["observedCount"] == 2
    assert result["wins"] == 1
    assert result["losses"] == 1


def test_research_query_command_group_by(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _seed_research_day("2026-08-03", "T1", "team_total", "YES", 0.5)
    _seed_research_day("2026-08-03", "T2", "game_total", "NO", 0.4)
    monkeypatch.setattr(sys, "argv", [
        "query_research.py", "research-query", "--date", "2026-08-03", "--group-by", "marketFamily",
    ])
    exit_code = query_script.main()
    assert exit_code == 0
    result = json.loads(capsys.readouterr().out)
    assert set(result) == {"team_total", "game_total"}
    assert result["team_total"]["wins"] == 1
    assert result["game_total"]["losses"] == 1


def test_research_query_command_requires_date_or_range(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["query_research.py", "research-query"])
    exit_code = query_script.main()
    assert exit_code == 1
