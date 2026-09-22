#!/usr/bin/env python3
"""Bounded multi-cycle loop for the MRV collector. RESEARCH ONLY, READ-ONLY.

Runs capture cycles every --cadence-minutes for --budget-minutes, persisting
(commit + push to the research branch) after EVERY cycle so a mid-job failure
loses at most one cycle.  Delivered cadence is whatever the persisted
manifests show; this script only paces.

Usage: run_loop.py --budget-minutes 340 --cadence-minutes 10 --branch research/mrv-prospective-v1 [--no-git]
"""
import argparse
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, REPO)

from lib.edgelab.research.mrv_collector import STORAGE_RELATIVE_ROOT, DEFAULT_RESEARCH_BRANCH  # noqa: E402


def persist(branch, root_rel):
    cmd = [sys.executable, os.path.join(REPO, "scripts", "ci", "git_data_commit.py"),
           "--message", "MRV prospective capture %s" % datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "--branch", branch, root_rel]
    return subprocess.call(cmd, cwd=REPO)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--budget-minutes", type=float, default=340.0)
    ap.add_argument("--cadence-minutes", type=float, default=10.0)
    ap.add_argument("--branch", default=DEFAULT_RESEARCH_BRANCH)
    ap.add_argument("--no-git", action="store_true")
    ap.add_argument("--max-cycles", type=int, default=0)
    args = ap.parse_args(argv)
    if args.branch in ("main", "master"):
        print("refusing to persist research capture to a protected branch", file=sys.stderr)
        return 2
    t_end = time.time() + args.budget_minutes * 60.0
    n = 0
    overruns = 0
    while time.time() < t_end and (not args.max_cycles or n < args.max_cycles):
        t0 = time.time()
        n += 1
        rc = subprocess.call([sys.executable, os.path.join(REPO, "scripts", "research", "mrv_collector", "run_cycle.py"),
                              "--trigger", os.environ.get("GITHUB_EVENT_NAME") or "loop", "--attempt", "1"], cwd=REPO)
        if not args.no_git:
            prc = persist(args.branch, STORAGE_RELATIVE_ROOT)
            if prc != 0:
                print("::warning::persist failed (rc=%d) after cycle %d; rows remain in the working tree and the next persist retries them" % (prc, n), file=sys.stderr)
        took = time.time() - t0
        wait = args.cadence_minutes * 60.0 - took
        print("[mrv-loop] cycle %d rc=%d took=%.0fs wait=%.0fs" % (n, rc, took, max(0.0, wait)), flush=True)
        if wait < 0:
            overruns += 1
        if time.time() + max(0.0, wait) >= t_end:
            break
        if wait > 0:
            time.sleep(wait)
    print("[mrv-loop] done cycles=%d overruns=%d" % (n, overruns))
    return 0


if __name__ == "__main__":
    sys.exit(main())
