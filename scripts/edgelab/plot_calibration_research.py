#!/usr/bin/env python3
"""
scripts/edgelab/plot_calibration_research.py
=============================================
Reliability diagrams for the calibration research report. RESEARCH ONLY.
Reads walkforward_predictions.jsonl.gz (out-of-sample rows) and writes PNGs
under data/edgelab/research_artifacts/calibration_research/plots/.
"""
import os
import sys

import numpy as np
import pandas as pd

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import matplotlib  # noqa: E402
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from lib.edgelab.research import calibration_analysis as ca  # noqa: E402

PLOT_DIR = os.path.join(ca.DATASET_DIR, "plots")
COLS = [("modelP", "raw production (Poisson)", "#c0392b"), ("C7_nb_structural", "frozen NB (no fit)", "#e67e22"),
        ("C11_nb_mean_shift_family_platt", "NB + shift + family Platt (walk-forward)", "#2980b9"),
        ("C5_global_model_market_blend", "model/market blend (walk-forward)", "#8e44ad"), ("marketP", "Kalshi mid", "#27ae60")]


def reliability_points(p, y, bins=10):
    p, y = ca.clip(p), np.asarray(y, dtype=float)
    edges = np.linspace(0, 1, bins + 1)
    idx = np.clip(np.digitize(p, edges) - 1, 0, bins - 1)
    xs, ys, ns = [], [], []
    for b in range(bins):
        m = idx == b
        if m.sum() >= 15:
            xs.append(p[m].mean())
            ys.append(y[m].mean())
            ns.append(int(m.sum()))
    return np.array(xs), np.array(ys), np.array(ns)


def plot_family(d, fam, path):
    fig, ax = plt.subplots(figsize=(6, 6))
    ax.plot([0, 1], [0, 1], "k--", lw=1, label="perfect calibration")
    y = d["outcome"].values
    for col, label, color in COLS:
        if col not in d or d[col].isna().all():
            continue
        xs, ys, ns = reliability_points(d[col].values, y)
        ax.plot(xs, ys, "o-", color=color, label=f"{label} (Brier {ca.brier(d[col].values, y):.4f})", ms=4)
    ax.set_xlabel("forecast probability")
    ax.set_ylabel("observed frequency")
    ax.set_title(f"{fam}: out-of-sample reliability (n={len(d)}, games={d['gameId'].nunique()})")
    ax.legend(fontsize=7, loc="upper left")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def plot_disagreement(d, path):
    d = d.copy()
    d["dis"] = d["modelP"] - d["marketP"]
    edges = [-1, -.2, -.1, -.05, -.025, 0, .025, .05, .1, .2, 1]
    d["b"] = pd.cut(d["dis"], edges)
    g = d.groupby("b", observed=True).agg(n=("outcome", "size"), model=("modelP", "mean"), market=("marketP", "mean"), realized=("outcome", "mean"))
    fig, ax = plt.subplots(figsize=(8, 4.5))
    x = np.arange(len(g))
    ax.plot(x, g["model"], "o-", color="#c0392b", label="mean model p")
    ax.plot(x, g["market"], "s-", color="#27ae60", label="mean Kalshi mid")
    ax.plot(x, g["realized"], "^-", color="black", label="realized frequency")
    ax.set_xticks(x)
    ax.set_xticklabels([str(i) for i in g.index], rotation=45, fontsize=7)
    ax.set_xlabel("model minus market (probability points)")
    ax.set_ylabel("probability")
    ax.set_title("When the model disagrees with Kalshi, outcomes track Kalshi")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def main():
    os.makedirs(PLOT_DIR, exist_ok=True)
    d = pd.read_json(os.path.join(ca.DATASET_DIR, "walkforward_predictions.jsonl.gz"), lines=True)
    d = d[d["outcome"].notna()].copy()
    d["outcome"] = d["outcome"].astype(int)
    plot_family(d, "ALL families", os.path.join(PLOT_DIR, "reliability_all.png"))
    for fam, g in d.groupby("family"):
        plot_family(g, fam, os.path.join(PLOT_DIR, f"reliability_{fam}.png"))
    plot_disagreement(d, os.path.join(PLOT_DIR, "disagreement.png"))
    print(f"wrote plots to {PLOT_DIR}")


if __name__ == "__main__":
    main()
