"""Cheap dormancy gate for the scheduled workflow (stdlib only, no install, ~seconds).

    python -m mlb.live.gate [--github-output FILE] [--horizon-hours 20]

Prints and writes `work=true|false`: true when the MLB schedule has any playable game that started in the last 30 hours (results /
settlement still due) or starts within the projection window. An off-season / all-star / off-day schedule is `work=false`
and the heavy job is skipped (successful no-op). A schedule fetch that fails fails OPEN (work=true) so a flaky gate can
never silently stop collection; the heavy job then fails visibly if the API really is down."""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone

from . import statsapi as api


def decide(games: list[dict], now: datetime, horizon_hours: float = 20.0) -> tuple[bool, str]:
    lo, hi = now - timedelta(hours=30), now + timedelta(hours=horizon_hours + 6)
    live = [g for g in games if g["detailed"] not in api.NOT_PLAYED and lo <= api.parse_ts(g["start_utc"]) <= hi]
    if live:
        nxt = min((g["start_utc"] for g in live if api.parse_ts(g["start_utc"]) > now), default=None)
        return True, f"{len(live)} game(s) in the window; next start {nxt}"
    return False, "no playable MLB game in the window (offseason / off-day): nothing to do"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--github-output", default=None)
    ap.add_argument("--horizon-hours", type=float, default=20.0)
    a = ap.parse_args(argv)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    games = []
    try:
        for d in api.capture_dates(now, ahead_hours=a.horizon_hours + 6):
            games += api.parse_schedule(api.http_json(api.SCHEDULE_URL.format(date=d))[0])
        work, why = decide(games, now, a.horizon_hours)
    except Exception as e:  # noqa: BLE001
        work, why = True, f"schedule fetch failed ({type(e).__name__}: {e}); failing open"
    print(f"work={'true' if work else 'false'}: {why}")
    if a.github_output:
        with open(a.github_output, "a") as f:
            f.write(f"work={'true' if work else 'false'}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
