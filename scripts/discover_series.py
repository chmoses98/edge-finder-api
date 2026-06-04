#!/usr/bin/env python3
"""
discover_series.py
==================
Find ALL Kalshi series that contain MLB markets for today.

Approach:
1. Brute-force series ticker variants (many more than first pass)
2. Search open markets by every relevant keyword — collect event_tickers
3. From those event_tickers, derive series tickers (prefix before first -)
4. Query each derived series fully
5. Pull the Kalshi series list endpoint (if it exists)
"""

import json, sys, os, time
from datetime import datetime, timezone, timedelta
from urllib.request import urlopen, Request
from urllib.error import HTTPError, URLError

KALSHI_BASE = 'https://api.elections.kalshi.com/trade-api/v2'
DATE = sys.argv[1] if len(sys.argv) > 1 else datetime.now(tz=timezone(timedelta(hours=-4))).strftime('%Y-%m-%d')
dt = datetime.strptime(DATE, '%Y-%m-%d')
MONTHS = ['JAN','FEB','MAR','APR','MAY','JUN','JUL','AUG','SEP','OCT','NOV','DEC']
KALSHI_DATE = str(dt.year)[2:] + MONTHS[dt.month-1] + str(dt.day).zfill(2)
print(f"DATE={DATE}  KALSHI_DATE={KALSHI_DATE}\n")

def get(url):
    try:
        req = Request(url, headers={'Accept':'application/json','User-Agent':'Mozilla/5.0'})
        with urlopen(req, timeout=20) as r:
            return json.loads(r.read()), None
    except HTTPError as e:
        return None, f"HTTP{e.code}"
    except Exception as e:
        return None, str(e)[:60]

# ── 1. Brute-force series tickers ────────────────────────────────────────────
# Kalshi naming patterns observed: KX{SPORT}{TYPE}
# SPORT variations: MLB, MLBGAME, MLBF5
# TYPE variations: none, GAME, F5, RL, SP, TOT, TT, NRFI, YRFI, RUN, RUNS, SPREAD
CANDIDATES = [
    # Confirmed
    'KXMLBGAME', 'KXMLBF5',
    # Spread / run line
    'KXMLBRL', 'KXMLBSP', 'KXMLBSPREAD', 'KXMLBRUNLINE', 'KXMLBRLINE',
    # Totals
    'KXMLBTOT', 'KXMLBTOTAL', 'KXMLBTOTALS', 'KXMLBRUNS', 'KXMLBRUN',
    'KXMLBOVER', 'KXMLBU', 'KXMLBO',
    # Team totals
    'KXMLBTT', 'KXMLBTEAMTOT', 'KXMLBTEAMTOTAL',
    # NRFI / YRFI
    'KXNRFI', 'KXYRFI', 'KXMLBNRFI', 'KXMLBYRFI',
    'KXMLB1ST', 'KXMLB1STIN', 'KXMLBFI', 'KXMLBFIRST',
    'KXMLBF1', 'KXMLBI1', 'KXMLB1INN',
    # F5 variants
    'KXMLBF5RL', 'KXMLBF5SP', 'KXMLBF5TOT',
    # Without KX prefix
    'MLBGAME', 'MLBF5', 'MLBRL', 'MLBTOT', 'MLBTT', 'MLBNRFI', 'MLBYRFI',
    # Alternative patterns
    'KXBB', 'KXBASEBALL', 'KXMLBWIN', 'KXMLBWINNER',
    'KXMLBSCORE', 'KXMLBSCORES',
]

found_series = {}  # series_ticker → list of today's market tickers

print("=== Phase 1: Series brute-force ===")
for series in CANDIDATES:
    # Try events
    url = f"{KALSHI_BASE}/events?series_ticker={series}&status=open&limit=50"
    data, err = get(url)
    events_today = []
    if data:
        for e in (data.get('events') or []):
            if KALSHI_DATE in (e.get('event_ticker','') or ''):
                events_today.append(e.get('event_ticker'))
    
    # Try markets
    url2 = f"{KALSHI_BASE}/markets?series_ticker={series}&status=open&limit=200"
    data2, err2 = get(url2)
    mkts_today = []
    if data2:
        for m in (data2.get('markets') or []):
            if KALSHI_DATE in (m.get('event_ticker','') or ''):
                mkts_today.append(m.get('ticker'))
    
    if events_today or mkts_today:
        found_series[series] = {'events': events_today, 'markets': mkts_today}
        print(f"  ✅ {series}: {len(events_today)} events, {len(mkts_today)} markets")
        for t in mkts_today[:6]:
            print(f"     {t}")

# ── 2. Keyword search — find market_tickers and derive series ────────────────
print("\n=== Phase 2: Keyword search → series discovery ===")

# Broad set — including things that might appear in run line/total/NRFI titles
KEYWORDS = [
    'baseball', 'MLB', 'inning', 'innings', 'runs', 'strikeout',
    'moneyline', 'spread', 'total', 'over', 'under', 'winner',
    'run line', 'first 5', 'no run', 'NRFI', 'YRFI', 'score',
    'Pittsburgh', 'Houston', 'Atlanta', 'Toronto', 'Kansas City',
    'Minnesota', 'Los Angeles', 'Arizona', 'Milwaukee', 'San Francisco',
    'Athletics', "A's", 'Cubs', 'Chicago C',
]

