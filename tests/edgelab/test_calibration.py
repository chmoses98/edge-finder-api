#!/usr/bin/env python3
"""
tests/edgelab/test_calibration.py
======================================
Coverage for lib/edgelab/calibration.py (EdgeLab Phase 2 Milestone 2 --
docs/EDGELAB_CALIBRATION.md). Builds on the same DuckDB-over-tmp_path
fixture pattern tests/edgelab/test_analytics.py established.
"""
import gzip
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab import calibration as cal
from lib.edgelab.analytics import open_session


def _write_jsonl(path, records, compressed=False):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    opener = gzip.open if compressed else open
    with opener(path, "wt") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


def _bet(bet_id, result="WIN", stake=10.0, net_profit_loss=5.0, clv=None, confidence=None,
         estimated_edge=None, model_fair_probability=None, market_family="game_result",
         thesis_tags=None, source="MODEL", recommendation_id=None, entry_timestamp="2026-07-01T12:00:00Z",
         scheduled_start=None, status="settled", **overrides):
    rec = {
        "betId": bet_id, "marketTicker": f"T-{bet_id}", "marketFamily": market_family,
        "selection": "x", "side": "YES", "stake": stake, "entryPrice": 0.5,
        "entryTimestamp": entry_timestamp, "scheduledStart": scheduled_start,
        "source": source, "recommendationId": recommendation_id, "confidence": confidence,
        "estimatedEdgeAtEntry": estimated_edge, "modelFairProbability": model_fair_probability,
        "thesisTags": thesis_tags or [], "status": status, "result": result,
        "netProfitLoss": net_profit_loss, "clv": clv,
    }
    rec.update(overrides)
    return rec


def _decided_bets(n, wins, **shared):
    """n bets, first `wins` are WIN, rest LOSS -- deterministic, no randomness."""
    bets = []
    for i in range(n):
        result = "WIN" if i < wins else "LOSS"
        bets.append(_bet(f"b{i}", result=result, **shared))
    return bets


def _session(tmp_path, bets):
    _write_jsonl(str(tmp_path / "bets" / "bets.jsonl"), bets)
    return open_session(root=str(tmp_path))


# ── Zero-bet / missing-data groups ──────────────────────────────────────

def test_all_dimensions_return_empty_list_when_bets_unavailable(tmp_path):
    with open_session(root=str(tmp_path)) as session:
        assert cal.edge_bucket_calibration(session) == []
        assert cal.confidence_calibration(session) == []
        assert cal.market_family_calibration(session) == []
        assert cal.market_family_report(session) == []
        assert cal.thesis_tag_calibration(session) == []
        assert cal.thesis_tag_cooccurrence(session) == []
        assert cal.clv_bucket_calibration(session) == []
        assert cal.clv_sign_study(session) == []
        assert cal.timing_bucket_calibration(session) == []
        assert cal.recommendation_path_calibration(session) == []
        assert cal.daily_trend_report(session) == []
        assert cal.weekly_trend_report(session) == []
        assert cal.monthly_trend_report(session) == []
        assert cal.season_to_date_report(session) == []


def test_pending_and_push_void_bets_excluded_from_every_bucket(tmp_path):
    """Only settled WIN/LOSS bets are 'decided' -- pending/void/push contribute to no bucket's n."""
    bets = [
        _bet("pending1", status="pending", result=None),
        _bet("void1", status="void", result="VOID"),
        _bet("push1", status="settled", result="PUSH"),
        _bet("decided1", status="settled", result="WIN"),
    ]
    with _session(tmp_path, bets) as session:
        rows = cal.market_family_calibration(session)
        assert len(rows) == 1
        assert rows[0]["n"] == 1


# ── Sample-size gating ───────────────────────────────────────────────────

def test_sample_size_status_boundaries():
    assert cal.calibration_status(19) == "INSUFFICIENT_SAMPLE"
    assert cal.calibration_status(20) == "DESCRIPTIVE_ONLY"
    assert cal.calibration_status(99) == "DESCRIPTIVE_ONLY"
    assert cal.calibration_status(100) == "CALIBRATED"
    assert cal.calibration_status(0) == "INSUFFICIENT_SAMPLE"


