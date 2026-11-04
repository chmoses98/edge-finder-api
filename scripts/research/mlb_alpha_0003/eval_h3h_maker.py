#!/usr/bin/env python3
"""
H3-H (Addendum B): join-the-bid limit orders with a MEASURED queue, filled only by subsequent tape prints.

  python3 scripts/research/mlb_alpha_0003/eval_h3h_maker.py --panel <h3a_panel.jsonl.gz> \
      --prospective-root <.../mlb_alpha_0002/prospective> --out <dir>
"""
import argparse
import collections
import gzip
import json
import math
import os
import sys
from datetime import datetime, timezone

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, REPO)

from lib.edgelab import kalshi_fees as kf  # noqa: E402
from lib.edgelab.mlb_alpha import corpus, paper, taxonomy as tx  # noqa: E402
from lib.edgelab.research.market_structure import stats as ST  # noqa: E402

FAMILIES = ("KXMLBF5TOTAL", "KXMLBF5SPREAD", "KXMLBF3", "KXMLBF5", "KXMLBF7", "KXMLBGAME")
WINDOW = (5.0, 240.0)
ORDER_USD = 10.0
PLACEMENT_LAG_S = 120          # books are fetched up to ~2 minutes after the run stamp
REG_LAST = "2026-09-27"
MAKER_MULT = kf.FEE_MULTIPLIER_MAKER_DESIGNATED


