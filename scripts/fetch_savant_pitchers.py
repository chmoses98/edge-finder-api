"""
scripts/fetch_savant_pitchers.py — v1.0

Fetches TTO (Times Through Order) splits for today's confirmed starters.
Runs in GitHub Actions — no Vercel timeout constraint.

Reads starter IDs from data/pitchers.json, fetches TTO splits from Savant,
merges results into data/slate.json pitcherSavant blocks.

Output: updates data/slate.json with ttoSplit, ttoRisk, tto1, tto3 fields.
"""

import json
import time
import urllib.request
from datetime import datetime

SEASON  = '2026'
FIP_CONST = 3.10
MIN_TTO_PA = 20   # minimum PA in each TTO window for reliable split

def fetch_csv(url, timeout=20):
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode('utf-8')
    except Exception as e:
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
            else: current += ch
        result.append(current.strip())
        return result
    headers = split_line(lines[0])
    rows = []
    for line in lines[1:]:
        if not line.strip(): continue
        values = split_line(line)
        rows.append({headers[i]: values[i] if i < len(values) else '' for i in range(len(headers))})
    return rows

def pf(val):
    try:
        n = float(val)
        return None if (n != n) else n
    except (TypeError, ValueError):
        return None

def fetch_tto_split(pitcher_id, tto_num):
    """Fetch stats for a specific times-through-order window from Savant."""
    url = (f'https://baseballsavant.mlb.com/statcast_search/csv?all=true'
           f'&hfSea={SEASON}%7C&player_type=pitcher'
           f'&pitchers_lookup%5B%5D={pitcher_id}'
           f'&hfGT=R%7C&hfTO={tto_num}%7C'
           f'&min_pitches=0&min_results=0&min_pas=0'
           f'&group_by=name&sort_col=pitches&sort_order=desc'
           f'&chk_stats_pa=on&chk_stats_so=on&chk_stats_bb=on'
           f'&chk_stats_hrs=on&chk_stats_xera=on&type=details')
    text = fetch_csv(url)
    rows = parse_csv(text)
    if not rows:
        return None

    # group_by=name returns one aggregated row
    row = rows[0]
    pa  = pf(row.get('pa') or row.get('plate_appearances') or row.get('total_pa')) or 0
    so  = pf(row.get('so') or row.get('strikeouts')) or 0
    bb  = pf(row.get('bb') or row.get('walks') or row.get('base_on_balls')) or 0
    hr  = pf(row.get('hrs') or row.get('home_runs') or row.get('hr')) or 0
    xe  = pf(row.get('estimated_era_using_speedangle') or row.get('xera'))

    if pa < MIN_TTO_PA:
        return None

    pa_as_ip = pa / 4.3
    fip = round((13 * hr + 3 * bb - 2 * so) / pa_as_ip + FIP_CONST, 2) if pa_as_ip > 0 else None
    return {'pa': pa, 'so': so, 'bb': bb, 'hr': hr, 'fip': fip, 'xERA': xe}

def compute_tto(pitcher_id):
    """Returns ttoSplit (3rd TTO FIP - 1st TTO FIP) and risk flag."""
    pid = str(pitcher_id)
    tto1 = fetch_tto_split(pid, 1)
    time.sleep(0.3)  # brief pause between requests
    tto3 = fetch_tto_split(pid, 3)

    if not tto1 or not tto3:
        return {'available': False, 'ttoSplit': None, 'ttoRisk': False, 'tto1': None, 'tto3': None}

    split = round(tto3['fip'] - tto1['fip'], 2) if tto3['fip'] and tto1['fip'] else None
    return {
        'available': True,
        'ttoSplit': split,
        'ttoRisk':  split is not None and split > 0.50,
        'tto1': tto1,
        'tto3': tto3,
    }

def main():
    start = time.time()

    with open('data/slate.json') as f:
        slate = json.load(f)

    games = slate.get('games', [])
    pitcher_ids = set()
    for game in games:
        for side in ['away', 'home']:
            pid = game.get(side, {}).get('pitcher', {}).get('id')
            if pid:
                pitcher_ids.add(str(pid))

    print(f'Fetching TTO splits for {len(pitcher_ids)} starters...')

    tto_results = {}
    for i, pid in enumerate(sorted(pitcher_ids)):
        print(f'  [{i+1}/{len(pitcher_ids)}] pitcher {pid}...', end=' ')
        result = compute_tto(pid)
        tto_results[pid] = result
        if result['available']:
            print(f'split={result["ttoSplit"]} risk={result["ttoRisk"]}')
        else:
            print('unavailable')
        time.sleep(0.5)

    # Merge into slate.json pitcherSavant blocks
    updated = 0
    for game in games:
        for side in ['away', 'home']:
            pid = str(game.get(side, {}).get('pitcher', {}).get('id', ''))
            if not pid or pid not in tto_results:
                continue
            ps = game.get(side, {}).get('pitcherSavant')
            if ps is None:
                continue
            tto = tto_results[pid]
            ps['ttoSplit']     = tto.get('ttoSplit')
            ps['ttoRisk']      = tto.get('ttoRisk', False)
            ps['ttoAvailable'] = tto.get('available', False)
            ps['tto1']         = tto.get('tto1')
            ps['tto3']         = tto.get('tto3')
            updated += 1

    with open('data/slate.json', 'w') as f:
        json.dump(slate, f)

    elapsed = round(time.time() - start, 1)
    resolved = sum(1 for r in tto_results.values() if r['available'])
    print(f'Done in {elapsed}s — {resolved}/{len(pitcher_ids)} TTO splits resolved')
    print(f'Updated {updated} pitcherSavant blocks in slate.json')

if __name__ == '__main__':
    main()
