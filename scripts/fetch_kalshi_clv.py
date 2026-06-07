#!/usr/bin/env python3
"""Fetch Kalshi pre-game closing prices for 2026-06-06 bets.
Strategy:
1. Look up each market to get its close_time
2. Use candlestick API with window ending at first pitch time
3. Fall back to /markets/{ticker} last_price if candlestick unavailable
"""
import json, urllib.request, os, time
from datetime import datetime, timezone, timedelta

KALSHI_BASE = "https://api.elections.kalshi.com/trade-api/v2"

def kget(url):
    try:
        req = urllib.request.Request(url, headers={"Accept":"application/json","User-Agent":"Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read()), None
    except urllib.error.HTTPError as e:
        return None, f"HTTP {e.code}"
    except Exception as e:
        return None, str(e)

def get_market_price(ticker, first_pitch_utc_ts):
    """Get last pre-game price for a market ticker."""
    # Try candlestick in 2hr window before first pitch
    start_ts = first_pitch_utc_ts - 7200
    end_ts   = first_pitch_utc_ts
    
    for url in [
        f"{KALSHI_BASE}/markets/{ticker}/candlesticks?start_ts={start_ts}&end_ts={end_ts}&period_interval=60",
        f"{KALSHI_BASE}/historical/markets/{ticker}/candlesticks?start_ts={start_ts}&end_ts={end_ts}&period_interval=60",
    ]:
        data, err = kget(url)
        if data and data.get("candlesticks"):
            candles = data["candlesticks"]
            last = max(candles, key=lambda c: c.get("end_period_ts", 0))
            bid_raw = last.get("yes_bid", {}).get("close")
            ask_raw = last.get("yes_ask", {}).get("close")
            try:
                bid = float(bid_raw) if bid_raw else None
                ask = float(ask_raw) if ask_raw else None
            except: bid, ask = None, None
            if bid and ask:
                mid = round((bid+ask)/2, 4)
                imp = round(mid*100, 1)
                amer = round(-mid/(1-mid)*100) if mid>0.5 else round((1-mid)/mid*100)
                return {"source":"candlestick","mid":mid,"implied_pct":imp,"american":amer,
                        "num_candles":len(candles),"yes_bid":bid,"yes_ask":ask}
            return {"source":"candlestick_nodata","error":f"{err} candles={data.get('candlesticks')}"}
    
    # Fallback: get market detail and use last_price
    data, err = kget(f"{KALSHI_BASE}/markets/{ticker}")
    if data and "market" in data:
        m = data["market"]
        lp = m.get("last_price")
        try:
            p = float(lp) if lp else None
        except: p = None
        if p:
            imp = round(p*100, 1)
            amer = round(-p/(1-p)*100) if p>0.5 else round((1-p)/p*100)
            return {"source":"last_price","mid":p,"implied_pct":imp,"american":amer}
        return {"source":"market_detail_nodata","status":m.get("status"),"last_price":lp}
    return {"source":"error","error":err}

# All bets with tickers and first pitch times (Unix UTC)
# First pitch times: ET + 4hrs = UTC
bets = [
  # KC@MIN 2:10pm ET = 18:10 UTC = 1749226200
  {"id":"001","ticker":"KXMLBGAME-26JUN061410KCMIN-MIN","fp":1749226200},
  {"id":"002","ticker":"KXMLBF5-26JUN061410KCMIN-MIN","fp":1749226200},
  {"id":"003","ticker":"KXMLBRFI-26JUN061410KCMIN","fp":1749226200},
  # CIN@STL 2:15pm ET = 18:15 UTC = 1749226500
  {"id":"004","ticker":"KXMLBGAME-26JUN061415CINSTL-STL","fp":1749226500},
  {"id":"005","ticker":"KXMLBF5-26JUN061415CINSTL-STL","fp":1749226500},
  {"id":"006","ticker":"KXMLBRFI-26JUN061415CINSTL","fp":1749226500},
  # SF@CHC 2:20pm ET = 18:20 UTC = 1749226800
  {"id":"007","ticker":"KXMLBGAME-26JUN061420SFCHC-SF","fp":1749226800},
  {"id":"008","ticker":"KXMLBF5-26JUN061420SFCHC-SF","fp":1749226800},
  # BAL@TOR 3:07pm ET = 19:07 UTC = 1749229620
  {"id":"009","ticker":"KXMLBRFI-26JUN061507BALTOR","fp":1749229620},
  # CWS@PHI 4:05pm ET = 20:05 UTC = 1749232500
  {"id":"010","ticker":"KXMLBGAME-26JUN061605CWSPHI-CWS","fp":1749232500},
  {"id":"011","ticker":"KXMLBRFI-26JUN061605CWSPHI","fp":1749232500},
  # WSH@AZ 4:10pm ET = 20:10 UTC = 1749232800
  {"id":"012","ticker":"KXMLBF5-26JUN061610WSHARI-WSH","fp":1749232800},
  {"id":"013","ticker":"KXMLBRFI-26JUN061610WSHARI","fp":1749232800},
  # ATH@HOU 4:10pm ET = 20:10 UTC = 1749232800
  {"id":"014","ticker":"KXMLBF5-26JUN061610ATHHOU-HOU","fp":1749232800},
  {"id":"015","ticker":"KXMLBRFI-26JUN061610ATHHOU","fp":1749232800},
  {"id":"016","ticker":"KXMLBGAME-26JUN061610ATHHOU-ATH","fp":1749232800},
  # PIT@ATL 4:10pm ET = 20:10 UTC = 1749232800
  {"id":"017","ticker":"KXMLBGAME-26JUN061610PITATL-PIT","fp":1749232800},
  {"id":"018","ticker":"KXMLBF5-26JUN061610PITATL-PIT","fp":1749232800},
  {"id":"019","ticker":"KXMLBRFI-26JUN061610PITATL","fp":1749232800},
  # CLE@TEX 7:35pm ET = 23:35 UTC = 1749245700
  {"id":"020","ticker":"KXMLBF5-26JUN061935CLETEX-TEX","fp":1749245700},
  {"id":"021","ticker":"KXMLBRFI-26JUN061935CLETEX","fp":1749245700},
  # BOS@NYY 7:35pm ET = 23:35 UTC = 1749245700
  {"id":"022","ticker":"KXMLBF5-26JUN061935BOSNYY-NYY","fp":1749245700},
  # MIL@COL 9:10pm ET = 01:10 UTC+1 = 1749251400
  {"id":"023","ticker":"KXMLBF5-26JUN062110MILCOL-COL","fp":1749251400},
  {"id":"024","ticker":"KXMLBRFI-26JUN062110MILCOL","fp":1749251400},
  # LAA@LAD 10:10pm ET = 02:10 UTC+1 = 1749254400 (actually 02:10 = 1749255000)
  {"id":"025","ticker":"KXMLBGAME-26JUN062210LAALAD-LAA","fp":1749255000},
  {"id":"026","ticker":"KXMLBF5-26JUN062210LAALAD-LAA","fp":1749255000},
  {"id":"027","ticker":"KXMLBRFI-26JUN062210LAALAD","fp":1749255000},
  # NYM@SD 10:10pm ET = 02:10 UTC+1 = 1749255000
  {"id":"028","ticker":"KXMLBGAME-26JUN062210NYMSD-NYM","fp":1749255000},
  {"id":"029","ticker":"KXMLBF5-26JUN062210NYMSD-NYM","fp":1749255000},
  {"id":"030","ticker":"KXMLBRFI-26JUN062210NYMSD","fp":1749255000},
]

os.makedirs("data", exist_ok=True)
results = {}
for b in bets:
    bid = f"2026-06-06-{b['id']:>03}"
    print(f"Fetching {bid} {b['ticker']}...")
    price = get_market_price(b["ticker"], b["fp"])
    results[bid] = {"ticker": b["ticker"], "closing": price}
    src = price.get("source","?") if price else "none"
    imp = price.get("implied_pct","?") if price else "?"
    print(f"  {src}: {imp}%")
    time.sleep(0.25)

with open("data/kalshi_clv_20260606.json", "w") as f:
    json.dump(results, f, indent=2)

hits = sum(1 for v in results.values() if v.get("closing",{}).get("implied_pct"))
print(f"\nDone. Prices found: {hits}/{len(results)}")
