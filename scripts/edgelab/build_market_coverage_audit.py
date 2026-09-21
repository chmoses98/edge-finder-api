#!/usr/bin/env python3
"""
scripts/edgelab/build_market_coverage_audit.py
==============================================
PHASE 7: how much of the COMPLETE archived MLB Kalshi market universe is
actually usable for close-based research?

Not "how many wagers do we have CLV for" -- that question was already
answered (21 TRUE_CLOSE wager rows). This one walks EVERY attributable
archived market, whether or not anyone ever bet it, whether or not the
model supports its family, and asks of each: was there a valid
executable quote before first pitch, and how close to first pitch was it?

Per market it derives:
  identity      ticker, gameId, gameDate, family, threshold, matchup
  timing        scheduledStart, first/last pre-start observation,
                secondsBeforeStart, observation count
  coverage      the canonical class from lib.edgelab.checkpoints, plus a
                finer bucket (<=5 / <=15 / <=30 / <=60 / <=90 / >90 / none)
  outcome       canonical settlement status and result, when settled
  usage         whether we ever wagered it

Rules it will not break:
  * a post-start observation is NEVER pre-start evidence;
  * a market whose scheduledStart cannot be resolved is counted as
    START_UNRESOLVED, never assumed;
  * price units are read from the observation's own declared fields --
    never inferred from magnitude;
  * settlement is read, never recomputed.

Deterministic and idempotent: same archive in, same bytes out.

Usage:
    python3 scripts/edgelab/build_market_coverage_audit.py [--out-dir DIR]
"""
import argparse
import collections
import glob
import gzip
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab import checkpoints

SCHEMA_VERSION = "market_coverage_audit_v1"
OUT_DIR = os.path.join("data", "edgelab", "reports")

# Finer than the canonical TRUE_CLOSE/PRE_CLOSE split, so the 30-minute
# policy can be re-examined from the data without recomputing anything.
BUCKETS = ((300, "<=5m"), (900, "<=15m"), (1800, "<=30m"),
           (3600, "<=60m"), (5400, "<=90m"))
BUCKET_OVER = ">90m"
BUCKET_NONE = "no_valid_prestart_quote"
BUCKET_UNRESOLVED = "start_unresolved"


def _read(path):
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


def bucket_for(seconds):
    if seconds is None:
        return BUCKET_NONE
    for limit, label in BUCKETS:
        if seconds <= limit:
            return label
    return BUCKET_OVER


def _executable_present(obs):
    """A quote is usable evidence only if some executable side exists."""
    return any(obs.get(k) is not None for k in ("yesAsk", "yesBid", "noAsk", "noBid"))


def collect():
    """Walk every observation partition once, folding per ticker."""
    per = {}
    for path in sorted(glob.glob(os.path.join("data", "edgelab", "observations", "*.jsonl*"))):
        for obs in _read(path):
            ticker = obs.get("marketTicker")
            if not ticker:
                continue
            row = per.get(ticker)
            if row is None:
                row = per[ticker] = {
                    "ticker": ticker, "observations": 0,
                    "gameDate": None, "gameId": None, "marketFamily": None,
                    "threshold": None, "awayTeam": None, "homeTeam": None,
                    "scheduledStart": None, "title": None,
                    "firstObservedAt": None, "lastObservedAt": None,
                    "lastPrestartAt": None, "lastPrestartSeconds": None,
                    "lastPrestart": None, "postStartObservations": 0,
                }
            row["observations"] += 1
            captured = obs.get("capturedAt")
            for key, better in (("firstObservedAt", min), ("lastObservedAt", max)):
                cur = row[key]
                row[key] = captured if cur is None else better(cur, captured)
            for key, src in (("gameDate", "gameDate"), ("gameId", "gameId"),
                             ("marketFamily", "marketFamily"), ("threshold", "threshold"),
                             ("awayTeam", "awayTeam"), ("homeTeam", "homeTeam"),
                             ("title", "title")):
                if row[key] is None and obs.get(src) is not None:
                    row[key] = obs.get(src)
            if row["gameDate"] is None and captured:
                row["gameDate"] = captured[:10]
            start = obs.get("scheduledStart")
            if start and row["scheduledStart"] is None:
                row["scheduledStart"] = start

            start = row["scheduledStart"] or start
            if not start or not captured:
                continue
            seconds = checkpoints.seconds_before_start(captured, start)
            if seconds is None:
                continue
            if seconds < 0:
                row["postStartObservations"] += 1
                continue
            status = (obs.get("marketStatus") or "active").lower()
            if status not in ("active", "unknown") or not _executable_present(obs):
                continue
            # LATEST valid pre-start quote == smallest non-negative distance.
            if row["lastPrestartSeconds"] is None or seconds < row["lastPrestartSeconds"]:
                row["lastPrestartSeconds"] = seconds
                row["lastPrestartAt"] = captured
                row["lastPrestart"] = {
                    "yesBid": obs.get("yesBid"), "yesAsk": obs.get("yesAsk"),
                    "noBid": obs.get("noBid"), "noAsk": obs.get("noAsk"),
                    "checkpoint": obs.get("checkpoint"),
                    "marketObservationId": obs.get("marketObservationId"),
                }
    return per


