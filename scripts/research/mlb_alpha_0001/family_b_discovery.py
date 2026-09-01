#!/usr/bin/env python3
"""MLB-ALPHA-0001 Family B discovery: cross-market / structural
inefficiencies, on DISCOVERY dates only.

Legs are simultaneous by construction: only quotes from the SAME capture
file (provenance.sourceFile -- one API sweep) are ever combined, and only
pregame (capturedAt strictly before eventTicker-decoded scheduled start),
active-status quotes with two-sided books participate.

Relationships audited (settlement semantics per the program's proven
corrections: total ladders and player props settle "value >= rung";
team_total/winning_margin thresholds are half-point):

  B1  crossed single book: yesAsk < yesBid                (pure arb flag)
  B2  three-way F5 (HOME/AWAY/TIE, MECE):
        buy all 3 YES at ask, payout 100   -> arb iff sum(ask) + fees < 100
        buy all 3 NO  at 100-bid, payout 200 -> arb iff 300-sum(bid)+fees < 200
        relative-value signal: |sum(mid) - 100|
  B3  ladder monotonicity, same family+event(+player), rungs t1 < t2
      (YES(t2) subset of YES(t1)): buy YES(t1)@ask + buy NO(t2)@100-bid,
        min payout 100 -> arb iff ask(t1) < bid(t2) (before fees)
        relative-value signal: mid(t1) - mid(t2) < 0 (inversion magnitude)
  B4  dominance across families (event subset relationships):
        winning_margin(team, any rung) subset of game_result(team win)
        team_total(team, rung K) subset of game_total(rung K)
        hitter_hits(player, N)  subset of hitter_total_bases(player, N)
        hitter_hits(player, N)  subset of hitter_hits_runs_rbis(player, N)
      dominated=D subset of dominating=S: buy YES(D is NOT bought; the
      arb is buy YES(S)@ask + buy NO(D)@100-bid, min payout 100
        -> arb iff ask(S) < bid(D); RV signal: mid(S) - mid(D) < 0

Every relationship instance is counted (audited); executable violations
are reported with pre-fee locked return, fees (1 contract per leg,
taker), post-fee locked return, capital required, and persistence
(consecutive capture batches). PURE ARBITRAGE is kept strictly separate
from RELATIVE VALUE.

Relative-value scoring rule (declared BEFORE outcomes): for each
(relationship instance, game date), take the FIRST pregame batch where
the RV signal magnitude >= 2 cents; enter both legs of the corrective
trade at executable prices ($10 per leg, Tier C realistic execution);
score on corrected settlements. Cluster by game.

RESEARCH ONLY.
"""

import glob
import gzip
import json
import os
import re
import sys
from collections import Counter, defaultdict

import numpy as np

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, REPO)
ART = os.path.join(REPO, "data", "edgelab", "research_artifacts", "mlb_alpha_0001")
EDGELAB = os.path.join(REPO, "data", "edgelab")

from lib.edgelab.kalshi_fees import max_contracts_for_cash, taker_fee  # noqa: E402
from scripts.research.mlb_alpha_0001.build_entry_rows import (  # noqa: E402
    parse_event, parse_ts, iter_jsonl, partition_paths, load_settlement_map)

BOOT = 2000
SEED = 20260902
RV_SIGNAL_CENTS = 2.0
ORDER = 10.00


def leg_fee(price_cents, contracts=1):
    """Taker fee per contract, in dollars; None for an unexecutable price."""
    if price_cents is None or not (0 < price_cents < 100):
        return None
    return taker_fee(contracts, price_cents / 100.0)


def pair_fees_cents(buy_yes_cents, buy_no_cents):
    fy, fn = leg_fee(buy_yes_cents), leg_fee(buy_no_cents)
    if fy is None or fn is None:
        return None
    return (fy + fn) * 100


def tier_c(price_cents, won):
    cents = int(round(price_cents))
    if not (1 <= cents <= 99):
        return None
    p = cents / 100.0
    c = max_contracts_for_cash(ORDER, p)
    if c <= 0:
        return None
    fee = taker_fee(c, p)
    cash = c * p + fee
    net = (c * 1.0 - cash) if won else -cash
    return {"netPL": net, "cash": cash, "fee": fee, "contracts": c}


