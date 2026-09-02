#!/usr/bin/env python3
"""MLB-ALPHA-0001 Section D: calibration study for the clustered tests.

A hypothesis test may not be adopted on the strength of its name. This
script measures the ACTUAL size (false-positive rate) of both clustered
tests under a TRUE NULL, on synthetic data whose payoff shape matches the
hardest real cell in this program (C01: ~95c entries, so ~+0.5 on a win
and ~-10.0 on a loss, with 1-3 correlated contracts per game cluster).

Finding, reproducible with the fixed seed below: both the null-centered
cluster bootstrap and the restricted wild cluster bootstrap are
ANTI-CONSERVATIVE for this payoff shape -- nominal 0.05 rejects ~0.08 of
the time, nominal 0.10 rejects ~0.13. The cause is the extreme skew of a
98%-small-win / 2%-catastrophic-loss ratio estimator: the resampling
distribution under-represents the rare large-loss tail.

Consequences, applied in family_a_discovery.py:
  * raw bootstrap p-values are reported as preregistered, AND
  * a conservative size-corrected p (p * SIZE_INFLATION, capped at 1) is
    reported alongside, with BH-FDR run on BOTH. Only cells surviving the
    conservative version are treated as real.
This adjustment was fixed BEFORE re-scoring any cell, and it can only
ever REMOVE survivors, never add them.

RESEARCH ONLY.
"""

import json
import os
import sys

import numpy as np

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
ART = os.path.join(REPO, "data", "edgelab", "research_artifacts", "mlb_alpha_0001")

SEED = 20260903
N_SIMS = 1000
B = 2000
N_GAMES = 200
WIN_PL, LOSS_PL = 0.52, -9.98          # a ~95c entry at a $10 order
P_BREAKEVEN = -LOSS_PL / (-LOSS_PL + WIN_PL)   # exact zero-EV win rate


def simulate_null_cell(n_games, p_win, rng):
    net, cash = {}, {}
    for g in range(n_games):
        k = int(rng.integers(1, 4))
        shared = rng.random() < p_win
        outs = np.where(rng.random(k) < 0.5, shared, rng.random(k) < p_win)
        net[g] = float(np.sum(np.where(outs, WIN_PL, LOSS_PL)))
        cash[g] = float(-LOSS_PL * k)
    return net, cash


def two_tests(net_by, cash_by, rng, B=B):
    g = list(net_by)
    net = np.array([net_by[x] for x in g], dtype=float)
    cash = np.array([cash_by[x] for x in g], dtype=float)
    n, tot = len(g), cash.sum()
    roi = net.sum() / tot
    net0 = net - roi * cash
    i0 = rng.integers(0, n, size=(B, n))
    r0 = net0[i0].sum(1) / np.maximum(cash[i0].sum(1), 1e-12)
    p_null = (1 + np.sum(np.abs(r0) >= abs(roi))) / (B + 1)
    v = rng.choice(np.array([-1.0, 1.0]), size=(B, n))
    rw = (v * net).sum(1) / tot
    p_wild = (1 + np.sum(np.abs(rw) >= abs(roi))) / (B + 1)
    return float(p_null), float(p_wild)


def main():
    rng = np.random.default_rng(SEED)
    ps = np.array([two_tests(*simulate_null_cell(N_GAMES, P_BREAKEVEN, rng), rng)
                   for _ in range(N_SIMS)])
    out = {
        "program": "MLB-ALPHA-0001",
        "section": "D_inference_calibration",
        "seed": SEED, "sims": N_SIMS, "bootstrapIterations": B,
        "gamesPerSim": N_GAMES,
        "payoffShape": {"winPL": WIN_PL, "lossPL": LOSS_PL,
                        "breakevenWinRate": round(P_BREAKEVEN, 6)},
        "measuredSize": {},
        "note": ("Both clustered tests are anti-conservative for this extreme "
                 "payoff skew. family_a_discovery.py therefore reports a "
                 "conservative size-corrected p alongside the raw p and runs "
                 "BH-FDR on both."),
    }
    for i, name in enumerate(["nullCenteredClusterBootstrap", "wildClusterBootstrap"]):
        r05 = float((ps[:, i] < 0.05).mean())
        r10 = float((ps[:, i] < 0.10).mean())
        out["measuredSize"][name] = {
            "nominal0.05": round(r05, 4),
            "nominal0.10": round(r10, 4),
            "inflationAt0.05": round(r05 / 0.05, 3),
            "inflationAt0.10": round(r10 / 0.10, 3),
            "mcStdErr0.05": round(float(np.sqrt(r05 * (1 - r05) / N_SIMS)), 4),
        }
        print("%-32s size@0.05=%.3f (x%.2f)  size@0.10=%.3f (x%.2f)"
              % (name, r05, r05 / 0.05, r10, r10 / 0.10))
    prim = out["measuredSize"]["nullCenteredClusterBootstrap"]
    inflation = max(prim["inflationAt0.05"], prim["inflationAt0.10"])
    out["adoptedSizeInflationFactor"] = inflation
    os.makedirs(ART, exist_ok=True)
    path = os.path.join(ART, "inference_calibration_study.json")
    with open(path, "w") as fh:
        json.dump(out, fh, indent=2, sort_keys=True)
        fh.write("\n")
    print("adopted conservative size-inflation factor:", inflation)
    print("wrote", path)


if __name__ == "__main__":
    sys.exit(main())
