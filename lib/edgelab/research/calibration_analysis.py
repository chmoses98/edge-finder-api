"""
lib/edgelab/research/calibration_analysis.py
=============================================
Shared metrics / resampling / calibration-map helpers for the MLB
probability-calibration research programme. RESEARCH ONLY -- nothing in
here is imported by production code.

Conventions
-----------
* probabilities are floats in (0, 1); outcomes are 0/1 ints.
* every CI is a GAME-clustered bootstrap (whole games resampled with
  replacement; correlated ladder rungs / sibling contracts move together).
* calibration maps are pure functions of parameters; fitting helpers
  return parameter dicts and never touch the rows they were fit on.
"""
import gzip
import json
import math
import os

import numpy as np
import pandas as pd

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
DATASET_DIR = os.path.join(_ROOT, "data", "edgelab", "research_artifacts", "calibration_research")
ROWS_PATH = os.path.join(DATASET_DIR, "pit_rows.jsonl.gz")
GAMES_PATH = os.path.join(DATASET_DIR, "pit_games.jsonl.gz")

EPS = 1e-4
DEFAULT_SEED = 20260904


# ------------------------------------------------------------------ loading

def load_rows(path=ROWS_PATH):
    df = pd.read_json(path, lines=True)
    df["capturedAt"] = pd.to_datetime(df["capturedAt"], utc=True)
    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
    for c in ("mid", "closeMid", "yesBid", "yesAsk"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["marketP"] = df["mid"] / 100.0
    df["closeP"] = df["closeMid"] / 100.0
    df["askP"] = df["yesAsk"] / 100.0
    df["bidP"] = df["yesBid"] / 100.0
    return df


def primary_rows(df, engine="B", max_date=None, min_date=None, require_close=False):
    """
    The primary analysis unit: one row per (ticker, side) = the LAST pregame
    capture (closest to first pitch), restricted to rows with a settled
    outcome and a simultaneous two-sided quote.
    """
    d = df[(df["engine"] == engine) & df["pregameAtCapture"] & df["outcome"].notna() & df["marketP"].notna()]
    d = d[(d["marketP"] > 0) & (d["marketP"] < 1)]
    if require_close:
        d = d[d["closeP"].notna() & (d["closeP"] > 0) & (d["closeP"] < 1)]
    if max_date:
        d = d[d["date"] <= max_date]
    if min_date:
        d = d[d["date"] >= min_date]
    d = d.sort_values("capturedAt").groupby(["ticker", "side"], as_index=False).tail(1)
    d = d.copy()
    d["outcome"] = d["outcome"].astype(int)
    return d.reset_index(drop=True)


# ------------------------------------------------------------------ metrics

def clip(p):
    return np.clip(np.asarray(p, dtype=float), EPS, 1 - EPS)


def logit(p):
    p = clip(p)
    return np.log(p / (1 - p))


def sigmoid(z):
    return 1.0 / (1.0 + np.exp(-np.asarray(z, dtype=float)))


def brier(p, y):
    p, y = clip(p), np.asarray(y, dtype=float)
    return float(np.mean((p - y) ** 2))


def log_loss(p, y):
    p, y = clip(p), np.asarray(y, dtype=float)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def ece(p, y, bins=10):
    p, y = clip(p), np.asarray(y, dtype=float)
    edges = np.linspace(0, 1, bins + 1)
    idx = np.clip(np.digitize(p, edges) - 1, 0, bins - 1)
    tot = 0.0
    for b in range(bins):
        m = idx == b
        if m.any():
            tot += m.mean() * abs(p[m].mean() - y[m].mean())
    return float(tot)


def murphy_decomposition(p, y, bins=10):
    """Brier = reliability - resolution + uncertainty (binned)."""
    p, y = clip(p), np.asarray(y, dtype=float)
    edges = np.linspace(0, 1, bins + 1)
    idx = np.clip(np.digitize(p, edges) - 1, 0, bins - 1)
    ybar = y.mean()
    rel = res = 0.0
    n = len(p)
    for b in range(bins):
        m = idx == b
        if m.any():
            rel += m.sum() / n * (p[m].mean() - y[m].mean()) ** 2
            res += m.sum() / n * (y[m].mean() - ybar) ** 2
    return {"reliability": float(rel), "resolution": float(res), "uncertainty": float(ybar * (1 - ybar)), "brier": brier(p, y)}


def calibration_slope_intercept(p, y):
    """Logistic regression of y on logit(p): y ~ sigmoid(a + b*logit(p)). Returns (a, b)."""
    x = logit(p)
    a, b = fit_platt_params(x, np.asarray(y, dtype=float))
    return float(a), float(b)


def reliability_table(p, y, edges=(0, .1, .2, .3, .4, .5, .6, .7, .8, .9, 1.0)):
    p, y = clip(p), np.asarray(y, dtype=float)
    edges = list(edges)
    out = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = (p >= lo) & (p < hi) if hi < 1 else (p >= lo) & (p <= hi)
        if m.sum() == 0:
            out.append({"lo": lo, "hi": hi, "n": 0})
            continue
        out.append({"lo": lo, "hi": hi, "n": int(m.sum()), "meanP": float(p[m].mean()), "obsRate": float(y[m].mean()),
                    "bias": float(p[m].mean() - y[m].mean())})
    return out


def summary(p, y):
    return {"n": int(len(p)), "brier": brier(p, y), "logLoss": log_loss(p, y), "ece": ece(p, y),
            "meanP": float(np.mean(clip(p))), "baseRate": float(np.mean(y))}


# ------------------------------------------------------------ resampling

def game_bootstrap(df, stat_fn, n_boot=500, seed=DEFAULT_SEED, cluster="gameId"):
    """
    stat_fn(sub_df) -> float. Resamples whole clusters with replacement.
    Returns (point, lo95, hi95, p_two_sided_vs_zero).
    """
    rng = np.random.default_rng(seed)
    groups = {k: v for k, v in df.groupby(cluster)}
    keys = np.array(list(groups.keys()), dtype=object)
    point = float(stat_fn(df))
    vals = []
    for _ in range(n_boot):
        draw = rng.choice(keys, size=len(keys), replace=True)
        sub = pd.concat([groups[k] for k in draw], ignore_index=True)
        vals.append(stat_fn(sub))
    vals = np.array(vals, dtype=float)
    lo, hi = np.percentile(vals, [2.5, 97.5])
    p = float(min(1.0, 2 * min((vals <= 0).mean(), (vals >= 0).mean())))
    return point, float(lo), float(hi), p


def paired_brier_delta(df, col_a, col_b):
    """mean Brier(col_a) - Brier(col_b) on the same rows (negative => a better)."""
    y = df["outcome"].values.astype(float)
    return brier(df[col_a].values, y) - brier(df[col_b].values, y)


def fast_cluster_bootstrap_mean(values, clusters, n_boot=1000, seed=DEFAULT_SEED):
    """
    Game-clustered bootstrap of a MEAN over rows: per-cluster sums and counts are
    resampled with replacement, so the statistic is the ratio-of-sums estimator of
    the row mean.  Returns (point, lo95, hi95, p_two_sided_vs_zero).
    """
    v = np.asarray(values, dtype=float)
    c = np.asarray(clusters)
    keys, inv = np.unique(c, return_inverse=True)
    sums = np.bincount(inv, weights=v, minlength=len(keys))
    cnts = np.bincount(inv, minlength=len(keys)).astype(float)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(keys), size=(n_boot, len(keys)))
    bs = sums[idx].sum(axis=1) / cnts[idx].sum(axis=1)
    point = float(v.mean())
    lo, hi = np.percentile(bs, [2.5, 97.5])
    p = float(min(1.0, 2 * min((bs <= 0).mean(), (bs >= 0).mean())))
    return point, float(lo), float(hi), p


