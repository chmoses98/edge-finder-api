#!/usr/bin/env python3
"""
MRV-EXEC-001: executable depth and slippage at size from archived Kalshi
order books (ALPHA-0002 prospective corpus).  RESEARCH ONLY, descriptive.

Usage: run_books.py --corpus-root <dir with books/> [--dates ...]
Writes data/edgelab/research_artifacts/market_structure/book_depth.json
"""
import argparse
import collections
import gzip
import json
import os
import sys
from datetime import timezone, datetime

sys.path.insert(0, os.path.dirname(__file__))
from _common import write_artifact, now_iso, lead_bucket  # noqa: E402

from lib.edgelab.research.market_structure import book as BK  # noqa: E402
from lib.edgelab.research.market_structure.identity import parse_ticker  # noqa: E402

ORDER_SIZES_USD = (10, 50, 100, 250)


def _iter(path):
    if not os.path.exists(path):
        return
    with gzip.open(path, "rt") as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus-root", required=True)
    ap.add_argument("--dates", default="")
    args = ap.parse_args()
    dates = args.dates.split(",") if args.dates else sorted(f[:10] for f in os.listdir(os.path.join(args.corpus_root, "books")) if f.endswith(".jsonl.gz"))
    cells = collections.defaultdict(lambda: collections.defaultdict(list))
    counts = collections.Counter()
    games = set()
    for date in dates:
        for b in _iter(os.path.join(args.corpus_root, "books", "%s.jsonl.gz" % date)):
            ob = b.get("orderbook")
            if not ob:
                counts["emptyBook"] += 1
                continue
            ident = parse_ticker(b["marketTicker"])
            if ident.get("status") != "RESOLVED":
                counts["unparsed"] += 1
                continue
            run_ts = int(datetime.strptime(b["capturedAt"][:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp())
            start = int(ident["scheduledStartUtc"].replace(tzinfo=timezone.utc).timestamp())
            if run_ts >= start:
                counts["postStart"] += 1
                continue
            mts = (start - run_ts) / 60.0
            games.add(ident["physicalGameKey"])
            counts["booksUsed"] += 1
            yb, ya, ybs, yas = BK.top_of_book(ob)
            fam = ident["family"]
            for cellkey in (("family", fam), ("family_lead", fam, lead_bucket(mts)), ("all",)):
                ck = "|".join(str(x) for x in cellkey)
                c = cells[ck]
                c["n"].append(1)
                c["twoSided"].append(1 if (yb is not None and ya is not None) else 0)
                if yb is not None and ya is not None:
                    c["spread"].append(ya - yb)
                    c["topDepthYesAsk"].append(yas)
                    c["topDepthYesBid"].append(ybs)
                for side in ("YES", "NO"):
                    for usd in ORDER_SIZES_USD:
                        w = BK.walk_buy(ob, side, usd * 100)
                        if w["topPriceCents"] is None:
                            continue
                        c["slip_%s_%d" % (side, usd)].append(w["slippageCents"] if w["filled"] else None)
                        c["unfilled_%s_%d" % (side, usd)].append(0 if w["filled"] else 1)
                        c["within2c_%s_%d" % (side, usd)].append(1 if (w["filled"] and w["slippageCents"] <= 2.0) else 0)
    def med(xs):
        xs = sorted(x for x in xs if x is not None)
        return xs[len(xs) // 2] if xs else None
    def mean(xs):
        xs = [x for x in xs if x is not None]
        return (sum(xs) / len(xs)) if xs else None
    summary = {}
    for ck, c in cells.items():
        s = {"books": len(c["n"]), "twoSidedShare": mean(c["twoSided"]), "spreadMedian": med(c["spread"]), "spreadMean": mean(c["spread"]),
             "topDepthYesAskMedian": med(c["topDepthYesAsk"]), "topDepthYesBidMedian": med(c["topDepthYesBid"])}
        for side in ("YES", "NO"):
            for usd in ORDER_SIZES_USD:
                s["slippageMedian_%s_%d" % (side, usd)] = med(c["slip_%s_%d" % (side, usd)])
                s["slippageMean_%s_%d" % (side, usd)] = mean(c["slip_%s_%d" % (side, usd)])
                s["unfilledShare_%s_%d" % (side, usd)] = mean(c["unfilled_%s_%d" % (side, usd)])
                s["fillWithin2cShare_%s_%d" % (side, usd)] = mean(c["within2c_%s_%d" % (side, usd)])
        summary[ck] = s
    out = {"generatedAt": now_iso(), "tier": "TIER_B_EXPLORATORY", "dates": dates, "games": len(games), "counts": dict(counts),
           "caveat": "collector capped at 400 books per run, so KXMLBTOTAL/SPREAD/TEAMTOTAL books are nearly absent; cells reflect the captured families only; sizes are Kalshi fp contracts; slippage excludes fees",
           "cells": summary}
    p = write_artifact("book_depth.json", out)
    print("wrote", p, dict(counts))
    for ck in sorted(summary):
        if ck.startswith("family|") or ck == "all":
            s = summary[ck]
            print(ck, s["books"], "spread", s["spreadMedian"], "depthAsk", s["topDepthYesAskMedian"], "slipYES100", s["slippageMedian_YES_100"], "unfilledYES250", s["unfilledShare_YES_250"], "within2c_YES_100", s["fillWithin2cShare_YES_100"])


if __name__ == "__main__":
    main()
