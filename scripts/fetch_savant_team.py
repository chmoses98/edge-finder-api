"""
scripts/fetch_savant_team.py — v4.0
Changes from v2:
  - Team batting (wOBA, fbPct) now fetched via Vercel api/enrich?type=batting endpoint
    instead of hitting Savant directly (blocked from GitHub Actions)
  - Pitcher fbPct still fetched from Savant pitcher leaderboard via Vercel savant.js
    (already works through Vercel)
  - Individual batter wOBA comes from the savant_batting endpoint response

Output: data/savant_team.json
"""

import json
import time
import urllib.request
from datetime import datetime

VERCEL_BASE = 'https://edge-finder-api.vercel.app'
SEASON      = '2026'

def fetch_json(url, timeout=30):
    try:
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0',
            'Accept': 'application/json',
            'Cache-Control': 'no-cache',
        })
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception as e:
        print(f'  fetch error: {e}')
        return None

def fetch_csv(url, timeout=25):
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode('utf-8')
    except Exception as e:
        print(f'  CSV fetch error: {e}')
        return None

def parse_csv(text):
    if not text: return [], []
    lines = text.strip().split('\n')
    if len(lines) < 2: return [], []
    def split_line(line):
        result, current, in_quotes = [], '', False
        for ch in line:
            if ch == '"': in_quotes = not in_quotes
            elif ch == ',' and not in_quotes:
                result.append(current.strip()); current = ''
            else: current += ch
        result.append(current.strip())
        return result
    headers = split_line(lines[0])
    rows = []
    for line in lines[1:]:
        if not line.strip(): continue
        values = split_line(line)
        rows.append({headers[i]: values[i] if i < len(values) else '' for i in range(len(headers))})
    return headers, rows

def pf(val):
    try:
        n = float(val)
        return None if (n != n) else n
    except (TypeError, ValueError): return None

def main():
    start = time.time()

    # 1. Fetch team batting + individual batter wOBA from Vercel savant_batting endpoint
    print('Fetching team batting from Vercel api/savant_batting...')
    batting_data = fetch_json(f'{VERCEL_BASE}/api/enrich?type=batting&year={SEASON}', timeout=55)

    teams   = {}
    batters = {}

    if batting_data and batting_data.get('ok'):
        teams   = batting_data.get('teams', {})
        batters = batting_data.get('batters', {})
        print(f'  Teams: {len(teams)} | Batters: {len(batters)}')
        if batting_data.get('csvHeaders'):
            print(f'  CSV headers (first 8): {batting_data["csvHeaders"][:8]}')
    else:
        print(f'  savant_batting failed: {batting_data}')

    # 2. Fetch pitcher fbPct from Vercel savant.js leaderboard (no playerIds = full leaderboard)
    print('Fetching pitcher fbPct from Vercel api/savant (leaderboard)...')
    pitcher_data = fetch_json(f'{VERCEL_BASE}/api/savant?year={SEASON}', timeout=55)

    pitchers = {}
    if pitcher_data and pitcher_data.get('ok'):
        raw_pitchers = pitcher_data.get('pitchers', {})
        for pid, p in raw_pitchers.items():
            fb = p.get('fbPct')
            xe = p.get('xERA') or p.get('xera')
            kp = p.get('kPct')
            bp = p.get('bbPct')
            if any(v is not None for v in [fb, xe, kp]):
                pitchers[pid] = {'fbPct': fb, 'xera': xe, 'kPct': kp, 'bbPct': bp}
        print(f'  Pitchers with data: {len(pitchers)} | fbPct populated: {sum(1 for p in pitchers.values() if p.get("fbPct") is not None)}')
    else:
        print(f'  savant pitcher fetch failed: {pitcher_data}')

    output = {
        'ok':           True,
        'season':       SEASON,
        'fetchedAt':    datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
        'teamCount':    len(teams),
        'batterCount':  len(batters),
        'pitcherCount': len(pitchers),
        'teams':        teams,
        'batters':      batters,
        'pitchers':     pitchers,
    }

    with open('data/savant_team.json', 'w') as f:
        json.dump(output, f)

    elapsed = round(time.time() - start, 1)
    print(f'Done in {elapsed}s -> data/savant_team.json')
    if teams:
        sample = list(teams.items())[:2]
        for abbr, d in sample:
            print(f'  {abbr}: xwoba={d.get("xwoba")} fbPct={d.get("fbPct")}')

if __name__ == '__main__':
    main()
