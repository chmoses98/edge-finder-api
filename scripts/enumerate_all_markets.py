#!/usr/bin/env python3
"""
enumerate_all_markets.py
========================
Exhaustively enumerate EVERY market attached to every MLB event on Kalshi.
Strategy:
  1. Pull all events in KXMLBGAME series (with_nested_markets=true)
  2. For each event, ALSO call /events/{event_ticker} directly
  3. For each event, pull /markets?event_ticker={event_ticker} (paginated)
  4. Combine all three sources — deduplicate by market_ticker
  5. Also search by series variants (KXMLB, KXMLBGAME, KXNRFI, KXYRFI, etc.)
  6. Dump raw API output for every event — no filtering, no classification
"""

import json, sys, os
from datetime import datetime, timezone, timedelta
from urllib.request import urlopen, Request
from urllib.error import HTTPError, URLError

KALSHI_BASE = 'https://api.elections.kalshi.com/trade-api/v2'
DATE = sys.argv[1] if len(sys.argv) > 1 else datetime.now(tz=timezone(timedelta(hours=-4))).strftime('%Y-%m-%d')

dt = datetime.strptime(DATE, '%Y-%m-%d')
MONTHS = ['JAN','FEB','MAR','APR','MAY','JUN','JUL','AUG','SEP','OCT','NOV','DEC']
KALSHI_DATE = str(dt.year)[2:] + MONTHS[dt.month-1] + str(dt.day).zfill(2)

print(f"DATE={DATE} KALSHI_DATE={KALSHI_DATE}")

def get(url, label=''):
    try:
        req = Request(url, headers={'Accept': 'application/json', 'User-Agent': 'Mozilla/5.0'})
        with urlopen(req, timeout=20) as r:
            raw = r.read()
            return json.loads(raw), None
    except HTTPError as e:
        body = e.read().decode('utf-8', errors='replace')[:200]
        return None, f"HTTP {e.code}: {body}"
    except URLError as e:
        return None, f"URLError: {e.reason}"
    except json.JSONDecodeError as e:
        return None, f"JSON: {e}"

def paginate(base_url, key, label='', max_pages=20):
    results = []
    cursor = ''
    for page in range(max_pages):
        url = f"{base_url}&cursor={cursor}" if cursor else base_url
        data, err = get(url, f"{label} p{page}")
        if err:
            print(f"  STOP [{label} p{page}]: {err}")
            break
        items = data.get(key, []) if data else []
        results.extend(items)
        cursor = (data or {}).get('cursor', '')
        print(f"  [{label} p{page}] {len(items)} items | cursor={'yes' if cursor else 'end'}")
        if not cursor or not items:
            break
    return results

# ── PHASE 1: Known MLB series tickers ────────────────────────────────────────
SERIES_CANDIDATES = [
    'KXMLBGAME',   # confirmed — game winner markets
    'KXMLB',       # may contain spread/total/TT variants
    'KXNRFI',      # NRFI dedicated series?
    'KXYRFI',      # YRFI dedicated series?
    'KXMLBF5',     # F5 dedicated series?
    'KXMLBTOT',    # totals?
    'KXMLBTT',     # team totals?
    'KXMLBRL',     # run line?
    'KXMLBSP',     # spread?
    'MLBNRFI',
    'MLBYRFI',
    'MLBF5',
    'MLBTOT',
]

all_events_by_series = {}
all_markets_by_series = {}

