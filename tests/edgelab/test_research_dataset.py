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


def test_no_closing_quote_when_scheduled_start_unresolved():
    """
    Regression for the KXMLBHRR-26AUG141910SDCLE-CLESKWAN38-5 measurement
    bug (data/edgelab/reports/market_price_calibration_audit.md): when a
    ticker's scheduledStart never resolves (no observation-level value,
    no matching game record) and no actualStart exists either, NOTHING
    can be verified pre-start -- not even the earliest tick -- so no row
    for this ticker may have isClosingQuote=True, no matter how much
    later a subsequent tick was captured.
    """
    observations = [
        _obs("o1", checkpoint="FIRST_DAILY", captured_at="2026-08-14T05:26:45Z", scheduled_start=None),
        _obs("o2", checkpoint="INTERMEDIATE", captured_at="2026-08-14T23:53:18Z", scheduled_start=None,
             yes_bid=0.0, yes_ask=97.0, no_bid=None, no_ask=None),
    ]
    rows = rd.build_opportunity_rows(observations)  # no `games` supplied -> actualStart also unresolved
    assert all(r["isClosingQuote"] is False for r in rows)
    assert all(r["minutesToStart"] is None for r in rows)
    # The dangerous post-start-like tick (o2) is neither a named checkpoint
    # nor (now, correctly) ever selected as closing, so it never becomes a
    # row at all -- it doesn't just fail to be flagged, it never leaks in.
    assert len(rows) == 1
    assert {r["marketObservationId"] for r in rows} == {"o1"}
    # FIRST_DAILY still becomes its own named-checkpoint row (that classification is independent of closing-quote selection) -- it just never gets promoted to CLOSING.
    first_daily = rows[0]
    assert first_daily["researchCheckpoint"] == "FIRST_DAILY"


def test_closing_quote_still_selected_once_scheduled_start_resolves():
    """Sanity counterpart: the exact same shape of history, but with a resolved scheduledStart, correctly selects the last pregame tick as closing -- proving the fix gates on unresolved timing specifically, not on having more than one observation."""
    observations = [
        _obs("o1", checkpoint="FIRST_DAILY", captured_at="2026-08-14T05:26:45Z", scheduled_start="2026-08-14T19:10:00Z"),
        _obs("o2", checkpoint="T_MINUS_15", captured_at="2026-08-14T18:55:00Z", scheduled_start="2026-08-14T19:10:00Z"),
    ]
    rows = rd.build_opportunity_rows(observations)
    closing_rows = [r for r in rows if r["isClosingQuote"]]
    assert len(closing_rows) == 1
    assert closing_rows[0]["marketObservationId"] == "o2"
    assert closing_rows[0]["minutesToStart"] == 15.0


def test_two_sided_executable_price_correct_on_valid_closing_quote():
    """The fix touches only WHICH observation is selected as closing, never the yes/no executable-price extraction on the row that IS selected."""
    observations = [_obs("o1", checkpoint="T_MINUS_5", captured_at="2026-08-07T18:25:00Z", yes_bid=44.0, yes_ask=46.0, no_bid=54.0, no_ask=56.0)]
    rows = rd.build_opportunity_rows(observations)
    assert rows[0]["isClosingQuote"] is True
    assert rows[0]["executableYesPrice"] == 0.46  # yesAsk preferred
    assert rows[0]["executableNoPrice"] == 0.56   # noAsk preferred


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


# ── marketPriceAgeSeconds / model-evaluation-time provenance (reliability pass) ──

def test_market_price_age_bucket_boundaries():
    assert rd.market_price_age_bucket(None) == rd.PRICE_AGE_UNAVAILABLE
    assert rd.market_price_age_bucket(0) == rd.PRICE_AGE_LE_5MIN
    assert rd.market_price_age_bucket(5 * 60) == rd.PRICE_AGE_LE_5MIN
    assert rd.market_price_age_bucket(5 * 60 + 1) == rd.PRICE_AGE_5_15MIN
    assert rd.market_price_age_bucket(15 * 60) == rd.PRICE_AGE_5_15MIN
    assert rd.market_price_age_bucket(15 * 60 + 1) == rd.PRICE_AGE_15_30MIN
    assert rd.market_price_age_bucket(30 * 60) == rd.PRICE_AGE_15_30MIN
    assert rd.market_price_age_bucket(30 * 60 + 1) == rd.PRICE_AGE_GT_30MIN


def test_earlier_market_observation_links_with_correct_positive_age():
    """A T_MINUS_30 model evaluation paired with a Kalshi observation captured several minutes earlier -- a valid causal pairing, positive age."""
    observations = [
        _obs("o_early", ticker="T1", captured_at="2026-08-07T17:45:00Z", checkpoint="T_MINUS_60"),
        _obs("o_late", ticker="T1", captured_at="2026-08-07T18:00:00Z", checkpoint="T_MINUS_30"),
    ]
    evaluations = [_evaluation("e1", ticker="T1", pipeline_run_id="2026-08-07T17:55:00Z", checkpoint="T_MINUS_30")]
    rows = rd.build_opportunity_rows(observations, evaluations=evaluations)
    row = next(r for r in rows if r["checkpoint"] == "T_MINUS_30")
    # Latest observation at-or-before 17:55 is o_early (17:45) -- NOT o_late (18:00, which is AFTER the model ran).
    assert row["marketObservationCapturedAtForModelEval"] == "2026-08-07T17:45:00Z"
    assert row["marketPriceAgeSeconds"] == 600.0  # 10 minutes
    assert row["marketPriceAgeBucket"] == rd.PRICE_AGE_5_15MIN


