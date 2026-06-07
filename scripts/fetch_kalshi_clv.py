#!/usr/bin/env python3
"""Fetch Kalshi closing prices for 2026-06-06 bets and write to data/kalshi_clv_20260606.json"""
import json, urllib.request, time
from datetime import datetime, timezone, timedelta

KALSHI_BASE = "https://api.elections.kalshi.com/trade-api/v2"

def kalshi_get(url):
    try:
        req = urllib.request.Request(url, headers={
            "Accept": "application/json", "User-Agent": "Mozilla/5.0"
        })
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read())
    except Exception as e:
        print(f"  Error: {e}")
        return None

def get_closing_price(ticker, game_date_str, hhmm_et):
    """Fetch last pre-game candlestick price for a ticker."""
    dt = datetime.strptime(game_date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    hh_et, mm = int(hhmm_et[:2]), int(hhmm_et[2:])
    hh_utc = (hh_et + 4) % 24
    if hh_utc < hh_et:
        cap_dt = (dt + timedelta(days=1)).replace(hour=hh_utc, minute=mm, second=0)
    else:
        cap_dt = dt.replace(hour=hh_utc, minute=mm, second=0)
    end_ts   = int(cap_dt.timestamp())
    start_ts = end_ts - 7200

    for endpoint in [
        f"{KALSHI_BASE}/markets/{ticker}/candlesticks?start_ts={start_ts}&end_ts={end_ts}&period_interval=60",
        f"{KALSHI_BASE}/historical/markets/{ticker}/candlesticks?start_ts={start_ts}&end_ts={end_ts}&period_interval=60",
    ]:
        data = kalshi_get(endpoint)
        if data and data.get("candlesticks"):
            candles = data["candlesticks"]
            last = max(candles, key=lambda c: c.get("end_period_ts", 0))
            bid_raw = last.get("yes_bid", {}).get("close")
            ask_raw = last.get("yes_ask", {}).get("close")
            try:
                bid = float(bid_raw) if bid_raw else None
                ask = float(ask_raw) if ask_raw else None
            except:
                bid, ask = None, None
            if bid and ask:
                mid = round((bid + ask) / 2, 4)
                implied_pct = round(mid * 100, 1)
                if mid > 0.5:
                    american = round(-mid / (1 - mid) * 100)
                else:
                    american = round((1 - mid) / mid * 100)
                return {"mid": mid, "implied_pct": implied_pct, "american": american,
                        "yes_bid": bid, "yes_ask": ask, "num_candles": len(candles)}
    return None

# All 30 bets with their tickers and game times
bets = [
  {"id":"2026-06-06-001","ticker":"KXMLBGAME-26JUN061410KCMIN-MIN","time":"1410"},
  {"id":"2026-06-06-002","ticker":"KXMLBF5-26JUN061410KCMIN-MIN","time":"1410"},
  {"id":"2026-06-06-003","ticker":"KXMLBRFI-26JUN061410KCMIN","time":"1410"},
  {"id":"2026-06-06-004","ticker":"KXMLBGAME-26JUN061415CINSTL-STL","time":"1415"},
  {"id":"2026-06-06-005","ticker":"KXMLBF5-26JUN061415CINSTL-STL","time":"1415"},
  {"id":"2026-06-06-006","ticker":"KXMLBRFI-26JUN061415CINSTL","time":"1415"},
  {"id":"2026-06-06-007","ticker":"KXMLBGAME-26JUN061420SFCHC-SF","time":"1420"},
  {"id":"2026-06-06-008","ticker":"KXMLBF5-26JUN061420SFCHC-SF","time":"1420"},
  {"id":"2026-06-06-009","ticker":"KXMLBRFI-26JUN061507BALTOR","time":"1507"},
  {"id":"2026-06-06-010","ticker":"KXMLBGAME-26JUN061605CWSPHI-CWS","time":"1605"},
  {"id":"2026-06-06-011","ticker":"KXMLBRFI-26JUN061605CWSPHI","time":"1605"},
  {"id":"2026-06-06-012","ticker":"KXMLBF5-26JUN061610WSHARI-WSH","time":"1610"},
  {"id":"2026-06-06-013","ticker":"KXMLBRFI-26JUN061610WSHARI","time":"1610"},
  {"id":"2026-06-06-014","ticker":"KXMLBF5-26JUN061610ATHHOU-HOU","time":"1610"},
  {"id":"2026-06-06-015","ticker":"KXMLBRFI-26JUN061610ATHHOU","time":"1610"},
  {"id":"2026-06-06-016","ticker":"KXMLBGAME-26JUN061610ATHHOU-ATH","time":"1610"},
  {"id":"2026-06-06-017","ticker":"KXMLBGAME-26JUN061610PITATL-PIT","time":"1610"},
  {"id":"2026-06-06-018","ticker":"KXMLBF5-26JUN061610PITATL-PIT","time":"1610"},
  {"id":"2026-06-06-019","ticker":"KXMLBRFI-26JUN061610PITATL","time":"1610"},
  {"id":"2026-06-06-020","ticker":"KXMLBF5-26JUN061935CLETEX-TEX","time":"1935"},
  {"id":"2026-06-06-021","ticker":"KXMLBRFI-26JUN061935CLETEX","time":"1935"},
  {"id":"2026-06-06-022","ticker":"KXMLBF5-26JUN061935BOSNYY-NYY","time":"1935"},
  {"id":"2026-06-06-023","ticker":"KXMLBF5-26JUN062110MILCOL-COL","time":"2110"},
  {"id":"2026-06-06-024","ticker":"KXMLBRFI-26JUN062110MILCOL","time":"2110"},
  {"id":"2026-06-06-025","ticker":"KXMLBGAME-26JUN062210LAALAD-LAA","time":"2210"},
  {"id":"2026-06-06-026","ticker":"KXMLBF5-26JUN062210LAALAD-LAA","time":"2210"},
  {"id":"2026-06-06-027","ticker":"KXMLBRFI-26JUN062210LAALAD","time":"2210"},
  {"id":"2026-06-06-028","ticker":"KXMLBGAME-26JUN062210NYMSD-NYM","time":"2210"},
  {"id":"2026-06-06-029","ticker":"KXMLBF5-26JUN062210NYMSD-NYM","time":"2210"},
  {"id":"2026-06-06-030","ticker":"KXMLBRFI-26JUN062210NYMSD","time":"2210"},
]

results = {}
for b in bets:
    print(f"Fetching {b['id']} {b['ticker']}...")
    price = get_closing_price(b["ticker"], "2026-06-06", b["time"])
    results[b["id"]] = {"ticker": b["ticker"], "closing": price}
    print(f"  -> {price}")
    time.sleep(0.3)

import os
os.makedirs("data", exist_ok=True)
with open("data/kalshi_clv_20260606.json", "w") as f:
    json.dump(results, f, indent=2)

print(f"\nWrote data/kalshi_clv_20260606.json")
hits = sum(1 for v in results.values() if v["closing"])
print(f"Closing prices found: {hits}/{len(results)}")
