#!/usr/bin/env python3
"""Request the ONE successor of a completed MRV capture job. RESEARCH ONLY.

Called as the last step of .github/workflows/research-mrv-prospective-capture.yml,
which runs it only when every earlier step succeeded (capture, persistence
verification, isolation checks) on the default branch.  GitHub's scheduled
delivery for this repository is sparse (1 of the first 5 hourly slots fired),
so without this a 340-minute bounded job is followed by a multi-hour gap.

Decision (pure, tested):
  * kill switch: repository variable MRV_CONTINUOUS_CAPTURE_ENABLED set to
    false/0/no/off/disabled -> no successor.  Unset or anything else -> enabled.
  * another run of this workflow already queued/pending/waiting/in progress
    (for example an hourly cron run) -> no successor; that run IS the successor
    and the concurrency group serialises it.
  * otherwise exactly one `gh workflow run` with the normal values
    budget_minutes=340, cadence_minutes=10.
If the run list cannot be read, one successor is still requested: the
non-cancelling concurrency group admits one running and one pending run, so a
redundant request can never produce overlapping collectors.

Usage: chain_successor.py --workflow <file> --ref <branch> [--dry-run]
Env:   GITHUB_RUN_ID, MRV_CONTINUOUS_CAPTURE_ENABLED, GH_TOKEN
"""
import argparse
import json
import os
import subprocess
import sys

KILL_VALUES = ("false", "0", "no", "off", "disabled")
ACTIVE_STATUSES = ("queued", "pending", "waiting", "requested", "in_progress")
SUCCESSOR_INPUTS = (("budget_minutes", "340"), ("cadence_minutes", "10"))


def continuous_enabled(raw):
    return (raw or "").strip().lower() not in KILL_VALUES


def decide(enabled_raw, runs, current_run_id, ref=None):
    """
    enabled_raw: the repository variable's value ('' when unset).
    runs: [{"databaseId": int, "status": str, "headBranch": str}] for this workflow, or None when unreadable.
    ref: the branch successors run on; runs on other branches (which never chain) are not successors.
    -> {"action": "DISPATCH" | "SKIP", "reason": str, "others": [run ids]}
    """
    if not continuous_enabled(enabled_raw):
        return {"action": "SKIP", "reason": "KILL_SWITCH_MRV_CONTINUOUS_CAPTURE_ENABLED_FALSE", "others": []}
    if runs is None:
        return {"action": "DISPATCH", "reason": "RUN_LIST_UNAVAILABLE_CONCURRENCY_GROUP_PREVENTS_OVERLAP", "others": []}
    cur = str(current_run_id or "")
    others = sorted(str(r.get("databaseId")) for r in runs
                    if str(r.get("databaseId")) != cur and (r.get("status") or "").lower() in ACTIVE_STATUSES
                    and (ref is None or r.get("headBranch") in (None, ref)))
    if others:
        return {"action": "SKIP", "reason": "SUCCESSOR_ALREADY_QUEUED", "others": others}
    return {"action": "DISPATCH", "reason": "NO_ACTIVE_SUCCESSOR", "others": []}


def dispatch_command(workflow, ref):
    cmd = ["gh", "workflow", "run", workflow, "--ref", ref]
    for k, v in SUCCESSOR_INPUTS:
        cmd += ["-f", "%s=%s" % (k, v)]
    return cmd


def list_runs(workflow, runner=subprocess.run):
    r = runner(["gh", "run", "list", "--workflow", workflow, "--limit", "20", "--json", "databaseId,status,headBranch"],
               capture_output=True, text=True)
    if r.returncode != 0:
        return None
    try:
        return json.loads(r.stdout or "[]")
    except ValueError:
        return None


def main(argv=None, runner=subprocess.run, env=None):
    env = os.environ if env is None else env
    ap = argparse.ArgumentParser()
    ap.add_argument("--workflow", required=True)
    ap.add_argument("--ref", required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    d = decide(env.get("MRV_CONTINUOUS_CAPTURE_ENABLED", ""), list_runs(args.workflow, runner), env.get("GITHUB_RUN_ID"), args.ref)
    d["currentRunId"] = env.get("GITHUB_RUN_ID")
    if d["action"] == "DISPATCH":
        d["command"] = dispatch_command(args.workflow, args.ref)
        if not args.dry_run:
            rc = runner(d["command"]).returncode
            d["dispatchReturnCode"] = rc
            print(json.dumps(d, sort_keys=True))
            return 0 if rc == 0 else 1
    print(json.dumps(d, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
