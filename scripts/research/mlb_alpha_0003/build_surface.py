#!/usr/bin/env python3
"""Tabulate (m, r) -> (p_home, P(total >= k)) for the frozen RunEnvSim params. Deterministic (fixed seed)."""
import gzip, json, os, sys
from multiprocessing import Pool

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, REPO)

N_DRAWS = 150_000
SEED = 20261027
MS = [5.0 + 0.25 * i for i in range(37)]            # 5.0 .. 14.0
RS = [0.60 + 0.04 * j for j in range(26)]           # 0.60 .. 1.60
KMAX = 40


def _row(i):
    import numpy as np
    from lib.edgelab.mlb_alpha import sim
    d = sim.Draws(n=N_DRAWS, seed=SEED)
    m = MS[i]
    ph, pg = [], []
    for r in RS:
        la = m / (1.0 + r); lh = m - la
        res = sim.run(la, lh, d)
        a, h = res.away.sum(axis=1), res.home.sum(axis=1)
        t = a + h
        ph.append(float((h > a).mean()))
        pg.append([float((t >= k).mean()) for k in range(KMAX + 1)])
    return i, ph, pg


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(REPO, "data/edgelab/research_artifacts/mlb_alpha_0003/anchor_surface_v1.json.gz")
    with Pool(4) as pool:
        rows = sorted(pool.map(_row, range(len(MS))))
    doc = {"version": "RunEnvSim_v1", "nDraws": N_DRAWS, "seed": SEED, "ms": MS, "rs": RS,
           "pHome": [r[1] for r in rows], "pGe": [r[2] for r in rows]}
    with gzip.open(out, "wt") as fh:
        json.dump(doc, fh)
    print("wrote", out, os.path.getsize(out))
