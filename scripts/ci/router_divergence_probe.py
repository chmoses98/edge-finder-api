#!/usr/bin/env python3
"""
scripts/ci/router_divergence_probe.py
=====================================
Collects the evidence PROD-9 judges: what kalshi-bet-router, the only party
holding AUTHENTICATED Kalshi fills, says about MLB -- read from its own
delivery runs, which are public.

WHY THIS EXISTS
---------------
From 2026-09-24 to 2026-09-28 twelve real MLB wagers sat on the router's
proposal branch (PR #247) instead of this repository's canonical ledger. The
router's delivery runs were red the whole time; nobody saw them, because they
are dispatched by github-actions[bot] and notify no one. And every surface in
THIS repository could only see the ledger it had, so none of them could say
"the account holds wagers you do not".

The router prints, on every delivery run, one line per destination:

    ROUTER_COVERAGE sport=MLB eligible=78 newest_game_date=2026-09-27 \
        newest_first_fill_utc=2026-09-27 refused_provably=0

and one reconciliation line per destination:

    reconciliation by identity (MLB wagers, 78 eligible row(s)): on ledger 78,
        proposed not merged 0, refused 0 , UNACCOUNTED 0

This script fetches the newest completed delivery run AND the newest one that
started at least ``--settle-hours`` before it, and writes both parsed records
to a JSON file. Two runs, because the ordinary path from a fill to this
repository's main is two router runs (propose, then merge once CI is green):
a divergence present in a run that old is not latency.

It JUDGES NOTHING. The pure assertion is PROD-9 in production_health_gate.py.
A fetch failure is recorded as such, never as health.

Network: GET only, api.github.com, the public router repository.
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

ROUTER_REPO = "chmoses98/kalshi-bet-router"
DELIVERY_WORKFLOW = "deliver-wagers.yml"
API = "https://api.github.com"

COVERAGE_RE = re.compile(
    r"ROUTER_COVERAGE sport=(?P<sport>\w+) eligible=(?P<eligible>\d+) "
    r"newest_game_date=(?P<newest_game_date>\S+) "
    r"newest_first_fill_utc=(?P<newest_first_fill_utc>\S+) "
    r"refused_provably=(?P<refused_provably>\d+)"
)
RECONCILE_RE = re.compile(
    r"reconciliation by identity \((?P<sport>\w+) wagers, (?P<eligible>\d+) eligible row\(s\)\): "
    r"on ledger (?P<on_ledger>\d+), proposed not merged (?P<proposed_not_merged>\d+), "
    r"refused (?P<refused>\d+)\s*, UNACCOUNTED (?P<unaccounted>\d+)"
)
DESTINATIONS_FAILED_RE = re.compile(r"destinations that failed: (\d+)")


def parse_delivery_log(text, sport="MLB"):
    """Pure. The router's machine-readable claims about one sport, or None
    for any line the run did not print."""
    coverage = None
    for match in COVERAGE_RE.finditer(text):
        if match.group("sport") == sport:
            coverage = {
                "eligible": int(match.group("eligible")),
                "newestGameDate": _date_or_none(match.group("newest_game_date")),
                "newestFirstFillUtc": _date_or_none(match.group("newest_first_fill_utc")),
                "refusedProvably": int(match.group("refused_provably")),
            }
    reconciliation = None
    for match in RECONCILE_RE.finditer(text):
        if match.group("sport") == sport:
            reconciliation = {k: int(match.group(k)) for k in
                              ("eligible", "on_ledger", "proposed_not_merged",
                               "refused", "unaccounted")}
    failed = DESTINATIONS_FAILED_RE.findall(text)
    return {
        "coverage": coverage,
        "reconciliation": reconciliation,
        "destinationsFailed": int(failed[-1]) if failed else None,
    }


def _date_or_none(value):
    return value if re.match(r"^\d{4}-\d{2}-\d{2}$", value or "") else None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _get(url, token, raw=False, attempts=3):
    """GET with retries. The logs endpoint answers 302 to a pre-signed blob
    URL, which must be fetched WITHOUT the Authorization header -- the blob
    store rejects a request carrying one."""
    headers = {"Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28",
               "User-Agent": "edge-finder-api-health-gate"}
    if token:
        headers["Authorization"] = "Bearer %s" % token
    last = None
    for attempt in range(attempts):
        try:
            opener = urllib.request.build_opener(_NoRedirect)
            try:
                with opener.open(urllib.request.Request(url, headers=headers), timeout=60) as resp:
                    body = resp.read()
            except urllib.error.HTTPError as exc:
                if exc.code in (301, 302, 303, 307, 308) and exc.headers.get("Location"):
                    with urllib.request.urlopen(exc.headers["Location"], timeout=120) as resp:
                        body = resp.read()
                else:
                    raise
            return body.decode("utf-8", "replace") if raw else json.loads(body)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            last = exc
            time.sleep(2 ** attempt)
    raise RuntimeError("GET %s failed: %s" % (url.split("?")[0], last))


def _run_record(run, token):
    jobs = _get("%s/repos/%s/actions/runs/%d/jobs" % (API, ROUTER_REPO, run["id"]), token)
    text = ""
    for job in jobs.get("jobs", []):
        text += _get("%s/repos/%s/actions/jobs/%d/logs" % (API, ROUTER_REPO, job["id"]),
                     token, raw=True)
    record = parse_delivery_log(text)
    record.update({
        "runId": run["id"],
        "createdAt": run.get("created_at"),
        "conclusion": run.get("conclusion"),
        "url": run.get("html_url"),
    })
    return record


def collect(token, settle_hours):
    runs = _get("%s/repos/%s/actions/workflows/%s/runs?status=completed&per_page=100"
                % (API, ROUTER_REPO, DELIVERY_WORKFLOW), token).get("workflow_runs", [])
    # A cancelled run may have stopped before printing anything; it is not
    # evidence either way.
    runs = [r for r in runs if r.get("conclusion") in ("success", "failure")]
    if not runs:
        return {"fetchError": "no completed delivery run found"}
    latest = runs[0]
    latest_at = _parse_ts(latest["created_at"])
    settled = next((r for r in runs
                    if _parse_ts(r["created_at"]) <= latest_at - timedelta(hours=settle_hours)),
                   None)
    return {
        "routerRepo": ROUTER_REPO,
        "settleHours": settle_hours,
        "latest": _run_record(latest, token),
        "settled": _run_record(settled, token) if settled else None,
    }


def _parse_ts(value):
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("--out", required=True)
    parser.add_argument("--settle-hours", type=float, default=3.0)
    args = parser.parse_args(argv)
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    try:
        evidence = collect(token, args.settle_hours)
    except Exception as exc:  # recorded, never swallowed into health
        evidence = {"fetchError": "%s: %s" % (type(exc).__name__, exc)}
    evidence["collectedAt"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with open(args.out, "w") as handle:
        json.dump(evidence, handle, indent=2, sort_keys=True)
    print(json.dumps(evidence, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
