#!/usr/bin/env python3
"""
tests/edgelab/test_research_reports.py
============================================
Coverage for lib/edgelab/research_reports.py -- the A-H research report
generators over lib.edgelab.research_dataset rows.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab import research_reports as rr
from lib.edgelab.research_dataset import build_opportunity_rows


def _obs(obs_id, ticker="T1", captured_at="2026-08-07T18:00:00Z", checkpoint="T_MINUS_5",
         scheduled_start="2026-08-07T18:30:00Z", yes_bid=44.0, yes_ask=46.0, no_bid=54.0, no_ask=56.0,
         market_family="KXMLBGAME", game_id="g1", player=None, team=None, threshold=None,
         comparison_operator=None, market_status="active", **overrides):
    row = {
        "marketObservationId": obs_id, "marketTicker": ticker, "capturedAt": captured_at,
        "checkpoint": checkpoint, "scheduledStart": scheduled_start, "gameId": game_id,
        "marketFamily": market_family, "yesBid": yes_bid, "yesAsk": yes_ask, "noBid": no_bid, "noAsk": no_ask,
        "lastPrice": yes_ask, "marketStatus": market_status, "isValidPregameObservation": True,
        "isClosingCandidate": True, "threshold": threshold, "comparisonOperator": comparison_operator,
        "team": team, "player": player, "outcomeLabel": None, "marketHorizon": "FULL_GAME",
        "lineupConfirmationState": None, "source": "test",
    }
    row.update(overrides)
    return row


def _settlement(ticker="T1", status="SETTLED", result="YES", game_id="g1", game_date="2026-08-07", **overrides):
    row = {"marketTicker": ticker, "settlementStatus": status, "result": result, "unavailableReason": None, "gameId": game_id}
    row.update(overrides)
    return row


def _evaluation(eval_id, ticker="T1", pipeline_run_id="2026-08-07T12:00:00Z", model_fair_probability=60.0,
                 market_implied_probability=50.0, selection="ML_Away", **overrides):
    row = {
        "modelEvaluationId": eval_id, "marketTicker": ticker, "pipelineRunId": pipeline_run_id,
        "modelFairProbability": model_fair_probability, "marketImpliedProbability": market_implied_probability,
        "selection": selection, "side": None, "threshold": None, "evaluationStatus": "EVALUATED",
        "confidence": "HIGH", "dataQuality": "full", "estimatedEdge": 5.0, "thesisTags": [], "correlationGroups": [],
    }
    row.update(overrides)
    return row


def _games(game_id="g1", game_date="2026-08-07"):
    return [{"gameId": game_id, "gameDate": game_date, "scheduledStartTime": "2026-08-07T18:30:00Z"}]


# ── Empty-input safety ────────────────────────────────────────────────────

def test_all_reports_handle_empty_rows():
    assert rr.market_calibration([])["overall"] == []
    assert rr.model_calibration([])["overall"] == []
    assert rr.edge_backtest([]) == []
    assert rr.market_family_research([]) == []
    assert rr.checkpoint_research([]) == []
    assert rr.ladder_research([]) == []
    dq = rr.research_data_quality([])
    assert dq["totalOpportunityRows"] == 0
    sv = rr.strategy_validation([])
    assert sv["totalDates"] == 0


# ── market_calibration ────────────────────────────────────────────────────

def test_market_calibration_full_universe_never_bet_never_recommended():
    rows = build_opportunity_rows([_obs("o1")], settlements=[_settlement()], recommendations=[], bets=[])
    report = rr.market_calibration(rows)
    assert report["overall"][0]["n"] == 1
    assert report["overall"][0]["actualYesRate"] == 1.0


def test_market_calibration_excludes_unresolved_and_void():
    rows = build_opportunity_rows(
        [_obs("o1", ticker="T1"), _obs("o2", ticker="T2"), _obs("o3", ticker="T3")],
        settlements=[
            _settlement(ticker="T1", status="SETTLED", result="YES"),
            _settlement(ticker="T2", status="SETTLEMENT_UNRESOLVED", result=None),
            _settlement(ticker="T3", status="VOID", result=None),
        ],
    )
    report = rr.market_calibration(rows)
    assert report["overall"][0]["n"] == 1  # only T1


# ── model_calibration ─────────────────────────────────────────────────────

def test_model_calibration_requires_causally_valid_evaluation():
    rows = build_opportunity_rows(
        [_obs("o1", captured_at="2026-08-07T10:00:00Z")],
        settlements=[_settlement()],
        evaluations=[_evaluation("e1", pipeline_run_id="2026-08-07T12:00:00Z")],  # AFTER the checkpoint
    )
    report = rr.model_calibration(rows)
    assert report["overall"] == []  # no causally-valid evaluation -> nothing eligible


def test_model_calibration_uses_normalized_0_1_scale():
    rows = build_opportunity_rows(
        [_obs("o1", captured_at="2026-08-07T18:00:00Z")],
        settlements=[_settlement(result="YES")],
        evaluations=[_evaluation("e1", model_fair_probability=64.0)],
    )
    report = rr.model_calibration(rows)
    overall = report["overall"][0]
    assert 0.0 <= overall["avgModelProbability"] <= 1.0
    assert abs(overall["avgModelProbability"] - 0.64) < 1e-6
    assert abs(overall["calibrationError"]) <= 1.0  # never a ~-47/-55-style scale-bug artifact


# ── edge_backtest: both sides ─────────────────────────────────────────────

def test_edge_backtest_produces_both_yes_and_no_opportunities():
    rows = build_opportunity_rows(
        [_obs("o1", yes_ask=40.0, no_ask=65.0)],
        settlements=[_settlement(result="YES")],
        evaluations=[_evaluation("e1", model_fair_probability=60.0)],
    )
    backtest = rr.edge_backtest(rows)
    total_n = sum(b["n"] for b in backtest)
    assert total_n == 2  # one YES opportunity + one NO (mirror) opportunity from the single row


def test_edge_backtest_side_filter():
    rows = build_opportunity_rows(
        [_obs("o1", yes_ask=40.0, no_ask=65.0)],
        settlements=[_settlement(result="YES")],
        evaluations=[_evaluation("e1", model_fair_probability=60.0)],
    )
    yes_only = rr.edge_backtest(rows, side_filter="YES")
    assert sum(b["n"] for b in yes_only) == 1


def test_edge_backtest_excludes_unresolved_settlement():
    rows = build_opportunity_rows(
        [_obs("o1")],
        settlements=[_settlement(status="SETTLEMENT_UNRESOLVED", result=None)],
        evaluations=[_evaluation("e1")],
    )
    assert rr.edge_backtest(rows) == []


def test_edge_backtest_never_uses_future_evaluation():
    rows = build_opportunity_rows(
        [_obs("o1", captured_at="2026-08-07T10:00:00Z")],
        settlements=[_settlement()],
        evaluations=[_evaluation("e1", pipeline_run_id="2026-08-07T12:00:00Z")],
    )
    assert rr.edge_backtest(rows) == []


# ── ladder_research ────────────────────────────────────────────────────

def test_ladder_monotonicity_violation_detected():
    observations = [
        _obs("o1", ticker="T1", checkpoint="CLOSING".replace("CLOSING", "T_MINUS_5"), threshold=1.5,
             comparison_operator="OVER", player="Player A", yes_ask=70.0),
        _obs("o2", ticker="T2", checkpoint="T_MINUS_5", threshold=2.5,
             comparison_operator="OVER", player="Player A", yes_ask=80.0),  # HIGHER threshold, HIGHER price -> violation
    ]
    rows = build_opportunity_rows(observations)
    ladders = rr.ladder_research(rows)
    assert len(ladders) == 1
    assert ladders[0]["isMonotonic"] is False
    assert len(ladders[0]["monotonicityViolations"]) == 1


def test_ladder_no_violation_for_proper_decreasing_over_ladder():
    observations = [
        _obs("o1", ticker="T1", checkpoint="T_MINUS_5", threshold=1.5, comparison_operator="OVER", player="Player A", yes_ask=70.0),
        _obs("o2", ticker="T2", checkpoint="T_MINUS_5", threshold=2.5, comparison_operator="OVER", player="Player A", yes_ask=40.0),
    ]
    rows = build_opportunity_rows(observations)
    ladders = rr.ladder_research(rows)
    assert ladders[0]["isMonotonic"] is True


def test_ladder_requires_at_least_two_rungs():
    observations = [_obs("o1", ticker="T1", threshold=1.5, comparison_operator="OVER", player="Player A")]
    rows = build_opportunity_rows(observations)
    assert rr.ladder_research(rows) == []


# ── research_data_quality ─────────────────────────────────────────────────

def test_data_quality_counts_are_sane():
    rows = build_opportunity_rows(
        [_obs("o1", ticker="T1"), _obs("o2", ticker="T2", checkpoint="T_MINUS_15")],
        settlements=[_settlement(ticker="T1", status="SETTLED", result="YES")],
        evaluations=[_evaluation("e1", ticker="T1")],
        games=_games(),
    )
    dq = rr.research_data_quality(rows)
    assert dq["uniqueGames"] == 1
    assert dq["uniqueMarketTickers"] == 2
    assert dq["totalOpportunityRows"] == 2
    assert dq["settlementStatusCounts"].get("SETTLED") == 1
    assert dq["settlementStatusCounts"].get("NOT_SETTLED") == 1


# ── strategy_validation ────────────────────────────────────────────────

def test_strategy_validation_partitions_never_overlap_dates():
    import datetime
    observations = []
    settlements = []
    for i in range(40):
        date = str(datetime.date(2026, 1, 1) + datetime.timedelta(days=i))
        ticker = f"T{i}"
        observations.append(_obs(f"o{i}", ticker=ticker, captured_at=f"{date}T18:00:00Z",
                                  scheduled_start=f"{date}T18:30:00Z", game_id=f"g{i}"))
        settlements.append(_settlement(ticker=ticker, status="SETTLED", result="YES", game_id=f"g{i}"))
    rows = build_opportunity_rows(observations, settlements=settlements, games=[
        {"gameId": f"g{i}", "gameDate": str(datetime.date(2026, 1, 1) + datetime.timedelta(days=i))} for i in range(40)
    ])
    result = rr.strategy_validation(rows)
    assert result["maturity"] == "USABLE"
    assert result["partitions"]["DEVELOPMENT"]["rowCount"] + result["partitions"]["VALIDATION"]["rowCount"] + result["partitions"]["HOLDOUT"]["rowCount"] == 40


def test_strategy_validation_small_corpus_labeled_framework_only():
    rows = build_opportunity_rows([_obs("o1")], settlements=[_settlement()], games=_games())
    result = rr.strategy_validation(rows)
    assert result["maturity"] == "FRAMEWORK_ONLY_INSUFFICIENT_DATES"
    assert "FRAMEWORK ONLY" in result["note"]


# ── Summary renders without crashing ──────────────────────────────────────

def test_render_summary_markdown_smoke():
    rows = build_opportunity_rows(
        [_obs("o1")], settlements=[_settlement()], evaluations=[_evaluation("e1")], games=_games(),
    )
    dq = rr.research_data_quality(rows)
    mc = rr.market_calibration(rows)
    mcal = rr.model_calibration(rows)
    eb = rr.edge_backtest(rows)
    sv = rr.strategy_validation(rows)
    text = rr.render_summary_markdown(dq, mc, mcal, eb, sv)
    assert "EdgeLab Research Trustworthiness Summary" in text
    assert "exploratory" in text.lower()


# ── edge_backtest clarity fields (Prospective Model Snapshots milestone) ──

def test_edge_backtest_underlying_rows_and_side_counts():
    rows = build_opportunity_rows(
        [_obs("o1", yes_ask=40.0, no_ask=65.0)],
        settlements=[_settlement(result="YES")],
        evaluations=[_evaluation("e1", model_fair_probability=60.0)],
    )
    backtest = rr.edge_backtest(rows)
    total_underlying = sum(b["underlyingModelRows"] for b in backtest)
    total_yes = sum(b["yesOpportunityCount"] for b in backtest)
    total_no = sum(b["noOpportunityCount"] for b in backtest)
    # One underlying row expands into exactly one YES + one NO opportunity (possibly in different buckets).
    assert total_yes == 1
    assert total_no == 1
    assert sum(b["n"] for b in backtest) == 2
    # underlyingModelRows sums to <= total opportunities (never claims more source rows than exist).
    assert total_underlying <= 2


def test_edge_backtest_bucket_n_can_exceed_underlying_rows_when_both_sides_land_in_one_bucket():
    """A row whose YES edge and mirrored NO edge both fall in the SAME bucket must show n=2, underlyingModelRows=1 -- not mistaken for duplicate data."""
    # model=50% (coin flip): YES edge = 0.50 - yesPrice, NO edge = 0.50 - noPrice. With yesAsk=noAsk=50, both edges land near 0.
    rows = build_opportunity_rows(
        [_obs("o1", yes_ask=48.0, no_ask=48.0)],
        settlements=[_settlement(result="YES")],
        evaluations=[_evaluation("e1", model_fair_probability=50.0)],
    )
    backtest = rr.edge_backtest(rows)
    combined_bucket = next(b for b in backtest if b["n"] == 2)
    assert combined_bucket["underlyingModelRows"] == 1
    assert combined_bucket["yesOpportunityCount"] == 1
    assert combined_bucket["noOpportunityCount"] == 1


# ── I. snapshot_coverage ──────────────────────────────────────────────────

def _prospective_evaluation(eval_id, ticker, game_id, checkpoint, model_fair_probability=60.0, **overrides):
    row = _evaluation(
        eval_id, ticker=ticker, model_fair_probability=model_fair_probability,
        gameId=game_id, checkpoint=checkpoint, artifactSource="prospective_snapshot",
    )
    row.update(overrides)
    return row


def test_snapshot_coverage_report_handles_empty_input():
    report = rr.snapshot_coverage_report([], [])
    assert report["gamesScheduled"] is None
    assert report["modelEvaluationsCapturedTotal"] == 0
    assert report["causalModelMarketPairCount"] == 0
    assert report["improvementOverPR86Baseline"]["currentCausalOpportunityRows"] == 0


def test_snapshot_coverage_report_counts_prospective_evaluations_by_checkpoint():
    rows = build_opportunity_rows(
        [_obs("o1", ticker="T1", game_id="g1"), _obs("o2", ticker="T2", game_id="g1", checkpoint="T_MINUS_30")],
        settlements=[_settlement(ticker="T1", game_id="g1"), _settlement(ticker="T2", game_id="g1")],
        evaluations=[
            _prospective_evaluation("e1", "T1", "g1", "T_MINUS_5"),
            _prospective_evaluation("e2", "T2", "g1", "T_MINUS_30"),
        ],
        games=_games(game_id="g1"),
    )
    evaluations = [
        _prospective_evaluation("e1", "T1", "g1", "T_MINUS_5"),
        _prospective_evaluation("e2", "T2", "g1", "T_MINUS_30"),
    ]
    report = rr.snapshot_coverage_report(rows, evaluations, games=[{"gameId": "g1"}])
    assert report["gamesScheduled"] == 1
    assert report["gamesWithProspectiveSnapshot"] == 1
    assert report["modelEvaluationsCapturedProspective"] == 2
    assert report["modelEvaluationsByCheckpoint"] == {"T_MINUS_5": 1, "T_MINUS_30": 1}


def test_snapshot_coverage_report_baseline_comparison_present_and_honest():
    rows = build_opportunity_rows(
        [_obs("o1")], settlements=[_settlement()], evaluations=[_prospective_evaluation("e1", "T1", "g1", "T_MINUS_30")],
    )
    report = rr.snapshot_coverage_report(rows, [_prospective_evaluation("e1", "T1", "g1", "T_MINUS_30")])
    baseline = report["improvementOverPR86Baseline"]
    assert baseline["baselineCausalOpportunityRows"] == 264
    assert baseline["baselineTotalOpportunityRows"] == 75280
    # 1 causal row out of a hypothetical baseline of 264 -> honest, small multiple, never inflated.
    assert baseline["currentCausalOpportunityRows"] == report["causalModelMarketPairCount"]


def test_snapshot_coverage_report_aggregates_research_run_skip_reasons():
    research_runs = [
        {
            "runType": "PROSPECTIVE_SNAPSHOT", "status": "success", "errors": [],
            "counts": {"modelEvaluationsSkippedDuplicate": 2, "gamesSkippedByReason": {"STARTED": 3, "NO_CHECKPOINT_DUE": 5}},
        },
        {
            "runType": "PROSPECTIVE_SNAPSHOT", "status": "success", "errors": [],
            "counts": {"modelEvaluationsSkippedDuplicate": 1, "gamesSkippedByReason": {"STARTED": 1}},
        },
        {"runType": "RECOMMENDATION_SYNC", "status": "success", "errors": [], "counts": {}},  # different runType -- must be ignored
    ]
    report = rr.snapshot_coverage_report([], [], research_runs=research_runs)
    assert report["duplicateOrIdempotencyCount"] == 3
    assert report["skippedStartedGameCount"] == 4


def test_snapshot_coverage_report_pct_settled_rows_with_causal_linkage():
    rows = build_opportunity_rows(
        [_obs("o1", ticker="T1"), _obs("o2", ticker="T2")],
        settlements=[_settlement(ticker="T1", result="YES"), _settlement(ticker="T2", result="NO")],
        evaluations=[_prospective_evaluation("e1", "T1", "g1", "T_MINUS_30", pipeline_run_id="2026-08-07T00:00:00Z")],
    )
    evaluations = [_prospective_evaluation("e1", "T1", "g1", "T_MINUS_30", pipeline_run_id="2026-08-07T00:00:00Z")]
    report = rr.snapshot_coverage_report(rows, evaluations)
    # 1 of 2 settled rows has a causal model link.
    assert report["pctSettledOpportunityRowsWithCausalLinkage"] == 0.5


# ── market_price_staleness_report / max_market_price_age_seconds filtering ──

def test_market_price_staleness_report_buckets_and_percentiles():
    observations = [
        _obs("o_prior1", ticker="T1", captured_at="2026-08-07T17:57:00Z", checkpoint="T_MINUS_30"),
        _obs("o_prior2", ticker="T1", captured_at="2026-08-07T18:11:00Z", checkpoint="T_MINUS_15"),
    ]
    evaluations = [
        _evaluation("e1", ticker="T1", pipeline_run_id="2026-08-07T18:00:00Z", checkpoint="T_MINUS_30"),  # ages: 180s (o_prior1)
    ]
    rows = build_opportunity_rows(observations, settlements=[_settlement(ticker="T1")], evaluations=evaluations)
    report = rr.market_price_staleness_report(rows)
    assert report["n"] >= 1
    assert "byBucket" in report
    assert report["nWithMarketLinkage"] + report["nWithoutMarketLinkage"] == report["n"]


def test_market_price_staleness_report_handles_empty_input():
    report = rr.market_price_staleness_report([])
    assert report["n"] == 0
    assert report["medianMarketPriceAgeSeconds"] is None
    assert report["p90MarketPriceAgeSeconds"] is None


def test_edge_backtest_filters_by_max_market_price_age_seconds():
    observations = [
        # Prior observations (for marketPriceAgeSeconds), both BEFORE their eval's pipelineRunId.
        _obs("o_fresh_prior", ticker="T1", captured_at="2026-08-07T17:58:00Z", checkpoint="T_MINUS_60"),  # 2 min before eval
        _obs("o_stale_prior", ticker="T2", captured_at="2026-08-07T17:30:00Z", checkpoint="T_MINUS_90"),  # 30 min before eval
        # Each ticker's own checkpoint observation (the row's primary causal anchor) -- AFTER its eval's pipelineRunId.
        _obs("o_checkpoint1", ticker="T1", captured_at="2026-08-07T18:05:00Z", checkpoint="T_MINUS_30"),
        _obs("o_checkpoint2", ticker="T2", captured_at="2026-08-07T18:05:00Z", checkpoint="T_MINUS_30"),
    ]
    evaluations = [
        _evaluation("e1", ticker="T1", pipeline_run_id="2026-08-07T18:00:00Z", model_fair_probability=70.0),
        _evaluation("e2", ticker="T2", pipeline_run_id="2026-08-07T18:00:00Z", model_fair_probability=70.0),
    ]
    settlements = [_settlement(ticker="T1", result="YES"), _settlement(ticker="T2", result="YES")]
    rows = build_opportunity_rows(observations, settlements=settlements, evaluations=evaluations)
    checkpoint_rows = [r for r in rows if r["checkpoint"] == "T_MINUS_30"]
    ages = {r["marketTicker"]: r["marketPriceAgeSeconds"] for r in checkpoint_rows}
    assert ages["T1"] == 120.0 and ages["T2"] == 1800.0

    unfiltered = rr.edge_backtest(checkpoint_rows)
    filtered = rr.edge_backtest(checkpoint_rows, max_market_price_age_seconds=300)  # 5 minutes -- excludes T2's 30-min-old pairing

    assert sum(b["n"] for b in unfiltered) > sum(b["n"] for b in filtered)
    for bucket in filtered:
        assert bucket["n"] >= 0


def test_edge_backtest_no_filter_by_default_never_drops_rows():
    observations = [
        _obs("o_checkpoint", ticker="T1", captured_at="2026-08-07T18:05:00Z", checkpoint="T_MINUS_30"),
    ]
    evaluations = [_evaluation("e1", ticker="T1", pipeline_run_id="2026-08-07T18:00:00Z")]  # no PRIOR observation exists -> marketPriceAgeSeconds unavailable
    settlements = [_settlement(ticker="T1", result="YES")]
    rows = build_opportunity_rows(observations, settlements=settlements, evaluations=evaluations)
    unfiltered = rr.edge_backtest(rows)
    assert sum(b["n"] for b in unfiltered) == 2  # YES+NO opportunities, staleness filter never applied by default