def load_observations_by_batch(dates):
    """date -> batch(sourceFile) -> list of usable pregame quotes."""
    parts = partition_paths("observations")
    for date in dates:
        if date not in parts:
            continue
        batches = defaultdict(list)
        for r in iter_jsonl(parts[date]):
            ev = parse_event(r.get("eventTicker"))
            if ev is None:
                continue
            game_date, start_utc, _ = ev
            if game_date != date:
                continue
            if (r.get("marketStatus") or "active").lower() not in ("active", "unknown"):
                continue
            if parse_ts(r["capturedAt"]) >= start_utc:
                continue
            yb, ya = r.get("yesBid"), r.get("yesAsk")
            if yb is None or ya is None:
                continue
            src = (r.get("provenance") or {}).get("sourceFile") or r["capturedAt"][:16]
            batches[src].append(r)
        yield date, batches


HIT_RE = re.compile(r"^(KXMLB(?:HIT|TB|HRR|RBI|SB|KS|OUTS))-([0-9A-Z]+)-([A-Z0-9]+?)-(\d+)$")
TOT_RE = re.compile(r"^(KXMLBTOTAL|KXMLBF5TOTAL)-([0-9A-Z]+)-(\d+)$")
TT_RE = re.compile(r"^KXMLBTEAMTOTAL-([0-9A-Z]+)-([A-Z]+?)(\d+)$")
SPREAD_RE = re.compile(r"^(KXMLBSPREAD|KXMLBF5SPREAD)-([0-9A-Z]+)-([A-Z]+?)(\d+)$")
GAME_RE = re.compile(r"^KXMLBGAME-([0-9A-Z]+)-([A-Z]+)$")
F5_RE = re.compile(r"^KXMLBF5-([0-9A-Z]+)-([A-Z]+)$")


def mid(q):
    return (q["yesBid"] + q["yesAsk"]) / 2.0


