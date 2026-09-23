#!/usr/bin/env python3
"""
MRV-TTP-001: execution-cost surface by time to first pitch and family from
the recovered 1-minute record.  RESEARCH ONLY, descriptive.

Usage: run_cost_surface.py --raw-root <dir with candles/>
Writes data/edgelab/research_artifacts/market_structure/cost_surface.json
"""
import argparse
import collections
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from _common import write_artifact, now_iso, lead_bucket  # noqa: E402

from lib.edgelab.research.market_structure import candles as CD, economics as E  # noqa: E402

FAMS = ("game_result", "game_total", "inning_result", "inning_total", "team_total", "winning_margin")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw-root", required=True)
    args = ap.parse_args()
    cells = collections.defaultdict(lambda: {"minutes": 0, "twoSided": 0, "spreads": [], "askFee": [], "volDelta": []})
    for date in CD.available_dates(args.raw_root):
        for ident, series, _ in CD.iter_date(args.raw_root, date, FAMS):
            start = CD.scheduled_start_ts(ident)
            prev_vol = None
            for t in sorted(series):
                if t > start or t < start - 24 * 3600:
                    continue
                bid, ask, vol, _ = series[t]
                lb = lead_bucket((start - t) / 60.0)
                for ck in (ident["family"] + "|" + lb, "all|" + lb):
                    c = cells[ck]
                    c["minutes"] += 1
                    if bid is not None and ask is not None:
                        c["twoSided"] += 1
                        c["spreads"].append(ask - bid)
                        c["askFee"].append(E.fee_drag_cents(ask))
                    if prev_vol is not None:
                        c["volDelta"].append(max(0.0, vol - prev_vol))
                prev_vol = vol
    def med(xs):
        xs = sorted(xs)
        return xs[len(xs) // 2] if xs else None
    out_cells = {}
    for ck, c in cells.items():
        out_cells[ck] = {"contractMinutes": c["minutes"], "twoSidedShare": (c["twoSided"] / c["minutes"]) if c["minutes"] else None,
                         "spreadMedianCents": med(c["spreads"]), "spreadMeanCents": (sum(c["spreads"]) / len(c["spreads"])) if c["spreads"] else None,
                         "takerFeeAtAskMeanCents": (sum(c["askFee"]) / len(c["askFee"])) if c["askFee"] else None,
                         "volumePerContractMinuteMean": (sum(c["volDelta"]) / len(c["volDelta"])) if c["volDelta"] else None}
    out = {"generatedAt": now_iso(), "tier": "TIER_B_EXPLORATORY", "cells": out_cells,
           "note": "descriptive; lead-time buckets by scheduled first pitch; spread from same-minute candle closes; fee = 100*0.07*P*(1-P) at the ask"}
    p = write_artifact("cost_surface.json", out)
    print("wrote", p)
    for ck in sorted(out_cells):
        if ck.startswith("all|"):
            print(ck, out_cells[ck])


if __name__ == "__main__":
    main()
