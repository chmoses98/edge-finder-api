#!/usr/bin/env python3
import json, sys
from datetime import datetime, timezone, timedelta
from urllib.request import urlopen, Request

KALSHI_BASE = 'https://api.elections.kalshi.com/trade-api/v2'
DATE = sys.argv[1] if len(sys.argv) > 1 else '2026-06-04'
dt = datetime.strptime(DATE, '%Y-%m-%d')
MONTHS = ['JAN','FEB','MAR','APR','MAY','JUN','JUL','AUG','SEP','OCT','NOV','DEC']
KALSHI_DATE = str(dt.year)[2:] + MONTHS[dt.month-1] + str(dt.day).zfill(2)
print(f"KALSHI_DATE={KALSHI_DATE}")

def get(url):
    req = Request(url, headers={'Accept':'application/json','User-Agent':'Mozilla/5.0'})
    with urlopen(req, timeout=15) as r:
        return json.loads(r.read())

for series in ['KXMLBRFI','KXMLBTEAMTOTAL','KXMLBF5TOTAL','KXMLBF5SPREAD']:
    try:
        d = get(f"{KALSHI_BASE}/markets?series_ticker={series}&status=open&limit=200")
        mkts = d.get('markets',[])
        today = [m for m in mkts if KALSHI_DATE in (m.get('event_ticker','') or '')]
        # Also check for all markets to see what dates they use
        sample_et = mkts[0].get('event_ticker','') if mkts else 'none'
        print(f"{series}: {len(mkts)} total | {len(today)} today | sample_event={sample_et}")
        for m in today[:2]:
            print(f"  {m['ticker']} | {m.get('title','')} | bid={m.get('yes_bid_dollars')} ask={m.get('yes_ask_dollars')}")
    except Exception as e:
        print(f"{series}: ERROR {e}")