def audit_batch(quotes, counters, arbs, rv_first, date, batch_id):
    """Audit one simultaneous capture batch."""
    by_ticker = {}
    for q in quotes:
        by_ticker[q["marketTicker"]] = q

    # B1 crossed books
    for t, q in by_ticker.items():
        counters["B1_books_audited"] += 1
        if q["yesAsk"] < q["yesBid"]:
            counters["B1_crossed_book"] += 1
            arbs.append({"kind": "B1_crossed_book", "date": date, "batch": batch_id,
                         "tickers": [t], "preFeeLocked": q["yesBid"] - q["yesAsk"]})

    # index structures
    f5 = defaultdict(dict)          # event -> leg -> quote
    ladders = defaultdict(dict)     # (series,event[,subject]) -> rung -> quote
    ml = defaultdict(dict)          # event -> team -> quote (game_result)
    spread_min = {}                 # (event, team) -> lowest-rung spread quote
    tt = defaultdict(dict)          # (event, team) -> rung -> quote
    gt = defaultdict(dict)          # event -> rung -> quote (game_total only)
    props = defaultdict(dict)       # (series, event, player) -> rung -> quote

    for t, q in by_ticker.items():
        m = F5_RE.match(t)
        if m:
            f5[m.group(1)][m.group(2)] = q
            continue
        m = GAME_RE.match(t)
        if m:
            ml[m.group(1)][m.group(2)] = q
            continue
        m = TOT_RE.match(t)
        if m:
            key = (m.group(1), m.group(2))
            ladders[key][int(m.group(3))] = q
            if m.group(1) == "KXMLBTOTAL":
                gt[m.group(2)][int(m.group(3))] = q
            continue
        m = TT_RE.match(t)
        if m:
            tt[(m.group(1), m.group(2))][int(m.group(3))] = q
            key = ("KXMLBTEAMTOTAL:" + m.group(2), m.group(1))
            ladders[key][int(m.group(3))] = q
            continue
        m = SPREAD_RE.match(t)
        if m:
            key = (m.group(1) + ":" + m.group(3), m.group(2))
            ladders[key][int(m.group(4))] = q
            if m.group(1) == "KXMLBSPREAD":
                cur = spread_min.get((m.group(2), m.group(3)))
                if cur is None or int(m.group(4)) < cur[0]:
                    spread_min[(m.group(2), m.group(3))] = (int(m.group(4)), q)
            continue
        m = HIT_RE.match(t)
        if m:
            key = (m.group(1) + ":" + m.group(3), m.group(2))
            ladders[key][int(m.group(4))] = q
            props[(m.group(1), m.group(2), m.group(3))][int(m.group(4))] = q

    # B2 three-way F5
    for event, legs in f5.items():
        if not all(k in legs for k in ("TIE",)) or len(legs) != 3:
            continue
        counters["B2_threeway_audited"] += 1
        asks = sum(q["yesAsk"] for q in legs.values())
        bids = sum(q["yesBid"] for q in legs.values())
        fy = [leg_fee(q["yesAsk"]) for q in legs.values()]
        fn = [leg_fee(100 - q["yesBid"]) for q in legs.values()]
        if any(x is None for x in fy) or any(x is None for x in fn):
            continue
        fees_yes = sum(fy) * 100
        fees_no = sum(fn) * 100
        if asks + fees_yes < 100:
            counters["B2_yes_arb"] += 1
            arbs.append({"kind": "B2_threeway_buy_all_yes", "date": date,
                         "batch": batch_id, "event": event,
                         "preFeeLocked": 100 - asks, "fees": fees_yes,
                         "postFeeLocked": 100 - asks - fees_yes,
                         "capitalCents": asks + fees_yes})
        if (300 - bids) + fees_no < 200:
            counters["B2_no_arb"] += 1
            arbs.append({"kind": "B2_threeway_buy_all_no", "date": date,
                         "batch": batch_id, "event": event,
                         "preFeeLocked": bids - 100, "fees": fees_no,
                         "postFeeLocked": bids - 100 - fees_no,
                         "capitalCents": 300 - bids + fees_no})
        dev = (sum(mid(q) for q in legs.values())) - 100
        counters["B2_sum_dev_ge2" if abs(dev) >= 2 else "B2_sum_dev_lt2"] += 1

    # B3 ladder monotonicity (adjacent listed rungs)
    for key, rungs in ladders.items():
        ks = sorted(rungs)
        for a, b in zip(ks, ks[1:]):
            qa, qb = rungs[a], rungs[b]
            counters["B3_adjacent_pairs_audited"] += 1
            fees = pair_fees_cents(qa["yesAsk"], 100 - qb["yesBid"])
            if qa["yesAsk"] < qb["yesBid"] and fees is not None:  # executable pure arb
                counters["B3_executable_inversion"] += 1
                arbs.append({"kind": "B3_ladder_inversion", "date": date,
                             "batch": batch_id,
                             "tickers": [qa["marketTicker"], qb["marketTicker"]],
                             "preFeeLocked": qb["yesBid"] - qa["yesAsk"],
                             "fees": fees,
                             "postFeeLocked": qb["yesBid"] - qa["yesAsk"] - fees})
            inv = mid(qb) - mid(qa)
            if inv >= RV_SIGNAL_CENTS:
                counters["B3_rv_signal"] += 1
                k = ("B3", key[0], key[1], a, b)
                if k not in rv_first:
                    rv_first[k] = {"date": date, "batch": batch_id,
                                   "legLow": qa, "legHigh": qb, "signal": inv}

    # B4 dominance
    def dominance(kind, q_super, q_sub, ident):
        counters["B4_%s_audited" % kind] += 1
        fees = pair_fees_cents(q_super["yesAsk"], 100 - q_sub["yesBid"])
        if q_super["yesAsk"] < q_sub["yesBid"] and fees is not None:
            counters["B4_%s_executable" % kind] += 1
            arbs.append({"kind": "B4_" + kind, "date": date, "batch": batch_id,
                         "tickers": [q_super["marketTicker"], q_sub["marketTicker"]],
                         "preFeeLocked": q_sub["yesBid"] - q_super["yesAsk"],
                         "fees": fees,
                         "postFeeLocked": q_sub["yesBid"] - q_super["yesAsk"] - fees})
        inv = mid(q_sub) - mid(q_super)
        if inv >= RV_SIGNAL_CENTS:
            counters["B4_%s_rv_signal" % kind] += 1
            k = ("B4", kind) + ident
            if k not in rv_first:
                rv_first[k] = {"date": date, "batch": batch_id,
                               "legLow": q_super, "legHigh": q_sub, "signal": inv}

    for (event, team), (rung, q_spread) in spread_min.items():
        q_ml = ml.get(event, {}).get(team)
        if q_ml is not None:
            dominance("spread_vs_ml", q_ml, q_spread, (event, team, rung))
    for (event, team), rungs in tt.items():
        for k, q_tt in rungs.items():
            q_gt = gt.get(event, {}).get(k)
            if q_gt is not None:
                dominance("tt_vs_gt", q_gt, q_tt, (event, team, k))
    for (series, event, player), rungs in props.items():
        if series != "KXMLBHIT":
            continue
        for k, q_hit in rungs.items():
            for sup_series in ("KXMLBTB", "KXMLBHRR"):
                q_sup = props.get((sup_series, event, player), {}).get(k)
                if q_sup is not None:
                    dominance("hits_vs_" + sup_series[5:].lower(), q_sup, q_hit,
                              (event, player, k, sup_series))


