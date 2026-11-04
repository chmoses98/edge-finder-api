#!/usr/bin/env python3
"""
H3-C: descriptive calibration + executable-price economics baseline for EVERY priced Kalshi MLB
family, 2026-09-02 .. 2026-10-25, from the observation archive (top-of-book quotes, all families
including player props).

Descriptive only: no hypothesis is tested here and nothing is selected on the results. It exists so
the report can say, per family, how well Kalshi's own price is calibrated, what the average taker
pays to cross, and whether either side of the market is fee-positive on average.

Two entries per ticker, always labelled:
  EARLY  first active quote with 5 < minutes-to-start <= 240   (point-in-time feasible)
  LAST   last active quote before the scheduled start          (ex post: the closing-line proxy)
"""
import json
import math
import os
import sys
from collections import defaultdict
from datetime import datetime

import numpy as np

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, REPO)

from lib.edgelab import kalshi_fees as kf  # noqa: E402
from lib.edgelab.mlb_alpha import corpus, taxonomy as tx  # noqa: E402
from lib.edgelab.research.market_structure import settlements, stats as ST  # noqa: E402

OUT = os.path.join(REPO, "data", "edgelab", "research_artifacts", "mlb_alpha_0003")
FIRST, LAST = "2026-09-02", "2026-10-25"
REG_LAST = "2026-09-27"


def _ll(p, y, eps=0.001):
    p = min(max(p, eps), 1 - eps)
    return -math.log(p if y else 1 - p)


def _slope(mids, ys):
    """Logistic recalibration y ~ a + b*logit(mid) by Newton; b=1,a=0 is perfect calibration."""
    x = np.log(np.clip(mids, 0.01, 0.99) / (1 - np.clip(mids, 0.01, 0.99)))
    X = np.column_stack([np.ones_like(x), x])
    w = np.array([0.0, 1.0])
    for _ in range(25):
        z = X @ w
        p = 1 / (1 + np.exp(-z))
        g = X.T @ (ys - p)
        H = (X.T * (p * (1 - p))) @ X + 1e-9 * np.eye(2)
        step = np.linalg.solve(H, g)
        w = w + step
        if np.abs(step).max() < 1e-8:
            break
    return float(w[0]), float(w[1])


def _side_econ(price, won):
    c = int(math.floor(10.0 / (price + kf.FEE_MULTIPLIER_TAKER_STANDARD * price * (1 - price)) + 1e-9))
    if c <= 0:
        return None
    fee = kf.taker_fee(c, price)
    cash = c * price + fee
    return {"net": (c if won else 0.0) - cash, "cash": cash}


