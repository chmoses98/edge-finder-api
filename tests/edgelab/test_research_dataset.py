#!/usr/bin/env python3
"""
tests/edgelab/test_research_dataset.py
===========================================
Coverage for lib/edgelab/research_dataset.py -- the canonical
(marketTicker x standardized checkpoint) opportunity dataset builder.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab import research_dataset as rd


def _obs(obs_id, ticker="T1", captured_at="2026-08-07T18:00:00Z", checkpoint="T_MINUS_30",
         scheduled_start="2026-08-07T18:30:00Z", yes_bid=44.0, yes_ask=46.0, no_bid=54.0, no_ask=56.0,
         market_status="active", is_valid_pregame=True, is_closing_candidate=True,
         market_family="KXMLBGAME", game_id="g1", **overrides):
    row = {
        "marketObservationId": obs_id,
        "marketTicker": ticker,
        "capturedAt": captured_at,
        "checkpoint": checkpoint,
        "scheduledStart": scheduled_start,
        "gameId": game_id,
        "marketFamily": market_family,
        "yesBid": yes_bid,
        "yesAsk": yes_ask,
        "noBid": no_bid,
        "noAsk": no_ask,
        "lastPrice": yes_ask,
        "marketStatus": market_status,
        "isValidPregameObservation": is_valid_pregame,
        "isClosingCandidate": is_closing_candidate,
        "threshold": None,
        "comparisonOperator": None,
        "team": None,
        "player": None,
        "outcomeLabel": None,
        "marketHorizon": "FULL_GAME",
        "lineupConfirmationState": None,
        "source": "edgelab_test",
    }
    row.update(overrides)
    return row


def _settlement(ticker="T1", status="SETTLED", result="YES", **overrides):
    row = {"marketTicker": ticker, "settlementStatus": status, "result": result, "unavailableReason": None}
    row.update(overrides)
    return row


def _evaluation(eval_id, ticker="T1", pipeline_run_id="2026-08-07T12:00:00Z", model_fair_probability=60.0,
                 market_implied_probability=50.0, selection="ML_Away", side=None, threshold=None, **overrides):
    row = {
        "modelEvaluationId": eval_id, "marketTicker": ticker, "pipelineRunId": pipeline_run_id,
        "modelFairProbability": model_fair_probability, "marketImpliedProbability": market_implied_probability,
        "selection": selection, "side": side, "threshold": threshold, "evaluationStatus": "EVALUATED",
        "confidence": "HIGH", "dataQuality": "full", "estimatedEdge": 5.0, "thesisTags": [], "correlationGroups": [],
    }
    row.update(overrides)
    return row


# ── Row grain / checkpoint selection ─────────────────────────────────────

def test_post_start_observation_never_becomes_its_own_row():
    observations = [
        _obs("o1", checkpoint="T_MINUS_5", captured_at="2026-08-07T18:25:00Z"),
        _obs("o2", checkpoint="POST_START", captured_at="2026-08-07T18:35:00Z", is_valid_pregame=False, is_closing_candidate=False),
    ]
    rows = rd.build_opportunity_rows(observations)
    checkpoints_seen = {r["checkpoint"] for r in rows}
    assert "POST_START" not in checkpoints_seen
    assert len(rows) == 1  # only T_MINUS_5 (which also becomes the closing quote)


def test_intermediate_observation_not_substituted_for_missing_named_checkpoint():
    """An INTERMEDIATE tick must never silently stand in for a missing T_MINUS_30 -- unless it's genuinely the closing quote."""
    observations = [_obs("o1", checkpoint="INTERMEDIATE", captured_at="2026-08-07T17:00:00Z")]
    rows = rd.build_opportunity_rows(observations)
    # The only INTERMEDIATE tick IS the sole valid pregame quote, so it legitimately becomes CLOSING.
    assert len(rows) == 1
    assert rows[0]["isClosingQuote"] is True
    assert rows[0]["researchCheckpoint"] == "CLOSING"


