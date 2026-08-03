#!/usr/bin/env python3
"""
tests/edgelab/test_bet_entry_scripts.py
===========================================
End-to-end wiring coverage for the CLI/workflow entry surfaces
(scripts/edgelab/log_bet.py, scripts/edgelab/record_bet_from_workflow.py,
scripts/edgelab/query_bets.py) -- Canonical Placed-Bet Ledger milestone.
Proves they all route through the same canonical write/read functions
rather than each implementing their own ledger logic.
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


log_bet_script = _load_script("log_bet.py")
workflow_script = _load_script("record_bet_from_workflow.py")
query_script = _load_script("query_bets.py")


def test_log_bet_cli_writes_through_canonical_path(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", [
        "log_bet.py", "--ticker", "KXMLBF5-TEST", "--selection", "DET F5 moneyline",
        "--stake", "5", "--entry-price", "0.505", "--entry-timestamp", "2026-08-03T18:00:00Z",
    ])
    exit_code = log_bet_script.main()
    assert exit_code == 0
    path = os.path.join("data", "edgelab", "bets", "bets.jsonl")
    rows = list(storage.read_records(path))
    assert len(rows) == 1
    assert rows[0]["entryMethod"] == "MANUAL_CHAT_CONFIRMED"


def test_log_bet_cli_conflict_exits_nonzero_and_does_not_overwrite(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    base_argv = [
        "log_bet.py", "--ticker", "KXMLBF5-TEST", "--selection", "DET F5 moneyline",
        "--entry-price", "0.505", "--entry-timestamp", "2026-08-03T18:00:00Z",
    ]
    monkeypatch.setattr(sys, "argv", base_argv + ["--stake", "5"])
    log_bet_script.main()

    monkeypatch.setattr(sys, "argv", base_argv + ["--stake", "999"])
    exit_code = log_bet_script.main()
    assert exit_code == 1
    rows = list(storage.read_records(os.path.join("data", "edgelab", "bets", "bets.jsonl")))
    assert len(rows) == 1
    assert rows[0]["stake"] == 5.0


def test_record_bet_from_workflow_missing_required_input_fails_closed(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for var in ("GAME_DATE", "MARKET_TICKER", "SELECTION", "STAKE", "ENTRY_PRICE", "PLACED_AT",
                "RECOMMENDATION_ID", "NOTES", "ADVANCED_JSON", "RECEIPT_PATH", "GITHUB_STEP_SUMMARY"):
        monkeypatch.delenv(var, raising=False)
    exit_code = workflow_script.main()
    assert exit_code == 1
    assert not os.path.exists(os.path.join("data", "edgelab", "bets", "bets.jsonl"))


def test_record_bet_from_workflow_writes_and_uploads_receipt(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GAME_DATE", "2026-08-03")
    monkeypatch.setenv("MARKET_TICKER", "KXMLBF5-TESTWF-DET")
    monkeypatch.setenv("SELECTION", "DET F5 moneyline")
    monkeypatch.setenv("SIDE", "YES")
    monkeypatch.setenv("STAKE", "5")
    monkeypatch.setenv("ENTRY_PRICE", "0.505")
    monkeypatch.setenv("PLACED_AT", "2026-08-03T18:00:00Z")
    monkeypatch.setenv("NOTES", "test")
    monkeypatch.setenv("RECEIPT_PATH", str(tmp_path / "receipt.json"))
    monkeypatch.delenv("RECOMMENDATION_ID", raising=False)
    monkeypatch.delenv("ADVANCED_JSON", raising=False)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)

    exit_code = workflow_script.main()
    assert exit_code == 0
    receipt = json.loads((tmp_path / "receipt.json").read_text())
    assert receipt["success"] is True
    assert receipt["duplicateStatus"] == "NEW"
    rows = list(storage.read_records(os.path.join("data", "edgelab", "bets", "bets.jsonl")))
    assert rows[0]["entryMethod"] == "MANUAL_GITHUB_FORM"


def test_record_bet_from_workflow_recommendation_id_sets_confirmed_entry_method(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GAME_DATE", "2026-08-03")
    monkeypatch.setenv("MARKET_TICKER", "KXMLBF5-TESTWF-DET2")
    monkeypatch.setenv("SELECTION", "DET F5 moneyline")
    monkeypatch.setenv("SIDE", "YES")
    monkeypatch.setenv("STAKE", "5")
    monkeypatch.setenv("ENTRY_PRICE", "0.505")
    monkeypatch.setenv("PLACED_AT", "2026-08-03T18:00:00Z")
    monkeypatch.setenv("RECOMMENDATION_ID", "rec-123")
    monkeypatch.setenv("RECEIPT_PATH", str(tmp_path / "receipt2.json"))
    monkeypatch.delenv("NOTES", raising=False)
    monkeypatch.delenv("ADVANCED_JSON", raising=False)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)

    exit_code = workflow_script.main()
    assert exit_code == 0
    rows = list(storage.read_records(os.path.join("data", "edgelab", "bets", "bets.jsonl")))
    assert rows[0]["entryMethod"] == "PRODUCTION_RECOMMENDATION_CONFIRMED"
    assert rows[0]["source"] == "MODEL"
    assert rows[0]["recommendationId"] == "rec-123"


def test_record_bet_from_workflow_invalid_advanced_json_fails_closed(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GAME_DATE", "2026-08-03")
    monkeypatch.setenv("MARKET_TICKER", "KXMLBF5-TESTWF-DET3")
    monkeypatch.setenv("SELECTION", "x")
    monkeypatch.setenv("STAKE", "5")
    monkeypatch.setenv("ENTRY_PRICE", "0.505")
    monkeypatch.setenv("PLACED_AT", "2026-08-03T18:00:00Z")
    monkeypatch.setenv("ADVANCED_JSON", "{not valid json")
    monkeypatch.setenv("RECEIPT_PATH", str(tmp_path / "receipt3.json"))
    exit_code = workflow_script.main()
    assert exit_code == 1
    assert not os.path.exists(os.path.join("data", "edgelab", "bets", "bets.jsonl"))


def test_record_bet_from_workflow_never_calls_a_kalshi_order_api():
    """
    Static guardrail: the backing script for the entry workflow must have
    no order-placement capability at all -- it only ever describes a bet
    the user says they ALREADY placed elsewhere.
    """
    path = os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "edgelab", "record_bet_from_workflow.py")
    with open(path) as f:
        source = f.read().lower()
    for forbidden in ("place_order", "createorder", "submit_order", "kalshi.post", "requests.post", "requests.session"):
        assert forbidden not in source, f"unexpected order-placement-shaped call: {forbidden!r}"


def test_query_bets_cli_today_filter(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", [
        "log_bet.py", "--ticker", "KXMLBF5-Q1", "--selection", "x",
        "--stake", "5", "--entry-price", "0.5", "--entry-timestamp", "2026-08-03T18:00:00Z",
        "--game-date", "2026-08-03",
    ])
    log_bet_script.main()

    monkeypatch.setattr(sys, "argv", ["query_bets.py", "--filter", "today", "--date", "2026-08-03"])
    import io
    from contextlib import redirect_stdout
    buf = io.StringIO()
    with redirect_stdout(buf):
        exit_code = query_script.main()
    assert exit_code == 0
    result = json.loads(buf.getvalue())
    assert result["betCount"] == 1
    assert result["totalStaked"] == 5
