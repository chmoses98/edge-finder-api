#!/usr/bin/env python3
"""Read-only: how many archived hitter observations have a point-in-time-valid prospective projection snapshot?

Applies lib.edgelab.hitter_snapshot_join.select_pit_snapshot to every hitter market observation in the range
against data/edgelab/hitter_projection_snapshots. Persists NOTHING and creates no ModelEvaluation: it measures
what a PIT-valid join could cover and why the rest cannot (no snapshot existed, snapshot generated after the
observation, not PROJECTED, ...). Unjoinable observations stay unjoined; no prediction is back-filled.

Pregame guard: the exact contract ticker encodes the scheduled first pitch in US/Eastern
(e.g. KXMLBHIT-26AUG191235DETPIT-... = 2026-08-19 12:35 ET). A snapshot generated after that instant is rejected
(SNAPSHOT_GENERATED_AFTER_GAME_START); a ticker whose start cannot be parsed is quarantined and counted
in pregameCrossCheck.tickerStartUnparseable. The violations counter re-asserts the guard and must be 0.

Usage:
  python3 scripts/edgelab/audit_hitter_snapshot_join.py --start-date 2026-08-19 --end-date 2026-10-31 --out artifacts/hitter_snapshot_join.json
"""
import argparse
import glob
import gzip
import json
import os
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab.hitter_snapshot_join import parse_utc, select_pit_snapshot, ticker_event_start_utc

HITTER_FAMILIES = ("hitter_hits", "hitter_total_bases", "hitter_rbis", "hitter_hits_runs_rbis")
DATA = "data/edgelab"


def _rows(path):
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt") as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


def _partition_date(path):
    return os.path.basename(path)[:10]


def load_snapshots(root=DATA):
    by_ticker = defaultdict(list)
    for path in sorted(glob.glob(os.path.join(root, "hitter_projection_snapshots", "*.jsonl*"))):
        for r in _rows(path):
            if r.get("marketTicker"):
                by_ticker[r["marketTicker"]].append(r)
    return by_ticker


def audit(start_date, end_date, root=DATA, snapshots=None):
    snapshots = snapshots if snapshots is not None else load_snapshots(root)
    reasons, joined_rows = Counter(), Counter()
    fam_rows, fam_joined = Counter(), Counter()
    tickers, joined_tickers = set(), set()
    per_date = defaultdict(lambda: Counter())
    ages, violations, no_start = [], 0, 0
    for path in sorted(glob.glob(os.path.join(root, "observations", "*.jsonl*"))):
        d = _partition_date(path)
        if not (start_date <= d <= end_date):
            continue
        for o in _rows(path):
            fam = o.get("marketFamily")
            if fam not in HITTER_FAMILIES:
                continue
            tk = o.get("marketTicker")
            tickers.add(tk)
            fam_rows[fam] += 1
            per_date[d]["rows"] += 1
            start = ticker_event_start_utc(tk)
            if start is None:
                no_start += 1
            # No verified pregame boundary means no eligible PIT join. Keep the row in the denominator.
            if start is None:
                reasons["TICKER_START_UNPARSEABLE"] += 1
                continue
            captured = parse_utc(o.get("capturedAt"))
            if captured is None:
                reasons["OBSERVATION_TIMESTAMP_INVALID"] += 1
                continue
            if captured > start:
                reasons["OBSERVATION_AFTER_TICKER_START"] += 1
                continue
            snap, reason, diag = select_pit_snapshot(snapshots.get(tk, []), o, game_start=start)
            if snap is None:
                reasons[reason] += 1
                continue
            joined_rows["rows"] += 1
            fam_joined[fam] += 1
            per_date[d]["joined"] += 1
            joined_tickers.add(tk)
            ages.append(diag["snapshotAgeSeconds"])
            if start is not None and parse_utc(snap["projectionGeneratedAt"]) > start:
                violations += 1  # unreachable by construction (guard above); kept as an independent assertion
    ages.sort()
    q = lambda p: round(ages[int(p * (len(ages) - 1))], 1) if ages else None
    return {
        "range": [start_date, end_date],
        "hitterObservationRows": sum(fam_rows.values()),
        "uniqueHitterTickers": len(tickers),
        "pitJoinedRows": joined_rows["rows"],
        "pitJoinedTickers": len(joined_tickers),
        "unjoinedReasonsByRow": dict(reasons),
        "rowsByFamily": dict(fam_rows),
        "joinedByFamily": dict(fam_joined),
        "snapshotAgeSecondsQuantiles": {"p50": q(0.5), "p90": q(0.9), "max": q(1.0)},
        "pregameCrossCheck": {"selectedSnapshotsGeneratedAfterTickerStart": violations, "tickerStartUnparseable": no_start},
        "perDate": {d: dict(c) for d, c in sorted(per_date.items())},
        "note": "Measurement only. Nothing persisted, no ModelEvaluation created, no historical prediction back-filled.",
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start-date", required=True)
    ap.add_argument("--end-date", required=True)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    result = audit(args.start_date, args.end_date)
    text = json.dumps(result, indent=1, sort_keys=True)
    if args.out:
        os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
        with open(args.out, "w") as f:
            f.write(text + "\n")
    print(json.dumps({k: v for k, v in result.items() if k != "perDate"}, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
