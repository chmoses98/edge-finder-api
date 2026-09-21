#!/usr/bin/env python3
"""
scripts/edgelab/plan_near_close_capture.py
==========================================
Coordinator CLI for near-close capture. Decides whether THIS wake should
dispatch a market capture, and emits the plan as JSON for the workflow
and for the capture health report.

Reads game start times from the canonical normalized slate
(data/pipeline/<date>/normalized_slate.json -> data.games[].startTime),
which is where lib/edgelab/market_universe.py already takes them from.
The games partition is NOT used: its scheduledStartTime is null in
practice, so trusting it would silently plan nothing.

Existing coverage is read from the archived snapshot filenames
(data/kalshi_registry_snapshots/kalshi_search_<date>_<HHMM>.json), which
are the actual evidence a capture happened -- not from a workflow's own
claim to have run.

Exit code is 0 whether or not a capture is due; "nothing to do" is a
normal outcome, not a failure. The workflow reads shouldCapture from the
emitted plan.

Usage:
    python3 scripts/edgelab/plan_near_close_capture.py [--date YYYY-MM-DD] [--now ISO8601]
"""
import argparse
import datetime
import glob
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab.near_close_plan import build_capture_plan

SNAPSHOT_RE = re.compile(r"kalshi_search_(\d{4}-\d{2}-\d{2})_(\d{2})(\d{2})\.json$")


def load_slate_games(date):
    """Games with their canonical scheduled start, or [] when no slate exists."""
    path = os.path.join("data", "pipeline", date, "normalized_slate.json")
    if not os.path.exists(path):
        return []
    try:
        with open(path) as fh:
            envelope = json.load(fh)
    except (json.JSONDecodeError, OSError):
        return []
    games = []
    for g in (envelope.get("data") or {}).get("games") or []:
        games.append({
            "gameId": g.get("gameId"),
            "scheduledStart": g.get("startTime"),
            "awayTeam": (g.get("away") or {}).get("abbr"),
            "homeTeam": (g.get("home") or {}).get("abbr"),
            "status": g.get("status"),
        })
    return games


def existing_capture_times(date, snapshot_dir=None):
    """When captures actually landed today, read off the archive itself."""
    snapshot_dir = snapshot_dir or os.path.join("data", "kalshi_registry_snapshots")
    out = []
    for path in glob.glob(os.path.join(snapshot_dir, "kalshi_search_%s_*.json" % date)):
        m = SNAPSHOT_RE.search(os.path.basename(path))
        if m:
            out.append("%sT%s:%s:00Z" % (m.group(1), m.group(2), m.group(3)))
    return sorted(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", default=None, help="slate date (default: today UTC)")
    ap.add_argument("--now", default=None, help="evaluation time (default: real now UTC)")
    ap.add_argument("--out", default=None, help="also write the plan JSON here")
    args = ap.parse_args()

    now = args.now or datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    date = args.date or now[:10]

    games = load_slate_games(date)
    captures = existing_capture_times(date)
    plan = build_capture_plan(games, now, captures)
    plan["date"] = date
    plan["existingCaptureCount"] = len(captures)
    plan["githubRunId"] = os.environ.get("GITHUB_RUN_ID")
    plan["githubWorkflow"] = os.environ.get("GITHUB_WORKFLOW")

    print(json.dumps(plan, indent=2, sort_keys=True))
    if args.out:
        os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
        with open(args.out, "w") as fh:
            json.dump(plan, fh, indent=2, sort_keys=True)

    step_output = os.environ.get("GITHUB_OUTPUT")
    if step_output:
        with open(step_output, "a") as fh:
            fh.write("should_capture=%s\n" % ("true" if plan["shouldCapture"] else "false"))
            fh.write("games_due=%d\n" % len(plan["gamesDue"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
