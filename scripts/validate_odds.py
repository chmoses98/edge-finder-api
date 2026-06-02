import json, sys
try:
    with open('data/odds.json') as f:
        d = json.load(f)
except Exception as e:
    print(f'WARNING: Could not parse odds.json: {e}')
    sys.exit(0)  # don't fail the build — let merge step handle it

if 'error' in d:
    print(f'WARNING: Odds API returned error: {d["error"]}')
    print('Check that ODDS_API_KEY is set in Vercel environment variables')
    sys.exit(0)  # warn but continue

games = d.get('games', [])
print(f'Odds OK: {len(games)} games | Credits remaining: {d.get("creditsRemaining","?")}')
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
