#!/usr/bin/env python3
"""
scripts/edgelab/build_archive_completeness_audit.py
===================================================
PHASE 8: does discovered == archived + explicitly excluded?

Every downstream number -- coverage, calibration, CLV -- is computed over
the archive and silently assumes the archive holds everything that was
discoverable. Nothing checked that assumption.

WHAT THE RECONCILIATION IS

For each capture the identity that must hold is:

    snapshot total_markets
      == observationsBuilt
       + marketsExcluded            (registry gate, counted)
       + rows with no ticker        (currently uncounted -- see below)

    observationsBuilt
      == observationsWritten
       + observationsDroppedNoChange     (unchanged tick, counted)
       + observationsSkippedDuplicate    (re-ingest, counted)

A gap on either line is unexplained loss. The point of writing it out is
that both lines are made of numbers the pipeline ALREADY computes; nobody
had ever subtracted them.

WHAT THIS IS NOT

Deduplication is not loss. observationsDroppedNoChange is a tick whose
yesBid/yesAsk/noBid/noAsk/lastPrice/marketStatus were identical to the
last retained row for that ticker (lib/edgelab/market_universe.py
select_observations_for_retention), and dropping it is correct: the
archive is a change log, and the price at that moment is known from the
row it duplicates. Volume is deliberately NOT compared, so a market whose
volume moved while its quote held still is dropped -- correctly. Counting
those as missing markets manufactures a defect out of a working
mechanism, and this script does not do it.

THE DEFECT IT DOES FIND

api/kalshisearch.js records its own partial fetches -- `fetchFailures`,
`fetchFailureCount`, `priceFetchFailureCount` -- into every snapshot.
Nothing reads them. Not one Python file in this repository references any
of those keys. So a tick that fetched three of seventeen series is
ingested, written, and reported `status: success` with
`marketsExcluded: 0`, indistinguishable from a complete one.

The loss is not random. `fetchAllPages` breaks out of its page loop on a
non-ok response, and the series are fetched sequentially, so a rate limit
that bites partway through the list truncates whichever series come LAST
-- consistently the high-volume hitter prop families. Any research over
those families is therefore computed on a sample biased against exactly
the busy game-time moments when rate limits bite.

Usage:
    python3 scripts/edgelab/build_archive_completeness_audit.py [--days 14]
"""
import argparse
import collections
import datetime
import glob
import gzip
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

SCHEMA_VERSION = "archive_completeness_v2"
OUT_DIR = os.path.join("data", "edgelab", "reports")
SNAPSHOT_DIR = os.path.join("data", "kalshi_registry_snapshots")

# A series a peer snapshot saw this many markets in is substantial enough
# that its total absence is evidence of truncation rather than of a slate
# that happened to carry none.
PEER_SERIES_SIGNIFICANCE = 20


def _read_jsonl(path):
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


def load_snapshots(date):
    out = []
    for path in sorted(glob.glob(os.path.join(SNAPSHOT_DIR, "kalshi_search_%s_*.json" % date))):
        try:
            with open(path) as fh:
                out.append((os.path.basename(path), json.load(fh)))
        except (OSError, json.JSONDecodeError) as exc:
            out.append((os.path.basename(path), {"__unreadable__": str(exc)}))
    return out


def failed_series(snapshot):
    """Which series the snapshot itself says it failed to fetch."""
    names = []
    for failure in snapshot.get("fetchFailures") or []:
        url = failure.get("url") or ""
        if "series_ticker=" in url:
            names.append(url.split("series_ticker=")[-1].split("&")[0])
        else:
            names.append("__broad_discovery__")
    return names


def truncation_loss(snapshot, peers):
    """Markets a partial fetch lost, measured against complete peers.

    A lower bound: it can only see a series that vanished ENTIRELY, not one
    truncated partway through its pages.
    """
    counts = snapshot.get("series_counts") or {}
    peer_counts = [p.get("series_counts") or {} for p in peers]
    absent = []
    for series in set().union(*peer_counts) if peer_counts else ():
        peak = max(pc.get(series, 0) for pc in peer_counts)
        if peak >= PEER_SERIES_SIGNIFICANCE and not counts.get(series):
            absent.append({"seriesTicker": series, "peerPeakMarkets": peak})
    return sorted(absent, key=lambda row: -row["peerPeakMarkets"])


def reconcile_run(run_record):
    """The identity the pipeline's own counters must satisfy."""
    counts = run_record.get("counts") or {}
    built = counts.get("observationsBuilt")
    if built is None:
        return None
    accounted = (counts.get("observationsWritten", 0)
                 + counts.get("observationsDroppedNoChange", 0)
                 + counts.get("observationsSkippedDuplicate", 0))
    return {
        "startedAt": run_record.get("startedAt"),
        "status": run_record.get("status"),
        "observationsBuilt": built,
        "observationsWritten": counts.get("observationsWritten", 0),
        "droppedNoChange": counts.get("observationsDroppedNoChange", 0),
        "skippedDuplicate": counts.get("observationsSkippedDuplicate", 0),
        "marketsExcluded": counts.get("marketsExcluded", 0),
        # Must be zero. A non-zero value is a market that entered the
        # pipeline and left no trace of where it went.
        "unaccountedObservations": built - accounted,
    }


