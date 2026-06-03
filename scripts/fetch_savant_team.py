"""
scripts/fetch_savant_team.py — v1.0

Fetches Savant team batting data (wOBA, FB%) and individual batter wOBA.
Runs in GitHub Actions — no Vercel timeout constraint.

Output: data/savant_team.json
  teams:   { abbr: { xwoba, fbPct, bbPct, kPct, hardHit, barrel } }
  batters: { player_id: xwoba }
"""

import json
import time
import urllib.request
from datetime import datetime

SEASON = '2026'

def fetch_csv(url, timeout=25):
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode('utf-8')
    except Exception as e:
        print(f'  fetch_csv error: {e}')
        return None

def parse_csv(text):
    if not text:
        return []
    lines = text.strip().split('\n')
    if len(lines) < 2:
        return []
    def split_line(line):
        result, current, in_quotes = [], '', False
        for ch in line:
            if ch == '"': in_quotes = not in_quotes
            elif ch == ',' and not in_quotes:
                result.append(current.strip()); current = ''
            else:
                current += ch
        result.append(current.strip())
        return result
    headers = split_line(lines[0])
    rows = []
    for line in lines[1:]:
        if not line.strip():
            continue
        values = split_line(line)
        rows.append({headers[i]: values[i] if i < len(values) else '' for i in range(len(headers))})
    return rows

def pf(val):
    try:
        n = float(val)
        return None if (n != n) else n
    except (TypeError, ValueError):
        return None

# Savant abbreviation -> MLB standard abbreviation mapping
SAVANT_TO_ABBR = {
    'ARI': 'ARI', 'ATL': 'ATL', 'BAL': 'BAL', 'BOS': 'BOS',
    'CHC': 'CHC', 'CWS': 'CWS', 'CIN': 'CIN', 'CLE': 'CLE',
    'COL': 'COL', 'DET': 'DET', 'HOU': 'HOU', 'KC':  'KC',
    'LAA': 'LAA', 'LAD': 'LAD', 'MIA': 'MIA', 'MIL': 'MIL',
    'MIN': 'MIN', 'NYM': 'NYM', 'NYY': 'NYY', 'OAK': 'ATH',
    'ATH': 'ATH', 'PHI': 'PHI', 'PIT': 'PIT', 'STL': 'STL',
    'SD':  'SD',  'SF':  'SF',  'SEA': 'SEA', 'TB':  'TB',
    'TEX': 'TEX', 'TOR': 'TOR', 'WSH': 'WSH', 'AZ':  'ARI',
}

def fetch_team_batting():
    """
    Fetch team-level batting stats from Savant individual leaderboard,
    then aggregate by team. groupBy=team is unreliable — aggregate manually.
    """
    print('Fetching Savant individual batter leaderboard (team aggregation)...')
    url = (f'https://baseballsavant.mlb.com/leaderboard/custom?year={SEASON}&type=batter'
           f'&filter=&min=10'
           f'&selections=xwoba,bb_percent,k_percent,hard_hit_percent,barrel_batted_rate,fb_percent'
           f'&chart=false&x=xwoba&y=xwoba&r=no&chartType=beeswarm&csv=true')
    text = fetch_csv(url)
    rows = parse_csv(text)
    print(f'  Got {len(rows)} batter rows')

    # Aggregate by team
    team_buckets = {}  # abbr -> lists of values
    batter_woba  = {}  # player_id -> xwoba

    for row in rows:
        pid   = row.get('player_id', '').strip()
        team  = row.get('team_name', '').strip().upper()
        abbr  = SAVANT_TO_ABBR.get(team, team)

        xwoba   = pf(row.get('xwoba'))
        fbpct   = pf(row.get('fb_percent'))
        bbpct   = pf(row.get('bb_percent'))
        kpct    = pf(row.get('k_percent'))
        hardhit = pf(row.get('hard_hit_percent'))
        barrel  = pf(row.get('barrel_batted_rate'))

        # Individual batter wOBA map
        if pid and xwoba is not None:
            batter_woba[pid] = xwoba

        # Team aggregation
        if not abbr:
            continue
        if abbr not in team_buckets:
            team_buckets[abbr] = {'xwoba': [], 'fbPct': [], 'bbPct': [],
                                  'kPct': [], 'hardHit': [], 'barrel': []}
        b = team_buckets[abbr]
        if xwoba   is not None: b['xwoba'].append(xwoba)
        if fbpct   is not None: b['fbPct'].append(fbpct)
        if bbpct   is not None: b['bbPct'].append(bbpct)
        if kpct    is not None: b['kPct'].append(kpct)
        if hardhit is not None: b['hardHit'].append(hardhit)
        if barrel  is not None: b['barrel'].append(barrel)

    # Average each team bucket
    teams = {}
    for abbr, b in team_buckets.items():
        avg = lambda lst: round(sum(lst)/len(lst), 3) if lst else None
        teams[abbr] = {
            'xwoba':   avg(b['xwoba']),
            'fbPct':   avg(b['fbPct']),
            'bbPct':   avg(b['bbPct']),
            'kPct':    avg(b['kPct']),
            'hardHit': avg(b['hardHit']),
            'barrel':  avg(b['barrel']),
        }

    print(f'  Aggregated {len(teams)} teams | {len(batter_woba)} individual batter wOBAs')
    return teams, batter_woba

def main():
    start = time.time()

    teams, batter_woba = fetch_team_batting()

    # Sample output
    sample = list(teams.items())[:3]
    for abbr, d in sample:
        print(f'  {abbr}: xwoba={d["xwoba"]} fbPct={d["fbPct"]}')

    output = {
        'ok':          True,
        'season':      SEASON,
        'fetchedAt':   datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
        'teamCount':   len(teams),
        'batterCount': len(batter_woba),
        'teams':       teams,
        'batters':     batter_woba,
    }

    with open('data/savant_team.json', 'w') as f:
        json.dump(output, f)

    elapsed = round(time.time() - start, 1)
    print(f'Done in {elapsed}s → data/savant_team.json')

if __name__ == '__main__':
    main()
