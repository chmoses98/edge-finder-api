"""
scripts/fetch_savant_team.py — v2.0
Changes from v1:
  - Fixed team column name lookup (tries 'player_team', 'team', 'team_name' etc.)
  - Added pitcher fbPct fetch from pitcher leaderboard
  - More robust CSV column detection

Output: data/savant_team.json
  teams:   { abbr: { xwoba, fbPct, bbPct, kPct, hardHit, barrel } }
  batters: { player_id: xwoba }
  pitchers: { player_id: { fbPct, xera, kPct } }
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
        print(f'  fetch_csv error for {url[:60]}: {e}')
        return None

def parse_csv(text):
    if not text:
        return [], []
    lines = text.strip().split('\n')
    if len(lines) < 2:
        return [], []
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
    except (TypeError, ValueError):
        return None

# Savant abbreviation -> MLB standard abbreviation
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

def get_team(row, headers):
    """Try multiple possible team column names from Savant CSV."""
    for col in ['player_team', 'team_name', 'team', 'team_abbrev', 'Team']:
        val = row.get(col, '').strip().upper()
        if val:
            return SAVANT_TO_ABBR.get(val, val)
    return None

def fetch_batter_data():
    print('Fetching Savant batter leaderboard (xwoba, fb_percent)...')
    url = (f'https://baseballsavant.mlb.com/leaderboard/custom?year={SEASON}&type=batter'
           f'&filter=&min=10'
           f'&selections=xwoba,bb_percent,k_percent,hard_hit_percent,barrel_batted_rate,fb_percent'
           f'&chart=false&x=xwoba&y=xwoba&r=no&chartType=beeswarm&csv=true')
    text = fetch_csv(url)
    headers, rows = parse_csv(text)
    print(f'  Headers: {headers[:8]}')
    print(f'  Rows: {len(rows)}')

    team_buckets = {}
    batter_woba  = {}

    for row in rows:
        pid  = row.get('player_id', '').strip()
        team = get_team(row, headers)
        xwoba   = pf(row.get('xwoba'))
        fbpct   = pf(row.get('fb_percent'))
        bbpct   = pf(row.get('bb_percent'))
        kpct    = pf(row.get('k_percent'))
        hardhit = pf(row.get('hard_hit_percent'))
        barrel  = pf(row.get('barrel_batted_rate'))

        if pid and xwoba is not None:
            batter_woba[pid] = xwoba

        if not team:
            continue
        if team not in team_buckets:
            team_buckets[team] = {'xwoba': [], 'fbPct': [], 'bbPct': [],
                                   'kPct': [], 'hardHit': [], 'barrel': []}
        b = team_buckets[team]
        if xwoba   is not None: b['xwoba'].append(xwoba)
        if fbpct   is not None: b['fbPct'].append(fbpct)
        if bbpct   is not None: b['bbPct'].append(bbpct)
        if kpct    is not None: b['kPct'].append(kpct)
        if hardhit is not None: b['hardHit'].append(hardhit)
        if barrel  is not None: b['barrel'].append(barrel)

    avg = lambda lst: round(sum(lst)/len(lst), 3) if lst else None
    teams = {abbr: {
        'xwoba':   avg(b['xwoba']),
        'fbPct':   avg(b['fbPct']),
        'bbPct':   avg(b['bbPct']),
        'kPct':    avg(b['kPct']),
        'hardHit': avg(b['hardHit']),
        'barrel':  avg(b['barrel']),
    } for abbr, b in team_buckets.items()}

    print(f'  Teams aggregated: {len(teams)} | Batters: {len(batter_woba)}')
    return teams, batter_woba

def fetch_pitcher_data():
    """Fetch pitcher fbPct from Savant pitcher leaderboard — for park factor modifier."""
    print('Fetching Savant pitcher leaderboard (fb_percent, xera)...')
    url = (f'https://baseballsavant.mlb.com/leaderboard/custom?year={SEASON}&type=pitcher'
           f'&filter=&min=1'
           f'&selections=k_percent,bb_percent,xera,hard_hit_percent,fb_percent'
           f'&chart=false&x=xera&y=xera&r=no&chartType=beeswarm&csv=true')
    text = fetch_csv(url)
    headers, rows = parse_csv(text)
    print(f'  Pitcher rows: {len(rows)}')

    pitchers = {}
    for row in rows:
        pid = row.get('player_id', '').strip()
        if not pid: continue
        pitchers[pid] = {
            'fbPct': pf(row.get('fb_percent')),
            'xera':  pf(row.get('xera')),
            'kPct':  pf(row.get('k_percent')),
            'bbPct': pf(row.get('bb_percent')),
        }
    print(f'  Pitchers with fbPct: {sum(1 for p in pitchers.values() if p["fbPct"] is not None)}')
    return pitchers

def main():
    start = time.time()
    teams, batter_woba = fetch_batter_data()
    time.sleep(1)
    pitchers = fetch_pitcher_data()

    if teams:
        sample = list(teams.items())[:2]
        for abbr, d in sample:
            print(f'  {abbr}: xwoba={d["xwoba"]} fbPct={d["fbPct"]}')
    else:
        print('  WARNING: No team data — team column not found in CSV')

    output = {
        'ok':          True,
        'season':      SEASON,
        'fetchedAt':   datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
        'teamCount':   len(teams),
        'batterCount': len(batter_woba),
        'pitcherCount': len(pitchers),
        'teams':       teams,
        'batters':     batter_woba,
        'pitchers':    pitchers,
    }

    with open('data/savant_team.json', 'w') as f:
        json.dump(output, f)

    elapsed = round(time.time() - start, 1)
    print(f'Done in {elapsed}s -> data/savant_team.json')

if __name__ == '__main__':
    main()
