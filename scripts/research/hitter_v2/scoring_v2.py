#!/usr/bin/env python3
"""
scripts/research/hitter_v2/scoring_v2.py
========================================
SIFT MLB hitter V2 lane -- RESEARCH ONLY. Matched player-game-stat scoring for
the preregistered comparison (docs/research/HITTER_V2_PREREG.md):

  * discrete CRPS (= ranked probability score summed over the full count ladder 0..kmax),
  * MAE of the predictive median, MAE and squared error of the predictive mean, bias,
  * Brier at every rung of the stat's threshold ladder (P(X >= t)),
  * randomized PIT (central-80% coverage), p10-p90 coverage,
  * per-rung reliability (10 equal-width bins) ECE and calibration-in-the-large,
  * paired, game-clustered percentile bootstrap (1,000 reps) of challenger - comparator.
"""
import math
import random

import numpy as np

LADDER = {"PA": tuple(range(1, 8)), "H": (1, 2, 3, 4), "TB": tuple(range(1, 9)), "HR": (1, 2),
          "RBI": (1, 2, 3, 4, 5), "R": (1, 2, 3, 4), "BB": (1, 2, 3), "K": (1, 2, 3, 4),
          "HRR": tuple(range(1, 8))}
PAIRED = ("crps", "abs_med", "abs", "sq", "ladder_brier")
W2_THRESHOLDS = {"PA": 5, "H": 1, "TB": 2, "HR": 1, "RBI": 1, "R": 1}


def pmf_summary(pmf):
    ks = sorted(pmf)
    mean = sum(k * pmf[k] for k in ks)
    cdf, q = 0.0, {}
    for k in ks:
        cdf += pmf[k]
        for lv in (0.1, 0.5, 0.9):
            if lv not in q and cdf >= lv - 1e-12:
                q[lv] = k
    return mean, q.get(0.1, ks[-1]), q.get(0.5, ks[-1]), q.get(0.9, ks[-1])


def row_metrics(pmf, y, stat, rng):
    mean, p10, med, p90 = pmf_summary(pmf)
    kmax = max(max(pmf), y) + 1
    cdf, crps, F_ym1, F_y = 0.0, 0.0, 0.0, None
    for k in range(kmax + 1):
        cdf += pmf.get(k, 0.0)
        crps += (cdf - (1.0 if y <= k else 0.0)) ** 2
        if k == y - 1:
            F_ym1 = cdf
        if k == y:
            F_y = cdf
    if F_y is None:
        F_y = 1.0
    ps, os_ = [], []
    for t in LADDER[stat]:
        p = min(1.0, max(0.0, sum(v for k, v in pmf.items() if k >= t)))
        ps.append(p)
        os_.append(1.0 if y >= t else 0.0)
    lb = sum((p - o) ** 2 for p, o in zip(ps, os_)) / len(ps)
    pit = F_ym1 + rng.random() * (F_y - F_ym1)
    extra = {}
    if stat in W2_THRESHOLDS:  # the #285 single outcome-blind threshold (legacy-rule metrics)
        t = W2_THRESHOLDS[stat]
        p1 = min(1 - 1e-6, max(1e-6, sum(v for k, v in pmf.items() if k >= t)))
        o1 = 1.0 if y >= t else 0.0
        extra = {"brier1": (p1 - o1) ** 2, "logloss1": -(o1 * math.log(p1) + (1 - o1) * math.log(1 - p1))}
    return {**extra, "mean": mean, "med": med, "err": mean - y, "abs": abs(mean - y), "abs_med": abs(med - y),
            "sq": (mean - y) ** 2, "crps": crps, "ladder_brier": lb, "p": ps, "o": os_,
            "cov80": 1.0 if p10 <= y <= p90 else 0.0, "pit": pit, "pit_in80": 1.0 if 0.1 <= pit <= 0.9 else 0.0}


def score_rows(rows, seed=20261012):
    """rows: [{cluster, stat, y, sub, pmfs: {arm: pmf}}]."""
    rng = random.Random(seed)
    out = []
    for r in rows:
        m = {name: row_metrics(pmf, r["y"], r["stat"], rng) for name, pmf in r["pmfs"].items()}
        out.append({"cluster": r["cluster"], "stat": r["stat"], "y": r["y"], "sub": r.get("sub", {}), "m": m})
    return out


