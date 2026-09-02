#!/usr/bin/env python3
"""MLB-ALPHA-0002 Family E: does our production model add information
AFTER conditioning on the market?

Never `model - Kalshi` as evidence. The test is incremental: with
settlement as the outcome, fit (walk-forward by date, game-clustered)

    logit P(YES) = a + b * logit(kalshi_mid) [+ c * logit(sharp)] + d * logit(model)

and ask whether d is reliably > 0 out-of-sample and whether adding the
model lowers out-of-sample log loss / Brier versus the market-only model.
Also: does (model - kalshi) predict the SUBSEQUENT Kalshi fair-mid move
to close (price-discovery evidence) and executable CLV on the model's
side?

Data: data/edgelab/model_evaluations (modelFairProbability with
provenance.capturedAt), joined AS-OF to the Kalshi observation panel
(pit_kalshi_panel: the latest model evaluation captured <= the Kalshi
observation time; evaluations after t are never used) and to the sharp
panel (latest Pinnacle vig-free prob captured <= t). Walk-forward:
predict date d using coefficients fit on dates < d (first 6 dates are
burn-in). RESEARCH ONLY.
"""

import glob
import gzip
import json
import math
import os
import sys
from collections import defaultdict

import numpy as np

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, REPO)
ART = os.path.join(REPO, "data", "edgelab", "research_artifacts", "mlb_alpha_0002")
EDGELAB = os.path.join(REPO, "data", "edgelab")
OUT = os.path.join(ART, "family_e_results.json")
SEED = 20260902
BURN_IN_DATES = 6

# model_evaluations family labels -> panel families / side semantics
FAM_MAP = {"game_result": "game_result", "ML_Home": "game_result", "ML_Away": "game_result",
           "team_total": "team_total", "KXMLBTEAMTOTAL": "team_total", "game_total": "game_total",
           "winning_margin": "winning_margin", "inning_result": "inning_result",
           "F5_ML_Home": "inning_result", "F5_ML_Away": "inning_result",
           "inning_total": "inning_total", "pitcher_strikeouts": "pitcher_strikeouts",
           "pitcher_outs": "pitcher_outs", "NRFI": "first_inning_run", "KXMLBRFI": "first_inning_run"}


def iter_jsonl(path):
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


def logit(p):
    p = min(max(p, 1e-4), 1 - 1e-4)
    return math.log(p / (1 - p))


def load_model_evals():
    """ticker -> sorted [(capturedAt, modelProb, side)] restricted to rows with a probability."""
    out = defaultdict(list)
    for p in sorted(glob.glob(os.path.join(EDGELAB, "model_evaluations", "2026-08-*.jsonl*"))):
        for r in iter_jsonl(p):
            mp = r.get("modelFairProbability")
            t = r.get("marketTicker")
            if mp is None or not t:
                continue
            cap = (r.get("provenance") or {}).get("capturedAt") or r.get("createdAt")
            if not cap:
                continue
            out[t].append((cap, float(mp), r.get("side") or r.get("selection"), r.get("marketFamily")))
    for t in out:
        out[t].sort()
    return out


def load_sharp():
    """gameKey -> sorted [(capturedAt, pinnacleHomeVigFree)]"""
    out = defaultdict(list)
    p = os.path.join(ART, "pit_sharp_panel.jsonl.gz")
    if not os.path.exists(p):
        return out
    for r in iter_jsonl(p):
        if r.get("book") == "pinnacle" and r.get("gameKey") and r.get("mlHomeVigFree") is not None:
            out[r["gameKey"]].append((r["capturedAt"], r["mlHomeVigFree"]))
    for k in out:
        out[k].sort()
    return out


def asof(lst, ts):
    best = None
    for item in lst:
        if item[0] <= ts:
            best = item
        else:
            break
    return best