def paired_delta_ci(df, col_a, col_b, n_boot=1000, seed=DEFAULT_SEED, cluster="gameId"):
    """Game-clustered CI for mean squared-error difference (col_a - col_b)."""
    y = df["outcome"].values.astype(float)
    d = (clip(df[col_a].values) - y) ** 2 - (clip(df[col_b].values) - y) ** 2
    return fast_cluster_bootstrap_mean(d, df[cluster].values, n_boot=n_boot, seed=seed)


# ------------------------------------------------------ calibration maps

def fit_platt_params(x, y, l2=0.0, w=None, max_iter=100):
    """
    Damped-Newton logistic fit y ~ sigmoid(a + b*x). x is already a logit.
    l2 penalises (b-1)^2 and a^2 weakly (toward identity) when > 0.
    """
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    w = np.ones_like(y) if w is None else np.asarray(w, dtype=float)
    a, b = 0.0, 1.0
    X = np.column_stack([np.ones_like(x), x])

    def nll(a_, b_):
        z = a_ + b_ * x
        p = sigmoid(z)
        p = np.clip(p, 1e-12, 1 - 1e-12)
        return -np.sum(w * (y * np.log(p) + (1 - y) * np.log(1 - p))) + l2 * (a_ ** 2 + (b_ - 1) ** 2)

    cur = nll(a, b)
    for _ in range(max_iter):
        p = sigmoid(a + b * x)
        g = X.T @ (w * (p - y)) + 2 * l2 * np.array([a, b - 1])
        H = (X * (w * p * (1 - p))[:, None]).T @ X + 2 * l2 * np.eye(2)
        try:
            step = np.linalg.solve(H, g)
        except np.linalg.LinAlgError:
            break
        t = 1.0
        improved = False
        for _ in range(30):
            na, nb = a - t * step[0], b - t * step[1]
            v = nll(na, nb)
            if v <= cur:
                a, b, cur, improved = na, nb, v, True
                break
            t *= 0.5
        if not improved or np.abs(step).max() * t < 1e-8:
            break
    return a, b


