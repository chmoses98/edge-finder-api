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


class TestUniversalPersistenceDiscoveryWiring:
    """
    Universal ModelEvaluation Persistence mission: build_recommendations.py's
    live call site (lib.edgelab.kalshi_discovery_bridge.load_discovery_lookup
    -> lib.edgelab.model_evaluation.extend_full_universe_evaluations)
    actually reads data/kalshi/discovery/<date>.json and persists a real
    ModelEvaluation row for a family the 11-REQUIRED_MARKETS pipeline
    never runs -- not just unit-tested in isolation, but exercised
    through the real CLI entry point end to end.
    """

    def _seed_discovery(self, tmp_path, ticker, marketFamily="winning_margin", fairProbabilityPct=61.234,
                         status="SUPPORTED", title="BOS wins by 1+?", line=0.5):
        discovery_dir = os.path.join(str(tmp_path), "data", "kalshi", "discovery")
        os.makedirs(discovery_dir, exist_ok=True)
        with open(os.path.join(discovery_dir, f"{DATE}.json"), "w") as f:
            json.dump({
                "date": DATE, "generatedAt": "t",
                "contracts": [{
                    "ticker": ticker, "marketFamily": marketFamily, "marketTitle": title, "line": line,
                    "modelSupportStatus": status, "fairProbabilityPct": fairProbabilityPct,
                    "impliedProbabilityPct": 55.0, "unsupportedReason": None,
                    "rawEdgePct": 6.234, "expectedProfitPerDollar": 0.11,
                }],
            }, f)

    def _seed_observation(self, ticker, marketFamily="winning_margin", threshold=0.5):
        obs = {
            "marketTicker": ticker, "seriesTicker": ticker.split("-", 1)[0], "gameId": "g1",
            "eventTicker": "E1", "marketFamily": marketFamily, "threshold": threshold, "runId": "obs-run",
            "provenance": {"sourceFile": "x", "sourceKey": "y", "capturedAt": "t", "sourceSystem": "s"},
        }
        storage.write_all_records(storage.partition_path("observations", DATE, compressed=True), [obs])

    def test_discovery_backed_row_persisted_through_real_cli_entry_point(self, tmp_path, monkeypatch):
        ticker = "KXMLBSPREAD-T-BOS1"
        monkeypatch.chdir(tmp_path)
        self._seed_discovery(tmp_path, ticker)
        self._seed_observation(ticker)
        _seed_pipeline_artifact([])  # no 11-REQUIRED_MARKETS games this date -- pure extension path
        monkeypatch.setattr(sys, "argv", ["build_recommendations.py", "--date", DATE])
        rc = build_recommendations.main()
        assert rc == 0

        evals = list(storage.read_records(storage.partition_path("model_evaluations", DATE)))
        matches = [e for e in evals if e.get("marketTicker") == ticker]
        assert len(matches) == 1
        row = matches[0]
        assert row["modelFairProbability"] == 61.234
        assert row["qualityTier"] == "RESEARCH_ONLY"
        assert row["source"] == "kalshi_discovery_extension"
        assert schema.validate_record("model_evaluation", row) == []

        # Recommendation-eligibility governance is untouched: this
        # ticker's Recommendation row (built entirely separately, by
        # lib.edgelab.recommendations.extend_with_full_universe, which
        # this mission never modifies) never carries a "recommended"
        # status -- only the pre-existing NOT_EVALUATED/
        # INSUFFICIENT_MODEL_SUPPORT/BET_PLACED extension statuses, none
        # of which this mission's discovery-backed ModelEvaluation
        # persistence can promote it into.
        recs = list(storage.read_records(storage.partition_path("recommendations", DATE)))
        rec_matches = [r for r in recs if r.get("marketTicker") == ticker]
        assert len(rec_matches) == 1
        assert rec_matches[0]["status"] in ("NOT_EVALUATED", "INSUFFICIENT_MODEL_SUPPORT")

    def test_no_discovery_file_present_degrades_to_pre_existing_behavior(self, tmp_path, monkeypatch):
        """A date discovery hasn't run for at all -- build_recommendations.py must still succeed exactly as before this mission (empty lookup, no crash)."""
        ticker = "KXMLBTOTAL-T-9"
        monkeypatch.chdir(tmp_path)
        self._seed_observation(ticker, marketFamily="game_total", threshold=9)
        _seed_pipeline_artifact([])
        monkeypatch.setattr(sys, "argv", ["build_recommendations.py", "--date", DATE])
        rc = build_recommendations.main()
        assert rc == 0
        evals = list(storage.read_records(storage.partition_path("model_evaluations", DATE)))
        matches = [e for e in evals if e.get("marketTicker") == ticker]
        assert len(matches) == 1
        assert matches[0]["modelFairProbability"] is None


