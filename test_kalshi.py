#!/usr/bin/env python3
"""
Diagnostic: test Kalshi API access and check what MLB markets exist for a given date.
Run this in GitHub Actions to test from unrestricted network.
"""
import json, sys, time
from datetime import datetime, timezone, timedelta
from urllib.request import urlopen, Request
from urllib.error import HTTPError

KALSHI_BASE = 'https://api.elections.kalshi.com/trade-api/v2'
DATE = sys.argv[1] if len(sys.argv) > 1 else '2026-06-03'

def get(url):
    try:
        req = Request(url, headers={'Accept': 'application/json', 'User-Agent': 'Mozilla/5.0'})
        with urlopen(req, timeout=20) as r:
            return json.loads(r.read())
    except Exception as e:
        print(f"  ERROR: {e}")
        return None

print(f"=== Kalshi API Diagnostic for {DATE} ===\n")

# Step 1: Check historical cutoff
print("1. GET /historical/cutoff")
d = get(f"{KALSHI_BASE}/historical/cutoff")
print(f"   Result: {json.dumps(d, indent=2) if d else 'FAILED'}\n")

# Step 2: Build timestamp range for the date
dt = datetime.strptime(DATE, '%Y-%m-%d')
min_ts = int(dt.replace(hour=0, minute=0, tzinfo=timezone.utc).timestamp())
max_ts = int((dt + timedelta(days=2)).replace(hour=6, tzinfo=timezone.utc).timestamp())
print(f"2. Timestamp range: {min_ts} → {max_ts}")

# Step 3: Try live endpoint for settled markets on that date
print(f"\n3. GET /markets?status=settled&min_settled_ts={min_ts}&max_settled_ts={max_ts}&limit=200")
d = get(f"{KALSHI_BASE}/markets?status=settled&min_settled_ts={min_ts}&max_settled_ts={max_ts}&limit=200")
if d:
    markets = d.get('markets', [])
    print(f"   Total: {len(markets)} markets")
    mlb = [m for m in markets if 'MLB' in (m.get('event_ticker','') or '').upper() 
           or 'BASEBALL' in (m.get('title','') or '').upper()
           or any(t in (m.get('event_ticker','') or '').upper() 
                  for t in ['NYY','NYM','LAD','BOS','ATL','CHC','STL','SF','SD','MIA','PHI','PIT',
                             'SEA','HOU','TB','DET','MIN','MIL','CLE','CIN','KC','COL','TEX',
                             'TOR','BAL','ATH','WSH','LAA','ARI'])]
    print(f"   MLB-related: {len(mlb)} markets")
    for m in mlb[:20]:
        print(f"     ticker={m.get('ticker','')} | event={m.get('event_ticker','')} | title={m.get('title','')}")
else:
    print("   FAILED — trying historical endpoint")
    # Step 4: Try historical endpoint
    print(f"\n4. GET /historical/markets?min_settled_ts={min_ts}&max_settled_ts={max_ts}&limit=200")
    d = get(f"{KALSHI_BASE}/historical/markets?min_settled_ts={min_ts}&max_settled_ts={max_ts}&limit=200")
    if d:
        markets = d.get('markets', [])
        print(f"   Total: {len(markets)} markets")
        mlb = [m for m in markets if any(t in (m.get('event_ticker','') or '').upper()
                for t in ['NYY','NYM','LAD','BOS','ATL'])]
        print(f"   Sample MLB: {len(mlb)}")
        for m in mlb[:10]:
            print(f"     {m.get('ticker','')} | {m.get('event_ticker','')} | {m.get('title','')}")
    else:
        print("   FAILED")

# Step 5: Search for specific F5 markets
print(f"\n5. Searching for F5 / first 5 inning markets...")
d = get(f"{KALSHI_BASE}/markets?status=settled&min_settled_ts={min_ts}&max_settled_ts={max_ts}&limit=1000")
if d:
    markets = d.get('markets', [])
    f5 = [m for m in markets if any(kw in (m.get('title','') or '').lower() 
          for kw in ['first 5', 'f5', '5 innings', 'first five'])]
    print(f"   F5 markets found: {len(f5)}")
    for m in f5[:10]:
        print(f"     {m.get('ticker','')} | {m.get('title','')}")
