#!/usr/bin/env python3
"""MLB-ALPHA-0002: pull HISTORICAL Pinnacle (via The Odds API /historical)
snapshots for chosen MLB dates, on a fixed pregame time grid, and store
the raw responses. Family D (external sharp-market lead/lag) needs
timestamped sharp prices; the repo has never archived any per-date
sportsbook history (docs/EDGELAB_HISTORICAL_SHARP_MARKET_AUDIT.md).

Credit discipline: The Odds API meters historical calls per market per
region (10 credits each). A snapshot with markets=h2h,totals costs 20.
The script reads x-requests-remaining on every call and HARD-STOPS at
--max-credits. Nothing is estimated; the manifest records the real
before/after credit counter. Snapshot selection is per-game safe: every
snapshot time is stamped, and consumers must only use a snapshot taken
strictly before that game's commence_time (the audit's documented defect
was a blanket end-of-day snapshot).

Storage (raw JSON preserved, write-once, idempotent skip):
  data/edgelab/research_artifacts/mlb_alpha_0002/pinnacle_history/
      <date>/<snapshotIsoZ>.json.gz  raw API response
      manifest.json                  per-snapshot status + credits

RESEARCH ONLY. Read-only. Requires ODDS_API_KEY (GitHub Actions secret);
the research sandbox has neither the key nor egress, so run via
.github/workflows/research-sharp-market-probe.yml with
  script=scripts/research/mlb_alpha_0002/pull_pinnacle_history.py
"""

import argparse
import glob
import gzip
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, REPO)
from lib.edgelab.mlb_alpha_identity import parse_event_ticker  # noqa: E402

BASE_URL = "https://api.the-odds-api.com/v4"
SPORT = "baseball_mlb"
KEY = (os.environ.get("ODDS_API_KEY") or "").strip()
OUT = os.path.join(REPO, "data", "edgelab", "research_artifacts", "mlb_alpha_0002",
                   "pinnacle_history")
MANIFEST = os.path.join(OUT, "manifest.json")
SETTLEMENTS = os.path.join(REPO, "data", "edgelab", "settlements")


def api_get(url):
    """-> (json or None, remaining-credits header or None, error)"""
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            rem = resp.headers.get("x-requests-remaining")
            used = resp.headers.get("x-requests-used")
            return json.loads(resp.read().decode()), rem, used, None
    except urllib.error.HTTPError as exc:
        return None, exc.headers.get("x-requests-remaining"), None, "HTTP %d" % exc.code
    except Exception as exc:
        return None, None, None, str(exc)


def scheduled_starts_for_date(date):
    """Distinct scheduled first-pitch times (UTC) for `date`, decoded from
    the Kalshi event tickers in the settlement store (identity-parsed)."""
    starts = set()
    for p in sorted(glob.glob(os.path.join(SETTLEMENTS, "*.jsonl*"))):
        opener = gzip.open if p.endswith(".gz") else open
        with opener(p, "rt") as fh:
            for line in fh:
                if not line.strip():
                    continue
                r = json.loads(line)
                t = r.get("marketTicker") or ""
                if "-" not in t:
                    continue
                ident = parse_event_ticker(t.rsplit("-", 1)[0])
                if ident.get("status") == "RESOLVED" and ident["gameDate"] == date:
                    starts.add(ident["scheduledStartUtc"])
    return sorted(starts)


def snapshot_grid(starts, hours_before, step_minutes):
    if not starts:
        return []
    lo = min(starts) - timedelta(hours=hours_before)
    hi = max(starts)
    grid, t = [], lo
    while t <= hi:
        grid.append(t)
        t += timedelta(minutes=step_minutes)
    return grid


def load_manifest():
    if os.path.exists(MANIFEST):
        with open(MANIFEST) as fh:
            return json.load(fh)
    return {"program": "MLB-ALPHA-0002", "source": "the-odds-api /historical, bookmakers=pinnacle",
            "snapshots": {}}


def save_manifest(man):
    os.makedirs(OUT, exist_ok=True)
    tmp = MANIFEST + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(man, fh, indent=1, sort_keys=True)
    os.replace(tmp, MANIFEST)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dates", required=True)
    ap.add_argument("--markets", default="h2h,totals")
    ap.add_argument("--step-minutes", type=int, default=15)
    ap.add_argument("--hours-before", type=float, default=3.0)
    ap.add_argument("--max-credits", type=int, default=1600)
    ap.add_argument("--regions", default="eu")
    args = ap.parse_args()
    if not KEY:
        print("ODDS_API_KEY missing: cannot pull (research sandbox has no key)")
        return 2
    man = load_manifest()
    start_rem = None
    calls = 0
    for date in [d for d in args.dates.split(",") if d]:
        starts = scheduled_starts_for_date(date)
        grid = snapshot_grid(starts, args.hours_before, args.step_minutes)
        print("%s: %d games-starts, %d snapshots on grid" % (date, len(starts), len(grid)))
        nd = (datetime.strptime(date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
        for snap in grid:
            key = date + "/" + snap.strftime("%Y-%m-%dT%H:%M:%SZ")
            if key in man["snapshots"] and man["snapshots"][key].get("status") == "done":
                continue
            url = ("%s/historical/sports/%s/odds?apiKey=%s&regions=%s&bookmakers=pinnacle"
                   "&markets=%s&oddsFormat=decimal&commenceTimeFrom=%sT00:00:00Z"
                   "&commenceTimeTo=%sT06:00:00Z&date=%s"
                   % (BASE_URL, SPORT, KEY, args.regions, args.markets, date, nd,
                      snap.strftime("%Y-%m-%dT%H:%M:%SZ")))
            data, rem, used, err = api_get(url)
            calls += 1
            rec = {"date": date, "requestedSnapshot": snap.strftime("%Y-%m-%dT%H:%M:%SZ"),
                   "creditsRemaining": rem, "fetchedAt": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")}
            if data is None:
                rec.update({"status": "error", "error": err})
            else:
                rec.update({"status": "done",
                            "actualSnapshot": data.get("timestamp"),
                            "previousSnapshot": data.get("previous_timestamp"),
                            "nextSnapshot": data.get("next_timestamp"),
                            "games": len(data.get("data") or [])})
                path = os.path.join(OUT, date, snap.strftime("%Y-%m-%dT%H%M%SZ") + ".json.gz")
                os.makedirs(os.path.dirname(path), exist_ok=True)
                if not os.path.exists(path):                    # write-once
                    with gzip.open(path, "wt") as fh:
                        json.dump(data, fh, sort_keys=True)
            man["snapshots"][key] = rec
            if rem is not None:
                try:
                    remi = int(rem)
                    if start_rem is None:
                        start_rem = remi + 0  # first observed counter
                    man["creditsRemainingLast"] = remi
                    spent = (start_rem - remi) if start_rem is not None else 0
                    if spent >= args.max_credits:
                        print("credit cap reached (%d spent); stopping" % spent)
                        save_manifest(man)
                        return 0
                except ValueError:
                    pass
            if calls % 10 == 0:
                save_manifest(man)
            time.sleep(0.5)
    save_manifest(man)
    print("done: %d calls; credits remaining %s" % (calls, man.get("creditsRemainingLast")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
