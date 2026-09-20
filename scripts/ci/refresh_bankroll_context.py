#!/usr/bin/env python3
"""
scripts/ci/refresh_bankroll_context.py
======================================
JUST-IN-TIME BANKROLL FRESHNESS.

Ask `kalshi-bet-router` to take a fresh authenticated Kalshi
available-cash reading and seal it into this repository's
`KALSHI_BANKROLL_CONTEXT` secret, and wait for that to finish, BEFORE
this repository builds a real-money handicapping card against it.

WHY THIS EXISTS
---------------
`lib/bankroll_context.py` only sizes in dollars from a reading less than
`DEFAULT_MAX_AGE_MINUTES` (30) old. The router's publisher is scheduled
`*/15`, but GitHub's scheduler is best-effort and on 2026-09-19 actually
fired it at 17:48, 19:31, 20:08, 22:19 and 00:15 -- gaps of up to two
and a half hours. The card build and the publisher were two independent
crons, so nothing ever guaranteed one ran shortly before the other, and
the public runtime sat at `bankrollStatus: STALE` /
`dollarSizingVerdict: NO_DOLLAR_SIZING` even though every publisher run
SUCCEEDED. Safe, and unusable.

Raising the cron frequency does not fix that; it just makes the same
best-effort scheduler miss more often. Coupling does: the build now
PULLS a reading it knows the age of, instead of hoping an unrelated cron
happened recently.

WHAT IT DOES NOT DO
-------------------
It never reads the balance and never sees the amount -- the router
remains the only component that holds a Kalshi credential, and this
script holds only a dispatch token. It never fails the workflow: a
refusal here must degrade to the EXISTING fail-closed behaviour (the
card reads whatever the secret holds, finds it stale, and refuses dollar
sizing), never to a slate that does not get fetched.

Exactly ONE dispatch per invocation, and a bounded wait. There is no
retry loop that could storm the router, and because the router's
publisher dispatches nothing back, the call graph is a single directed
edge and cannot cycle (pinned by
tests/test_bankroll_refresh_coupling.py).

Outcomes, written to GITHUB_OUTPUT as `outcome`:
    REFRESHED         the publisher ran and succeeded; the secret is new
    ADOPTED           a publisher run was already in flight; waited for it
    PUBLISHER_FAILED  it ran and failed; the card will fail closed
    TIMED_OUT         still running when the budget ran out
    NO_CREDENTIAL     no dispatch token configured (forks, or not set up yet)
    DISPATCH_REFUSED  the API refused the dispatch (token scope, ref, ...)

Usage:
    python3 scripts/ci/refresh_bankroll_context.py
    python3 scripts/ci/refresh_bankroll_context.py --max-wait-seconds 420
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

API_ROOT = os.environ.get("GITHUB_API_ROOT", "https://api.github.com")

#: The router repository and the workflow that reads the balance. The
#: publisher is the ONLY thing this token is ever pointed at.
ROUTER_REPO = "chmoses98/kalshi-bet-router"
PUBLISHER_WORKFLOW = "publish-bankroll.yml"
PUBLISHER_REF = "main"

#: The env var holding a fine-grained PAT whose ONLY permission is
#: Actions: read and write on ROUTER_REPO. It can neither read a secret
#: nor push a commit anywhere.
TOKEN_ENV = "ROUTER_BANKROLL_DISPATCH_TOKEN"

#: A dispatch returns 204 with no run id, so the run has to be found by
#: listing. Clock skew between the two sides makes an exact
#: created_at >= sent_at test unsafe.
DISPATCH_SKEW_SECONDS = 90

OUTCOME_REFRESHED = "REFRESHED"
OUTCOME_ADOPTED = "ADOPTED"
OUTCOME_PUBLISHER_FAILED = "PUBLISHER_FAILED"
OUTCOME_TIMED_OUT = "TIMED_OUT"
OUTCOME_NO_CREDENTIAL = "NO_CREDENTIAL"
OUTCOME_DISPATCH_REFUSED = "DISPATCH_REFUSED"

#: Outcomes after which the destination secret is expected to be fresh.
SUCCESSFUL_OUTCOMES = frozenset({OUTCOME_REFRESHED, OUTCOME_ADOPTED})

IN_FLIGHT_STATUSES = ("queued", "in_progress", "waiting", "requested", "pending")


def api(method, path, token, body=None, timeout=30):
    """(status, decoded_json_or_None). Never raises for an HTTP status."""
    url = path if path.startswith("http") else f"{API_ROOT}{path}"
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Accept", "application/vnd.github+json")
    request.add_header("Authorization", f"Bearer {token}")
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            return response.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as error:
        raw = error.read()
        try:
            return error.code, json.loads(raw) if raw else None
        except ValueError:
            return error.code, None
    except (urllib.error.URLError, TimeoutError, OSError):
        return 0, None


def list_runs(token, per_page=10):
    status, payload = api(
        "GET",
        f"/repos/{ROUTER_REPO}/actions/workflows/{PUBLISHER_WORKFLOW}/runs?per_page={per_page}",
        token)
    if status != 200 or not payload:
        return []
    return payload.get("workflow_runs") or []


def in_flight_run(token):
    """A publisher run already running, or None.

    THE IDEMPOTENCY RULE. Two slate builds close together, or a retry,
    must not queue a second reading -- the router's own `concurrency:
    publish-bankroll` with `cancel-in-progress: true` would then CANCEL
    the run this one is waiting on, and a cancelled publisher leaves the
    secret exactly as stale as it was.
    """
    for run in list_runs(token):
        if run.get("status") in IN_FLIGHT_STATUSES:
            return run
    return None


def find_dispatched_run(token, sent_at):
    """The run this invocation caused, or None if it has not appeared."""
    cutoff = sent_at - timedelta(seconds=DISPATCH_SKEW_SECONDS)
    for run in list_runs(token):
        if run.get("event") != "workflow_dispatch":
            continue
        created = parse_iso(run.get("created_at"))
        if created is not None and created >= cutoff:
            return run
    return None


def parse_iso(value):
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def wait_for(token, run_id, deadline, poll_seconds, sleep=time.sleep, now=None):
    """(status, conclusion) when it finishes, or (status, None) at the deadline."""
    now = now or (lambda: datetime.now(tz=timezone.utc))
    while True:
        status, payload = api("GET", f"/repos/{ROUTER_REPO}/actions/runs/{run_id}", token)
        if status == 200 and payload:
            if payload.get("status") == "completed":
                return "completed", payload.get("conclusion")
        if now() >= deadline:
            return (payload or {}).get("status"), None
        sleep(poll_seconds)


def refresh(token, *, max_wait_seconds, poll_seconds, sleep=time.sleep, now=None,
            out=sys.stdout):
    """(outcome, run_id_or_None). Never raises."""
    now = now or (lambda: datetime.now(tz=timezone.utc))
    if not (token or "").strip():
        print(f"  no {TOKEN_ENV} configured -- not asking the router for a reading. "
              "The card will use whatever the destination secret holds and refuse "
              "dollar sizing if it is stale.", file=out)
        return OUTCOME_NO_CREDENTIAL, None

    deadline = now() + timedelta(seconds=max_wait_seconds)

    existing = in_flight_run(token)
    if existing:
        print(f"  a publisher run is already in flight ({existing['id']}); waiting for it "
              "rather than dispatching a second one", file=out)
        run_id = existing["id"]
        adopted = True
    else:
        sent_at = now()
        status, payload = api(
            "POST",
            f"/repos/{ROUTER_REPO}/actions/workflows/{PUBLISHER_WORKFLOW}/dispatches",
            token, body={"ref": PUBLISHER_REF})
        if status != 204:
            message = (payload or {}).get("message") if isinstance(payload, dict) else None
            print(f"  the router refused the dispatch (HTTP {status}"
                  f"{': ' + message if message else ''}). The card will fail closed.",
                  file=out)
            return OUTCOME_DISPATCH_REFUSED, None
        print(f"  asked {ROUTER_REPO} to publish a fresh reading", file=out)
        adopted = False

        run = None
        while run is None:
            run = find_dispatched_run(token, sent_at)
            if run is not None:
                break
            if now() >= deadline:
                print("  the dispatched run never appeared within the budget", file=out)
                return OUTCOME_TIMED_OUT, None
            sleep(poll_seconds)
        run_id = run["id"]

    print(f"  publisher run {run_id}", file=out)
    status, conclusion = wait_for(token, run_id, deadline, poll_seconds,
                                  sleep=sleep, now=now)
    if status != "completed":
        print(f"  still {status!r} when the {max_wait_seconds}s budget ran out; "
              "not waiting longer. The card will fail closed if the reading is stale.",
              file=out)
        return OUTCOME_TIMED_OUT, run_id
    if conclusion != "success":
        print(f"  the publisher finished {conclusion!r}. The reading was NOT refreshed "
              "and the card will fail closed.", file=out)
        return OUTCOME_PUBLISHER_FAILED, run_id

    print("  the publisher succeeded: it read the balance, sealed it into this "
          "repository and verified the destination is holding it. The amount is not "
          "visible to this job and never reaches this log.", file=out)
    return (OUTCOME_ADOPTED if adopted else OUTCOME_REFRESHED), run_id


def write_outputs(outcome, run_id):
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a") as handle:
        handle.write(f"outcome={outcome}\n")
        handle.write(f"router_run_id={run_id or ''}\n")
        handle.write(f"refreshed={'true' if outcome in SUCCESSFUL_OUTCOMES else 'false'}\n")


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--max-wait-seconds", type=int, default=420)
    parser.add_argument("--poll-seconds", type=int, default=10)
    args = parser.parse_args(argv)

    outcome, run_id = refresh(
        os.environ.get(TOKEN_ENV, ""),
        max_wait_seconds=args.max_wait_seconds,
        poll_seconds=args.poll_seconds,
    )
    print(f"[refresh_bankroll_context] {outcome}")
    write_outputs(outcome, run_id)
    # ALWAYS 0. A slate fetch must never fail because the bankroll could
    # not be refreshed; the card's own staleness check is the gate.
    return 0


if __name__ == "__main__":
    sys.exit(main())
