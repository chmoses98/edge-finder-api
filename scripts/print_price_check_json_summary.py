#!/usr/bin/env python3
"""
scripts/print_price_check_json_summary.py
===============================================
Reads a kalshi_price_check.json file (the full, already-filtered/
validated records written by scripts/check_kalshi_prices.py) and prints
a collapsible Markdown <details> block containing the raw JSON, for
.github/workflows/kalshi-price-check.yml to append to the job summary
and workflow log -- a second, zero-download way to reach the exact
primary output that the kalshi-price-check-json artifact ZIP already
contains, per the "artifact usability" investigation (GitHub Actions
always ZIPs uploaded artifacts; this does not fight that, it just adds
a lightweight direct-consumption path alongside it).

This script is a thin, impure CLI wrapper only -- all rendering logic
(size-capping, collapsible-block formatting) lives in the pure,
independently-tested lib.kalshi_price_check.format_json_summary_block().
It does not fetch, price, filter, classify, or validate anything itself,
and never touches data/slate.json or any production pipeline module --
same safety isolation as scripts/check_kalshi_prices.py (see
tests/test_check_kalshi_prices_safety_isolation.py).

Usage:
    python3 scripts/print_price_check_json_summary.py kalshi_price_check.json
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from lib.kalshi_price_check import format_json_summary_block


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if not argv:
        print("usage: print_price_check_json_summary.py <kalshi_price_check.json>", file=sys.stderr)
        return 1
    with open(argv[0]) as f:
        records = json.load(f)
    print(format_json_summary_block(records))
    return 0


if __name__ == "__main__":
    sys.exit(main())
