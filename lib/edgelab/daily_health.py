"""
lib/edgelab/daily_health.py
================================================================
EdgeLab Daily Pipeline Heartbeat/Watchdog -- independent guardrail
(Pipeline Health Incident follow-up, 2026-08-24).

Two separate historical outages were both root-caused this session:
  1. fetch-slate.yml had no `schedule:` trigger of its own for its
     entire history until 2026-08-21 (PR #106) -- data/slate.json,
     data/pipeline/<date>/recommendations.json, and the
     PRE_GAME_DECISION snapshot all silently stopped refreshing for
     2026-08-11..15 whenever nobody remembered to workflow_dispatch it.
  2. scripts/edgelab/build_recommendations.py started importing duckdb
     (2026-08-17) but edgelab-postgame.yml never installed it --
     RECOMMENDATION_SYNC's full-universe ModelEvaluation extension
     silently produced zero rows for every run from 2026-08-18 onward,
     masked by continue-on-error.

Neither was a model-logic bug. Both were "an expected daily artifact
silently didn't exist, and nothing said so." This module is the pure
classification logic behind the fix: scripts/edgelab/daily_health_check.py
gathers real facts (file existence, row counts, provenance, a live MLB
schedule check) into a plain `inputs` dict and hands it to
compute_daily_health() here, which contains ALL of the actual
pass/fail/reason logic and touches no filesystem or network itself --
so every historical regression case this mission exists to catch can be
asserted directly against a hand-built `inputs` dict, no fixtures,
mocks, or tmp_path filesystem needed.

Design notes:
  - recommendations.json / the PRE_GAME_DECISION snapshot are TODAY-
    scoped (fetch-slate.yml writes them same-day, forward-looking).
  - Settlement AND RECOMMENDATION_SYNC's full-universe ModelEvaluation
    extension are both YESTERDAY-scoped (edgelab-postgame.yml runs the
    morning after, once the prior date's games are final) -- both are
    checked against `settlementDateChecked`, not `date`. This is
    deliberate: checking TODAY's model_evaluations file alone for
    "row count > 0" would NOT have caught outage #2, because
    model-snapshot-scheduler.yml's independent 15-minute prospective
    cycle keeps writing real rows to today's file regardless of whether
    RECOMMENDATION_SYNC's full-universe extension is alive -- that is
    exactly how outage #2 stayed invisible for a week. Catching it
    requires checking, for the settlement date specifically, that at
    least one ModelEvaluation row actually came from RECOMMENDATION_SYNC
    (source in FULL_UNIVERSE_EXTENSION_SOURCES), not merely that the
    file is non-empty.
  - A PRE_GAME_DECISION manifest whose own `capturedAt` falls on a
    LATER calendar date than its `date` field is a late recovery by
    scripts/check_snapshot_capture.py re-running the same
    build_snapshot() call days after the fact (confirmed by reading
    that script: "recovery: it just calls
    lib.edgelab.snapshot.build_snapshot() again") -- never a genuine
    same-day prospective capture, and must never be reported as if it
    were, however healthy its own completenessStatus/captureMode looks
    (both PARTIAL_REPLAY and LIVE_CAPTURE are the NORMAL values on
    genuinely healthy, on-time captures too, so neither field alone
    distinguishes "real" from "recovered late" -- same-day capture
    timing is the reliable signal).
"""

# "2" adds the `dateResolution` block (Heartbeat False-Failure Incident,
# 2026-08-27): a health artifact must be able to say WHICH production
# date it was asked about and WHY -- intended scheduled checkpoint,
# actual anchor, delay, trigger type. Every other field is unchanged, so
# a version "1" artifact stays readable; only `dateResolution` is absent
# from one (see lib/edgelab/production_date.py).
SCHEMA_VERSION = "2"

HEALTH_STATUS_HEALTHY = "HEALTHY"
HEALTH_STATUS_DEGRADED = "DEGRADED"
HEALTH_STATUS_UNHEALTHY = "UNHEALTHY"
HEALTH_STATUS_NO_MLB_GAMES = "NO_MLB_GAMES"

VALID_HEALTH_STATUSES = frozenset({
    HEALTH_STATUS_HEALTHY, HEALTH_STATUS_DEGRADED, HEALTH_STATUS_UNHEALTHY, HEALTH_STATUS_NO_MLB_GAMES,
})

