#!/usr/bin/env python3
"""MLB-ALPHA-0002 candidate distillation: F5 moneyline short-horizon
REVERSAL (Family C). Registered rule family C_REV; this script exists to
stress a single coarse rule BEFORE any freeze, not to tune it.

Rule R(k, h): an inning_result (KXMLBF5 moneyline) contract whose mid
fell by >= k cents over the prior h minutes (a thin-market overreaction
hypothesis). Action: BUY YES at the ask at the decision time. Mirror
rule: rose by >= k -> BUY NO at 100-bid. Thresholds k in {2,3,4} cents
and h in {30,60} are the ONLY predeclared variants (coarse, no search).

Reported per variant: games, dates, fair-mid move to close on the bought
side, executable CLV, $10 post-fee P/L (game-cluster CIs), split by
date halves, max single-game share of total P/L, price-band and team
concentration, and the +15/+30/+60 minute path. RESEARCH ONLY.
"""

import gzip
import json
import os
import sys
from collections import defaultdict

import numpy as np

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
ART = os.path.join(REPO, "data", "edgelab", "research_artifacts", "mlb_alpha_0002")
OUT = os.path.join(ART, "candidate_eval_f5_reversal.json")
SEED = 20260902


def iter_gz(p):
    with gzip.open(p, "rt") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


def cluster_boot(vals, clus, B=3000, seed=SEED):
    rng = np.random.default_rng(seed)
    g = defaultdict(list)
    for v, c in zip(vals, clus):
        g[c].append(v)
    keys = list(g); sums = np.array([sum(g[k]) for k in keys]); cnt = np.array([len(g[k]) for k in keys])
    idx = rng.integers(0, len(keys), size=(B, len(keys)))
    means = sums[idx].sum(1) / cnt[idx].sum(1)
    mean = float(np.mean(vals)); centred = means - means.mean()
    return mean, [float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))], len(keys), max(float((np.abs(centred) >= abs(mean)).mean()), 1.0 / B)


