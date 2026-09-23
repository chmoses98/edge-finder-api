#!/usr/bin/env python3
"""
MRV-XV-001..004: sportsbook no-vig probabilities vs Kalshi moneyline /
total ladder on the ALPHA-0002 prospective corpus (research branch,
hydrated outside git).  RESEARCH ONLY.

Usage: run_sharp.py --corpus-root <dir with quotes/ odds/> [--dates ...]
Writes data/edgelab/research_artifacts/market_structure/sharp_vs_kalshi.json
"""
import argparse
import collections
import gzip
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))
from _common import REPO, write_artifact, now_iso  # noqa: E402

from lib.edgelab.research.market_structure import sharp as SH, economics as E, stats as ST, book as BK  # noqa: E402
from lib.edgelab.research.market_structure.identity import parse_ticker  # noqa: E402
from lib.edgelab.research.market_structure.leadlag import ladder_implied_mean  # noqa: E402
from lib.edgelab.research.market_structure.settlements import build_outcomes  # noqa: E402

DISAGREE_PP = 3.0        # preregistered by ALPHA-0002 D3; not tuned here
MAX_QUOTE_AGE_S = 1800   # a book quote older than 30 min relative to the run is stale
STALE_KALSHI_SPREAD = 10 # cents; wider books excluded from executable tests


def _ts(s):
    return int(datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp())