def test_sample_size_status_matches_between_python_and_sql(tmp_path):
    bets = _decided_bets(19, wins=10) + _decided_bets(1, wins=1, market_family="team_total")
    with _session(tmp_path, bets) as session:
        rows = cal.market_family_calibration(session)
        by_family = {r["canonicalMarketFamily"]: r for r in rows}
        assert by_family["game_result"]["n"] == 19
        assert by_family["game_result"]["status"] == "INSUFFICIENT_SAMPLE"
        assert by_family["team_total"]["n"] == 1
        assert by_family["team_total"]["status"] == "INSUFFICIENT_SAMPLE"


def test_descriptive_only_at_exactly_20(tmp_path):
    bets = _decided_bets(20, wins=10)
    with _session(tmp_path, bets) as session:
        rows = cal.market_family_calibration(session)
        assert rows[0]["n"] == 20
        assert rows[0]["status"] == "DESCRIPTIVE_ONLY"


def test_calibrated_at_exactly_100(tmp_path):
    bets = _decided_bets(100, wins=55)
    with _session(tmp_path, bets) as session:
        rows = cal.market_family_calibration(session)
        assert rows[0]["n"] == 100
        assert rows[0]["status"] == "CALIBRATED"


def test_underlying_value_never_withheld_regardless_of_status(tmp_path):
    """A 3-bet bucket still reports a real winRate/roi number -- status is a reading instruction, not a filter."""
    bets = _decided_bets(3, wins=3)
    with _session(tmp_path, bets) as session:
        rows = cal.market_family_calibration(session)
        assert rows[0]["status"] == "INSUFFICIENT_SAMPLE"
        assert rows[0]["winRate"] == 1.0
        assert rows[0]["roi"] is not None


# ── Calibration calculations ─────────────────────────────────────────────

def test_actual_win_rate_and_calibration_error_arithmetic(tmp_path):
    bets = [
        _bet("b0", result="WIN", model_fair_probability=0.6, stake=10, net_profit_loss=5),
        _bet("b1", result="WIN", model_fair_probability=0.6, stake=10, net_profit_loss=5),
        _bet("b2", result="LOSS", model_fair_probability=0.6, stake=10, net_profit_loss=-10),
        _bet("b3", result="LOSS", model_fair_probability=0.6, stake=10, net_profit_loss=-10),
    ]
    with _session(tmp_path, bets) as session:
        rows = cal.market_family_calibration(session)
        row = rows[0]
        assert row["n"] == 4
        assert row["actualWinRate"] == 0.5
        assert row["winRate"] == row["actualWinRate"]
        assert abs(row["expectedWinRate"] - 0.6) < 1e-9
        assert abs(row["calibrationError"] - (0.5 - 0.6)) < 1e-9
        assert row["totalStake"] == 40
        assert row["totalNetProfitLoss"] == -10
        assert abs(row["roi"] - (-10 / 40)) < 1e-9


def _raw_eval_row(**overrides):
    row = {
        "modelEvaluationId": overrides.pop("modelEvaluationId", "me1"),
        "runId": "run1",
        "gameId": overrides.pop("gameId", "g1"),
        "marketTicker": overrides.pop("marketTicker", "T-b0"),
        "marketFamily": overrides.pop("marketFamily", "game_result"),
        "selection": overrides.pop("selection", "ML_Away"),
        "side": overrides.pop("side", None),
        "threshold": overrides.pop("threshold", None),
        "evaluationStatus": overrides.pop("evaluationStatus", "EVALUATED"),
        "modelFairProbability": overrides.pop("modelFairProbability", 60.0),  # 0-100 scale, per ModelEvaluation schema
        "marketImpliedProbability": overrides.pop("marketImpliedProbability", 50.0),
        "estimatedEdge": overrides.pop("estimatedEdge", 5.0),
        "confidence": overrides.pop("confidence", "HIGH"),
        "dataQuality": overrides.pop("dataQuality", "full"),
        "createdAt": overrides.pop("createdAt", "2026-07-01T00:00:00Z"),
    }
    row.update(overrides)
    return row


def _session_with_evaluations(tmp_path, bets, evaluations):
    _write_jsonl(str(tmp_path / "bets" / "bets.jsonl"), bets)
    _write_jsonl(str(tmp_path / "model_evaluations" / "evals.jsonl"), evaluations)
    return open_session(root=str(tmp_path))


