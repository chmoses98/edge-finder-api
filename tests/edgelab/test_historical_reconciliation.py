#!/usr/bin/env python3
"""
tests/edgelab/test_historical_reconciliation.py
====================================================
Coverage for scripts/edgelab/ingest_existing_bets.py's --dry-run/backup
behavior and scripts/edgelab/reconcile_bet_history.py's counts --
Canonical Placed-Bet Ledger milestone, requirements 15 & 17.
"""
import glob
import importlib.util
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def _load_script(name):
    path = os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "edgelab", name)
    spec = importlib.util.spec_from_file_location(name.replace(".py", ""), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ingest_script = _load_script("ingest_existing_bets.py")
reconcile_script = _load_script("reconcile_bet_history.py")


def _write_json(path, data):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f)


def test_dry_run_never_writes_the_canonical_ledger(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    root_bets = str(tmp_path / "bets.json")
    session_bets = str(tmp_path / "data" / "bets.json")
    _write_json(root_bets, [{
        "date": "2026-07-31", "game": "DET@ATH", "market": "F5_ML_Away",
        "ticker": "KXMLBF5-26JUL312140DETATH-DET", "betSize": 4.5,
        "actualEntryPrice": 0.505, "entryTimestamp": "2026-07-31T22:38:09Z",
    }])
    _write_json(session_bets, [])

    monkeypatch.setattr(sys, "argv", ["ingest_existing_bets.py", "--root-bets", root_bets, "--session-bets", session_bets, "--dry-run"])
    exit_code = ingest_script.main()
    assert exit_code == 0
    canonical_path = os.path.join("data", "edgelab", "bets", "bets.jsonl")
    assert not os.path.exists(canonical_path)


def test_real_run_creates_a_backup_before_writing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    root_bets = str(tmp_path / "bets.json")
    session_bets = str(tmp_path / "data" / "bets.json")
    _write_json(root_bets, [{
        "date": "2026-07-31", "game": "DET@ATH", "market": "F5_ML_Away",
        "ticker": "KXMLBF5-26JUL312140DETATH-DET", "betSize": 4.5,
        "actualEntryPrice": 0.505, "entryTimestamp": "2026-07-31T22:38:09Z",
    }])
    _write_json(session_bets, [])

    monkeypatch.setattr(sys, "argv", ["ingest_existing_bets.py", "--root-bets", root_bets, "--session-bets", session_bets])
    exit_code = ingest_script.main()
    assert exit_code == 0
    canonical_path = os.path.join("data", "edgelab", "bets", "bets.jsonl")
    assert os.path.exists(canonical_path)
    # First run: nothing existed yet, so no backup needed (nothing to lose).
    backups_first = glob.glob(os.path.join("data", "edgelab", "bets", "backups", "*.bak"))
    assert backups_first == []

    # Second run with a genuinely changed record -> must back up the existing ledger first.
    _write_json(root_bets, [{
        "date": "2026-07-31", "game": "DET@ATH", "market": "F5_ML_Away",
        "ticker": "KXMLBF5-26JUL312140DETATH-DET", "betSize": 9.0,  # changed stake
        "actualEntryPrice": 0.505, "entryTimestamp": "2026-07-31T22:38:09Z",
    }])
    monkeypatch.setattr(sys, "argv", ["ingest_existing_bets.py", "--root-bets", root_bets, "--session-bets", session_bets])
    ingest_script.main()
    backups_second = glob.glob(os.path.join("data", "edgelab", "bets", "backups", "*.bak"))
    assert len(backups_second) == 1


def test_reconcile_report_counts_missing_fields_and_tranches(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    root_bets = str(tmp_path / "bets.json")
    session_bets = str(tmp_path / "data" / "bets.json")
    _write_json(root_bets, [
        {"date": "2026-07-31", "game": "DET@ATH", "market": "F5_ML_Away",
         "ticker": "KXMLBF5-TICK1", "betSize": 4.5,
         "actualEntryPrice": 0.505, "entryTimestamp": "2026-07-31T22:38:09Z"},
        {"date": "2026-07-31", "game": "DET@ATH", "market": "F5_ML_Away",
         "ticker": "KXMLBF5-TICK1", "betSize": 2.0,
         "actualEntryPrice": None, "entryTimestamp": "2026-07-31T22:45:00Z"},  # missing price, distinct tranche
        {"date": "2026-07-31", "game": "DET@ATH", "market": "no ticker at all"},  # missing ticker entirely
    ])
    _write_json(session_bets, [])

    monkeypatch.setattr(sys, "argv", ["reconcile_bet_history.py", "--root-bets", root_bets, "--session-bets", session_bets])
    exit_code = reconcile_script.main()
    assert exit_code == 0
    # Function is print-only in main(); re-derive the report directly for assertions.
    import io
    from contextlib import redirect_stdout
    buf = io.StringIO()
    with redirect_stdout(buf):
        reconcile_script.main()
    report = json.loads(buf.getvalue())
    assert report["missingTicker"] == 1
    assert report["missingEntryPrice"] == 1
    assert report["totalUniqueHistoricalBets"] == 2


def test_reconcile_report_is_read_only(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    root_bets = str(tmp_path / "bets.json")
    session_bets = str(tmp_path / "data" / "bets.json")
    _write_json(root_bets, [])
    _write_json(session_bets, [])
    monkeypatch.setattr(sys, "argv", ["reconcile_bet_history.py", "--root-bets", root_bets, "--session-bets", session_bets])
    reconcile_script.main()
    assert not os.path.exists(os.path.join("data", "edgelab", "bets", "bets.jsonl"))
