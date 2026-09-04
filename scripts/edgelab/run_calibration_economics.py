#!/usr/bin/env python3
"""
scripts/edgelab/run_calibration_economics.py
=============================================
Economic validation of the walk-forward (out-of-sample) calibration
candidates: does a better-calibrated probability also make better bets?
RESEARCH ONLY.

For each candidate probability p and each row, edge = p - executable YES ask
at capture time.  A hypothetical $10 taker order is placed whenever edge
exceeds a threshold; P&L is settled with lib.edgelab.kalshi_fees
(fee-aware).  Also reports realized return by calibrated-edge bucket
(monotonicity), hit rates, CLV proxy (pregame closing mid - entry ask), and
false-positive counts (apparent >=5-point edges that lose money).

Reads walkforward_predictions.jsonl.gz; writes economics.{json,md}.
"""
import os
import sys

import numpy as np
import pandas as pd

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from lib.edgelab.research import calibration_analysis as ca  # noqa: E402
from lib.edgelab.kalshi_fees import taker_fee  # noqa: E402

PRED_PATH = os.path.join(ca.DATASET_DIR, "walkforward_predictions.jsonl.gz")
OUT_JSON = os.path.join(ca.DATASET_DIR, "economics.json")
OUT_MD = os.path.join(ca.DATASET_DIR, "economics.md")
ORDER_DOLLARS = 10.0
THRESHOLDS = (0.0, 0.02, 0.05, 0.10)
EDGE_BUCKETS = [(-1.0, -0.10), (-0.10, -0.05), (-0.05, 0.0), (0.0, 0.025), (0.025, 0.05), (0.05, 0.10), (0.10, 0.20), (0.20, 1.01)]


