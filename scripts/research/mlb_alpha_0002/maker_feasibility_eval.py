#!/usr/bin/env python3
"""MLB-ALPHA-0002-MAKER-FEASIBILITY-V1 -- historical evaluation.

Applies the frozen passive-execution protocols to the signals that
Family C found predictive (the F5 moneyline reversal and the order-flow
follow-through) and asks the ONE question the taker analysis could not:

    the predicted move is ~1-5 cents and the taker spread is ~4-9 cents;
    does resting on the bid instead of crossing it turn the sign?

Buying passively means paying the BID rather than the ASK, i.e. saving
the whole spread -- but only on the fills that actually happen, and the
fills that happen are exactly the ones where the market came to us,
which is where adverse selection lives. Both effects are measured.

HONESTY CONSTRAINTS
  * No fill here was observed. Every fill is inferred from the public
    trade tape's taker side, and the queue ahead of us is UNKNOWABLE
    historically (candlesticks carry no sizes, and Kalshi publishes no
    order-book history). The queue is therefore swept over a declared
    grid and every number is labelled COUNTERFACTUAL_QUEUE_UNKNOWN.
  * CONSERVATIVE_FILL and OPTIMISTIC_BOUND are reported separately and
    never averaged.
  * Adverse selection is measured on every hypothetical fill.

RESEARCH ONLY. No orders. No betting path.
"""

import argparse
import gzip
import json
import os
import sys
from collections import defaultdict

import numpy as np

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, REPO)
from scripts.research.mlb_alpha_0002 import maker_simulation as ms          # noqa: E402
from scripts.research.mlb_alpha_0002.build_candle_panel import (            # noqa: E402
    load_ticker_series, state_at, two_sided, EPOCH, minute_of)

ART = os.path.join(REPO, "data", "edgelab", "research_artifacts", "mlb_alpha_0002")
HIST = os.path.join(ART, "kalshi_history")
OUT = os.path.join(ART, "maker_feasibility_results.json")
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
    keys = list(g)
    sums = np.array([sum(g[k]) for k in keys], dtype=float)
    cnt = np.array([len(g[k]) for k in keys], dtype=float)
    idx = rng.integers(0, len(keys), size=(B, len(keys)))
    means = sums[idx].sum(1) / cnt[idx].sum(1)
    mean = float(np.mean(vals))
    centred = means - means.mean()
    return {"mean": mean,
            "ci95": [float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))],
            "games": len(keys), "p": max(float((np.abs(centred) >= abs(mean)).mean()), 1.0 / B)}


def load_trades_by_ticker(dates):
    out = defaultdict(list)
    for d in dates:
        p = os.path.join(HIST, "trades", d + ".jsonl.gz")
        if not os.path.exists(p):
            continue
        for rec in iter_gz(p):
            rows = []
            for tr in rec["trades"]:
                try:
                    q = float(tr.get("count_fp") or 0)
                except (TypeError, ValueError):
                    q = 0.0
                rows.append({"created_minute": minute_of(tr["created_time"]),
                             "taker_side": tr.get("taker_side"),
                             "yes_price_cents": ms.cents(tr.get("yes_price_dollars")),
                             "quantity": q})
            rows.sort(key=lambda r: r["created_minute"])
            out[rec["ticker"]] = rows
    return out


