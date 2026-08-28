#!/usr/bin/env python3
"""
scripts/edgelab/daily_health_check.py
================================================================
EdgeLab Daily Pipeline Heartbeat/Watchdog -- independent guardrail
(Pipeline Health Incident follow-up, 2026-08-24). See
lib/edgelab/daily_health.py's own docstring for the full mission
rationale and the two historical outages this exists to catch.

Deliberately INDEPENDENT of every workflow it watches: reads only
already-committed repository state (via lib.edgelab.storage, which
already transparently handles the .jsonl/.jsonl.gz compaction split --
see storage.resolve_partition_path's own docstring for the exact
mistake this repeats otherwise) plus one live, unauthenticated MLB Stats
API schedule call (lib.edgelab.mlb_schedule.fetch_schedule) that shares
no code path, credentials, or trigger with fetch-slate.yml,
edgelab-postgame.yml, or RECOMMENDATION_SYNC. Never invoked via
`workflow_run` off any of them -- see .github/workflows/edgelab-
heartbeat.yml's own header for why that's the entire point.

All classification logic lives in lib.edgelab.daily_health.compute_daily_health
(pure, no I/O of its own) -- this script only gathers real facts into
its `inputs` dict and hands them off, then writes the result and sets
the process exit code.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab import daily_health, ids, mlb_schedule, production_date, storage
from lib.edgelab.snapshot import load_latest_pregame_manifest

HEALTH_DIR = os.path.join("data", "edgelab", "health")
COVERAGE_DIR = os.path.join("data", "kalshi", "discovery")

# lib.kalshi_market_coverage.ALL_TERMINAL_STATES buckets that constitute
# the "supported" population (a real adapter/research engine exists for
# this family, whether or not THIS run actually produced a probability)
# -- UNSUPPORTED_MODEL_FAMILY/PARSER_UNRESOLVED/GAME_MAPPING_UNRESOLVED
# are deliberately excluded from the denominator (item 13: "use expected
# supported population as denominator", "do not fail merely because
# intentionally unsupported/suspended families exist").
_SUPPORTED_STATES = ("FULLY_EVALUATED", "RESEARCH_MODEL_ONLY", "MISSING_REQUIRED_CONTEXT", "AMBIGUOUS_TICKER_MATCH")
_EVALUATED_STATES = ("FULLY_EVALUATED", "RESEARCH_MODEL_ONLY")
_MISSING_INPUT_STATES = ("MISSING_REQUIRED_CONTEXT", "AMBIGUOUS_TICKER_MATCH")


def _load_coverage_inputs(date, coverage_dir=COVERAGE_DIR):
    """
    Best-effort read of data/kalshi/discovery/<date>_coverage.json
    (written by scripts/build_full_market_coverage.py, a SEPARATE
    workflow this heartbeat never depends on firing -- see this
    function's own callers). Returns the coverage-related `inputs` keys
    lib.edgelab.daily_health.compute_daily_health expects, using the
    PREGAME-SCOPED breakdown (lib.kalshi_market_coverage.pregame_view)
    since that is the population a manual analyst / calibration
    consumer actually cares about for the given date -- never the raw
    archive total, which also includes already-started-game contracts.

    Never raises, never fabricates a coverage artifact that doesn't
    exist: a missing/malformed file yields
    coverageArtifactAvailable=False and every count at 0, which
    compute_daily_health treats as informational-only (never a false
    DEGRADED/UNHEALTHY -- see its own docstring).
    """
    path = os.path.join(coverage_dir, f"{date}_coverage.json")
    try:
        with open(path) as f:
            doc = json.load(f)
    except (OSError, ValueError, json.JSONDecodeError):
        return {
            "coverageArtifactAvailable": False, "archivedSupportedTickerCount": 0,
            "evaluatedProbabilityCount": 0, "missingInputCount": 0,
            "unsupportedCount": 0, "suspendedCount": 0, "familyCoverageBreakdown": {},
        }

    pregame = doc.get("pregameView") or {}
    # Family breakdown is derived directly from the ledger, filtered to
    # the SAME pregame scope as pregameView above (excluding
    # NOT_APPLICABLE/STARTED_GAME_EXCLUDED) -- the sibling top-level
    # byFamilyState key covers the full (non-pregame-filtered) archive,
    # which would make a per-family total inconsistent with the overall
    # pregame-scoped counts above if used directly.
    _pregame_excluded = {"NOT_APPLICABLE", "STARTED_GAME_EXCLUDED"}
    by_family_state = {}
    for row in doc.get("ledger") or []:
        state = row.get("finalCoverageState")
        if state in _pregame_excluded:
            continue
        family = row.get("marketFamily") or row.get("seriesTicker") or "UNKNOWN"
        by_family_state.setdefault(family, {}).setdefault(state, 0)
        by_family_state[family][state] += 1

    archived_supported = sum(pregame.get(k, 0) for k in (
        "pregameFullyEvaluatedProduction", "pregameResearchSupportedHitterMarkets",
        "pregameMissingRequiredContext", "pregameAmbiguousTickerMatch",
    ))
    evaluated_probability_count = (
        pregame.get("pregameFullyEvaluatedProduction", 0) + pregame.get("pregameResearchSupportedHitterMarkets", 0)
    )
    missing_input_count = (
        pregame.get("pregameMissingRequiredContext", 0) + pregame.get("pregameAmbiguousTickerMatch", 0)
    )
    unsupported_count = pregame.get("pregameUnsupportedByAllModels", 0)

    family_breakdown = {}
    for family, states in by_family_state.items():
        family_supported = sum(states.get(k, 0) for k in _SUPPORTED_STATES)
        family_evaluated = sum(states.get(k, 0) for k in _EVALUATED_STATES)
        family_breakdown[family] = {
            "archivedSupportedTickerCount": family_supported,
            "evaluatedProbabilityCount": family_evaluated,
            "probabilityCoveragePct": (
                round(100.0 * family_evaluated / family_supported, 2) if family_supported else None
            ),
        }

    return {
        "coverageArtifactAvailable": True,
        "archivedSupportedTickerCount": archived_supported,
        "evaluatedProbabilityCount": evaluated_probability_count,
        "missingInputCount": missing_input_count,
        "unsupportedCount": unsupported_count,
        "suspendedCount": 0,
        "familyCoverageBreakdown": family_breakdown,
    }


def _et_today(now=None):
    """Today's production date in America/New_York (lib.edgelab.production_date.et_today).

    The ONLY caller is the local/no-context fallback in main(): under
    GitHub Actions the target date is always resolved by
    scripts/edgelab/resolve_heartbeat_target.py and passed in with
    --date, so this never decides a scheduled run's date. Previously
    this function's own body did strftime() on a UTC datetime while its
    docstring claimed America/New_York -- a real, if secondary, part of
    the 2026-08-27 false-failure incident (see production_date's module
    docstring). It now genuinely converts via zoneinfo, so it is correct
    across DST instead of only during the hours when UTC and ET happen
    to share a calendar date.
    """
    return production_date.et_today(now)


def _count_unique_tickers(records):
    tickers = set()
    for row in records:
        t = row.get("marketTicker")
        if t:
            tickers.add(t)
    return len(tickers)


def gather_inputs(date, settlement_date, *, now_iso=None):
    """
    Real (non-pure) fact-gathering. Returns the `inputs` dict
    lib.edgelab.daily_health.compute_daily_health expects.
    """
    now_iso = now_iso or ids.utc_now_iso()

    # ---- live, independent ground truth for "were MLB games scheduled today" ----
    schedule_json = mlb_schedule.fetch_schedule(date)
    games_scheduled_today = None
    if schedule_json is not None:
        try:
            games_scheduled_today = len(mlb_schedule.parse_schedule_games(schedule_json))
        except Exception:
            games_scheduled_today = None

    # ---- A. market observations (today) ----
    markets_observed = _count_unique_tickers(storage.read_partition("observations", date))

    # ---- B. slate / recommendation chain (today, fetch-slate.yml's own output) ----
    rec_path = os.path.join("data", "pipeline", date, "recommendations.json")
    prov_path = os.path.join("data", "pipeline", date, "provenance.json")
    recommendations_file_exists = os.path.exists(rec_path)
    recommendations_is_current_date = False
    recommendations_row_count = 0
    if recommendations_file_exists:
        try:
            with open(rec_path) as f:
                rec_doc = json.load(f)
            meta = rec_doc.get("meta") or {}
            recommendations_is_current_date = meta.get("slateDate") == date
            games = (rec_doc.get("data") or {}).get("games")
            recommendations_row_count = len(games) if isinstance(games, list) else 0
        except (OSError, ValueError, json.JSONDecodeError):
            recommendations_is_current_date = False

    recommendations_provenance_valid = False
    if os.path.exists(prov_path):
        try:
            with open(prov_path) as f:
                prov_doc = json.load(f)
            prov_meta = prov_doc.get("meta") or {}
            prov_data = prov_doc.get("data") or {}
            # capturedAt is a UTC instant; `date` is an Eastern production
            # date (see lib/edgelab/production_date.py). Comparing the two
            # as strings marked every slate captured between 00:00 and
            # ~04:00 UTC -- i.e. the same ET evening the slate belongs to,
            # e.g. 2026-08-26's own 2026-08-27T02:18:17Z capture -- as
            # INVALID_PROVENANCE. Convert, then compare production dates.
            recommendations_provenance_valid = (
                prov_meta.get("slateDate") == date
                and bool(prov_data.get("workflowRunId"))
                and production_date.et_date_for_timestamp(prov_data.get("capturedAt")) == date
            )
        except (OSError, ValueError, json.JSONDecodeError):
            recommendations_provenance_valid = False

    # ---- C. ModelEvaluation persistence (today's base coverage file) ----
    model_evals_file_exists = storage.partition_exists("model_evaluations", date)
    model_evals_rows = list(storage.read_partition("model_evaluations", date)) if model_evals_file_exists else []
    model_evals_row_count = len(model_evals_rows)
    # The partition path itself is already date-keyed (model_evaluations/<date>.jsonl[.gz]);
    # storage.resolve_partition_path can only ever resolve a path under the exact requested
    # date key, so existence IS the freshness proof -- no separate staleness check needed.
    model_evals_is_current_date = model_evals_file_exists

    # ---- D. PRE_GAME_DECISION snapshot (today) ----
    manifest = load_latest_pregame_manifest(date)
    pregame_file_exists = manifest is not None
    pregame_same_day_capture = False
    pregame_completeness = None
    if manifest is not None:
        pregame_completeness = manifest.get("completenessStatus")
        # Same UTC-instant vs Eastern-production-date correction as the
        # provenance check above: a manifest filed under
        # snapshots/2026-08-26/ whose capturedAt is 2026-08-27T02:18:47Z
        # WAS captured on production date 2026-08-26 (22:18 ET) and is a
        # genuine same-day prospective capture, not a late
        # check_snapshot_capture.py recovery. Recoveries days later still
        # resolve to a different ET date and are still reported.
        pregame_same_day_capture = (
            production_date.et_date_for_timestamp(manifest.get("capturedAt")) == date
        )

    # ---- E. Settlement + RECOMMENDATION_SYNC full-universe extension (settlement date) ----
    settlement_markets_observed = _count_unique_tickers(storage.read_partition("observations", settlement_date))
    settlements_expected = settlement_markets_observed > 0
    settlements_file_exists = storage.partition_exists("settlements", settlement_date)
    settlements_row_count = len(list(storage.read_partition("settlements", settlement_date))) if settlements_file_exists else 0

    full_universe_row_count = 0
    if storage.partition_exists("model_evaluations", settlement_date):
        for row in storage.read_partition("model_evaluations", settlement_date):
            if row.get("source") in daily_health.FULL_UNIVERSE_EXTENSION_SOURCES:
                full_universe_row_count += 1

    # ---- F. Full-universe probability coverage (item 13, today's date) ----
    coverage_inputs = _load_coverage_inputs(date)

    return {
        "date": date,
        "gamesScheduledToday": games_scheduled_today,
        "marketsObservedCount": markets_observed,
        "recommendationsFileExists": recommendations_file_exists,
        "recommendationsIsCurrentDate": recommendations_is_current_date,
        "recommendationsProvenanceValid": recommendations_provenance_valid,
        "recommendationsRowCount": recommendations_row_count,
        "modelEvaluationsFileExists": model_evals_file_exists,
        "modelEvaluationsIsCurrentDate": model_evals_is_current_date,
        "modelEvaluationsRowCount": model_evals_row_count,
        "preGameDecisionSnapshotFileExists": pregame_file_exists,
        "preGameDecisionSnapshotIsSameDayCapture": pregame_same_day_capture,
        "preGameDecisionSnapshotCompletenessStatus": pregame_completeness,
        "settlementDateChecked": settlement_date,
        "settlementsExpected": settlements_expected,
        "settlementsFileExists": settlements_file_exists,
        "settlementsRowCount": settlements_row_count,
        "fullUniverseExtensionRowCount": full_universe_row_count,
        **coverage_inputs,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=None, help="Target production date (YYYY-MM-DD). Under GitHub Actions this is always passed explicitly by scripts/edgelab/resolve_heartbeat_target.py.")
    parser.add_argument("--resolution-file", default=None, help="JSON written by scripts/edgelab/resolve_heartbeat_target.py; embedded in the artifact as dateResolution and cross-checked against --date.")
    args = parser.parse_args(argv)

    # The target date is never re-derived here. Either it was resolved
    # once, upstream, by the single authoritative resolver (--date, plus
    # its own --resolution-file audit trail), or -- only for a local,
    # context-free invocation -- it falls back to the current Eastern
    # production date, the same convention every other daily workflow in
    # this repo uses. A scheduled run's date is NEVER decided by this
    # process's wall clock: that is exactly what made a run delayed to
    # 2026-08-27T05:06Z manufacture an Aug 27 outage.
    resolution = None
    if args.resolution_file:
        try:
            with open(args.resolution_file) as f:
                resolution = json.load(f)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            parser.error(f"--resolution-file could not be read: {exc}")

    try:
        if args.date:
            date = production_date.validate_date(args.date, field="--date")
        elif resolution:
            date = production_date.validate_date(resolution.get("targetDate"), field="dateResolution.targetDate")
        else:
            date = _et_today()
            resolution = production_date.resolve_target_date(event_name=None)
    except production_date.TargetDateError as exc:
        parser.error(str(exc))

    if resolution and resolution.get("targetDate") and resolution["targetDate"] != date:
        # Fail loudly rather than write an artifact whose own audit trail
        # contradicts the date it was filed under.
        parser.error(
            f"--date {date} contradicts the resolved target date {resolution['targetDate']} in "
            f"{args.resolution_file} -- refusing to check a date the resolver did not choose"
        )

    settlement_date = production_date.previous_date(date)
    now_iso = ids.utc_now_iso()

    inputs = gather_inputs(date, settlement_date, now_iso=now_iso)
    record = daily_health.compute_daily_health(inputs, now_iso, date_resolution=resolution)

    os.makedirs(HEALTH_DIR, exist_ok=True)
    # Filed under the TARGET production date -- `checkedAt` above keeps
    # the real execution timestamp, so a delayed run never overwrites the
    # health artifact of the (later) date it happened to start on.
    out_path = os.path.join(HEALTH_DIR, f"{date}.json")
    with open(out_path, "w") as f:
        json.dump(record, f, indent=2, sort_keys=True)
        f.write("\n")

    trigger = (resolution or {}).get("triggerType")
    print(
        f"[daily_health_check] date={date} settlementDateChecked={settlement_date} "
        f"trigger={trigger} healthStatus={record['healthStatus']}"
    )
    for reason in record["reasons"]:
        print(f"[daily_health_check] REASON: {reason}", file=sys.stderr)

    if record["healthStatus"] == daily_health.HEALTH_STATUS_UNHEALTHY:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
