#!/usr/bin/env python3
"""
scripts/prune_kalshi_snapshots.py
=====================================
Production Reliability and Settlement Recovery milestone: dry-run-first
retention tool for data/kalshi_registry_snapshots/. See
lib/snapshot_retention.py for the full policy rationale (why 21 days,
what the previously-broken `find -mtime +3` step actually did wrong, and
why dated per-slate-date files are always kept forever).

Dry-run by default -- never deletes anything unless --execute is passed.
Deterministic given the same directory contents, --today, and
--retention-days. Prints/writes a machine-readable plan (see
build_retention_plan()'s docstring for the exact shape) before touching
anything.

Usage:
    python3 scripts/prune_kalshi_snapshots.py                     # dry run, default 21-day window
    python3 scripts/prune_kalshi_snapshots.py --plan-out plan.json
    python3 scripts/prune_kalshi_snapshots.py --execute            # actually deletes pruned files
    python3 scripts/prune_kalshi_snapshots.py --retention-days 30 --today 2026-08-02
"""
import argparse
import datetime
import json
import os
import sys

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT_DIR)

from lib.snapshot_retention import DEFAULT_RETENTION_DAYS, build_retention_plan  # noqa: E402

DEFAULT_SNAPSHOT_DIR = os.path.join(ROOT_DIR, "data", "kalshi_registry_snapshots")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--snapshot-dir", default=DEFAULT_SNAPSHOT_DIR,
                         help="Directory of kalshi_search_*.json snapshots (default: data/kalshi_registry_snapshots/)")
    parser.add_argument("--retention-days", type=int, default=DEFAULT_RETENTION_DAYS,
                         help=f"Timestamped snapshots older than this many days are pruned (default: {DEFAULT_RETENTION_DAYS})")
    parser.add_argument("--today", default=None,
                         help="Reference date YYYY-MM-DD (default: real UTC today). Set explicitly for deterministic/reproducible runs.")
    parser.add_argument("--plan-out", default=None, help="Write the machine-readable plan JSON to this path (also always printed to stdout).")
    parser.add_argument("--execute", action="store_true",
                         help="Actually delete the files the plan marks for pruning. Without this flag, nothing is ever deleted.")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    today = (
        datetime.date.fromisoformat(args.today) if args.today
        else datetime.datetime.now(datetime.timezone.utc).date()
    )

    if not os.path.isdir(args.snapshot_dir):
        print(f"ERROR: snapshot directory does not exist: {args.snapshot_dir}", file=sys.stderr)
        return 1

    plan = build_retention_plan(args.snapshot_dir, today, args.retention_days)
    plan["mode"] = "EXECUTE" if args.execute else "DRY_RUN"

    print(json.dumps(plan, indent=2))
    if args.plan_out:
        with open(args.plan_out, "w") as f:
            json.dump(plan, f, indent=2)

    print(
        f"\n{'EXECUTE' if args.execute else 'DRY RUN'}: "
        f"{len(plan['timestampedFilesToPrune'])} file(s) to prune, "
        f"{plan['projectedBytesReclaimed']} bytes (~{plan['projectedBytesReclaimed'] / 1e6:.2f} MB) reclaimable. "
        f"{plan['datedFilesKeptForever']} dated file(s) and {plan['timestampedFilesKept']} recent timestamped file(s) kept.",
        file=sys.stderr,
    )

    if not args.execute:
        print("Dry run only -- no files deleted. Re-run with --execute to actually prune.", file=sys.stderr)
        return 0

    deleted = 0
    for filename in plan["timestampedFilesToPrune"]:
        path = os.path.join(args.snapshot_dir, filename)
        try:
            os.remove(path)
            deleted += 1
        except OSError as e:
            print(f"ERROR: failed to delete {path}: {e}", file=sys.stderr)
    print(f"Deleted {deleted}/{len(plan['timestampedFilesToPrune'])} file(s).", file=sys.stderr)
    return 0 if deleted == len(plan["timestampedFilesToPrune"]) else 1


if __name__ == "__main__":
    sys.exit(main())
