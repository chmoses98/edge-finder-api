#!/usr/bin/env python3
"""
scripts/parse_advanced_filters.py
======================================
Parses the `advanced_filters_json` workflow_dispatch input -- a small
JSON object carrying the less-common check_kalshi_prices.py filters
(date, outcome, participant, ticker, event_ticker, series_ticker,
games, exclude_started) that were consolidated into a single input
specifically because GitHub Actions hard-caps workflow_dispatch at 10
top-level inputs (see docs/KALSHI_PRICE_CHECKER.md and this branch's
PR description for the full root-cause investigation) -- into one CLI
flag/value pair per line (or a bare flag for a boolean key -- see
BOOLEAN_FLAG_KEYS), safe for a workflow step to read into a bash array
with a `while read` loop.

Prints nothing and exits 0 for a blank/empty input (no advanced
filters requested). Exits non-zero with a clear stderr message on
invalid JSON, a non-object JSON value, or an unrecognized key --
never silently drops or ignores a typo'd filter name.

Usage:
    python3 scripts/parse_advanced_filters.py '{"date": "2026-07-30"}'
    python3 scripts/parse_advanced_filters.py '{"games": "PIT@CIN,NYY@BOS", "exclude_started": true}'
"""
import json
import sys

ALLOWED_KEYS = {
    "date": "--date",
    "outcome": "--outcome",
    "participant": "--participant",
    "ticker": "--ticker",
    "event_ticker": "--event-ticker",
    "series_ticker": "--series-ticker",
    "games": "--games",
}

# Standalone Kalshi price-check usability mission: selected-game and
# not-started-only filtering, added the same way every other rare
# filter already was -- consolidated into advanced_filters_json rather
# than a new top-level workflow_dispatch input, since GitHub Actions
# hard-caps workflow_dispatch at 10 top-level inputs and this workflow
# is already at that limit (see kalshi-price-check.yml's own header
# comment and tests/test_kalshi_price_check_workflow.py). Boolean flags
# take no value -- present it as `true` (JSON boolean) to enable, or
# omit the key entirely.
BOOLEAN_FLAG_KEYS = {
    "exclude_started": "--exclude-started",
}


def parse(raw):
    """
    Pure. Returns a list of CLI args for every non-empty allowed key
    present in `raw` (a JSON object string) -- (flag, value, flag,
    value, ...) for ALLOWED_KEYS, and a bare flag (no value) for each
    truthy BOOLEAN_FLAG_KEYS entry. Raises ValueError with a
    human-readable message for invalid JSON, a non-object top level, or
    any unrecognized key -- the JSON blob is never partially applied
    while silently ignoring the parts it didn't understand.
    """
    raw = (raw or "").strip()
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"advanced_filters_json is not valid JSON: {e}")
    if not isinstance(data, dict):
        raise ValueError(
            "advanced_filters_json must be a JSON object, "
            'e.g. {"date": "2026-07-30", "ticker": "KXMLBF5-..."}'
        )
    recognized = set(ALLOWED_KEYS) | set(BOOLEAN_FLAG_KEYS)
    unknown = sorted(set(data) - recognized)
    if unknown:
        raise ValueError(
            f"advanced_filters_json has unrecognized key(s): {unknown}. "
            f"Allowed keys: {sorted(recognized)}"
        )
    args = []
    for key, flag in ALLOWED_KEYS.items():
        value = data.get(key)
        if value:
            args.append(flag)
            args.append(str(value))
    for key, flag in BOOLEAN_FLAG_KEYS.items():
        if data.get(key):
            args.append(flag)
    return args


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    raw = argv[0] if argv else ""
    try:
        args = parse(raw)
    except ValueError as e:
        print(f"::error::{e}", file=sys.stderr)
        return 1
    for arg in args:
        print(arg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
