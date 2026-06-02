import json, sys

try:
    with open('data/odds.json') as f:
        d = json.load(f)
except Exception as e:
    print(f'WARNING: Could not parse odds.json: {e}')
    sys.exit(0)

if 'error' in d:
    print(f'WARNING: Odds API error: {d["error"]}')
    sys.exit(0)

games = d.get('games', [])
kalshi_keys = d.get('kalshiNativeKeys', [])

print(f'Odds OK: {len(games)} games | Credits: {d.get("creditsRemaining","?")}')
print(f'Kalshi native game keys: {kalshi_keys[:10]}')

ml=rl=tot=f5=tt=nrfi=matched=0
for g in games:
    kal = g.get('books',{}).get('kalshi',{})
    if g.get('kalshiMatched'): matched+=1
    if kal.get('ml',{}).get('away'): ml+=1
    if kal.get('rl',{}).get('away'): rl+=1
    if kal.get('total',{}).get('line'): tot+=1
    if kal.get('f5ml',{}).get('away'): f5+=1
    if kal.get('teamTotals',{}).get('away',{}).get('over'): tt+=1
    if kal.get('nrfi',{}).get('nrfi'): nrfi+=1

n = len(games)
print(f'Kalshi: matched={matched} ML={ml} RL={rl} Total={tot} F5={f5} TT={tt} NRFI={nrfi} (of {n} games)')
