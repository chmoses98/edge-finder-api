#!/usr/bin/env python3
"""
MRV-PB-001 (RSCH-0026 forward score + preregistered rerun), MRV-PB-002
(price-band post-fee ROI, September) and MRV-LIQ-001 (spread / volume
buckets) on the observation archive + settlements.  RESEARCH ONLY.

Usage: run_pricebands.py [--start 2026-09-01 --end 2026-09-21]
Writes data/edgelab/research_artifacts/market_structure/pricebands_sep.json
"""
import argparse
import collections
import gzip
import json
import math
import os
import sys
from datetime import timezone

sys.path.insert(0, os.path.dirname(__file__))
from _common import REPO, write_artifact, now_iso, price_band  # noqa: E402

from lib.edgelab.research.market_structure import economics as E, stats as ST  # noqa: E402
from lib.edgelab.research.market_structure.identity import parse_ticker  # noqa: E402
from lib.edgelab.research.market_structure.settlements import build_outcomes  # noqa: E402

FAMS = ("game_result", "game_total", "inning_result", "inning_total", "team_total", "winning_margin",
        "first_inning_run", "pitcher_strikeouts", "pitcher_outs")
FROZEN_0026 = os.path.join(REPO, "data", "edgelab", "analytics", "frozen_mlb_rsch_0026_forward_model.json")


def _ts(s):
    from datetime import datetime
    return int(datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp())


from lib.edgelab.research.ladder_semantics import observation_quote_cents as _cents  # noqa: E402


def last_pregame_quotes(start, end):
    """{ticker: row} latest valid pregame observation with a two-sided YES book (bid<ask), plus identity fields."""
    d = os.path.join(REPO, "data", "edgelab", "observations")
    best = {}
    for fn in sorted(os.listdir(d)):
        date = fn[:10]
        if date < start or date > end:
            continue
        with gzip.open(os.path.join(d, fn), "rt") as f:
            for line in f:
                r = json.loads(line)
                if r.get("validationStatus") != "valid" or r.get("yesBid") is None or r.get("yesAsk") is None:
                    continue
                t = r["marketTicker"]
                cur = best.get(t)
                if cur is not None and cur["capturedAt"] >= r["capturedAt"]:
                    continue
                # identity for family (exchange families only) and scheduled start
                ident = parse_ticker(t)
                fam = r.get("marketFamily")
                if ident.get("status") == "RESOLVED":
                    start_ts = int(ident["scheduledStartUtc"].replace(tzinfo=timezone.utc).timestamp())
                    gkey = ident["physicalGameKey"]
                    fam = ident["family"]
                    gdate = ident["gameDate"]
                else:
                    if not r.get("scheduledStart") or fam not in FAMS:
                        continue
                    start_ts = _ts(r["scheduledStart"])
                    gkey = (r.get("eventTicker") or t).split("-")[1] if "-" in (r.get("eventTicker") or t) else r.get("gameId")
                    gdate = date
                if fam not in FAMS:
                    continue
                cap = _ts(r["capturedAt"])
                if cap >= start_ts or r.get("gameStartedAtCapture") is True:
                    continue
                bid, ask = _cents(r["yesBid"]), _cents(r["yesAsk"])
                if bid is None or ask is None or not (0 < bid < 100 and 0 < ask < 100 and bid < ask):
                    continue
                no_ask = _cents(r["noAsk"]) if r.get("noAsk") is not None else None
                best[t] = {"ticker": t, "capturedAt": r["capturedAt"], "family": fam, "physicalGameKey": gkey, "gameDate": gdate,
                           "bid": bid, "ask": ask, "noAsk": no_ask, "spread": ask - bid, "volume": r.get("volume") or 0.0,
                           "minutesToStart": (start_ts - cap) / 60.0}
    return best


def sigmoid(x):
    return 1.0 / (1.0 + math.exp(-x))


def logit(p):
    p = min(max(p, 0.01), 0.99)
    return math.log(p / (1 - p))


def fit_beta(rows, base, lo=0.2, hi=2.0):
    """1-D NLL minimisation over beta by golden-section search (rows carry lf=logit(fair), y)."""
    lb = logit(base)
    def nll(b):
        s = 0.0
        for r in rows:
            p = sigmoid(lb + b * (r["lf"] - lb))
            p = min(max(p, 1e-9), 1 - 1e-9)
            s -= math.log(p) if r["y"] else math.log(1 - p)
        return s
    g = (math.sqrt(5) - 1) / 2
    a, b = lo, hi
    c, d = b - g * (b - a), a + g * (b - a)
    fc, fd = nll(c), nll(d)
    for _ in range(40):
        if fc < fd:
            b, d, fd = d, c, fc
            c = b - g * (b - a); fc = nll(c)
        else:
            a, c, fc = c, d, fd
            d = a + g * (b - a); fd = nll(d)
    return (a + b) / 2


