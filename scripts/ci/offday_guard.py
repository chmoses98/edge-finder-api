#!/usr/bin/env python3
"""
scripts/ci/offday_guard.py
==========================
Is DATE a schedule-verified MLB off-day, according to the record Fetch Slate
Data's slate-day gate wrote? Used by workflows triggered on fetch-slate's
completion (discover-kalshi-mlb-markets.yml): since the slate-day gate, a
verified off-day concludes SUCCESS with the fetch job skipped, and a
dependent workflow must not then run a market discovery for a date with no
games (and turn red, or write data/pipeline/<date>/ for a day with no slate).

Off-day ONLY when data/fetch_status.json has
  status == "NO_GAMES_SCHEDULED", requestedDate == DATE, and
  scheduleEvidence.status == "NO_GAMES_SCHEDULED" for DATE.
A missing/unreadable file, another date, another status, or absent schedule
evidence is NOT an off-day: the caller runs as before (fail open to doing
the work, never to skipping it).

Writes `off_day=true|false` to $GITHUB_OUTPUT; always exits 0 (it decides
nothing about success -- the work it gates does).
"""
import json
import os
import sys

FETCH_STATUS_PATH = os.path.join("data", "fetch_status.json")


def is_verified_off_day(fetch_status, date):
    """Pure."""
    if not isinstance(fetch_status, dict):
        return False
    evidence = fetch_status.get("scheduleEvidence")
    return (
        fetch_status.get("status") == "NO_GAMES_SCHEDULED"
        and fetch_status.get("requestedDate") == date
        and isinstance(evidence, dict)
        and evidence.get("status") == "NO_GAMES_SCHEDULED"
        and evidence.get("date") == date
    )


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    date = argv[0] if argv else ""
    try:
        with open(FETCH_STATUS_PATH) as f:
            status = json.load(f)
    except (OSError, ValueError):
        status = None
    off_day = bool(date) and is_verified_off_day(status, date)
    if off_day:
        print(f"{date}: schedule-verified MLB off-day (data/fetch_status.json NO_GAMES_SCHEDULED) "
              f"-- nothing to discover; this run concludes success.")
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            with open(summary, "a") as f:
                f.write(f"## Kalshi MLB discovery: NOT_APPLICABLE\n\n{date} is a schedule-verified "
                        f"MLB off-day (no games scheduled); discovery skipped.\n")
    else:
        print(f"{date}: not a verified off-day -- discovery runs.")
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a") as f:
            f.write(f"off_day={'true' if off_day else 'false'}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
