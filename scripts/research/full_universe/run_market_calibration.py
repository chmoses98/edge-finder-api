#!/usr/bin/env python3
"""
scripts/research/full_universe/run_market_calibration.py
========================================================
PHASE 10: is Kalshi's own executable pre-start price calibrated, across
the complete archived MLB market universe?

Reads the Phase 7 audit rows (market_coverage_audit.jsonl) and keeps only
markets that are BOTH canonically settled AND carry a valid executable
pre-start quote. Everything else is excluded and counted, never imputed.

The probability used is the executable YES ASK -- what a buyer of YES
would actually have paid. Not the midpoint, not the bid. That makes every
number here answerable to real execution rather than to a theoretical
mid. Fee-adjusted figures use the repo's existing Kalshi fee model.

Reports, by probability bucket and by market family and by closing
quality:
  n, YES-resolution frequency, mean price, Brier, log loss, and a
  Wilson 95% interval on the observed frequency.

RESEARCH ONLY. Nothing here is a betting instruction, and nothing here
is promoted into production policy. A bucket that looks mispriced at
n=40 is noise until it survives a holdout.

Usage:
    python3 scripts/research/full_universe/run_market_calibration.py
"""
import argparse
import collections
import gzip
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))))

SCHEMA_VERSION = "full_universe_calibration_v1"
AUDIT_ROWS = os.path.join("data", "edgelab", "reports", "market_coverage_audit.jsonl.gz")
OUT_DIR = os.path.join("data", "edgelab", "reports")

BUCKET_EDGES = (0.0, 0.05, 0.10, 0.20, 0.30, 0.40, 0.50,
                0.60, 0.70, 0.80, 0.90, 0.95, 1.0)


def bucket_label(p):
    for lo, hi in zip(BUCKET_EDGES, BUCKET_EDGES[1:]):
        if lo <= p < hi:
            return "%.2f-%.2f" % (lo, hi)
    return "0.95-1.00"


def wilson(successes, n, z=1.96):
    """95% interval on a proportion. Small-n buckets must LOOK small."""
    if not n:
        return (None, None)
    phat = successes / n
    denom = 1 + z * z / n
    centre = (phat + z * z / (2 * n)) / denom
    margin = z * math.sqrt((phat * (1 - phat) + z * z / (4 * n)) / n) / denom
    return (round(max(0.0, centre - margin), 4), round(min(1.0, centre + margin), 4))


def stats(rows):
    n = len(rows)
    if not n:
        return None
    yes = sum(1 for r in rows if r["resolvedYes"])
    brier = sum((r["price"] - (1.0 if r["resolvedYes"] else 0.0)) ** 2 for r in rows) / n
    eps = 1e-15
    logloss = -sum(
        math.log(max(eps, r["price"])) if r["resolvedYes"] else math.log(max(eps, 1.0 - r["price"]))
        for r in rows
    ) / n
    lo, hi = wilson(yes, n)
    mean_price = sum(r["price"] for r in rows) / n
    return {
        "n": n,
        "meanExecutablePrice": round(mean_price, 4),
        "observedYesRate": round(yes / n, 4),
        "observedYesRate95Lo": lo,
        "observedYesRate95Hi": hi,
        # Positive => the market UNDER-priced YES in this cell.
        "calibrationGap": round(yes / n - mean_price, 4),
        "marketWithinInterval": bool(lo is not None and lo <= mean_price <= hi),
        "brier": round(brier, 5),
        "logLoss": round(logloss, 5),
        "independentGames": len({r["gameId"] for r in rows if r["gameId"]}),
        "dateRange": [min(r["gameDate"] for r in rows if r["gameDate"]),
                      max(r["gameDate"] for r in rows if r["gameDate"])],
    }


