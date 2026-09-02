#!/usr/bin/env python3
"""MLB-ALPHA-0002 activation audit: measure -- never estimate -- what the
prospective collector would actually cost before it is put on a schedule.

Three audits in one run, because each needs live egress that the research
sandbox does not have (so this runs in GitHub Actions):

  1. ODDS API BUDGET (Section C): ONE controlled request, recording the
     real x-requests-last / x-requests-used / x-requests-remaining
     headers. Costs a handful of credits, never a bulk pull.
  2. MLB SERIES UNIVERSE (Section H): the COMPLETE currently-open Kalshi
     MLB series list from GET /series and GET /markets, so no family is
     silently omitted from the alpha data map.
  3. CAPTURE SMOKE TEST (Section F): one real capture pass measuring
     ticker counts, HTTP request counts, order-book/trade volumes,
     runtime, raw and compressed bytes, and the projected daily and
     monthly growth at a 10-minute cadence.

Writes data/edgelab/research_artifacts/mlb_alpha_0002/activation_audit.json.
READ-ONLY: public GET endpoints only; places no orders; touches no
betting path. RESEARCH ONLY.
"""

import argparse
import gzip
import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
ART = os.path.join(REPO, "data", "edgelab", "research_artifacts", "mlb_alpha_0002")
OUT = os.path.join(ART, "activation_audit.json")
KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
ODDS = "https://api.the-odds-api.com/v4"
ODDS_KEY = (os.environ.get("ODDS_API_KEY") or "").strip()

# Every MLB-associated series this repo has ever observed, plus whatever
# GET /series reports today. Nothing is hardcoded as "the universe".
KNOWN_MLB_PREFIXES = ("KXMLB", "MLB")
STATS = Counter()


def http(url, timeout=30):
    STATS["httpRequests"] += 1
    t0 = time.time()
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json",
                                                   "User-Agent": "edgelab-research"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            STATS["httpBytes"] += len(body)
            STATS["httpSeconds"] += time.time() - t0
            return json.loads(body.decode()), dict(resp.headers), None
    except urllib.error.HTTPError as exc:
        STATS["httpSeconds"] += time.time() - t0
        STATS["httpErrors"] += 1
        return None, dict(exc.headers or {}), "HTTP %d" % exc.code
    except Exception as exc:
        STATS["httpSeconds"] += time.time() - t0
        STATS["httpErrors"] += 1
        return None, {}, str(exc)


# ------------------------------------------------------------ 1. Odds API
def audit_odds():
    out = {"keyPresent": bool(ODDS_KEY)}
    if not ODDS_KEY:
        out["status"] = "NO_KEY"
        return out
    url = ("%s/sports/baseball_mlb/odds?apiKey=%s&regions=eu&bookmakers=pinnacle"
           "&markets=h2h&oddsFormat=decimal" % (ODDS, ODDS_KEY))
    t0 = time.time()
    data, hdr, err = http(url)
    out.update({
        "controlledRequest": {"markets": "h2h", "regions": "eu", "books": "pinnacle",
                              "endpoint": "/v4/sports/baseball_mlb/odds"},
        "xRequestsLast": hdr.get("x-requests-last"),
        "xRequestsUsed": hdr.get("x-requests-used"),
        "xRequestsRemaining": hdr.get("x-requests-remaining"),
        "seconds": round(time.time() - t0, 2),
        "gamesReturned": len(data) if isinstance(data, list) else None,
        "error": err,
    })
    out["status"] = "OK" if err is None else "ERROR"
    return out


