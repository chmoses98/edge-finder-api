#!/usr/bin/env python3
"""
scripts/edgelab/generate_rolling_report.py
===============================================
CLI entry point: rolling last-N-settled-canonical-bets performance
report (Tier/confidence breakdown, market-family breakdown, model/
manual-fair-probability calibration, CLV coverage) --
data/edgelab/reports/rolling_last_<N>.json plus a human-readable
data/edgelab/reports/rolling_last_<N>.md.

Reads ONLY the canonical placed-bet ledger
(data/edgelab/bets/bets.jsonl) -- never a legacy ledger, never the
recommendation list. See lib.edgelab.reports.build_rolling_window_report
for the full methodology.

Usage:
    python3 scripts/edgelab/generate_rolling_report.py [--window 30] [--include-legacy]
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab import storage
from lib.edgelab.reports import ROLLING_WINDOW_SIZE, build_rolling_window_report, render_rolling_window_markdown


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--window", type=int, default=ROLLING_WINDOW_SIZE)
    parser.add_argument("--include-legacy", action="store_true",
                         help="Include pre-canonical-era bets (never the default -- see lib.edgelab.canonical_era).")
    args = parser.parse_args()

    bets = list(storage.read_records(storage.singleton_path("bets", "bets.jsonl")))
    report = build_rolling_window_report(bets, window_size=args.window, include_legacy=args.include_legacy)
    markdown = render_rolling_window_markdown(report)

    reports_dir = os.path.join("data", "edgelab", "reports")
    os.makedirs(reports_dir, exist_ok=True)
    json_path = os.path.join(reports_dir, f"rolling_last_{args.window}.json")
    md_path = os.path.join(reports_dir, f"rolling_last_{args.window}.md")

    with open(json_path, "w") as f:
        json.dump(report, f, indent=2, sort_keys=True)
    with open(md_path, "w") as f:
        f.write(markdown)

    print(
        f"[generate_rolling_report] window={report['windowActual']}/{report['windowRequested']} "
        f"({report['windowSampleStatus']}) -> {json_path}, {md_path}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