REASON_MISSING_RECOMMENDATIONS = "MISSING_RECOMMENDATIONS"
REASON_MISSING_MODEL_EVALUATIONS = "MISSING_MODEL_EVALUATIONS"
REASON_MISSING_PRE_GAME_DECISION_SNAPSHOT = "MISSING_PRE_GAME_DECISION_SNAPSHOT"
REASON_MISSING_SETTLEMENTS = "MISSING_SETTLEMENTS"
REASON_STALE_ARTIFACT = "STALE_ARTIFACT"
REASON_ZERO_ROWS_WITH_ELIGIBLE_MARKETS = "ZERO_ROWS_WITH_ELIGIBLE_MARKETS"
REASON_INVALID_PROVENANCE = "INVALID_PROVENANCE"
REASON_ZERO_MARKET_OBSERVATIONS = "ZERO_MARKET_OBSERVATIONS_WITH_SCHEDULED_GAMES"
REASON_LOW_PROBABILITY_COVERAGE = "LOW_PROBABILITY_COVERAGE"

# Phase 2 Full-Universe Probability Persistence, item 13: minimum
# fraction of the SUPPORTED archived population (tickers whose family
# has a real adapter, per lib.kalshi_market_coverage's terminal-state
# taxonomy -- never the raw full archive, which also includes families
# with no adapter at all) that must actually carry a computed
# modelFairProbability before this is reported as a coverage problem.
# A first-cut, documented, tunable threshold -- not empirically
# calibrated yet (see the Phase 2 report's own recommendation on how
# much prospective data should accumulate before any threshold here is
# revisited).
MIN_PROBABILITY_COVERAGE_PCT = 80.0

# ModelEvaluation `source` values that can ONLY come from RECOMMENDATION_SYNC
# (scripts/edgelab/build_recommendations.py's extend_full_universe_evaluations()
# / build_model_evaluations_from_pipeline()) -- never from the independent
# model-snapshot-scheduler.yml prospective cycle (which always tags
# source="prospective_snapshot"). Presence of at least one row with one of
# these sources, for the settlement date, is the only reliable proof that
# RECOMMENDATION_SYNC actually ran and persisted -- exactly the signal
# outage #2 (the duckdb import crash) silently zeroed out for a week.
FULL_UNIVERSE_EXTENSION_SOURCES = frozenset({
    "market_universe_extension", "kalshi_discovery_extension", "pipeline_recommendations",
})