print("\n=== PHASE 1: Series-level event + market enumeration ===")
for series in SERIES_CANDIDATES:
    print(f"\n--- Series: {series} ---")
    
    # Events endpoint
    ev_url = f"{KALSHI_BASE}/events?series_ticker={series}&status=open&with_nested_markets=true&limit=100"
    events = paginate(ev_url, 'events', f"{series}/events")
    today_events = [e for e in events if KALSHI_DATE in (e.get('event_ticker','') or '')]
    if events:
        print(f"  Events total={len(events)}, today={len(today_events)}")
        all_events_by_series[series] = events
    
    # Markets endpoint  
    mk_url = f"{KALSHI_BASE}/markets?series_ticker={series}&status=open&limit=200"
    markets = paginate(mk_url, 'markets', f"{series}/markets")
    today_markets = [m for m in markets if KALSHI_DATE in (m.get('event_ticker','') or '')]
    if markets:
        print(f"  Markets total={len(markets)}, today={len(today_markets)}")
        all_markets_by_series[series] = markets

# ── PHASE 2: Per-event exhaustive enumeration ─────────────────────────────────
# Collect all event_tickers seen for today
today_event_tickers = set()
for series, events in all_events_by_series.items():
    for e in events:
        if KALSHI_DATE in (e.get('event_ticker','') or ''):
            today_event_tickers.add(e['event_ticker'])
for series, markets in all_markets_by_series.items():
    for m in markets:
        if KALSHI_DATE in (m.get('event_ticker','') or ''):
            today_event_tickers.add(m['event_ticker'])

print(f"\n=== PHASE 2: Per-event exhaustive market pull ({len(today_event_tickers)} events) ===")

all_markets_by_event = {}   # event_ticker → list of market dicts

for et in sorted(today_event_tickers):
    print(f"\n--- Event: {et} ---")
    event_markets = {}  # market_ticker → market dict (deduplicate)
    
    # 2a: GET /events/{event_ticker}?with_nested_markets=true
    url = f"{KALSHI_BASE}/events/{et}?with_nested_markets=true"
    data, err = get(url, f"event/{et}")
    if data:
        ev = data.get('event', data)
        nested = ev.get('markets', [])
        print(f"  /events/{et}: {len(nested)} nested markets")
        for m in nested:
            event_markets[m['ticker']] = m
    else:
        print(f"  /events/{et}: {err}")
    
    # 2b: GET /markets?event_ticker={et} (paginated — catches markets not in nested)
    mk_url = f"{KALSHI_BASE}/markets?event_ticker={et}&status=open&limit=200"
    mk_page2, err2 = get(mk_url, f"markets?et={et}")
    if mk_page2:
        for m in (mk_page2.get('markets') or []):
            event_markets[m['ticker']] = m
        cursor = mk_page2.get('cursor','')
        page = 1
        while cursor and page < 10:
            url2 = f"{KALSHI_BASE}/markets?event_ticker={et}&status=open&limit=200&cursor={cursor}"
            d2, _ = get(url2)
            if not d2: break
            for m in (d2.get('markets') or []):
                event_markets[m['ticker']] = m
            cursor = d2.get('cursor','')
            page += 1
        print(f"  /markets?event_ticker={et}: total unique now {len(event_markets)}")
    
    # 2c: Also try settled markets for this event (catches markets closed today)
    mk_settled_url = f"{KALSHI_BASE}/markets?event_ticker={et}&status=settled&limit=200"
    mk_settled, _ = get(mk_settled_url, f"settled/{et}")
    if mk_settled:
        settled_mkts = mk_settled.get('markets') or []
        for m in settled_mkts:
            if m['ticker'] not in event_markets:
                m['_settled'] = True
                event_markets[m['ticker']] = m
        if settled_mkts:
            print(f"  /markets?event_ticker={et}&status=settled: {len(settled_mkts)} additional")
    
    all_markets_by_event[et] = list(event_markets.values())
    print(f"  TOTAL unique markets for {et}: {len(event_markets)}")
    for ticker, m in sorted(event_markets.items()):
        bid = m.get('yes_bid_dollars') or m.get('yes_bid')
        ask = m.get('yes_ask_dollars') or m.get('yes_ask')
        print(f"    ticker={ticker}")
        print(f"    title={m.get('title','')}")
        print(f"    subtitle={m.get('subtitle','')}")
        print(f"    bid={bid} ask={ask} last={m.get('last_price_dollars') or m.get('last_price')}")
        print(f"    close_time={m.get('close_time','')}")
        print()

