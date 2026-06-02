import json

try:
    with open('data/kalshi_raw.json') as f:
        d = json.load(f)
except:
    print('kalshi_raw.json not parseable')
    exit(0)

if 'error' in d:
    print('Kalshi error:', d['error'])
    exit(0)

print(f'todayGames: {d.get("todayGames",0)} | totalMarketsOpen: {d.get("totalMarketsOpen",0)}')
games = d.get('games', [])
for g in games[:5]:
    print(f'  {g.get("awayTeam")}@{g.get("homeTeam")} {g.get("gameTime")} | mid={g.get("mid")} american={g.get("americanOdds")} ticker={g.get("ticker","")}')

try:
    with open('data/kalshi_search.json') as f:
        s = json.load(f)
    results = s.get('results', s.get('markets', []))
    print(f'Kalshi search results: {len(results)}')
    for r in results[:5]:
        print(f'  {r.get("ticker","")[:40]} | {r.get("title","")[:50]}')
except:
    print('kalshi_search.json not parseable')
