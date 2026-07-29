#!/usr/bin/env python3
"""
scripts/research/append_inning_result_snapshots.py
========================================================
Model Performance Phase 2A, Part 13 -- append-safe historical
snapshot archive builder. Reads
data/research/inning_result_shadow_ledger.json (already-built, no
live network call) and writes/updates one date-partitioned file per
date under data/research/inning_result_snapshots/<date>.json.

Idempotent: rerunning against the same shadow ledger produces
byte-identical output (lib.research.inning_result_snapshot_archive's
merge_snapshots() upserts by stable recordId). Never touches
data/slate.json, bets.json, or any production pipeline artifact.
"""
import json
import os
import sys
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from lib.research.inning_result_snapshot_archive import build_snapshot_record, merge_snapshots

LEDGER_PATH = os.path.join(ROOT, "data", "research", "inning_result_shadow_ledger.json")
SNAPSHOTS_DIR = os.path.join(ROOT, "data", "research", "inning_result_snapshots")


def build_records_by_date(ledger_rows):
    """Pure. Groups build_snapshot_record() output by date."""
    by_date = defaultdict(list)
    for row in ledger_rows:
        extra = {
            "projectionTimestamp": row.get("snapshotTimestamp"),
            "projectionVersion": "phase2a_v1",
            "distributionFamily": "independent_poisson",
        }
        record = build_snapshot_record(row, extra)
        by_date[record["date"]].append(record)
    return by_date


def run():
    with open(LEDGER_PATH) as f:
        ledger = json.load(f)

    by_date = build_records_by_date(ledger.get("rows", []))
    os.makedirs(SNAPSHOTS_DIR, exist_ok=True)

    written = {}
    for date, new_records in sorted(by_date.items()):
        path = os.path.join(SNAPSHOTS_DIR, f"{date}.json")
        try:
            with open(path) as f:
                existing = json.load(f).get("records", [])
        except (FileNotFoundError, json.JSONDecodeError):
            existing = []

        merged = merge_snapshots(existing, new_records)
        out = {
            "date": date,
            "note": (
                "RESEARCH-ONLY, append-safe historical snapshot archive for "
                "F3/F5/F7 inning-result contracts. Never consumed by "
                "production betting logic. settlementResult/"
                "settlementTimestamp are populated later, by a separate "
                "step, never backfilled at projection-snapshot time."
            ),
            "recordCount": len(merged),
            "records": merged,
        }
        with open(path, "w") as f:
            json.dump(out, f, indent=2, sort_keys=True)
        written[date] = len(merged)

    return written


def main():
    written = run()
    total = sum(written.values())
    print(f"Wrote/updated {len(written)} date-partitioned snapshot file(s), {total} total records:")
    for date, count in sorted(written.items()):
        print(f"  {date}: {count} records")
    return 0


if __name__ == "__main__":
    sys.exit(main())