keyword_markets = {}  # ticker → market dict
for kw in KEYWORDS:
    url = f"{KALSHI_BASE}/markets?status=open&limit=100&search={kw.replace(' ','%20')}"
    data, err = get(url)
    if data:
        for m in (data.get('markets') or []):
            t = m.get('ticker','')
            et = m.get('event_ticker','')
            if KALSHI_DATE in et or KALSHI_DATE in t:
                if t not in keyword_markets:
                    keyword_markets[t] = m
                    print(f"  [{kw:15s}] {t} | {m.get('title','')[:50]}")
    time.sleep(0.1)  # gentle rate limiting

# Derive series from found tickers
derived_series = set()
for t in keyword_markets:
    parts = t.split('-')
    if len(parts) >= 2:
        # Series is everything before the date portion
        # e.g. KXMLBF5-26JUN04... → KXMLBF5
        derived_series.add(parts[0])

print(f"\n  Derived series from keyword hits: {sorted(derived_series)}")
for s in sorted(derived_series):
    if s not in found_series and s not in CANDIDATES:
        print(f"  NEW series found via keyword: {s}")
        url = f"{KALSHI_BASE}/markets?series_ticker={s}&status=open&limit=200"
        data, err = get(url)
        if data:
            today_mkts = [m for m in (data.get('markets') or []) 
                         if KALSHI_DATE in (m.get('event_ticker','') or '')]
            found_series[s] = {'events': [], 'markets': [m['ticker'] for m in today_mkts]}
            for m in today_mkts:
                print(f"    {m['ticker']} | {m.get('title','')}")

# ── 3. Try the series list endpoint ─────────────────────────────────────────
print("\n=== Phase 3: Kalshi series list endpoint ===")
for url in [
    f"{KALSHI_BASE}/series",
    f"{KALSHI_BASE}/series?category=sports",
    f"{KALSHI_BASE}/series?search=mlb",
    f"{KALSHI_BASE}/series?search=baseball",
]:
    data, err = get(url)
    if data:
        series_list = data.get('series', data.get('items', []))
        mlb_series = [s for s in series_list if any(
            kw in (s.get('ticker','') + s.get('title','')).upper()
            for kw in ['MLB','BASEBALL','KXMLB']
        )]
        print(f"  {url}")
        print(f"  → {len(series_list)} total series, {len(mlb_series)} MLB-related")
        for s in mlb_series:
            print(f"    {s.get('ticker','')}: {s.get('title','')}")
    else:
        print(f"  {url} → {err}")

# ── 4. Try event_ticker direct variants ─────────────────────────────────────
# We know KXMLBGAME events. Do other series use the same event_ticker suffix?
print(f"\n=== Phase 4: Direct event_ticker variants for known games ===")
KNOWN_GAME_SUFFIXES = [
    '1915TORATL', '1940KCMIN', '2005ATHCHC', '2010PITHOU', '2140LADAZ'
]
TYPE_PREFIXES = [
    'KXMLBRL', 'KXMLBSP', 'KXMLBTOT', 'KXMLBTT', 'KXNRFI', 'KXYRFI',
    'KXMLBF5RL', 'KXMLBNRFI', 'KXMLBYRFI', 'KXMLBI1', 'KXMLB1',
]

for suffix in KNOWN_GAME_SUFFIXES:
    for prefix in TYPE_PREFIXES:
        candidate_et = f"{prefix}-{KALSHI_DATE}{suffix}"
        url = f"{KALSHI_BASE}/events/{candidate_et}?with_nested_markets=true"
        data, err = get(url)
        if data and 'event' in data:
            ev = data['event']
            mkts = ev.get('markets', [])
            print(f"  ✅ FOUND: {candidate_et} ({len(mkts)} markets)")
            for m in mkts:
                print(f"    {m.get('ticker','')} | {m.get('title','')}")
        # Don't print misses — too noisy

# ── 5. Write output ──────────────────────────────────────────────────────────
os.makedirs('data', exist_ok=True)
output = {
    'date': DATE,
    'kalshi_date': KALSHI_DATE,
    'fetched_at': datetime.now(tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
    'confirmed_series': found_series,
    'keyword_market_tickers': {
        t: {
            'title': m.get('title',''),
            'event_ticker': m.get('event_ticker',''),
            'yes_bid': m.get('yes_bid_dollars') or m.get('yes_bid'),
            'yes_ask': m.get('yes_ask_dollars') or m.get('yes_ask'),
        }
        for t, m in keyword_markets.items()
    },
    'summary': {
        'series_found': sorted(found_series.keys()),
        'total_today_markets': sum(len(v['markets']) for v in found_series.values()),
    }
}
with open('data/kalshi_series_discovery.json', 'w') as f:
    json.dump(output, f, indent=2)

print(f"\n=== FINAL SUMMARY ===")
print(f"Series with today's markets: {sorted(found_series.keys())}")
for s, v in sorted(found_series.items()):
    print(f"\n{s} ({len(v['markets'])} market tickers):")
    for t in v['markets']:
        print(f"  {t}")
print(f"\nWritten: data/kalshi_series_discovery.json")
