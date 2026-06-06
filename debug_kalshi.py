"""
Debug script: fetch Kalshi settled markets for 2026-06-05
and dump all MLB tickers/titles to a JSON file.
Run via: python3 debug_kalshi.py
"""
import json, urllib.request, os
from datetime import datetime, timezone, timedelta

KALSHI_BASE = 'https://api.elections.kalshi.com/trade-api/v2'

def kalshi_get(url):
    req = urllib.request.Request(url, headers={'Accept': 'application/json', 'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())

date_str = '2026-06-05'
dt = datetime.strptime(date_str, '%Y-%m-%d')
min_ts = int(dt.replace(hour=0, tzinfo=timezone.utc).timestamp())
max_ts = int((dt + timedelta(days=2)).replace(hour=6, tzinfo=timezone.utc).timestamp())

print(f"Fetching settled markets {date_str} (ts {min_ts}..{max_ts})...")

urls = [
    f"{KALSHI_BASE}/markets?status=settled&min_settled_ts={min_ts}&max_settled_ts={max_ts}&limit=1000",
    f"{KALSHI_BASE}/historical/markets?min_settled_ts={min_ts}&max_settled_ts={max_ts}&limit=1000",
]

all_markets = []
for url in urls:
    try:
        data = kalshi_get(url)
        markets = data.get('markets', [])
        print(f"  {url[:60]}... → {len(markets)} markets")
        if markets:
            all_markets = markets
            break
    except Exception as e:
        print(f"  ERROR: {e}")

# Filter for baseball-looking tickers
mlb_markets = [m for m in all_markets if any(
    kw in (m.get('event_ticker','') or '').upper() or
    kw in (m.get('title','') or '').upper()
    for kw in ['MLB', 'KXMLB', 'BASEBALL', 'SEA', 'DET', 'BAL', 'TOR', 'TB', 'MIA',
               'PIT', 'ATL', 'ATH', 'HOU', 'CIN', 'STL', 'CLE', 'TEX', 'KC', 'MIN',
               'NYM', 'SD', 'LAA', 'LAD', 'WSH', 'AZ']
)]

print(f"\nTotal markets: {len(all_markets)} | MLB-related: {len(mlb_markets)}")
print("\nSample MLB market tickers/titles:")
for m in mlb_markets[:30]:
    print(f"  {m.get('ticker',''):<45} | {(m.get('title') or '')[:60]}")

with open('kalshi_debug.json', 'w') as f:
    json.dump({'total': len(all_markets), 'mlb': mlb_markets, 'sample_all': all_markets[:10]}, f, indent=2)
print("\nSaved to kalshi_debug.json")
