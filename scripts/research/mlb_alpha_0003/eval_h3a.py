#!/usr/bin/env python3
"""
H3-A evaluation, exactly as frozen in docs/EDGELAB_MLB_ALPHA_0003_PROGRAM.md.

  python3 scripts/research/mlb_alpha_0003/eval_h3a.py --panel <panel.jsonl.gz> --out <dir>

Run ONCE. Any change to the rule after this file has produced numbers is a new hypothesis and
must be registered as such (and counted in the BH multiplicity).
"""
import argparse
import collections
import gzip
import json
import math
import os
import sys
from multiprocessing import Pool

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, REPO)

from lib.edgelab import kalshi_fees as kf  # noqa: E402
from lib.edgelab.mlb_alpha import anchor, pricer, sim, taxonomy as tx  # noqa: E402
from lib.edgelab.research.market_structure import stats as ST  # noqa: E402

N_SIM = 150_000
SEED = 20261027
TAU_PRIMARY = 0.03
TAUS = (0.0, 0.015, 0.03, 0.05)
WINDOW = (5.0, 240.0)
MIN_SIZE = 50
PRICE_BAND = (0.03, 0.97)
REG_SEASON_LAST_DATE = "2026-09-27"
FAMILIES = ("KXMLBF5TOTAL", "KXMLBF5SPREAD", "KXMLBF3", "KXMLBF5", "KXMLBF7", "KXMLBGAME",
            "KXMLBTOTAL", "KXMLBTEAMTOTAL", "KXMLBSPREAD", "KXMLBRFI")
ORDER_USD = 10.0

_W = {}


def _init(surface_path):
    import gzip as gz
    _W["surface"] = anchor.Surface.from_json(json.load(gz.open(surface_path, "rt")))
    _W["draws"] = sim.Draws(n=N_SIM, seed=SEED)


def _price_group(task):
    """task: (key, {'pinnacle':..., 'consensus':...}, [tickers]) -> (key, {variant: {ticker: p}}, diag)"""
    key, anchors, tickers = task
    out, diag = {}, {}
    parsed = {t: tx.parse_market_ticker(t) for t in tickers}
    for variant in ("pinnacle", "consensus"):
        a = anchors.get(variant)
        if not a:
            continue
        sol = anchor.solve(_W["surface"], a["pHome"], a["line"], a["pOver"])
        if sol is None:
            diag[variant] = "ANCHOR_OUTSIDE_SURFACE"
            continue
        la, lh, resid = sol
        if max(abs(resid[0]), abs(resid[1])) > 0.003:
            diag[variant] = "ANCHOR_RESIDUAL_%.4f" % max(abs(resid[0]), abs(resid[1]))
            continue
        res = sim.run(la, lh, _W["draws"])
        out[variant] = {t: pricer.probability(res, parsed[t]) for t in tickers}
        diag[variant] = {"lamAway": la, "lamHome": lh}
    return key, out, diag


def _eligible_row(r):
    if r["outcome"] not in ("YES", "NO"):
        return False
    lo, hi = WINDOW
    if not (lo < r["minToStart"] <= hi):
        return False
    return bool(r["yesBidC"] and r["yesAskC"] and r["yesAskC"] >= r["yesBidC"])


def _fill(row, side):
    """$10 whole-contract market order from the archived book; returns (contracts, avg_price_dollars) or None."""
    w = row["buyYes10" if side == "YES" else "buyNo10"]
    if not w or not w.get("contracts") or w.get("avgPriceCents") is None:
        return None
    return int(math.floor(w["contracts"] + 1e-9)), w["avgPriceCents"] / 100.0


def trade_pl(row, side):
    f = _fill(row, side)
    if not f or f[0] <= 0:
        return None
    n, px = f
    fee = kf.taker_fee(n, px)
    cash = n * px + fee
    won = (row["outcome"] == "YES") == (side == "YES")
    return {"contracts": n, "price": px, "fee": fee, "cash": round(cash, 4),
            "net": round((n if won else 0.0) - cash, 4), "won": bool(won)}