def load_settlements():
    out = {}
    for path in sorted(glob.glob(os.path.join("data", "edgelab", "settlements", "*.jsonl*"))):
        for rec in _read(path):
            ticker = rec.get("marketTicker")
            if ticker:
                out[ticker] = (rec.get("settlementStatus"), rec.get("result"))
    return out


def load_wagered():
    path = os.path.join("data", "edgelab", "bets", "bets.jsonl")
    if not os.path.exists(path):
        return set()
    return {b.get("marketTicker") for b in _read(path) if b.get("marketTicker")}


def build_rows(per, settlements, wagered):
    rows = []
    for ticker, row in per.items():
        start = row["scheduledStart"]
        seconds = row["lastPrestartSeconds"]
        if not start:
            coverage, bucket = BUCKET_UNRESOLVED, BUCKET_UNRESOLVED
        else:
            coverage = checkpoints.classify_closing_coverage(seconds)
            bucket = bucket_for(seconds)
        status, result = settlements.get(ticker, (None, None))
        quote = row["lastPrestart"] or {}
        rows.append({
            "schemaVersion": SCHEMA_VERSION,
            "ticker": ticker,
            "gameDate": row["gameDate"],
            "gameId": row["gameId"],
            "matchup": ("%s@%s" % (row["awayTeam"], row["homeTeam"])
                        if row["awayTeam"] and row["homeTeam"] else None),
            "marketFamily": row["marketFamily"],
            "marketTitle": row["title"],
            "threshold": row["threshold"],
            "scheduledStart": start,
            "observations": row["observations"],
            "postStartObservations": row["postStartObservations"],
            "firstObservedAt": row["firstObservedAt"],
            "lastObservedAt": row["lastObservedAt"],
            "lastPrestartAt": row["lastPrestartAt"],
            "secondsBeforeStart": seconds,
            "coverageClass": coverage,
            "coverageBucket": bucket,
            "lastPrestartYesBid": quote.get("yesBid"),
            "lastPrestartYesAsk": quote.get("yesAsk"),
            "lastPrestartNoBid": quote.get("noBid"),
            "lastPrestartNoAsk": quote.get("noAsk"),
            "lastPrestartCheckpoint": quote.get("checkpoint"),
            "lastPrestartObservationId": quote.get("marketObservationId"),
            "settlementStatus": status,
            "settlementResult": result,
            "wageredByUs": ticker in wagered,
        })
    rows.sort(key=lambda r: (r["gameDate"] or "", r["ticker"]))
    return rows


