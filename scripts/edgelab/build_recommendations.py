#!/usr/bin/env python3
"""
scripts/edgelab/build_recommendations.py
=============================================
CLI entry point: build the EdgeLab decision-layer ledger for one date
from data/pipeline/<date>/recommendations.json + execution.json (the
11-market model config's decisions) plus full-universe extension rows
for every other market EdgeLab observed that day.

Usage:
    python3 scripts/edgelab/build_recommendations.py [--date YYYY-MM-DD]
"""
import argparse
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab import ids, storage
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

    placed_bet_tickers = {}
    for row in storage.read_records(storage.singleton_path("bets", "bets.jsonl")):
        if row.get("marketTicker"):
            placed_bet_tickers.setdefault(row["marketTicker"], row["betId"])

    pipeline_records, warnings = build_recommendations_from_pipeline(date, run_id, placed_bet_tickers)
    pipeline_path = storage.partition_path("recommendations", date)
    written, skipped = storage.append_records(pipeline_path, pipeline_records, "recommendationId")

    observations = list(storage.read_records(storage.partition_path("observations", date, compressed=True)))
    model_covered_series = load_model_covered_series()
    covered_tickers = {r["marketTicker"] for r in pipeline_records if r.get("marketTicker")}
    extension_records = extend_with_full_universe(covered_tickers, observations, model_covered_series, date, placed_bet_tickers)
    ext_updated, ext_inserted = storage.upsert_records(pipeline_path, extension_records, "recommendationId")

    run_record = {
        "schemaVersion": "1",
        "runId": run_id,
        "runType": "RECOMMENDATION_SYNC",
        "startedAt": started_at,
        "completedAt": ids.utc_now_iso(),
        "status": "success" if not warnings else "partial",
        "sourceWorkflow": os.environ.get("GITHUB_WORKFLOW"),
        "githubRunId": os.environ.get("GITHUB_RUN_ID"),
        "inputFiles": [
            os.path.join("data", "pipeline", date, "recommendations.json"),
            os.path.join("data", "pipeline", date, "execution.json"),
            storage.partition_path("observations", date, compressed=True),
        ],
        "outputFiles": [pipeline_path],
        "counts": {
            "pipelineRowsWritten": written,
            "pipelineRowsSkippedDuplicate": skipped,
            "extensionRowsInserted": ext_inserted,
            "extensionRowsUpdated": ext_updated,
            "observationsConsidered": len(observations),
        },
        "errors": [],
        "warnings": warnings,
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
        f"warnings={warnings}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
