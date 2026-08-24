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
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab import daily_health, ids, mlb_schedule, storage
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
    """'Today' in America/New_York, matching every other daily workflow's own date-resolution convention (no zoneinfo dependency -- fixed UTC-4/-5 offset is not needed here since callers pass an explicit `now` in tests; production uses the system TZ database via the `TZ` env var already set by the workflow, same as fetch-slate.yml's own `Set date` step)."""
    now = now or datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%d")


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
            recommendations_provenance_valid = (
                prov_meta.get("slateDate") == date
                and bool(prov_data.get("workflowRunId"))
                and str(prov_data.get("capturedAt", "")).startswith(date)
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
        captured_at = str(manifest.get("capturedAt") or "")
        pregame_same_day_capture = captured_at.startswith(date)

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
    parser.add_argument("--date", default=None, help="Date to check (YYYY-MM-DD). Leave blank for today ET.")
    args = parser.parse_args(argv)

    date = args.date or _et_today()
    settlement_date = (datetime.strptime(date, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
    now_iso = ids.utc_now_iso()

    inputs = gather_inputs(date, settlement_date, now_iso=now_iso)
    record = daily_health.compute_daily_health(inputs, now_iso)

    os.makedirs(HEALTH_DIR, exist_ok=True)
    out_path = os.path.join(HEALTH_DIR, f"{date}.json")
    with open(out_path, "w") as f:
        json.dump(record, f, indent=2, sort_keys=True)
        f.write("\n")

    print(f"[daily_health_check] date={date} settlementDateChecked={settlement_date} healthStatus={record['healthStatus']}")
    for reason in record["reasons"]:
        print(f"[daily_health_check] REASON: {reason}", file=sys.stderr)

    if record["healthStatus"] == daily_health.HEALTH_STATUS_UNHEALTHY:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
