#!/usr/bin/env python3
"""
scripts/print_price_check_summary.py
=========================================
Reads a kalshi_price_check_metadata.json file (written by
scripts/check_kalshi_prices.py's --metadata-output) and prints its
Markdown job-summary rendering to stdout. Used by
.github/workflows/kalshi-price-check.yml so the workflow never needs a
fragile inline Python heredoc nested inside bash conditionals.

Usage:
    python3 scripts/print_price_check_summary.py kalshi_price_check_metadata.json
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from lib.kalshi_price_check import format_job_summary_markdown


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if not argv:
        print("usage: print_price_check_summary.py <metadata.json>", file=sys.stderr)
        return 1
    with open(argv[0]) as f:
        metadata = json.load(f)
    print(format_job_summary_markdown(metadata))
    return 0


if __name__ == "__main__":
    sys.exit(main())
