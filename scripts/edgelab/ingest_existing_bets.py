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
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab import ids, schema, storage
from lib.edgelab.bets import (
    from_legacy_root_bets_record,
    from_legacy_session_bets_record,
    reconcile_with_existing,
)


def _load(path):
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return json.load(f)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root-bets", default="bets.json")
    parser.add_argument("--session-bets", default=os.path.join("data", "bets.json"))
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
