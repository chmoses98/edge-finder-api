#!/usr/bin/env python3
"""
pull_confirmed.py
Now that we know all series, pull complete market lists for the final unknown ones:
KXMLBRFI (NRFI/YRFI), KXMLBTEAMTOTAL (TT), KXMLBF5TOTAL, KXMLBF5SPREAD
"""
import json, sys, os
from datetime import datetime, timezone, timedelta
from urllib.request import urlopen, Request
from urllib.error import HTTPError

KALSHI_BASE = 'https://api.elections.kalshi.com/trade-api/v2'
DATE = sys.argv[1] if len(sys.argv) > 1 else datetime.now(tz=timezone(timedelta(hours=-4))).strftime('%Y-%m-%d')
dt = datetime.strptime(DATE, '%Y-%m-%d')
MONTHS = ['JAN','FEB','MAR','APR','MAY','JUN','JUL','AUG','SEP','OCT','NOV','DEC']
KALSHI_DATE = str(dt.year)[2:] + MONTHS[dt.month-1] + str(dt.day).zfill(2)

def get(url):
    try:
        req = Request(url, headers={'Accept':'application/json','User-Agent':'Mozilla/5.0'})
        with urlopen(req, timeout=15) as r:
            return json.loads(r.read()), None
    except HTTPError as e:
        return None, f"HTTP{e.code}"
    except Exception as e:
        return None, str(e)[:80]

def pull_series(series_ticker):
    all_mkts = []
    cursor = ''
    for page in range(10):
        url = f"{KALSHI_BASE}/markets?series_ticker={series_ticker}&status=open&limit=200"
        if cursor: url += f"&cursor={cursor}"
        data, err = get(url)
        if not data: break
        mkts = data.get('markets') or []
        all_mkts.extend(mkts)
        cursor = data.get('cursor','')
        if not cursor or not mkts: break
    today = [m for m in all_mkts if KALSHI_DATE in (m.get('event_ticker','') or '')]
    return all_mkts, today

# Confirmed series to fully document
SERIES = [
    'KXMLBRFI',      # Run in First Inning = NRFI/YRFI
    'KXMLBTEAMTOTAL',# Team Total
    'KXMLBF5TOTAL',  # F5 Total
    'KXMLBF5SPREAD', # F5 Spread
    # Also check these from the series list
    'KXMLBEXTRAS',   # Extra innings?
    'KXMLBKS',       # Strikeouts
    'KXMLBHR',       # Home Runs
]

all_results = {}
for series in SERIES:
    print(f"\n=== {series} ===")
    all_mkts, today = pull_series(series)
    print(f"Total: {len(all_mkts)} | Today: {len(today)}")
    
    if today:
        by_event = {}
        for m in today:
            et = m.get('event_ticker','')
            if et not in by_event: by_event[et] = []
            by_event[et].append(m)
        
        for et, mkts in sorted(by_event.items()):
            print(f"\n  {et} ({len(mkts)} markets):")
            for m in sorted(mkts, key=lambda x: x['ticker']):
                print(f"    {m['ticker']}")
                print(f"    title: {m.get('title','')}")
                print(f"    bid={m.get('yes_bid_dollars')} ask={m.get('yes_ask_dollars')}")
        
        all_results[series] = {
            'total': len(all_mkts),
            'today': len(today),
            'by_event': {
                et: [{'ticker': m['ticker'], 'title': m.get('title',''),
                      'yes_bid': m.get('yes_bid_dollars'), 'yes_ask': m.get('yes_ask_dollars'),
                      'close_time': m.get('close_time','')}
                     for m in sorted(mkts, key=lambda x: x['ticker'])]
                for et, mkts in by_event.items()
            }
        }
    else:
        print(f"  (no today markets)")
        # Show sample to understand what's there
        if all_mkts:
            print(f"  Sample: {all_mkts[0].get('event_ticker','')} | {all_mkts[0].get('title','')}")

os.makedirs('data', exist_ok=True)
with open('data/kalshi_confirmed_series.json','w') as f:
    json.dump({
        'date': DATE,
        'kalshi_date': KALSHI_DATE,
        'fetched_at': datetime.now(tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'results': all_results
    }, f, indent=2)
print("\nWritten: data/kalshi_confirmed_series.json")
