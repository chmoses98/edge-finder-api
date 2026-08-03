#!/usr/bin/env python3
"""
scripts/edgelab/generate_postmortem.py
==========================================
CLI entry point: build the daily postmortem (Canonical Placed-Bet Ledger
milestone, requirement 14) from the canonical placed-bet ledger --
data/edgelab/bets/bets.jsonl -- NEVER from the recommendation list or
chat memory. Writes data/edgelab/reports/<date>_postmortem.json and
data/edgelab/reports/<date>_postmortem.md.

Usage:
    python3 scripts/edgelab/generate_postmortem.py [--date YYYY-MM-DD]
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab import canonical_era, storage
from lib.edgelab.bankroll import compute_bankroll_summary
from lib.edgelab.reports import build_postmortem, render_postmortem_markdown


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=None)
    parser.add_argument(
        "--include-legacy", action="store_true",
        help="Include pre-canonical-era (before %s) bets in this postmortem's bet list and bankroll "
             "figures. Never the default -- an official postmortem only ever counts canonical-era "
             "bets unless this is explicitly passed." % canonical_era.CANONICAL_ERA_START_DATE,
    )
    args = parser.parse_args()
    date = args.date or datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")

    all_bets = list(storage.read_records(storage.singleton_path("bets", "bets.jsonl")))
    bets = all_bets if args.include_legacy else canonical_era.canonical_era_bets(all_bets)
    transactions = list(storage.read_records(storage.singleton_path("bankroll", "transactions.jsonl")))
    bankroll_summary = compute_bankroll_summary(transactions, bets) if transactions or bets else None

    report = build_postmortem(date, bets, bankroll_summary=bankroll_summary)
    report["canonicalEraStartDate"] = canonical_era.CANONICAL_ERA_START_DATE
    report["legacyIncluded"] = args.include_legacy
    markdown = render_postmortem_markdown(report)

    reports_dir = os.path.join("data", "edgelab", "reports")
    os.makedirs(reports_dir, exist_ok=True)
    json_path = os.path.join(reports_dir, f"{date}_postmortem.json")
    md_path = os.path.join(reports_dir, f"{date}_postmortem.md")

    with open(json_path, "w") as f:
        json.dump(report, f, indent=2, sort_keys=True)
    with open(md_path, "w") as f:
        f.write(markdown)

    print(f"[generate_postmortem] date={date} betsPlaced={report['betsPlaced']} -> {json_path}, {md_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
