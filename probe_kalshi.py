"""
Probe specific Kalshi tickers for 6/5/26 to see what the API returns.
"""
import json, urllib.request, sys
from datetime import datetime, timezone, timedelta

KALSHI_BASE = 'https://api.elections.kalshi.com/trade-api/v2'

def kalshi_get(url):
    req = urllib.request.Request(url, headers={
        'Accept': 'application/json',
        'User-Agent': 'python-requests/2.28.0',
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, json.loads(r.read())
    except urllib.request.HTTPError as e:
        return e.code, e.read().decode()[:300]
    except Exception as e:
        return 0, str(e)

# Test tickers for 6/5/26
tickers = [
    'KXMLBRFI-26JUN051910TBMIA',
    'KXMLBF5-26JUN051910TBMIA-TB',
    'KXMLBF5-26JUN051910TBMIA-MIA',
    'KXMLBGAME-26JUN052010ATHHOU-HOU',
    'KXMLBGAME-26JUN051907BALTOR-TOR',
]

# Test: can we fetch the market itself first?
print("=== Market existence check ===")
for ticker in tickers:
    status, data = kalshi_get(f"{KALSHI_BASE}/markets/{ticker}")
    if isinstance(data, dict):
        print(f"  {ticker}: HTTP {status} | status={data.get('status','?')} | title={data.get('title','')[:40]}")
    else:
        print(f"  {ticker}: HTTP {status} | {str(data)[:80]}")

print("\n=== Candlestick check (TB@MIA NRFI, window around 23:10 UTC) ===")
# TB@MIA starts 23:10 UTC → window 21:10-23:10 UTC on 6/5
dt = datetime(2026, 6, 5, tzinfo=timezone.utc)
end_ts = int(datetime(2026, 6, 5, 23, 10, tzinfo=timezone.utc).timestamp())
start_ts = end_ts - 7200

ticker = 'KXMLBRFI-26JUN051910TBMIA'
for endpoint in ['markets', 'historical/markets']:
    url = f"{KALSHI_BASE}/{endpoint}/{ticker}/candlesticks?start_ts={start_ts}&end_ts={end_ts}&period_interval=60"
    status, data = kalshi_get(url)
    n_candles = len(data.get('candlesticks', [])) if isinstance(data, dict) else 0
    print(f"  [{endpoint}] HTTP {status} | candles={n_candles}")
    if isinstance(data, dict) and data.get('candlesticks'):
        last = data['candlesticks'][-1]
        print(f"    Last candle: ts={last.get('end_period_ts')} yes_bid={last.get('yes_bid')} yes_ask={last.get('yes_ask')}")
    elif isinstance(data, str):
        print(f"    Error: {data[:150]}")

# Also test with wider window in case our timing is off
print("\n=== Wide window test (full 6/5 to 6/6) ===")
start_wide = int(datetime(2026, 6, 5, 0, 0, tzinfo=timezone.utc).timestamp())
end_wide = int(datetime(2026, 6, 6, 6, 0, tzinfo=timezone.utc).timestamp())
url = f"{KALSHI_BASE}/markets/{ticker}/candlesticks?start_ts={start_wide}&end_ts={end_wide}&period_interval=3600"
status, data = kalshi_get(url)
n_candles = len(data.get('candlesticks', [])) if isinstance(data, dict) else 0
print(f"  Wide window: HTTP {status} | candles={n_candles}")
if isinstance(data, str): print(f"  {data[:200]}")
