#!/usr/bin/env python3
import json, urllib.request, os

KALSHI_BASE = "https://api.elections.kalshi.com/trade-api/v2"

def kalshi_get_raw(url):
    try:
        req = urllib.request.Request(url, headers={
            "Accept": "application/json", "User-Agent": "Mozilla/5.0"
        })
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
            print(f"  Status: {r.status}, bytes: {len(raw)}")
            return raw.decode("utf-8")
    except Exception as e:
        print(f"  Error: {type(e).__name__}: {e}")
        return None

os.makedirs("data", exist_ok=True)
results = {}

# Test 1: single known ticker - try candlesticks for a late game (NYM@SD)
# This market was active at 00:37 UTC so the ticker is confirmed correct
print("Test 1: NYM@SD ML candlesticks (confirmed ticker from kalshi_search.json)")
ticker = "KXMLBGAME-26JUN062210NYMSD-NYM"
# game at 2210 ET = 0210 UTC next day; pre-game window = 0010-0210 UTC 6/7
# start_ts = Jun 7 00:10 UTC, end_ts = Jun 7 02:10 UTC
import time
start_ts = 1749254400  # Jun 7 2026 00:00 UTC
end_ts   = 1749261600  # Jun 7 2026 02:00 UTC
for url in [
    f"{KALSHI_BASE}/markets/{ticker}/candlesticks?start_ts={start_ts}&end_ts={end_ts}&period_interval=60",
    f"{KALSHI_BASE}/historical/markets/{ticker}/candlesticks?start_ts={start_ts}&end_ts={end_ts}&period_interval=60",
]:
    print(f"  URL: {url}")
    raw = kalshi_get_raw(url)
    print(f"  Response: {raw[:300] if raw else None}")
    results[f"test_candle_{url[-15:]}"] = raw[:500] if raw else "ERROR"

# Test 2: fetch market details for the ticker  
print("\nTest 2: Market details for KXMLBGAME-26JUN062210NYMSD-NYM")
url2 = f"{KALSHI_BASE}/markets/KXMLBGAME-26JUN062210NYMSD-NYM"
raw2 = kalshi_get_raw(url2)
print(f"  Response: {raw2[:300] if raw2 else None}")
results["test_market_detail"] = raw2[:500] if raw2 else "ERROR"

# Test 3: settled markets search
print("\nTest 3: Settled markets endpoint")
url3 = f"{KALSHI_BASE}/markets?status=settled&limit=5"
raw3 = kalshi_get_raw(url3)
print(f"  Response: {raw3[:500] if raw3 else None}")
results["test_settled"] = raw3[:1000] if raw3 else "ERROR"

with open("data/kalshi_clv_20260606.json", "w") as f:
    json.dump(results, f, indent=2)
print("\nDone. Wrote debug results.")
