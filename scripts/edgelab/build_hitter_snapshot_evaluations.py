#!/usr/bin/env python3
"""Emit PIT-valid ModelEvaluation-shaped records from the prospective hitter projection snapshots.

Reads data/edgelab/hitter_projection_snapshots, writes ONLY data/edgelab/model_evaluations_hitter_snapshot/<generation
date>.jsonl, and only with --apply (default: dry run, creates nothing). Canonical model_evaluations partitions are never
touched. Idempotent (id = f(snapshot id); no wall-clock fields). See lib/edgelab/hitter_snapshot_evaluations.py.

Usage:
  python3 scripts/edgelab/build_hitter_snapshot_evaluations.py            # dry run, all snapshot dates
  python3 scripts/edgelab/build_hitter_snapshot_evaluations.py --apply
"""
import argparse
import contextlib
import glob
import json
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab import storage
from lib.edgelab.hitter_snapshot_evaluations import ENTITY, build_records


def load_snapshots(root=None):
    root = root or storage.EDGELAB_ROOT
    for path in sorted(glob.glob(os.path.join(root, "hitter_projection_snapshots", "*.jsonl*"))):
        for row in storage.read_records(path):
            yield row


def emit(snapshots, apply=False):
    snapshots = list(snapshots)
    records, rejects = build_records(snapshots)
    by_date = defaultdict(list)
    for r in records:
        by_date[r["partitionDate"]].append(r)
    stats = {"inserted": 0, "unchanged": 0, "updated": 0}
    for date, recs in sorted(by_date.items()):
        path = storage.partition_path(ENTITY, date)
        with (storage.locked(path) if apply else contextlib.nullcontext()):
            existing = {r["modelEvaluationId"]: r for r in storage.read_records(path)}
            merged = dict(existing)
            for r in recs:
                old = existing.get(r["modelEvaluationId"])
                stats["unchanged" if old == r else ("updated" if old else "inserted")] += 1
                merged[r["modelEvaluationId"]] = r
            if apply and merged != existing:
                storage.write_all_records(path, [merged[k] for k in sorted(merged)])
    return {"dryRun": not apply, "snapshotsRead": len(snapshots), "recordsEligible": len(records),
            "rejected": dict(sorted(rejects.items())), "partitions": len(by_date), "write": stats,
            "canonicalModelEvaluationsModified": 0}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    print(json.dumps(emit(load_snapshots(), apply=args.apply), indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