def _iter(path):
    if not os.path.exists(path):
        return
    with gzip.open(path, "rt") as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus-root", required=True)
    ap.add_argument("--dates", default="")
    args = ap.parse_args()
    dates = args.dates.split(",") if args.dates else sorted(f[:10] for f in os.listdir(os.path.join(args.corpus_root, "quotes")) if f.endswith(".jsonl.gz"))
    outcomes = build_outcomes(REPO)

    # 1. Kalshi ML + total quotes per (run, physicalGame) FROM ORDER BOOKS.
    # The collector stores per-game quotes only inside the book records (the
    # quote rows for FULL_MICROSTRUCTURE tickers carry null prices) and
    # de-duplicates unchanged books by fingerprint reference; a reference is
    # resolved to the identical earlier book, which is exact, not stale.
    ml = collections.defaultdict(dict)      # (runAt, gkey) -> {"HOME": (bid, ask), "AWAY": (bid, ask)}
    tot = collections.defaultdict(dict)     # (runAt, gkey) -> {rung: mid}
    game_meta = {}
    book_by_fp = {}
    book_stats = collections.Counter()
    def _ingest_book(run_at, ticker, book):
        ident = parse_ticker(ticker)
        if ident.get("status") != "RESOLVED":
            return
        yb, ya, _, _ = BK.top_of_book(book)
        if yb is None or ya is None:
            book_stats["oneSided"] += 1
            return
        key = (run_at, ident["physicalGameKey"])
        game_meta.setdefault(ident["physicalGameKey"], ident)
        if ident["seriesTicker"] == "KXMLBGAME":
            ml[key][ident["side"]] = (yb, ya)
        elif ident["seriesTicker"] == "KXMLBTOTAL":
            tot[key][ident["rung"]] = (yb + ya) / 2.0
    for date in dates:
        for b in _iter(os.path.join(args.corpus_root, "books", "%s.jsonl.gz" % date)):
            if b.get("orderbook"):
                book_by_fp[b["fp"]] = b["orderbook"]
                book_stats["books"] += 1
                _ingest_book(b["capturedAt"], b["marketTicker"], b["orderbook"])
    for date in dates:
        for b in _iter(os.path.join(args.corpus_root, "books_unchanged", "%s.jsonl.gz" % date)):
            ob = book_by_fp.get(b.get("fp"))
            if ob is None:
                book_stats["danglingRefs"] += 1
                continue
            book_stats["resolvedRefs"] += 1
            _ingest_book(b["capturedAt"], b["marketTicker"], ob)
    # 2. sportsbook rows per run, matched on abbreviations + commence within 45 min
    panel = []
    unmatched = collections.Counter()
    by_game_runs = collections.defaultdict(list)
    for date in dates:
        for row in _iter(os.path.join(args.corpus_root, "odds", "%s.jsonl.gz" % date)):
            pr = SH.odds_row_probabilities(row)
            if not pr["awayAbbr"] or not pr["homeAbbr"] or pr["commenceTs"] is None:
                unmatched["unmappedTeamOrTime"] += 1
                continue
            run_at = row["capturedAt"]
            run_ts = _ts(run_at)
            # find the Kalshi game with same teams and start within 45 min
            cands = [g for g, ident in game_meta.items() if ident["awayTeam"] == pr["awayAbbr"] and ident["homeTeam"] == pr["homeAbbr"]
                     and abs(int(ident["scheduledStartUtc"].replace(tzinfo=timezone.utc).timestamp()) - pr["commenceTs"]) <= 45 * 60]
            if len(cands) != 1:
                unmatched["noUniqueKalshiGame"] += 1
                continue
            gkey = cands[0]
            ident = game_meta[gkey]
            start_ts = int(ident["scheduledStartUtc"].replace(tzinfo=timezone.utc).timestamp())
            if run_ts >= start_ts:
                unmatched["postStart"] += 1
                continue
            mq = ml.get((run_at, gkey))
            if not mq or "HOME" not in mq:
                unmatched["noKalshiMLAtRun"] += 1
                continue
            hb, ha = mq["HOME"]
            k_mid = (hb + ha) / 2.0
            pin = pr["books"].get("pinnacle")
            pin_p = pin.get("pHome") if pin else None
            pin_age = None
            if pin and pin.get("lastUpdate"):
                pin_age = run_ts - _ts(pin["lastUpdate"])
            cons_p, nb = SH.consensus(pr["books"])
            tp = None
            if pin and pin.get("totalPoint") is not None:
                tp = pin["totalPoint"]
            rec = {"runAt": run_at, "runTs": run_ts, "physicalGameKey": gkey, "gameDate": ident["gameDate"],
                   "minutesToStart": (start_ts - run_ts) / 60.0, "kalshiHomeBid": hb, "kalshiHomeAsk": ha, "kalshiMid": k_mid,
                   "awayBid": mq.get("AWAY", (None, None))[0], "awayAsk": mq.get("AWAY", (None, None))[1],
                   "pinnacleHome": pin_p, "pinnacleAgeS": pin_age, "consensusHome": cons_p, "consensusBooks": nb,
                   "pinnacleTotalPoint": tp, "ladderMids": tot.get((run_at, gkey)) or {}}
            panel.append(rec)
            by_game_runs[gkey].append(rec)
    # 3. targets: next-run and last-pregame Kalshi mid change
    for gkey, recs in by_game_runs.items():
        recs.sort(key=lambda r: r["runTs"])
        last = recs[-1]
        for i, r in enumerate(recs):
            r["dMidNext"] = (recs[i + 1]["kalshiMid"] - r["kalshiMid"]) if i + 1 < len(recs) else None
            r["dMidLast"] = (last["kalshiMid"] - r["kalshiMid"]) if r is not last else None
            r["minutesToNext"] = ((recs[i + 1]["runTs"] - r["runTs"]) / 60.0) if i + 1 < len(recs) else None
            r["lastMinutesToStart"] = last["minutesToStart"]
            # ladder-mean change to next on a fixed rung set
            if i + 1 < len(recs) and r["ladderMids"] and recs[i + 1]["ladderMids"]:
                common = set(r["ladderMids"]) & set(recs[i + 1]["ladderMids"])
                if len(common) >= 3:
                    r["ladderMeanNow"] = ladder_implied_mean({k: r["ladderMids"][k] for k in common})
                    r["dLadderMeanNext"] = ladder_implied_mean({k: recs[i + 1]["ladderMids"][k] for k in common}) - r["ladderMeanNow"]
            # implied median of the ladder (first rung whose mid < 50)
            if r["ladderMids"]:
                below = sorted(k for k, v in r["ladderMids"].items() if v < 50)
                r["ladderMedianRung"] = below[0] if below else None

    def add_d(rows, book_key, dkey):
        for r in rows:
            p = r.get(book_key)
            r[dkey] = None if p is None else (p * 100.0 - r["kalshiMid"])   # percentage points, + means book higher on HOME
    add_d(panel, "pinnacleHome", "dPin")
    add_d(panel, "consensusHome", "dCons")

    # 4. registered tests
    def slope_fn(xk, yk):
        return lambda rows: ST.ols_slope([(r.get(xk), r.get(yk)) for r in rows])
    tests = {}
    tests["MRV-XV-001_slopeNext"] = ST.cluster_bootstrap([r for r in panel if r.get("dPin") is not None and r.get("dMidNext") is not None], slope_fn("dPin", "dMidNext"))
    tests["MRV-XV-001_slopeLast"] = ST.cluster_bootstrap([r for r in panel if r.get("dPin") is not None and r.get("dMidLast") is not None], slope_fn("dPin", "dMidLast"))
    tests["MRV-XV-003_slopeNext"] = ST.cluster_bootstrap([r for r in panel if r.get("dCons") is not None and r.get("dMidNext") is not None], slope_fn("dCons", "dMidNext"))
    tests["MRV-XV-003_slopeLast"] = ST.cluster_bootstrap([r for r in panel if r.get("dCons") is not None and r.get("dMidLast") is not None], slope_fn("dCons", "dMidLast"))

    # XV-002 / XV-003 ROI: first qualifying capture per game-side, |d| >= 3pp, buy the book's side at executable price
    def episodes(dkey):
        eps = []
        seen = set()
        for r in sorted(panel, key=lambda r: r["runTs"]):
            d = r.get(dkey)
            if d is None or abs(d) < DISAGREE_PP:
                continue
            if (r["kalshiHomeAsk"] - r["kalshiHomeBid"]) > STALE_KALSHI_SPREAD:
                continue
            if dkey == "dPin" and r.get("pinnacleAgeS") is not None and r["pinnacleAgeS"] > MAX_QUOTE_AGE_S:
                continue
            side = "HOME" if d > 0 else "AWAY"
            k = (r["physicalGameKey"], side)
            if k in seen:
                continue
            seen.add(k)
            # buy HOME YES at home ask; buy AWAY == HOME NO at 100 - home bid (the AWAY contract is the same book's complement in a 2-way game)
            price = r["kalshiHomeAsk"] if side == "HOME" else 100 - r["kalshiHomeBid"]
            home_t = "KXMLBGAME-%s-%s" % (r["physicalGameKey"], game_meta[r["physicalGameKey"]]["homeTeam"])
            oc = outcomes.get(home_t)
            won = None if oc is None else ((oc == "YES") if side == "HOME" else (oc == "NO"))
            # executable CLV to last pregame capture (same side): last mid vs entry price, positive good
            last_mid = r["kalshiMid"] + (r["dMidLast"] or 0.0) if r.get("dMidLast") is not None else None
            clv = None if last_mid is None else ((last_mid - price) if side == "HOME" else ((100 - last_mid) - price))
            ep = {"physicalGameKey": r["physicalGameKey"], "gameDate": r["gameDate"], "side": side, "d": d, "price": price,
                  "minutesToStart": r["minutesToStart"], "won": won, "clvCents": clv}
            if won is not None:
                so = E.settlement_order(price, won)
                if so:
                    ep["netPL"], ep["cash"] = so["netProfitLoss"], so["actualCashConsumed"]
            eps.append(ep)
        return eps
    def roi(rows):
        c = sum(r["cash"] for r in rows if r.get("cash"))
        return (sum(r["netPL"] for r in rows if r.get("cash")) / c) if c > 0 else None
    def mean_clv(rows):
        return ST.mean([r.get("clvCents") for r in rows])
    ep_pin, ep_cons = episodes("dPin"), episodes("dCons")
    settled_pin = [e for e in ep_pin if e.get("cash")]
    settled_cons = [e for e in ep_cons if e.get("cash")]
    tests["MRV-XV-002_roi"] = ST.cluster_bootstrap(settled_pin, roi)
    tests["MRV-XV-002_clv"] = ST.cluster_bootstrap([e for e in ep_pin if e.get("clvCents") is not None], mean_clv)
    tests["MRV-XV-003_roi"] = ST.cluster_bootstrap(settled_cons, roi)
    tests["MRV-XV-002_stability"] = ST.date_half_stability(settled_pin, roi)
    # XV-004 totals
    t4 = [r for r in panel if r.get("pinnacleTotalPoint") is not None and r.get("ladderMedianRung") is not None and r.get("dLadderMeanNext") is not None]
    for r in t4:
        r["lineGap"] = r["pinnacleTotalPoint"] - (r["ladderMedianRung"] - 0.5)
    tests["MRV-XV-004_slope"] = ST.cluster_bootstrap(t4, slope_fn("lineGap", "dLadderMeanNext"))

    # descriptive: disagreement distribution and the break-even disagreement implied by the fitted slope
    def share_ge(rows, key, thr):
        vals = [abs(r[key]) for r in rows if r.get(key) is not None]
        return (sum(1 for v in vals if v >= thr) / len(vals)) if vals else None
    dist = {}
    for dk in ("dPin", "dCons"):
        dist[dk] = {"n": sum(1 for r in panel if r.get(dk) is not None),
                    "meanAbs": ST.mean([abs(r[dk]) for r in panel if r.get(dk) is not None]),
                    "shareGe1pp": share_ge(panel, dk, 1.0), "shareGe2pp": share_ge(panel, dk, 2.0), "shareGe3pp": share_ge(panel, dk, 3.0),
                    "shareGe5pp": share_ge(panel, dk, 5.0)}
    # round-trip taker cost at a 50c contract with a 2c spread: half-spread in + fee in + half-spread out + fee out
    rt_cost = 1.0 + E.fee_drag_cents(50) + 1.0 + E.fee_drag_cents(50)
    hold_cost = 1.0 + E.fee_drag_cents(50)   # enter at ask, hold to settlement: half-spread + one fee
    be = {}
    for k in ("MRV-XV-001_slopeLast", "MRV-XV-003_slopeLast"):
        sl = tests[k].get("point")
        be[k] = {"slopeLast": sl, "roundTripTakerCostCents": rt_cost, "holdToSettlementCostCents": hold_cost,
                 "breakEvenAbsDppRoundTrip": (rt_cost / sl) if sl and sl > 0 else None,
                 "breakEvenAbsDppHold": (hold_cost / sl) if sl and sl > 0 else None}
    pv = {k: v.get("pPercentile") for k, v in tests.items() if isinstance(v, dict) and "pTwoSided" in v}
    bh = ST.benjamini_hochberg(pv)
    desc = {"panelRows": len(panel), "games": len(by_game_runs), "dates": len({r["gameDate"] for r in panel}),
            "runsPerGameMedian": sorted(len(v) for v in by_game_runs.values())[len(by_game_runs) // 2] if by_game_runs else None,
            "meanAbsDPin": ST.mean([abs(r["dPin"]) for r in panel if r.get("dPin") is not None]),
            "meanAbsDCons": ST.mean([abs(r["dCons"]) for r in panel if r.get("dCons") is not None]),
            "shareAbsDPinGe3": (sum(1 for r in panel if r.get("dPin") is not None and abs(r["dPin"]) >= 3) / max(1, sum(1 for r in panel if r.get("dPin") is not None))),
            "medianMinutesToNext": sorted(r["minutesToNext"] for r in panel if r.get("minutesToNext"))[len([r for r in panel if r.get("minutesToNext")]) // 2] if any(r.get("minutesToNext") for r in panel) else None,
            "pinnacleAgeMedianS": sorted(r["pinnacleAgeS"] for r in panel if r.get("pinnacleAgeS") is not None)[len([r for r in panel if r.get("pinnacleAgeS") is not None]) // 2] if any(r.get("pinnacleAgeS") is not None for r in panel) else None,
            "unmatched": dict(unmatched), "bookStats": dict(book_stats), "mlRunGames": len(ml), "totalLadderRunGames": len(tot),
            "xv002Episodes": len(ep_pin), "xv002Settled": len(settled_pin), "xv002Games": len({e["physicalGameKey"] for e in settled_pin}),
            "xv003Episodes": len(ep_cons), "xv003Settled": len(settled_cons)}
    out = {"generatedAt": now_iso(), "tier": "TIER_B_EXPLORATORY", "dates": dates, "descriptive": desc, "disagreementDistribution": dist, "breakEven": be, "tests": tests, "bh": bh,
           "timingCaveat": "capturedAt is the run start; the Odds API leg is fetched later in the same run (book last_update can be a few minutes NEWER than the Kalshi book), so a small part of any same-run disagreement may already be stale on the Kalshi side; horizons are hours, which bounds the contamination",
           "conventions": "d = book no-vig HOME prob (pp) - Kalshi HOME fair mid (cents); slopes are OLS of Kalshi mid change (cents) on d; XV-002/003: first capture per game-side with |d|>=3pp, spread<=10c, Pinnacle quote age<=30min, USD 10 taker order on the book's side (HOME at ask, AWAY at 100-bid), settled with archived outcomes",
           "episodesSample": ep_pin[:100]}
    p = write_artifact("sharp_vs_kalshi.json", out)
    print("wrote", p); print(json.dumps(desc, indent=1)); 
    for k, v in tests.items():
        print(k, {kk: v.get(kk) for kk in ("point", "ciLow", "ciHigh", "pTwoSided", "clusters", "n")} if isinstance(v, dict) else v)
    print("BH", bh)


if __name__ == "__main__":
    main()
