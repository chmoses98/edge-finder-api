#!/usr/bin/env python3
"""
scripts/edgelab/generate_daily_report.py
=============================================
CLI entry point: aggregate one date's EdgeLab partitions into a
human-readable daily report (data/edgelab/reports/<date>.md) plus a
machine-readable version (data/edgelab/reports/<date>.json) and a
calibration export (data/edgelab/reports/<date>_calibration.jsonl).

Usage:
    python3 scripts/edgelab/generate_daily_report.py [--date YYYY-MM-DD]
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab import ids, storage
from lib.edgelab.reports import build_calibration_rows, build_daily_report, render_markdown


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=None)
    args = parser.parse_args()
    date = args.date or datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")

    games = list(storage.read_records(storage.partition_path("games", date)))
    markets = list(storage.read_records(storage.partition_path("markets", date)))
    observations = list(storage.read_records(storage.partition_path("observations", date, compressed=True)))
    recommendations = list(storage.read_records(storage.partition_path("recommendations", date)))
    clv_quotes = list(storage.read_records(storage.partition_path("clv_quotes", date)))
    settlements = list(storage.read_records(storage.partition_path("settlements", date)))
    research_runs = list(storage.read_records(storage.partition_path("research_runs", date)))
    bets = [
        b for b in storage.read_records(storage.singleton_path("bets", "bets.jsonl"))
        if (b.get("entryTimestamp") or "")[:10] == date
    ]

    report = build_daily_report(date, games, markets, observations, recommendations, clv_quotes, settlements, bets, research_runs)
    markdown = render_markdown(report)
    calibration_rows = build_calibration_rows(recommendations, settlements)

    reports_dir = os.path.join("data", "edgelab", "reports")
    os.makedirs(reports_dir, exist_ok=True)
    json_path = os.path.join(reports_dir, f"{date}.json")
    md_path = os.path.join(reports_dir, f"{date}.md")
    calibration_path = os.path.join(reports_dir, f"{date}_calibration.jsonl")

    with open(json_path, "w") as f:
        json.dump(report, f, indent=2, sort_keys=True)
    with open(md_path, "w") as f:
        f.write(markdown)
    with open(calibration_path, "w") as f:
        for row in calibration_rows:
            f.write(json.dumps(row, sort_keys=True) + "\n")

    print(f"[generate_daily_report] date={date} -> {json_path}, {md_path}, {calibration_path} ({len(calibration_rows)} calibration rows)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