def apply_platt(p, a, b):
    return sigmoid(a + b * logit(p))


def fit_beta_calibration(p, y, w=None):
    """Beta calibration (Kull et al. 2017): y ~ sigmoid(c + a*log(p) - b*log(1-p)). Returns (c, a, b)."""
    p = clip(p)
    y = np.asarray(y, dtype=float)
    w = np.ones_like(y) if w is None else np.asarray(w, dtype=float)
    X = np.column_stack([np.ones_like(p), np.log(p), -np.log(1 - p)])
    theta = np.array([0.0, 1.0, 1.0])

    def nll(th):
        z = X @ th
        q = np.clip(sigmoid(z), 1e-12, 1 - 1e-12)
        return -np.sum(w * (y * np.log(q) + (1 - y) * np.log(1 - q)))

    cur = nll(theta)
    for _ in range(100):
        q = sigmoid(X @ theta)
        g = X.T @ (w * (q - y))
        H = (X * (w * q * (1 - q))[:, None]).T @ X + 1e-6 * np.eye(3)
        step = np.linalg.solve(H, g)
        t = 1.0
        improved = False
        for _ in range(30):
            nt = theta - t * step
            v = nll(nt)
            if v <= cur:
                theta, cur, improved = nt, v, True
                break
            t *= 0.5
        if not improved or np.abs(step).max() * t < 1e-8:
            break
    return tuple(float(v) for v in theta)


def apply_beta_calibration(p, c, a, b):
    p = clip(p)
    return sigmoid(c + a * np.log(p) - b * np.log(1 - p))


def fit_isotonic(p, y):
    from sklearn.isotonic import IsotonicRegression
    iso = IsotonicRegression(y_min=EPS, y_max=1 - EPS, out_of_bounds="clip")
    iso.fit(clip(p), np.asarray(y, dtype=float))
    return iso


def fit_market_blend(pm, pk, y, l2=0.0):
    """y ~ sigmoid(c + w_m*logit(pm) + w_k*logit(pk)). Returns (c, w_m, w_k)."""
    xm, xk = logit(pm), logit(pk)
    y = np.asarray(y, dtype=float)
    X = np.column_stack([np.ones_like(xm), xm, xk])
    theta = np.array([0.0, 0.0, 1.0])

    def nll(th):
        q = np.clip(sigmoid(X @ th), 1e-12, 1 - 1e-12)
        return -np.sum(y * np.log(q) + (1 - y) * np.log(1 - q)) + l2 * np.sum((th - np.array([0, 0, 1])) ** 2)

    cur = nll(theta)
    for _ in range(100):
        q = sigmoid(X @ theta)
        g = X.T @ (q - y) + 2 * l2 * (theta - np.array([0, 0, 1]))
        H = (X * (q * (1 - q))[:, None]).T @ X + 2 * l2 * np.eye(3) + 1e-9 * np.eye(3)
        step = np.linalg.solve(H, g)
        t = 1.0
        improved = False
        for _ in range(30):
            nt = theta - t * step
            v = nll(nt)
            if v <= cur:
                theta, cur, improved = nt, v, True
                break
            t *= 0.5
        if not improved or np.abs(step).max() * t < 1e-8:
            break
    return tuple(float(v) for v in theta)


def apply_market_blend(pm, pk, c, wm, wk):
    return sigmoid(c + wm * logit(pm) + wk * logit(pk))


# ------------------------------------------------------------- utilities

def write_json(obj, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(obj, f, indent=2, sort_keys=True, default=_json_default)


def _json_default(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, pd.Timestamp):
        return o.isoformat()
    return str(o)


def md_table(rows, cols, fmt=None):
    fmt = fmt or {}
    out = ["| " + " | ".join(cols) + " |", "|" + "|".join(["---"] * len(cols)) + "|"]
    for r in rows:
        cells = []
        for c in cols:
            v = r.get(c) if isinstance(r, dict) else getattr(r, c, None)
            if v is None or (isinstance(v, float) and math.isnan(v)):
                cells.append("")
            elif c in fmt:
                cells.append(fmt[c].format(v))
            elif isinstance(v, float):
                cells.append(f"{v:.4f}")
            else:
                cells.append(str(v))
        out.append("| " + " | ".join(cells) + " |")
    return "\n".join(out)