# ── PHASE 3: Broad open market search for MLB keywords ───────────────────────
print("\n=== PHASE 3: Keyword search for MLB-adjacent market types ===")
MLB_KEYWORDS = [
    'First 5 Innings', 'First 5', 'F5',
    'NRFI', 'No Run First Inning', 'First Inning Run',
    'YRFI', 'score in the first inning',
    'Total Runs', 'Over', 'Under',
    'wins by', 'run line', 'spread',
    'team total', 'score over',
    'strikeouts', 'strikeout',
]

found_extra = {}
for kw in MLB_KEYWORDS:
    url = f"{KALSHI_BASE}/markets?status=open&limit=20&search={kw.replace(' ','%20')}"
    data, err = get(url, f"search:{kw}")
    if data:
        mkts = data.get('markets', [])
        mlb_mkts = [m for m in mkts if KALSHI_DATE in (m.get('event_ticker','') or '')]
        if mlb_mkts:
            print(f"  kw='{kw}': {len(mlb_mkts)} today's markets")
            for m in mlb_mkts:
                t = m['ticker']
                if t not in found_extra:
                    found_extra[t] = m
                    print(f"    NEW: {t} | {m.get('title','')}")

# ── PHASE 4: Write full raw output ───────────────────────────────────────────
os.makedirs('data', exist_ok=True)
output = {
    'date': DATE,
    'kalshi_date': KALSHI_DATE,
    'fetched_at': datetime.now(tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
    'series_checked': SERIES_CANDIDATES,
    'series_with_today_events': {
        s: [e['event_ticker'] for e in evs if KALSHI_DATE in (e.get('event_ticker','') or '')]
        for s, evs in all_events_by_series.items()
        if any(KALSHI_DATE in (e.get('event_ticker','') or '') for e in evs)
    },
    'series_with_today_markets': {
        s: list({m['ticker'] for m in mkts if KALSHI_DATE in (m.get('event_ticker','') or '')})
        for s, mkts in all_markets_by_series.items()
        if any(KALSHI_DATE in (m.get('event_ticker','') or '') for m in mkts)
    },
    'markets_by_event': {
        et: [
            {
                'market_ticker': m['ticker'],
                'event_ticker':  m.get('event_ticker', et),
                'title':         m.get('title',''),
                'subtitle':      m.get('subtitle',''),
                'open_time':     m.get('open_time',''),
                'close_time':    m.get('close_time',''),
                'status':        m.get('status',''),
                'yes_bid':       m.get('yes_bid_dollars') or m.get('yes_bid'),
                'yes_ask':       m.get('yes_ask_dollars') or m.get('yes_ask'),
                'last_price':    m.get('last_price_dollars') or m.get('last_price'),
                'volume':        m.get('volume_fp') or m.get('volume'),
                '_settled':      m.get('_settled', False),
                '_raw_keys':     sorted(m.keys()),
            }
            for m in sorted(mkts, key=lambda x: x['ticker'])
        ]
        for et, mkts in all_markets_by_event.items()
    },
    'keyword_extras': {
        t: {'title': m.get('title',''), 'event_ticker': m.get('event_ticker','')}
        for t, m in found_extra.items()
    },
    'summary': {
        et: {
            'total_markets': len(mkts),
            'tickers': [m['ticker'] for m in sorted(mkts, key=lambda x: x['ticker'])],
        }
        for et, mkts in all_markets_by_event.items()
    }
}

with open('data/kalshi_full_enumeration.json', 'w') as f:
    json.dump(output, f, indent=2)

print(f"\n=== SUMMARY ===")
for et, s in output['summary'].items():
    print(f"{et}: {s['total_markets']} markets")
    for t in s['tickers']:
        print(f"  {t}")

print(f"\nWritten: data/kalshi_full_enumeration.json")
