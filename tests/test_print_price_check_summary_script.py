#!/usr/bin/env python3
"""
tests/test_print_price_check_summary_script.py
====================================================
Tests for scripts/print_price_check_summary.py.
"""
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(ROOT, "scripts")


class TestPrintPriceCheckSummary:

    def test_prints_formatted_summary(self, tmp_path):
        meta_path = tmp_path / "metadata.json"
        meta_path.write_text(json.dumps({
            "sourceUsed": "live",
            "fetchInfo": {"endpoint": "https://x/api/kalshisearch", "httpStatus": 200,
                          "responseSizeBytes": 642, "marketsKeyPresent": True},
            "rawRecordsFetched": 642, "normalizedRecordCount": 642,
            "classifiedCount": 601, "unknownCount": 41, "malformedRecordCount": 0,
            "removedByFilterStage": {"date": 570}, "resultCount": 8,
            "diagnosis": "8 record(s) matched.",
        }))
        result = subprocess.run(
            [sys.executable, os.path.join(SCRIPTS_DIR, "print_price_check_summary.py"), str(meta_path)],
            capture_output=True, text=True, env={**os.environ, "PYTHONPATH": ROOT},
        )
        assert result.returncode == 0
        assert "642" in result.stdout
        assert "Reason" in result.stdout

    def test_missing_argument_returns_nonzero(self):
        result = subprocess.run(
            [sys.executable, os.path.join(SCRIPTS_DIR, "print_price_check_summary.py")],
            capture_output=True, text=True, env={**os.environ, "PYTHONPATH": ROOT},
        )
        assert result.returncode != 0