def band(p):
    return "%02d-%02d" % (int(p // 10) * 10, int(p // 10) * 10 + 10)


def main():
    rows = [r for r in iter_gz(os.path.join(ART, "pit_candle_panel.jsonl.gz")) if r["marketFamily"] == "inning_result"]
    dates = sorted({r["gameDate"] for r in rows})
    half = dates[len(dates) // 2] if dates else None
    res = {"programId": "MLB-ALPHA-0002", "rule": "C_REV F5 moneyline reversal", "rows": len(rows),
           "games": len({r["gameKey"] for r in rows}), "dates": dates, "variants": {}, "variantsRegistered": 12}
    for h in (30, 60):
        for k in (2, 3, 4):
            for direction, side in (("DOWN_buyYES", +1), ("UP_buyNO", -1)):
                f = "dMid%d" % h
                sel = [r for r in rows if r.get(f) is not None and (r[f] <= -k if side > 0 else r[f] >= k)]
                # ONE decision per contract per triggering episode: keep the first grid row of each run
                sel.sort(key=lambda r: (r["marketTicker"], r["decisionMinute"]))
                first, last_t, last_m = [], None, None
                for r in sel:
                    if r["marketTicker"] == last_t and r["decisionMinute"] - last_m <= 5:
                        last_m = r["decisionMinute"]; continue
                    first.append(r); last_t, last_m = r["marketTicker"], r["decisionMinute"]
                sel = first
                key = "h%d_k%d_%s" % (h, k, direction)
                games = {r["gameKey"] for r in sel}
                v = {"episodes": len(sel), "games": len(games), "dates": len({r["gameDate"] for r in sel})}
                if len(games) < 15:
                    v["status"] = "INSUFFICIENT"; res["variants"][key] = v; continue
                clus = [r["gameKey"] for r in sel]
                fm = [side * r["fairMidMoveToClose"] for r in sel]
                clv = [r["clvYesCents"] if side > 0 else r["clvNoCents"] for r in sel]
                pl = [r["netPlBuyYes"] if side > 0 else r["netPlBuyNo"] for r in sel]
                for name, vals in (("fairMid", fm), ("execClv", clv), ("netPl", pl)):
                    m, ci, ng, p = cluster_boot(vals, clus)
                    v[name] = {"mean": m, "ci95": ci, "p": p}
                for hh in (15, 30, 60):
                    x = [side * r["fairMidMove%dm" % hh] for r in sel if r.get("fairMidMove%dm" % hh) is not None]
                    v["fairMid%dm" % hh] = float(np.mean(x)) if x else None
                v["winRate"] = float(np.mean([(r["settlementResult"] == "YES") == (side > 0) for r in sel]))
                v["avgEntryPrice"] = float(np.mean([r["yesAsk"] if side > 0 else 100 - r["yesBid"] for r in sel]))
                v["avgSpread"] = float(np.mean([r["spreadCents"] for r in sel]))
                # robustness
                a = [r for r in sel if r["gameDate"] < half]; b = [r for r in sel if r["gameDate"] >= half]
                v["netPlFirstHalf"] = float(np.sum([r["netPlBuyYes"] if side > 0 else r["netPlBuyNo"] for r in a]))
                v["netPlSecondHalf"] = float(np.sum([r["netPlBuyYes"] if side > 0 else r["netPlBuyNo"] for r in b]))
                v["fairMidFirstHalf"] = float(np.mean([side * r["fairMidMoveToClose"] for r in a])) if a else None
                v["fairMidSecondHalf"] = float(np.mean([side * r["fairMidMoveToClose"] for r in b])) if b else None
                gpl = defaultdict(float)
                for r in sel:
                    gpl[r["gameKey"]] += r["netPlBuyYes"] if side > 0 else r["netPlBuyNo"]
                tot = sum(gpl.values())
                v["maxSingleGameShareOfNetPl"] = (max(gpl.values()) / tot) if tot > 0 else None
                v["netPlWithoutBestGame"] = tot - max(gpl.values())
                bands = defaultdict(int); teams = defaultdict(int)
                for r in sel:
                    bands[band(r["yesAsk"] if side > 0 else 100 - r["yesBid"])] += 1
                    teams[r["marketTicker"].rsplit("-", 1)[-1]] += 1
                v["priceBandShare"] = {b_: round(n / len(sel), 3) for b_, n in sorted(bands.items(), key=lambda kv: -kv[1])[:4]}
                v["topTeamShare"] = round(max(teams.values()) / len(sel), 3)
                v["status"] = "TESTED"
                res["variants"][key] = v
    with open(OUT, "w") as fh:
        json.dump(res, fh, indent=1, sort_keys=True); fh.write("\n")
    print("F5 reversal: rows %d games %d dates %d" % (res["rows"], res["games"], len(dates)))
    for key, v in res["variants"].items():
        if v.get("status") != "TESTED":
            print("%-22s %s (games=%d)" % (key, v.get("status"), v["games"])); continue
        print("%-22s ep=%4d g=%3d fm=%+.2f [%+.2f,%+.2f] clv=%+.2f pl=%+.2f [%+.2f,%+.2f] p=%.3f wr=%.2f entry=%.0f spr=%.1f halves=%+.1f/%+.1f maxGame=%s" % (
            key, v["episodes"], v["games"], v["fairMid"]["mean"], *v["fairMid"]["ci95"], v["execClv"]["mean"], v["netPl"]["mean"], *v["netPl"]["ci95"], v["netPl"]["p"],
            v["winRate"], v["avgEntryPrice"], v["avgSpread"], v["netPlFirstHalf"], v["netPlSecondHalf"], None if v["maxSingleGameShareOfNetPl"] is None else round(v["maxSingleGameShareOfNetPl"], 2)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
