#!/usr/bin/env python3
"""
tests/research/test_inning_result_shadow_comparison_script.py
===================================================================
Model Performance Phase 2A Part 11 -- tests for
scripts/research/build_inning_result_shadow_comparison.py.
"""
import hashlib
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCRIPTS_RESEARCH_DIR = os.path.join(ROOT, "scripts", "research")
sys.path.insert(0, ROOT)
sys.path.insert(0, SCRIPTS_RESEARCH_DIR)

import build_inning_result_shadow_comparison as bc


class TestCompareRow:

    def test_recommendation_change_flag_matches_status_difference(self):
        row = {
            "date": "2026-07-24", "gameId": "G1", "scope": "F5", "outcome": "Away",
            "ticker": "KXMLBF5-X-AWY",
            "legacyConditionalProb": 0.50, "canonicalModelProb": 0.40, "yesAsk": 0.38,
        }
        r = bc.compare_row(row)
        assert r["recommendationWouldChange"] == (r["legacyStatus"] != r["canonicalShadowStatus"])

    def test_missing_data_returns_none(self):
        assert bc.compare_row({"date": "d", "gameId": "g", "scope": "F5", "outcome": "Away",
                                "ticker": "t", "legacyConditionalProb": None,
                                "canonicalModelProb": 0.4, "yesAsk": 0.4}) is None

    def test_difference_pct_points_sign(self):
        row = {
            "date": "2026-07-24", "gameId": "G1", "scope": "F5", "outcome": "Away",
            "ticker": "KXMLBF5-X-AWY",
            "legacyConditionalProb": 0.55, "canonicalModelProb": 0.45, "yesAsk": 0.40,
        }
        r = bc.compare_row(row)
        assert r["differencePctPoints"] < 0  # canonical (0.45) < legacy (0.55)


class TestConfidenceFromEdgeReplica:

    def test_below_paper_threshold_is_none(self):
        assert bc._confidence_from_edge(0.5) is None

    def test_high_threshold(self):
        assert bc._confidence_from_edge(3.5) == "HIGH"

    def test_medium_threshold(self):
        assert bc._confidence_from_edge(2.0) == "MEDIUM"

    def test_paper_threshold(self):
        assert bc._confidence_from_edge(1.2) == "PAPER"

    def test_none_edge_returns_none(self):
        assert bc._confidence_from_edge(None) is None


class TestBuildComparison:

    def test_only_f5_away_home_included(self):
        comparison = bc.build_comparison()
        for row in comparison["rows"]:
            assert row["scope"] == "F5"
            assert row["outcome"] in ("Away", "Home")

    def test_counts_reconcile(self):
        comparison = bc.build_comparison()
        assert comparison["totalComparisons"] == len(comparison["rows"])
        assert comparison["countRecommendationWouldChange"] == sum(
            1 for r in comparison["rows"] if r["recommendationWouldChange"]
        )

    def test_deterministic(self):
        c1 = bc.build_comparison()
        c2 = bc.build_comparison()
        assert c1["rows"] == c2["rows"]

    def test_note_documents_research_only_status(self):
        comparison = bc.build_comparison()
        assert "RESEARCH-ONLY" in comparison["note"]
        assert "never" in comparison["note"].lower() or "does not" in comparison["note"].lower()


class TestNoProductionMutation:

    def test_does_not_write_any_file(self):
        def _hash(p):
            if not os.path.exists(p):
                return None
            with open(p, "rb") as f:
                return hashlib.sha256(f.read()).hexdigest()

        before = _hash(bc.OUTPUT_PATH)
        bc.build_comparison()
        after = _hash(bc.OUTPUT_PATH)
        assert before == after

    def test_output_path_under_data_research_only(self):
        assert os.path.normpath(bc.OUTPUT_PATH).endswith(
            os.path.normpath("data/research/inning_result_shadow_comparison.json")
        )
