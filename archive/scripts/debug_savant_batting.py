"""Debug: show Kalshi settled market tickers for recent dates."""
import urllib.request, json, time

KALSHI_BASE = 'https://api.elections.kalshi.com/trade-api/v2'

def get(url):
    try:
        req = urllib.request.Request(url, headers={'Accept': 'application/json', 'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read())
    except Exception as e:
        print(f'  Error: {e}')
        return None

# Fetch a page of settled markets and show their event_tickers
print('Fetching settled Kalshi markets...')
data = get(f'{KALSHI_BASE}/markets?status=settled&limit=50')
if data:
    markets = data.get('markets', [])
    print(f'Got {len(markets)} markets')
    # Show unique event_ticker patterns
    seen = set()
    for m in markets:
        et = m.get('event_ticker', '')
        tk = m.get('ticker', '')
        title = (m.get('title') or '')[:40]
        if et not in seen and ('MLB' in et.upper() or 'KXML' in et.upper() or 'Jun' in et or 'Jun' in tk):
            seen.add(et)
            print(f'  ticker={tk} | event={et} | title={title}')
    print()
    # Show all event_tickers to find the date pattern
    print('All MLB-related event_tickers:')
    for m in markets:
        et = m.get('event_ticker','')
        if 'KXMLB' in et or 'KXNRFI' in et or 'KXYRFI' in et or 'F5' in et.upper():
            print(f'  {et} -> {m.get("ticker","")} ({(m.get("title") or "")[:35]})')
else:
    print('No data returned')
