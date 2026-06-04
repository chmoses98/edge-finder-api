#!/usr/bin/env python3
"""
Kalshi diagnostic v2 — dump all market data for the date to see actual ticker formats.
"""
import json, sys
from datetime import datetime, timezone, timedelta
from urllib.request import urlopen, Request

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

dt = datetime.strptime(DATE, '%Y-%m-%d')
min_ts = int(dt.replace(hour=0, minute=0, tzinfo=timezone.utc).timestamp())
max_ts = int((dt + timedelta(days=2)).replace(hour=6, tzinfo=timezone.utc).timestamp())

print(f"=== Kalshi ALL settled markets for {DATE} (ts {min_ts}..{max_ts}) ===\n")

# Paginate through ALL settled markets in that window
all_markets = []
cursor = ''
page = 0
while page < 20:
    url = f"{KALSHI_BASE}/markets?status=settled&min_settled_ts={min_ts}&max_settled_ts={max_ts}&limit=200"
    if cursor:
        url += f"&cursor={cursor}"
    d = get(url)
    if not d:
        print(f"Failed on page {page}")
        break
    markets = d.get('markets', [])
    all_markets.extend(markets)
    cursor = d.get('cursor', '')
    print(f"Page {page}: {len(markets)} markets | cursor={'yes' if cursor else 'none'}")
    if not cursor or not markets:
        break
    page += 1

print(f"\nTotal: {len(all_markets)} markets\n")

# Print all tickers and titles
print("=== ALL TICKERS + TITLES ===")
for m in all_markets:
    print(f"  {m.get('ticker','')[:50]:<50} | {(m.get('title') or '')[:60]}")

# Look for baseball-related by scanning titles broadly
print("\n=== BASEBALL / SPORTS MARKETS (broad search) ===")
sports_kw = ['baseball', 'mlb', 'inning', 'runs', 'pitcher', 'game', 'score', 
             'win', 'series', 'world series', 'playoff', 'season']
found = [m for m in all_markets if any(kw in (m.get('title','') or '').lower() or 
         kw in (m.get('event_ticker','') or '').lower() for kw in sports_kw)]
print(f"Found {len(found)} potentially sports-related:")
for m in found[:50]:
    print(f"  ticker={m.get('ticker','')} | event={m.get('event_ticker','')} | title={m.get('title','')}")

# Also check what categories/series are in there
print("\n=== UNIQUE EVENT_TICKER PREFIXES (first 6 chars) ===")
prefixes = {}
for m in all_markets:
    p = (m.get('event_ticker','') or '')[:8]
    prefixes[p] = prefixes.get(p, 0) + 1
for p, cnt in sorted(prefixes.items(), key=lambda x: -x[1])[:30]:
    print(f"  {p}: {cnt}")
