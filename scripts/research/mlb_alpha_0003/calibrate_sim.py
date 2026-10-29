#!/usr/bin/env python3
"""
Calibrate the RunEnvSim structural constants (q, league heterogeneity, home edge)
to 2022-2025 MLB box scores. No Kalshi price and no 2026 outcome is used here.

Targets (pooled 2022-2025, 9,719 games, data/research_cache/batting_backtest):
  Var(away runs), Var(home runs), P(team scores 0), P(home win),
  P(extras) from starter_workload box scores (home pitchers' outs > 27)
Free parameters: q (run-size tail), sigma (cross-game log-sd of each team's lam),
  hfa (home lam multiplier), extra_nu.
"""
import gzip, json, os, sys, itertools, math

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, REPO)


def load_empirical(repo=REPO, years=(2022, 2023, 2024, 2025)):
    A, H, ex = [], [], []
    for y in years:
        seen = set()
        for l in gzip.open(os.path.join(repo, "data/research_cache/batting_backtest/%d/boxscores.jsonl.gz" % y), "rt"):
            d = json.loads(l)
            if d["gamePk"] in seen:
                continue
            seen.add(d["gamePk"])
            try:
                A.append(d["awayBatting"]["runs"]); H.append(d["homeBatting"]["runs"])
            except KeyError:
                pass
        seen = set()
        for l in gzip.open(os.path.join(repo, "data/research_cache/starter_workload/%d/boxscores.jsonl.gz" % y), "rt"):
            d = json.loads(l)
            if d["gamePk"] in seen:
                continue
            seen.add(d["gamePk"])
            ex.append(sum(p.get("outs", 0) for p in d["homePitchers"]) > 27)
    import numpy as np
    A, H, ex = np.array(A), np.array(H), np.array(ex)
    return {
        "n": len(A), "meanA": A.mean(), "meanH": H.mean(), "varA": A.var(), "varH": H.var(),
        "p0A": (A == 0).mean(), "p0H": (H == 0).mean(), "homeWin": (H > A).mean(), "extras": ex.mean(),
        "pmfA": [float((A == k).mean()) for k in range(12)], "pmfH": [float((H == k).mean()) for k in range(12)],
    }


def simulate_mix(q, sigma, hfa, extra_nu, base=4.43, n=200_000, seed=7):
    """League-mix simulation: every game draws its own (lam_away, lam_home), lognormal, mean `base`."""
    import numpy as np
    from lib.edgelab.mlb_alpha import sim
    rng = np.random.default_rng(seed)
    z = rng.standard_normal((2, n))
    lam_a = base * np.exp(sigma * z[0] - sigma ** 2 / 2)
    lam_h = base * hfa * np.exp(sigma * z[1] - sigma ** 2 / 2)
    d = sim.Draws(n=n, seed=seed + 1, params=sim.SimParams(q=q, extra_nu=extra_nu))
    r = sim.run(lam_a, lam_h, d)
    A, H = r.away.sum(axis=1), r.home.sum(axis=1)
    return {"meanA": A.mean(), "meanH": H.mean(), "varA": A.var(), "varH": H.var(), "p0A": (A == 0).mean(),
            "p0H": (H == 0).mean(), "homeWin": (H > A).mean(), "extras": r.extras.mean(),
            "pmfA": [float((A == k).mean()) for k in range(12)], "pmfH": [float((H == k).mean()) for k in range(12)]}


TARGET_KEYS = (("varA", 0.25), ("varH", 0.25), ("p0A", 0.004), ("p0H", 0.004), ("homeWin", 0.005),
               ("extras", 0.004), ("meanA", 0.05), ("meanH", 0.05))


def loss(sim_stats, emp):
    return sum(((sim_stats[k] - emp[k]) / sd) ** 2 for k, sd in TARGET_KEYS)


def fit(emp, n=120_000):
    best = None
    grid = list(itertools.product((0.30, 0.33, 0.36, 0.39, 0.42), (0.10, 0.16, 0.22, 0.28), (1.00, 1.03, 1.06),
                                  (0.92,)))
    for q, sg, hfa, en in grid:
        st = simulate_mix(q, sg, hfa, en, n=n)
        l = loss(st, emp)
        if best is None or l < best[0]:
            best = (l, dict(q=q, sigma=sg, hfa=hfa, extra_nu=en), st)
    return best


if __name__ == "__main__":
    emp = load_empirical()
    l, params, st = fit(emp)
    out = {"empirical": emp, "fit": params, "loss": l, "simulated": st}
    print(json.dumps({k: out[k] for k in ("fit", "loss")}, indent=1))
    for k, _ in TARGET_KEYS:
        print("%-8s empirical %.4f  simulated %.4f" % (k, emp[k], st[k]))
    if len(sys.argv) > 1:
        json.dump(out, open(sys.argv[1], "w"), indent=1)