def settle_yes_order(price, won, order_dollars=ORDER_DOLLARS):
    """Whole-contract $order at YES ask `price` (0-1); returns (net_pnl, cash_used, fee)."""
    if not (0 < price < 1):
        return 0.0, 0.0, 0.0
    contracts = int(order_dollars // price)
    if contracts <= 0:
        return 0.0, 0.0, 0.0
    fee = taker_fee(contracts, price)
    cost = contracts * price
    payout = contracts * 1.0 if won else 0.0
    return payout - cost - fee, cost + fee, fee


def evaluate_rule(d, pcol, threshold):
    """Bet YES when p - ask > threshold. Returns dict of aggregate economics with game-clustered CI on mean return per $ risked."""
    edge = d[pcol].values - d["askP"].values
    m = (edge > threshold) & d["askP"].between(0.02, 0.98).values
    sub = d[m]
    if len(sub) == 0:
        return {"threshold": threshold, "bets": 0}
    pnl, risked, fees, wins = [], [], [], []
    for r in sub.itertuples(index=False):
        net, cash, fee = settle_yes_order(r.askP, bool(r.outcome))
        pnl.append(net)
        risked.append(cash)
        fees.append(fee)
        wins.append(int(r.outcome))
    pnl, risked = np.array(pnl), np.array(risked)
    ret = np.where(risked > 0, pnl / np.where(risked > 0, risked, 1), 0.0)
    pt, lo, hi, pv = ca.fast_cluster_bootstrap_mean(ret, sub["gameId"].values)
    clv = (sub["closeP"] - sub["askP"]).dropna()
    return {"threshold": threshold, "bets": int(len(sub)), "games": int(sub["gameId"].nunique()), "hitRate": float(np.mean(wins)),
            "meanAsk": float(sub["askP"].mean()), "meanEdge": float(edge[m].mean()),
            "netPnl": float(pnl.sum()), "risked": float(risked.sum()), "fees": float(np.sum(fees)),
            "roi": float(pnl.sum() / risked.sum()) if risked.sum() > 0 else None,
            "meanReturnPerDollar": pt, "lo95": lo, "hi95": hi,
            "clvMean": float(clv.mean()) if len(clv) else None, "clvPositiveRate": float((clv > 0).mean()) if len(clv) else None, "clvN": int(len(clv))}


def edge_buckets(d, pcol):
    edge = d[pcol].values - d["askP"].values
    rows = []
    for lo, hi in EDGE_BUCKETS:
        m = (edge >= lo) & (edge < hi) & d["askP"].between(0.02, 0.98).values
        sub = d[m]
        if len(sub) < 30:
            rows.append({"lo": lo, "hi": hi, "n": int(len(sub))})
            continue
        # realized return of a YES contract bought at ask: outcome - ask (gross, per contract, fee-free)
        gross = sub["outcome"].values - sub["askP"].values
        pt, l, h, _ = ca.fast_cluster_bootstrap_mean(gross, sub["gameId"].values)
        rows.append({"lo": lo, "hi": hi, "n": int(len(sub)), "games": int(sub["gameId"].nunique()), "hitRate": float(sub["outcome"].mean()),
                     "meanP": float(sub[pcol].mean()), "meanAsk": float(sub["askP"].mean()), "grossReturnPerContract": pt, "lo95": l, "hi95": h,
                     "clvMean": float((sub["closeP"] - sub["askP"]).mean())})
    return rows


def main():
    d = pd.read_json(PRED_PATH, lines=True)
    d = d[d["askP"].notna() & d["outcome"].notna()].copy()
    d["outcome"] = d["outcome"].astype(int)
    cands = ["modelP", "marketP", "nbP", "C1_global_platt", "C2_family_platt_hier", "C4_family_isotonic", "C5_global_model_market_blend",
             "C8_nb_plus_family_platt", "C11_nb_mean_shift_family_platt", "M1_market_shrink"]
    cands = [c for c in cands if c in d.columns]
    out = {"orderDollars": ORDER_DOLLARS, "rows": len(d), "games": int(d["gameId"].nunique()), "rules": {}, "buckets": {}, "byFamily": {}}
    md = ["# Economic validation of out-of-sample calibration candidates\n",
          f"Rows={len(d)} games={d['gameId'].nunique()} (walk-forward test dates only). Rule: buy YES at the executable ask when p - ask > threshold; "
          f"${ORDER_DOLLARS:.0f} taker orders, whole contracts, Kalshi taker fees. Return CIs are 95% game-clustered bootstrap. CLV proxy = pregame closing mid - entry ask.\n"]
    for c in cands:
        rules = [evaluate_rule(d, c, t) for t in THRESHOLDS]
        out["rules"][c] = rules
        md.append(f"\n## {c}\n")
        md.append(ca.md_table(rules, ["threshold", "bets", "games", "hitRate", "meanAsk", "meanEdge", "netPnl", "risked", "fees", "roi", "meanReturnPerDollar", "lo95", "hi95", "clvMean", "clvPositiveRate"]))
        b = edge_buckets(d, c)
        out["buckets"][c] = b
        md.append(f"\n### {c}: realized gross return by edge bucket (monotonicity)\n")
        md.append(ca.md_table(b, ["lo", "hi", "n", "games", "hitRate", "meanP", "meanAsk", "grossReturnPerContract", "lo95", "hi95", "clvMean"]))
    # per family at the 5-point threshold for the key candidates
    fam_rows = []
    for fam, g in d.groupby("family"):
        for c in ("modelP", "C2_family_platt_hier", "C11_nb_mean_shift_family_platt", "C5_global_model_market_blend"):
            if c not in g:
                continue
            r = evaluate_rule(g, c, 0.05)
            r.update({"family": fam, "candidate": c})
            fam_rows.append(r)
    out["byFamily"] = fam_rows
    md.append("\n## By family at the 5-point edge threshold\n")
    md.append(ca.md_table(fam_rows, ["family", "candidate", "bets", "games", "hitRate", "meanEdge", "roi", "meanReturnPerDollar", "lo95", "hi95", "clvMean"]))
    ca.write_json(out, OUT_JSON)
    with open(OUT_MD, "w") as f:
        f.write("\n".join(md) + "\n")
    print(f"wrote {OUT_JSON}, {OUT_MD}")


if __name__ == "__main__":
    main()
