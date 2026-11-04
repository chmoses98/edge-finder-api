#!/usr/bin/env python3
"""
H3-F (Addendum A): do Pinnacle pre-game totals lines under-forecast actual scoring in a recurring
calendar pattern? Tested on 2022-2025 only (the motivating 2026 sample is excluded from inference).

d = actual total runs - Pinnacle line.   Date-clustered bootstrap. Decision rule is the one registered in
docs/EDGELAB_MLB_ALPHA_0003_PROGRAM.md Addendum A; this script computes it, it does not choose it.
"""
import glob
import gzip
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import numpy as np

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, REPO)
from lib.edgelab.research.market_structure import stats as ST  # noqa: E402

CACHE = os.path.join(REPO, "data", "research_cache")
OUT = os.path.join(REPO, "data", "edgelab", "research_artifacts", "mlb_alpha_0003")
YEARS = (2022, 2023, 2024, 2025)


def ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).replace(tzinfo=timezone.utc)


def american_to_decimal(p):
    p = float(p)
    return 1.0 + (p / 100.0 if p > 0 else 100.0 / -p)


def load_games(year):
    games = {}
    for f in glob.glob(os.path.join(CACHE, "bullpen_backtest", str(year), "schedules", "*.json")):
        for d in json.load(open(f)).get("dates", []):
            for g in d.get("games", []):
                if g.get("gameType") != "R":
                    continue
                t = g.get("teams", {})
                games[g["gamePk"]] = {"start": ts(g["gameDate"]), "away": t["away"]["team"]["name"],
                                      "home": t["home"]["team"]["name"], "status": g.get("status", {}).get("codedGameState")}
    return games


def load_runs(year):
    runs = {}
    for l in gzip.open(os.path.join(CACHE, "batting_backtest", str(year), "boxscores.jsonl.gz"), "rt"):
        d = json.loads(l)
        try:
            runs[d["gamePk"]] = d["awayBatting"]["runs"] + d["homeBatting"]["runs"]
        except KeyError:
            pass
    return runs


def pinnacle_totals(year):
    """{event_id: record} latest pre-commence Pinnacle totals per game."""
    best = {}
    for f in sorted(glob.glob(os.path.join(CACHE, "pinnacle_historical", str(year), "*.json"))):
        for snap in json.load(open(f)).get("snapshots", []):
            for g in snap.get("games", []):
                ct = ts(g["commence_time"])
                for b in g.get("bookmakers", []):
                    if b.get("key") != "pinnacle":
                        continue
                    for m in b.get("markets", []):
                        if m.get("key") != "totals":
                            continue
                        lu = ts(m.get("last_update") or b["last_update"])
                        if lu >= ct:
                            continue
                        over = next((o for o in m["outcomes"] if o["name"] == "Over"), None)
                        under = next((o for o in m["outcomes"] if o["name"] == "Under"), None)
                        if not over or not under or over.get("point") != under.get("point"):
                            continue
                        rec = {"home": g["home_team"], "away": g["away_team"], "commence": ct, "line": float(over["point"]),
                               "last_update": lu, "over": american_to_decimal(over["price"]), "under": american_to_decimal(under["price"])}
                        prev = best.get(g["id"])
                        if prev is None or lu > prev["last_update"]:
                            best[g["id"]] = rec
    return best


