#!/usr/bin/env python3
"""
tests/edgelab/test_build_recommendations_script.py
=======================================================
End-to-end coverage for scripts/edgelab/build_recommendations.py's
live best-expression wiring (MLB Model Expression Guardrails milestone,
review pass): ModelEvaluations are now built and written BEFORE
Recommendations specifically so lib.edgelab.market_comparison's
clustering sees the day's COMPLETE candidate market set, and the
resulting comparisonStatus/dominantMarketTicker/dominationReasons/
comparisonMarkets are annotated onto each Recommendation record on disk.

Runs the real CLI script's main() against a tmp_path-isolated
data/ tree (monkeypatch.chdir) -- no mocking of
lib.edgelab.market_comparison or lib.edgelab.analytics.open_session,
so this proves the actual reordered wiring works, not just that the
pieces are individually callable.
"""
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCRIPTS_DIR = os.path.join(ROOT, "scripts", "edgelab")
sys.path.insert(0, ROOT)
sys.path.insert(0, SCRIPTS_DIR)

import lib.pipeline_artifacts as pipeline_artifacts
from lib.edgelab import storage
from lib.edgelab import schema

import build_recommendations  # noqa: E402

DATE = "2026-08-01"


def _game_row(status, market, **overrides):
    row = {
        "market": market, "status": status, "ticker": None,
        "marketTicker": None, "modelProb": None, "kalshiVF": None,
        "calibratedEdgeVsExecutable": None, "confidenceTier": None,
        "rejectionReason": None, "missingFields": [], "evaluationError": None,
        "lineupDataQuality": "full", "lineupConfirmedOfficial": True, "lineupAdjApplied": True,
    }
    row.update(overrides)
    return row


def _seed_pipeline_artifact(games):
    # PIPELINE_ROOT (lib/pipeline_artifacts.py) and EDGELAB_ROOT
    # (lib/edgelab/storage.py, lib/edgelab/analytics.py) are all relative
    # paths ("data/pipeline", "data/edgelab") -- every caller here relies
    # SOLELY on monkeypatch.chdir(tmp_path) (never a raw module-attribute
    # assignment, which wouldn't auto-revert at teardown and would leak a
    # deleted tmp_path into every later test in the same pytest process).
    pipeline_artifacts.write_stage_artifact("recommendations", DATE, {"date": DATE, "games": games})


def _same_team_duplicate_thesis_game(*, ml_edge=2.0, f5_edge=3.0, market_order=None):
    """
    One game: ML_Away and F5_ML_Away for the SAME team ('PIT') --
    market_comparison.py's cluster_key() groups these into one WIN-thesis
    cluster (alternate horizons of the identical 'PIT wins' side), so
    whichever has the higher edge should be BEST_EXPRESSION and the
    other DOMINATED_MARKET.
    """
    ml = _game_row("Accepted", "ML_Away", ticker="KXMLBGAME-26AUG011810PITCIN-PIT",
                    modelProb=60.0, kalshiVF=50.0, calibratedEdgeVsExecutable=ml_edge, confidenceTier="MEDIUM")
    f5 = _game_row("Accepted", "F5_ML_Away", ticker="KXMLBF5-26AUG011810PITCIN-PIT",
                    modelProb=63.0, kalshiVF=53.0, calibratedEdgeVsExecutable=f5_edge, confidenceTier="MEDIUM")
    markets = [ml, f5] if market_order is None else ([ml, f5] if market_order == "ml_first" else [f5, ml])
    return {
        "gameId": "g1", "away": {"abbr": "PIT"}, "home": {"abbr": "CIN"}, "status": "Scheduled",
        "marketLedger": markets,
    }


def _run(tmp_path, monkeypatch, games):
    monkeypatch.chdir(tmp_path)
    _seed_pipeline_artifact(games)
    monkeypatch.setattr(sys, "argv", ["build_recommendations.py", "--date", DATE])
    rc = build_recommendations.main()
    assert rc == 0
    recs_path = storage.partition_path("recommendations", DATE)
    return list(storage.read_records(recs_path))


def _rec(records, market_name):
    for r in records:
        if r.get("marketName") == market_name:
            return r
    raise KeyError(f"{market_name!r} not found in {[r.get('marketName') for r in records]}")


class TestBestExpressionAnnotation:
    def test_higher_edge_expression_is_best(self, tmp_path, monkeypatch):
        records = _run(tmp_path, monkeypatch, [_same_team_duplicate_thesis_game(ml_edge=2.0, f5_edge=3.0)])
        f5 = _rec(records, "F5_ML_Away")
        assert f5["comparisonStatus"] == "BEST_EXPRESSION"

    def test_lower_edge_expression_is_dominated_with_reasons(self, tmp_path, monkeypatch):
        records = _run(tmp_path, monkeypatch, [_same_team_duplicate_thesis_game(ml_edge=2.0, f5_edge=3.0)])
        ml = _rec(records, "ML_Away")
        assert ml["comparisonStatus"] == "DOMINATED_MARKET"
        assert ml["dominantMarketTicker"] == "KXMLBF5-26AUG011810PITCIN-PIT"
        assert "HIGHER_EV" in ml["dominationReasons"]
        assert "INFERIOR_NET_EV" in ml["dominationReasons"]
        assert "DUPLICATE_THESIS" in ml["dominationReasons"]

    def test_comparison_markets_populated_both_directions(self, tmp_path, monkeypatch):
        records = _run(tmp_path, monkeypatch, [_same_team_duplicate_thesis_game()])
        ml = _rec(records, "ML_Away")
        f5 = _rec(records, "F5_ML_Away")
        assert ml["comparisonMarkets"] == ["KXMLBF5-26AUG011810PITCIN-PIT"]
        assert f5["comparisonMarkets"] == ["KXMLBGAME-26AUG011810PITCIN-PIT"]

    def test_dominated_row_is_not_removed_from_output(self, tmp_path, monkeypatch):
        """Do NOT force exactly one recommendation per game -- the dominated candidate must still appear."""
        records = _run(tmp_path, monkeypatch, [_same_team_duplicate_thesis_game()])
        names = {r["marketName"] for r in records}
        assert {"ML_Away", "F5_ML_Away"} <= names

    def test_every_record_still_schema_valid(self, tmp_path, monkeypatch):
        records = _run(tmp_path, monkeypatch, [_same_team_duplicate_thesis_game()])
        for r in records:
            assert schema.validate_record("recommendation", r) == []


