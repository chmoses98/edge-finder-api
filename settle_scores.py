import json
from urllib.request import urlopen, Request

def get(url):
    req = Request(url, headers={'Accept':'application/json','User-Agent':'Mozilla/5.0'})
    with urlopen(req, timeout=20) as r:
        return json.loads(r.read())

d = get("https://statsapi.mlb.com/api/v1/schedule?sportId=1&date=2026-06-03&hydrate=linescore,boxscore")
for entry in d.get('dates',[]):
    for g in entry.get('games',[]):
        away = g['teams']['away']['team']['name']
        home = g['teams']['home']['team']['name']
        away_score = g['teams']['away'].get('score')
        home_score = g['teams']['home'].get('score')
        innings = g.get('linescore',{}).get('innings',[])
        inning_scores = []
        for i,inn in enumerate(innings,1):
            ar = inn.get('away',{}).get('runs','x')
            hr = inn.get('home',{}).get('runs','x')
            inning_scores.append(f"I{i}:{ar}-{hr}")
        print(f"{away}({away_score}) @ {home}({home_score}) | {' '.join(inning_scores[:9])}")
