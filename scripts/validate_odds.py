import json, sys, urllib.request

with open('data/odds.json') as f:
    d = json.load(f)

if 'error' in d:
    print(f'WARNING: Odds API error: {d["error"]}')
    sys.exit(0)

games = d.get('games', [])
print(f'Odds OK: {len(games)} games | Credits remaining: {d.get("creditsRemaining","?")}')

# Check Kalshi markets for first game via event markets endpoint
if games and len(sys.argv) > 1:
    # API key passed as arg from action
    pass

# Show summary of what Kalshi has
kalshi_ml = kalshi_rl = kalshi_tot = kalshi_f5 = kalshi_tt = kalshi_nrfi = 0
for g in games:
    kal = g.get('books', {}).get('kalshi', {})
    if kal.get('ml', {}).get('away'): kalshi_ml += 1
    if kal.get('rl', {}).get('away'): kalshi_rl += 1
    if kal.get('total', {}).get('line'): kalshi_tot += 1
    if kal.get('f5ml', {}).get('away'): kalshi_f5 += 1
    if kal.get('teamTotals', {}).get('away', {}).get('line'): kalshi_tt += 1
    if kal.get('nrfi', {}).get('nrfi'): kalshi_nrfi += 1

print(f'Kalshi coverage ({len(games)} games):')
print(f'  ML: {kalshi_ml} | RL: {kalshi_rl} | Total: {kalshi_tot} | F5: {kalshi_f5} | TT: {kalshi_tt} | NRFI: {kalshi_nrfi}')

# Sample first game
if games:
    g = games[0]
    pvf = g.get('pinnacleVF') or {}
    kvf = g.get('kalshiVF') or {}
    books = g.get('books', {})
    pin_ml = books.get('pinnacle', {}).get('ml', {})
    kal_ml = books.get('kalshi', {}).get('ml', {})
    print(f'Sample: {g["awayTeam"]} @ {g["homeTeam"]}')
    print(f'  Pinnacle: {pin_ml.get("away")}/{pin_ml.get("home")} VF={pvf.get("away")}/{pvf.get("home")} ({pvf.get("source")} avail={pvf.get("available")})')
    print(f'  Kalshi:   {kal_ml.get("away")}/{kal_ml.get("home")} VF={kvf.get("away")}/{kvf.get("home")}')
