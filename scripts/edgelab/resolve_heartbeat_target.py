#!/usr/bin/env python3
"""
scripts/edgelab/resolve_heartbeat_target.py
================================================================
The workflow-side half of the heartbeat's ONE date-resolution path
(Heartbeat False-Failure Incident, 2026-08-27 -- see
lib/edgelab/production_date.py for the full incident write-up and the
invariant "delay must not change the date being validated").

This script is deliberately thin: every semantic decision lives in the
pure lib.edgelab.production_date.resolve_target_date, and this file only
does the two impure things that function must not do --

  1. read the GitHub Actions run context (event name, the run's own
     `github.event.schedule` cron literal, the manual dispatch date);
  2. obtain a DURABLE anchor timestamp for the run.

Anchor precedence, and why it is not just `github.run_started_at`
---------------------------------------------------------------------
GitHub's workflow-run object carries two timestamps: `created_at` (when
the run was created -- for a scheduled run, when GitHub actually
dispatched it) and `run_started_at`, documented as "the start time of
the latest run", i.e. it RESETS when a run is re-run. Re-running a
workflow does not create a new run: the run id and its `created_at`
survive, only `run_attempt` increments. So:

  1. --anchor / HEARTBEAT_ANCHOR   -- explicit override (tests, replays)
  2. REST created_at of THIS run   -- durable across re-runs, so a
     re-run of the 2026-08-26 heartbeat clicked on 2026-08-28 still
     resolves the 2026-08-26 checkpoint
  3. RUN_STARTED_AT (github.run_started_at) -- correct on a first
     attempt, and only an approximation on a re-run; recorded as such
     in `anchorSource` so any artifact produced this way is auditable
  4. the wall clock -- last resort, same caveat, recorded as such

Whichever anchor is used, the cron floor in production_date makes the
DELAY itself irrelevant: only an anchor that lands before the intended
checkpoint (impossible for a real run) or after the NEXT checkpoint
(a re-run more than one cron period late, falling back to 3/4) can
change the answer -- which is exactly why 2 exists and is preferred.

Writes the resolution JSON to --out and, when running under Actions,
`target_date` / `settlement_date` to $GITHUB_OUTPUT. Never reads any
repository data file: the target date must never depend on which date
happens to have artifacts.
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab.production_date import TargetDateError, resolve_target_date

ANCHOR_SOURCE_EXPLICIT = "explicit_anchor_argument"
ANCHOR_SOURCE_REST_CREATED_AT = "github_rest_run_created_at"
ANCHOR_SOURCE_RUN_STARTED_AT = "github_context_run_started_at_resets_on_rerun"
ANCHOR_SOURCE_WALL_CLOCK = "wall_clock_fallback_no_github_context"

API_ROOT = os.environ.get("GITHUB_API_URL", "https://api.github.com")


def fetch_run_created_at(repository, run_id, token, *, timeout=15, opener=None):
    """`created_at` for a workflow run, or None on any failure.

    Best-effort by design: a token/network/API problem must degrade to
    the next anchor in the precedence list (and say so in the artifact),
    never take the heartbeat down and never silently invent a date.
    """
    if not (repository and run_id):
        return None
    url = f"{API_ROOT}/repos/{repository}/actions/runs/{run_id}"
    request = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "edgelab-heartbeat-target-resolver",
    })
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        opener = opener or urllib.request.urlopen
        with opener(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"[resolve_heartbeat_target] WARNING: could not read run created_at ({exc}); falling back", file=sys.stderr)
        return None
    return payload.get("created_at") or None


def _schedule_expression_from_event_payload(path):
    """`github.event.schedule` as delivered in the event payload file."""
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            return (json.load(f) or {}).get("schedule") or None
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def build_parser():
    parser = argparse.ArgumentParser(description="Resolve the EdgeLab heartbeat's target production date.")
    parser.add_argument("--event-name", default=None, help="Defaults to $GITHUB_EVENT_NAME.")
    parser.add_argument("--dispatch-date", default=None, help="workflow_dispatch input date; defaults to $HEARTBEAT_DISPATCH_DATE.")
    parser.add_argument("--schedule-expression", default=None, help="github.event.schedule; defaults to $HEARTBEAT_SCHEDULE_EXPRESSION or the event payload.")
    parser.add_argument("--anchor", default=None, help="Explicit anchor timestamp (ISO-8601); defaults to $HEARTBEAT_ANCHOR, then the run's REST created_at.")
    parser.add_argument("--out", default=None, help="Write the resolution JSON here.")
    parser.add_argument("--github-output", action="store_true", help="Also write target_date/settlement_date to $GITHUB_OUTPUT.")
    return parser


def resolve(argv=None, env=None, *, opener=None):
    """Parse arguments and resolve -- the entry point tests drive directly."""
    return resolve_args(build_parser().parse_args(argv), env, opener=opener)


def resolve_args(args, env=None, *, opener=None):
    env = os.environ if env is None else env

    event_name = args.event_name if args.event_name is not None else env.get("GITHUB_EVENT_NAME", "")
    dispatch_date = args.dispatch_date if args.dispatch_date is not None else env.get("HEARTBEAT_DISPATCH_DATE", "")
    schedule_expression = args.schedule_expression or env.get("HEARTBEAT_SCHEDULE_EXPRESSION") or None
    if event_name == "schedule" and not schedule_expression:
        schedule_expression = _schedule_expression_from_event_payload(env.get("GITHUB_EVENT_PATH"))

    anchor = args.anchor or env.get("HEARTBEAT_ANCHOR") or None
    anchor_source = ANCHOR_SOURCE_EXPLICIT if anchor else None
    if event_name == "schedule" and not anchor:
        anchor = fetch_run_created_at(
            env.get("GITHUB_REPOSITORY"), env.get("GITHUB_RUN_ID"),
            env.get("GITHUB_TOKEN") or env.get("GH_TOKEN"), opener=opener,
        )
        anchor_source = ANCHOR_SOURCE_REST_CREATED_AT if anchor else None
    if event_name == "schedule" and not anchor:
        anchor = env.get("HEARTBEAT_RUN_STARTED_AT") or None
        anchor_source = ANCHOR_SOURCE_RUN_STARTED_AT if anchor else None
    if event_name == "schedule" and not anchor:
        from datetime import datetime, timezone
        anchor = datetime.now(timezone.utc)
        anchor_source = ANCHOR_SOURCE_WALL_CLOCK

    return resolve_target_date(
        event_name=event_name or None,
        dispatch_date=dispatch_date,
        schedule_expression=schedule_expression,
        anchor=anchor,
        anchor_source=anchor_source,
    )


def main(argv=None, env=None, *, opener=None):
    env = os.environ if env is None else env
    args = build_parser().parse_args(sys.argv[1:] if argv is None else argv)

    try:
        record = resolve_args(args, env, opener=opener)
    except TargetDateError as exc:
        print(f"[resolve_heartbeat_target] FATAL: {exc}", file=sys.stderr)
        return 2

    text = json.dumps(record, indent=2, sort_keys=True)
    print(text)
    if args.out:
        directory = os.path.dirname(os.path.abspath(args.out))
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(args.out, "w") as f:
            f.write(text + "\n")

    if args.github_output and env.get("GITHUB_OUTPUT"):
        with open(env["GITHUB_OUTPUT"], "a") as f:
            f.write(f"target_date={record['targetDate']}\n")
            f.write(f"settlement_date={record['settlementDate']}\n")
            f.write(f"trigger_type={record['triggerType']}\n")

    print(
        f"[resolve_heartbeat_target] triggerType={record['triggerType']} targetDate={record['targetDate']} "
        f"settlementDate={record['settlementDate']} checkpoint={record['scheduledCheckpointUtc']} "
        f"anchor={record['anchorTimestamp']} ({record['anchorSource']}) delayedRun={record['delayedRun']}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