def test_intermediate_tick_dropped_when_not_the_closing_quote():
    """An earlier INTERMEDIATE tick must never become its own row when it is neither a named checkpoint nor the closing quote."""
    observations = [
        _obs("o1", checkpoint="INTERMEDIATE", captured_at="2026-08-07T17:50:00Z"),
        _obs("o2", checkpoint="T_MINUS_30", captured_at="2026-08-07T18:00:00Z"),
    ]
    rows = rd.build_opportunity_rows(observations)
    checkpoints_seen = {r["checkpoint"] for r in rows}
    assert checkpoints_seen == {"T_MINUS_30"}  # the earlier INTERMEDIATE never became its own row
    assert rows[0]["isClosingQuote"] is True    # the later named checkpoint is the closing quote


def test_closing_uses_canonical_selection_not_merely_last_observation():
    """A suspended (non-active) later tick must NOT become closing; the last valid tradable tick before it must."""
    observations = [
        _obs("o1", checkpoint="T_MINUS_30", captured_at="2026-08-07T18:00:00Z", market_status="active"),
        _obs("o2", checkpoint="T_MINUS_15", captured_at="2026-08-07T18:15:00Z", market_status="suspended", is_closing_candidate=False),
    ]
    rows = rd.build_opportunity_rows(observations)
    closing_rows = [r for r in rows if r["isClosingQuote"]]
    assert len(closing_rows) == 1
    assert closing_rows[0]["marketObservationId"] == "o1"


def test_missing_named_checkpoint_stays_missing_not_interpolated():
    observations = [_obs("o1", checkpoint="T_MINUS_30", captured_at="2026-08-07T18:00:00Z")]
    rows = rd.build_opportunity_rows(observations)
    assert {r["checkpoint"] for r in rows} == {"T_MINUS_30"}
    assert not any(r["checkpoint"] == "T_MINUS_5" for r in rows)


# ── Full-universe inclusion ───────────────────────────────────────────────

def test_never_recommended_never_bet_market_still_included():
    observations = [_obs("o1")]
    rows = rd.build_opportunity_rows(observations, recommendations=[], bets=[])
    assert len(rows) == 1
    assert rows[0]["wasRecommended"] is False
    assert rows[0]["wasPlaced"] is False


def test_was_recommended_and_placed_flags_stay_none_when_source_not_loaded():
    observations = [_obs("o1")]
    rows = rd.build_opportunity_rows(observations)  # recommendations/bets not passed at all
    assert rows[0]["wasRecommended"] is None
    assert rows[0]["wasPlaced"] is None


# ── Settlement / outcome handling ─────────────────────────────────────────

def test_unresolved_settlement_never_produces_a_hypothetical_return():
    observations = [_obs("o1")]
    settlements = [_settlement(status="SETTLEMENT_UNRESOLVED", result=None, unavailableReason="missing_final_score")]
    rows = rd.build_opportunity_rows(observations, settlements=settlements)
    assert rows[0]["hypotheticalYesReturn"] is None
    assert rows[0]["hypotheticalNoReturn"] is None
    assert rows[0]["settlementStatus"] == "SETTLEMENT_UNRESOLVED"


def test_void_settlement_never_produces_a_hypothetical_return():
    observations = [_obs("o1")]
    settlements = [_settlement(status="VOID", result=None)]
    rows = rd.build_opportunity_rows(observations, settlements=settlements)
    assert rows[0]["hypotheticalYesReturn"] is None
    assert rows[0]["hypotheticalNoReturn"] is None


def test_yes_hypothetical_return_math():
    observations = [_obs("o1", yes_ask=40.0)]  # 0.40 executable YES price
    settlements = [_settlement(status="SETTLED", result="YES")]
    rows = rd.build_opportunity_rows(observations, settlements=settlements)
    assert abs(rows[0]["hypotheticalYesReturn"] - (0.6 / 0.4)) < 1e-9


def test_yes_hypothetical_return_loss():
    observations = [_obs("o1", yes_ask=40.0)]
    settlements = [_settlement(status="SETTLED", result="NO")]
    rows = rd.build_opportunity_rows(observations, settlements=settlements)
    assert rows[0]["hypotheticalYesReturn"] == -1.0


