#!/usr/bin/env python3
"""
tests/edgelab/test_generate_daily_report_script.py
=======================================================
Regression coverage for scripts/edgelab/generate_daily_report.py's own
bet-loading date filter (the one piece of wiring not already covered by
tests/edgelab/test_reports.py's pure build_daily_report() tests).

Confirmed bug: the filter used only entryTimestamp's date prefix, so a
timestamp-free canonical manual import (entryTimestamp=None by design --
see lib.edgelab.bets.build_manual_bet_record's Timestamp-Optional Manual
Imports milestone) silently disappeared from placedBets even though its
gameDate was known and correct. lib.edgelab.reports.build_postmortem
already used the correct gameDate-first filter; this script was the one
place left using the broken one.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import importlib.util
import json

_spec = importlib.util.spec_from_file_location(
    "generate_daily_report_script",
    os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "edgelab", "generate_daily_report.py"),
)
generate_daily_report_script = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(generate_daily_report_script)


def test_timestamp_free_bet_is_counted_by_gameDate(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    import sys as _sys
    from lib.edgelab import storage

    date = "2026-08-03"
    storage.write_all_records(storage.singleton_path("bets", "bets.jsonl"), [{
        "betId": "manual-import-1", "marketTicker": "KXMLBF5-TEST-DET", "selection": "DET F5",
        "stake": 10.0, "entryPrice": 0.5, "entryTimestamp": None, "gameDate": date,
        "status": "pending", "recordStatus": "ACTIVE",
    }])

    monkeypatch.setattr(_sys, "argv", ["generate_daily_report.py", "--date", date])
    exit_code = generate_daily_report_script.main()
    assert exit_code == 0

    with open(os.path.join("data", "edgelab", "reports", f"{date}.json")) as f:
        report = json.load(f)
    assert report["placedBets"] == 1


def test_bet_on_a_different_gameDate_is_excluded_even_with_no_timestamp(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    import sys as _sys
    from lib.edgelab import storage

    date = "2026-08-03"
    storage.write_all_records(storage.singleton_path("bets", "bets.jsonl"), [{
        "betId": "manual-import-other-day", "marketTicker": "KXMLBF5-TEST-DET", "selection": "DET F5",
        "stake": 10.0, "entryPrice": 0.5, "entryTimestamp": None, "gameDate": "2026-08-04",
        "status": "pending", "recordStatus": "ACTIVE",
    }])

    monkeypatch.setattr(_sys, "argv", ["generate_daily_report.py", "--date", date])
    generate_daily_report_script.main()

    with open(os.path.join("data", "edgelab", "reports", f"{date}.json")) as f:
        report = json.load(f)
    assert report["placedBets"] == 0


def test_legacy_bet_with_no_gameDate_still_falls_back_to_entryTimestamp(tmp_path, monkeypatch):
    """
    Preserves correct behavior for older/legacy rows that predate
    gameDate (or genuinely never carried one) but do carry a real
    entryTimestamp -- the gameDate-first filter must fall back to the
    old entryTimestamp-prefix behavior for exactly this case, not
    regress it.
    """
    monkeypatch.chdir(tmp_path)
    import sys as _sys
    from lib.edgelab import storage

    date = "2026-08-03"
    storage.write_all_records(storage.singleton_path("bets", "bets.jsonl"), [{
        "betId": "legacy-1", "marketTicker": "KXMLBF5-TEST-DET", "selection": "DET F5",
        "stake": 10.0, "entryPrice": 0.5, "entryTimestamp": f"{date}T18:00:00Z", "gameDate": None,
        "status": "pending", "recordStatus": "ACTIVE",
    }])

    monkeypatch.setattr(_sys, "argv", ["generate_daily_report.py", "--date", date])
    generate_daily_report_script.main()

    with open(os.path.join("data", "edgelab", "reports", f"{date}.json")) as f:
        report = json.load(f)
    assert report["placedBets"] == 1
