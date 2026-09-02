#!/usr/bin/env python3
"""MLB-ALPHA-0002 market topology: does the SHAPE of one market predict
another contract's movement? (Not an arbitrage scan.)

From the minute panel, at each decision time t and game:
  full-game total ladder  P_N = mid of rung N (P(total >= N)), N ascending
  F5 total ladder         Q_N likewise
Features (known at t):
  ladderSteepness  = P_{m} - P_{m+2} around the median rung m (how peaked)
  ladderSkew       = (P_{m+1} - 0.5) - (0.5 - P_{m-1})  (asymmetry)
  ladderMean       = sum over rungs of P_N (expected total, approx)
  f5FullRatio      = F5 expected total / full-game expected total
  ladderInversions = count of P_N < P_{N+1} (monotonicity violations)
Targets: fair-mid move to close of the game's F5 total rungs, team totals
and game_result contracts, executable CLV on the signalled side.
Coarse rules: ratio in the top/bottom decile of the development sample,
inversion present. Game-cluster bootstrap. RESEARCH ONLY.
"""

import gzip
import json
import os
import sys
from collections import defaultdict

import numpy as np

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
ART = os.path.join(REPO, "data", "edgelab", "research_artifacts", "mlb_alpha_0002")
OUT = os.path.join(ART, "family_t_results.json")
SEED = 20260902


def iter_gz(p):
    with gzip.open(p, "rt") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


def cluster_boot(vals, clus, B=2000, seed=SEED):
    rng = np.random.default_rng(seed)
    g = defaultdict(list)
    for v, c in zip(vals, clus):
        g[c].append(v)
    keys = list(g); sums = np.array([sum(g[k]) for k in keys]); cnt = np.array([len(g[k]) for k in keys])
    idx = rng.integers(0, len(keys), size=(B, len(keys)))
    means = sums[idx].sum(1) / cnt[idx].sum(1)
    return float(np.mean(vals)), [float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))], len(keys)


def ladder_features(rungs):
    """rungs: {N: mid_prob} -> features or None"""
    if len(rungs) < 4:
        return None
    Ns = sorted(rungs)
    P = [rungs[n] for n in Ns]
    inv = sum(1 for i in range(len(P) - 1) if P[i] < P[i + 1] - 0.005)
    mean_total = Ns[0] - 1 + sum(P)             # E[T] ~ sum_N P(T>=N) (+ offset)
    # median rung: first N with P < 0.5
    m_idx = next((i for i, p in enumerate(P) if p < 0.5), len(P) - 1)
    lo, hi = max(m_idx - 1, 0), min(m_idx + 1, len(P) - 1)
    steep = P[lo] - P[hi]
    skew = (P[hi] - 0.5) + (P[lo] - 0.5)
    return {"ladderMean": mean_total, "ladderSteepness": steep, "ladderSkew": skew, "ladderInversions": inv, "rungs": len(P)}


def main():
    rows = list(iter_gz(os.path.join(ART, "pit_candle_panel.jsonl.gz")))
    by = defaultdict(lambda: defaultdict(list))   # (game, minute) -> family -> rows
    for r in rows:
        by[(r["gameKey"], r["decisionMinute"])][r["marketFamily"]].append(r)
    feats = []
    for (g, m), fams in by.items():
        gt = {int(r["marketTicker"].rsplit("-", 1)[1]): r["mid"] / 100.0 for r in fams.get("game_total", []) if r["marketTicker"].rsplit("-", 1)[1].isdigit()}
        f5 = {int(r["marketTicker"].rsplit("-", 1)[1]): r["mid"] / 100.0 for r in fams.get("inning_total", []) if r["marketTicker"].rsplit("-", 1)[1].isdigit()}
        lf, lf5 = ladder_features(gt), ladder_features(f5)
        if not lf or not lf5:
            continue
        ratio = lf5["ladderMean"] / lf["ladderMean"] if lf["ladderMean"] > 0 else None
        for tgt_fam in ("inning_total", "game_result", "team_total"):
            for r in fams.get(tgt_fam, []):
                feats.append({"game": g, "date": r["gameDate"], "fam": tgt_fam, "ratio": ratio,
                              "fullInv": lf["ladderInversions"], "f5Inv": lf5["ladderInversions"],
                              "fullSkew": lf["ladderSkew"], "f5Steep": lf5["ladderSteepness"],
                              "fm": r["fairMidMoveToClose"], "clvYes": r["clvYesCents"], "clvNo": r["clvNoCents"],
                              "plYes": r["netPlBuyYes"], "plNo": r["netPlBuyNo"]})
    res = {"programId": "MLB-ALPHA-0002", "family": "T", "rows": len(feats), "games": len({f["game"] for f in feats}),
           "dates": len({f["date"] for f in feats}), "tests": {}}
    ratios = [f["ratio"] for f in feats if f["ratio"] is not None]
    if len(ratios) >= 100 and len({f["game"] for f in feats}) >= 15:
        lo, hi = np.percentile(ratios, [10, 90])
        res["f5FullRatio"] = {"p10": float(lo), "median": float(np.median(ratios)), "p90": float(hi)}
        def test(name, sel, side):
            games = {f["game"] for f in sel}
            if len(games) < 15:
                res["tests"][name] = {"rows": len(sel), "status": "INSUFFICIENT"}; return
            clus = [f["game"] for f in sel]
            m1, c1, ng = cluster_boot([side * f["fm"] for f in sel], clus)
            m2, c2, _ = cluster_boot([f["clvYes"] if side > 0 else f["clvNo"] for f in sel], clus)
            m3, c3, _ = cluster_boot([f["plYes"] if side > 0 else f["plNo"] for f in sel], clus)
            res["tests"][name] = {"rows": len(sel), "games": ng, "fairMidSignalSideCents": m1, "ci95": c1,
                                  "execClvCents": m2, "clvCi95": c2, "netPlPer10Usd": m3, "plCi95": c3, "status": "TESTED"}
        f5 = [f for f in feats if f["fam"] == "inning_total" and f["ratio"] is not None]
        test("T1_f5_ratio_low_buyYES_f5total", [f for f in f5 if f["ratio"] <= lo], +1)     # F5 cheap vs full -> F5 rungs rise?
        test("T1_f5_ratio_high_buyNO_f5total", [f for f in f5 if f["ratio"] >= hi], -1)
        test("T2_full_ladder_inversion_buyNO_f5total", [f for f in f5 if f["fullInv"] > 0], -1)
        test("T2_full_ladder_inversion_buyYES_f5total", [f for f in f5 if f["fullInv"] > 0], +1)
        tt = [f for f in feats if f["fam"] == "team_total" and f["ratio"] is not None]
        test("T3_f5_ratio_low_buyYES_teamtotal", [f for f in tt if f["ratio"] <= lo], +1)
        test("T3_f5_ratio_high_buyNO_teamtotal", [f for f in tt if f["ratio"] >= hi], -1)
        res["hypothesesTested"] = sum(1 for t in res["tests"].values() if t.get("status") == "TESTED") * 3
    else:
        res["status"] = "INSUFFICIENT"
    with open(OUT, "w") as fh:
        json.dump(res, fh, indent=1, sort_keys=True); fh.write("\n")
    print(json.dumps(res, indent=1, default=str)[:2500])
    return 0


if __name__ == "__main__":
    sys.exit(main())
