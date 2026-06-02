import json, sys
with open('data/odds.json') as f:
    d = json.load(f)
if 'error' in d:
    print('ERROR:', d['error'])
    sys.exit(1)
games = d.get('games', [])
print(f'Games: {len(games)} | Credits remaining: {d.get("creditsRemaining","?")}')
if games:
    g = games[0]
    pvf = g.get('pinnacleVF') or {}
    kvf = g.get('kalshiVF') or {}
    books = g.get('books', {})
    pin_ml = books.get('pinnacle', {}).get('ml', {})
    kal_ml = books.get('kalshi', {}).get('ml', {})
    kal_f5 = books.get('kalshi', {}).get('f5ml', {})
    kal_nrfi = books.get('kalshi', {}).get('nrfi', {})
    print(f'Sample: {g["awayTeam"]} @ {g["homeTeam"]}')
    print(f'  Pinnacle ML: {pin_ml.get("away")}/{pin_ml.get("home")} | VF: {pvf.get("away")}/{pvf.get("home")} source={pvf.get("source","?")} available={pvf.get("available")}')
    print(f'  Kalshi ML:   {kal_ml.get("away")}/{kal_ml.get("home")} | VF: {kvf.get("away")}/{kvf.get("home")}')
    print(f'  Kalshi F5:   away={kal_f5.get("away","N/A")} home={kal_f5.get("home","N/A")}')
    print(f'  Kalshi NRFI: nrfi={kal_nrfi.get("nrfi","N/A")} yrfi={kal_nrfi.get("yrfi","N/A")}')