def reliability(ps, os_):
    bins = [[0.0, 0.0, 0] for _ in range(10)]
    for p, o in zip(ps, os_):
        b = bins[min(9, int(p * 10))]
        b[0] += p
        b[1] += o
        b[2] += 1
    n = len(ps)
    ece = sum(c / n * abs(sp / c - so / c) for sp, so, c in bins if c)
    return {"ece": ece, "citl": (sum(ps) - sum(os_)) / n, "mean_p": sum(ps) / n, "obs": sum(os_) / n,
            "bins": [{"bin": i, "n": c, "mean_p": round(sp / c, 4), "obs": round(so / c, 4)}
                     for i, (sp, so, c) in enumerate(bins) if c]}


def summarize(scored, arms):
    n = len(scored)
    if not n:
        return {"n": 0}
    res = {"n": n, "n_games": len({s["cluster"] for s in scored}), "mean_actual": sum(s["y"] for s in scored) / n}
    stat = scored[0]["stat"]
    for a in arms:
        ms = [s["m"][a] for s in scored]
        d = {k: sum(x[k] for x in ms) / n for k in ("mean", "err", "abs", "abs_med", "sq", "crps", "ladder_brier",
                                                    "cov80", "pit_in80")}
        d["rmse"] = math.sqrt(d.pop("sq"))
        d["bias"] = d.pop("err")
        d["mean_forecast"] = d.pop("mean")
        h = [0] * 10
        for x in ms:
            h[min(9, int(x["pit"] * 10))] += 1
        d["pit_deciles"] = [round(c / n, 4) for c in h]
        rungs = {}
        for j, t in enumerate(LADDER[stat]):
            rel = reliability([x["p"][j] for x in ms], [x["o"][j] for x in ms])
            rel["brier"] = sum((x["p"][j] - x["o"][j]) ** 2 for x in ms) / n
            rungs[str(t)] = rel
        d["rungs"] = rungs
        d["max_rung_ece"] = max(r["ece"] for r in rungs.values())
        d["max_rung_abs_citl"] = max(abs(r["citl"]) for r in rungs.values())
        res[a] = d
    return res


def paired_bootstrap(scored, a, b, metrics=PAIRED, reps=1000, seed=20261012):
    """b minus a; mean diff + game-clustered 95% percentile CI. Also |bias_b| - |bias_a|."""
    clusters = {}
    for s in scored:
        clusters.setdefault(s["cluster"], []).append(s)
    names = sorted(clusters)
    C = len(names)
    mets = list(metrics) + ["err"]
    K = len(mets)
    sa = np.zeros((C, K))
    sb = np.zeros((C, K))
    cnt = np.zeros(C)
    for i, c in enumerate(names):
        for s in clusters[c]:
            cnt[i] += 1
            for j, mt in enumerate(mets):
                sa[i, j] += s["m"][a][mt]
                sb[i, j] += s["m"][b][mt]

    def stat(ix):
        n = cnt[ix].sum()
        ma = sa[ix].sum(axis=0) / n
        mb = sb[ix].sum(axis=0) / n
        d = mb - ma
        d[-1] = abs(mb[-1]) - abs(ma[-1])
        return d

    point = stat(np.arange(C))
    rng = np.random.default_rng(seed)
    draws = np.array([stat(rng.integers(0, C, C)) for _ in range(reps)])
    out = {"n": int(cnt.sum()), "n_games": C}
    for j, mt in enumerate(mets):
        lo, hi = np.percentile(draws[:, j], [2.5, 97.5])
        key = "abs_bias" if mt == "err" else mt
        out[key] = {"delta": round(float(point[j]), 6), "ci95": [round(float(lo), 6), round(float(hi), 6)]}
    # relative MAE-of-median difference (for the non-inferiority margin): upper CI of delta / mean(a)
    if "abs_med" in mets:
        ja = mets.index("abs_med")
        out["abs_med"]["comparator_mean"] = round(float(sa[:, ja].sum() / cnt.sum()), 6)
    return out
