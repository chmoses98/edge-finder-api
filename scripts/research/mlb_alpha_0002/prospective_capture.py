#!/usr/bin/env python3
"""MLB-ALPHA-0002 prospective capture: the sources a Kalshi trader could
use that the archive never recorded, captured with the time WE saw them.

Per invocation (intended every 10 minutes in the MLB window; runs only
where egress exists, i.e. GitHub Actions):
  1. KALSHI open MLB markets (core families): quote + volume + OI, plus
     the public ORDER BOOK (full depth, both sides) and the trade tape
     since the previous capture. Public GET only.
  2. EXTERNAL live odds (The Odds API, if ODDS_API_KEY is present):
     pinnacle/draftkings/fanduel/betmgm h2h+totals with the book's own
     last_update -- gives sharp-market event times at 10-min resolution
     (or finer, from last_update) for the lead/lag hypothesis.
  3. BASEBALL EVENT STATE (MLB Stats API, free): per game, probable
     pitchers and whether the boxscore lineup is posted, so the FIRST
     capture at which a lineup/pitcher fact appears is a timestamped
     information event.

Storage: append-only JSONL per capture day under
  data/edgelab/research_artifacts/mlb_alpha_0002/prospective/
      kalshi_<date>.jsonl, orderbook_<date>.jsonl, trades_<date>.jsonl,
      odds_<date>.jsonl, mlb_state_<date>.jsonl
Every record carries capturedAt and runId. Nothing here reads or writes
bets, recommendations, config, staking, or risk gates. Kalshi is
READ-ONLY. RESEARCH ONLY.
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, REPO)

KALSHI_API = "https://api.elections.kalshi.com/trade-api/v2"
ODDS_API = "https://api.the-odds-api.com/v4"
MLB_API = "https://statsapi.mlb.com/api/v1"
OUT = os.path.join(REPO, "data", "edgelab", "research_artifacts", "mlb_alpha_0002", "prospective")
SERIES = ["KXMLBGAME", "KXMLBTOTAL", "KXMLBF5", "KXMLBF5TOTAL", "KXMLBTEAMTOTAL", "KXMLBSPREAD", "KXMLBRFI"]
STATE = os.path.join(OUT, "capture_state.json")


def http_json(url, timeout=30):
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "edgelab-research"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode()), dict(resp.headers)
    except Exception as exc:
        print("  fetch failed %s: %s" % (url.split("?")[0], exc))
        return None, {}


def append(path, rec):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a") as fh:                       # append-only, never "w"
        fh.write(json.dumps(rec, sort_keys=True, default=str) + "\n")


def load_state():
    if os.path.exists(STATE):
        with open(STATE) as fh:
            return json.load(fh)
    return {"lastTradeTs": None}


def save_state(st):
    os.makedirs(OUT, exist_ok=True)
    with open(STATE, "w") as fh:
        json.dump(st, fh, sort_keys=True)


def capture_kalshi(run_id, now, date, with_book, sleep):
    n_m = n_b = 0
    tickers = []
    for series in SERIES:
        js, _ = http_json("%s/markets?series_ticker=%s&status=open&limit=1000" % (KALSHI_API, series))
        for m in ((js or {}).get("markets") or []):
            t = m.get("ticker")
            if not t:
                continue
            append(os.path.join(OUT, "kalshi_%s.jsonl" % date),
                   {"runId": run_id, "capturedAt": now, "marketTicker": t, "eventTicker": m.get("event_ticker"),
                    "seriesTicker": series, "yesBid": m.get("yes_bid"), "yesAsk": m.get("yes_ask"),
                    "noBid": m.get("no_bid"), "noAsk": m.get("no_ask"), "lastPrice": m.get("last_price"),
                    "volume": m.get("volume"), "volume24h": m.get("volume_24h"), "openInterest": m.get("open_interest"),
                    "liquidity": m.get("liquidity"), "status": m.get("status"), "closeTime": m.get("close_time"),
                    "raw": m})
            n_m += 1
            tickers.append(t)
        time.sleep(sleep)
    if with_book:
        for t in tickers:
            js, _ = http_json("%s/markets/%s/orderbook" % (KALSHI_API, t))
            if js is not None:
                append(os.path.join(OUT, "orderbook_%s.jsonl" % date),
                       {"runId": run_id, "capturedAt": now, "marketTicker": t, "orderbook": js.get("orderbook"),
                        "orderbookFp": js.get("orderbook_fp")})
                n_b += 1
            time.sleep(sleep)
    return n_m, n_b, tickers


def capture_trades(run_id, now, date, since_iso, sleep):
    """All MLB-series trades since the previous capture (public tape, paginated)."""
    min_ts = int((datetime.fromisoformat(since_iso.replace("Z", "+00:00")).replace(tzinfo=None)
                  - datetime(1970, 1, 1)).total_seconds()) if since_iso else int(time.time()) - 900
    n = 0
    cursor = ""
    for _ in range(50):
        js, _ = http_json("%s/markets/trades?min_ts=%d&limit=1000%s" % (KALSHI_API, min_ts, ("&cursor=" + cursor) if cursor else ""))
        if js is None:
            break
        for tr in js.get("trades") or []:
            if any((tr.get("ticker") or "").startswith(s + "-") for s in SERIES):
                append(os.path.join(OUT, "trades_%s.jsonl" % date), {"runId": run_id, "capturedAt": now, "trade": tr})
                n += 1
        cursor = js.get("cursor") or ""
        if not cursor:
            break
        time.sleep(sleep)
    return n


def capture_odds(run_id, now, date):
    key = (os.environ.get("ODDS_API_KEY") or "").strip()
    if not key:
        return None
    js, hdr = http_json("%s/sports/baseball_mlb/odds?apiKey=%s&regions=eu,us&bookmakers=pinnacle,draftkings,fanduel,betmgm"
                        "&markets=h2h,totals&oddsFormat=decimal" % (ODDS_API, key))
    if js is None:
        return 0
    for g in js:
        append(os.path.join(OUT, "odds_%s.jsonl" % date),
               {"runId": run_id, "capturedAt": now, "eventId": g.get("id"), "commenceTime": g.get("commence_time"),
                "home": g.get("home_team"), "away": g.get("away_team"), "bookmakers": g.get("bookmakers"),
                "creditsRemaining": hdr.get("x-requests-remaining")})
    return len(js)


def capture_mlb_state(run_id, now, date):
    js, _ = http_json("%s/schedule?sportId=1&date=%s&hydrate=probablePitcher,lineups" % (MLB_API, date))
    n = 0
    for d in ((js or {}).get("dates") or []):
        for g in d.get("games") or []:
            lineups = g.get("lineups") or {}
            append(os.path.join(OUT, "mlb_state_%s.jsonl" % date),
                   {"runId": run_id, "capturedAt": now, "gamePk": g.get("gamePk"), "gameDate": g.get("gameDate"),
                    "status": (g.get("status") or {}).get("detailedState"),
                    "awayProbable": ((g.get("teams") or {}).get("away") or {}).get("probablePitcher"),
                    "homeProbable": ((g.get("teams") or {}).get("home") or {}).get("probablePitcher"),
                    "awayLineupPosted": bool(lineups.get("awayPlayers")), "homeLineupPosted": bool(lineups.get("homePlayers")),
                    "awayLineupIds": [p.get("id") for p in (lineups.get("awayPlayers") or [])],
                    "homeLineupIds": [p.get("id") for p in (lineups.get("homePlayers") or [])]})
            n += 1
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-orderbook", action="store_true")
    ap.add_argument("--sleep", type=float, default=0.13)
    args = ap.parse_args()
    now_dt = datetime.utcnow()
    now = now_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    run_id = "ALPHA0002_CAPTURE_" + now_dt.strftime("%Y%m%dT%H%M%SZ")
    date = (now_dt - timedelta(hours=4)).strftime("%Y-%m-%d")     # ET game date
    st = load_state()
    n_m, n_b, _ = capture_kalshi(run_id, now, date, not args.no_orderbook, args.sleep)
    n_t = capture_trades(run_id, now, date, st.get("lastTradeTs"), args.sleep)
    n_o = capture_odds(run_id, now, date)
    n_s = capture_mlb_state(run_id, now, date)
    st["lastTradeTs"] = now
    save_state(st)
    print("captured markets=%d orderbooks=%d trades=%d odds_games=%s mlb_games=%d at %s" % (n_m, n_b, n_t, n_o, n_s, now))
    return 0


if __name__ == "__main__":
    sys.exit(main())
