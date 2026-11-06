#!/usr/bin/env python3
"""Store official exchange results for tracked hitter-hit tickers as EVIDENCE (never as settlements).

Input: the output of scripts/edgelab/verify_kalshi_hitter_hit_results.py. Default is a dry run (report only).
--apply writes data/edgelab/official_result_evidence/<gameDate>.jsonl (+ ..._conflicts/ for contradictions) with the
idempotent, quarantine-on-conflict merge in lib/edgelab/official_result_evidence.py. Canonical settlements, bets and
model evaluations are never touched; unresolved settlements stay unresolved.

Usage:
  python3 scripts/edgelab/reconcile_official_hitter_results.py --reconciliation artifacts/hitter_hit_exchange_reconciliation.json
  python3 scripts/edgelab/reconcile_official_hitter_results.py --reconciliation ... --apply
"""
import argparse
import contextlib
import json
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab import storage
from lib.edgelab.official_result_evidence import CONFLICT_ENTITY, ENTITY, build_evidence_records, merge_evidence


def reconcile(reconciliation, apply=False):
    incoming, summary = build_evidence_records(reconciliation)
    by_date = defaultdict(list)
    for rec in incoming:
        by_date[rec["gameDate"]].append(rec)
    totals = {"inserted": 0, "unchanged": 0, "upgraded": 0, "conflictsQuarantined": 0, "regressionsPrevented": 0}
    all_conflicts = []
    for date, recs in sorted(by_date.items()):
        path = storage.partition_path(ENTITY, date)
        cpath = storage.partition_path(CONFLICT_ENTITY, date)
        # a dry run takes no lock and creates nothing (locked() would create the directory and a .lock file)
        with (storage.locked(path) if apply else contextlib.nullcontext()):
            existing = list(storage.read_records(path))
            merged, conflicts, stats = merge_evidence(existing, recs)
            for k in totals:
                totals[k] += stats[k]
            all_conflicts.extend(conflicts)
            if apply:
                if merged != existing:
                    storage.write_all_records(path, merged)
                if conflicts:
                    known = list(storage.read_records(cpath))
                    have = {c["conflictId"] for c in known}
                    fresh = [c for c in conflicts if c["conflictId"] not in have]
                    if fresh:
                        storage.write_all_records(cpath, sorted(known + fresh, key=lambda c: c["conflictId"]))
    return {"dryRun": not apply, **summary, "merge": totals, "quarantinedConflicts": all_conflicts,
            "settlementsModified": 0}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reconciliation", required=True)
    ap.add_argument("--apply", action="store_true", help="write evidence partitions (default: report only)")
    args = ap.parse_args()
    with open(args.reconciliation, encoding="utf-8") as f:
        result = reconcile(json.load(f), apply=args.apply)
    print(json.dumps(result, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
