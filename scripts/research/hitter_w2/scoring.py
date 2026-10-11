#!/usr/bin/env python3
"""
scripts/research/hitter_w2/scoring.py
=====================================
SIFT W2 hitter lane -- RESEARCH ONLY. Matched player-game-stat scoring with
game-clustered paired bootstrap CIs (numpy). One row per player-game-stat; one
outcome-blind threshold per stat (preregistered: PA>=5, H>=1, TB>=2, HR>=1,
RBI>=1, R>=1).
"""
import math
import random

import numpy as np

STATS = ("PA", "H", "TB", "HR", "RBI", "R")
THRESHOLDS = {"PA": 5, "H": 1, "TB": 2, "HR": 1, "RBI": 1, "R": 1}
PRIMARY = ("crps", "abs", "sq", "brier", "logloss")


def pmf_stats(pmf):
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
    t = THRESHOLDS[stat]
    mean, p10, med, p90 = pmf_stats(pmf)
    kmax = max(max(pmf), y) + 1
    cdf, crps = 0.0, 0.0
    F_ym1, F_y = 0.0, None
    for k in range(kmax + 1):
        cdf += pmf.get(k, 0.0)
        crps += (cdf - (1.0 if y <= k else 0.0)) ** 2
        if k == y - 1:
            F_ym1 = cdf
        if k == y:
            F_y = cdf
    if F_y is None:
        F_y = 1.0
    p = min(1 - 1e-6, max(1e-6, sum(v for k, v in pmf.items() if k >= t)))
    o = 1.0 if y >= t else 0.0
    pit = F_ym1 + rng.random() * (F_y - F_ym1)
    return {
        "mean": mean, "err": mean - y, "abs": abs(mean - y), "sq": (mean - y) ** 2, "crps": crps,
        "p": p, "o": o, "brier": (p - o) ** 2, "logloss": -(o * math.log(p) + (1 - o) * math.log(1 - p)),
        "cov80": 1.0 if p10 <= y <= p90 else 0.0, "pit": pit, "pit_in80": 1.0 if 0.1 <= pit <= 0.9 else 0.0,
    }


def score_rows(rows, seed=20261011):
    """rows: list of {cluster, stat, y, pmfs: {model: pmf}, sub: {...}}. Returns metric arrays."""
    rng = random.Random(seed)
    out = []
    for r in rows:
        m = {name: row_metrics(pmf, r["y"], r["stat"], rng) for name, pmf in r["pmfs"].items()}
        out.append({"cluster": r["cluster"], "stat": r["stat"], "y": r["y"], "sub": r.get("sub", {}), "m": m})
    return out


def summarize(scored, models):
    res = {}
    n = len(scored)
    if not n:
        return {"n": 0}
    res["n"] = n
    res["n_games"] = len({s["cluster"] for s in scored})
    res["mean_actual"] = sum(s["y"] for s in scored) / n
    for mname in models:
        ms = [s["m"][mname] for s in scored]
        d = {
            "mean_forecast": sum(x["mean"] for x in ms) / n,
            "bias": sum(x["err"] for x in ms) / n,
            "mae": sum(x["abs"] for x in ms) / n,
            "rmse": math.sqrt(sum(x["sq"] for x in ms) / n),
            "crps": sum(x["crps"] for x in ms) / n,
            "brier": sum(x["brier"] for x in ms) / n,
            "logloss": sum(x["logloss"] for x in ms) / n,
            "mean_p_threshold": sum(x["p"] for x in ms) / n,
            "obs_rate_threshold": sum(x["o"] for x in ms) / n,
            "cov80_p10_p90": sum(x["cov80"] for x in ms) / n,
            "pit_in_10_90": sum(x["pit_in80"] for x in ms) / n,
            "pit_deciles": _pit_hist([x["pit"] for x in ms]),
            "reliability": _reliability([(x["p"], x["o"]) for x in ms]),
        }
        res[mname] = d
    return res


def _pit_hist(v):
    h = [0] * 10
    for x in v:
        h[min(9, int(x * 10))] += 1
    n = len(v)
    return [round(c / n, 4) for c in h]


def _reliability(po):
    bins = [[0.0, 0.0, 0] for _ in range(10)]
    for p, o in po:
        b = bins[min(9, int(p * 10))]
        b[0] += p
        b[1] += o
        b[2] += 1
    out, ece, n = [], 0.0, len(po)
    for i, (sp, so, c) in enumerate(bins):
        if c:
            out.append({"bin": f"[{i / 10:.1f},{(i + 1) / 10:.1f})", "n": c, "mean_p": round(sp / c, 4),
                        "obs": round(so / c, 4)})
            ece += c / n * abs(sp / c - so / c)
    return {"bins": out, "ece": round(ece, 5)}


def paired_bootstrap(scored, a, b, metrics=PRIMARY + ("err",), reps=1000, seed=20261011):
    """Challenger b minus champion a; per metric mean diff + game-clustered 95% percentile CI.
    For 'err' the statistic is |bias_b| - |bias_a|."""
    clusters = {}
    for s in scored:
        clusters.setdefault(s["cluster"], []).append(s)
    names = sorted(clusters)
    C = len(names)
    K = len(metrics)
    sums_a = np.zeros((C, K))
    sums_b = np.zeros((C, K))
    cnt = np.zeros(C)
    for i, c in enumerate(names):
        for s in clusters[c]:
            cnt[i] += 1
            for j, mt in enumerate(metrics):
                sums_a[i, j] += s["m"][a][mt]
                sums_b[i, j] += s["m"][b][mt]
    rng = np.random.default_rng(seed)
    out = {}

    def stat(ix):
        n = cnt[ix].sum()
        ma = sums_a[ix].sum(axis=0) / n
        mb = sums_b[ix].sum(axis=0) / n
        d = mb - ma
        for j, mt in enumerate(metrics):
            if mt == "err":
                d[j] = abs(mb[j]) - abs(ma[j])
        return d

    point = stat(np.arange(C))
    if C < 3:
        return {mt: {"delta": float(point[j]), "ci95": None} for j, mt in enumerate(metrics)}
    draws = np.array([stat(rng.integers(0, C, C)) for _ in range(reps)])
    for j, mt in enumerate(metrics):
        lo, hi = np.percentile(draws[:, j], [2.5, 97.5])
        key = "abs_bias" if mt == "err" else mt
        out[key] = {"delta": round(float(point[j]), 6), "ci95": [round(float(lo), 6), round(float(hi), 6)],
                    "ci_excludes_0": bool(lo > 0 or hi < 0)}
    out["n_games"] = C
    out["n"] = int(cnt.sum())
    return out
