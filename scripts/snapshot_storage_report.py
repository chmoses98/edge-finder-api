#!/usr/bin/env python3
"""
scripts/snapshot_storage_report.py
=====================================
Historical Capture Completeness and Immutable Snapshot Foundation
milestone (item 11): a dry-run storage/retention report. Never deletes
anything -- purely descriptive, computed from real, already-committed
Snapshot manifests (run scripts/backfill_snapshots.py or let production
workflows accumulate a few days first if this reports zero snapshots).

Marginal storage cost is FROZEN_COPY bytes only -- REFERENCED_IMMUTABLE
components cost ~0 marginal bytes (no duplication; see
docs/SNAPSHOT_ARCHITECTURE.md's classification rule). manifest.json
itself is small and counted separately.

Usage: python3 scripts/snapshot_storage_report.py [--days-per-season N] [--seasons N]
"""
import argparse
import json
import os
import sys

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT_DIR)

from lib.edgelab import snapshot as snap  # noqa: E402

DEFAULT_DAYS_PER_SEASON = 185  # ~6 MLB months, matches this repo's own season-length assumptions elsewhere


def _iter_manifests():
    root = snap.SNAPSHOTS_ROOT
    if not os.path.isdir(root):
        return
    for date_name in sorted(os.listdir(root)):
        date_dir = os.path.join(root, date_name)
        if not os.path.isdir(date_dir):
            continue
        for stage_name in sorted(os.listdir(date_dir)):
            manifest_file = os.path.join(date_dir, stage_name, "manifest.json")
            if os.path.isfile(manifest_file):
                with open(manifest_file) as f:
                    yield json.load(f), os.path.getsize(manifest_file)


def main():
    parser = argparse.ArgumentParser(description="Dry-run storage/retention estimate for EdgeLab Snapshots.")
    parser.add_argument("--days-per-season", type=int, default=DEFAULT_DAYS_PER_SEASON)
    parser.add_argument("--seasons", type=int, default=3)
    args = parser.parse_args()

    per_stage = {}
    manifest_count = 0
    for manifest, manifest_bytes in _iter_manifests():
        stage = manifest["snapshotStage"]
        bucket = per_stage.setdefault(stage, {
            "manifests": 0, "manifestBytes": 0, "frozenBytes": 0, "frozenFiles": 0, "referencedBytes": 0,
        })
        bucket["manifests"] += 1
        bucket["manifestBytes"] += manifest_bytes
        for component in manifest.get("components", []):
            if component.get("storageMode") == snap.STORAGE_FROZEN_COPY and component.get("byteSize"):
                bucket["frozenBytes"] += component["byteSize"]
                bucket["frozenFiles"] += 1
            elif component.get("storageMode") == snap.STORAGE_REFERENCED_IMMUTABLE and component.get("byteSize"):
                bucket["referencedBytes"] += component["byteSize"]
        manifest_count += 1

    observed_days = len({m["snapshotDate"] for m, _ in _iter_manifests()}) if manifest_count else 0

    projections = {}
    for stage, bucket in per_stage.items():
        per_day_marginal = (bucket["manifestBytes"] + bucket["frozenBytes"]) / observed_days if observed_days else 0
        projections[stage] = {
            "observedDays": observed_days,
            "observedMarginalBytesPerDay": round(per_day_marginal, 1),
            "projectedBytesPerSeason": round(per_day_marginal * args.days_per_season, 1),
            "projectedBytesFor_N_seasons": round(per_day_marginal * args.days_per_season * args.seasons, 1),
        }

    report = {
        "note": (
            "Marginal cost is FROZEN_COPY + manifest.json bytes only -- REFERENCED_IMMUTABLE "
            "components duplicate zero bytes by design (see docs/SNAPSHOT_ARCHITECTURE.md). "
            "Extrapolated from currently observed days; will sharpen as more real days accumulate."
        ),
        "daysPerSeasonAssumption": args.days_per_season,
        "seasonsProjected": args.seasons,
        "perStage": {
            stage: {**per_stage[stage], **projections[stage]} for stage in per_stage
        },
        "totalObservedBytes": sum(b["manifestBytes"] + b["frozenBytes"] for b in per_stage.values()),
        "totalProjectedBytesFor_N_seasons": round(
            sum(p["projectedBytesFor_N_seasons"] for p in projections.values()), 1
        ),
    }
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