def _ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def load_tape(prospective_root, tickers):
    tape = collections.defaultdict(list)
    for r in corpus.iter_prospective(prospective_root, "trades"):
        t = r.get("ticker")
        if t not in tickers:
            continue
        tape[t].append({"ts": _ts(r["created_time"]), "yes_price": float(r["yes_price_dollars"]),
                        "count": float(r["count_fp"]), "taker_side": r.get("taker_outcome_side") or r.get("taker_side")})
    for t in tape:
        tape[t].sort(key=lambda x: x["ts"])
    return tape


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--panel", required=True)
    ap.add_argument("--prospective-root", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)

    rows = [json.loads(l) for l in gzip.open(a.panel, "rt")]
    elig = [r for r in rows if r["series"] in FAMILIES and r["outcome"] in ("YES", "NO")
            and WINDOW[0] < r["minToStart"] <= WINDOW[1] and r["yesBidC"] and r["yesAskC"] and r["yesAskC"] >= r["yesBidC"]]
    first = {}
    for r in sorted(elig, key=lambda x: x["capturedAt"]):
        first.setdefault(r["ticker"], r)
    tape = load_tape(a.prospective_root, set(first))
    print("eligible tickers", len(first), "with any tape", len(tape), flush=True)

    orders = []
    for t, r in first.items():
        cap = _ts(r["capturedAt"])
        start = cap + r["minToStart"] * 60.0
        for side in ("YES", "NO"):
            if side == "YES":
                price, queue = r["yesBidC"] / 100.0, r["yesBidSize"] or 0.0
            else:
                price, queue = 1.0 - r["yesAskC"] / 100.0, r["yesAskSize"] or 0.0
            if not (0.02 <= price <= 0.98):
                continue
            n = int(math.floor(ORDER_USD / (price + MAKER_MULT * price * (1 - price)) + 1e-9))
            if n <= 0:
                continue
            fill = paper.simulate_resting_fill(tape.get(t, []), side, price, n, placed_ts=cap + PLACEMENT_LAG_S,
                                               queue_ahead=queue, cancel_ts=start)
            won = (r["outcome"] == "YES") == (side == "YES")
            q = fill["filled"]
            ptaker = (r["yesAskC"] / 100.0) if side == "YES" else (1.0 - r["yesBidC"] / 100.0)
            row = {"ticker": t, "series": r["series"], "gameKey": r["gameKey"], "gameDate": r["gameDate"], "side": side,
                   "phase": "REGULAR" if r["gameDate"] <= REG_LAST else "POSTSEASON", "price": price, "contracts": n,
                   "queueAhead": queue, "filled": q, "won": bool(won), "minToStart": r["minToStart"], "spread": ptaker - price,
                   "tapeVolumeSeenAtOrThrough": fill["sawVolumeAtLevel"]}
            # economics on the FILLED quantity (maker fee headline; zero-fee sensitivity)
            for label, mult in (("fee0175", MAKER_MULT), ("fee0", 0.0)):
                cash = q * price + kf.taker_fee(int(q), price, multiplier=mult) if q >= 1 else 0.0
                row["cash_" + label] = round(cash, 4)
                row["net_" + label] = round((int(q) if won else 0.0) - cash, 4) if q >= 1 else 0.0
            # the counterfactual "if the whole order had filled" (no selection) and the taker alternative
            row["cfNet_fee0175"] = round((n if won else 0.0) - (n * price + kf.taker_fee(n, price, multiplier=MAKER_MULT)), 4)
            row["cfCash_fee0175"] = round(n * price + kf.taker_fee(n, price, multiplier=MAKER_MULT), 4)
            nt = int(math.floor(ORDER_USD / (ptaker + 0.07 * ptaker * (1 - ptaker)) + 1e-9))
            if nt > 0:
                tcash = nt * ptaker + kf.taker_fee(nt, ptaker)
                row["takerCash"], row["takerNet"] = round(tcash, 4), round((nt if won else 0.0) - tcash, 4)
            orders.append(row)

    def summarize(rs, label):
        filled = [x for x in rs if x["filled"] >= 1]
        recs = [dict(x, c=x["gameKey"]) for x in filled]
        out = {"label": label, "placed": len(rs), "filled": len(filled), "fillRate": len(filled) / len(rs) if rs else None,
               "games": len({x["gameKey"] for x in filled}), "dates": len({x["gameDate"] for x in filled})}
        for fee in ("fee0175", "fee0"):
            b = ST.cluster_bootstrap_ratio(recs, "net_" + fee, "cash_" + fee, "c", n_resamples=2000)
            out["roi_" + fee] = {"point": b["point"], "ci90": [b["ciLow"], b["ciHigh"]], "p": b["pPercentile"]}
        cf = ST.cluster_bootstrap_ratio([dict(x, c=x["gameKey"]) for x in rs], "cfNet_fee0175", "cfCash_fee0175", "c", n_resamples=1000)
        out["ifEveryOrderHadFilled_roi"] = {"point": cf["point"], "ci90": [cf["ciLow"], cf["ciHigh"]]}
        tk = [dict(x, c=x["gameKey"]) for x in rs if "takerNet" in x]
        tb = ST.cluster_bootstrap_ratio(tk, "takerNet", "takerCash", "c", n_resamples=1000)
        out["takerAlternative_roi"] = {"point": tb["point"], "ci90": [tb["ciLow"], tb["ciHigh"]]}
        out["meanSpreadCents"] = round(100 * sum(x["spread"] for x in rs) / len(rs), 2) if rs else None
        out["medianQueueAhead"] = sorted(x["queueAhead"] for x in rs)[len(rs) // 2] if rs else None
        return out

    res = {"program": "MLB-ALPHA-0003", "section": "H3-H", "placementLagSeconds": PLACEMENT_LAG_S, "orders": len(orders),
           "makerFeeMultiplierHeadline": MAKER_MULT, "cells": {}}
    pv = {}
    for fam in FAMILIES:
        for side in ("YES", "NO"):
            rs = [x for x in orders if x["series"] == fam and x["side"] == side and x["phase"] == "REGULAR"]
            if not rs:
                continue
            s = summarize(rs, "%s %s REGULAR" % (fam, side))
            res["cells"]["%s|%s|REGULAR" % (fam, side)] = s
            if s["games"] >= 30:
                pv["%s|%s" % (fam, side)] = s["roi_fee0175"]["p"]
    for side in ("YES", "NO"):
        rs = [x for x in orders if x["side"] == side and x["phase"] == "REGULAR"]
        s = summarize(rs, "POOLED %s REGULAR" % side)
        res["cells"]["POOLED|%s|REGULAR" % side] = s
        pv["POOLED|%s" % side] = s["roi_fee0175"]["p"]
    res["cells"]["POOLED|BOTH|POSTSEASON"] = summarize([x for x in orders if x["phase"] == "POSTSEASON"], "POOLED POSTSEASON")
    surv = ST.benjamini_hochberg(pv, q=ST.FDR_Q)
    res["multiplicity"] = {"m": len(pv), "pvalues": pv, "bhSurvivors": [k for k, v in surv.items() if v]}
    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, "h3h_maker_results.json"), "w") as fh:
        json.dump(res, fh, indent=1, sort_keys=True, default=str)
    f = lambda x, d=3: None if x is None else round(x, d)
    for k, v in res["cells"].items():
        print("%-26s placed=%5d filled=%5d fill=%.3f games=%3d | maker ROI(fee .0175)=%s CI=%s p=%s | if-all-filled=%s | taker=%s | spread=%.1fc q~%s" % (
            k, v["placed"], v["filled"], v["fillRate"] or 0, v["games"], f(v["roi_fee0175"]["point"]),
            [f(x) for x in v["roi_fee0175"]["ci90"]], f(v["roi_fee0175"]["p"]), f(v["ifEveryOrderHadFilled_roi"]["point"]),
            f(v["takerAlternative_roi"]["point"]), v["meanSpreadCents"] or 0, v["medianQueueAhead"]))
    print("m =", res["multiplicity"]["m"], "BH survivors", res["multiplicity"]["bhSurvivors"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
