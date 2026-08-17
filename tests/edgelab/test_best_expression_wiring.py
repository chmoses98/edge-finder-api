#!/usr/bin/env python3
"""
tests/edgelab/test_best_expression_wiring.py
=================================================
MLB Model Expression Guardrails milestone: coverage for
  1. lib.edgelab.market_comparison.comparison_markets_lookup() -- reduces
     build_comparisons()'s flat output to the {ticker: [otherTickers]}
     shape Recommendation.comparisonMarkets needs.
  2. lib.edgelab.recommendations.build_recommendations_from_pipeline()'s
     new optional `comparison_lookup` parameter -- wires that shape into
     the (previously permanently-empty) comparisonMarkets field.
  3. lib.edgelab.market_comparison.domination_reasons()'s new
     STARTER_ONLY_THESIS_PREFERS_F5 label.

These are two independently-testable, decoupled halves (market_comparison.py
never needs to know about the pipeline artifact format; recommendations.py
never needs a DuckDB session) -- both are exercised together, and each is
also exercised alone, so a break in either half is caught precisely.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import lib.pipeline_artifacts as pipeline_artifacts
from lib.edgelab import schema
from lib.edgelab.market_comparison import (
    HORIZON_F5,
    HORIZON_FULL_GAME,
    comparison_markets_lookup,
    domination_reasons,
)
from lib.edgelab.recommendations import build_recommendations_from_pipeline

DATE = "2026-07-31"


def _game_row(status, **overrides):
    row = {
        "market": "F5_ML_Away", "status": status, "ticker": None,
        "marketTicker": None, "modelProb": None, "kalshiVF": None,
        "calibratedEdgeVsExecutable": None, "confidenceTier": None,
        "rejectionReason": None, "missingFields": [], "evaluationError": None,
    }
    row.update(overrides)
    return row


def _write_recommendations(monkeypatch, tmp_path, games):
    monkeypatch.setattr(pipeline_artifacts, "PIPELINE_ROOT", str(tmp_path))
    pipeline_artifacts.write_stage_artifact("recommendations", DATE, {"date": DATE, "games": games})


# ── comparison_markets_lookup() ──────────────────────────────────────────

class TestComparisonMarketsLookup:
    def test_cluster_of_two_produces_mutual_lookup(self):
        comparisons = [
            {"marketTicker": "ML-A", "clusterId": "c1"},
            {"marketTicker": "F5-A", "clusterId": "c1"},
        ]
        lookup = comparison_markets_lookup(comparisons)
        assert lookup == {"ML-A": ["F5-A"], "F5-A": ["ML-A"]}

    def test_cluster_of_three_excludes_self(self):
        comparisons = [
            {"marketTicker": "ML-A", "clusterId": "c1"},
            {"marketTicker": "F5-A", "clusterId": "c1"},
            {"marketTicker": "RL-A", "clusterId": "c1"},
        ]
        lookup = comparison_markets_lookup(comparisons)
        assert lookup["ML-A"] == ["F5-A", "RL-A"]
        assert "ML-A" not in lookup["ML-A"]

    def test_unclustered_row_never_appears(self):
        comparisons = [{"marketTicker": "SOLO", "clusterId": None}]
        assert comparison_markets_lookup(comparisons) == {}

    def test_single_member_cluster_never_appears(self):
        """A 'cluster' of exactly one market has nothing to compare -- not fabricated as an empty relationship."""
        comparisons = [{"marketTicker": "ONLY", "clusterId": "c1"}]
        assert comparison_markets_lookup(comparisons) == {}

    def test_row_missing_ticker_is_skipped(self):
        comparisons = [
            {"marketTicker": None, "clusterId": "c1"},
            {"marketTicker": "ML-A", "clusterId": "c1"},
        ]
        assert comparison_markets_lookup(comparisons) == {}

    def test_two_independent_clusters_never_cross_contaminate(self):
        comparisons = [
            {"marketTicker": "ML-A", "clusterId": "c1"},
            {"marketTicker": "F5-A", "clusterId": "c1"},
            {"marketTicker": "ML-B", "clusterId": "c2"},
            {"marketTicker": "F5-B", "clusterId": "c2"},
        ]
        lookup = comparison_markets_lookup(comparisons)
        assert lookup["ML-A"] == ["F5-A"]
        assert lookup["ML-B"] == ["F5-B"]


# ── build_recommendations_from_pipeline()'s comparison_lookup param ────

class TestComparisonLookupWiring:
    def test_default_preserves_empty_comparison_markets(self, monkeypatch, tmp_path):
        """Omitting comparison_lookup entirely must be byte-identical to before this parameter existed."""
        ticker = "KXMLBF5-26JUL311810PITCIN-PIT"
        games = [{"gameId": "g1", "away": {"abbr": "PIT"}, "home": {"abbr": "CIN"}, "status": "Scheduled",
                  "marketLedger": [_game_row("Accepted", ticker=ticker, modelProb=63.0, kalshiVF=57.0)]}]
        _write_recommendations(monkeypatch, tmp_path, games)
        records, _ = build_recommendations_from_pipeline(DATE, "run1", {})
        assert records[0]["comparisonMarkets"] == []

    def test_lookup_populates_comparison_markets(self, monkeypatch, tmp_path):
        ticker = "KXMLBF5-26JUL311810PITCIN-PIT"
        games = [{"gameId": "g1", "away": {"abbr": "PIT"}, "home": {"abbr": "CIN"}, "status": "Scheduled",
                  "marketLedger": [_game_row("Accepted", ticker=ticker, modelProb=63.0, kalshiVF=57.0)]}]
        _write_recommendations(monkeypatch, tmp_path, games)
        lookup = {ticker: ["KXMLBGAME-26JUL311810PITCIN-PIT"]}
        records, _ = build_recommendations_from_pipeline(DATE, "run1", {}, comparison_lookup=lookup)
        assert records[0]["comparisonMarkets"] == ["KXMLBGAME-26JUL311810PITCIN-PIT"]
        assert schema.validate_record("recommendation", records[0]) == []

    def test_ticker_not_in_lookup_still_gets_empty_list(self, monkeypatch, tmp_path):
        ticker = "KXMLBF5-26JUL311810PITCIN-PIT"
        games = [{"gameId": "g1", "away": {"abbr": "PIT"}, "home": {"abbr": "CIN"}, "status": "Scheduled",
                  "marketLedger": [_game_row("Accepted", ticker=ticker, modelProb=63.0, kalshiVF=57.0)]}]
        _write_recommendations(monkeypatch, tmp_path, games)
        records, _ = build_recommendations_from_pipeline(DATE, "run1", {}, comparison_lookup={"SOME-OTHER-TICKER": ["X"]})
        assert records[0]["comparisonMarkets"] == []

    def test_null_ticker_row_never_looked_up(self, monkeypatch, tmp_path):
        """A row with no ticker at all (e.g. Missing Data) must not crash on a comparison_lookup.get(None, ...) lookup."""
        games = [{"gameId": "g1", "away": {"abbr": "PIT"}, "home": {"abbr": "CIN"}, "status": "Scheduled",
                  "marketLedger": [_game_row("Missing Data", missingFields=["x"])]}]
        _write_recommendations(monkeypatch, tmp_path, games)
        records, _ = build_recommendations_from_pipeline(DATE, "run1", {}, comparison_lookup={None: ["should-never-match"]})
        assert records[0]["comparisonMarkets"] == []


# ── domination_reasons()'s STARTER_ONLY_THESIS_PREFERS_F5 label ─────────

class TestStarterOnlyThesisPrefersF5:
    def test_label_applied_when_f5_dominates_full_game_same_team_lower_risk(self):
        candidate = {"horizon": HORIZON_FULL_GAME, "team": "AWAY", "estimatedEdge": 2.0, "dataQuality": "full"}
        dominator = {"horizon": HORIZON_F5, "team": "AWAY", "estimatedEdge": 3.0, "dataQuality": "full"}
        reasons = domination_reasons(candidate, dominator)
        assert "STARTER_ONLY_THESIS_PREFERS_F5" in reasons
        assert "LOWER_MATERIAL_RISK" in reasons   # always appended alongside, never instead of

    def test_label_absent_when_teams_differ(self):
        candidate = {"horizon": HORIZON_FULL_GAME, "team": "HOME", "estimatedEdge": 2.0, "dataQuality": "full"}
        dominator = {"horizon": HORIZON_F5, "team": "AWAY", "estimatedEdge": 3.0, "dataQuality": "full"}
        reasons = domination_reasons(candidate, dominator)
        assert "STARTER_ONLY_THESIS_PREFERS_F5" not in reasons

    def test_label_absent_when_dominator_is_not_f5(self):
        candidate = {"horizon": HORIZON_FULL_GAME, "team": "AWAY", "estimatedEdge": 2.0, "dataQuality": "full"}
        dominator = {"horizon": HORIZON_FULL_GAME, "team": "AWAY", "estimatedEdge": 3.0, "dataQuality": "full"}
        reasons = domination_reasons(candidate, dominator)
        assert "STARTER_ONLY_THESIS_PREFERS_F5" not in reasons

    def test_label_absent_when_candidate_is_not_full_game(self):
        candidate = {"horizon": HORIZON_F5, "team": "AWAY", "estimatedEdge": 2.0, "dataQuality": "full"}
        dominator = {"horizon": HORIZON_F5, "team": "AWAY", "estimatedEdge": 3.0, "dataQuality": "full"}
        reasons = domination_reasons(candidate, dominator)
        assert "STARTER_ONLY_THESIS_PREFERS_F5" not in reasons
