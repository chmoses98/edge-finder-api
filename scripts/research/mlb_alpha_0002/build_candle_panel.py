#!/usr/bin/env python3
"""MLB-ALPHA-0002 PIT feature layer, part 3: the MINUTE panel from Kalshi's
own exchange record (recovered 1-minute candlesticks + public trade tape,
scripts/research/mlb_alpha_0002/recover_kalshi_history.py).

For every recovered contract we rebuild a minute-by-minute pregame state:
  bid/ask close of each 1-minute candle (dollars -> cents), volume,
  open interest, plus the trade tape aggregated per minute into taker
  YES-buys vs NO-buys (order-flow imbalance), block-trade flags, and the
  last trade price.
Feature rows are emitted on a fixed grid of decision times t
(every GRID_MIN minutes from T-MAX_BEFORE to T-MIN_BEFORE before first
pitch). EVERY feature uses candles/trades with end_period_ts <= t
(what we knew, when we knew it). Targets look forward to the last pregame
minute with a two-sided quote ("close") and to corrected settlement.

Executable prices: BUY YES at the ask, BUY NO at 100 - bid. Midpoint is
diagnostic only. CLV signs from lib.edgelab.clv_convention.
RESEARCH ONLY.
"""

import argparse
import glob
import gzip
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, REPO)
from lib.edgelab import clv_convention as cc                      # noqa: E402
from lib.edgelab.kalshi_fees import net_settlement_pl_for_order    # noqa: E402
from lib.edgelab.mlb_alpha_identity import parse_event_ticker     # noqa: E402
from scripts.research.mlb_alpha_0002.build_kalshi_panel import load_settlements  # noqa: E402

ART = os.path.join(REPO, "data", "edgelab", "research_artifacts", "mlb_alpha_0002")
HIST = os.path.join(ART, "kalshi_history")
OUT = os.path.join(ART, "pit_candle_panel.jsonl.gz")
PROGRAM = "MLB-ALPHA-0002"
ORDER_USD = 10.0
GRID_MIN = 5
MAX_BEFORE = 240      # first decision time: T-240 min
MIN_BEFORE = 5        # last decision time:  T-5 min
EPOCH = datetime(1970, 1, 1)


def to_cents(s):
    try:
        return round(float(s) * 100.0, 2)
    except (TypeError, ValueError):
        return None


def iter_gz(path):
    with gzip.open(path, "rt") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


