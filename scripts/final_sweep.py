#!/usr/bin/env python3
"""
final_sweep.py
Confirmed series: KXMLBGAME, KXMLBF5, KXMLBTOTAL, KXMLBSPREAD
Missing: Team Total, NRFI, YRFI
This script:
1. Fully enumerates KXMLBSPREAD markets
2. Searches for TT/NRFI/YRFI with aggressive naming
3. Pulls the /series endpoint with pagination
4. Tries KXMLBSPREAD event to see if nested markets include other types
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
print(f"DATE={DATE} KALSHI_DATE={KALSHI_DATE}\n")

def get(url):
    try:
        req = Request(url, headers={'Accept':'application/json','User-Agent':'Mozilla/5.0'})
        with urlopen(req, timeout=15) as r:
            return json.loads(r.read()), None
    except HTTPError as e:
        return None, f"HTTP{e.code}"
    except Exception as e:
        return None, str(e)[:80]

def paginate_markets(series_ticker):
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
    return all_mkts

# ── 1. Full KXMLBSPREAD enumeration ─────────────────────────────────────────
print("=== 1. KXMLBSPREAD full enumeration ===")
spread_mkts = paginate_markets('KXMLBSPREAD')
today_spread = [m for m in spread_mkts if KALSHI_DATE in (m.get('event_ticker','') or '')]
print(f"Total: {len(spread_mkts)} | Today: {len(today_spread)}")

# Group by event
spread_by_event = {}
for m in today_spread:
    et = m.get('event_ticker','')
    if et not in spread_by_event: spread_by_event[et] = []
    spread_by_event[et].append(m)

for et, mkts in sorted(spread_by_event.items()):
    print(f"\n  {et} ({len(mkts)} markets):")
    for m in sorted(mkts, key=lambda x: x['ticker']):
        print(f"    {m['ticker']}")
        print(f"    title: {m.get('title','')}")
        print(f"    bid={m.get('yes_bid_dollars')} ask={m.get('yes_ask_dollars')}")

# ── 2. Series list with full pagination ──────────────────────────────────────
print("\n=== 2. All Kalshi series ===")
all_series = []
cursor = ''
for page in range(10):
    url = f"{KALSHI_BASE}/series?limit=200"
    if cursor: url += f"&cursor={cursor}"
    data, err = get(url)
    if not data:
        print(f"  page {page}: {err}")
        break
    items = data.get('series') or []
    all_series.extend(items)
    cursor = data.get('cursor','')
    print(f"  page {page}: {len(items)} | cursor={'yes' if cursor else 'end'}")
    if not cursor or not items: break

print(f"\n  Total series: {len(all_series)}")
# Show ALL series — we want to see everything that might be MLB related
mlb_terms = ['MLB','BASEBALL','SPORT','NRFI','YRFI','INNING','GAME','TEAM','RUN']
for s in all_series:
    t = s.get('ticker','')
    title = s.get('title','')
    if any(kw in (t+title).upper() for kw in mlb_terms):
        print(f"  ★ {t:35s} | {s.get('category',''):15s} | {title}")
    else:
        print(f"    {t:35s} | {s.get('category',''):15s} | {title}")

# ── 3. More series candidates based on KXMLBSPREAD discovery ────────────────
print("\n=== 3. Extended candidates based on SPREAD pattern ===")
# KXMLBSPREAD → try: KXMLBTEAMTOTAL, KXMLBTT, KXMLBTEAMSCORE
# Also try variations of NRFI/YRFI
MORE_SERIES = [
    # Team totals — logical from KXMLBSPREAD pattern
    'KXMLBTEAMTOTAL', 'KXMLBTEAM', 'KXMLBTT', 'KXMLBTEAMSCORE',
    'KXMLBTEAMRUN', 'KXMLBRUNSTEAM', 'KXMLBSCORE',
    # NRFI/YRFI — try all combos
    'KXMLBNRFI', 'KXMLBYRFI', 'KXNRFI', 'KXYRFI',
    'KXMLB1INN', 'KXMLB1STIN', 'KXMLBFIRSTINN',
    'KXMLBINNING', 'KXMLBING1', 'KXMLBI1RUN',
    'KXMLB1STINNING', 'KXMLBRUN1',
    # F5 with additional market types
    'KXMLBF5TOTAL', 'KXMLBF5TT', 'KXMLBF5SPREAD', 'KXMLBF5RL',
    # More spread variants
    'KXMLBSPREAD15', 'KXMLBSP15',
]

found_more = {}
for series in MORE_SERIES:
    url = f"{KALSHI_BASE}/markets?series_ticker={series}&status=open&limit=50"
    data, err = get(url)
    if data:
        mkts = data.get('markets') or []
        today = [m for m in mkts if KALSHI_DATE in (m.get('event_ticker','') or '')]
        if today:
            found_more[series] = today
            print(f"\n✅ {series}: {len(today)} today markets")
            for m in today[:3]:
                print(f"  {m['ticker']} | {m.get('title','')} | bid={m.get('yes_bid_dollars')} ask={m.get('yes_ask_dollars')}")
        elif mkts:
            print(f"  ℹ️  {series}: exists ({len(mkts)} markets) but none today — sample: {mkts[0].get('event_ticker','')}")

# ── 4. Check KXMLBSPREAD events for nested markets of other types ─────────────
print("\n=== 4. Check KXMLBSPREAD event nested markets ===")
# Event format: KXMLBSPREAD-26JUN041915TORATL
known_suffixes = ['1915TORATL','1940KCMIN','2005ATHCHC','2010PITHOU','2140LADAZ']
for suffix in known_suffixes:
    et = f"KXMLBSPREAD-{KALSHI_DATE}{suffix}"
    url = f"{KALSHI_BASE}/events/{et}?with_nested_markets=true"
    data, err = get(url)
    if data:
        ev = data.get('event', data)
        mkts = ev.get('markets',[])
        print(f"  {et}: {len(mkts)} nested markets")
        for m in mkts[:3]:
            print(f"    {m.get('ticker','')} | {m.get('title','')}")
    else:
        print(f"  {et}: {err}")

# ── 5. Write output ───────────────────────────────────────────────────────────
os.makedirs('data', exist_ok=True)
output = {
    'date': DATE,
    'kalshi_date': KALSHI_DATE,
    'fetched_at': datetime.now(tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
    'kxmlbspread_today': {
        et: [{'ticker': m['ticker'], 'title': m.get('title',''),
              'yes_bid': m.get('yes_bid_dollars'), 'yes_ask': m.get('yes_ask_dollars')}
             for m in sorted(mkts, key=lambda x: x['ticker'])]
        for et, mkts in spread_by_event.items()
    },
    'new_series': {
        s: [{'ticker': m['ticker'], 'title': m.get('title','')} for m in mkts]
        for s, mkts in found_more.items()
    },
    'all_series_count': len(all_series),
    'all_series': [
        {'ticker': s.get('ticker',''), 'title': s.get('title',''), 'category': s.get('category','')}
        for s in all_series
    ],
}
with open('data/kalshi_final_sweep.json','w') as f:
    json.dump(output, f, indent=2)
print("\nWritten: data/kalshi_final_sweep.json")

print("\n=== SERIES STATUS ===")
print("✅ KXMLBGAME  — Moneyline (2 per game)")
print("✅ KXMLBF5    — F5 ML with tie (3 per game)")
print("✅ KXMLBTOTAL — Game Total, integer lines (many per game)")
print("✅ KXMLBSPREAD — Spread / run margin (many per game)")
print("?  Team Total — not yet found")
print("?  NRFI       — not yet found")
print("?  YRFI       — not yet found")
