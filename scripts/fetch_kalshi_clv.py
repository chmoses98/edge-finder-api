#!/usr/bin/env python3
"""Discover Kalshi tickers for 2026-06-06 MLB games from settled markets endpoint."""
import json, urllib.request, time
from datetime import datetime, timezone, timedelta

KALSHI_BASE = "https://api.elections.kalshi.com/trade-api/v2"

def kalshi_get(url):
    try:
        req = urllib.request.Request(url, headers={
            "Accept": "application/json", "User-Agent": "Mozilla/5.0"
        })
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except Exception as e:
        print(f"  API error for {url}: {e}")
        return None

# Step 1: Get settled markets for 6/6/2026
# settled_ts range: Jun 6 2026 00:00 UTC to Jun 7 2026 12:00 UTC
min_ts = int(datetime(2026, 6, 6, 12, 0, 0, tzinfo=timezone.utc).timestamp())
max_ts = int(datetime(2026, 6, 7, 12, 0, 0, tzinfo=timezone.utc).timestamp())

print(f"Fetching settled markets min_ts={min_ts} max_ts={max_ts}")

all_markets = []
for endpoint in [
    f"{KALSHI_BASE}/markets?status=settled&min_settled_ts={min_ts}&max_settled_ts={max_ts}&limit=1000",
    f"{KALSHI_BASE}/historical/markets?min_settled_ts={min_ts}&max_settled_ts={max_ts}&limit=1000",
]:
    print(f"  Trying: {endpoint}")
    data = kalshi_get(endpoint)
    if data:
        markets = data.get("markets", [])
        print(f"  Got {len(markets)} markets, keys: {list(data.keys())}")
        if markets:
            all_markets = markets
            break
        # Maybe different key name
        print(f"  Full response keys: {list(data.keys())}")
        print(f"  Sample: {json.dumps(data)[:500]}")

# Filter for MLB
mlb_markets = [m for m in all_markets if "MLB" in m.get("event_ticker","").upper() 
               or "KXML" in m.get("event_ticker","").upper()]

print(f"\nTotal settled: {len(all_markets)}, MLB: {len(mlb_markets)}")

# Save all MLB markets
import os; os.makedirs("data", exist_ok=True)
with open("data/kalshi_clv_20260606.json", "w") as f:
    json.dump({
        "total_settled": len(all_markets),
        "mlb_markets": mlb_markets[:50],
        "sample_all": all_markets[:5] if all_markets else [],
    }, f, indent=2)

print(f"\nTop MLB market tickers found:")
for m in mlb_markets[:20]:
    print(f"  {m.get('ticker','')} | {m.get('title','')} | result={m.get('result','?')}")
