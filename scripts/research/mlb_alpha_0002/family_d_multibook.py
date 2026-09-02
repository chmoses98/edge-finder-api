#!/usr/bin/env python3
"""MLB-ALPHA-0002 Family D (coarse, all August dates): multi-book consensus
vs Kalshi on the moneyline, using the slate snapshots (2-14 captures/day)
joined AS-OF to the Kalshi observation panel.

At each Kalshi game_result observation t, take the latest slate capture
<= t. Features: Pinnacle vig-free prob for the ticker's team, consensus
mean across books, book dispersion, number of books that moved in the
same direction since the previous capture (>= 1pp), Pinnacle's move since
the previous capture. Targets: Kalshi fair-mid move to close, executable
CLV on the sharp side, settlement residual. Coarse rules only; game-cluster
bootstrap. RESEARCH ONLY.
"""

import gzip
import json
import os
import sys
from collections import defaultdict

import numpy as np

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, REPO)
ART = os.path.join(REPO, "data", "edgelab", "research_artifacts", "mlb_alpha_0002")
OUT = os.path.join(ART, "family_d_multibook_results.json")
SEED = 20260902


def iter_gz(p):
    with gzip.open(p, "rt") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


def cluster_boot(vals, clus, B=2000, seed=SEED):
    rng = np.random.default_rng(seed)
    g = defaultdict(list)
    for v, c in zip(vals, clus):
        g[c].append(v)
    keys = list(g); sums = np.array([sum(g[k]) for k in keys]); cnt = np.array([len(g[k]) for k in keys])
    idx = rng.integers(0, len(keys), size=(B, len(keys)))
    means = sums[idx].sum(1) / cnt[idx].sum(1)
    return float(np.mean(vals)), [float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))], len(keys)


def main():
    sharp = defaultdict(lambda: defaultdict(dict))   # gameKey -> capturedAt -> book -> homeVF
    for r in iter_gz(os.path.join(ART, "pit_sharp_panel.jsonl.gz")):
        if r.get("gameKey") and r.get("mlHomeVigFree") is not None:
            sharp[r["gameKey"]][r["capturedAt"]][r["book"]] = r["mlHomeVigFree"]
    rows = []
    for r in iter_gz(os.path.join(ART, "pit_kalshi_panel.jsonl.gz")):
        if r["marketFamily"] != "game_result" or not r.get("team"):
            continue
        caps = sorted(sharp.get(r["gameKey"], {}))
        prior = [c for c in caps if c <= r["capturedAt"]]
        if not prior:
            continue
        cur = sharp[r["gameKey"]][prior[-1]]
        prev = sharp[r["gameKey"]][prior[-2]] if len(prior) >= 2 else None
        is_home = r["team"] == r["homeTeam"]
        def side(p):
            return p if is_home else 1.0 - p
        if "pinnacle" not in cur:
            continue
        pinn = side(cur["pinnacle"])
        cons = float(np.mean([side(v) for v in cur.values()]))
        disp = float(np.std([side(v) for v in cur.values()])) if len(cur) > 1 else 0.0
        k = r["mid"] / 100.0
        n_up = n_dn = 0; dpinn = None
        if prev:
            for b, v in cur.items():
                if b in prev:
                    d = side(v) - side(prev[b])
                    n_up += d >= 0.01; n_dn += d <= -0.01
            if "pinnacle" in prev:
                dpinn = pinn - side(prev["pinnacle"])
        rows.append({"game": r["gameKey"], "date": r["gameDate"], "d": pinn - k, "dc": cons - k, "disp": disp,
                     "nUp": n_up, "nDn": n_dn, "dPinn": dpinn, "books": len(cur),
                     "fm": r["fairMidMoveToClose"] / 100.0, "clvYes": r["clvYesCents"], "clvNo": r["clvNoCents"],
                     "plYes": r["netPlBuyYes"], "plNo": r["netPlBuyNo"], "y": 1.0 if r["settlementResult"] == "YES" else 0.0,
                     "k": k, "mts": r["minutesToStart"], "sharpAgeMin": None})
    res = {"programId": "MLB-ALPHA-0002", "family": "D-multibook", "rows": len(rows),
           "games": len({r["game"] for r in rows}), "dates": len({r["date"] for r in rows}), "tests": {}}
    if rows:
        res["meanAbsDisagreementPinnaclePp"] = float(np.mean([abs(r["d"]) for r in rows]) * 100)
        res["meanAbsDisagreementConsensusPp"] = float(np.mean([abs(r["dc"]) for r in rows]) * 100)
    def test(name, sel, side_of):
        if len({r["game"] for r in sel}) < 15:
            res["tests"][name] = {"rows": len(sel), "status": "INSUFFICIENT"}; return
        clus = [r["game"] for r in sel]
        fm = [side_of(r) * r["fm"] * 100 for r in sel]
        clv = [r["clvYes"] if side_of(r) > 0 else r["clvNo"] for r in sel]
        pl = [r["plYes"] if side_of(r) > 0 else r["plNo"] for r in sel]
        resid = [(r["y"] - r["k"]) * side_of(r) * 100 for r in sel]
        m1, c1, ng = cluster_boot(fm, clus); m2, c2, _ = cluster_boot(clv, clus); m3, c3, _ = cluster_boot(pl, clus); m4, c4, _ = cluster_boot(resid, clus)
        res["tests"][name] = {"rows": len(sel), "games": ng, "fairMidTowardSignalPp": m1, "fairMidCi95": c1,
                              "execClvCents": m2, "execClvCi95": c2, "netPlPer10Usd": m3, "netPlCi95": c3,
                              "settlementResidualSignalSidePp": m4, "residCi95": c4, "status": "TESTED"}
    sgn = lambda r: 1.0 if r["d"] > 0 else -1.0
    test("DM1_pinnacle_disagree_ge2pp", [r for r in rows if abs(r["d"]) >= 0.02], sgn)
    test("DM1_pinnacle_disagree_ge4pp", [r for r in rows if abs(r["d"]) >= 0.04], sgn)
    test("DM2_consensus_disagree_ge2pp", [r for r in rows if abs(r["dc"]) >= 0.02], lambda r: 1.0 if r["dc"] > 0 else -1.0)
    test("DM3_pinnacle_moved_ge1pp_since_prev", [r for r in rows if r["dPinn"] is not None and abs(r["dPinn"]) >= 0.01], lambda r: 1.0 if r["dPinn"] > 0 else -1.0)
    test("DM4_ge3_books_same_direction", [r for r in rows if max(r["nUp"], r["nDn"]) >= 3 and r["nUp"] != r["nDn"]], lambda r: 1.0 if r["nUp"] > r["nDn"] else -1.0)
    test("DM5_disagree_ge2pp_and_low_dispersion", [r for r in rows if abs(r["dc"]) >= 0.02 and r["disp"] <= 0.01], lambda r: 1.0 if r["dc"] > 0 else -1.0)
    res["hypothesesTested"] = sum(1 for t in res["tests"].values() if t.get("status") == "TESTED") * 4
    with open(OUT, "w") as fh:
        json.dump(res, fh, indent=1, sort_keys=True); fh.write("\n")
    print(json.dumps({k: v for k, v in res.items() if k != "tests"}, indent=1))
    for k, t in res["tests"].items():
        print(k, json.dumps(t, default=lambda x: round(x, 3) if isinstance(x, float) else x)[:400])
    return 0


if __name__ == "__main__":
    sys.exit(main())
