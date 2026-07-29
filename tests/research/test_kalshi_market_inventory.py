#!/usr/bin/env python3
"""
tests/research/test_kalshi_market_inventory.py
===================================================
Model Performance Phase 1 -- tests for
scripts/research/build_kalshi_market_inventory.py.
"""
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCRIPTS_RESEARCH_DIR = os.path.join(ROOT, "scripts", "research")
sys.path.insert(0, ROOT)
sys.path.insert(0, SCRIPTS_RESEARCH_DIR)

import build_kalshi_market_inventory as inv


class TestInventoryBuild:

    def test_build_inventory_returns_one_entry_per_input_market(self):
        result = inv.build_inventory()
        assert result["totalMarketsInLatestSnapshot"] == len(result["entries"])

    def test_no_entry_has_null_classification_status(self):
        result = inv.build_inventory()
        for entry in result["entries"]:
            assert entry["classificationStatus"] is not None

    def test_no_entry_silently_missing_market_ticker(self):
        result = inv.build_inventory()
        for entry in result["entries"]:
            assert entry["marketTicker"], "every entry must retain its raw market ticker"

    def test_confirmed_absent_series_documented(self):
        result = inv.build_inventory()
        assert "F3" in result["confirmedAbsentSeries"]
        assert "F7" in result["confirmedAbsentSeries"]
        assert "KXMLBF3" not in result["seriesTickersObservedInLatestSnapshot"]
        assert "KXMLBF7" not in result["seriesTickersObservedInLatestSnapshot"]

    def test_f5_tie_marked_as_dead_data_path_not_consumed(self):
        result = inv.build_inventory()
        tie_rows = [e for e in result["entries"] if e["family"] == "inning_result" and e["outcome"] == "Tie"]
        assert len(tie_rows) > 0, "expected at least one F5 Tie market in the real snapshot"
        for row in tie_rows:
            assert row["productionConsumptionStatus"] == "data_captured_never_evaluated"
            assert row["modelSupportStatus"] == "not_supported_dead_data_path"

    def test_full_game_never_marked_three_way(self):
        result = inv.build_inventory()
        game_rows = [e for e in result["entries"] if e["family"] == "game_result"]
        assert len(game_rows) > 0
        for row in game_rows:
            assert row["isThreeWay"] is False

    def test_no_bid_ask_gap_documented_not_fabricated(self):
        """
        This repo's own snapshot format does not capture NO-side
        pricing -- the inventory must record noBid/noAsk as None
        (unknown), never guess or derive a fabricated value.
        """
        result = inv.build_inventory()
        for entry in result["entries"][:20]:
            assert entry["noBid"] is None
            assert entry["noAsk"] is None

    def test_settlement_rules_text_documented_as_unavailable(self):
        result = inv.build_inventory()
        for entry in result["entries"][:20]:
            assert entry["settlementRulesText"] is None
            assert entry["settlementRulesSource"] == "inferred_from_ticker_structure_not_kalshi_rules_field"

    def test_deterministic_given_same_snapshot(self):
        r1 = inv.build_inventory()
        r2 = inv.build_inventory()
        assert r1["entries"] == r2["entries"]
        assert r1["totalMarketsInLatestSnapshot"] == r2["totalMarketsInLatestSnapshot"]

    def test_note_field_documents_research_only_status(self):
        result = inv.build_inventory()
        assert "RESEARCH-ONLY" in result["note"]
        assert "no live Kalshi API call" in result["note"]


class TestNoProductionMutation:

    def test_build_inventory_does_not_write_any_file(self, tmp_path, monkeypatch):
        """
        build_inventory() itself (as opposed to main()) must be a pure
        read-and-return function -- only main() writes to disk, and
        only to the research output path.
        """
        import hashlib

        def _hash_file(p):
            with open(p, "rb") as f:
                return hashlib.sha256(f.read()).hexdigest()

        before = None
        if os.path.exists(inv.OUTPUT_PATH):
            before = _hash_file(inv.OUTPUT_PATH)

        inv.build_inventory()

        after = None
        if os.path.exists(inv.OUTPUT_PATH):
            after = _hash_file(inv.OUTPUT_PATH)
        assert before == after, "build_inventory() must not write to disk"

    def test_output_path_is_under_data_research_only(self):
        assert os.path.normpath(inv.OUTPUT_PATH).endswith(
            os.path.normpath("data/research/kalshi_mlb_market_inventory.json")
        )

    def test_no_slate_json_or_bets_json_touched(self):
        import hashlib

        def _hash(p):
            if not os.path.exists(p):
                return None
            with open(p, "rb") as f:
                return hashlib.sha256(f.read()).hexdigest()

        slate_path = os.path.join(ROOT, "data", "slate.json")
        bets_path = os.path.join(ROOT, "bets.json")
        before_slate, before_bets = _hash(slate_path), _hash(bets_path)
        inv.build_inventory()
        after_slate, after_bets = _hash(slate_path), _hash(bets_path)
        assert before_slate == after_slate
        assert before_bets == after_bets
