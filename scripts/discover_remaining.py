#!/usr/bin/env python3
"""
discover_remaining.py
Finds RL (run line/spread), Team Total, NRFI, YRFI series.
Confirmed so far: KXMLBGAME (ML), KXMLBF5 (F5 ML), KXMLBTOTAL (Game Total)
Still needed: spread/RL, team total, NRFI, YRFI
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
        return None, f"HTTP{e.code}"
    except Exception as e:
        return None, str(e)[:60]

# ── 1. Series list endpoint ─────────────────────────────────────────────────
print("=== Series list endpoint ===")
data, err = get(f"{KALSHI_BASE}/series?limit=200")
if data:
    all_series = data.get('series') or []
    print(f"Total series: {len(all_series)}")
    for s in all_series:
        t = s.get('ticker','')
        title = s.get('title','')
        if any(kw in t.upper() for kw in ['MLB','BASEBALL','NRFI','YRFI','KX']):
            print(f"  {t:35s} | {title}")
else:
    print(f"  Series list: {err}")

# ── 2. Extended series candidates ────────────────────────────────────────────
print("\n=== Extended series candidates ===")
EXTENDED = [
    # Run line / spread — many naming possibilities
    'KXMLBRL',       'KXMLBSP',      'KXMLBSPREAD',
    'KXMLBRUNLINE',  'KXMLBCOVER',   'KXMLBLINE',
    'KXMLBWIN2',     'KXMLBWIN15',   'KXMLBWIN25',
    'KXMLBGAMRL',    'KXMLBGAMERL',  'KXMLBF5RL',
    'KXMLB15',       'KXMLB25',
    # Team totals
    'KXMLBTT',       'KXMLBTEAM',    'KXMLBTEAMTOT',
    'KXMLBTTR',      'KXMLBHTT',     'KXMLBATT',
    'KXMLBHOME',     'KXMLBAWAY',    'KXMLBSCORE',
    'KXMLBSCORES',   'KXMLBSCORET',  'KXMLBRUNT',
    'KXMLBTEAMRUN',  'KXMLBRUNS',
    # NRFI
    'KXMLBNRFI',     'KXMLBNR',      'KXNRFI',
    'KXMLBI1NR',     'KXMLB1NR',     'KXMLB1STNR',
    'KXMLBINNING1',  'KXMLBINN1',
    # YRFI
    'KXMLBYRFI',     'KXMLBYR',      'KXYRFI',
    'KXMLBI1YR',     'KXMLB1YR',
    # First inning generic
    'KXMLBFI',       'KXMLB1ST',     'KXMLB1INN',
    'KXMLBI1',       'KXMLBF1INN',   'KXMLB1STIN',
    'KXMLBFIRST',    'KXMLB1INNINGS',
    # F5 variants
    'KXMLBF5T',      'KXMLBF5TOT',   'KXMLBF5TOTAL',
    'KXMLBF5TT',
]

found = {}
for series in EXTENDED:
    url = f"{KALSHI_BASE}/markets?series_ticker={series}&status=open&limit=50"
    data, err = get(url)
    if data:
        mkts = data.get('markets') or []
        today = [m for m in mkts if KALSHI_DATE in (m.get('event_ticker','') or '')]
        if today:
            found[series] = today
            print(f"\n✅ {series}: {len(today)} markets today")
            # Show first 3 to understand structure
            for m in today[:3]:
                print(f"  {m['ticker']} | {m.get('title','')} | bid={m.get('yes_bid_dollars')} ask={m.get('yes_ask_dollars')}")
        elif mkts:
            # Has markets but not today — still interesting
            sample = mkts[0]
            print(f"  {series}: {len(mkts)} markets (not today) | sample: {sample.get('event_ticker','')}")

# ── 3. Pull all open markets with pagination — scan for MLB ─────────────────
# This is the nuclear option: paginate through ALL open markets and look
# for any that reference today's teams
print("\n=== Paginating ALL open markets (looking for today's teams) ===")
TEAM_SIGNALS = [
    'TORATL', 'KCMIN', 'ATHCHC', 'PITHOU', 'LADAZ', 'SFMIL',
    '1915TOR', '1940KC', '2005ATH', '2010PIT', '2140LAD', '1410SF',
    KALSHI_DATE
]

all_open = []
cursor = ''
page = 0
found_non_standard = {}
while page < 30:  # cap at 30 pages
    url = f"{KALSHI_BASE}/markets?status=open&limit=200"
    if cursor:
        url += f"&cursor={cursor}"
    data, err = get(url)
    if not data:
        print(f"  Page {page}: {err}")
        break
    mkts = data.get('markets') or []
    cursor = data.get('cursor','')
    
    # Check each market for today's game signals
    for m in mkts:
        et = m.get('event_ticker','')
        t  = m.get('ticker','')
        if any(sig in et or sig in t for sig in TEAM_SIGNALS):
            if t not in found_non_standard:
                found_non_standard[t] = m
                series = t.split('-')[0] if '-' in t else 'UNKNOWN'
                if series not in ('KXMLBGAME','KXMLBF5','KXMLBTOTAL'):
                    print(f"  NEW [{series}] {t} | {m.get('title','')} | bid={m.get('yes_bid_dollars')} ask={m.get('yes_ask_dollars')}")
    
    print(f"  Page {page}: {len(mkts)} markets | cursor={'yes' if cursor else 'end'} | running total today={len(found_non_standard)}")
    if not cursor or not mkts:
        break
    page += 1

# Write output
os.makedirs('data', exist_ok=True)
result = {
    'date': DATE,
    'kalshi_date': KALSHI_DATE,
    'fetched_at': datetime.now(tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
    'new_series_found': {
        s: [{'ticker': m['ticker'], 'title': m.get('title',''),
             'event_ticker': m.get('event_ticker',''),
             'yes_bid': m.get('yes_bid_dollars'),
             'yes_ask': m.get('yes_ask_dollars')}
            for m in mkts]
        for s, mkts in found.items()
    },
    'all_today_markets_from_open_scan': {
        t: {'title': m.get('title',''), 'event_ticker': m.get('event_ticker','')}
        for t, m in found_non_standard.items()
    }
}
with open('data/kalshi_remaining_discovery.json','w') as f:
    json.dump(result, f, indent=2)

print(f"\n=== SUMMARY ===")
print(f"New series found: {list(found.keys())}")
print(f"Total today markets in open scan: {len(found_non_standard)}")
print("Written: data/kalshi_remaining_discovery.json")