def test_linked_model_evaluation_probability_normalized_to_0_1_scale(tmp_path):
    """
    Regression test for the 0-100-vs-0-1 calibration scale bug: a bet
    whose modelFairProbability is sourced from a LINKED ModelEvaluation
    (stored 0-100, e.g. 55.64) must never be compared directly against
    actualWinRate (always a 0-1 fraction). Before the fix, this exact
    setup produced calibrationError ~= 0.538 - 55.64 = -55.1 instead of
    a sane in-range value.
    """
    bets = [
        _bet("b0", result="WIN", model_fair_probability=None, modelEvaluationId="me0", marketTicker="T-b0"),
        _bet("b1", result="LOSS", model_fair_probability=None, modelEvaluationId="me1", marketTicker="T-b1"),
    ]
    evaluations = [
        _raw_eval_row(modelEvaluationId="me0", marketTicker="T-b0", modelFairProbability=55.64),
        _raw_eval_row(modelEvaluationId="me1", marketTicker="T-b1", modelFairProbability=47.99),
    ]
    with _session_with_evaluations(tmp_path, bets, evaluations) as session:
        row = cal.market_family_calibration(session)[0]
        assert row["n"] == 2
        # expectedWinRate must be the 0-1-normalized average (0.5564+0.4799)/2, not the raw 0-100 average.
        assert abs(row["expectedWinRate"] - 0.51815) < 1e-6
        assert -1.0 <= row["expectedWinRate"] <= 1.0
        assert row["actualWinRate"] == 0.5
        # calibrationError must be a plausible probability-space delta, never ~ -47 or ~ -55.
        assert abs(row["calibrationError"]) <= 1.0
        assert abs(row["calibrationError"] - (0.5 - 0.51815)) < 1e-3


def test_linked_model_evaluation_falls_back_to_bet_own_probability_on_join_miss(tmp_path):
    """A modelEvaluationId that doesn't resolve to any real ModelEvaluation row still falls back to the bet's own (already 0-1) copy, never NULL."""
    bets = [_bet("b0", result="WIN", model_fair_probability=0.6, modelEvaluationId="does-not-exist")]
    evaluations = [_raw_eval_row(modelEvaluationId="me-other", marketTicker="T-other")]
    with _session_with_evaluations(tmp_path, bets, evaluations) as session:
        row = cal.market_family_calibration(session)[0]
        assert abs(row["expectedWinRate"] - 0.6) < 1e-9


def test_expected_win_rate_none_when_no_model_probability_recorded(tmp_path):
    """Real-data finding: none of today's settled bets carry modelFairProbability -- must not fabricate 0 or crash."""
    bets = _decided_bets(5, wins=3, model_fair_probability=None)
    with _session(tmp_path, bets) as session:
        row = cal.market_family_calibration(session)[0]
        assert row["expectedWinRate"] is None
        assert row["calibrationError"] is None
        assert row["actualWinRate"] == 0.6  # still computed


def test_roi_none_when_stake_is_zero_or_missing(tmp_path):
    bets = [_bet("b0", result="WIN", stake=None, net_profit_loss=None)]
    with _session(tmp_path, bets) as session:
        row = cal.market_family_calibration(session)[0]
        assert row["roi"] is None
        assert row["totalStake"] is None or row["totalStake"] == 0


# ── Edge bucket boundaries ───────────────────────────────────────────────

def test_edge_bucket_boundaries_are_half_open(tmp_path):
    bets = [
        _bet("b0", estimated_edge=1.99),  # -> 0-2
        _bet("b1", estimated_edge=2.0),   # -> 2-4 (boundary belongs to the upper bucket)
        _bet("b2", estimated_edge=3.99),  # -> 2-4
        _bet("b3", estimated_edge=4.0),   # -> 4-6
    ]
    with _session(tmp_path, bets) as session:
        rows = {r["edgeBucket"]: r["n"] for r in cal.edge_bucket_calibration(session)}
        assert rows["0-2"] == 1
        assert rows["2-4"] == 2
        assert rows["4-6"] == 1


def test_edge_bucket_null_edge_gets_unknown_bucket_not_dropped(tmp_path):
    bets = [_bet("b0", estimated_edge=None), _bet("b1", estimated_edge=None)]
    with _session(tmp_path, bets) as session:
        rows = cal.edge_bucket_calibration(session)
        assert len(rows) == 1
        assert rows[0]["edgeBucket"] == "UNKNOWN"
        assert rows[0]["n"] == 2


# ── Confidence ────────────────────────────────────────────────────────────

