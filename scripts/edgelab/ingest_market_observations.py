#!/usr/bin/env python3
"""
scripts/edgelab/ingest_market_observations.py
=================================================
CLI entry point: normalize the raw Kalshi registry snapshot(s) for one
date into EdgeLab MarketObservation/Market/Game records.

Makes NO Kalshi API calls -- reads data/kalshi_registry_snapshots/*.json,
already written by the existing capture-snapshots-scheduled.yml /
clv_capture.yml workflows. Safe to run multiple times per day (dedup is
by marketObservationId, keyed on the snapshot's own capturedAt).

Usage:
    python3 scripts/edgelab/ingest_market_observations.py [--date YYYY-MM-DD] [--all-snapshots]
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab import ids, storage
from lib.edgelab.market_universe import (
    build_game_records,
    build_market_records,
    build_observations_from_snapshot,
    find_latest_snapshot,
    find_snapshots_for_date,
    load_game_context,
)
from lib.kalshi_mlb_single_game_registry import detect_new_unclassified_mlb_series


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=None, help="UTC date YYYY-MM-DD; defaults to today")
    parser.add_argument("--all-snapshots", action="store_true", help="Ingest every snapshot file for the date, not just the latest (backfill use)")
    args = parser.parse_args()

    date = args.date or datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    run_id = ids.new_run_id("MARKET_OBSERVATION_INGEST", github_run_id=os.environ.get("GITHUB_RUN_ID"))
    started_at = ids.utc_now_iso()

    snapshot_paths = (
        find_snapshots_for_date(date) if args.all_snapshots
        else [p for p in [find_latest_snapshot(date)] if p]
    )

    run_record = {
        "schemaVersion": "1",
        "runId": run_id,
        "runType": "MARKET_OBSERVATION_INGEST",
        "startedAt": started_at,
        "completedAt": None,
        "status": "running",
        "sourceWorkflow": os.environ.get("GITHUB_WORKFLOW"),
        "githubRunId": os.environ.get("GITHUB_RUN_ID"),
        "inputFiles": snapshot_paths,
        "outputFiles": [],
        "counts": {},
        "errors": [],
        "warnings": [],
        "createdAt": started_at,
        "provenance": {
            "sourceSystem": "edgelab_cli",
            "sourceFile": __file__,
            "sourceKey": date,
            "capturedAt": started_at,
            "ingestedAt": started_at,
        },
    }

    if not snapshot_paths:
        print(f"[ingest_market_observations] no snapshot files found for {date}; nothing to do")
        run_record["status"] = "success"
        run_record["completedAt"] = ids.utc_now_iso()
        run_record["warnings"].append(f"no kalshi_registry_snapshots file found for {date}")
        _write_run_record(date, run_record)
        return 0

    game_context = load_game_context(date)
    all_observations = []
    all_excluded = []

    for snapshot_path in snapshot_paths:
        try:
            observations, excluded = build_observations_from_snapshot(snapshot_path, run_id, game_context)
        except (json.JSONDecodeError, OSError) as exc:
            run_record["errors"].append(f"{snapshot_path}: {exc}")
            continue
        all_observations.extend(observations)
        all_excluded.extend(excluded)

    obs_path = storage.partition_path("observations", date)
    written, skipped = storage.append_records(obs_path, all_observations, "marketObservationId")

    game_records = build_game_records(all_observations, game_context)
    market_records = build_market_records(all_observations)
    games_path = storage.partition_path("games", date)
    markets_path = storage.partition_path("markets", date)
    storage.upsert_records(games_path, game_records, "gameId")
    storage.upsert_records(markets_path, market_records, "marketTicker")

    new_series_warnings = detect_new_unclassified_mlb_series(all_excluded)
    for w in new_series_warnings:
        run_record["warnings"].append(f"NEW_UNCLASSIFIED_MLB_SERIES: {w['seriesTicker']} ({w['title']})")

    run_record["status"] = "success" if not run_record["errors"] else "partial"
    run_record["completedAt"] = ids.utc_now_iso()
    run_record["outputFiles"] = [obs_path, games_path, markets_path]
    run_record["counts"] = {
        "snapshotsProcessed": len(snapshot_paths),
        "observationsWritten": written,
        "observationsSkippedDuplicate": skipped,
        "gamesUpserted": len(game_records),
        "marketsUpserted": len(market_records),
        "marketsExcluded": len(all_excluded),
        "newUnclassifiedSeries": len(new_series_warnings),
    }
    _write_run_record(date, run_record)

    print(
        f"[ingest_market_observations] date={date} snapshots={len(snapshot_paths)} "
        f"observations_written={written} skipped_dup={skipped} excluded={len(all_excluded)} "
        f"new_unclassified_series={len(new_series_warnings)}"
    )
    return 0


def _write_run_record(date, run_record):
    runs_path = storage.partition_path("research_runs", date)
    storage.append_records(runs_path, [run_record], "runId")


if __name__ == "__main__":
    sys.exit(main())