class TestOrderInvariance:
    def test_result_invariant_to_marketledger_row_order(self, tmp_path, monkeypatch):
        records_a = _run(tmp_path, monkeypatch, [_same_team_duplicate_thesis_game(market_order="ml_first")])
        # Fresh tmp_path/isolated run with the SAME game but reversed row order.
        tmp_path_2 = tmp_path / "run2"
        tmp_path_2.mkdir()
        records_b = _run(tmp_path_2, monkeypatch, [_same_team_duplicate_thesis_game(market_order="f5_first")])

        status_a = {r["marketName"]: r["comparisonStatus"] for r in records_a}
        status_b = {r["marketName"]: r["comparisonStatus"] for r in records_b}
        assert status_a == status_b

        dom_a = {r["marketName"]: r["dominantMarketTicker"] for r in records_a}
        dom_b = {r["marketName"]: r["dominantMarketTicker"] for r in records_b}
        assert dom_a == dom_b


class TestIndependentTheses:
    def test_genuinely_independent_markets_both_carry_no_domination(self, tmp_path, monkeypatch):
        """Two markets sharing nothing (different games) must both remain fully actionable, un-dominated."""
        game1 = _same_team_duplicate_thesis_game()
        game1["marketLedger"] = [game1["marketLedger"][1]]  # keep only F5_ML_Away
        nrfi = _game_row("Accepted", "NRFI", ticker="KXMLBRFI-26AUG011005SEAOAK",
                          modelProb=58.0, kalshiVF=50.0, calibratedEdgeVsExecutable=2.5, confidenceTier="MEDIUM")
        game2 = {"gameId": "g2", "away": {"abbr": "SEA"}, "home": {"abbr": "OAK"}, "status": "Scheduled",
                  "marketLedger": [nrfi]}
        records = _run(tmp_path, monkeypatch, [game1, game2])
        f5 = _rec(records, "F5_ML_Away")
        nrfi_row = _rec(records, "NRFI")
        assert f5["comparisonStatus"] != "DOMINATED_MARKET"
        assert nrfi_row["comparisonStatus"] != "DOMINATED_MARKET"
        assert nrfi_row["dominantMarketTicker"] is None


class TestGracefulDegradation:
    def test_comparison_engine_failure_never_blocks_recommendations(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _seed_pipeline_artifact([_same_team_duplicate_thesis_game()])
        monkeypatch.setattr(sys, "argv", ["build_recommendations.py", "--date", DATE])

        def _failing_lookup():
            return {}, {}, 0, ["best-expression comparison unavailable: RuntimeError: simulated"]
        monkeypatch.setattr(build_recommendations, "build_comparison_lookups", _failing_lookup)

        rc = build_recommendations.main()
        assert rc == 0
        records = list(storage.read_records(storage.partition_path("recommendations", DATE)))
        assert len(records) >= 2
        f5 = _rec(records, "F5_ML_Away")
        assert f5["comparisonStatus"] is None
        assert f5["comparisonMarkets"] == []

        run_records = list(storage.read_records(storage.partition_path("research_runs", DATE)))
        assert any("best-expression comparison unavailable" in w for r in run_records for w in r.get("warnings", []))

    def test_build_comparison_lookups_itself_catches_a_real_exception(self, tmp_path, monkeypatch):
        """Direct test of the try/except in build_comparison_lookups() -- not just that main() tolerates a pre-degraded result."""
        monkeypatch.chdir(tmp_path)

        def _raise(*a, **k):
            raise RuntimeError("boom")
        monkeypatch.setattr(build_recommendations, "build_comparisons", _raise)

        lookup, annotations, rows_evaluated, warnings = build_recommendations.build_comparison_lookups()
        assert lookup == {}
        assert annotations == {}
        assert rows_evaluated == 0
        assert len(warnings) == 1
        assert "RuntimeError" in warnings[0]


class TestNoAutomaticWagering:
    def test_script_never_writes_a_placed_bet(self, tmp_path, monkeypatch):
        _run(tmp_path, monkeypatch, [_same_team_duplicate_thesis_game()])
        bets_path = storage.singleton_path("bets", "bets.jsonl")
        assert list(storage.read_records(bets_path)) == []


class TestFeeCalculationUnaffected:
    def test_recommendation_edge_fields_copied_verbatim_not_recomputed(self, tmp_path, monkeypatch):
        """The comparison annotation must never alter estimatedEdge/modelFairProbability -- those still come straight from the marketLedger row, exactly once."""
        records = _run(tmp_path, monkeypatch, [_same_team_duplicate_thesis_game(ml_edge=2.0, f5_edge=3.0)])
        ml = _rec(records, "ML_Away")
        f5 = _rec(records, "F5_ML_Away")
        assert ml["estimatedEdge"] == 2.0
        assert f5["estimatedEdge"] == 3.0
