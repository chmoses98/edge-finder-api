#!/usr/bin/env python3
"""
MRV-LL-001..004: cross-family lead/lag on the recovered 1-minute record.
RESEARCH ONLY.  12 registered tests (4 directions x 3 horizons).

Usage: run_leadlag.py --raw-root <dir with candles/> [--dates ...]
Writes data/edgelab/research_artifacts/market_structure/leadlag_1min.json
"""
import argparse
import collections
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from _common import write_artifact, now_iso  # noqa: E402

from lib.edgelab.research.market_structure import candles as CD, stats as ST, leadlag as LL  # noqa: E402

HORIZONS = (5, 15, 30)
FAMS = ("game_result", "inning_result", "game_total", "inning_total")


def mids(series):
    return {t: CD.mid(q) for t, q in series.items() if CD.mid(q) is not None}


def ladder_mid_series(items, start=None):
    """Fixed rung set = rungs two-sided at >= 50% of PREGAME minutes (T-240..T-0); implied mean per minute where all those rungs are quoted."""
    per_minute = collections.defaultdict(dict)
    for ident, series, _ in items:
        for t, q in series.items():
            if start is not None and not (start - 240 * 60 <= t <= start):
                continue
            m = CD.mid(q)
            if m is not None:
                per_minute[t][ident["rung"]] = m
    if not per_minute:
        return {}
    cnt = collections.Counter(r for d in per_minute.values() for r in d)
    keep = {r for r, c in cnt.items() if c >= 0.5 * len(per_minute)}
    if len(keep) < 3:
        return {}
    out = {}
    for t, d in per_minute.items():
        if keep <= set(d):
            out[t] = LL.ladder_implied_mean({r: d[r] for r in keep}) * 100.0   # in "cents of a run" so slopes are comparable
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw-root", required=True)
    ap.add_argument("--dates", default="")
    args = ap.parse_args()
    dates = args.dates.split(",") if args.dates else CD.available_dates(args.raw_root)
    rows = {("ML->F5ML", h): [] for h in HORIZONS}
    rows.update({("F5ML->ML", h): [] for h in HORIZONS})
    rows.update({("TOT->F5TOT", h): [] for h in HORIZONS})
    rows.update({("F5TOT->TOT", h): [] for h in HORIZONS})
    games = set()
    for date in dates:
        items = list(CD.iter_date(args.raw_root, date, FAMS))
        for gkey, gitems in CD.group_by_game(items).items():
            start = CD.scheduled_start_ts(gitems[0][0])
            home = gitems[0][0]["homeTeam"]
            ml = [it for it in gitems if it[0]["seriesTicker"] == "KXMLBGAME" and it[0]["team"] == home]
            f5 = [it for it in gitems if it[0]["seriesTicker"] == "KXMLBF5" and it[0]["team"] == home]
            tot = [it for it in gitems if it[0]["seriesTicker"] == "KXMLBTOTAL"]
            f5t = [it for it in gitems if it[0]["seriesTicker"] == "KXMLBF5TOTAL"]
            pairs = []
            if ml and f5:
                a, b = mids(ml[0][1]), mids(f5[0][1])
                pairs += [("ML->F5ML", a, b), ("F5ML->ML", b, a)]
            if tot and f5t:
                a, b = ladder_mid_series(tot, start), ladder_mid_series(f5t, start)
                if a and b:
                    pairs += [("TOT->F5TOT", a, b), ("F5TOT->TOT", b, a)]
            for name, a, b in pairs:
                for h in HORIZONS:
                    for x, z, y in LL.grid_rows(a, b, start, h):
                        rows[(name, h)].append({"physicalGameKey": gkey, "gameDate": date, "x": x, "z": z, "y": y})
                        games.add(gkey)
    tests = {}
    pv = {}
    for (name, h), rs in rows.items():
        key = "%s_h%d" % (name, h)
        res = ST.cluster_bootstrap(rs, LL.slope_stat)
        res["dates"] = len({r["gameDate"] for r in rs})
        res["stability"] = ST.date_half_stability(rs, LL.slope_stat)
        res["meanAbsX"] = ST.mean([abs(r["x"]) for r in rs])
        res["ownLagSlope"] = ST.ols_slope([(r["z"], r["y"]) for r in rs])
        tests[key] = res
        pv[key] = res["pPercentile"]
    bh = ST.benjamini_hochberg(pv)
    out = {"generatedAt": now_iso(), "tier": "TIER_B_EXPLORATORY", "dates": dates, "games": len(games),
           "design": "partial slope of follower move (t,t+h) on leader move (t-h,t) controlling follower own move (t-h,t); 5-min grid T-240..T-5; totals use ladder implied mean over a fixed rung set (x100)",
           "tests": tests, "bh": bh}
    p = write_artifact("leadlag_1min.json", out)
    print("wrote", p)
    for k, v in tests.items():
        print(k, "n", v["n"], "games", v["clusters"], "slope", None if v["point"] is None else round(v["point"], 4), "ci", v["ciLow"], v["ciHigh"], "p", v["pTwoSided"], "BH", bh[k])


if __name__ == "__main__":
    main()