def test_confidence_calibration_groups_by_value_including_null(tmp_path):
    bets = [
        _bet("b0", confidence="HIGH"), _bet("b1", confidence="HIGH"),
        _bet("b2", confidence="LOW"), _bet("b3", confidence=None),
    ]
    with _session(tmp_path, bets) as session:
        rows = {r["confidence"]: r["n"] for r in cal.confidence_calibration(session)}
        assert rows["HIGH"] == 2
        assert rows["LOW"] == 1
        assert rows["UNKNOWN"] == 1


# ── Market-family report (avg edge / avg confidence) ────────────────────

def test_market_family_report_avg_edge_and_confidence_score(tmp_path):
    bets = [
        _bet("b0", market_family="game_result", estimated_edge=2.0, confidence="LOW"),
        _bet("b1", market_family="game_result", estimated_edge=4.0, confidence="HIGH"),
        _bet("b2", market_family="game_result", estimated_edge=3.0, confidence="PAPER"),  # excluded from confidence score avg
    ]
    with _session(tmp_path, bets) as session:
        row = cal.market_family_report(session)[0]
        assert row["bets"] == 3
        assert abs(row["avgEdge"] - 3.0) < 1e-9
        # PAPER excluded from the ordinal average: (1 + 3) / 2 = 2.0, not (1+3+x)/3
        assert abs(row["avgConfidenceScore"] - 2.0) < 1e-9


# ── Thesis tags: multi-tag aggregation + co-occurrence ──────────────────

def test_multi_tag_bet_contributes_to_every_one_of_its_tags_bucket(tmp_path):
    bets = [
        _bet("b0", result="WIN", thesis_tags=["PITCHER_FATIGUE", "WEATHER"]),
        _bet("b1", result="LOSS", thesis_tags=["WEATHER"]),
    ]
    with _session(tmp_path, bets) as session:
        rows = {r["thesisTag"]: r["n"] for r in cal.thesis_tag_calibration(session)}
        assert rows["PITCHER_FATIGUE"] == 1
        assert rows["WEATHER"] == 2


def test_thesis_tag_cooccurrence_counts_pairs_on_same_bet(tmp_path):
    bets = [
        _bet("b0", thesis_tags=["A", "B"]),
        _bet("b1", thesis_tags=["A", "B", "C"]),
        _bet("b2", thesis_tags=["C"]),
    ]
    with _session(tmp_path, bets) as session:
        pairs = {(r["tagA"], r["tagB"]): r["coOccurrenceCount"] for r in cal.thesis_tag_cooccurrence(session)}
        assert pairs[("A", "B")] == 2
        assert pairs[("A", "C")] == 1
        assert pairs[("B", "C")] == 1


def test_thesis_tag_cooccurrence_ignores_bets_with_fewer_than_two_tags(tmp_path):
    bets = [_bet("b0", thesis_tags=["SOLO"]), _bet("b1", thesis_tags=[])]
    with _session(tmp_path, bets) as session:
        assert cal.thesis_tag_cooccurrence(session) == []


def test_thesis_tag_calibration_empty_when_no_tags_present_matches_real_data(tmp_path):
    """Matches docs/EDGELAB_PHASE2_DESIGN.md's documented 0%-coverage finding -- an honest empty list, not an error."""
    bets = _decided_bets(5, wins=2, thesis_tags=[])
    with _session(tmp_path, bets) as session:
        assert cal.thesis_tag_calibration(session) == []
        assert cal.thesis_tag_cooccurrence(session) == []


# ── CLV aggregation ──────────────────────────────────────────────────────

def test_clv_bucket_boundaries(tmp_path):
    bets = [
        _bet("b0", clv=-0.01),  # -5-0
        _bet("b1", clv=0.0),    # 0-5
        _bet("b2", clv=4.99),   # 0-5
        _bet("b3", clv=5.0),    # 5-10
    ]
    with _session(tmp_path, bets) as session:
        rows = {r["clvBucket"]: r["n"] for r in cal.clv_bucket_calibration(session)}
        assert rows["-5-0"] == 1
        assert rows["0-5"] == 2
        assert rows["5-10"] == 1


def test_clv_bucket_null_clv_gets_unknown_bucket(tmp_path):
    bets = [_bet("b0", clv=None)]
    with _session(tmp_path, bets) as session:
        rows = cal.clv_bucket_calibration(session)
        assert rows[0]["clvBucket"] == "UNKNOWN"
        assert rows[0]["avgClv"] is None


