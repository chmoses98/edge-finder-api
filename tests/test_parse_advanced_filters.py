#!/usr/bin/env python3
"""
tests/test_parse_advanced_filters.py
=========================================
Tests for scripts/parse_advanced_filters.py -- the consolidation point
for the 6 rare check_kalshi_prices.py filters (date, outcome,
participant, ticker, event_ticker, series_ticker) that no longer have
individual workflow_dispatch inputs, because GitHub Actions hard-caps
workflow_dispatch at 10 top-level inputs and this workflow previously
defined 15.
"""
import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(ROOT, "scripts")
sys.path.insert(0, SCRIPTS_DIR)

import parse_advanced_filters as paf


class TestParsePure:

    def test_blank_input_returns_empty(self):
        assert paf.parse("") == []
        assert paf.parse("   ") == []
        assert paf.parse(None) == []

    def test_single_key(self):
        assert paf.parse('{"date": "2026-07-30"}') == ["--date", "2026-07-30"]

    def test_multiple_keys_in_fixed_order(self):
        result = paf.parse(json.dumps({"ticker": "KXMLBF5-X-TIE", "date": "2026-07-30"}))
        assert result == ["--date", "2026-07-30", "--ticker", "KXMLBF5-X-TIE"]

    def test_all_six_keys(self):
        payload = {
            "date": "2026-07-30", "outcome": "Tie", "participant": "Ohtani",
            "ticker": "KXMLBF5-X-TIE", "event_ticker": "KXMLBF5-X", "series_ticker": "KXMLBF5",
        }
        result = paf.parse(json.dumps(payload))
        assert result == [
            "--date", "2026-07-30", "--outcome", "Tie", "--participant", "Ohtani",
            "--ticker", "KXMLBF5-X-TIE", "--event-ticker", "KXMLBF5-X",
            "--series-ticker", "KXMLBF5",
        ]

    def test_empty_string_value_is_omitted(self):
        assert paf.parse(json.dumps({"date": "", "outcome": "Tie"})) == ["--outcome", "Tie"]

    def test_invalid_json_raises(self):
        with pytest.raises(ValueError, match="not valid JSON"):
            paf.parse("{not json")

    def test_non_object_json_raises(self):
        with pytest.raises(ValueError, match="must be a JSON object"):
            paf.parse('["date", "2026-07-30"]')

    def test_unrecognized_key_raises(self):
        with pytest.raises(ValueError, match="unrecognized key"):
            paf.parse(json.dumps({"team": "Yankees"}))

    def test_unrecognized_key_names_the_bad_key(self):
        with pytest.raises(ValueError, match="max_results"):
            paf.parse(json.dumps({"max_results": 1000}))


class TestParseCLI:

    def _run(self, arg):
        return subprocess.run(
            [sys.executable, os.path.join(SCRIPTS_DIR, "parse_advanced_filters.py"), arg],
            capture_output=True, text=True,
        )

    def test_cli_no_argument_is_blank(self):
        result = subprocess.run(
            [sys.executable, os.path.join(SCRIPTS_DIR, "parse_advanced_filters.py")],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert result.stdout == ""

    def test_cli_prints_one_token_per_line(self):
        result = self._run('{"date": "2026-07-30", "outcome": "Tie"}')
        assert result.returncode == 0
        assert result.stdout.splitlines() == ["--date", "2026-07-30", "--outcome", "Tie"]

    def test_cli_invalid_json_exits_nonzero_with_error_marker(self):
        result = self._run("{bad")
        assert result.returncode != 0
        assert "::error::" in result.stderr
        assert "not valid JSON" in result.stderr

    def test_cli_unrecognized_key_exits_nonzero(self):
        result = self._run(json.dumps({"bogus_key": "x"}))
        assert result.returncode != 0
        assert "unrecognized key" in result.stderr
