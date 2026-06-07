#!/usr/bin/env python3
"""
discover_series_v2.py — targeted series discovery
Based on confirmed pattern: KXMLBGAME and KXMLBF5
Try logical extensions: KXMLBRL, KXMLBTOT, KXMLBTT, KXMLBNRFI, KXMLBYRFI
Plus: check the Kalshi series endpoint directly.
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
print(f"DATE={DATE} KALSHI_DATE={KALSHI_DATE}\n")

def get(url):
    try:
        req = Request(url, headers={'Accept':'application/json','User-Agent':'Mozilla/5.0'})
        with urlopen(req, timeout=15) as r:
            return json.loads(r.read()), None
    except HTTPError as e:
        body = e.read().decode('utf-8', errors='replace')[:100]
        return None, f"HTTP{e.code}:{body}"
    except Exception as e:
        return None, str(e)[:80]

# ── 1. Series endpoint — list all series, find MLB ───────────────────────────
print("=== 1. Kalshi series list endpoint ===")
for url in [
    f"{KALSHI_BASE}/series?limit=200",
    f"{KALSHI_BASE}/series?limit=200&category=sports",
]:
    data, err = get(url)
    if data:
        series_list = data.get('series') or []
        print(f"  {url} → {len(series_list)} series")
        # Print all — don't filter, we want to see what's there
        for s in series_list:
            ticker = s.get('ticker','')
            title  = s.get('title','')
            cat    = s.get('category','')
            print(f"    {ticker:30s} | {cat:15s} | {title}")
    else:
        print(f"  {url} → {err}")

# ── 2. Targeted series candidates based on KXMLBF5 pattern ──────────────────
print("\n=== 2. Targeted series candidates ===")
# Pattern: KX + MLB + {TYPE}
# Confirmed: KXMLBGAME (ML), KXMLBF5 (F5 ML with tie)
# Try the obvious next ones:
TARGETED = [
    'KXMLBRL',       # run line / spread
    'KXMLBSP',       # spread alt
    'KXMLBTOT',      # game total
    'KXMLBTOTAL',    # alt
    'KXMLBTT',       # team total
    'KXMLBNRFI',     # NRFI
    'KXMLBYRFI',     # YRFI
    'KXMLB1ST',      # first inning
    'KXMLBI1',       # inning 1
    'KXMLBF5RL',     # F5 run line
    'KXMLBF5T',      # F5 total
    'KXNRFI',        # NRFI without MLB
    'KXYRFI',        # YRFI without MLB
]

results = {}
for series in TARGETED:
    # Check markets endpoint (faster than events)
    url = f"{KALSHI_BASE}/markets?series_ticker={series}&status=open&limit=200"
    data, err = get(url)
    if data:
        mkts = data.get('markets') or []
        today = [m for m in mkts if KALSHI_DATE in (m.get('event_ticker','') or '')]
        if today:
            results[series] = today
            print(f"  ✅ {series}: {len(today)} today markets")
            for m in today:
                print(f"     {m['ticker']} | {m.get('title','')} | bid={m.get('yes_bid_dollars')} ask={m.get('yes_ask_dollars')}")
        elif mkts:
            print(f"  ℹ️  {series}: {len(mkts)} total markets but none today")
        else:
            print(f"  ✗  {series}: no markets")
    else:
        print(f"  ✗  {series}: {err}")

# ── 3. Direct event_ticker construction for today's known games ──────────────
# Known game suffixes from KXMLBGAME:
# 1410SFMIL, 1915TORATL, 1940KCMIN, 2005ATHCHC, 2010PITHOU, 2140LADAZ
print("\n=== 3. Direct event_ticker construction ===")
KNOWN_SUFFIXES = [
    '1410SFMIL', '1915TORATL', '1940KCMIN',
    '2005ATHCHC', '2010PITHOU', '2140LADAZ'
]
# Series to try directly (all plausible ones)
SERIES_TO_TRY = ['KXMLBRL','KXMLBTOT','KXMLBTT','KXMLBNRFI','KXMLBYRFI',
                  'KXMLBI1','KXNRFI','KXYRFI','KXMLBF5RL','KXMLBSP']

found_direct = {}
for suffix in KNOWN_SUFFIXES:
    for series in SERIES_TO_TRY:
        et = f"{series}-{KALSHI_DATE}{suffix}"
        url = f"{KALSHI_BASE}/events/{et}?with_nested_markets=true"
        data, err = get(url)
        if data and ('event' in data or 'ticker' in data):
            ev = data.get('event', data)
            mkts = ev.get('markets', [])
            print(f"  ✅ {et}: {len(mkts)} markets")
            for m in mkts:
                print(f"    {m.get('ticker','')} | {m.get('title','')} | bid={m.get('yes_bid_dollars')} ask={m.get('yes_ask_dollars')}")
            found_direct[et] = mkts
        # Also try markets endpoint for the event
        url2 = f"{KALSHI_BASE}/markets?event_ticker={et}&status=open&limit=50"
        data2, _ = get(url2)
        if data2:
            mkts2 = data2.get('markets') or []
            if mkts2:
                print(f"  ✅ {et} via /markets: {len(mkts2)}")
                for m in mkts2:
                    print(f"    {m.get('ticker','')} | {m.get('title','')} | bid={m.get('yes_bid_dollars')} ask={m.get('yes_ask_dollars')}")
                found_direct[et] = found_direct.get(et, []) + mkts2

# ── 4. Broad search with team names ─────────────────────────────────────────
print("\n=== 4. Broad open market search (team names + market type keywords) ===")
SEARCHES = [
    # Team names for today's games — catches any market referencing them
    'Toronto Atlanta', 'Kansas City Minnesota', 'Pittsburgh Houston',
    'Athletics Cubs', 'Los Angeles Arizona',
    # Market type keywords
    'runs over', 'total runs', 'run line', 'wins by',
    'no run first', 'score first', 'first inning',
    'strikeouts', 'innings winner',
]
found_search = {}
for term in SEARCHES:
    url = f"{KALSHI_BASE}/markets?status=open&limit=50&search={term.replace(' ','%20')}"
    data, err = get(url)
    if data:
        mkts = data.get('markets') or []
        today = [m for m in mkts if KALSHI_DATE in (m.get('event_ticker','') or '')]
        for m in today:
            t = m['ticker']
            if t not in found_search:
                found_search[t] = m
                print(f"  [{term}] {t} | {m.get('title','')} | event={m.get('event_ticker','')}")

# ── 5. Summary ────────────────────────────────────────────────────────────────
print("\n=== FINAL SUMMARY ===")
print(f"Series found with today markets:")
for s, mkts in results.items():
    print(f"  {s}: {len(mkts)} markets")
    for m in mkts:
        print(f"    {m['ticker']}")
print(f"\nDirect event construction hits: {len(found_direct)} events")
for et, mkts in found_direct.items():
    print(f"  {et}: {len(mkts)} markets")
print(f"\nSearch extras: {len(found_search)} unique tickers")
for t, m in found_search.items():
    print(f"  {t}: {m.get('title','')}")

# Write output
os.makedirs('data', exist_ok=True)
with open('data/kalshi_series_discovery.json','w') as f:
    json.dump({
        'date': DATE,
        'kalshi_date': KALSHI_DATE,
        'fetched_at': datetime.now(tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'targeted_series_results': {
            s: [{'ticker': m['ticker'], 'title': m.get('title',''),
                 'event_ticker': m.get('event_ticker',''),
                 'yes_bid': m.get('yes_bid_dollars'), 'yes_ask': m.get('yes_ask_dollars')}
                for m in mkts]
            for s, mkts in results.items()
        },
        'direct_event_hits': {
            et: [{'ticker': m.get('ticker',''), 'title': m.get('title','')} for m in mkts]
            for et, mkts in found_direct.items()
        },
        'search_hits': {
            t: {'title': m.get('title',''), 'event_ticker': m.get('event_ticker','')}
            for t, m in found_search.items()
        },
    }, f, indent=2)
print("\nWritten: data/kalshi_series_discovery.json")