def test_clv_sign_study_positive_neutral_negative_boundaries(tmp_path):
    band = cal.NEUTRAL_CLV_BAND
    bets = [
        _bet("pos", clv=band + 0.01),
        _bet("neg", clv=-band - 0.01),
        _bet("neutral_pos_edge", clv=band),
        _bet("neutral_neg_edge", clv=-band),
        _bet("neutral_zero", clv=0.0),
        _bet("unknown", clv=None),
    ]
    with _session(tmp_path, bets) as session:
        rows = {r["clvSign"]: r["n"] for r in cal.clv_sign_study(session)}
        assert rows["POSITIVE"] == 1
        assert rows["NEGATIVE"] == 1
        assert rows["NEUTRAL"] == 3
        assert rows["UNKNOWN"] == 1


def test_clv_sign_study_order_is_positive_neutral_negative_unknown(tmp_path):
    bets = [_bet("a", clv=1.0), _bet("b", clv=-1.0), _bet("c", clv=0.0), _bet("d", clv=None)]
    with _session(tmp_path, bets) as session:
        order = [r["clvSign"] for r in cal.clv_sign_study(session)]
        assert order == ["POSITIVE", "NEUTRAL", "NEGATIVE", "UNKNOWN"]


# ── Recommendation-path analysis ────────────────────────────────────────

def test_recommendation_path_categorizes_bets_correctly(tmp_path):
    bets = [
        _bet("b0", source="MANUAL", recommendation_id=None),
        _bet("b1", source="MODEL", recommendation_id=None),
        _bet("b2", source="MODEL", recommendation_id="rec-1"),
        _bet("b3", source="OTHER", recommendation_id=None),
    ]
    with _session(tmp_path, bets) as session:
        rows = {r["recommendationPath"]: r["n"] for r in cal.recommendation_path_calibration(session)}
        assert rows["MANUAL_BET"] == 1
        assert rows["MODEL_BET"] == 1
        assert rows["RECOMMENDED_AND_BET"] == 1
        assert rows["OTHER_BET"] == 1


def test_recommendation_path_includes_not_bet_and_passed_when_recommendations_available(tmp_path):
    recs = [
        {"recommendationId": "r1", "runId": "run1", "status": "RECOMMENDED_NOT_BET",
         "modelFairProbability": 0.6, "marketImpliedProbability": 0.5, "estimatedEdge": 10.0,
         "betPlaced": False, "createdAt": "2026-07-01T00:00:00Z", "source": "MODEL",
         "validationStatus": "valid", "provenance": {}},
        {"recommendationId": "r2", "runId": "run1", "status": "PASS_NO_EDGE",
         "modelFairProbability": 0.5, "marketImpliedProbability": 0.5, "estimatedEdge": 0.0,
         "betPlaced": False, "createdAt": "2026-07-01T00:00:00Z", "source": "MODEL",
         "validationStatus": "valid", "provenance": {}},
        {"recommendationId": "r3", "runId": "run1", "status": "PASS_LOW_LIQUIDITY",
         "modelFairProbability": 0.7, "marketImpliedProbability": 0.4, "estimatedEdge": 30.0,
         "betPlaced": False, "createdAt": "2026-07-01T00:00:00Z", "source": "MODEL",
         "validationStatus": "valid", "provenance": {}},
    ]
    _write_jsonl(str(tmp_path / "recommendations" / "2026-07-01.jsonl"), recs)
    with open_session(root=str(tmp_path)) as session:
        rows = {r["recommendationPath"]: r for r in cal.recommendation_path_calibration(session)}
        assert rows["RECOMMENDED_NOT_BET"]["n"] == 1
        assert rows["RECOMMENDED_NOT_BET"]["winRate"] is None
        assert rows["RECOMMENDED_NOT_BET"]["roi"] is None
        assert abs(rows["RECOMMENDED_NOT_BET"]["avgModelFairProbability"] - 0.6) < 1e-9
        assert rows["PASSED"]["n"] == 2  # both PASS_NO_EDGE and PASS_LOW_LIQUIDITY roll up together


def test_recommendation_path_absent_when_no_recommendations_and_no_bets(tmp_path):
    with open_session(root=str(tmp_path)) as session:
        assert cal.recommendation_path_calibration(session) == []


