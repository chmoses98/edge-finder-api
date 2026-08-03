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
    """Walks the full tree -- PRE_GAME_DECISION manifests now live one
    level deeper (data/edgelab/snapshots/<date>/pre_game_decision/<runKey>/manifest.json)
    than POST_GAME_SETTLEMENT/CLOSING_LINE (.../<date>/<stage>/manifest.json),
    so a plain os.walk is simpler and correct for both shapes."""
    root = snap.SNAPSHOTS_ROOT
    if not os.path.isdir(root):
        return
    for dirpath, _dirs, files in os.walk(root):
        if "manifest.json" not in files:
            continue
        manifest_file = os.path.join(dirpath, "manifest.json")
        with open(manifest_file) as f:
            yield json.load(f), os.path.getsize(manifest_file)


def _dir_bytes(path):
    total = 0
    if not os.path.isdir(path):
        return 0
    for dirpath, _dirs, files in os.walk(path):
        for fn in files:
            try:
                total += os.path.getsize(os.path.join(dirpath, fn))
            except OSError:
                pass
    return total


REPORT_PATH = os.path.join("data", "edgelab", "reports", "storage_health_report.json")
# Forward Replay Corpus and Production Provenance milestone (item 12):
# 1/3/5-season projections explicitly, rather than a single --seasons
# flag -- the milestone asks for all three at once, not a caller-chosen one.
SEASON_HORIZONS = (1, 3, 5)


def main():
    parser = argparse.ArgumentParser(description="Dry-run storage/retention estimate for EdgeLab Snapshots + replay outputs.")
    parser.add_argument("--days-per-season", type=int, default=DEFAULT_DAYS_PER_SEASON)
    parser.add_argument("--report-path", default=REPORT_PATH)
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

    def _projections_for(marginal_bytes_per_day):
        return {
            f"{n}Season" if n == 1 else f"{n}Seasons": round(marginal_bytes_per_day * args.days_per_season * n, 1)
            for n in SEASON_HORIZONS
        }

    projections = {}
    for stage, bucket in per_stage.items():
        per_day_marginal = (bucket["manifestBytes"] + bucket["frozenBytes"]) / observed_days if observed_days else 0
        projections[stage] = {
            "observedDays": observed_days,
            "observedMarginalBytesPerDay": round(per_day_marginal, 1),
            "projectedBytesPerSeason": round(per_day_marginal * args.days_per_season, 1),
            "projectedBytes": _projections_for(per_day_marginal),
        }

    # ── Item 12: replay outputs get their own bucket + retention policy.
    # Research outputs may use a DIFFERENT retention policy than source
    # snapshots (e.g. could be pruned/regenerated from the still-retained
    # snapshot + replay-engine code, unlike a snapshot's own frozen
    # decision-time bytes, which can never be regenerated once the live
    # source is overwritten) -- reported separately, never folded silently
    # into the snapshot total.
    replay_runs_root = os.path.join("data", "edgelab", "replay_runs")
    replay_bytes = _dir_bytes(replay_runs_root)
    replay_run_dirs = [d for d in os.listdir(replay_runs_root)] if os.path.isdir(replay_runs_root) else []
    replay_dates = set()
    for run_id in replay_run_dirs:
        run_path = os.path.join(replay_runs_root, run_id, "replay_run.json")
        if os.path.exists(run_path):
            with open(run_path) as f:
                replay_dates.add(json.load(f).get("snapshotDate"))
    replay_observed_days = len(replay_dates)
    replay_per_day_marginal = replay_bytes / replay_observed_days if replay_observed_days else 0
    replay_bucket = {
        "runs": len(replay_run_dirs), "totalBytes": replay_bytes,
        "observedDays": replay_observed_days,
        "observedMarginalBytesPerDay": round(replay_per_day_marginal, 1),
        "projectedBytes": _projections_for(replay_per_day_marginal),
        "retentionNote": (
            "Research outputs (ReplayRun/ReplayResult) may use a different retention "
            "policy than source PRE_GAME_DECISION snapshots -- they are mechanically "
            "reproducible from the retained snapshot + current replay-engine code "
            "(deterministic rerun, see docs/REPLAY_ENGINE.md), unlike a snapshot's own "
            "frozen decision-time bytes, which cannot be regenerated once live sources "
            "are overwritten. No deletion policy is implemented in this milestone -- "
            "this note only documents that the two retention concerns are distinct."
        ),
    }

    snapshot_total_observed = sum(b["manifestBytes"] + b["frozenBytes"] for b in per_stage.values())
    total_observed = snapshot_total_observed + replay_bytes

    report = {
        "note": (
            "Marginal cost is FROZEN_COPY + manifest.json bytes only -- REFERENCED_IMMUTABLE "
            "components duplicate zero bytes by design (see docs/SNAPSHOT_ARCHITECTURE.md). "
            "Extrapolated from currently observed days; will sharpen as more real days accumulate. "
            "Manifests are retained permanently by policy (never pruned by any script in this "
            "repository) -- these are size estimates, not a retention/deletion mechanism."
        ),
        "daysPerSeasonAssumption": args.days_per_season,
        "seasonHorizons": list(SEASON_HORIZONS),
        "perStage": {
            stage: {**per_stage[stage], **projections[stage]} for stage in per_stage
        },
        "replayRuns": replay_bucket,
        "totalObservedBytes": total_observed,
        "totalProjectedBytes": {
            k: round(sum(p["projectedBytes"][k] for p in projections.values()) + replay_bucket["projectedBytes"][k], 1)
            for k in _projections_for(0)
        },
    }
    print(json.dumps(report, indent=2))

    os.makedirs(os.path.dirname(args.report_path), exist_ok=True)
    with open(args.report_path, "w") as f:
        json.dump(report, f, indent=2, sort_keys=True)
    print(f"\nFull report written to {args.report_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