def decide(row, p, tau):
    """Frozen rule R1. Returns 'YES' / 'NO' / None."""
    ya, yb = row["yesAskC"] / 100.0, row["yesBidC"] / 100.0
    na = 1.0 - yb
    lo, hi = PRICE_BAND
    yes_ok = lo <= ya <= hi and (row["yesAskSize"] or 0) >= MIN_SIZE
    no_ok = lo <= na <= hi and (row["yesBidSize"] or 0) >= MIN_SIZE
    if yes_ok and p - ya - kf.taker_fee(1, ya) >= tau - 1e-12:
        # a single-contract fee is rounded up to a cent; use the per-contract rate for the edge test
        return "YES"
    if no_ok and (1.0 - p) - na - kf.taker_fee(1, na) >= tau - 1e-12:
        return "NO"
    return None


def _per_contract_fee(price):
    return 0.07 * price * (1.0 - price)


def decide_rate(row, p, tau):
    ya, yb = row["yesAskC"] / 100.0, row["yesBidC"] / 100.0
    na = 1.0 - yb
    lo, hi = PRICE_BAND
    if lo <= ya <= hi and (row["yesAskSize"] or 0) >= MIN_SIZE and p - ya - _per_contract_fee(ya) >= tau - 1e-12:
        return "YES"
    if lo <= na <= hi and (row["yesBidSize"] or 0) >= MIN_SIZE and (1.0 - p) - na - _per_contract_fee(na) >= tau - 1e-12:
        return "NO"
    return None


def logloss(p, y, eps=0.001):
    p = min(max(p, eps), 1.0 - eps)
    return -math.log(p if y else 1.0 - p)


def phase_of(date):
    return "REGULAR" if date <= REG_SEASON_LAST_DATE else "POSTSEASON"


