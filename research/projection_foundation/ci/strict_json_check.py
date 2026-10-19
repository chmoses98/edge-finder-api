#!/usr/bin/env python3
"""Fail if any JSON file about to be published contains NaN / Infinity (invalid JSON: a browser's JSON.parse rejects the whole file).

usage: strict_json_check.py <archive dir>

Checks the files SIFT reads: projections/*/latest.json, summary/*/latest.json and scorecards/*/{daily,weekly,season}.json. Older
timestamped copies are immutable history (the archive is additions-only) and are not rewritten, so they are not checked.
"""
import glob
import json
import sys


def refuse(token):
    raise ValueError(f"non-finite token {token}")


def check(root: str) -> list[str]:
    bad = []
    for pattern in ("projections/*/latest.json", "summary/*/latest.json", "scorecards/*/daily.json", "scorecards/*/weekly.json",
                    "scorecards/*/season.json"):
        for f in sorted(glob.glob(f"{root}/{pattern}")):
            try:
                with open(f) as fh:
                    json.load(fh, parse_constant=refuse)
            except ValueError as e:
                bad.append(f"{f}: {e}")
    return bad


if __name__ == "__main__":
    problems = check(sys.argv[1])
    if problems:
        print("\n".join(problems))
        sys.exit(1)
    print("published JSON is strict")