# ------------------------------------------------- 2. MLB series universe
def audit_series():
    seen = {}
    data, _, err = http("%s/series?limit=1000" % KALSHI)
    catalogue_err = err
    for s in ((data or {}).get("series") or []):
        t = (s.get("ticker") or "").upper()
        title = s.get("title") or ""
        if t.startswith(KNOWN_MLB_PREFIXES) or "MLB" in t or "baseball" in title.lower():
            seen[t] = {"seriesTicker": t, "title": title, "category": s.get("category"),
                       "foundVia": "GET /series"}
    # Open markets tell us which series are actually tradable right now,
    # and catch series the catalogue misses.
    by_series = Counter()
    quotes = defaultdict(list)
    cursor = ""
    for _ in range(40):
        d, _h, e = http("%s/markets?status=open&limit=1000%s"
                        % (KALSHI, ("&cursor=" + cursor) if cursor else ""))
        if d is None:
            catalogue_err = catalogue_err or e
            break
        for m in d.get("markets") or []:
            tick = m.get("ticker") or ""
            ser = tick.split("-", 1)[0].upper()
            if not (ser.startswith(KNOWN_MLB_PREFIXES) or "MLB" in ser):
                continue
            by_series[ser] += 1
            quotes[ser].append({"volume": m.get("volume") or 0,
                                "openInterest": m.get("open_interest") or 0,
                                "spread": (m.get("yes_ask") or 0) - (m.get("yes_bid") or 0)
                                if m.get("yes_ask") is not None and m.get("yes_bid") is not None else None,
                                "liquidity": m.get("liquidity")})
            seen.setdefault(ser, {"seriesTicker": ser, "title": None, "category": None,
                                  "foundVia": "GET /markets?status=open"})
        cursor = d.get("cursor") or ""
        if not cursor:
            break
    rows = []
    for ser, rec in sorted(seen.items()):
        qs = quotes.get(ser, [])
        vols = [q["volume"] for q in qs]
        ois = [q["openInterest"] for q in qs]
        sps = [q["spread"] for q in qs if q["spread"] is not None]
        rows.append({**rec, "openMarkets": by_series.get(ser, 0),
                     "totalVolume": sum(vols), "totalOpenInterest": sum(ois),
                     "medianSpreadCents": sorted(sps)[len(sps) // 2] if sps else None,
                     "marketsWithVolume": sum(1 for v in vols if v > 0)})
    return {"seriesFound": len(rows), "openMlbMarkets": sum(by_series.values()),
            "error": catalogue_err, "series": rows}


# --------------------------------------------------- 3. Capture smoke test
def audit_capture(series_rows, book_limit):
    """One real pass: quotes for every open MLB market, order books for the
    prioritised subset, and one trade-tape page. Measures everything."""
    t0 = time.time()
    quote_rows, book_rows, trade_rows = [], [], []
    tickers = []
    n_before = STATS["httpRequests"]
    for row in series_rows:
        ser = row["seriesTicker"]
        if not row["openMarkets"]:
            continue
        cursor = ""
        for _ in range(20):
            d, _h, _e = http("%s/markets?series_ticker=%s&status=open&limit=1000%s"
                             % (KALSHI, ser, ("&cursor=" + cursor) if cursor else ""))
            if d is None:
                break
            for m in d.get("markets") or []:
                quote_rows.append({"marketTicker": m.get("ticker"), "seriesTicker": ser,
                                   "yesBid": m.get("yes_bid"), "yesAsk": m.get("yes_ask"),
                                   "volume": m.get("volume"), "openInterest": m.get("open_interest"),
                                   "raw": m})
                tickers.append((ser, m.get("ticker")))
            cursor = d.get("cursor") or ""
            if not cursor:
                break
    quote_requests = STATS["httpRequests"] - n_before

    n_before = STATS["httpRequests"]
    for ser, t in tickers[:book_limit]:
        d, _h, _e = http("%s/markets/%s/orderbook" % (KALSHI, t))
        if d is not None:
            book_rows.append({"marketTicker": t, "orderbook": d.get("orderbook")})
        time.sleep(0.12)
    book_requests = STATS["httpRequests"] - n_before

    n_before = STATS["httpRequests"]
    min_ts = int(time.time()) - 600
    cursor, pages = "", 0
    while pages < 10:
        d, _h, _e = http("%s/markets/trades?min_ts=%d&limit=1000%s"
                         % (KALSHI, min_ts, ("&cursor=" + cursor) if cursor else ""))
        pages += 1
        if d is None:
            break
        for tr in d.get("trades") or []:
            if (tr.get("ticker") or "").upper().startswith(KNOWN_MLB_PREFIXES):
                trade_rows.append(tr)
        cursor = d.get("cursor") or ""
        if not cursor:
            break
    trade_requests = STATS["httpRequests"] - n_before

    def sizes(rows):
        raw = sum(len(json.dumps(r, sort_keys=True, default=str)) + 1 for r in rows)
        comp = len(gzip.compress(("\n".join(json.dumps(r, sort_keys=True, default=str)
                                            for r in rows)).encode())) if rows else 0
        return raw, comp

    q_raw, q_comp = sizes(quote_rows)
    b_raw, b_comp = sizes(book_rows)
    t_raw, t_comp = sizes(trade_rows)
    raw = q_raw + b_raw + t_raw
    comp = q_comp + b_comp + t_comp
    runs_per_day = 6 * 14          # 10-minute cadence across a 14-hour MLB window
    # How much of the order-book payload is actually NEW information?
    fps = Counter(json.dumps(r.get("orderbook"), sort_keys=True) for r in book_rows)
    dup_share = 1.0 - (len(fps) / len(book_rows)) if book_rows else None
    return {
        "openMlbTickers": len(tickers),
        "orderBookRequests": book_requests, "orderBookSampled": len(book_rows),
        "orderBookLimitApplied": book_limit,
        "quoteRequests": quote_requests, "tradePages": trade_requests,
        "mlbTradesInLast10Min": len(trade_rows),
        "totalHttpRequests": STATS["httpRequests"], "httpErrors": STATS["httpErrors"],
        "wallClockSeconds": round(time.time() - t0, 1),
        "bytes": {"quotesRaw": q_raw, "quotesGz": q_comp, "orderBooksRaw": b_raw,
                  "orderBooksGz": b_comp, "tradesRaw": t_raw, "tradesGz": t_comp,
                  "totalRaw": raw, "totalGz": comp},
        "identicalOrderBookShareWithinRun": dup_share,
        "projection": {
            "runsPerDay10MinWindow": runs_per_day,
            "note": "order books scaled from the sampled subset to the full ticker count",
            "rawMbPerRunFullUniverse": round((q_raw + t_raw + (b_raw / max(len(book_rows), 1)) * len(tickers)) / 1048576.0, 2),
            "gzMbPerRunFullUniverse": round((q_comp + t_comp + (b_comp / max(len(book_rows), 1)) * len(tickers)) / 1048576.0, 2),
        },
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--book-limit", type=int, default=150)
    ap.add_argument("--skip-odds", action="store_true")
    args = ap.parse_args()
    started = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    odds = {"status": "SKIPPED"} if args.skip_odds else audit_odds()
    series = audit_series()
    capture = audit_capture(series["series"], args.book_limit)
    p = capture["projection"]
    p["gzMbPerDay"] = round(p["gzMbPerRunFullUniverse"] * p["runsPerDay10MinWindow"], 1)
    p["gzGbPerMonth"] = round(p["gzMbPerDay"] * 30 / 1024.0, 2)
    p["rawMbPerDay"] = round(p["rawMbPerRunFullUniverse"] * p["runsPerDay10MinWindow"], 1)
    doc = {"programId": "MLB-ALPHA-0002", "audit": "activation", "startedAt": started,
           "finishedAt": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
           "readOnly": True, "ordersPlaced": 0,
           "oddsApi": odds, "seriesUniverse": series, "captureSmokeTest": capture}
    os.makedirs(ART, exist_ok=True)
    with open(OUT, "w") as fh:
        json.dump(doc, fh, indent=1, sort_keys=True)
        fh.write("\n")
    print(json.dumps({"oddsApi": odds,
                      "seriesFound": series["seriesFound"],
                      "openMlbMarkets": series["openMlbMarkets"],
                      "capture": {k: v for k, v in capture.items() if k != "bytes"},
                      "bytes": capture["bytes"]}, indent=1)[:4000])
    print("\nseries universe:")
    for r in series["series"]:
        print("  %-18s open=%5d vol=%9d oi=%9d medSpread=%s withVol=%d" % (
            r["seriesTicker"], r["openMarkets"], r["totalVolume"], r["totalOpenInterest"],
            r["medianSpreadCents"], r["marketsWithVolume"]))
    print("wrote", OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
