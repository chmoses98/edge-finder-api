#!/usr/bin/env python3
"""MLB-ALPHA-0002 Family C: does Kalshi's own market state predict its next
move, executable CLV, or settlement?

Input: pit_candle_panel (minute-resolution exchange record, decision grid
every 5 minutes, T-240..T-5). Predeclared feature families:
  MOMENTUM   dMid5/10/30/60, sameDirRun5
  STALENESS  minutesUnchanged, quoteAgeMin, minutesSinceLastTrade
  LIQUIDITY  spreadCents, dSpread30, volume, dVol30/60, openInterest, dOI60,
             tradeQty30/60, blockTrades60
  ORDER FLOW ofi10/30/60 (taker YES qty - taker NO qty)/total, lastTradeMinusMid
  PRICE      mid, distFrom50, minutesToStart
Targets: fairMidMoveToClose (price discovery), executable CLV on the
signalled side (execution), settlement residual, $10 post-fee P/L.

Two evidence layers:
 1. COARSE RULES (economically meaningful thresholds, no cut-point search):
    e.g. sign of ofi30, sign of dMid30, stale >= 60 min, lastTrade above/below
    mid. Per rule and family: mean target on the signalled side with a
    game-cluster bootstrap CI. Every rule is registered (winners and losers).
 2. WALK-FORWARD RIDGE (feature-discovery tool, not a strategy): fit on dates
    < d, predict fairMidMoveToClose on d; report OOS correlation and
    directional accuracy, plus the executable CLV of "buy the side the model
    says the market moves toward" when |prediction| >= 1c.
BH-FDR over all coarse-rule tests at q=0.10. RESEARCH ONLY.
"""

import gzip
import json
import os
import sys
from collections import defaultdict

import numpy as np

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, REPO)
ART = os.path.join(REPO, "data", "edgelab", "research_artifacts", "mlb_alpha_0002")
OUT = os.path.join(ART, "family_c_results.json")
SEED = 20260902
BURN_IN = 5
FEATURES = ["dMid5", "dMid10", "dMid30", "dMid60", "sameDirRun5", "minutesUnchanged", "quoteAgeMin",
            "minutesSinceLastTrade", "spreadCents", "dSpread30", "volume", "dVol30", "dVol60",
            "openInterest", "dOI60", "tradeQty30", "tradeQty60", "blockTrades60", "ofi10", "ofi30",
            "ofi60", "lastTradeMinusMid", "mid", "distFrom50", "minutesToStart"]
RULES = [   # (id, feature, predicate -> signalled side: +1 buy YES, -1 buy NO)
    ("C_OFI30_POS", "ofi30", lambda v: v is not None and v > 0.2, +1),
    ("C_OFI30_NEG", "ofi30", lambda v: v is not None and v < -0.2, -1),
    ("C_OFI60_POS", "ofi60", lambda v: v is not None and v > 0.2, +1),
    ("C_OFI60_NEG", "ofi60", lambda v: v is not None and v < -0.2, -1),
    ("C_MOM30_UP", "dMid30", lambda v: v is not None and v >= 2, +1),
    ("C_MOM30_DOWN", "dMid30", lambda v: v is not None and v <= -2, -1),
    ("C_MOM60_UP", "dMid60", lambda v: v is not None and v >= 3, +1),
    ("C_MOM60_DOWN", "dMid60", lambda v: v is not None and v <= -3, -1),
    ("C_REV30_UP", "dMid30", lambda v: v is not None and v >= 2, -1),      # reversal hypothesis
    ("C_REV30_DOWN", "dMid30", lambda v: v is not None and v <= -2, +1),
    ("C_LASTTRADE_ABOVE_MID", "lastTradeMinusMid", lambda v: v is not None and v > 0, +1),
    ("C_LASTTRADE_BELOW_MID", "lastTradeMinusMid", lambda v: v is not None and v < 0, -1),
    ("C_STALE60_BUYYES", "minutesUnchanged", lambda v: v is not None and v >= 60, +1),
    ("C_STALE60_BUYNO", "minutesUnchanged", lambda v: v is not None and v >= 60, -1),
    ("C_SPREADWIDE_BUYNO", "spreadCents", lambda v: v is not None and v >= 6, -1),
    ("C_SPREADWIDE_BUYYES", "spreadCents", lambda v: v is not None and v >= 6, +1),
    ("C_OI_SURGE_BUYYES", "dOI60", lambda v: v is not None and v > 0, +1),
    ("C_OI_SURGE_BUYNO", "dOI60", lambda v: v is not None and v > 0, -1),
]


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
    mean = float(np.mean(vals))
    # null-centred two-sided p
    centred = means - means.mean()
    p = float((np.abs(centred) >= abs(mean)).mean())
    return mean, [float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))], len(keys), max(p, 1.0 / B)


def bh(pvals, q=0.10):
    idx = sorted(range(len(pvals)), key=lambda i: pvals[i]); n = len(pvals); keep = set(); thr = 0
    for rank, i in enumerate(idx, 1):
        if pvals[i] <= q * rank / n:
            thr = rank
    for rank, i in enumerate(idx, 1):
        if rank <= thr:
            keep.add(i)
    return keep


