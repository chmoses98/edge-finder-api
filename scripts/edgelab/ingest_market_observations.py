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

May make ONE MLB Stats API schedule-by-date call (lib.edgelab.
mlb_schedule, a different upstream than Kalshi) -- but only when at
least one of this date's Game rows is still missing mlbGamePk after the
pipeline-slate pass, e.g. a standalone/manual-only day with no
data/pipeline/<date>/normalized_slate.json. A fully slate-backed date
never triggers this call.

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
    backfill_missing_game_pks,
    build_game_records,
    build_market_records,
    build_observations_from_snapshot,
    find_latest_snapshot,
    find_snapshots_for_date,
    load_game_context,
    mark_superseded_game_identities,
    new_unclassified_series_warnings,
    select_observations_for_retention,
)
from lib.edgelab.mlb_schedule import backfill_missing_game_pks_via_schedule


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
    started_at = ids.utc_now_iso()

    snapshot_paths = (
        find_snapshots_for_date(date) if args.all_snapshots
        else [p for p in [find_latest_snapshot(date)] if p]
    )

    # Research-Run Manifest Identity fix: content_signature (a hash of
    # source_system + the sorted snapshot paths this invocation is about
    # to process) makes run_id distinguish two invocations inside the
    # SAME GitHub Actions run/second that process different snapshot
    # sets, while still deterministically re-deriving the identical id
    # for a true retry of the exact same inputs. github_run_attempt
    # additionally distinguishes a manual re-run of the same workflow run.
    content_signature = ids.build_run_content_signature(args.source_system, *sorted(snapshot_paths))
    run_id = ids.new_run_id(
        "MARKET_OBSERVATION_INGEST",
        github_run_id=os.environ.get("GITHUB_RUN_ID"),
        github_run_attempt=os.environ.get("GITHUB_RUN_ATTEMPT"),
        content_signature=content_signature,
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

    game_records = build_game_records(all_built, game_context, date=date)
    market_records = build_market_records(all_built)
    games_path = storage.partition_path("games", date)
    markets_path = storage.resolve_partition_path("markets", date)
    storage.upsert_records(games_path, game_records, "gameId")
    storage.upsert_records(markets_path, market_records, "marketTicker")

    # Root-cause fix for Game rows permanently stuck with mlbGamePk=null:
    # a row's mlbGamePk is only ever set from whatever game_context was
    # available the moment build_game_records first created THAT row,
    # and upsert_records only ever replaces a row sharing its exact
    # gameId -- it never revisits an existing row just because a NEW
    # game_context match has since become available for its (away, home)
    # pair. Every run now also re-checks every ALREADY-STORED row for
    # this date (not just the ones this run's own observations touched)
    # against the game_context loaded above, so a game whose markets
    # stopped being captured before that day's slate was ready gets
    # self-healed the moment a later run has both a stored row and a
    # real slate match -- see lib.edgelab.market_universe.
    # backfill_missing_game_pks.
    all_games_for_date = list(storage.read_records(games_path))
    backfilled_games = backfill_missing_game_pks(all_games_for_date, game_context)
    if backfilled_games:
        storage.upsert_records(games_path, backfilled_games, "gameId")

    # Companion self-heal for the other half of the same root cause: a
    # game first ingested before game_context existed gets a
    # ticker-fallback gameId, and once game_context becomes available
    # every NEW observation for that (away, home) pair gets the
    # authoritative gameId instead -- creating a SECOND, independent Game
    # row rather than fixing the first one in place (the real 2026-08-04
    # case: 15 games each ended up with two rows -- one fallback-keyed,
    # one gamePk-keyed -- doubling that day's Game count to 30). Re-reads
    # the games file since backfill_missing_game_pks may have just
    # rewritten it above. Never renames/deletes a row -- see
    # lib.edgelab.market_universe.mark_superseded_game_identities.
    all_games_for_date = list(storage.read_records(games_path))
    superseded_games = mark_superseded_game_identities(all_games_for_date, game_context, date)
    if superseded_games:
        storage.upsert_records(games_path, superseded_games, "gameId")

    # Second identity source (lib.edgelab.mlb_schedule): a standalone/
    # manual-only Kalshi day that never had a data/pipeline/<date>/
    # normalized_slate.json run leaves game_context empty above, so
    # every row stays mlbGamePk=null no matter how many times ingestion
    # reruns. Only fetches (a live MLB schedule-by-date call) when at
    # least one row still needs it -- a no-op, no-network-call path for
    # every ordinary slate-backed day. See that module's docstring for
    # the full root-cause writeup.
    all_games_for_date = list(storage.read_records(games_path))
    schedule_backfilled_games, schedule_warnings = backfill_missing_game_pks_via_schedule(all_games_for_date, date)
    if schedule_backfilled_games:
        storage.upsert_records(games_path, schedule_backfilled_games, "gameId")
    for w in schedule_warnings:
        run_record["warnings"].append(f"MLB_SCHEDULE_IDENTITY: {w}")

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
        "gamesBackfilledMlbGamePk": len(backfilled_games),
        "gamesIdentitySuperseded": len(superseded_games),
        "gamesBackfilledMlbGamePkViaSchedule": len(schedule_backfilled_games),
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