def score_rv(rv_first, settled, rng):
    """Score first-firing RV pair trades on corrected settlements."""
    groups = defaultdict(list)
    for k, v in rv_first.items():
        kind = k[0] + ("_" + k[1] if k[0] == "B4" else "_" + (k[1].split(":")[0] if isinstance(k[1], str) else str(k[1])))
        lo, hi = v["legLow"], v["legHigh"]
        r_lo, r_hi = settled.get(lo["marketTicker"]), settled.get(hi["marketTicker"])
        if r_lo not in ("YES", "NO") or r_hi not in ("YES", "NO"):
            continue
        e_lo = tier_c(lo["yesAsk"], r_lo == "YES")             # buy underpriced YES
        e_hi = tier_c(100 - hi["yesBid"], r_hi == "NO")        # buy overpriced NO
        if e_lo is None or e_hi is None:
            continue
        gd = parse_event(lo.get("eventTicker"))
        game = (v["date"] + ":" + lo["eventTicker"].split("-", 1)[1]) if gd else v["date"]
        groups[kind].append({
            "date": v["date"], "game": game, "signal": v["signal"],
            "netPL": e_lo["netPL"] + e_hi["netPL"],
            "cash": e_lo["cash"] + e_hi["cash"],
        })
    out = []
    for kind, items in sorted(groups.items()):
        games = defaultdict(float)
        cash_g = defaultdict(float)
        for o in items:
            games[o["game"]] += o["netPL"]
            cash_g[o["game"]] += o["cash"]
        total_net = sum(o["netPL"] for o in items)
        total_cash = sum(o["cash"] for o in items)
        res = {"rvKind": kind, "pairs": len(items),
               "uniqueGames": len(games),
               "dates": len({o["date"] for o in items}),
               "netPL": round(total_net, 2),
               "netROI": round(total_net / total_cash, 4) if total_cash else None}
        if len(games) >= 20:
            g = list(games)
            net = np.array([games[x] for x in g])
            cash = np.array([cash_g[x] for x in g])
            idx = rng.integers(0, len(g), size=(BOOT, len(g)))
            rois = idx_roi(net, cash, idx)
            lo_, hi_ = np.percentile(rois, [5, 95])
            p = 2 * min(float((rois <= 0).mean()), float((rois >= 0).mean()))
            res.update({"ci90": [round(float(lo_), 4), round(float(hi_), 4)],
                        "bootP": round(max(p, 1.0 / BOOT), 5), "tested": True})
        else:
            res["tested"] = False
        out.append(res)
    return out


def idx_roi(net, cash, idx):
    net_s = net[idx].sum(axis=1)
    cash_s = cash[idx].sum(axis=1)
    return np.where(cash_s > 0, net_s / np.maximum(cash_s, 1e-9), 0.0)


def main():
    rng = np.random.default_rng(SEED)
    with open(os.path.join(ART, "frozen_splits.json")) as fh:
        dates = json.load(fh)["discovery"]["dates"]
    settled = load_settlement_map()
    counters = Counter()
    arbs = []
    rv_first = {}
    for date, batches in load_observations_by_batch(dates):
        for batch_id, quotes in batches.items():
            audit_batch(quotes, counters, arbs, rv_first, date, batch_id)
        print(date, "batches:", len(batches))
    rv_results = score_rv(rv_first, settled, rng)

    post_fee_arbs = [a for a in arbs if a.get("postFeeLocked", -1) > 0]
    doc = {
        "program": "MLB-ALPHA-0001",
        "family": "B",
        "split": "discovery",
        "rvSignalThresholdCents": RV_SIGNAL_CENTS,
        "counters": dict(counters),
        "arbitrage": {
            "totalExecutableViolations": len(arbs),
            "postFeeProfitable": len(post_fee_arbs),
            "samples": arbs[:200],
        },
        "relativeValue": rv_results,
    }
    out = os.path.join(ART, "family_b_discovery_results.json")
    with open(out, "w") as fh:
        json.dump(doc, fh, indent=1, sort_keys=True, default=str)
        fh.write("\n")
    print("wrote", out)
    print(json.dumps(dict(counters), indent=1, sort_keys=True))
    print("executable violations:", len(arbs), "post-fee profitable:", len(post_fee_arbs))
    for r in rv_results:
        print(r)


if __name__ == "__main__":
    sys.exit(main())