def compute_daily_health(inputs, checked_at, *, date_resolution=None):
    """
    Pure. `inputs` keys (all required):

      date                                        str, YYYY-MM-DD (today)
      gamesScheduledToday                          int or None (None = live MLB schedule check failed/unavailable)
      marketsObservedCount                         int
      recommendationsFileExists                    bool
      recommendationsIsCurrentDate                 bool
      recommendationsProvenanceValid               bool
      recommendationsRowCount                      int
      modelEvaluationsFileExists                   bool   (today's file)
      modelEvaluationsIsCurrentDate                bool
      modelEvaluationsRowCount                     int
      preGameDecisionSnapshotFileExists             bool
      preGameDecisionSnapshotIsSameDayCapture       bool
      preGameDecisionSnapshotCompletenessStatus     str or None
      settlementDateChecked                         str, YYYY-MM-DD (yesterday)
      settlementsExpected                           bool  (derived from yesterday's already-archived market corpus)
      settlementsFileExists                         bool
      settlementsRowCount                           int
      fullUniverseExtensionRowCount                 int   (settlement date, FULL_UNIVERSE_EXTENSION_SOURCES only)
      coverageArtifactAvailable                     bool  (whether a data/kalshi/discovery/<date>_coverage.json artifact exists for `date` -- see below)
      archivedSupportedTickerCount                  int   (pregame-scoped tickers whose family HAS a real adapter -- lib.kalshi_market_coverage's FULLY_EVALUATED+RESEARCH_MODEL_ONLY+MISSING_REQUIRED_CONTEXT+AMBIGUOUS_TICKER_MATCH)
      evaluatedProbabilityCount                     int   (of those, how many actually carry a computed probability -- FULLY_EVALUATED+RESEARCH_MODEL_ONLY)
      missingInputCount                             int   (MISSING_REQUIRED_CONTEXT+AMBIGUOUS_TICKER_MATCH)
      unsupportedCount                              int   (UNSUPPORTED_MODEL_FAMILY -- no adapter exists at all; never counted against coverage)
      suspendedCount                                int   (reserved for a family intentionally excluded by policy -- 0 today, see lib.edgelab.probability_status)
      familyCoverageBreakdown                       dict  ({family: {archivedSupportedTickerCount, evaluatedProbabilityCount, probabilityCoveragePct}})

    `date_resolution` is the (optional) audit record produced by
    lib.edgelab.production_date.resolve_target_date describing how
    `inputs["date"]` was chosen -- embedded verbatim as `dateResolution`
    and never consulted for any classification decision here. Its
    presence or absence can never change a HEALTHY/UNHEALTHY verdict:
    the strictness of every check below is exactly as it was before it
    existed.

    Returns the full health record dict (schema in this module's
    docstring / scripts/edgelab/daily_health_check.py).

    Coverage gate (item 13): only evaluated when coverageArtifactAvailable
    is True AND archivedSupportedTickerCount > 0 -- an absent coverage
    artifact (the discovery/coverage workflow simply hasn't run yet, or
    hasn't been backfilled for a historical date) is informational-only
    and NEVER itself produces a DEGRADED/UNHEALTHY reason ("no false
    failures" -- this coverage gate is additive to, not a replacement
    for, checks A-E above). A supported population of zero (every
    archived ticker today happens to belong to an unsupported family)
    is likewise never penalized -- coverage is measured only against the
    population the model actually claims to support.
    """
    date = inputs["date"]
    reasons = []

    games_scheduled_today = inputs["gamesScheduledToday"]
    # Defensive: an unavailable live schedule check (network hiccup) must
    # never itself suppress detection -- fail toward checking too much,
    # not too little. Only a schedule check that SUCCEEDED and explicitly
    # returned zero games marks today as a legitimate off-day.
    today_eligible = True if games_scheduled_today is None else games_scheduled_today > 0

    # ---- A. Market observations ----
    markets_observed = inputs["marketsObservedCount"]
    if today_eligible and markets_observed == 0:
        reasons.append(f"{REASON_ZERO_MARKET_OBSERVATIONS}: {games_scheduled_today} MLB game(s) scheduled for {date} but zero MarketObservation rows captured")

    # ---- B. Slate / recommendation chain (today) ----
    recommendations_expected = today_eligible
    recommendations_produced = False
    recommendations_row_count = inputs["recommendationsRowCount"]
    if recommendations_expected:
        if not inputs["recommendationsFileExists"]:
            reasons.append(f"{REASON_MISSING_RECOMMENDATIONS}: data/pipeline/{date}/recommendations.json does not exist")
        elif not inputs["recommendationsIsCurrentDate"]:
            reasons.append(f"{REASON_STALE_ARTIFACT}: recommendations.json exists but is not dated {date}")
        elif not inputs["recommendationsProvenanceValid"]:
            reasons.append(f"{REASON_INVALID_PROVENANCE}: recommendations.json has no valid same-date production provenance marker")
        elif recommendations_row_count == 0:
            reasons.append(f"{REASON_ZERO_ROWS_WITH_ELIGIBLE_MARKETS}: recommendations.json exists but covers 0 games despite {games_scheduled_today} scheduled")
        else:
            recommendations_produced = True
    else:
        recommendations_produced = inputs["recommendationsFileExists"]

    # ---- C. ModelEvaluation persistence (today's file, base coverage) ----
    model_evals_expected = today_eligible
    model_evals_produced = False
    model_evals_row_count = inputs["modelEvaluationsRowCount"]
    if model_evals_expected:
        if not inputs["modelEvaluationsFileExists"]:
            reasons.append(f"{REASON_MISSING_MODEL_EVALUATIONS}: data/edgelab/model_evaluations/{date}.jsonl(.gz) does not exist")
        elif not inputs["modelEvaluationsIsCurrentDate"]:
            reasons.append(f"{REASON_STALE_ARTIFACT}: model_evaluations partition exists but is not dated {date}")
        elif model_evals_row_count == 0:
            reasons.append(f"{REASON_ZERO_ROWS_WITH_ELIGIBLE_MARKETS}: model_evaluations/{date} exists but has 0 rows despite {games_scheduled_today} scheduled games")
        else:
            model_evals_produced = True
    else:
        model_evals_produced = inputs["modelEvaluationsFileExists"]

    # ---- D. PRE_GAME_DECISION snapshot (today) ----
    snapshot_expected = today_eligible
    snapshot_present = False
    if snapshot_expected:
        if not inputs["preGameDecisionSnapshotFileExists"]:
            reasons.append(f"{REASON_MISSING_PRE_GAME_DECISION_SNAPSHOT}: no PRE_GAME_DECISION manifest exists for {date}")
        elif inputs["preGameDecisionSnapshotCompletenessStatus"] == "MISSING_REQUIRED_INPUT":
            reasons.append(f"{REASON_MISSING_PRE_GAME_DECISION_SNAPSHOT}: manifest exists but completenessStatus=MISSING_REQUIRED_INPUT")
        elif not inputs["preGameDecisionSnapshotIsSameDayCapture"]:
            reasons.append(
                f"{REASON_MISSING_PRE_GAME_DECISION_SNAPSHOT}: manifest exists but was not captured on {date} itself -- "
                "this is a late scripts/check_snapshot_capture.py recovery, never a genuine same-day prospective capture"
            )
        else:
            snapshot_present = True
    else:
        snapshot_present = inputs["preGameDecisionSnapshotFileExists"]

    # ---- E. Settlement + RECOMMENDATION_SYNC full-universe extension (settlement date) ----
    settlement_date = inputs["settlementDateChecked"]
    settlements_expected = inputs["settlementsExpected"]
    settlements_produced = False
    settlements_row_count = inputs["settlementsRowCount"]
    full_universe_row_count = inputs["fullUniverseExtensionRowCount"]
    if settlements_expected:
        if not inputs["settlementsFileExists"]:
            reasons.append(f"{REASON_MISSING_SETTLEMENTS}: data/edgelab/settlements/{settlement_date}.jsonl(.gz) does not exist")
        elif settlements_row_count == 0:
            reasons.append(f"{REASON_ZERO_ROWS_WITH_ELIGIBLE_MARKETS}: settlements/{settlement_date} exists but has 0 rows despite archived market activity that day")
        else:
            settlements_produced = True

        if full_universe_row_count == 0:
            reasons.append(
                f"{REASON_MISSING_MODEL_EVALUATIONS}: no RECOMMENDATION_SYNC full-universe ModelEvaluation extension rows "
                f"(source in {sorted(FULL_UNIVERSE_EXTENSION_SOURCES)}) found for settlement date {settlement_date} -- "
                "RECOMMENDATION_SYNC likely did not run or crashed on import (e.g. the 2026-08-18 duckdb regression)"
            )
    else:
        settlements_produced = inputs["settlementsFileExists"]

    # ---- F. Full-universe probability coverage (item 13) ----
    coverage_artifact_available = inputs.get("coverageArtifactAvailable", False)
    archived_supported = inputs.get("archivedSupportedTickerCount", 0) or 0
    evaluated_probability_count = inputs.get("evaluatedProbabilityCount", 0) or 0
    missing_input_count = inputs.get("missingInputCount", 0) or 0
    unsupported_count = inputs.get("unsupportedCount", 0) or 0
    suspended_count = inputs.get("suspendedCount", 0) or 0
    family_coverage_breakdown = inputs.get("familyCoverageBreakdown") or {}

    probability_coverage_pct = (
        round(100.0 * evaluated_probability_count / archived_supported, 2) if archived_supported else None
    )
    coverage_degraded = False
    if coverage_artifact_available and archived_supported > 0 and probability_coverage_pct < MIN_PROBABILITY_COVERAGE_PCT:
        coverage_degraded = True
        reasons.append(
            f"{REASON_LOW_PROBABILITY_COVERAGE}: {probability_coverage_pct}% of {archived_supported} "
            f"supported archived tickers have a computed probability (below the {MIN_PROBABILITY_COVERAGE_PCT}% threshold)"
        )

    # ---- overall status ----
    if not today_eligible and not settlements_expected:
        health_status = HEALTH_STATUS_NO_MLB_GAMES
        artifact_freshness_status = "N/A"
    elif any(r for r in reasons if not r.startswith(REASON_LOW_PROBABILITY_COVERAGE)):
        health_status = HEALTH_STATUS_UNHEALTHY
        artifact_freshness_status = "STALE" if any(r.startswith(REASON_STALE_ARTIFACT) for r in reasons) else "MISSING"
    elif coverage_degraded:
        health_status = HEALTH_STATUS_DEGRADED
        artifact_freshness_status = "CURRENT"
    else:
        health_status = HEALTH_STATUS_HEALTHY
        artifact_freshness_status = "CURRENT"

    return {
        "schemaVersion": SCHEMA_VERSION,
        "date": date,
        "checkedAt": checked_at,
        "marketsObserved": markets_observed,
        "slateRunsExpected": 1 if today_eligible else 0,
        "slateRunsObserved": 1 if inputs["recommendationsFileExists"] else 0,
        "recommendationsExpected": recommendations_expected,
        "recommendationsProduced": recommendations_produced,
        "recommendationRowCount": recommendations_row_count,
        "modelEvaluationsExpected": model_evals_expected,
        "modelEvaluationsProduced": model_evals_produced,
        "modelEvaluationRowCount": model_evals_row_count,
        "preGameDecisionSnapshotExpected": snapshot_expected,
        "preGameDecisionSnapshotPresent": snapshot_present,
        "settlementDateChecked": settlement_date,
        "settlementsExpected": settlements_expected,
        "settlementsProduced": settlements_produced,
        "settlementRowCount": settlements_row_count,
        "fullUniverseExtensionRowCount": full_universe_row_count,
        "coverageArtifactAvailable": coverage_artifact_available,
        "archivedSupportedTickerCount": archived_supported,
        "evaluatedTickerCount": archived_supported,
        "evaluatedProbabilityCount": evaluated_probability_count,
        "missingInputCount": missing_input_count,
        "unsupportedCount": unsupported_count,
        "suspendedCount": suspended_count,
        "probabilityCoveragePct": probability_coverage_pct,
        "familyCoverageBreakdown": family_coverage_breakdown,
        "artifactFreshnessStatus": artifact_freshness_status,
        "healthStatus": health_status,
        "reasons": reasons,
        "dateResolution": date_resolution,
    }
