#!/usr/bin/env python3
"""
scripts/edgelab/build_recommendations.py
=============================================
CLI entry point: build the EdgeLab decision-layer ledger for one date
from data/pipeline/<date>/recommendations.json + execution.json (the
11-market model config's decisions) plus full-universe extension rows
for every other market EdgeLab observed that day. Since Phase 2
Milestone 3 (docs/EDGELAB_MODEL_EVALUATION.md), also builds the parallel
ModelEvaluation ledger from the exact same source rows, and backfills
any already-logged PlacedBet's recommendationId/modelEvaluationId once a
matching evaluation exists for its ticker.

Usage:
    python3 scripts/edgelab/build_recommendations.py [--date YYYY-MM-DD]
"""
import argparse
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab import ids, storage
from lib.edgelab.bets import link_bets_to_recommendations
from lib.edgelab.model_evaluation import build_model_evaluations_from_pipeline, extend_full_universe_evaluations
from lib.edgelab.recommendations import (
    build_recommendations_from_pipeline,
    extend_with_full_universe,
    load_model_covered_series,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=None)
    args = parser.parse_args()
    date = args.date or datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")

    run_id = ids.new_run_id("RECOMMENDATION_SYNC", github_run_id=os.environ.get("GITHUB_RUN_ID"))
    started_at = ids.utc_now_iso()

    bets_path = storage.singleton_path("bets", "bets.jsonl")
    bets = list(storage.read_records(bets_path))
    placed_bet_tickers = {}
    for row in bets:
        if row.get("marketTicker"):
            placed_bet_tickers.setdefault(row["marketTicker"], row["betId"])

    observations = list(storage.read_records(storage.partition_path("observations", date, compressed=True)))

    pipeline_records, warnings = build_recommendations_from_pipeline(date, run_id, placed_bet_tickers, observations)
    pipeline_path = storage.partition_path("recommendations", date)
    written, skipped = storage.append_records(pipeline_path, pipeline_records, "recommendationId")

    model_covered_series = load_model_covered_series()
    covered_tickers = {r["marketTicker"] for r in pipeline_records if r.get("marketTicker")}
    extension_records = extend_with_full_universe(covered_tickers, observations, model_covered_series, date, placed_bet_tickers)
    ext_updated, ext_inserted = storage.upsert_records(pipeline_path, extension_records, "recommendationId")

    eval_pipeline_records, eval_warnings = build_model_evaluations_from_pipeline(date, run_id, observations)
    evaluations_path = storage.partition_path("model_evaluations", date)
    eval_written, eval_skipped = storage.append_records(evaluations_path, eval_pipeline_records, "modelEvaluationId")

    eval_covered_tickers = {r["marketTicker"] for r in eval_pipeline_records if r.get("marketTicker")}
    eval_extension_records = extend_full_universe_evaluations(eval_covered_tickers, observations, date, model_covered_series)
    eval_ext_updated, eval_ext_inserted = storage.upsert_records(evaluations_path, eval_extension_records, "modelEvaluationId")

    bet_updates = link_bets_to_recommendations(bets, pipeline_records + extension_records)
    if bet_updates:
        storage.upsert_records(bets_path, bet_updates, "betId")

    all_warnings = warnings + eval_warnings
    run_record = {
        "schemaVersion": "1",
        "runId": run_id,
        "runType": "RECOMMENDATION_SYNC",
        "startedAt": started_at,
        "completedAt": ids.utc_now_iso(),
        "status": "success" if not all_warnings else "partial",
        "sourceWorkflow": os.environ.get("GITHUB_WORKFLOW"),
        "githubRunId": os.environ.get("GITHUB_RUN_ID"),
        "inputFiles": [
            os.path.join("data", "pipeline", date, "recommendations.json"),
            os.path.join("data", "pipeline", date, "execution.json"),
            storage.partition_path("observations", date, compressed=True),
        ],
        "outputFiles": [pipeline_path, evaluations_path, bets_path],
        "counts": {
            "pipelineRowsWritten": written,
            "pipelineRowsSkippedDuplicate": skipped,
            "extensionRowsInserted": ext_inserted,
            "extensionRowsUpdated": ext_updated,
            "modelEvaluationsWritten": eval_written,
            "modelEvaluationsSkippedDuplicate": eval_skipped,
            "modelEvaluationExtensionRowsInserted": eval_ext_inserted,
            "modelEvaluationExtensionRowsUpdated": eval_ext_updated,
            "betsLinked": len(bet_updates),
            "observationsConsidered": len(observations),
        },
        "errors": [],
        "warnings": all_warnings,
        "createdAt": started_at,
        "provenance": {
            "sourceSystem": "edgelab_cli",
            "sourceFile": __file__,
            "sourceKey": date,
            "capturedAt": started_at,
            "ingestedAt": started_at,
        },
    }
    storage.append_records(storage.partition_path("research_runs", date), [run_record], "runId")

    print(
        f"[build_recommendations] date={date} pipeline_rows={written} "
        f"skipped_dup={skipped} extension_inserted={ext_inserted} extension_updated={ext_updated} "
        f"model_evaluations_written={eval_written} eval_extension_inserted={eval_ext_inserted} "
        f"bets_linked={len(bet_updates)} warnings={all_warnings}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
