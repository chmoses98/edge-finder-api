#!/usr/bin/env python3
"""
tests/test_print_price_check_json_summary_script.py
=========================================================
Tests for scripts/print_price_check_json_summary.py -- the CLI wrapper
around lib.kalshi_price_check.format_json_summary_block(), used by
.github/workflows/kalshi-price-check.yml to embed the primary JSON
output directly in the job summary/log (a zero-download alternative to
the ZIP-wrapped artifact -- see the workflow's own "ARTIFACT-USABILITY
INVESTIGATION" comment).
"""
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(ROOT, "scripts")


class TestPrintPriceCheckJsonSummary:

    def test_prints_collapsible_json_block(self, tmp_path):
        json_path = tmp_path / "kalshi_price_check.json"
        json_path.write_text(json.dumps([{"ticker": "KXMLBF5-X-TIE", "matchup": "SEA@LAD"}]))
        result = subprocess.run(
            [sys.executable, os.path.join(SCRIPTS_DIR, "print_price_check_json_summary.py"), str(json_path)],
            capture_output=True, text=True, env={**os.environ, "PYTHONPATH": ROOT},
        )
        assert result.returncode == 0
        assert "<details>" in result.stdout
        assert "KXMLBF5-X-TIE" in result.stdout

    def test_empty_records_prints_no_markets_message(self, tmp_path):
        json_path = tmp_path / "kalshi_price_check.json"
        json_path.write_text("[]")
        result = subprocess.run(
            [sys.executable, os.path.join(SCRIPTS_DIR, "print_price_check_json_summary.py"), str(json_path)],
            capture_output=True, text=True, env={**os.environ, "PYTHONPATH": ROOT},
        )
        assert result.returncode == 0
        assert "No markets matched" in result.stdout

    def test_missing_argument_returns_nonzero(self):
        result = subprocess.run(
            [sys.executable, os.path.join(SCRIPTS_DIR, "print_price_check_json_summary.py")],
            capture_output=True, text=True, env={**os.environ, "PYTHONPATH": ROOT},
        )
        assert result.returncode != 0