def minute_of(ts_iso):
    dt = datetime.fromisoformat(ts_iso.replace("Z", "+00:00")).replace(tzinfo=None)
    return int((dt - EPOCH).total_seconds() // 60)


def load_ticker_series(dates):
    """-> {ticker: {"meta":..., "candles": {minute: (bid, ask, vol, oi)}, "trades": {minute: agg}}}"""
    out = {}
    for date in dates:
        cp = os.path.join(HIST, "candles", date + ".jsonl.gz")
        if not os.path.exists(cp):
            continue
        for rec in iter_gz(cp):
            t = rec["ticker"]
            cd = {}
            for c in rec["candlesticks"]:
                m = int(c["end_period_ts"]) // 60
                bid = to_cents((c.get("yes_bid") or {}).get("close_dollars"))
                ask = to_cents((c.get("yes_ask") or {}).get("close_dollars"))
                try:
                    vol = float(c.get("volume_fp") or 0.0)
                    oi = float(c.get("open_interest_fp") or 0.0)
                except ValueError:
                    vol, oi = 0.0, 0.0
                cd[m] = (bid, ask, vol, oi)
            out[t] = {"family": rec["family"], "gameDate": rec["gameDate"], "candles": cd, "trades": {}}
        tp = os.path.join(HIST, "trades", date + ".jsonl.gz")
        if os.path.exists(tp):
            for rec in iter_gz(tp):
                t = rec["ticker"]
                if t not in out:
                    continue
                agg = out[t]["trades"]
                for tr in rec["trades"]:
                    m = minute_of(tr["created_time"])
                    a = agg.setdefault(m, {"n": 0, "yesBuyQty": 0.0, "noBuyQty": 0.0, "block": 0,
                                           "lastYesPriceCents": None, "lastTs": None})
                    try:
                        q = float(tr.get("count_fp") or tr.get("count") or 0)
                    except ValueError:
                        q = 0.0
                    a["n"] += 1
                    if tr.get("taker_side") == "yes":
                        a["yesBuyQty"] += q
                    else:
                        a["noBuyQty"] += q
                    if tr.get("is_block_trade"):
                        a["block"] += 1
                    ts = tr["created_time"]
                    if a["lastTs"] is None or ts > a["lastTs"]:
                        a["lastTs"] = ts
                        a["lastYesPriceCents"] = to_cents(tr.get("yes_price_dollars"))
    return out


def state_at(cd, m):
    """Latest candle at or before minute m -> (bid, ask, vol, oi, minute) or None."""
    # candles are dense (1/min) while the market is open; walk back a little
    for k in range(0, 8):
        c = cd.get(m - k)
        if c is not None:
            return c + (m - k,)
    return None


def two_sided(c):
    return c is not None and c[0] is not None and c[1] is not None and 1 <= c[1] <= 99 and 1 <= (100 - c[0]) <= 99 and c[1] >= c[0]


def window_sum(agg, m, minutes):
    ys = ns = n = b = 0.0
    for k in range(minutes):
        a = agg.get(m - k)
        if a:
            ys += a["yesBuyQty"]; ns += a["noBuyQty"]; n += a["n"]; b += a["block"]
    return ys, ns, n, b


def build(dates):
    settled = load_settlements()
    series = load_ticker_series(dates)
    rows = []
    for t, s in series.items():
        if t not in settled:
            continue
        ident = parse_event_ticker(t.rsplit("-", 1)[0])
        if ident.get("status") != "RESOLVED":
            continue
        start = ident["scheduledStartUtc"]
        m0 = int((start - EPOCH).total_seconds() // 60)
        cd, agg = s["candles"], s["trades"]
        # close = last pregame minute (<= m0-1) with a two-sided quote
        close = None
        for m in range(m0 - 1, m0 - 60, -1):
            c = cd.get(m)
            if two_sided(c):
                close = c + (m,)
                break
        if close is None:
            continue
        c_mid = (close[0] + close[1]) / 2.0
        c_yes_ask, c_no_ask = float(close[1]), 100.0 - float(close[0])
        result = settled[t]
        gk = ident["gameDate"] + ":" + t.rsplit("-", 1)[0].split("-", 1)[1]
        for before in range(MAX_BEFORE, MIN_BEFORE - 1, -GRID_MIN):
            m = m0 - before
            if m >= close[4]:
                break
            c = state_at(cd, m)
            if not two_sided(c):
                continue
            bid, ask, vol, oi, cm = c
            mid = (bid + ask) / 2.0
            yes_ask, no_ask = float(ask), 100.0 - float(bid)
            feat = {"programId": PROGRAM, "marketTicker": t, "marketFamily": s["family"],
                    "gameDate": ident["gameDate"], "gameKey": gk,
                    "homeTeam": ident["homeTeam"], "awayTeam": ident["awayTeam"],
                    "scheduledStartUtc": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "decisionMinute": m, "minutesToStart": before,
                    "decisionAt": (EPOCH + timedelta(minutes=m)).strftime("%Y-%m-%dT%H:%M:00Z"),
                    "yesBid": bid, "yesAsk": ask, "mid": mid, "spreadCents": round(ask - bid, 2),
                    "distFrom50": abs(mid - 50.0), "volume": vol, "openInterest": oi,
                    "quoteAgeMin": m - cm}
            # momentum over lookbacks (mid change), spread change, OI/volume change
            for lb in (5, 10, 30, 60):
                p = state_at(cd, m - lb)
                if two_sided(p):
                    feat["dMid%d" % lb] = round(mid - (p[0] + p[1]) / 2.0, 2)
                    feat["dSpread%d" % lb] = round((ask - bid) - (p[1] - p[0]), 2)
                    feat["dVol%d" % lb] = round(vol - p[2], 2)
                    feat["dOI%d" % lb] = round(oi - p[3], 2)
                else:
                    feat["dMid%d" % lb] = feat["dSpread%d" % lb] = feat["dVol%d" % lb] = feat["dOI%d" % lb] = None
            # staleness: minutes since the two-sided quote last changed
            stale = 0
            for k in range(1, 121):
                p = cd.get(m - k)
                if p is None or p[0] != bid or p[1] != ask:
                    break
                stale += 1
            feat["minutesUnchanged"] = stale
            # consecutive same-direction 5-min moves
            run, prevm = 0, mid
            for k in range(1, 7):
                p = state_at(cd, m - 5 * k)
                if not two_sided(p):
                    break
                pm = (p[0] + p[1]) / 2.0
                d = prevm - pm
                if d == 0:
                    break
                if run == 0:
                    run = 1 if d > 0 else -1
                elif (d > 0) == (run > 0):
                    run += 1 if run > 0 else -1
                else:
                    break
                prevm = pm
            feat["sameDirRun5"] = run
            # order flow from the trade tape (taker YES qty vs taker NO qty)
            for w in (10, 30, 60):
                ys, ns, n, b = window_sum(agg, m, w)
                tot = ys + ns
                feat["ofi%d" % w] = round((ys - ns) / tot, 4) if tot > 0 else None
                feat["tradeQty%d" % w] = round(tot, 2)
                feat["trades%d" % w] = int(n)
                feat["blockTrades%d" % w] = int(b)
            last = None
            for k in range(0, 121):
                a = agg.get(m - k)
                if a and a.get("lastYesPriceCents") is not None:
                    last = (a["lastYesPriceCents"], k)
                    break
            feat["lastTradeYesCents"] = last[0] if last else None
            feat["minutesSinceLastTrade"] = last[1] if last else None
            feat["lastTradeMinusMid"] = round(last[0] - mid, 2) if last else None
            # ---- targets (strictly later than t)
            feat.update({
                "closeMinute": close[4], "closeMinutesToStart": m0 - close[4],
                "closeYesBid": close[0], "closeYesAsk": close[1],
                "fairMidMoveToClose": round(c_mid - mid, 2),
                "clvYesCents": cc.clv_for_yes(yes_ask, c_yes_ask, unit=cc.UNIT_CENTS),
                "clvNoCents": cc.clv_for_no(no_ask, c_no_ask, unit=cc.UNIT_CENTS),
                "settlementResult": result,
                "netPlBuyYes": round(net_settlement_pl_for_order(ORDER_USD, yes_ask / 100.0, result == "YES"), 4),
                "netPlBuyNo": round(net_settlement_pl_for_order(ORDER_USD, no_ask / 100.0, result == "NO"), 4),
                "clvConvention": cc.CONVENTION_ID, "clvUnit": cc.UNIT_CENTS,
            })
            for h in (15, 30, 60):
                f = state_at(cd, min(m + h, close[4]))
                feat["fairMidMove%dm" % h] = round((f[0] + f[1]) / 2.0 - mid, 2) if two_sided(f) else None
            rows.append(feat)
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dates", default="")
    args = ap.parse_args()
    dates = [d for d in args.dates.split(",") if d] or sorted(
        os.path.basename(p)[:10] for p in glob.glob(os.path.join(HIST, "candles", "*.jsonl.gz")))
    rows = build(dates)
    os.makedirs(ART, exist_ok=True)
    with gzip.open(OUT, "wt") as fh:
        for r in rows:
            fh.write(json.dumps(r, sort_keys=True) + "\n")
    fam = defaultdict(int)
    for r in rows:
        fam[r["marketFamily"]] += 1
    meta = {"programId": PROGRAM, "artifact": os.path.relpath(OUT, REPO), "rows": len(rows),
            "tickers": len({r["marketTicker"] for r in rows}), "games": len({r["gameKey"] for r in rows}),
            "dates": sorted({r["gameDate"] for r in rows}), "byFamily": dict(fam),
            "grid": {"stepMin": GRID_MIN, "firstMinutesBefore": MAX_BEFORE, "lastMinutesBefore": MIN_BEFORE},
            "asOfRule": "features use candles/trades with end minute <= decision minute; targets use the last pregame two-sided minute and corrected settlement",
            "clvConvention": cc.CONVENTION_ID}
    with open(os.path.join(ART, "pit_candle_panel.meta.json"), "w") as fh:
        json.dump(meta, fh, indent=1, sort_keys=True); fh.write("\n")
    print(json.dumps(meta, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