def signals_from_panel(family, feature, threshold):
    """Predeclared triggers, one decision per episode (identical rule to
    candidate_eval_f5_reversal so the two analyses are comparable)."""
    rows = [r for r in iter_gz(os.path.join(ART, "pit_candle_panel.jsonl.gz"))
            if r["marketFamily"] == family and r.get(feature) is not None]
    out = []
    for side, keep in (("YES", lambda v: v <= -threshold), ("NO", lambda v: v >= threshold)):
        sel = [r for r in rows if keep(r[feature])]
        sel.sort(key=lambda r: (r["marketTicker"], r["decisionMinute"]))
        last_t, last_m = None, None
        for r in sel:
            if r["marketTicker"] == last_t and r["decisionMinute"] - last_m <= 5:
                last_m = r["decisionMinute"]
                continue
            out.append((r, side))
            last_t, last_m = r["marketTicker"], r["decisionMinute"]
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--family", default="inning_result")
    ap.add_argument("--feature", default="dMid60")
    ap.add_argument("--threshold", type=float, default=3.0)
    args = ap.parse_args()

    protocols = {p["protocolId"]: p for p in ms.maker_protocols()}
    sigs = signals_from_panel(args.family, args.feature, args.threshold)
    dates = sorted({r["gameDate"] for r, _ in sigs})
    trades = load_trades_by_ticker(dates)
    series = load_ticker_series(dates)

    results = {"programId": "MLB-ALPHA-0002",
               "hypothesisId": "MLB-ALPHA-0002-MAKER-FEASIBILITY-V1",
               "signalSource": {"family": args.family, "feature": args.feature,
                                "thresholdCents": args.threshold,
                                "note": "same trigger as candidate C01-F5REV; C01 itself is unchanged"},
               "feeConfig": ms.fee_config(),
               "protocols": [{k: v for k, v in p.items()} for p in protocols.values()],
               "queueAheadGrid": list(ms.QUEUE_AHEAD_GRID),
               "evidenceClass": "COUNTERFACTUAL_QUEUE_UNKNOWN",
               "episodes": len(sigs), "dates": dates,
               "games": len({r["gameKey"] for r, _ in sigs}),
               "byProtocol": {}}

    for pid, proto in protocols.items():
        size = proto["modelledContracts"]
        wait = proto["maxWaitMinutes"]
        per_queue = {}
        for q in ms.QUEUE_AHEAD_GRID:
            fills, econ_c, econ_o, adverse, clus, taker_pl = [], [], [], defaultdict(list), [], []
            for r, side in sigs:
                t = r["marketTicker"]
                m = r["decisionMinute"]
                cd = series.get(t, {}).get("candles") or {}
                st = state_at(cd, m)
                if not two_sided(st):
                    continue
                bid, ask = st[0], st[1]
                if pid == "MAKER-A-JOIN-BEST":
                    limit = bid if side == "YES" else 100 - ask
                else:
                    if (ask - bid) < proto["minSpreadCents"]:
                        continue
                    limit = (bid + 1) if side == "YES" else (100 - ask + 1)
                start_min = minute_of(r["scheduledStartUtc"])
                deadline = min(m + wait, start_min - 5, r["closeMinute"])
                if deadline <= m:
                    continue
                sim = ms.simulate_passive_fill(trades.get(t, []), m, deadline, side,
                                               limit, q, size)
                fills.append(1.0 if sim["filled"] else 0.0)
                clus.append(r["gameKey"])
                # taker baseline at the same instant: cross the spread now
                taker_price = ask if side == "YES" else 100 - bid
                won = (r["settlementResult"] == "YES") == (side == "YES")
                tfee = ms.fee_for(size, taker_price / 100.0, ms.TAKER_MULTIPLIER)
                tcash = size * taker_price / 100.0 + tfee
                taker_pl.append((size * 1.0 if won else 0.0) - tcash)
                if not sim["filled"]:
                    continue
                for label, mult, bucket in (("conservative", ms.MAKER_MULTIPLIER_CONSERVATIVE, econ_c),
                                            ("zeroMakerFee", ms.MAKER_MULTIPLIER_OPTIMISTIC, econ_o)):
                    e = ms.fill_economics(side, limit, size, r["settlementResult"], mult)
                    bucket.append(e["netProfitLoss"])
                fm = state_at(cd, sim["filledAtMinute"])
                if two_sided(fm):
                    base = (fm[0] + fm[1]) / 2.0
                    sgn = 1.0 if side == "YES" else -1.0
                    for h in (1, 5, 10, 30):
                        nxt = state_at(cd, min(sim["filledAtMinute"] + h, r["closeMinute"]))
                        if two_sided(nxt):
                            adverse["+%dmin" % h].append(sgn * ((nxt[0] + nxt[1]) / 2.0 - base))
                    adverse["toClose"].append(sgn * ((r["closeYesBid"] + r["closeYesAsk"]) / 2.0 - base))
            if not fills:
                per_queue[str(q)] = {"status": "NO_EPISODES"}
                continue
            filled_clus = [c for c, f in zip(clus, fills) if f > 0]
            rec = {"episodes": len(fills), "fills": int(sum(fills)),
                   "fillRate": round(float(np.mean(fills)), 4),
                   "fillRateGames": len(set(filled_clus))}
            if econ_c:
                rec["netPlPerFill_makerFee25pctOfTaker"] = cluster_boot(econ_c, filled_clus)
                rec["netPlPerFill_zeroMakerFee"] = cluster_boot(econ_o, filled_clus)
                rec["netPlPerEpisode_makerFee25pct"] = round(float(np.sum(econ_c) / len(fills)), 4)
                rec["adverseSelectionCents"] = {k: round(float(np.mean(v)), 3)
                                                for k, v in sorted(adverse.items()) if v}
            rec["takerBaselineNetPlPerEpisode"] = round(float(np.mean(taker_pl)), 4)
            per_queue[str(q)] = rec
        results["byProtocol"][pid] = {"protocolSha256": proto["protocolSha256"],
                                      "class": proto["class"], "byQueueAhead": per_queue}

    with open(OUT, "w") as fh:
        json.dump(results, fh, indent=1, sort_keys=True)
        fh.write("\n")
    print("episodes %d games %d dates %d" % (results["episodes"], results["games"], len(dates)))
    for pid, blk in results["byProtocol"].items():
        print("\n%s  [%s]" % (pid, blk["class"]))
        for q, rec in blk["byQueueAhead"].items():
            if rec.get("status"):
                print("  queue=%-4s %s" % (q, rec["status"])); continue
            if "netPlPerFill_makerFee25pctOfTaker" not in rec:
                print("  queue=%-4s fills=%d/%d (%.1f%%)  no fills to price"
                      % (q, rec["fills"], rec["episodes"], 100 * rec["fillRate"])); continue
            c = rec["netPlPerFill_makerFee25pctOfTaker"]
            print("  queue=%-4s fills=%4d/%4d (%.1f%%, %d games) netPL/fill=%+.3f [%+.3f,%+.3f] p=%.3f "
                  "| perEpisode=%+.3f | takerBaseline=%+.3f | adverse=%s"
                  % (q, rec["fills"], rec["episodes"], 100 * rec["fillRate"], c["games"],
                     c["mean"], c["ci95"][0], c["ci95"][1], c["p"],
                     rec["netPlPerEpisode_makerFee25pct"], rec["takerBaselineNetPlPerEpisode"],
                     rec.get("adverseSelectionCents")))
    print("\nwrote", OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
