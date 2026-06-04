#!/usr/bin/env python3
"""
Look up June 3 MLB box scores via MLB Stats API to get F5 scores.
The MLB Stats API is free and public.
"""
import json, sys
from urllib.request import urlopen, Request
from urllib.error import HTTPError

def get(url):
    try:
        req = Request(url, headers={'Accept':'application/json','User-Agent':'Mozilla/5.0'})
        with urlopen(req, timeout=20) as r:
            return json.loads(r.read())
    except Exception as e:
        print(f"  ERROR {url[:60]}: {e}")
        return None

# Step 1: Get schedule for June 3, 2026
print("Fetching MLB schedule for 2026-06-03...")
d = get("https://statsapi.mlb.com/api/v1/schedule?sportId=1&date=2026-06-03&hydrate=linescore")
if not d:
    print("FAILED")
    sys.exit(1)

games = []
for date_entry in d.get('dates', []):
    for g in date_entry.get('games', []):
        games.append(g)

print(f"Found {len(games)} games\n")

f5_scores = {}
for g in games:
    gid = g.get('gamePk')
    status = g.get('status', {}).get('abstractGameState', '')
    away = g.get('teams', {}).get('away', {}).get('team', {}).get('name', '?')
    home = g.get('teams', {}).get('home', {}).get('team', '').get('name', '?') if isinstance(g.get('teams', {}).get('home', {}).get('team', ''), dict) else g.get('teams', {}).get('home', {}).get('team', {}).get('name', '?')
    
    # Get linescore from hydrated data
    ls = g.get('linescore', {})
    innings = ls.get('innings', [])
    
    away_f5 = sum(i.get('away', {}).get('runs', 0) or 0 for i in innings[:5])
    home_f5 = sum(i.get('home', {}).get('runs', 0) or 0 for i in innings[:5])
    
    inning_count = len(innings)
    print(f"  gamePk={gid} | {away} @ {home} | status={status} | innings={inning_count} | F5: {away_f5}-{home_f5}")
    
    if inning_count >= 5:
        f5_scores[gid] = {
            'away': away,
            'home': home,
            'away_f5': away_f5,
            'home_f5': home_f5,
            'final_away': g.get('teams',{}).get('away',{}).get('score'),
            'final_home': g.get('teams',{}).get('home',{}).get('score'),
        }

print(f"\n=== F5 SCORES AVAILABLE: {len(f5_scores)} games ===")
for gid, s in f5_scores.items():
    print(f"  {s['away']} @ {s['home']}: F5 {s['away_f5']}-{s['home_f5']} | Final {s['final_away']}-{s['final_home']}")
