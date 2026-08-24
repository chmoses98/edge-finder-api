#!/usr/bin/env python3
"""
tests/edgelab/test_daily_health.py
================================================================
Coverage for lib.edgelab.daily_health.compute_daily_health -- the pure
classification core of the EdgeLab Daily Pipeline Heartbeat/Watchdog
(Pipeline Health Incident follow-up, 2026-08-24). Every historical
regression case this mission exists to catch is asserted directly
against a hand-built `inputs` dict here, with no filesystem or network
involved -- see scripts/edgelab/daily_health_check.py for the real
fact-gathering this module is deliberately decoupled from.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab import daily_health
from lib.edgelab.daily_health import compute_daily_health

CHECKED_AT = "2026-08-24T23:45:00Z"


def _base_inputs(**overrides):
    """A fully-healthy baseline `inputs` dict -- every test overrides only what it needs to break."""
    inputs = {
        "date": "2026-08-20",
        "gamesScheduledToday": 12,
        "marketsObservedCount": 5000,
        "recommendationsFileExists": True,
        "recommendationsIsCurrentDate": True,
        "recommendationsProvenanceValid": True,
        "recommendationsRowCount": 12,
        "modelEvaluationsFileExists": True,
        "modelEvaluationsIsCurrentDate": True,
        "modelEvaluationsRowCount": 400,
        "preGameDecisionSnapshotFileExists": True,
        "preGameDecisionSnapshotIsSameDayCapture": True,
        "preGameDecisionSnapshotCompletenessStatus": "PARTIAL_REPLAY",
        "settlementDateChecked": "2026-08-19",
        "settlementsExpected": True,
        "settlementsFileExists": True,
        "settlementsRowCount": 5100,
        "fullUniverseExtensionRowCount": 5200,
    }
    inputs.update(overrides)
    return inputs


class TestHealthyDay:
    def test_fully_healthy_day_is_healthy_with_no_reasons(self):
        record = compute_daily_health(_base_inputs(), CHECKED_AT)
        assert record["healthStatus"] == daily_health.HEALTH_STATUS_HEALTHY
        assert record["reasons"] == []
        assert record["artifactFreshnessStatus"] == "CURRENT"

    def test_partial_replay_completeness_status_alone_does_not_disqualify_a_real_same_day_capture(self):
        """PARTIAL_REPLAY is the NORMAL completenessStatus on genuinely healthy, on-time captures too -- only late (non-same-day) capture timing should disqualify a snapshot."""
        record = compute_daily_health(_base_inputs(preGameDecisionSnapshotCompletenessStatus="PARTIAL_REPLAY"), CHECKED_AT)
        assert record["healthStatus"] == daily_health.HEALTH_STATUS_HEALTHY
        assert record["preGameDecisionSnapshotPresent"] is True


class TestHistoricalRegressionCaseA_Aug11to15Blackout:
    """fetch-slate.yml had no schedule trigger -- recommendations.json and the PRE_GAME_DECISION
    snapshot silently never got produced for 5 real MLB game days, while settlement (a fully
    independent pipeline) stayed healthy the whole time."""

    def test_missing_recommendations_and_snapshot_is_unhealthy(self):
        inputs = _base_inputs(
            recommendationsFileExists=False,
            recommendationsRowCount=0,
            preGameDecisionSnapshotFileExists=False,
            preGameDecisionSnapshotIsSameDayCapture=False,
            preGameDecisionSnapshotCompletenessStatus=None,
        )
        record = compute_daily_health(inputs, CHECKED_AT)
        assert record["healthStatus"] == daily_health.HEALTH_STATUS_UNHEALTHY
        assert any(r.startswith(daily_health.REASON_MISSING_RECOMMENDATIONS) for r in record["reasons"])
        assert any(r.startswith(daily_health.REASON_MISSING_PRE_GAME_DECISION_SNAPSHOT) for r in record["reasons"])

    def test_settlement_stays_healthy_and_unflagged_during_the_recommendation_blackout(self):
        """Settlement is architecturally independent of the slate/recommendation chain -- proven
        real during the actual 2026-08-11..15 outage (settlements/2026-08-11..15 all had real
        rows despite recommendations.json never existing). The watchdog must reflect that: no
        settlement-related reason should appear when settlement itself is genuinely healthy."""
        inputs = _base_inputs(
            recommendationsFileExists=False,
            recommendationsRowCount=0,
            preGameDecisionSnapshotFileExists=False,
            preGameDecisionSnapshotIsSameDayCapture=False,
            preGameDecisionSnapshotCompletenessStatus=None,
        )
        record = compute_daily_health(inputs, CHECKED_AT)
        assert record["settlementsProduced"] is True
        assert not any("SETTLEMENT" in r for r in record["reasons"])


class TestHistoricalRegressionCaseB_DuckdbOutage:
    """build_recommendations.py started importing duckdb but edgelab-postgame.yml never installed
    it -- masked by continue-on-error, RECOMMENDATION_SYNC's full-universe ModelEvaluation
    extension silently produced zero rows for a week while recommendations.json (a completely
    different, unaffected artifact) and today's base model_evaluations coverage (written
    independently by model-snapshot-scheduler.yml) both looked completely normal."""

    def test_recommendations_and_base_model_evaluations_present_but_full_universe_extension_absent_is_unhealthy(self):
        inputs = _base_inputs(
            recommendationsFileExists=True,
            recommendationsRowCount=12,
            modelEvaluationsFileExists=True,
            modelEvaluationsRowCount=350,  # real rows exist -- from the independent prospective scheduler, NOT RECOMMENDATION_SYNC
            fullUniverseExtensionRowCount=0,  # this is the actual broken signal
        )
        record = compute_daily_health(inputs, CHECKED_AT)
        assert record["healthStatus"] == daily_health.HEALTH_STATUS_UNHEALTHY
        assert record["recommendationsProduced"] is True
        assert record["modelEvaluationsProduced"] is True
        assert any(r.startswith(daily_health.REASON_MISSING_MODEL_EVALUATIONS) and "RECOMMENDATION_SYNC" in r for r in record["reasons"])

    def test_naive_nonzero_row_count_check_alone_would_have_missed_this_outage(self):
        """Documents exactly why a bare 'model_evaluations file has >0 rows' check is insufficient
        -- the independent prospective scheduler keeps writing real rows the whole time; only
        checking fullUniverseExtensionRowCount specifically catches this."""
        healthy_inputs = _base_inputs(fullUniverseExtensionRowCount=5200)
        broken_inputs = _base_inputs(fullUniverseExtensionRowCount=0)
        assert healthy_inputs["modelEvaluationsRowCount"] == broken_inputs["modelEvaluationsRowCount"]
        healthy_record = compute_daily_health(healthy_inputs, CHECKED_AT)
        broken_record = compute_daily_health(broken_inputs, CHECKED_AT)
        assert healthy_record["healthStatus"] == daily_health.HEALTH_STATUS_HEALTHY
        assert broken_record["healthStatus"] == daily_health.HEALTH_STATUS_UNHEALTHY


class TestNoGameDay:
    def test_zero_games_scheduled_and_no_settlement_expected_is_no_mlb_games_not_unhealthy(self):
        inputs = _base_inputs(
            gamesScheduledToday=0,
            marketsObservedCount=0,
            recommendationsFileExists=False,
            recommendationsRowCount=0,
            modelEvaluationsFileExists=False,
            modelEvaluationsRowCount=0,
            preGameDecisionSnapshotFileExists=False,
            preGameDecisionSnapshotIsSameDayCapture=False,
            preGameDecisionSnapshotCompletenessStatus=None,
            settlementsExpected=False,
            settlementsFileExists=False,
            settlementsRowCount=0,
            fullUniverseExtensionRowCount=0,
        )
        record = compute_daily_health(inputs, CHECKED_AT)
        assert record["healthStatus"] == daily_health.HEALTH_STATUS_NO_MLB_GAMES
        assert record["reasons"] == []

    def test_today_off_but_yesterday_still_needs_real_settlement_checking(self):
        """An off-day today must not silently suppress a genuine settlement problem for
        yesterday -- NO_MLB_GAMES only applies when NEITHER side has anything to check."""
        inputs = _base_inputs(
            gamesScheduledToday=0,
            marketsObservedCount=0,
            recommendationsFileExists=False,
            recommendationsRowCount=0,
            modelEvaluationsFileExists=False,
            modelEvaluationsRowCount=0,
            preGameDecisionSnapshotFileExists=False,
            preGameDecisionSnapshotIsSameDayCapture=False,
            preGameDecisionSnapshotCompletenessStatus=None,
            settlementsExpected=True,
            settlementsFileExists=False,
            settlementsRowCount=0,
            fullUniverseExtensionRowCount=0,
        )
        record = compute_daily_health(inputs, CHECKED_AT)
        assert record["healthStatus"] == daily_health.HEALTH_STATUS_UNHEALTHY
        assert any(r.startswith(daily_health.REASON_MISSING_SETTLEMENTS) for r in record["reasons"])

    def test_unavailable_live_schedule_check_defaults_to_eligible_not_no_mlb_games(self):
        """A failed/unavailable live MLB schedule fetch (gamesScheduledToday=None) must never be
        silently treated as a legitimate off-day -- that would let a real outage masquerade as
        NO_MLB_GAMES. Must fail toward checking too much, not too little."""
        inputs = _base_inputs(gamesScheduledToday=None, recommendationsFileExists=False, recommendationsRowCount=0)
        record = compute_daily_health(inputs, CHECKED_AT)
        assert record["healthStatus"] == daily_health.HEALTH_STATUS_UNHEALTHY
        assert any(r.startswith(daily_health.REASON_MISSING_RECOMMENDATIONS) for r in record["reasons"])


class TestStaleArtifact:
    def test_recommendations_present_but_wrong_date_is_stale_not_healthy(self):
        inputs = _base_inputs(recommendationsIsCurrentDate=False)
        record = compute_daily_health(inputs, CHECKED_AT)
        assert record["healthStatus"] == daily_health.HEALTH_STATUS_UNHEALTHY
        assert record["recommendationsProduced"] is False
        assert any(r.startswith(daily_health.REASON_STALE_ARTIFACT) for r in record["reasons"])
        assert record["artifactFreshnessStatus"] == "STALE"


class TestZeroRowsDespiteEligibleMarkets:
    def test_recommendations_file_exists_current_but_zero_games_covered_is_unhealthy(self):
        inputs = _base_inputs(recommendationsRowCount=0)
        record = compute_daily_health(inputs, CHECKED_AT)
        assert record["healthStatus"] == daily_health.HEALTH_STATUS_UNHEALTHY
        assert any(r.startswith(daily_health.REASON_ZERO_ROWS_WITH_ELIGIBLE_MARKETS) for r in record["reasons"])

    def test_zero_market_observations_despite_scheduled_games_is_unhealthy(self):
        inputs = _base_inputs(marketsObservedCount=0)
        record = compute_daily_health(inputs, CHECKED_AT)
        assert record["healthStatus"] == daily_health.HEALTH_STATUS_UNHEALTHY
        assert any(r.startswith(daily_health.REASON_ZERO_MARKET_OBSERVATIONS) for r in record["reasons"])

    def test_settlements_file_exists_but_zero_rows_is_unhealthy(self):
        inputs = _base_inputs(settlementsRowCount=0)
        record = compute_daily_health(inputs, CHECKED_AT)
        assert record["healthStatus"] == daily_health.HEALTH_STATUS_UNHEALTHY
        assert any(r.startswith(daily_health.REASON_ZERO_ROWS_WITH_ELIGIBLE_MARKETS) for r in record["reasons"])


class TestInvalidProvenance:
    def test_recommendations_without_valid_provenance_marker_is_unhealthy(self):
        inputs = _base_inputs(recommendationsProvenanceValid=False)
        record = compute_daily_health(inputs, CHECKED_AT)
        assert record["healthStatus"] == daily_health.HEALTH_STATUS_UNHEALTHY
        assert record["recommendationsProduced"] is False
        assert any(r.startswith(daily_health.REASON_INVALID_PROVENANCE) for r in record["reasons"])


class TestPartialReplayVsTrueProspectiveSnapshot:
    def test_late_recovery_capture_never_masquerades_as_true_prospective_coverage(self):
        """A manifest that exists under today's date directory but was captured on a LATER
        calendar date is scripts/check_snapshot_capture.py's late recovery re-running
        build_snapshot() days after the fact -- never a genuine same-day prospective capture."""
        inputs = _base_inputs(
            preGameDecisionSnapshotFileExists=True,
            preGameDecisionSnapshotIsSameDayCapture=False,
            preGameDecisionSnapshotCompletenessStatus="PARTIAL_REPLAY",
        )
        record = compute_daily_health(inputs, CHECKED_AT)
        assert record["healthStatus"] == daily_health.HEALTH_STATUS_UNHEALTHY
        assert record["preGameDecisionSnapshotPresent"] is False
        assert any(r.startswith(daily_health.REASON_MISSING_PRE_GAME_DECISION_SNAPSHOT) for r in record["reasons"])

    def test_missing_required_input_completeness_status_is_never_true_coverage_even_if_same_day(self):
        inputs = _base_inputs(
            preGameDecisionSnapshotFileExists=True,
            preGameDecisionSnapshotIsSameDayCapture=True,
            preGameDecisionSnapshotCompletenessStatus="MISSING_REQUIRED_INPUT",
        )
        record = compute_daily_health(inputs, CHECKED_AT)
        assert record["preGameDecisionSnapshotPresent"] is False
        assert record["healthStatus"] == daily_health.HEALTH_STATUS_UNHEALTHY


class TestHealthArtifactSchema:
    EXPECTED_FIELDS = {
        "schemaVersion", "date", "checkedAt", "marketsObserved", "slateRunsExpected", "slateRunsObserved",
        "recommendationsExpected", "recommendationsProduced", "recommendationRowCount",
        "modelEvaluationsExpected", "modelEvaluationsProduced", "modelEvaluationRowCount",
        "preGameDecisionSnapshotExpected", "preGameDecisionSnapshotPresent",
        "settlementDateChecked", "settlementsExpected", "settlementsProduced", "settlementRowCount",
        "fullUniverseExtensionRowCount", "artifactFreshnessStatus", "healthStatus", "reasons",
    }

    def test_record_contains_every_required_field(self):
        record = compute_daily_health(_base_inputs(), CHECKED_AT)
        assert self.EXPECTED_FIELDS.issubset(record.keys())

    def test_health_status_is_always_one_of_the_valid_enum_values(self):
        for inputs in (
            _base_inputs(),
            _base_inputs(recommendationsFileExists=False),
            _base_inputs(gamesScheduledToday=0, marketsObservedCount=0, recommendationsFileExists=False,
                         recommendationsRowCount=0, modelEvaluationsFileExists=False, modelEvaluationsRowCount=0,
                         preGameDecisionSnapshotFileExists=False, preGameDecisionSnapshotIsSameDayCapture=False,
                         preGameDecisionSnapshotCompletenessStatus=None, settlementsExpected=False,
                         settlementsFileExists=False, settlementsRowCount=0, fullUniverseExtensionRowCount=0),
        ):
            record = compute_daily_health(inputs, CHECKED_AT)
            assert record["healthStatus"] in daily_health.VALID_HEALTH_STATUSES

    def test_reasons_is_always_a_list_of_strings(self):
        record = compute_daily_health(_base_inputs(recommendationsFileExists=False), CHECKED_AT)
        assert isinstance(record["reasons"], list)
        assert all(isinstance(r, str) for r in record["reasons"])

    def test_checked_at_and_date_are_passed_through_verbatim(self):
        record = compute_daily_health(_base_inputs(date="2026-08-21"), "2026-08-21T23:45:00Z")
        assert record["date"] == "2026-08-21"
        assert record["checkedAt"] == "2026-08-21T23:45:00Z"


class TestFullUniverseProbabilityCoverageGate:
    """Phase 2 (Full-Universe MLB Kalshi Probability Persistence), item 13."""

    def test_missing_coverage_artifact_never_causes_a_false_failure(self):
        # coverageArtifactAvailable defaults to False in _base_inputs()
        # (the field is simply absent) -- must never itself degrade a
        # healthy day.
        record = compute_daily_health(_base_inputs(), CHECKED_AT)
        assert record["healthStatus"] == daily_health.HEALTH_STATUS_HEALTHY
        assert not any(r.startswith(daily_health.REASON_LOW_PROBABILITY_COVERAGE) for r in record["reasons"])

    def test_zero_supported_population_never_causes_a_false_failure(self):
        # Every archived ticker today happens to belong to an unsupported
        # family -- coverage is measured only against the population the
        # model actually claims to support, so a zero denominator must
        # never itself be treated as a failure.
        record = compute_daily_health(_base_inputs(
            coverageArtifactAvailable=True, archivedSupportedTickerCount=0,
            evaluatedProbabilityCount=0,
        ), CHECKED_AT)
        assert record["healthStatus"] == daily_health.HEALTH_STATUS_HEALTHY
        assert record["probabilityCoveragePct"] is None

    def test_high_coverage_stays_healthy(self):
        record = compute_daily_health(_base_inputs(
            coverageArtifactAvailable=True, archivedSupportedTickerCount=300,
            evaluatedProbabilityCount=300,
        ), CHECKED_AT)
        assert record["healthStatus"] == daily_health.HEALTH_STATUS_HEALTHY
        assert record["probabilityCoveragePct"] == 100.0

    def test_low_coverage_with_otherwise_healthy_day_is_degraded_not_unhealthy(self):
        record = compute_daily_health(_base_inputs(
            coverageArtifactAvailable=True, archivedSupportedTickerCount=300,
            evaluatedProbabilityCount=100,
        ), CHECKED_AT)
        assert record["healthStatus"] == daily_health.HEALTH_STATUS_DEGRADED
        assert any(r.startswith(daily_health.REASON_LOW_PROBABILITY_COVERAGE) for r in record["reasons"])
        assert record["probabilityCoveragePct"] == round(100.0 * 100 / 300, 2)

    def test_low_coverage_never_masks_a_genuine_unhealthy_reason(self):
        # A real UNHEALTHY condition (missing recommendations) must not
        # be downgraded to DEGRADED just because coverage also happens
        # to be low that day -- the worse condition wins.
        record = compute_daily_health(_base_inputs(
            recommendationsFileExists=False,
            coverageArtifactAvailable=True, archivedSupportedTickerCount=300,
            evaluatedProbabilityCount=50,
        ), CHECKED_AT)
        assert record["healthStatus"] == daily_health.HEALTH_STATUS_UNHEALTHY
        assert any(r.startswith(daily_health.REASON_LOW_PROBABILITY_COVERAGE) for r in record["reasons"])
        assert any(r.startswith(daily_health.REASON_MISSING_RECOMMENDATIONS) for r in record["reasons"])

    def test_unsupported_and_suspended_families_never_penalize_coverage(self):
        # unsupportedCount/suspendedCount are reported, but are NOT part
        # of the denominator -- a family with genuinely no adapter must
        # never drag coverage down.
        record = compute_daily_health(_base_inputs(
            coverageArtifactAvailable=True, archivedSupportedTickerCount=100,
            evaluatedProbabilityCount=100, unsupportedCount=5000, suspendedCount=12,
        ), CHECKED_AT)
        assert record["healthStatus"] == daily_health.HEALTH_STATUS_HEALTHY
        assert record["unsupportedCount"] == 5000
        assert record["suspendedCount"] == 12

    def test_coverage_fields_present_on_every_record(self):
        record = compute_daily_health(_base_inputs(), CHECKED_AT)
        for field in ("coverageArtifactAvailable", "archivedSupportedTickerCount", "evaluatedTickerCount",
                      "evaluatedProbabilityCount", "missingInputCount", "unsupportedCount", "suspendedCount",
                      "probabilityCoveragePct", "familyCoverageBreakdown"):
            assert field in record

    def test_family_coverage_breakdown_passed_through(self):
        breakdown = {"team_total": {"archivedSupportedTickerCount": 14, "evaluatedProbabilityCount": 14, "probabilityCoveragePct": 100.0}}
        record = compute_daily_health(_base_inputs(
            coverageArtifactAvailable=True, archivedSupportedTickerCount=14,
            evaluatedProbabilityCount=14, familyCoverageBreakdown=breakdown,
        ), CHECKED_AT)
        assert record["familyCoverageBreakdown"] == breakdown