def fit_logistic(X, y, l2=1.0, iters=200):
    """Ridge-penalised logistic regression by Newton steps (intercept unpenalised)."""
    n, k = X.shape
    Xb = np.hstack([np.ones((n, 1)), X])
    w = np.zeros(k + 1)
    pen = np.eye(k + 1) * l2; pen[0, 0] = 0.0
    for _ in range(iters):
        z = Xb @ w
        p = 1.0 / (1.0 + np.exp(-z))
        g = Xb.T @ (p - y) + pen @ w
        W = p * (1 - p)
        H = (Xb * W[:, None]).T @ Xb + pen
        step = np.linalg.solve(H, g)
        w -= step
        if np.max(np.abs(step)) < 1e-8:
            break
    return w


def predict(w, X):
    z = w[0] + X @ w[1:]
    return 1.0 / (1.0 + np.exp(-z))


def cluster_boot_mean(vals, clusters, B=2000, seed=SEED):
    rng = np.random.default_rng(seed)
    groups = defaultdict(list)
    for v, c in zip(vals, clusters):
        groups[c].append(v)
    keys = list(groups)
    sums = np.array([sum(groups[k]) for k in keys]); cnts = np.array([len(groups[k]) for k in keys])
    idx = rng.integers(0, len(keys), size=(B, len(keys)))
    means = sums[idx].sum(1) / cnts[idx].sum(1)
    return float(np.mean(vals)), [float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))]