def summarize(rows):
    def count(pred):
        return sum(1 for r in rows if pred(r))

    settled = [r for r in rows if r["settlementStatus"] == "SETTLED"]
    within = lambda r, s: r["secondsBeforeStart"] is not None and r["secondsBeforeStart"] <= s

    by_family = collections.defaultdict(lambda: collections.Counter())
    for r in rows:
        fam = by_family[r["marketFamily"] or "UNKNOWN"]
        fam["markets"] += 1
        fam["observations"] += r["observations"]
        if r["secondsBeforeStart"] is not None:
            fam["anyPrestart"] += 1
            for limit, label in BUCKETS:
                if r["secondsBeforeStart"] <= limit:
                    fam[label] += 1
        if r["settlementStatus"] == "SETTLED":
            fam["settled"] += 1
            if within(r, 1800):
                fam["settledT30"] += 1

    by_date = collections.defaultdict(lambda: collections.Counter())
    for r in rows:
        d = by_date[r["gameDate"] or "UNKNOWN"]
        d["markets"] += 1
        if r["secondsBeforeStart"] is not None:
            d["anyPrestart"] += 1
            if within(r, 1800):
                d["t30"] += 1
            if within(r, 900):
                d["t15"] += 1
            if within(r, 300):
                d["t5"] += 1

    ages = sorted(r["secondsBeforeStart"] for r in rows if r["secondsBeforeStart"] is not None)

    def pct(q):
        return ages[min(round(q * (len(ages) - 1)), len(ages) - 1)] if ages else None

    return {
        "schemaVersion": SCHEMA_VERSION,
        "totalAttributableMarkets": len(rows),
        "totalArchivedObservations": sum(r["observations"] for r in rows),
        "marketsWithAnyPrestartEvidence": len(ages),
        "marketsWithNoPrestartEvidence": count(lambda r: r["coverageBucket"] == BUCKET_NONE),
        "marketsWithUnresolvedStart": count(lambda r: r["coverageBucket"] == BUCKET_UNRESOLVED),
        "t30Markets": count(lambda r: within(r, 1800)),
        "t15Markets": count(lambda r: within(r, 900)),
        "t5Markets": count(lambda r: within(r, 300)),
        "settledMarkets": len(settled),
        "settledWithT30": sum(1 for r in settled if within(r, 1800)),
        "settledWithT15": sum(1 for r in settled if within(r, 900)),
        "settledWithT5": sum(1 for r in settled if within(r, 300)),
        "settledWithAnyPrestart": sum(1 for r in settled if r["secondsBeforeStart"] is not None),
        "wageredMarkets": count(lambda r: r["wageredByUs"]),
        "coverageClassCounts": dict(collections.Counter(r["coverageClass"] for r in rows)),
        "coverageBucketCounts": dict(collections.Counter(r["coverageBucket"] for r in rows)),
        "medianSecondsBeforeStart": pct(0.5),
        "p90SecondsBeforeStart": pct(0.9),
        "marketsWithPostStartObservations": count(lambda r: r["postStartObservations"] > 0),
        "byFamily": {k: dict(v) for k, v in sorted(by_family.items())},
        "byDate": {k: dict(v) for k, v in sorted(by_date.items())},
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out-dir", default=OUT_DIR)
    args = ap.parse_args()

    rows = build_rows(collect(), load_settlements(), load_wagered())
    summary = summarize(rows)

    os.makedirs(args.out_dir, exist_ok=True)
    # GZIPPED, following the same convention lib/edgelab/storage.py already
    # applies to MarketObservation -- "the one entity whose git-committed
    # volume doesn't fit a season". One row per attributable market is
    # 184 MB plain, which GitHub's 100 MB file limit rejects outright, and
    # 9.1 MB compressed. mtime is pinned to 0 for the same reason storage.py
    # pins it: gzip otherwise embeds wall-clock time, so a rerun over an
    # unchanged archive would byte-differ and destroy the idempotency this
    # export is supposed to have.
    rows_path = os.path.join(args.out_dir, "market_coverage_audit.jsonl.gz")
    summary_path = os.path.join(args.out_dir, "market_coverage_audit_summary.json")
    payload = "".join(json.dumps(r, sort_keys=True) + "\n" for r in rows).encode("utf-8")
    with open(rows_path, "wb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as fh:
            fh.write(payload)
    with open(summary_path, "w") as fh:
        json.dump(summary, fh, indent=2, sort_keys=True)

    print(json.dumps({k: v for k, v in summary.items() if k not in ("byFamily", "byDate")},
                     indent=2, sort_keys=True))
    print("\nrows    -> %s" % rows_path)
    print("summary -> %s" % summary_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
