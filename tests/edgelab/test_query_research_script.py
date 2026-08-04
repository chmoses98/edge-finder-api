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
