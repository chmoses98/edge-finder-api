"""
scripts/fetch_savant_bullpen_hl.py — v1.0

Fetches high-leverage bullpen xFIP splits from Savant for all 30 teams.
Runs in GitHub Actions (no Vercel timeout).
Merges hlXFIP into data/bullpen.json.

High-leverage = relief appearances with leverage index >= 1.5 (hfSit=high_lev).
"""

import json
import time
import urllib.request
from datetime import datetime

SEASON    = '2026'
FIP_CONST = 3.10

MLB_TEAM_ABBRS = [
    'LAA','ARI','BAL','BOS','CHC','CIN','CLE','COL','DET','HOU',
    'KC','LAD','WSH','NYM','ATH','PIT','SD','SEA','SF','STL',
    'TB','TEX','TOR','MIN','PHI','ATL','CWS','MIA','NYY','MIL',
]

# Savant team name -> abbr (for CSV team_name field)
SAVANT_TO_ABBR = {
    'ARI':'ARI','ATL':'ATL','BAL':'BAL','BOS':'BOS','CHC':'CHC',
    'CWS':'CWS','CIN':'CIN','CLE':'CLE','COL':'COL','DET':'DET',
    'HOU':'HOU','KC':'KC','LAA':'LAA','LAD':'LAD','MIA':'MIA',
    'MIL':'MIL','MIN':'MIN','NYM':'NYM','NYY':'NYY','OAK':'ATH',
    'ATH':'ATH','PHI':'PHI','PIT':'PIT','STL':'STL','SD':'SD',
    'SF':'SF','SEA':'SEA','TB':'TB','TEX':'TEX','TOR':'TOR',
    'WSH':'WSH','AZ':'ARI',
}

def fetch_csv(url, timeout=25):
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode('utf-8')
    except Exception as e:
        print(f'  fetch error: {e}')
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

def bullpen_grade(xfip):
    if xfip is None: return None
    if xfip < 3.50: return 'ELITE'
    if xfip < 4.00: return 'ABOVE_AVERAGE'
    if xfip < 4.50: return 'AVERAGE'
    if xfip < 5.00: return 'BELOW_AVERAGE'
    return 'VULNERABLE'

def fetch_hl_bullpen():
    """
    Fetch all high-leverage relief appearances, grouped by team.
    Uses hfSit=high_lev (LI >= 1.5) and hfRO=1 (relief only).
    """
    print('Fetching Savant high-leverage bullpen data...')
    url = (f'https://baseballsavant.mlb.com/statcast_search/csv?all=true'
           f'&hfSea={SEASON}%7C&player_type=pitcher&hfGT=R%7C'
           f'&hfSit=high_lev%7C'
           f'&hfRO=1%7C'
           f'&min_pitches=0&min_results=0&min_pas=0'
           f'&group_by=team&sort_col=pitches&sort_order=desc'
           f'&chk_stats_pa=on&chk_stats_so=on&chk_stats_bb=on'
           f'&chk_stats_hrs=on&chk_stats_era=on&chk_stats_xera=on'
           f'&type=details')

    text = fetch_csv(url, timeout=30)
    headers, rows = parse_csv(text)
    print(f'  Headers: {headers[:8]}')
    print(f'  Rows: {len(rows)}')

    team_hl = {}
    for row in rows:
        # Try multiple team column names
        team_raw = ''
        for col in ['player_team', 'team_name', 'team', 'team_abbreviation']:
            team_raw = row.get(col, '').strip().upper()
            if team_raw: break

        abbr = SAVANT_TO_ABBR.get(team_raw, team_raw)
        if not abbr: continue

        pa  = pf(row.get('pa') or row.get('plate_appearances')) or 0
        so  = pf(row.get('so') or row.get('strikeouts')) or 0
        bb  = pf(row.get('bb') or row.get('walks')) or 0
        hr  = pf(row.get('hrs') or row.get('home_runs') or row.get('hr')) or 0
        xe  = pf(row.get('estimated_era_using_speedangle') or row.get('xera'))

        if pa < 10: continue
        pa_as_ip = pa / 4.3
        hl_fip = round((13*hr + 3*bb - 2*so) / pa_as_ip + FIP_CONST, 2) if pa_as_ip > 0 else None

        team_hl[abbr] = {
            'hlFIP':      hl_fip,
            'hlXERA':     xe,
            'hlXFIP':     hl_fip if hl_fip is not None else xe,
            'hlGrade':    bullpen_grade(hl_fip if hl_fip is not None else xe),
            'hlSamplePA': pa,
            'hlAvailable': hl_fip is not None or xe is not None,
        }

    print(f'  Teams with HL data: {len(team_hl)}')
    return team_hl

def main():
    start = time.time()
    hl_data = fetch_hl_bullpen()

    # Load existing bullpen.json and merge HL fields
    try:
        with open('data/bullpen.json') as f:
            bullpen = json.load(f)
    except Exception as e:
        print(f'Could not load bullpen.json: {e}')
        return

    bullpens = bullpen.get('bullpens', {})
    merged = 0
    for abbr, bp in bullpens.items():
        hl = hl_data.get(abbr, {})
        bp['hlXFIP']      = hl.get('hlXFIP')
        bp['hlFIP']       = hl.get('hlFIP')
        bp['hlXERA']      = hl.get('hlXERA')
        bp['hlGrade']     = hl.get('hlGrade')
        bp['hlAvailable'] = hl.get('hlAvailable', False)
        bp['hlSamplePA']  = hl.get('hlSamplePA')
        # Divergence: HL vs overall xFIP
        overall = bp.get('xFIP')
        hl_xfip = bp.get('hlXFIP')
        bp['hlDivergence'] = round(hl_xfip - overall, 2) if (hl_xfip and overall) else None
        merged += 1

    bullpen['hlDataAvailable'] = len(hl_data) > 0
    bullpen['hlFetchedAt'] = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')

    with open('data/bullpen.json', 'w') as f:
        json.dump(bullpen, f)

    elapsed = round(time.time() - start, 1)
    print(f'Done in {elapsed}s — merged HL data for {merged} teams ({len(hl_data)} resolved)')

if __name__ == '__main__':
    main()
