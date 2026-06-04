#!/usr/bin/env python3
"""Fetch scores for June 2-3 2026 from MLB Stats API for bet settlement."""
import json
from urllib.request import urlopen, Request

def get(url):
    try:
        req = Request(url, headers={'Accept':'application/json','User-Agent':'Mozilla/5.0'})
        with urlopen(req, timeout=20) as r:
            return json.loads(r.read())
    except Exception as e:
        print(f"ERROR: {e}")
        return None

results = {}
for date in ['2026-06-02', '2026-06-03']:
    d = get(f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={date}&hydrate=linescore")
    if not d: continue
    for entry in d.get('dates',[]):
        for g in entry.get('games',[]):
            away = g['teams']['away']['team']['name']
            home = g['teams']['home']['team']['name']
            innings = g.get('linescore',{}).get('innings',[])
            final_away = g['teams']['away'].get('score')
            final_home = g['teams']['home'].get('score')
            f5_away = sum(i.get('away',{}).get('runs',0) or 0 for i in innings[:5])
            f5_home = sum(i.get('home',{}).get('runs',0) or 0 for i in innings[:5])
            key = f"{away} @ {home}"
            results[key] = {
                'date': date,
                'away': away, 'home': home,
                'final_away': final_away, 'final_home': final_home,
                'f5_away': f5_away, 'f5_home': f5_home,
                'total': (final_away or 0) + (final_home or 0)
            }
            print(f"{date} | {away} @ {home}: F5 {f5_away}-{f5_home} | Final {final_away}-{final_home} | Total={(final_away or 0)+(final_home or 0)}")

with open('scores_june2_3.json','w') as f:
    json.dump(results, f, indent=2)
print("\nWritten scores_june2_3.json")