def test_later_market_observation_never_used_for_price_age():
    """No observation exists BEFORE the model evaluated -- linkage must stay unavailable, never attach the later quote."""
    observations = [_obs("o1", ticker="T1", captured_at="2026-08-07T18:00:00Z", checkpoint="T_MINUS_30")]
    evaluations = [_evaluation("e1", ticker="T1", pipeline_run_id="2026-08-07T12:00:00Z", checkpoint="T_MINUS_90")]
    rows = rd.build_opportunity_rows(observations, evaluations=evaluations)
    row = rows[0]
    assert row["modelEvaluationAvailable"] is True  # the temporal_alignment join itself is still valid (12:00 <= 18:00)
    assert row["marketObservationCapturedAtForModelEval"] is None
    assert row["marketPriceAgeSeconds"] is None
    assert row["marketPriceAgeBucket"] == rd.PRICE_AGE_UNAVAILABLE


def test_market_price_age_never_negative():
    """A five-minute-old quote and a twenty-minute-old quote are both valid (non-negative); a hypothetically-later quote is excluded by construction, never surfaced as negative."""
    observations = [
        _obs("o1", ticker="T1", captured_at="2026-08-07T17:40:00Z", checkpoint="T_MINUS_60"),
        _obs("o2", ticker="T1", captured_at="2026-08-07T18:20:00Z", checkpoint="T_MINUS_5"),  # AFTER the model eval below
    ]
    evaluations = [_evaluation("e1", ticker="T1", pipeline_run_id="2026-08-07T18:00:00Z", checkpoint="T_MINUS_30")]
    rows = rd.build_opportunity_rows(observations, evaluations=evaluations)
    for row in rows:
        if row["marketPriceAgeSeconds"] is not None:
            assert row["marketPriceAgeSeconds"] >= 0


def test_checkpoint_timing_error_computed_for_time_target_checkpoints():
    """Actual model-evaluation time vs the nominal T_MINUS_30 target (30 min) -- e.g. an evaluation actually run at T-26m42s."""
    observations = [_obs("o1", ticker="T1", captured_at="2026-08-07T18:10:00Z", checkpoint="T_MINUS_30", scheduled_start="2026-08-07T18:30:00Z")]
    # Model evaluated at 18:03:18 (before the observation, so causally valid) -> 26.7 min to start (T-26m42s), nominal target 30 min -> error = (26.7-30)*60 = -198s.
    evaluations = [_evaluation("e1", ticker="T1", pipeline_run_id="2026-08-07T18:03:18Z", checkpoint="T_MINUS_30")]
    rows = rd.build_opportunity_rows(observations, evaluations=evaluations)
    row = rows[0]
    assert row["modelEvaluationCheckpoint"] == "T_MINUS_30"
    assert abs(row["modelEvaluationMinutesToStart"] - 26.7) < 0.1
    assert row["checkpointTimingErrorSeconds"] is not None
    assert abs(row["checkpointTimingErrorSeconds"] - (-198)) < 5


def test_checkpoint_timing_error_none_for_non_time_target_checkpoints():
    observations = [_obs("o1", ticker="T1", captured_at="2026-08-07T18:00:00Z", checkpoint="LINEUP_CONFIRMATION")]
    evaluations = [_evaluation("e1", ticker="T1", pipeline_run_id="2026-08-07T17:50:00Z", checkpoint="LINEUP_CONFIRMATION")]
    rows = rd.build_opportunity_rows(observations, evaluations=evaluations)
    assert rows[0]["checkpointTimingErrorSeconds"] is None
    assert rows[0]["modelEvaluationMinutesToStart"] is not None  # still reported, just no "error vs target" concept


def test_input_freshness_note_passed_through_to_row():
    observations = [_obs("o1", ticker="T1")]
    evaluations = [_evaluation("e1", ticker="T1", inputFreshnessNote="ALL_INPUTS_PERSISTED_FROM_SLATE_AT_LAST_PIPELINE_FETCH")]
    rows = rd.build_opportunity_rows(observations, evaluations=evaluations)
    assert rows[0]["inputFreshnessNote"] == "ALL_INPUTS_PERSISTED_FROM_SLATE_AT_LAST_PIPELINE_FETCH"


def test_model_closing_window_distinct_from_market_closing_checkpoint():
    """The row's own market-side researchCheckpoint ('CLOSING', isClosingQuote-derived) and the model's own modelEvaluationCheckpoint ('MODEL_CLOSING_WINDOW') must never be conflated into the same field."""
    observations = [_obs("o1", ticker="T1", checkpoint="T_MINUS_5", captured_at="2026-08-07T18:25:00Z")]  # sole obs -> also becomes CLOSING
    evaluations = [_evaluation("e1", ticker="T1", pipeline_run_id="2026-08-07T18:20:00Z", checkpoint="MODEL_CLOSING_WINDOW")]
    rows = rd.build_opportunity_rows(observations, evaluations=evaluations)
    row = rows[0]
    assert row["researchCheckpoint"] == "CLOSING"  # market-side concept (PR #86)
    assert row["modelEvaluationCheckpoint"] == "MODEL_CLOSING_WINDOW"  # model-side concept (this milestone) -- visibly distinct