def main():
    rows = []
    unmatched = defaultdict(int)
    for y in YEARS:
        games, runs, pin = load_games(y), load_runs(y), pinnacle_totals(y)
        idx = defaultdict(list)
        for pk, g in games.items():
            idx[(g["home"], g["away"])].append((pk, g))
        for eid, r in pin.items():
            cands = [(pk, g) for pk, g in idx.get((r["home"], r["away"]), []) if abs((g["start"] - r["commence"]).total_seconds()) <= 90 * 60]
            if len(cands) != 1 or cands[0][0] not in runs:
                unmatched[y] += 1
                continue
            pk, g = cands[0]
            ip_o, ip_u = 1.0 / r["over"], 1.0 / r["under"]
            p_over = ip_o / (ip_o + ip_u)
            total = runs[pk]
            rows.append({"year": y, "date": (g["start"] - timedelta(hours=5)).strftime("%Y-%m-%d"), "month": g["start"].month if g["start"].hour >= 5 else (g["start"] - timedelta(days=1)).month,
                         "line": r["line"], "total": total, "d": total - r["line"], "pOverNoVig": p_over,
                         "push": float(r["line"]).is_integer(), "over": 1.0 if total > r["line"] else (0.0 if total < r["line"] else None)})
    print("matched games", len(rows), "unmatched", dict(unmatched))

    def stat_mean(rs):
        return float(np.mean([x["d"] for x in rs])) if rs else None

    res = {"program": "MLB-ALPHA-0003", "section": "H3-F", "seasons": list(YEARS), "matchedGames": len(rows),
           "unmatchedByYear": dict(unmatched), "cells": {}}
    pvals = {}
    cells = [("ALL", None)] + [("month_%02d" % m, m) for m in sorted({r["month"] for r in rows})]
    for label, m in cells:
        rs = [r for r in rows if m is None or r["month"] == m]
        if len(rs) < 40:
            continue
        for r in rs:
            r["cluster"] = r["date"]
        b = ST.cluster_bootstrap(rs, stat_mean, "cluster", n_resamples=2000)
        non_push = [r for r in rs if r["over"] is not None]
        res["cells"][label] = {
            "games": len(rs), "dates": len({r["date"] for r in rs}),
            "meanActualMinusLine": b["point"], "ciLow": b["ciLow"], "ciHigh": b["ciHigh"], "p": b["pPercentile"],
            "meanLine": float(np.mean([r["line"] for r in rs])),
            "realizedOverRate": float(np.mean([r["over"] for r in non_push])) if non_push else None,
            "impliedOverRateNoVig": float(np.mean([r["pOverNoVig"] for r in non_push])) if non_push else None,
            "bySeason": {str(y): {"games": len([r for r in rs if r["year"] == y]),
                                  "meanD": stat_mean([r for r in rs if r["year"] == y])} for y in YEARS},
        }
        pvals[label] = b["pPercentile"]
    surv = ST.benjamini_hochberg(pvals, q=ST.FDR_Q)
    res["multiplicity"] = {"m": len(pvals), "q": ST.FDR_Q, "pvalues": pvals, "bhSurvivors": [k for k, v in surv.items() if v]}

    # POST-REGISTRATION CORRECTION (declared, not hidden): totals lines are set at the MEDIAN (no-vig Over ~ 50%)
    # and run totals are right-skewed, so mean(actual - line) is > 0 by construction (+0.56 runs here) and says
    # nothing about bias. The correctly specified statistic is the realized Over rate minus the no-vig implied
    # Over probability on push-free lines. Same cells, same clustering, its own BH family.
    def over_resid(rs):
        rs = [r for r in rs if r["over"] is not None]
        return float(np.mean([r["over"] - r["pOverNoVig"] for r in rs])) if rs else None

    res["overRateCells"], p2 = {}, {}
    for label, m in cells:
        rs = [r for r in rows if (m is None or r["month"] == m) and r["over"] is not None]
        if len(rs) < 40:
            continue
        for r in rs:
            r["cluster"] = r["date"]
        b = ST.cluster_bootstrap(rs, over_resid, "cluster", n_resamples=2000)
        res["overRateCells"][label] = {
            "games": len(rs), "realizedOver": float(np.mean([r["over"] for r in rs])),
            "impliedOver": float(np.mean([r["pOverNoVig"] for r in rs])),
            "residual": b["point"], "ciLow": b["ciLow"], "ciHigh": b["ciHigh"], "p": b["pPercentile"],
            "bySeason": {str(y): over_resid([r for r in rs if r["year"] == y]) for y in YEARS}}
        p2[label] = b["pPercentile"]
    surv2 = ST.benjamini_hochberg(p2, q=ST.FDR_Q)
    res["overRateMultiplicity"] = {"m": len(p2), "pvalues": p2, "bhSurvivors": [k for k, v in surv2.items() if v]}
    sep2 = res["overRateCells"].get("month_09")
    if sep2:
        pos2 = sum(1 for y in YEARS if sep2["bySeason"][str(y)] is not None and sep2["bySeason"][str(y)] > 0)
        res["overRateDecision"] = {"septemberPositiveSeasons": pos2, "septemberResidual": sep2["residual"],
                                   "ciLow": sep2["ciLow"], "ciHigh": sep2["ciHigh"],
                                   "seasonalitySupported": bool(pos2 >= 3 and sep2["ciLow"] > 0)}
    sep = res["cells"].get("month_09")
    if sep:
        pos = sum(1 for y in YEARS if sep["bySeason"][str(y)]["meanD"] is not None and sep["bySeason"][str(y)]["meanD"] > 0)
        res["registeredDecision"] = {"septemberPositiveSeasons": pos, "pooledCiExcludesZero": bool(sep["ciLow"] > 0 or sep["ciHigh"] < 0),
                                     "seasonalitySupported": bool(pos >= 3 and sep["ciLow"] > 0)}
    with open(os.path.join(OUT, "h3f_totals_seasonality.json"), "w") as fh:
        json.dump(res, fh, indent=1, sort_keys=True, default=str)
    for k, v in res["cells"].items():
        print("%-9s games=%4d  mean(actual-line)=%+.3f  CI90[%+.3f,%+.3f] p=%.3f  over %.3f vs implied %.3f | by season %s" % (
            k, v["games"], v["meanActualMinusLine"], v["ciLow"], v["ciHigh"], v["p"], v["realizedOverRate"] or 0, v["impliedOverRateNoVig"] or 0,
            {y: (None if s["meanD"] is None else round(s["meanD"], 2)) for y, s in v["bySeason"].items()}))
    print(res.get("registeredDecision"), res["multiplicity"]["bhSurvivors"])
    print("-- over-rate residual (realized - implied), the correctly specified statistic")
    for k, v in res["overRateCells"].items():
        print("%-9s n=%4d realized %.3f implied %.3f resid %+.3f CI90[%+.3f,%+.3f] p=%.3f | by season %s" % (
            k, v["games"], v["realizedOver"], v["impliedOver"], v["residual"], v["ciLow"], v["ciHigh"], v["p"],
            {y: (None if r is None else round(r, 3)) for y, r in v["bySeason"].items()}))
    print(res.get("overRateDecision"), res["overRateMultiplicity"]["bhSurvivors"])


if __name__ == "__main__":
    main()
