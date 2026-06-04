import json
from urllib.request import urlopen, Request

def get(url):
    try:
        req = Request(url, headers={'Accept':'application/json','User-Agent':'Mozilla/5.0'})
        with urlopen(req, timeout=20) as r:
            return json.loads(r.read())
    except Exception as e:
        print(f"ERROR: {e}"); return None

d = get("https://statsapi.mlb.com/api/v1/schedule?sportId=1&date=2026-06-02&hydrate=linescore")
for entry in (d or {}).get('dates',[]):
    for g in entry.get('games',[]):
        away = g['teams']['away']['team']['name']
        home = g['teams']['home']['team']['name']
        innings = g.get('linescore',{}).get('innings',[])
        fa = g['teams']['away'].get('score'); fh = g['teams']['home'].get('score')
        f5a = sum(i.get('away',{}).get('runs',0) or 0 for i in innings[:5])
        f5h = sum(i.get('home',{}).get('runs',0) or 0 for i in innings[:5])
        print(f"2026-06-02 | {away} @ {home}: F5 {f5a}-{f5h} | Final {fa}-{fh} | Total={(fa or 0)+(fh or 0)}")
        if 'Kansas City' in away or 'Cincinnati' in home:
            with open('kc_cin_score.json','w') as f:
                json.dump({'f5_away':f5a,'f5_home':f5h,'final_away':fa,'final_home':fh},f)
            print("Written kc_cin_score.json")
