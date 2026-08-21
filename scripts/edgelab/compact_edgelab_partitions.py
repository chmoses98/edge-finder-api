#!/usr/bin/env python3
"""
scripts/edgelab/compact_edgelab_partitions.py
====================================================
Corpus Storage Growth mission: CLI entry point that gzip-compacts every
already-finalized (not-most-recent) date partition for a fixed set of
EdgeLab entities, via lib.edgelab.storage.compact_finalized_partitions().

WHY these five entities specifically: they are the ones actually growing
the committed repository day over day (settlements/clv_quotes/
model_evaluations/markets/recommendations -- confirmed via `du`, each
already 68-126MB and adding a new multi-MB file every single day). Every
one of them is written EXCLUSIVELY through lib.edgelab.storage's own
append_records/upsert_records (confirmed by auditing every writer in this
repository), and read EXCLUSIVELY through lib.edgelab.storage.read_records
or lib.edgelab.analytics's DuckDB glob layer (confirmed by auditing every
reader) -- both already transparently handle a `.jsonl.gz` suffix, so
compaction requires no reader rewrite beyond widening a glob pattern from
`*.jsonl` to `*.jsonl*` (already done for analytics.py, hitter_validation.py,
and backfill_evaluation_metadata.py as part of this same mission).

Deliberately EXCLUDES (see this mission's audit report for the reasoning,
not fixed here):
  - observations: already compressed at write time (compressed=True),
    nothing to compact.
  - snapshots: a hash-verified, manifest-driven tree of many small
    individually-referenced files, not a flat per-date JSONL -- compacting
    it safely would mean auditing every read_bytes()/hash-verification
    call site in lib/edgelab/snapshot.py, out of scope for this pass.
  - data/edgelab/analytics/latest_*.json generated reports: not part of
    the daily automated pipeline (manually regenerated, ~6 commits total
    across this repo's whole history) and a different shape (a single
    "latest" artifact per report, not a growing per-date series) -- lower
    urgency, flagged as a follow-up rather than rushed here.

Always keeps the single most-recent date per entity uncompressed (the
one a live pipeline may still be actively appending to) -- see
lib.edgelab.storage.compact_finalized_partitions's own docstring.
Idempotent: a date already compacted is skipped, reported as
"already_compacted".

Usage:
    python3 scripts/edgelab/compact_edgelab_partitions.py
    python3 scripts/edgelab/compact_edgelab_partitions.py --json
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab import ids, storage

ENTITIES = ("settlements", "clv_quotes", "model_evaluations", "markets", "recommendations")


def compact_all(entities=ENTITIES):
    """Pure orchestration over lib.edgelab.storage.compact_finalized_partitions. Returns {entity: [per-date results]}."""
    return {entity: storage.compact_finalized_partitions(entity) for entity in entities}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    started_at = ids.utc_now_iso()
    results = compact_all()

    compacted_files = 0
    bytes_before_total = 0
    bytes_after_total = 0
    for entity, entries in results.items():
        for entry in entries:
            if entry["action"] == "compacted":
                compacted_files += 1
                bytes_before_total += entry["bytesBefore"]
                bytes_after_total += entry["bytesAfter"]
                assert entry["recordsBefore"] == entry["recordsAfter"], (
                    f"{entity}/{entry['date']}: record count changed during compaction "
                    f"({entry['recordsBefore']} -> {entry['recordsAfter']}) -- this should be "
                    f"impossible (compact_partition_to_gzip verifies before returning); "
                    f"treating as a hard failure rather than silently continuing"
                )

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        for entity, entries in results.items():
            compacted = [e for e in entries if e["action"] == "compacted"]
            already = [e for e in entries if e["action"] == "already_compacted"]
            recent = [e for e in entries if e["action"] == "skipped_recent"]
            print(f"[compact_edgelab_partitions] {entity}: "
                  f"compacted={len(compacted)} already_compacted={len(already)} skipped_recent={len(recent)}")
            for e in compacted:
                saved_pct = round(100.0 * (1 - e["bytesAfter"] / e["bytesBefore"]), 1) if e["bytesBefore"] else 0.0
                print(f"    {e['date']}: {e['bytesBefore']:,}B -> {e['bytesAfter']:,}B "
                      f"(-{saved_pct}%), {e['recordsBefore']} records verified unchanged")
        total_saved = bytes_before_total - bytes_after_total
        print(f"[compact_edgelab_partitions] TOTAL: {compacted_files} file(s) compacted, "
              f"{bytes_before_total:,}B -> {bytes_after_total:,}B (saved {total_saved:,}B)")

    run_record = {
        "schemaVersion": "1",
        "runId": ids.new_run_id("CORPUS_COMPACTION", github_run_id=os.environ.get("GITHUB_RUN_ID")),
        "runType": "CORPUS_COMPACTION",
        "startedAt": started_at,
        "completedAt": ids.utc_now_iso(),
        "status": "success",
        "sourceWorkflow": os.environ.get("GITHUB_WORKFLOW"),
        "githubRunId": os.environ.get("GITHUB_RUN_ID"),
        "inputFiles": [f"data/edgelab/{e}/" for e in ENTITIES],
        "outputFiles": [f"data/edgelab/{e}/" for e in ENTITIES],
        "counts": {
            "filesCompacted": compacted_files,
            "bytesBefore": bytes_before_total,
            "bytesAfter": bytes_after_total,
            "bytesSaved": bytes_before_total - bytes_after_total,
            "byEntity": {
                entity: {
                    "compacted": sum(1 for e in entries if e["action"] == "compacted"),
                    "alreadyCompacted": sum(1 for e in entries if e["action"] == "already_compacted"),
                    "skippedRecent": sum(1 for e in entries if e["action"] == "skipped_recent"),
                }
                for entity, entries in results.items()
            },
        },
        "errors": [],
        "warnings": [],
        "createdAt": started_at,
        "provenance": {
            "sourceSystem": "edgelab_cli",
            "sourceFile": __file__,
            "sourceKey": started_at[:10],
            "capturedAt": started_at,
            "ingestedAt": ids.utc_now_iso(),
        },
    }
    storage.append_records(storage.partition_path("research_runs", started_at[:10]), [run_record], "runId")

    return 0


if __name__ == "__main__":
    sys.exit(main())
