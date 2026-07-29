#!/usr/bin/env python3
"""
tests/research/test_inning_result_shadow_ledger_script.py
==============================================================
Model Performance Phase 2A Part 9 -- tests for
scripts/research/build_inning_result_shadow_ledger.py.
"""
import hashlib
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCRIPTS_RESEARCH_DIR = os.path.join(ROOT, "scripts", "research")
sys.path.insert(0, ROOT)
sys.path.insert(0, SCRIPTS_RESEARCH_DIR)

import build_inning_result_shadow_ledger as bl


class TestBuildLedger:

    def test_build_ledger_returns_only_f5_rows_today(self):
        """F3/F7 are not yet ingested by any real fetcher -- every row
        in the real artifact today must be F5 (see module docstring)."""
        ledger = bl.build_ledger()
        scopes = {r["scope"] for r in ledger["rows"]}
        assert scopes <= {"F5"}

    def test_no_row_is_real_money_eligible(self):
        ledger = bl.build_ledger()
        for row in ledger["rows"]:
            assert row["realMoneyEligible"] is False

    def test_rows_only_from_real_ticker_data(self):
        ledger = bl.build_ledger()
        for row in ledger["rows"]:
            assert row["ticker"].startswith("KXMLBF5-")

    def test_projection_method_labeled_not_claimed_as_production(self):
        ledger = bl.build_ledger()
        for row in ledger["rows"]:
            if row.get("projectionMethod") is not None:
                assert row["projectionMethod"] == bl.PROJECTION_METHOD

    def test_deterministic(self):
        r1 = bl.build_ledger()
        r2 = bl.build_ledger()
        assert r1["rows"] == r2["rows"]

    def test_counts_reconcile(self):
        ledger = bl.build_ledger()
        assert ledger["totalRows"] == len(ledger["rows"])


class TestNoProductionMutation:

    def test_build_ledger_does_not_write_any_file(self):
        def _hash(p):
            if not os.path.exists(p):
                return None
            with open(p, "rb") as f:
                return hashlib.sha256(f.read()).hexdigest()

        before = _hash(bl.OUTPUT_PATH)
        bl.build_ledger()
        after = _hash(bl.OUTPUT_PATH)
        assert before == after

    def test_output_path_under_data_research_only(self):
        assert os.path.normpath(bl.OUTPUT_PATH).endswith(
            os.path.normpath("data/research/inning_result_shadow_ledger.json")
        )

    def test_no_slate_or_bets_json_touched(self):
        def _hash(p):
            if not os.path.exists(p):
                return None
            with open(p, "rb") as f:
                return hashlib.sha256(f.read()).hexdigest()

        slate_path = os.path.join(ROOT, "data", "slate.json")
        bets_path = os.path.join(ROOT, "bets.json")
        before = (_hash(slate_path), _hash(bets_path))
        bl.build_ledger()
        after = (_hash(slate_path), _hash(bets_path))
        assert before == after
