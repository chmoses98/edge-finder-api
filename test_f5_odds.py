#!/usr/bin/env python3
"""
Test what F5 market data The Odds API returns for June 3 games.
Runs in GitHub Actions with full internet access.
"""
import json, os, sys, time
from datetime import datetime, timedelta
from urllib.request import urlopen, Request
from urllib.error import HTTPError

ODDS_API_KEY = os.environ.get('ODDS_API_KEY', '')
BASE_URL = 'https://api.the-odds-api.com/v4'
SPORT = 'baseball_mlb'
DATE = '2026-06-03'

def get(url):
    try:
        req = Request(url, headers={'Accept': 'application/json'})
        with urlopen(req, timeout=25) as r:
            remaining = r.headers.get('x-requests-remaining', '?')
            return json.loads(r.read()), remaining
    except HTTPError as e:
        print(f"  HTTP {e.code}: {e.read().decode()[:200]}")
        return None, None
    except Exception as e:
        print(f"  ERROR: {e}")
        return None, None

print(f"=== F5 Odds API test for {DATE} ===\n")
print(f"API key present: {'yes' if ODDS_API_KEY else 'NO - missing'}\n")

next_day = '2026-06-04'
snapshot = f'{next_day}T02:00:00Z'

# Step 1: Get event list
print("1. Fetching event list...")
url = (f"{BASE_URL}/historical/sports/{SPORT}/events"
       f"?apiKey={ODDS_API_KEY}"
       f"&commenceTimeFrom={DATE}T00:00:00Z"
       f"&commenceTimeTo={next_day}T06:00:00Z"
       f"&date={snapshot}")
data, remaining = get(url)
if not data:
    print("FAILED to get events")
    sys.exit(1)

events = data.get('data', []) if isinstance(data, dict) else data
print(f"  {len(events)} events | credits_remaining={remaining}")
for e in events:
    print(f"    {e.get('id','')[:12]} {e.get('away_team','')} @ {e.get('home_team','')}")

# Step 2: For a few games, test what markets are available with different bookmaker combos
test_games = events[:4]  # test first 4 games
market_keys = 'h2h_1st_5_innings,spreads_1st_5_innings,h2h_1st_1_innings'

print(f"\n2. Testing F5 market availability for {len(test_games)} games...")
print(f"   Markets: {market_keys}\n")

for e in test_games:
    eid = e['id']
    away = e.get('away_team','?')
    home = e.get('home_team','?')
    print(f"  {away} @ {home}:")
    
    # Test different region/bookmaker combos
    combos = [
        ('us_ex', 'kalshi'),
        ('us', 'pinnacle'),
        ('us', 'fanduel'),
        ('us', 'draftkings'),
        ('us,us_ex', ''),  # all books
    ]
    
    for regions, books in combos:
        time.sleep(0.3)
        bk_param = f'&bookmakers={books}' if books else ''
        url = (f"{BASE_URL}/historical/sports/{SPORT}/events/{eid}/odds"
               f"?apiKey={ODDS_API_KEY}&regions={regions}{bk_param}"
               f"&markets={market_keys}&oddsFormat=american&date={snapshot}")
        d, rem = get(url)
        if d:
            gd = d.get('data') if isinstance(d, dict) else d
            bks = gd.get('bookmakers', []) if gd else []
            found = []
            for bk in bks:
                mkt_keys = [m['key'] for m in bk.get('markets', [])]
                f5_mkts = [k for k in mkt_keys if 'f5' in k.lower() or '5_innings' in k or '1_innings' in k]
                if f5_mkts:
                    found.append(f"{bk['key']}:{f5_mkts}")
            label = f"{regions}+{books}" if books else f"{regions}(all)"
            print(f"    [{label}] credits={rem} | F5 found: {found if found else 'NONE'}")
        else:
            print(f"    [{regions}+{books}] FAILED")

print("\nDone.")