def main():
    evals = load_model_evals()
    sharp = load_sharp()
    rows = []
    for r in iter_jsonl(os.path.join(ART, "pit_kalshi_panel.jsonl.gz")):
        ev = asof(evals.get(r["marketTicker"], []), r["capturedAt"])
        if ev is None:
            continue
        fam = FAM_MAP.get(ev[3], ev[3])
        # model probability is for the YES side of the ticker unless side says NO
        mp = ev[1]
        if ev[2] and str(ev[2]).upper() == "NO":
            mp = 1.0 - mp
        sp = asof(sharp.get(r["gameKey"], []), r["capturedAt"])
        sharp_p = None
        if sp is not None and fam == "game_result":
            sharp_p = sp[1] if r.get("team") == r.get("homeTeam") else 1.0 - sp[1]
        rows.append({"fam": fam, "date": r["gameDate"], "game": r["gameKey"], "ticker": r["marketTicker"],
                     "k": r["mid"] / 100.0, "m": mp, "s": sharp_p, "y": 1.0 if r["settlementResult"] == "YES" else 0.0,
                     "fairMove": r["fairMidMoveToClose"], "clvYes": r["clvYesCents"], "clvNo": r["clvNoCents"],
                     "plYes": r["netPlBuyYes"], "plNo": r["netPlBuyNo"], "mts": r["minutesToStart"],
                     "evalAge": None})
    results = {"programId": "MLB-ALPHA-0002", "family": "E", "rowsJoined": len(rows),
               "hypothesesTested": 0, "perFamily": {}}
    by_fam = defaultdict(list)
    for r in rows:
        by_fam[r["fam"]].append(r)
    for fam, rs in sorted(by_fam.items(), key=lambda kv: -len(kv[1])):
        dates = sorted({r["date"] for r in rs})
        games = {r["game"] for r in rs}
        res = {"rows": len(rs), "games": len(games), "dates": len(dates)}
        if len(dates) <= BURN_IN_DATES + 2 or len(games) < 30:
            res["status"] = "INSUFFICIENT"
            results["perFamily"][fam] = res
            continue
        # walk-forward incremental test: market-only vs market+model
        oos = []
        for i, d in enumerate(dates):
            if i < BURN_IN_DATES:
                continue
            train = [r for r in rs if r["date"] < d]; test = [r for r in rs if r["date"] == d]
            if len(train) < 50 or not test:
                continue
            Xm = np.array([[logit(r["k"])] for r in train]); y = np.array([r["y"] for r in train])
            Xmm = np.array([[logit(r["k"]), logit(r["m"])] for r in train])
            w1 = fit_logistic(Xm, y); w2 = fit_logistic(Xmm, y)
            for r in test:
                p1 = float(predict(w1, np.array([[logit(r["k"])]]))[0])
                p2 = float(predict(w2, np.array([[logit(r["k"]), logit(r["m"])]]))[0])
                oos.append((r, p1, p2, float(w2[2])))
        results["hypothesesTested"] += 1
        if len(oos) < 50:
            res["status"] = "INSUFFICIENT_OOS"
            results["perFamily"][fam] = res
            continue
        y = np.array([o[0]["y"] for o in oos]); p1 = np.array([o[1] for o in oos]); p2 = np.array([o[2] for o in oos])
        kmid = np.array([o[0]["k"] for o in oos])
        clus = [o[0]["game"] for o in oos]
        ll = lambda p: -(y * np.log(np.clip(p, 1e-6, 1)) + (1 - y) * np.log(np.clip(1 - p, 1e-6, 1)))
        d_ll = ll(p1) - ll(p2)              # positive = model helps
        d_br = (p1 - y) ** 2 - (p2 - y) ** 2  # positive = model helps
        m_ll, ci_ll = cluster_boot_mean(d_ll, clus); m_br, ci_br = cluster_boot_mean(d_br, clus)
        res.update({"oosRows": len(oos), "oosGames": len(set(clus)),
                    "marketOnlyLogLoss": float(ll(p1).mean()), "marketPlusModelLogLoss": float(ll(p2).mean()),
                    "rawKalshiMidLogLoss": float(ll(kmid).mean()),
                    "deltaLogLossModelHelps": {"mean": m_ll, "ci95gameCluster": ci_ll},
                    "deltaBrierModelHelps": {"mean": m_br, "ci95gameCluster": ci_br},
                    "modelCoefLastFit": oos[-1][3]})
        # price-discovery: does sign(model - kalshi) predict the fair-mid move to close?
        sgn = np.array([1.0 if o[0]["m"] > o[0]["k"] else -1.0 for o in oos])
        fm = np.array([o[0]["fairMove"] for o in oos])
        agree = sgn * fm
        m_fm, ci_fm = cluster_boot_mean(agree, clus)
        # executable: buy the side the model favours (only when disagreement >= 5pp)
        big = [o for o in oos if abs(o[0]["m"] - o[0]["k"]) >= 0.05]
        if len(big) >= 30:
            clv = np.array([o[0]["clvYes"] if o[0]["m"] > o[0]["k"] else o[0]["clvNo"] for o in big])
            pl = np.array([o[0]["plYes"] if o[0]["m"] > o[0]["k"] else o[0]["plNo"] for o in big])
            cb = [o[0]["game"] for o in big]
            m_clv, ci_clv = cluster_boot_mean(clv, cb); m_pl, ci_pl = cluster_boot_mean(pl, cb)
            res["modelSideDisagreeGe5pp"] = {"rows": len(big), "games": len(set(cb)),
                                              "execClvCentsMean": m_clv, "execClvCi95": ci_clv,
                                              "netPlPer10UsdMean": m_pl, "netPlCi95": ci_pl}
        results["hypothesesTested"] += 2
        res["fairMidMoveTowardModelSide"] = {"meanCents": m_fm, "ci95gameCluster": ci_fm}
        res["status"] = ("MODEL_ADDS_INFO" if ci_ll[0] > 0 else "NO_INCREMENTAL_INFO" if ci_ll[1] < 0.0005 else "INCONCLUSIVE")
        results["perFamily"][fam] = res
    with open(OUT, "w") as fh:
        json.dump(results, fh, indent=1, sort_keys=True); fh.write("\n")
    for fam, res in results["perFamily"].items():
        print(fam, json.dumps({k: res[k] for k in res if k in ("rows", "games", "dates", "status", "oosGames", "marketOnlyLogLoss", "marketPlusModelLogLoss", "deltaLogLossModelHelps", "fairMidMoveTowardModelSide", "modelSideDisagreeGe5pp")}, default=str)[:700])
    return 0


if __name__ == "__main__":
    sys.exit(main())
