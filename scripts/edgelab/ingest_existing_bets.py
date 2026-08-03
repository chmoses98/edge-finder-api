#!/usr/bin/env python3
"""
scripts/edgelab/ingest_existing_bets.py
===========================================
One-time-and-repeatable backfill: normalize every record from the two
pre-existing bet ledgers (bets.json, data/bets.json) into the EdgeLab
canonical placed_bet ledger (data/edgelab/bets/bets.jsonl). Idempotent --
upserts by betId, so rerunning after either legacy file gains new rows
(or a settlement update) only touches the rows that actually changed.

Neither legacy file is modified.

Usage:
    python3 scripts/edgelab/ingest_existing_bets.py [--root-bets bets.json] [--session-bets data/bets.json]
"""
import argparse
import json
import os
import shutil
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab import ids, schema, storage
from lib.edgelab.bets import (
    from_legacy_root_bets_record,
    from_legacy_session_bets_record,
    reconcile_with_existing,
)


def _backup(path):
    """
    Timestamped copy of the canonical ledger before a bulk-reconciliation
    write (requirement 17: "backup before migration/reconciliation").
    No-op if the ledger doesn't exist yet (nothing to lose). Never
    deletes old backups itself -- see docs/CANONICAL_BET_LEDGER.md's
    recovery-procedures section for pruning guidance.

    Uses microsecond precision, not ids.utc_now_iso()'s whole-second ISO
    format: two reconciliation runs within the same wall-clock second
    (an automated retry loop, or back-to-back runs in a test/benchmark)
    would otherwise derive the SAME backup filename, and the second
    run's backup would silently overwrite the first -- destroying the
    one snapshot that could have restored the true pre-any-change state
    (found during the maintainer review of this milestone).
    """
    if not os.path.exists(path):
        return None
    backups_dir = os.path.join(os.path.dirname(path), "backups")
    os.makedirs(backups_dir, exist_ok=True)
    stamp = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup_path = os.path.join(backups_dir, f"{os.path.basename(path)}.{stamp}.bak")
    shutil.copy2(path, backup_path)
    return backup_path


def _load(path):
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return json.load(f)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root-bets", default="bets.json")
    parser.add_argument("--session-bets", default=os.path.join("data", "bets.json"))
    parser.add_argument("--dry-run", action="store_true",
                         help="Compute and print what would change without writing bets.jsonl or a research_run record.")
    args = parser.parse_args()

    run_id = ids.new_run_id("BET_LEDGER_INGEST", github_run_id=os.environ.get("GITHUB_RUN_ID"))
    started_at = ids.utc_now_iso()

    records = []
    skipped_no_ticker = 0

    for i, raw in enumerate(_load(args.root_bets)):
        rec = from_legacy_root_bets_record(raw, i, source_file=args.root_bets)
        if not rec["marketTicker"]:
            skipped_no_ticker += 1
            continue
        records.append(rec)

    for i, raw in enumerate(_load(args.session_bets)):
        rec = from_legacy_session_bets_record(raw, i, source_file=args.session_bets)
        if not rec["marketTicker"]:
            skipped_no_ticker += 1
            continue
        records.append(rec)

    valid_records = []
    schema_warnings = []
    for rec in records:
        errors = schema.validate_record("placed_bet", rec)
        if errors:
            schema_warnings.append(f"{rec['provenance']['sourceFile']}#{rec['provenance']['sourceKey']}: {'; '.join(errors)}")
            continue
        valid_records.append(rec)

    path = storage.singleton_path("bets", "bets.jsonl")
    existing_by_id = {row["betId"]: row for row in storage.read_records(path)}
    reconciled_records = [reconcile_with_existing(rec, existing_by_id) for rec in valid_records]

    # Only records that actually changed content (reconcile_with_existing
    # returns the byte-identical existing row for an unchanged rerun) --
    # what dry-run mode reports as "would touch".
    would_touch = [
        rec for rec in reconciled_records
        if rec["betId"] not in existing_by_id or rec != existing_by_id[rec["betId"]]
    ]

    if args.dry_run:
        print(
            f"[ingest_existing_bets] DRY RUN: read={len(records) + skipped_no_ticker} "
            f"would_write={len(would_touch)} unchanged={len(reconciled_records) - len(would_touch)} "
            f"skipped_no_ticker={skipped_no_ticker} skipped_schema_warning={len(schema_warnings)}"
        )
        return 0

    if would_touch:
        backup_path = _backup(path)
        if backup_path:
            print(f"[ingest_existing_bets] backed up existing ledger -> {backup_path}")
    updated, inserted = storage.upsert_records(path, reconciled_records, "betId")

    warnings = list(schema_warnings)
    if skipped_no_ticker:
        warnings.append(
            f"{skipped_no_ticker} legacy bet record(s) have no marketTicker and were not "
            f"carried into the EdgeLab ledger — a known gap in the pre-EdgeLab ledgers "
            f"(see docs/EDGELAB_PHASE1.md limitations), never fabricated here."
        )

    run_record = {
        "schemaVersion": "1",
        "runId": run_id,
        "runType": "BET_LEDGER_INGEST",
        "startedAt": started_at,
        "completedAt": ids.utc_now_iso(),
        "status": "success",
        "sourceWorkflow": os.environ.get("GITHUB_WORKFLOW"),
        "githubRunId": os.environ.get("GITHUB_RUN_ID"),
        "inputFiles": [args.root_bets, args.session_bets],
        "outputFiles": [path],
        "counts": {
            "recordsRead": len(records) + skipped_no_ticker,
            "recordsSkippedNoTicker": skipped_no_ticker,
            "recordsInserted": inserted,
            "recordsUpdated": updated,
            "recordsSkippedSchemaWarning": len(schema_warnings),
        },
        "errors": [],
        "warnings": warnings,
        "createdAt": started_at,
        "provenance": {
            "sourceSystem": "edgelab_cli",
            "sourceFile": __file__,
            "sourceKey": None,
            "capturedAt": started_at,
            "ingestedAt": started_at,
        },
    }
    date = started_at[:10]
    storage.append_records(storage.partition_path("research_runs", date), [run_record], "runId")

    print(
        f"[ingest_existing_bets] read={len(records) + skipped_no_ticker} "
        f"inserted={inserted} updated={updated} skipped_no_ticker={skipped_no_ticker} "
        f"skipped_schema_warning={len(schema_warnings)}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