def main():
    rows = list(iter_gz(os.path.join(ART, "pit_candle_panel.jsonl.gz")))
    dates = sorted({r["gameDate"] for r in rows})
    fams = defaultdict(list)
    for r in rows:
        fams[r["marketFamily"]].append(r)
    fams["ALL"] = rows
    res = {"programId": "MLB-ALPHA-0002", "family": "C", "rows": len(rows), "dates": dates,
           "games": len({r["gameKey"] for r in rows}), "features": FEATURES, "rulesRegistered": len(RULES),
           "hypothesesTested": 0, "coarseRules": [], "walkForward": {}}
    tests = []
    for fam, rs in fams.items():
        for rid, feat, pred, side in RULES:
            sel = [r for r in rs if pred(r.get(feat))]
            games = {r["gameKey"] for r in sel}
            rec = {"rule": rid, "family": fam, "rows": len(sel), "games": len(games), "dates": len({r["gameDate"] for r in sel})}
            if len(games) < 15:
                rec["status"] = "DESCRIPTIVE_ONLY"; res["coarseRules"].append(rec); continue
            clus = [r["gameKey"] for r in sel]
            fm = [side * r["fairMidMoveToClose"] for r in sel]
            clv = [r["clvYesCents"] if side > 0 else r["clvNoCents"] for r in sel]
            pl = [r["netPlBuyYes"] if side > 0 else r["netPlBuyNo"] for r in sel]
            m1, ci1, ng, p1 = cluster_boot(fm, clus); m2, ci2, _, p2 = cluster_boot(clv, clus); m3, ci3, _, p3 = cluster_boot(pl, clus)
            rec.update({"fairMidMoveSignalSideCents": m1, "fairMidCi95": ci1, "pFairMid": p1,
                        "execClvCents": m2, "execClvCi95": ci2, "pExecClv": p2,
                        "netPlPer10Usd": m3, "netPlCi95": ci3, "pNetPl": p3,
                        "winRateSignalSide": float(np.mean([(r["settlementResult"] == "YES") == (side > 0) for r in sel])),
                        "status": "TESTED"})
            tests.append(rec)
            res["coarseRules"].append(rec)
    res["hypothesesTested"] = len(tests) * 3
    for key, pk in (("fairMid", "pFairMid"), ("execClv", "pExecClv"), ("netPl", "pNetPl")):
        keep = bh([t[pk] for t in tests]) if tests else set()
        for i, t in enumerate(tests):
            t["bhSurvivor_" + key] = i in keep
    # walk-forward ridge on fairMidMoveToClose (feature discovery only)
    for fam, rs in fams.items():
        fd = sorted({r["gameDate"] for r in rs})
        if len(fd) <= BURN_IN + 1:
            res["walkForward"][fam] = {"status": "INSUFFICIENT_DATES", "dates": len(fd)}; continue
        preds, acts, clv_sel, pl_sel, clus_sel = [], [], [], [], []
        for i, d in enumerate(fd):
            if i < BURN_IN:
                continue
            tr = [r for r in rs if r["gameDate"] < d]; te = [r for r in rs if r["gameDate"] == d]
            def mat(rr):
                X = np.array([[(r.get(f) if r.get(f) is not None else 0.0) for f in FEATURES] for r in rr], dtype=float)
                return X
            Xtr, ytr = mat(tr), np.array([r["fairMidMoveToClose"] for r in tr])
            mu, sd = Xtr.mean(0), Xtr.std(0) + 1e-9
            Xs = (Xtr - mu) / sd
            lam = 10.0 * len(tr)**0.5
            w = np.linalg.solve(Xs.T @ Xs + lam * np.eye(Xs.shape[1]), Xs.T @ (ytr - ytr.mean()))
            Xte = (mat(te) - mu) / sd
            ph = Xte @ w + ytr.mean()
            for r, p in zip(te, ph):
                preds.append(p); acts.append(r["fairMidMoveToClose"]); clus_sel.append(r["gameKey"])
                if abs(p) >= 1.0:
                    clv_sel.append((r["clvYesCents"] if p > 0 else r["clvNoCents"], r["netPlBuyYes"] if p > 0 else r["netPlBuyNo"], r["gameKey"]))
        if len(preds) < 100:
            res["walkForward"][fam] = {"status": "INSUFFICIENT_OOS", "oosRows": len(preds)}; continue
        P, A = np.array(preds), np.array(acts)
        wf = {"oosRows": len(P), "oosGames": len(set(clus_sel)), "corr": float(np.corrcoef(P, A)[0, 1]) if P.std() > 0 else None,
              "directionalAccuracy": float(np.mean(np.sign(P) == np.sign(A))) if len(P) else None,
              "signalledRows": len(clv_sel)}
        if len(clv_sel) >= 30:
            m, ci, ng, p = cluster_boot([c[0] for c in clv_sel], [c[2] for c in clv_sel])
            m2, ci2, _, p2 = cluster_boot([c[1] for c in clv_sel], [c[2] for c in clv_sel])
            wf.update({"signalledGames": ng, "execClvCents": m, "execClvCi95": ci, "pExecClv": p,
                       "netPlPer10Usd": m2, "netPlCi95": ci2, "pNetPl": p2})
        res["walkForward"][fam] = wf
    with open(OUT, "w") as fh:
        json.dump(res, fh, indent=1, sort_keys=True); fh.write("\n")
    tested = [t for t in res["coarseRules"] if t.get("status") == "TESTED"]
    print("rows %d games %d dates %d | rules tested %d" % (res["rows"], res["games"], len(dates), len(tested)))
    for t in sorted(tested, key=lambda t: t["pExecClv"])[:12]:
        print("%-24s %-16s g=%3d fairMid=%+.2f [%+.2f,%+.2f] clv=%+.2f [%+.2f,%+.2f] pl=%+.2f p=%.3f bh=%s" % (
            t["rule"], t["family"], t["games"], t["fairMidMoveSignalSideCents"], *t["fairMidCi95"],
            t["execClvCents"], *t["execClvCi95"], t["netPlPer10Usd"], t["pExecClv"], t["bhSurvivor_execClv"]))
    print("walk-forward:", json.dumps(res["walkForward"], default=str)[:800])
    return 0


if __name__ == "__main__":
    sys.exit(main())