def test_no_hypothetical_return_math():
    observations = [_obs("o1", no_ask=45.0)]  # 0.45 executable NO price
    settlements = [_settlement(status="SETTLED", result="NO")]
    rows = rd.build_opportunity_rows(observations, settlements=settlements)
    assert abs(rows[0]["hypotheticalNoReturn"] - (0.55 / 0.45)) < 1e-3


def test_no_hypothetical_return_loss():
    observations = [_obs("o1", no_ask=45.0)]
    settlements = [_settlement(status="SETTLED", result="YES")]
    rows = rd.build_opportunity_rows(observations, settlements=settlements)
    assert rows[0]["hypotheticalNoReturn"] == -1.0


# ── Model-state temporal alignment ────────────────────────────────────────

def test_model_state_uses_temporally_valid_evaluation_only():
    observations = [_obs("o1", captured_at="2026-08-07T18:00:00Z")]
    evaluations = [_evaluation("e1", pipeline_run_id="2026-08-07T12:00:00Z", model_fair_probability=64.0)]
    rows = rd.build_opportunity_rows(observations, evaluations=evaluations)
    assert rows[0]["modelEvaluationAvailable"] is True
    assert abs(rows[0]["modelFairProbability"] - 0.64) < 1e-9  # normalized 0-100 -> 0-1


def test_future_evaluation_never_attaches_to_earlier_checkpoint():
    observations = [_obs("o1", captured_at="2026-08-07T10:00:00Z")]
    evaluations = [_evaluation("e1", pipeline_run_id="2026-08-07T12:00:00Z")]  # produced AFTER this checkpoint
    rows = rd.build_opportunity_rows(observations, evaluations=evaluations)
    assert rows[0]["modelEvaluationAvailable"] is False
    assert rows[0]["modelEvaluationUnavailableReason"] == rd.MODEL_UNAVAILABLE_FUTURE_ONLY
    assert rows[0]["modelFairProbability"] is None


def test_contemporaneous_edge_uses_this_checkpoints_own_price():
    """edge must be computed against THIS row's own executable price, not the evaluation's own marketImpliedProbability snapshot."""
    observations = [_obs("o1", captured_at="2026-08-07T18:00:00Z", yes_ask=50.0)]  # 0.50 at this checkpoint
    evaluations = [_evaluation("e1", pipeline_run_id="2026-08-07T12:00:00Z", model_fair_probability=60.0, market_implied_probability=45.0)]
    rows = rd.build_opportunity_rows(observations, evaluations=evaluations)
    # model 0.60 - this checkpoint's own 0.50 price = 0.10, NOT 0.60-0.45=0.15 (the evaluation's own stale snapshot).
    assert abs(rows[0]["contemporaneousEdge"] - 0.10) < 1e-9
    assert rows[0]["estimatedEdgeAtEvaluationTime"] == 5.0  # the pipeline's own (differently-scaled) figure, kept separate


def test_model_evaluation_unavailable_when_not_loaded_at_all():
    observations = [_obs("o1")]
    rows = rd.build_opportunity_rows(observations)  # evaluations=None
    assert rows[0]["modelEvaluationAvailable"] is False
    assert rows[0]["modelEvaluationUnavailableReason"] is None  # never guesses a reason when the source wasn't even checked


# ── Price movement ─────────────────────────────────────────────────────

def test_price_movement_to_close_computed_for_non_closing_rows():
    observations = [
        _obs("o1", checkpoint="T_MINUS_30", captured_at="2026-08-07T18:00:00Z", yes_ask=40.0, is_closing_candidate=True),
        _obs("o2", checkpoint="T_MINUS_5", captured_at="2026-08-07T18:25:00Z", yes_ask=48.0, is_closing_candidate=True),
    ]
    rows = rd.build_opportunity_rows(observations)
    by_checkpoint = {r["checkpoint"]: r for r in rows}
    t30 = by_checkpoint["T_MINUS_30"]
    assert t30["closingExecutableYesPrice"] == by_checkpoint["T_MINUS_5"]["executableYesPrice"]
    assert abs(t30["fullUniverseMarketMovementToClose"] - 0.08) < 1e-9
    assert by_checkpoint["T_MINUS_5"]["fullUniverseMarketMovementToClose"] is None  # the closing row itself has no "move to close"
