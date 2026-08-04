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
    new_unclassified_series_warnings,
    select_observations_for_retention,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=None, help="UTC date YYYY-MM-DD; defaults to today")
    parser.add_argument("--all-snapshots", action="store_true", help="Ingest every snapshot file for the date, not just the latest (backfill use)")
    parser.add_argument(
        "--source-system", default="kalshi_registry_snapshots",
        help="Tag written observations with this source (e.g. 'standalone_price_check' when this "
             "run is archiving alongside a manually-triggered price-check run -- see "
             "lib.edgelab.observation_linkage, which prefers this source when linking a manual bet).",
    )
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
    github_run_id = os.environ.get("GITHUB_RUN_ID")
    commit_sha = os.environ.get("GITHUB_SHA")

    # Market Research Corpus milestone: FIRST_DAILY classification and the
    # growth-control retention filter both need to know what's already been
    # committed today BEFORE this run -- loaded once, up front, and updated
    # in place as each snapshot file is processed (so a second snapshot
    # file in the same --all-snapshots run sees the first file's tickers
    # too, not just what was on disk before this whole invocation started).
    already_recorded = list(storage.read_records(storage.partition_path("observations", date, compressed=True)))
    tickers_seen_today = {o["marketTicker"] for o in already_recorded}
    previous_by_ticker = {}
    for o in already_recorded:
        previous_by_ticker[o["marketTicker"]] = o  # last one wins -- file is already in capture order

    all_built = []
    all_excluded = []

    for snapshot_path in snapshot_paths:
        try:
            observations, excluded = build_observations_from_snapshot(
                snapshot_path, run_id, game_context, source_system=args.source_system,
                existing_tickers_seen_today=tickers_seen_today,
                github_run_id=github_run_id, commit_sha=commit_sha,
            )
        except (json.JSONDecodeError, OSError) as exc:
            run_record["errors"].append(f"{snapshot_path}: {exc}")
            continue
        tickers_seen_today.update(o["marketTicker"] for o in observations)
        all_built.extend(observations)
        all_excluded.extend(excluded)

    all_observations = select_observations_for_retention(all_built, previous_by_ticker=previous_by_ticker)

    obs_path = storage.partition_path("observations", date, compressed=True)
    written, skipped = storage.append_records(obs_path, all_observations, "marketObservationId")

    game_records = build_game_records(all_built, game_context)
    market_records = build_market_records(all_built)
    games_path = storage.partition_path("games", date)
    markets_path = storage.partition_path("markets", date)
    storage.upsert_records(games_path, game_records, "gameId")
    storage.upsert_records(markets_path, market_records, "marketTicker")

    new_series_warnings = new_unclassified_series_warnings(all_built, all_excluded)
    for w in new_series_warnings:
        run_record["warnings"].append(f"NEW_UNCLASSIFIED_MLB_SERIES: {w['seriesTicker']} ({w['title']})")

    run_record["status"] = "success" if not run_record["errors"] else "partial"
    run_record["completedAt"] = ids.utc_now_iso()
    run_record["outputFiles"] = [obs_path, games_path, markets_path]
    run_record["counts"] = {
        "snapshotsProcessed": len(snapshot_paths),
        "observationsBuilt": len(all_built),
        "observationsRetained": len(all_observations),
        "observationsDroppedNoChange": len(all_built) - len(all_observations),
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
        f"built={len(all_built)} retained={len(all_observations)} written={written} "
        f"skipped_dup={skipped} excluded={len(all_excluded)} "
        f"new_unclassified_series={len(new_series_warnings)}"
    )
    return 0


def _write_run_record(date, run_record):
    runs_path = storage.partition_path("research_runs", date)
    storage.append_records(runs_path, [run_record], "runId")


if __name__ == "__main__":
    sys.exit(main())
