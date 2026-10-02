#!/usr/bin/env python3
"""
scripts/ci/mlb_schedule_probe.py
================================
Collects MLB schedule evidence for scripts/ci/production_health_gate.py,
the same way scripts/ci/router_divergence_probe.py collects router evidence:
the gate itself stays network-free and reads this file.

WHY. The gate's freshness assertions counted CALENDAR days. Any 3-10 day
stretch with no MLB games at all -- the days after the World Series, a gap
between postseason rounds, the All-Star break -- made the slate, settlement,
recommendation and model-evaluation partitions look "stale" and turned the
gate CRITICAL every morning until the 10-day "inactive pipeline" escape
kicked in. With this evidence the gate skips VERIFIED off-days (dates the
schedule says had no playable game) and still demands everything up to the
last real game day.

OUTPUT (one JSON object):
  {"source", "gameTypes", "fetchedAt", "start", "end",
   "playableGamesByDate": {"YYYY-MM-DD": int, ...}}      -- success
  {"fetchError": "..."}                                    -- anything else

A fetch failure is recorded, never raised: the gate reads a missing or
failed probe as "no verified off-days", i.e. exactly its previous,
calendar-day behaviour (fail closed).
"""

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from lib.edgelab import mlb_schedule  # noqa: E402
from lib.edgelab import slate_day_contract as day_contract  # noqa: E402

#: Must cover production_health_gate.INACTIVE_PIPELINE_DAYS plus the largest
#: freshness limit, so every date the gate could walk back over is covered.
DEFAULT_LOOKBACK_DAYS = 14


def build_evidence(response, start, end, fetched_at):
    """Pure: the probe's JSON payload for one schedule response."""
    counts = day_contract.playable_games_by_date(response, start, end)
    if counts is None:
        return {"fetchError": "schedule fetch failed or response malformed",
                "start": start, "end": end, "fetchedAt": fetched_at}
    return {
        "source": day_contract.SCHEDULE_SOURCE,
        "gameTypes": list(day_contract.SCHEDULE_GAME_TYPES),
        "fetchedAt": fetched_at,
        "start": start,
        "end": end,
        "playableGamesByDate": counts,
    }


def main(argv=None, now=None, fetch=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", required=True)
    parser.add_argument("--lookback-days", type=int, default=DEFAULT_LOOKBACK_DAYS)
    args = parser.parse_args(argv)

    now = now or datetime.now(timezone.utc)
    today_et = now.astimezone(ZoneInfo("America/New_York")).date()
    start = (today_et - timedelta(days=args.lookback_days)).isoformat()
    end = today_et.isoformat()
    fetch = fetch or mlb_schedule.fetch_schedule_range_all_game_types
    evidence = build_evidence(fetch(start, end), start, end,
                              now.strftime("%Y-%m-%dT%H:%M:%SZ"))
    with open(args.out, "w") as f:
        json.dump(evidence, f, indent=2, sort_keys=True)
    if "fetchError" in evidence:
        print("schedule evidence UNAVAILABLE (%s) -- the gate will not skip any day"
              % evidence["fetchError"])
    else:
        off = sorted(d for d, n in evidence["playableGamesByDate"].items() if n == 0)
        print("schedule evidence %s..%s: %d day(s) with no playable MLB game%s"
              % (start, end, len(off), (": " + ", ".join(off)) if off else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