def summarize_trades(trades, label):
    rows = [dict(t, cluster=t["gameKey"]) for t in trades]
    r = ST.cluster_bootstrap_ratio(rows, "net", "cash", "cluster")
    games = len({t["gameKey"] for t in trades})
    dates = len({t["gameDate"] for t in trades})
    half = ST.date_half_stability(rows, lambda rs: (sum(x["net"] for x in rs) / sum(x["cash"] for x in rs))
                                  if rs and sum(x["cash"] for x in rs) > 0 else None)
    return {"label": label, "trades": len(trades), "games": games, "dates": dates,
            "cash": round(sum(t["cash"] for t in trades), 2), "net": round(sum(t["net"] for t in trades), 2),
            "roi": r["point"], "ciLow": r["ciLow"], "ciHigh": r["ciHigh"], "p": r["pPercentile"],
            "winRate": (sum(1 for t in trades if t["won"]) / len(trades)) if trades else None,
            "dateHalves": half}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--panel", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--surface", default=os.path.join(REPO, "data/edgelab/research_artifacts/mlb_alpha_0003/anchor_surface_v1.json.gz"))
    ap.add_argument("--procs", type=int, default=4)
    a = ap.parse_args(argv)

    rows = [json.loads(l) for l in gzip.open(a.panel, "rt")]
    elig = [r for r in rows if r["series"] in FAMILIES and _eligible_row(r)]
    groups = collections.defaultdict(list)
    for r in elig:
        groups[(r["runId"], r["gameKey"])].append(r)
    tasks = []
    for key, rs in groups.items():
        an = rs[0]["anchor"]
        tasks.append((key, {"pinnacle": an["pinnacle"], "consensus": an["consensus"]}, sorted({r["ticker"] for r in rs})))
    print("eligible rows", len(elig), "groups", len(tasks), flush=True)
    priced, diags = {}, {}
    with Pool(a.procs, initializer=_init, initargs=(a.surface,)) as pool:
        for key, out, diag in pool.imap_unordered(_price_group, tasks, chunksize=8):
            priced[key] = out
            diags[key] = diag
    nodiag = collections.Counter()
    for d in diags.values():
        for v, x in d.items():
            nodiag[(v, "ok" if isinstance(x, dict) else x[:22])] += 1
    print("anchor solve diagnostics", dict(nodiag), flush=True)

    # attach model probabilities
    for r in elig:
        out = priced.get((r["runId"], r["gameKey"]), {})
        for variant in ("pinnacle", "consensus"):
            pr = (out.get(variant) or {}).get(r["ticker"])
            r["p_" + variant] = pr["p"] if pr and pr["status"] in (pricer.PRICED, pricer.PRICED_RULE_UNVERIFIED) else None

    results = {"protocol": "docs/EDGELAB_MLB_ALPHA_0003_PROGRAM.md (H3-A)", "nSim": N_SIM,
               "eligibleRows": len(elig), "anchorDiagnostics": {"%s|%s" % k: v for k, v in nodiag.items()},
               "information": {}, "trading": {}, "sensitivity": {}, "descriptive": {}}

    # ---------------- I1: information test (first eligible run per ticker) ----------------
    first = {}
    for r in sorted(elig, key=lambda x: x["capturedAt"]):
        first.setdefault(r["ticker"], r)
    pvals = {}
    for fam in FAMILIES:
        for phase in ("REGULAR", "POSTSEASON"):
            sel = [r for r in first.values() if r["series"] == fam and phase_of(r["gameDate"]) == phase and r["p_pinnacle"] is not None]
            if not sel:
                continue
            recs = []
            for r in sel:
                mid = (r["yesBidC"] + r["yesAskC"]) / 200.0
                y = r["outcome"] == "YES"
                recs.append({"cluster": r["gameKey"], "dll": logloss(mid, y) - logloss(r["p_pinnacle"], y),
                             "dbr": (mid - y) ** 2 - (r["p_pinnacle"] - y) ** 2, "one": 1.0, "gameDate": r["gameDate"]})
            ll = ST.cluster_bootstrap_ratio(recs, "dll", "one", "cluster")
            br = ST.cluster_bootstrap_ratio(recs, "dbr", "one", "cluster")
            games = len({x["cluster"] for x in recs})
            key = "%s|%s" % (fam, phase)
            results["information"][key] = {
                "tickers": len(recs), "games": games, "dates": len({x["gameDate"] for x in recs}),
                "meanDeltaLogLoss": ll["point"], "ciLow": ll["ciLow"], "ciHigh": ll["ciHigh"], "p": ll["pPercentile"],
                "meanDeltaBrier": br["point"], "brierCiLow": br["ciLow"], "brierCiHigh": br["ciHigh"],
                "interpretation": "positive = simulator beats market mid",
            }
            if phase == "REGULAR" and games >= 30:
                pvals["I1|" + fam] = ll["pPercentile"]

    # ---------------- R1: trading rule ----------------
    def run_rule(variant, tau, label):
        trades = []
        taken = set()
        for r in sorted(elig, key=lambda x: x["capturedAt"]):
            if r["ticker"] in taken:
                continue
            p = r.get("p_" + variant)
            if p is None:
                continue
            side = decide_rate(r, p, tau)
            if not side:
                continue
            pl = trade_pl(r, side)
            if pl is None:
                continue
            taken.add(r["ticker"])
            trades.append(dict(pl, ticker=r["ticker"], series=r["series"], side=side, gameKey=r["gameKey"],
                               gameDate=r["gameDate"], phase=phase_of(r["gameDate"]), p=p,
                               minToStart=r["minToStart"], entryPriceC=round(pl["price"] * 100, 2),
                               edgeNet=round((p - pl["price"] if side == "YES" else (1 - p) - pl["price"]) - _per_contract_fee(pl["price"]), 4)))
        return trades

    primary_trades = run_rule("pinnacle", TAU_PRIMARY, "R1")
    results["trading"]["pooled|REGULAR"] = summarize_trades([t for t in primary_trades if t["phase"] == "REGULAR"], "R1 pooled REGULAR")
    results["trading"]["pooled|POSTSEASON"] = summarize_trades([t for t in primary_trades if t["phase"] == "POSTSEASON"], "R1 pooled POSTSEASON")
    pvals["R1|pooled"] = results["trading"]["pooled|REGULAR"]["p"]
    for fam in FAMILIES:
        for side in ("YES", "NO"):
            ts = [t for t in primary_trades if t["series"] == fam and t["side"] == side and t["phase"] == "REGULAR"]
            if not ts:
                continue
            s = summarize_trades(ts, "R1 %s %s REGULAR" % (fam, side))
            results["trading"]["%s|%s|REGULAR" % (fam, side)] = s
            if s["games"] >= 30:
                pvals["R1|%s|%s" % (fam, side)] = s["p"]
    for tau in TAUS:
        if tau == TAU_PRIMARY:
            continue
        ts = [t for t in run_rule("pinnacle", tau, "tau") if t["phase"] == "REGULAR"]
        s = summarize_trades(ts, "R1 tau=%.3f REGULAR" % tau)
        results["sensitivity"]["tau_%.3f" % tau] = s
        pvals["SENS|tau_%.3f" % tau] = s["p"]
    ts = [t for t in run_rule("consensus", TAU_PRIMARY, "consensus") if t["phase"] == "REGULAR"]
    s = summarize_trades(ts, "R1 consensus anchor REGULAR")
    results["sensitivity"]["consensus_anchor"] = s
    pvals["SENS|consensus"] = s["p"]

    # ---------------- multiplicity ----------------
    surv = ST.benjamini_hochberg(pvals, q=ST.FDR_Q)
    results["multiplicity"] = {"m": len(pvals), "q": ST.FDR_Q, "pvalues": pvals, "bhSurvivors": [k for k, v in surv.items() if v]}

    # ---------------- descriptive: executable price vs model by family (regular season, first eligible) ----------------
    desc = {}
    for fam in FAMILIES:
        sel = [r for r in first.values() if r["series"] == fam and r["p_pinnacle"] is not None]
        if not sel:
            continue
        desc[fam] = {"n": len(sel),
                     "meanAbsGapCents": round(100 * sum(abs(r["p_pinnacle"] - (r["yesBidC"] + r["yesAskC"]) / 200.0) for r in sel) / len(sel), 3),
                     "meanSignedGapCents(model-mid)": round(100 * sum(r["p_pinnacle"] - (r["yesBidC"] + r["yesAskC"]) / 200.0 for r in sel) / len(sel), 3),
                     "medianSpreadCents": sorted(r["yesAskC"] - r["yesBidC"] for r in sel)[len(sel) // 2],
                     "shareModelEdgeNetGE3c": round(sum(1 for r in sel if decide_rate(r, r["p_pinnacle"], TAU_PRIMARY)) / len(sel), 4)}
    results["descriptive"]["modelVsMidByFamily"] = desc

    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, "h3a_results.json"), "w") as fh:
        json.dump(results, fh, indent=1, sort_keys=True, default=str)
    with gzip.open(os.path.join(a.out, "h3a_primary_trades.jsonl.gz"), "wt") as fh:
        for t in primary_trades:
            fh.write(json.dumps(t, sort_keys=True) + "\n")
    with gzip.open(os.path.join(a.out, "h3a_model_probabilities_first_eligible.jsonl.gz"), "wt") as fh:
        for r in sorted(first.values(), key=lambda x: x["ticker"]):
            fh.write(json.dumps({k: r[k] for k in ("ticker", "series", "gameKey", "gameDate", "capturedAt", "minToStart",
                                                     "yesBidC", "yesAskC", "outcome", "p_pinnacle", "p_consensus")}, sort_keys=True) + "\n")
    print(json.dumps({"trading": results["trading"], "multiplicity": results["multiplicity"]}, indent=1, default=str)[:6000])
    return 0


if __name__ == "__main__":
    sys.exit(main())