class TestHitterBoardBridgeWiring:
    """
    Hitter Prop Methodology Repair mission: build_recommendations.py's
    call site also merges lib.edgelab.hitter_board_bridge.load_hitter_board_lookup
    into the same discovery_lookup -- exercised through the real CLI
    entry point end to end, mirroring TestUniversalPersistenceDiscoveryWiring's
    approach for the non-hitter bridge.
    """

    def _seed_hitter_board(self, tmp_path, ticker, model_prob=0.42, exec_price=0.35,
                            family="hitter_hits", threshold=1, title="Player: 1+ hits?"):
        board_dir = os.path.join(str(tmp_path), "data", "pipeline", DATE)
        os.makedirs(board_dir, exist_ok=True)
        row = {
            "marketTicker": ticker, "marketFamily": family, "threshold": threshold,
            "naturalLanguageMarket": title, "modelProbability": model_prob,
            "executableKalshiPrice": exec_price, "rawProbabilityEdge": round(model_prob - exec_price, 4),
            "expectedValuePerDollar": 0.2, "projectionStatus": "PROJECTED", "projectionStatusReason": None,
        }
        with open(os.path.join(board_dir, "hitter_projection_board.json"), "w") as f:
            json.dump({"data": {"rows": [row], "hitterSummaries": [], "summary": {}},
                       "meta": {"stage": "hitter_projection_board", "producedBy": "scripts/build_hitter_projection_board.py"}}, f)

    def _seed_observation(self, ticker, marketFamily="hitter_hits", threshold=1):
        obs = {
            "marketTicker": ticker, "seriesTicker": ticker.split("-", 1)[0], "gameId": "g1",
            "eventTicker": "E1", "marketFamily": marketFamily, "threshold": threshold, "runId": "obs-run",
            "provenance": {"sourceFile": "x", "sourceKey": "y", "capturedAt": "t", "sourceSystem": "s"},
        }
        storage.write_all_records(storage.partition_path("observations", DATE, compressed=True), [obs])

    def test_hitter_board_backed_row_persisted_through_real_cli_entry_point(self, tmp_path, monkeypatch):
        ticker = "KXMLBHIT-T-MTROUT27-1"
        monkeypatch.chdir(tmp_path)
        self._seed_hitter_board(tmp_path, ticker)
        self._seed_observation(ticker)
        _seed_pipeline_artifact([])
        monkeypatch.setattr(sys, "argv", ["build_recommendations.py", "--date", DATE])
        rc = build_recommendations.main()
        assert rc == 0

        evals = list(storage.read_records(storage.partition_path("model_evaluations", DATE)))
        matches = [e for e in evals if e.get("marketTicker") == ticker]
        assert len(matches) == 1
        row = matches[0]
        assert row["modelFairProbability"] == 42.0
        assert row["qualityTier"] == "RESEARCH_ONLY"
        assert row["modelSource"] == "lib.research.hitter_board_builder.build_hitter_projection_rows"
        assert schema.validate_record("model_evaluation", row) == []

        recs = list(storage.read_records(storage.partition_path("recommendations", DATE)))
        rec_matches = [r for r in recs if r.get("marketTicker") == ticker]
        assert len(rec_matches) == 1
        assert rec_matches[0]["status"] in ("NOT_EVALUATED", "INSUFFICIENT_MODEL_SUPPORT")

    def test_hitter_and_nonhitter_bridges_coexist_without_collision(self, tmp_path, monkeypatch):
        hitter_ticker = "KXMLBHIT-T-MTROUT27-1"
        spread_ticker = "KXMLBSPREAD-T-BOS1"
        monkeypatch.chdir(tmp_path)
        self._seed_hitter_board(tmp_path, hitter_ticker)
        self._seed_observation(hitter_ticker)

        discovery_dir = os.path.join(str(tmp_path), "data", "kalshi", "discovery")
        os.makedirs(discovery_dir, exist_ok=True)
        with open(os.path.join(discovery_dir, f"{DATE}.json"), "w") as f:
            json.dump({"date": DATE, "generatedAt": "t", "contracts": [{
                "ticker": spread_ticker, "marketFamily": "winning_margin", "marketTitle": "BOS wins by 1+?",
                "line": 0.5, "modelSupportStatus": "SUPPORTED", "fairProbabilityPct": 61.234,
                "impliedProbabilityPct": 55.0, "unsupportedReason": None, "rawEdgePct": 6.234,
                "expectedProfitPerDollar": 0.11,
            }]}, f)
        obs2 = {
            "marketTicker": spread_ticker, "seriesTicker": "KXMLBSPREAD", "gameId": "g1",
            "eventTicker": "E1", "marketFamily": "winning_margin", "threshold": 0.5, "runId": "obs-run",
            "provenance": {"sourceFile": "x", "sourceKey": "y", "capturedAt": "t", "sourceSystem": "s"},
        }
        storage.append_records(storage.partition_path("observations", DATE, compressed=True), [obs2], "marketTicker")

        _seed_pipeline_artifact([])
        monkeypatch.setattr(sys, "argv", ["build_recommendations.py", "--date", DATE])
        rc = build_recommendations.main()
        assert rc == 0

        evals = list(storage.read_records(storage.partition_path("model_evaluations", DATE)))
        by_ticker = {e["marketTicker"]: e for e in evals if e.get("marketTicker") in (hitter_ticker, spread_ticker)}
        assert len(by_ticker) == 2
        assert by_ticker[hitter_ticker]["modelSource"] == "lib.research.hitter_board_builder.build_hitter_projection_rows"
        assert by_ticker[spread_ticker]["modelSource"] == "lib.kalshi_probability_adapters.adapt_contract"