def brier(rows, pfn):
    return sum((pfn(r) - r["y"]) ** 2 for r in rows) / len(rows) if rows else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2026-09-01")
    ap.add_argument("--end", default="2026-09-21")
    args = ap.parse_args()
    outcomes = build_outcomes(REPO)
    quotes = last_pregame_quotes(args.start, args.end)
    rows = []
    for t, q in quotes.items():
        oc = outcomes.get(t)
        if oc not in ("YES", "NO"):
            continue
        q = dict(q)
        q["y"] = 1 if oc == "YES" else 0
        q["fair"] = (q["bid"] + q["ask"]) / 200.0
        q["lf"] = logit(q["fair"])
        rows.append(q)
    games = {r["physicalGameKey"] for r in rows}
    dates = sorted({r["gameDate"] for r in rows})
    desc = {"rows": len(rows), "games": len(games), "dates": len(dates), "firstDate": dates[0] if dates else None, "lastDate": dates[-1] if dates else None,
            "medianMinutesToStart": sorted(r["minutesToStart"] for r in rows)[len(rows) // 2] if rows else None,
            "shareWithin30min": (sum(1 for r in rows if r["minutesToStart"] <= 30) / len(rows)) if rows else None,
            "byFamily": dict(collections.Counter(r["family"] for r in rows))}

    # ---- PB-002: price-band ROI cells ----
    def roi_fn(rows_):
        c = sum(r["cash"] for r in rows_)
        return (sum(r["pl"] for r in rows_) / c) if c > 0 else None
    cell_rows = collections.defaultdict(list)
    for r in rows:
        for side in ("YES", "NO"):
            price = r["ask"] if side == "YES" else (r["noAsk"] if r["noAsk"] is not None else 100 - r["bid"])
            won = (r["y"] == 1) if side == "YES" else (r["y"] == 0)
            so = E.settlement_order(price, won)
            if not so:
                continue
            rr = {"physicalGameKey": r["physicalGameKey"], "gameDate": r["gameDate"], "pl": so["netProfitLoss"], "cash": so["actualCashConsumed"],
                  "price": price, "spread": r["spread"], "volume": r["volume"], "family": r["family"], "side": side}
            cell_rows[(r["family"], side, price_band(price))].append(rr)
            cell_rows[("ALL", side, price_band(price))].append(rr)
            cell_rows[(r["family"], side, "ALL")].append(rr)
    pb_tests = {}
    pv = {}
    for key, rs in cell_rows.items():
        k = "|".join(str(x) for x in key)
        res = ST.cluster_bootstrap_ratio(rs, "pl", "cash")
        res["dates"] = len({r["gameDate"] for r in rs})
        res["cash"] = round(sum(r["cash"] for r in rs), 2)
        res["stability"] = ST.date_half_stability(rs, roi_fn)
        pb_tests[k] = res
        if key[0] != "ALL" and key[2] != "ALL":
            pv["PB002|" + k] = res["pPercentile"]

    # ---- LIQ-001: spread and volume buckets (YES side, pooled; 2 registered tests) ----
    yes_rows = [r for rs in cell_rows.values() for r in rs if r["side"] == "YES"]
    # dedupe (rows were added to 3 cells)
    seen = set(); uniq = []
    for r in yes_rows:
        k = (r["physicalGameKey"], r["family"], r["price"], r["spread"], r["volume"])
        if k in seen:
            continue
        seen.add(k); uniq.append(r)
    def diff_fn(flag_key):
        def f(rs):
            a = roi_fn([r for r in rs if r[flag_key] == 1]); b = roi_fn([r for r in rs if r[flag_key] == 0])
            return None if a is None or b is None else a - b
        return f
    vols = sorted(r["volume"] for r in uniq)
    lo_cut, hi_cut = vols[len(vols) // 3], vols[2 * len(vols) // 3]
    for r in uniq:
        r["wide"] = 1 if r["spread"] >= 4 else (0 if r["spread"] <= 2 else None)
        r["lowVol"] = 1 if r["volume"] <= lo_cut else (0 if r["volume"] >= hi_cut else None)
    liq = {"wideMinusTight": ST.cluster_bootstrap([r for r in uniq if r["wide"] is not None], diff_fn("wide")),
           "lowMinusHighVolume": ST.cluster_bootstrap([r for r in uniq if r["lowVol"] is not None], diff_fn("lowVol")),
           "volumeTercileCuts": [lo_cut, hi_cut],
           "nWide": sum(1 for r in uniq if r["wide"] == 1), "nTight": sum(1 for r in uniq if r["wide"] == 0)}
    pv["LIQ001|wideMinusTight"] = liq["wideMinusTight"]["pPercentile"]
    pv["LIQ001|lowMinusHighVolume"] = liq["lowMinusHighVolume"]["pPercentile"]

    # ---- PB-001: RSCH-0026 forward score with the frozen beta, then the preregistered rerun ----
    frozen = json.load(open(FROZEN_0026))
    beta0, base0 = frozen["beta"], frozen["base"]
    lb0 = logit(base0)
    fwd = [r for r in rows]  # every row here settled after 2026-08-28 by construction (September games)
    def paired_delta(rs):
        if not rs:
            return None
        return brier(rs, lambda r: sigmoid(lb0 + beta0 * (r["lf"] - lb0))) - brier(rs, lambda r: sigmoid(r["lf"]))
    fwd_res = ST.cluster_bootstrap(fwd, paired_delta)
    fwd_res.update(marketBrier=brier(fwd, lambda r: sigmoid(r["lf"])), shrunkBrier=brier(fwd, lambda r: sigmoid(lb0 + beta0 * (r["lf"] - lb0))),
                   beta=beta0, base=base0, note="both forecasts clamped to [0.01,0.99] symmetrically; population = all settled non-prop rows with a two-sided last pregame quote (broader than RSCH-0026's production-evaluated corpus)")
    # rerun: TRAIN = first half of dates, VAL = second half, base = TRAIN YES rate
    cut = dates[len(dates) // 2]
    train = [r for r in rows if r["gameDate"] < cut]; val = [r for r in rows if r["gameDate"] >= cut]
    base_t = sum(r["y"] for r in train) / len(train) if train else None
    beta_t = fit_beta(train, base_t) if train else None
    lbt = logit(base_t) if base_t else None
    def beta_stat(rs):
        return fit_beta(rs, base_t)
    beta_ci = ST.cluster_bootstrap(train, beta_stat, n_resamples=200)
    def val_delta(rs):
        if not rs:
            return None
        return brier(rs, lambda r: sigmoid(lbt + beta_t * (r["lf"] - lbt))) - brier(rs, lambda r: sigmoid(r["lf"]))
    val_res = ST.cluster_bootstrap(val, val_delta)
    bands_improving = 0
    band_table = {}
    for b in ("00-20", "20-40", "40-60", "60-80", "80-100"):
        rs = [r for r in val if price_band(int(round(r["fair"] * 100))) == b]
        if rs:
            d = val_delta(rs)
            band_table[b] = {"n": len(rs), "delta": d, "marketBias": sum(r["fair"] - r["y"] for r in rs) / len(rs)}
            if d is not None and d < 0:
                bands_improving += 1
    rerun = {"trainRows": len(train), "trainGames": len({r["physicalGameKey"] for r in train}), "valRows": len(val), "valGames": len({r["physicalGameKey"] for r in val}),
             "cutDate": cut, "baseTrain": base_t, "betaTrain": beta_t, "betaCI": beta_ci, "valBrierDelta": val_res, "valBands": band_table,
             "bandsImproving": bands_improving, "gateBetaCIExcludes1": (beta_ci["ciLow"] is not None and (beta_ci["ciLow"] > 1 or beta_ci["ciHigh"] < 1)),
             "gateValDeltaNegative": (val_res["point"] is not None and val_res["point"] < 0 and val_res["ciHigh"] is not None and val_res["ciHigh"] < 0)}
    pv["PB001|forwardBrierDelta"] = fwd_res["pPercentile"]
    pv["PB001|rerunValBrierDelta"] = val_res["pPercentile"]
    bh = ST.benjamini_hochberg(pv)
    out = {"generatedAt": now_iso(), "tier": "TIER_B_EXPLORATORY", "window": [args.start, args.end], "descriptive": desc,
           "pb001": {"forward": fwd_res, "rerun": rerun}, "pb002": pb_tests, "liq001": liq, "bh": bh,
           "conventions": "last valid pregame two-sided YES quote per ticker; YES at ask, NO at noAsk else 100-bid; USD 10 taker order; outcomes with the >=N ladder correction; cluster = physical game; BH q=0.10 over all PB-002 family x side x band cells + PB-001 (2) + LIQ-001 (2)"}
    p = write_artifact("pricebands_sep.json", out)
    print("wrote", p); print(json.dumps(desc, indent=1))
    print("PB-001 forward", {k: fwd_res[k] for k in ("point", "ciLow", "ciHigh", "pTwoSided", "clusters", "n", "marketBrier", "shrunkBrier")})
    print("PB-001 rerun", {k: rerun[k] for k in ("betaTrain", "gateBetaCIExcludes1", "gateValDeltaNegative", "bandsImproving")}, rerun["betaCI"]["ciLow"], rerun["betaCI"]["ciHigh"], rerun["valBrierDelta"]["point"])
    print("LIQ-001", {k: (v["point"], v["ciLow"], v["ciHigh"]) for k, v in liq.items() if isinstance(v, dict)})
    surv = [k for k, v in bh.items() if v]
    print("BH survivors", surv)
    for k in surv:
        if k.startswith("PB002|"):
            t = pb_tests[k[6:]]; print(k, t["point"], t["ciLow"], t["ciHigh"], t["n"], t["clusters"])


if __name__ == "__main__":
    main()
