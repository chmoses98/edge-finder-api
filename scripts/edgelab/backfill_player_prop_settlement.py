#!/usr/bin/env python3
"""
scripts/edgelab/backfill_player_prop_settlement.py
========================================================
CLI entry point: idempotently reprocess one date or a date range's
player-prop (and, incidentally, every other family's) settlement
(GitHub issue #43).

Reuses scripts/edgelab/settle_markets.py's settle_date() directly --
this is NOT a second settlement implementation. A player-prop market's
settlement is derived fresh from its own marketTicker/eventTicker/title
every time (see lib/edgelab/player_prop_settlement.py's module
docstring), so a date ingested long before issue #43 shipped is
settleable immediately -- no separate re-ingestion step is required,
only this settlement rerun.

Every run (including --dry-run) is idempotent: settle_date() writes
Settlement/PlacedBet records via storage.upsert_records keyed by
settlementId/betId, so rerunning a date already settled with identical
final MLB stats reproduces the same records (never a duplicate), and
rerunning after a corrected final stat safely updates the existing
Settlement record and every matching bet in place.

Usage:
    python3 scripts/edgelab/backfill_player_prop_settlement.py --date 2026-08-02 --dry-run
    python3 scripts/edgelab/backfill_player_prop_settlement.py --start-date 2026-08-01 --end-date 2026-08-03
"""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from scripts.edgelab.settle_markets import PLAYER_PROP_FAMILIES, settle_date


def _date_range(start_date, end_date):
    start = datetime.strptime(start_date, "%Y-%m-%d").date()
    end = datetime.strptime(end_date, "%Y-%m-%d").date()
    if end < start:
        raise ValueError(f"--end-date {end_date} is before --start-date {start_date}")
    current = start
    while current <= end:
        yield current.strftime("%Y-%m-%d")
        current += timedelta(days=1)


def _merge_family_counts(total_by_family, date_by_family):
    for family, counts in date_by_family.items():
        totals = total_by_family.setdefault(
            family, {"observed": 0, "settled": 0, "void": 0, "unresolved": 0, "betsUpdated": 0},
        )
        for key, value in counts.items():
            totals[key] += value


def _merge_unresolved_reasons(total_reasons, date_reasons):
    for family, reasons in date_reasons.items():
        totals = total_reasons.setdefault(family, {})
        for reason, count in reasons.items():
            totals[reason] = totals.get(reason, 0) + count


def run_backfill(dates, dry_run=False):
    """
    Pure orchestration over settle_date() for a list of dates. Returns
    a combined summary: {"dates", "dryRun", "byFamily" (player-prop
    families only), "unresolvedReasonsByFamily", "perDate": [...each
    date's own settle_date() summary...]}.
    """
    by_family = {}
    unresolved_reasons_by_family = {}
    per_date = []

    for date in dates:
        summary = settle_date(date, dry_run=dry_run)
        per_date.append(summary)
        player_prop_family_counts = {
            family: counts for family, counts in summary["byFamily"].items()
            if family in PLAYER_PROP_FAMILIES
        }
        _merge_family_counts(by_family, player_prop_family_counts)
        _merge_unresolved_reasons(
            unresolved_reasons_by_family,
            {f: r for f, r in summary["unresolvedReasonsByFamily"].items() if f in PLAYER_PROP_FAMILIES},
        )

    return {
        "dates": dates,
        "dryRun": dry_run,
        "byFamily": by_family,
        "unresolvedReasonsByFamily": unresolved_reasons_by_family,
        "perDate": per_date,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--date", default=None, help="Single date YYYY-MM-DD to reprocess")
    parser.add_argument("--start-date", default=None, help="First date YYYY-MM-DD of an inclusive range")
    parser.add_argument("--end-date", default=None, help="Last date YYYY-MM-DD of an inclusive range")
    parser.add_argument("--dry-run", action="store_true", help="Compute and print counts only -- writes nothing")
    args = parser.parse_args()

    if args.date:
        dates = [args.date]
    elif args.start_date and args.end_date:
        dates = list(_date_range(args.start_date, args.end_date))
    else:
        parser.error("must pass either --date, or both --start-date and --end-date")
        return 2

    summary = run_backfill(dates, dry_run=args.dry_run)

    mode = "DRY-RUN (no writes)" if args.dry_run else "LIVE (writing settlements/bets)"
    print(f"[backfill_player_prop_settlement] {mode} dates={dates}")
    print("Player-prop family counts (observed/settled/void/unresolved/betsUpdated):")
    for family in sorted(PLAYER_PROP_FAMILIES):
        counts = summary["byFamily"].get(family, {"observed": 0, "settled": 0, "void": 0, "unresolved": 0, "betsUpdated": 0})
        print(f"  {family:28s} {json.dumps(counts)}")
    print("Unresolved-reason breakdown:")
    for family in sorted(summary["unresolvedReasonsByFamily"]):
        print(f"  {family}: {json.dumps(summary['unresolvedReasonsByFamily'][family])}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
