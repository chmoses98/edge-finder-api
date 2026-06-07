"""
Debug script: fetch Kalshi settled markets for a date and dump to JSON.
"""
import json, urllib.request, os, sys
from datetime import datetime, timezone, timedelta

KALSHI_BASE = 'https://api.elections.kalshi.com/trade-api/v2'

def kalshi_get(url):
    req = urllib.request.Request(url, headers={'Accept': 'application/json', 'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())

date_str = os.environ.get('DEBUG_DATE') or (sys.argv[1] if len(sys.argv) > 1 else '2026-06-05')
print(f"Debugging Kalshi markets for {date_str}")

dt = datetime.strptime(date_str, '%Y-%m-%d')
min_ts = int(dt.replace(hour=0, tzinfo=timezone.utc).timestamp())
max_ts = int((dt + timedelta(days=2)).replace(hour=6, tzinfo=timezone.utc).timestamp())

all_markets = []
for label, url in [
    ('live', f"{KALSHI_BASE}/markets?status=settled&min_settled_ts={min_ts}&max_settled_ts={max_ts}&limit=1000"),
    ('historical', f"{KALSHI_BASE}/historical/markets?min_settled_ts={min_ts}&max_settled_ts={max_ts}&limit=1000"),
]:
    try:
        data = kalshi_get(url)
        markets = data.get('markets', [])
        print(f"  [{label}] {len(markets)} markets")
        if markets:
            all_markets = markets
            break
    except Exception as e:
        print(f"  [{label}] ERROR: {e}")

# Kalshi date string fallback
kalshi_date = dt.strftime('%y') + dt.strftime('%b') + dt.strftime('%d').lstrip('0')
print(f"  Kalshi date string: {kalshi_date}")

if not all_markets:
    print("  Falling back to pagination with event_ticker date filter...")
    cursor = ''
    for page in range(10):
        url = f"{KALSHI_BASE}/markets?status=settled&limit=200"
        if cursor: url += f"&cursor={cursor}"
        try:
            data = kalshi_get(url)
            markets = data.get('markets', [])
            day = [m for m in markets if kalshi_date in (m.get('event_ticker','') or '')]
            all_markets.extend(day)
            cursor = data.get('cursor', '')
            if not cursor or not markets:
                break
        except Exception as e:
            print(f"  Page {page} error: {e}")
            break
    print(f"  Pagination total: {len(all_markets)}")

# Dump all tickers and titles
print(f"\nAll markets ({len(all_markets)}):")
for m in all_markets:
    print(f"  {m.get('ticker',''):<50} | ET:{m.get('event_ticker',''):<35} | {(m.get('title') or '')[:50]}")

# Save output
os.makedirs('data', exist_ok=True)
out = {'date': date_str, 'total': len(all_markets), 'markets': all_markets}
with open('data/kalshi_debug.json', 'w') as f:
    json.dump(out, f, indent=2)
print(f"\nSaved {len(all_markets)} markets to data/kalshi_debug.json")