# ── Timing bucket ────────────────────────────────────────────────────────

def test_timing_bucket_classifies_by_distance_to_scheduled_start(tmp_path):
    bets = [
        _bet("b0", entry_timestamp="2026-07-01T10:30:00Z", scheduled_start="2026-07-01T12:00:00Z"),  # 90 min before
        _bet("b1", entry_timestamp="2026-07-01T11:00:00Z", scheduled_start="2026-07-01T12:00:00Z"),  # 60 min before
        _bet("b2", entry_timestamp=None, scheduled_start=None),
    ]
    with _session(tmp_path, bets) as session:
        rows = {r["timingBucket"]: r["n"] for r in cal.timing_bucket_calibration(session)}
        assert rows.get("T_MINUS_90") == 1
        assert rows.get("T_MINUS_60") == 1


def test_timing_bucket_handles_non_utc_offset_entry_timestamp_correctly(tmp_path):
    """
    entryTimestamp can carry a real, non-UTC offset (e.g. Eastern time) on
    real committed data -- classify_checkpoint must receive a correctly
    UTC-converted instant, not a value with the offset silently dropped.
    12:00:00-04:00 is 16:00:00Z, i.e. exactly 30 min before a 16:30Z start.
    """
    bets = [_bet("b0", entry_timestamp="2026-07-01T12:00:00-04:00", scheduled_start="2026-07-01T16:30:00Z")]
    with _session(tmp_path, bets) as session:
        rows = {r["timingBucket"]: r["n"] for r in cal.timing_bucket_calibration(session)}
        assert rows.get("T_MINUS_30") == 1


def test_timing_bucket_missing_scheduled_start_is_intermediate_not_dropped(tmp_path):
    bets = [_bet("b0", scheduled_start=None)]
    with _session(tmp_path, bets) as session:
        rows = cal.timing_bucket_calibration(session)
        assert len(rows) == 1
        assert rows[0]["timingBucket"] == "INTERMEDIATE"
        assert rows[0]["n"] == 1


# ── Trend reports ────────────────────────────────────────────────────────

def test_daily_weekly_monthly_season_trend_reports(tmp_path):
    bets = [
        _bet("b0", entry_timestamp="2026-07-01T12:00:00Z"),
        _bet("b1", entry_timestamp="2026-07-01T18:00:00Z"),
        _bet("b2", entry_timestamp="2026-07-08T12:00:00Z"),
        _bet("b3", entry_timestamp="2026-08-01T12:00:00Z"),
    ]
    with _session(tmp_path, bets) as session:
        daily = {r["period"]: r["n"] for r in cal.daily_trend_report(session)}
        assert daily["2026-07-01"] == 2
        assert daily["2026-07-08"] == 1
        assert daily["2026-08-01"] == 1

        monthly = {r["period"]: r["n"] for r in cal.monthly_trend_report(session)}
        assert monthly["2026-07"] == 3
        assert monthly["2026-08"] == 1

        season = cal.season_to_date_report(session)
        assert len(season) == 1
        assert season[0]["n"] == 4
        assert season[0]["period"] == "SEASON_TO_DATE"


def test_season_to_date_empty_when_no_decided_bets(tmp_path):
    bets = [_bet("pending1", status="pending", result=None)]
    with _session(tmp_path, bets) as session:
        assert cal.season_to_date_report(session) == []


# ── Determinism ──────────────────────────────────────────────────────────

def test_repeated_calibration_runs_produce_identical_results(tmp_path):
    bets = _decided_bets(25, wins=13, clv=1.5, estimated_edge=3.0, confidence="HIGH", thesis_tags=["A", "B"])

    def _run():
        with open_session(root=str(tmp_path)) as session:
            return (
                cal.edge_bucket_calibration(session),
                cal.confidence_calibration(session),
                cal.market_family_report(session),
                cal.thesis_tag_calibration(session),
                cal.thesis_tag_cooccurrence(session),
                cal.clv_bucket_calibration(session),
                cal.clv_sign_study(session),
                cal.timing_bucket_calibration(session),
                cal.recommendation_path_calibration(session),
                cal.daily_trend_report(session),
                cal.season_to_date_report(session),
            )

    _write_jsonl(str(tmp_path / "bets" / "bets.jsonl"), bets)
    first = _run()
    second = _run()
    third = _run()
    assert first == second == third