def main():
    outcomes = settlements.build_outcomes(REPO)
    by_ticker = defaultdict(list)
    for r in corpus.iter_observations(REPO):
        t = r.get("marketTicker") or ""
        s = t.split("-")[0]
        cls = tx.classify_series(s)
        if cls["domain"] not in (tx.D_GAME, tx.D_PLAYER_GAME):
            continue
        p = tx.parse_market_ticker(t)
        if not p.get("gameKey") or not (FIRST <= p["gameDate"] <= LAST):
            continue
        yb, ya = corpus.obs_price_dollars(r, "yesBid"), corpus.obs_price_dollars(r, "yesAsk")
        if yb is None or ya is None or ya < yb:
            continue
        if (r.get("marketStatus") or "active").lower() not in ("active", "unknown"):
            continue
        cap = datetime.fromisoformat(r["capturedAt"].replace("Z", "+00:00"))
        m2s = (p["scheduledStartUtc"] - cap).total_seconds() / 60.0
        if m2s <= 0:
            continue
        by_ticker[t].append((cap, m2s, yb, ya, p))

    recs = {"EARLY": [], "LAST": []}
    for t, obs in by_ticker.items():
        res = outcomes.get(t)
        if res not in ("YES", "NO"):
            continue
        obs.sort(key=lambda x: x[0])
        p = obs[0][4]
        early = next((o for o in obs if 5 < o[1] <= 240), None)
        for label, o in (("EARLY", early), ("LAST", obs[-1])):
            if o is None:
                continue
            _, m2s, yb, ya, _ = o
            recs[label].append({"ticker": t, "series": p["seriesTicker"], "game": p["gameKey"], "date": p["gameDate"],
                                "phase": "REGULAR" if p["gameDate"] <= REG_LAST else "POSTSEASON",
                                "yb": yb, "ya": ya, "mid": (yb + ya) / 2, "y": res == "YES", "m2s": m2s})

    report = {"program": "MLB-ALPHA-0003", "section": "H3-C", "window": [FIRST, LAST],
              "note": "descriptive; props settle through the archived player-prop path (issue #43 caveat)", "entries": {}}
    for label, rs in recs.items():
        fams = {}
        for fam in sorted({r["series"] for r in rs}):
            for phase in ("REGULAR", "POSTSEASON"):
                sub = [r for r in rs if r["series"] == fam and r["phase"] == phase]
                if len(sub) < 30:
                    continue
                mids = np.array([r["mid"] for r in sub])
                ys = np.array([1.0 if r["y"] else 0.0 for r in sub])
                a, b = _slope(mids, ys)
                econ = {}
                for side in ("YES", "NO"):
                    trades = []
                    for r in sub:
                        price = r["ya"] if side == "YES" else 1.0 - r["yb"]
                        if not (0.01 <= price <= 0.99):
                            continue
                        e = _side_econ(price, r["y"] == (side == "YES"))
                        if e:
                            trades.append({"net": e["net"], "cash": e["cash"], "g": r["game"]})
                    cb = ST.cluster_bootstrap_ratio(trades, "net", "cash", "g", n_resamples=1000)
                    econ[side] = {"trades": len(trades), "roi": cb["point"], "ci90": [cb["ciLow"], cb["ciHigh"]]}
                fams["%s|%s" % (fam, phase)] = {
                    "contracts": len(sub), "games": len({r["game"] for r in sub}), "dates": len({r["date"] for r in sub}),
                    "meanMid": round(float(mids.mean()), 4), "baseRate": round(float(ys.mean()), 4),
                    "brierMid": round(float(((mids - ys) ** 2).mean()), 5),
                    "logLossMid": round(float(np.mean([_ll(m, y > 0.5) for m, y in zip(mids, ys)])), 5),
                    "brierConstantBaseRate": round(float(((ys.mean() - ys) ** 2).mean()), 5),
                    "calibrationIntercept": round(a, 3), "calibrationSlope": round(b, 3),
                    "medianSpreadCents": round(100 * float(np.median([r["ya"] - r["yb"] for r in sub])), 2),
                    "meanSpreadCents": round(100 * float(np.mean([r["ya"] - r["yb"] for r in sub])), 2),
                    "takerBuyYes": econ["YES"], "takerBuyNo": econ["NO"],
                }
        report["entries"][label] = fams
    with open(os.path.join(OUT, "h3c_calibration_baseline.json"), "w") as fh:
        json.dump(report, fh, indent=1, sort_keys=True, default=str)
    for label in ("EARLY", "LAST"):
        print("==", label)
        for k, v in report["entries"][label].items():
            if k.endswith("REGULAR"):
                print("%-22s n=%5d g=%3d mid=%.3f base=%.3f brier=%.4f vs const=%.4f slope=%.2f spr=%.1f | YES roi=%s NO roi=%s" % (
                    k, v["contracts"], v["games"], v["meanMid"], v["baseRate"], v["brierMid"], v["brierConstantBaseRate"],
                    v["calibrationSlope"], v["meanSpreadCents"],
                    None if v["takerBuyYes"]["roi"] is None else round(v["takerBuyYes"]["roi"], 3),
                    None if v["takerBuyNo"]["roi"] is None else round(v["takerBuyNo"]["roi"], 3)))


if __name__ == "__main__":
    main()
