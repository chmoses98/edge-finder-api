#!/usr/bin/env python3
"""
scripts/print_price_check_table.py
=======================================
Reads a kalshi_price_check.json file (the full, already-filtered/
validated records written by scripts/check_kalshi_prices.py) and prints
a single mobile-friendly Markdown table of every market to stdout --
used by .github/workflows/kalshi-price-check.yml so the SAME rendering
appears in both the workflow log (piped through `tee`) and the GitHub
Actions job summary, without a fragile inline Python heredoc.

This script is a thin, impure CLI wrapper only -- all table-building
logic (sorting, family/scope/team display names, threshold formatting,
row truncation) lives in the pure, independently-tested
lib.kalshi_price_check.format_mobile_markdown_table(). It does not
fetch, price, filter, classify, or validate anything itself, and never
touches data/slate.json or any production pipeline module -- same
safety isolation as scripts/check_kalshi_prices.py (see
tests/test_check_kalshi_prices_safety_isolation.py).

Usage:
    python3 scripts/print_price_check_table.py kalshi_price_check.json [max_rows]
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from lib.kalshi_price_check import format_mobile_markdown_table, MOBILE_TABLE_MAX_ROWS_DEFAULT


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if not argv:
        print("usage: print_price_check_table.py <kalshi_price_check.json> [max_rows]", file=sys.stderr)
        return 1
    with open(argv[0]) as f:
        records = json.load(f)
    max_rows = int(argv[1]) if len(argv) > 1 else MOBILE_TABLE_MAX_ROWS_DEFAULT
    print(format_mobile_markdown_table(records, max_rows=max_rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