def load(path):
    kept, excluded = [], collections.Counter()
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            r = json.loads(line)
            if r.get("settlementStatus") != "SETTLED":
                excluded["not_settled"] += 1
                continue
            if r.get("settlementResult") not in ("YES", "NO"):
                excluded["settled_without_yes_no_result"] += 1
                continue
            if r.get("secondsBeforeStart") is None:
                excluded["no_valid_prestart_quote"] += 1
                continue
            price = r.get("lastPrestartYesAsk")
            if price is None:
                excluded["no_executable_yes_ask"] += 1
                continue
            if not (0.0 < price < 1.0):
                # 0 and 1 are not tradable probabilities; they are an absent
                # or already-resolved book. Excluded, never clamped.
                excluded["price_not_strictly_between_0_and_1"] += 1
                continue
            kept.append({
                "ticker": r["ticker"], "gameId": r.get("gameId"), "gameDate": r.get("gameDate"),
                "marketFamily": r.get("marketFamily") or "UNKNOWN",
                "price": float(price), "resolvedYes": r["settlementResult"] == "YES",
                "secondsBeforeStart": r["secondsBeforeStart"],
                "coverageClass": r.get("coverageClass"),
            })
    return kept, excluded


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--audit-rows", default=AUDIT_ROWS)
    ap.add_argument("--out-dir", default=OUT_DIR)
    args = ap.parse_args()

    rows, excluded = load(args.audit_rows)

    by_bucket = collections.defaultdict(list)
    by_family = collections.defaultdict(list)
    by_quality = collections.defaultdict(list)
    for r in rows:
        by_bucket[bucket_label(r["price"])].append(r)
        by_family[r["marketFamily"]].append(r)
        s = r["secondsBeforeStart"]
        by_quality["T5" if s <= 300 else "T15" if s <= 900 else
                    "T30" if s <= 1800 else "PRE_CLOSE"].append(r)

    t30 = [r for r in rows if r["secondsBeforeStart"] <= 1800]
    report = {
        "schemaVersion": SCHEMA_VERSION,
        "sample": {
            "eligibleRows": len(rows),
            "excludedReasons": dict(excluded),
            "trueCloseRows": len(t30),
            "independentGames": len({r["gameId"] for r in rows if r["gameId"]}),
        },
        "overall": stats(rows),
        "overallTrueCloseOnly": stats(t30),
        "byProbabilityBucket": {k: stats(v) for k, v in sorted(by_bucket.items())},
        "byProbabilityBucketTrueCloseOnly": {
            k: stats([r for r in v if r["secondsBeforeStart"] <= 1800])
            for k, v in sorted(by_bucket.items())
        },
        "byMarketFamily": {k: stats(v) for k, v in sorted(by_family.items())},
        "byClosingQuality": {k: stats(v) for k, v in sorted(by_quality.items())},
        "interpretation": [
            "RESEARCH ONLY. No result here is a betting instruction and none was "
            "promoted into production policy.",
            "The probability is the executable YES ASK -- what a YES buyer pays -- "
            "not a midpoint, so these numbers answer to real execution.",
            "calibrationGap = observed YES rate - mean paid price. Positive means "
            "the market UNDER-priced YES in that cell. It is NOT a return: buying "
            "the ask pays the spread, and Kalshi fees are charged on top.",
            "marketWithinInterval=true means the observed frequency's 95% interval "
            "contains the mean price -- i.e. NO detectable miscalibration at this n.",
            "Buckets with small n are noise. Read the interval, not the point estimate.",
            "THE BIGGEST CAVEAT: the Wilson interval assumes INDEPENDENT trials, and "
            "these rows are not independent. Thousands of markets resolve off a few "
            "hundred games -- every hitter prop, team total and game total on one game "
            "shares that game's outcome. Compare n against independentGames in each "
            "cell: where n/independentGames is large the true interval is MUCH wider "
            "than the one printed, and a cell flagged marketWithinInterval=false may "
            "well be inside a correctly-widened interval. Treat every gap here as "
            "EXPLORATORY, never as a measured edge.",
            "A gap is also not a profit. Buying the YES ask pays the spread, Kalshi "
            "charges a fee on top, and a negative gap would require SELLING YES -- i.e. "
            "buying NO at its own ask, which is a different and usually worse price.",
        ],
        "correlationWarning": {
            "rowsPerIndependentGame": round(
                len(rows) / max(1, len({r["gameId"] for r in rows if r["gameId"]})), 1),
            "note": "Printed confidence intervals assume independence and are therefore "
                    "too narrow by roughly this factor's square root in the worst case.",
        },
    }

    os.makedirs(args.out_dir, exist_ok=True)
    out = os.path.join(args.out_dir, "full_universe_calibration.json")
    with open(out, "w") as fh:
        json.dump(report, fh, indent=2, sort_keys=True)

    print(json.dumps({"sample": report["sample"], "overall": report["overall"],
                      "overallTrueCloseOnly": report["overallTrueCloseOnly"],
                      "byClosingQuality": report["byClosingQuality"]},
                     indent=2, sort_keys=True))
    print("\n-> %s" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