def audit_date(date):
    snapshots = load_snapshots(date)
    if not snapshots:
        return None

    complete = [s for _, s in snapshots
                if "__unreadable__" not in s and not (s.get("fetchFailureCount") or 0)]

    captures = []
    for name, snap in snapshots:
        if "__unreadable__" in snap:
            captures.append({"snapshot": name, "unreadable": snap["__unreadable__"]})
            continue
        failures = snap.get("fetchFailureCount") or 0
        peers = [p for p in complete if p is not snap]
        lost = truncation_loss(snap, peers) if failures else []
        captures.append({
            "snapshot": name,
            "fetchedAt": snap.get("fetched_at"),
            "totalMarkets": snap.get("total_markets"),
            "fetchFailureCount": failures,
            "priceFetchFailureCount": snap.get("priceFetchFailureCount") or 0,
            "failedSeries": failed_series(snap),
            "seriesAbsentVsPeers": lost,
            "lowerBoundMarketsLost": sum(row["peerPeakMarkets"] for row in lost),
            "isPartialFetch": bool(failures),
        })

    runs = []
    for suffix in (".jsonl.gz", ".jsonl"):
        path = os.path.join("data", "edgelab", "research_runs", "%s%s" % (date, suffix))
        if os.path.exists(path):
            runs = [r for r in (reconcile_run(row) for row in _read_jsonl(path)) if r]
            break

    partial = [c for c in captures if c.get("isPartialFetch")]
    return {
        "date": date,
        "captures": len(captures),
        "partialFetches": len(partial),
        "unreadableSnapshots": sum(1 for c in captures if "unreadable" in c),
        "lowerBoundMarketsLost": sum(c.get("lowerBoundMarketsLost", 0) for c in captures),
        "seriesTruncated": sorted({s for c in partial for s in c["failedSeries"]}),
        "ingestRuns": len(runs),
        "runsReportingSuccess": sum(1 for r in runs if r["status"] == "success"),
        "unaccountedObservations": sum(r["unaccountedObservations"] for r in runs),
        "byCapture": captures,
        "byRun": runs,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", default=None)
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--out-dir", default=OUT_DIR)
    args = ap.parse_args()

    today = args.date or datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    base = datetime.date.fromisoformat(today)

    days = []
    for offset in range(args.days):
        day = audit_date((base - datetime.timedelta(days=offset)).isoformat())
        if day:
            days.append(day)

    captures = sum(d["captures"] for d in days)
    partial = sum(d["partialFetches"] for d in days)
    series = collections.Counter()
    for d in days:
        for c in d["byCapture"]:
            series.update(c.get("failedSeries") or [])

    report = {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "window": {"days": args.days, "datesAudited": len(days),
                   "from": days[-1]["date"] if days else None,
                   "to": days[0]["date"] if days else None},
        "totals": {
            "captures": captures,
            "partialFetches": partial,
            "partialFetchPct": round(100.0 * partial / captures, 1) if captures else None,
            "lowerBoundMarketsLost": sum(d["lowerBoundMarketsLost"] for d in days),
            "unreadableSnapshots": sum(d["unreadableSnapshots"] for d in days),
            "ingestRuns": sum(d["ingestRuns"] for d in days),
            "runsReportingSuccess": sum(d["runsReportingSuccess"] for d in days),
            "unaccountedObservations": sum(d["unaccountedObservations"] for d in days),
            "truncatedSeriesFrequency": dict(series.most_common()),
        },
        "byDate": days,
        "findings": {
            "fetchFailuresAreRecordedButUnread": (
                "api/kalshisearch.js writes fetchFailures/fetchFailureCount/"
                "priceFetchFailureCount into every snapshot. No Python file in this "
                "repository reads any of them, so a partial capture is ingested and "
                "reported status=success, indistinguishable from a complete one."),
            "lossIsSystematicNotRandom": (
                "fetchAllPages breaks out of its page loop on a non-ok response, and "
                "the 17 series are fetched sequentially, so a rate limit truncates "
                "whichever series come last -- consistently the high-volume hitter "
                "prop families. Research over those families is computed on a sample "
                "biased against the busy game-time moments when rate limits bite."),
            "deduplicationIsNotLoss": (
                "observationsDroppedNoChange is a tick identical on every price and "
                "status field to the last retained row for its ticker. The archive is "
                "a change log; the price at that moment is known from the row it "
                "duplicates. It is reconciled here, never counted as missing."),
            "boundsAreLower": (
                "Truncation loss is measured only where a series vanished ENTIRELY "
                "versus a complete peer on the same date. A series truncated partway "
                "through its pages is invisible, as is any loss on a date with no "
                "complete peer. Snapshots older than the 21-day retention window "
                "cannot be audited at all."),
        },
    }

    os.makedirs(args.out_dir, exist_ok=True)
    out = os.path.join(args.out_dir, "archive_completeness.json")
    with open(out, "w") as fh:
        json.dump(report, fh, indent=2, sort_keys=True)
    print(json.dumps({"window": report["window"], "totals": report["totals"]},
                     indent=2, sort_keys=True))
    print("\n-> %s" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
