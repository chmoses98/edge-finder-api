#!/usr/bin/env python3
"""
MRV-EXEC-002 / MRV-EXEC-003: taker/maker decomposition from the recovered
trade tape + 1-minute candles.  RESEARCH ONLY.

Usage: run_tape.py --raw-root <dir with candles/ and trades/> [--dates ...]
Writes data/edgelab/research_artifacts/market_structure/tape_decomposition.json
"""
import argparse
import collections
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from _common import write_artifact, now_iso, price_band, lead_bucket  # noqa: E402

from lib.edgelab.research.market_structure import candles as CD, tape as TP, stats as ST  # noqa: E402
from lib.edgelab.research.market_structure.identity import parse_ticker  # noqa: E402

FAMS = ("game_result", "game_total", "inning_result", "inning_total", "team_total", "winning_margin")
KEYS = ("halfSpread", "fee", "drift_5", "drift_30", "drift_last", "takerNet_5", "takerNet_30", "takerNet_last",
        "makerNet_30", "makerNet_last")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw-root", required=True)
    ap.add_argument("--dates", default="")
    args = ap.parse_args()
    dates = args.dates.split(",") if args.dates else CD.available_dates(args.raw_root)

    cells = collections.defaultdict(list)      # (family) -> rows (for clustered inference)
    agg = collections.defaultdict(lambda: collections.defaultdict(lambda: [0.0, 0.0]))  # cell -> key -> [sum w*v, sum w]
    counts = collections.Counter()
    games = set()
    for date in dates:
        series_by_ticker = {}
        for ident, series, _ in CD.iter_date(args.raw_root, date, FAMS):
            series_by_ticker[ident["marketTicker"]] = (ident, series)
        for rec in TP.iter_trade_records(args.raw_root, date):
            t = rec.get("ticker")
            if t not in series_by_ticker:
                counts["noCandleSeries"] += 1
                continue
            ident, series = series_by_ticker[t]
            start = CD.scheduled_start_ts(ident)
            last = TP.last_two_sided_pregame_minute(series, start)
            for tr in rec.get("trades") or []:
                counts["prints"] += 1
                row = TP.decompose_print(tr, series, start, last if last else start)
                if row is None:
                    counts["skipped"] += 1
                    continue
                counts["used"] += 1
                games.add(ident["physicalGameKey"])
                fam = ident["family"]
                pb = price_band(row["price"])
                lb = lead_bucket(row["minutesToStart"])
                for cell in (("family", fam), ("family_band", fam, pb), ("family_lead", fam, lb), ("all",)):
                    ck = "|".join(str(x) for x in cell)
                    a = agg[ck]
                    a["count"][0] += row["count"]; a["count"][1] += 1
                    for k in KEYS:
                        v = row.get(k)
                        if v is not None:
                            a[k][0] += v * row["count"]; a[k][1] += row["count"]
                cells[fam].append({"physicalGameKey": ident["physicalGameKey"], "gameDate": date, "count": row["count"],
                                   "drift_last": row["drift_last"], "drift_30": row["drift_30"],
                                   "takerNet_last": row["takerNet_last"], "makerNet_30": row["makerNet_30"]})
        print(date, dict(counts), flush=True)

    summary = {}
    for ck, a in agg.items():
        summary[ck] = {"prints": int(a["count"][1]), "contracts": round(a["count"][0], 2)}
        for k in KEYS:
            s, w = a[k]
            summary[ck][k] = round(s / w, 4) if w > 0 else None

    # inference (registered): per family, volume-weighted drift to last pregame minute (EXEC-002) and maker net at +30 (EXEC-003)
    def wmean(key):
        return lambda rows: TP.weighted_mean(rows, key)
    inference = {}
    pvals_002, pvals_003 = {}, {}
    for fam, rows in cells.items():
        for r in rows:   # weighted numerators for the ratio fast path
            for k in ("drift_last", "drift_30", "takerNet_last", "makerNet_30"):
                r["w_" + k] = None if r.get(k) is None else r[k] * r["count"]
        r2 = ST.cluster_bootstrap_ratio(rows, "w_drift_last", "count")
        r3 = ST.cluster_bootstrap_ratio(rows, "w_makerNet_30", "count")
        inference[fam] = {"driftToLast": r2, "makerNet30": r3, "takerNetLast": ST.cluster_bootstrap_ratio(rows, "w_takerNet_last", "count"),
                          "drift30": ST.cluster_bootstrap_ratio(rows, "w_drift_30", "count"),
                          "stabilityDrift": ST.date_half_stability(rows, wmean("drift_last")), "games": len({r["physicalGameKey"] for r in rows})}
        pvals_002[fam] = r2["pPercentile"]
        pvals_003[fam] = r3["pPercentile"]
    bh2 = ST.benjamini_hochberg(pvals_002)
    bh3 = ST.benjamini_hochberg(pvals_003)
    out = {"generatedAt": now_iso(), "tier": "TIER_B_EXPLORATORY", "dates": dates, "games": len(games),
           "counts": dict(counts), "cells": summary, "inference": inference,
           "bh": {"MRV-EXEC-002_driftToLast": bh2, "MRV-EXEC-003_makerNet30": bh3},
           "conventions": "cents per contract, volume-weighted by count_fp; pregame prints only; pre-trade mid = candle close one minute before the print; drift signed toward the taker; taker fee 0.07*P*(1-P) unrounded; maker fee at 0.0175 (conservative)"}
    p = write_artifact("tape_decomposition.json", out)
    print("wrote", p)
    for fam, v in inference.items():
        print(fam, "drift_last", v["driftToLast"]["point"], v["driftToLast"]["ciLow"], v["driftToLast"]["ciHigh"], "makerNet30", v["makerNet30"]["point"])


if __name__ == "__main__":
    main()
