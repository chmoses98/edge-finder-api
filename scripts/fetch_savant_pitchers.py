"""
scripts/fetch_savant_pitchers.py — v3.0
Changes from v1:
  - TTO splits now fetched via Vercel api/enrich?type=tto endpoint (MLB Stats API based)
    instead of Savant statcast_search (blocked from GitHub Actions)
  - fbPct now merged from data/savant_team.json pitchers map (also Vercel-fetched)
  - Writes updated ttoSplit, ttoRisk, fbPct into slate.json pitcherSavant blocks
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
        print(f'  fetch error {url[:60]}: {e}')
        return None

def main():
    start = time.time()

    with open('data/slate.json') as f:
        slate = json.load(f)

    # Load pitcher fbPct from savant_team.json (fetched by fetch_savant_team.py)
    try:
        with open('data/savant_team.json') as f:
            savant_team = json.load(f)
        pitcher_map = savant_team.get('pitchers', {})
        print(f'Loaded pitcher fbPct map: {len(pitcher_map)} pitchers')
    except Exception as e:
        pitcher_map = {}
        print(f'savant_team.json not found: {e}')

    # Collect starter IDs from slate
    games    = slate.get('games', [])
    starter_ids = []
    for g in games:
        for side in ['away', 'home']:
            pid = g.get(side, {}).get('pitcher', {}).get('id')
            if pid:
                starter_ids.append(str(pid))
    starter_ids = list(set(starter_ids))
    print(f'Fetching TTO splits for {len(starter_ids)} starters via Vercel...')

    # Fetch TTO splits from Vercel endpoint (batched to avoid URL length limits)
    tto_results = {}
    BATCH = 10
    for i in range(0, len(starter_ids), BATCH):
        batch = starter_ids[i:i+BATCH]
        ids_str = ','.join(batch)
        url = f'{VERCEL_BASE}/api/enrich?type=tto&playerIds={ids_str}&year={SEASON}'
        data = fetch_json(url, timeout=45)
        if data and data.get('ok'):
            tto_results.update(data.get('pitchers', {}))
            print(f'  Batch {i//BATCH+1}: {len(data.get("pitchers",{}))} TTO results')
        else:
            print(f'  Batch {i//BATCH+1}: failed')
        time.sleep(0.5)

    # Merge into slate.json
    updated = 0
    for game in games:
        for side in ['away', 'home']:
            pid = str(game.get(side, {}).get('pitcher', {}).get('id', ''))
            if not pid:
                continue
            ps = game.get(side, {}).get('pitcherSavant')
            if ps is None:
                continue

            # TTO split
            tto = tto_results.get(pid, {})
            ps['ttoSplit']     = tto.get('ttoSplit')
            ps['ttoRisk']      = tto.get('ttoRisk', False)
            ps['ttoAvailable'] = tto.get('available', False)
            ps['tto1']         = tto.get('tto1')
            ps['tto3']         = tto.get('tto3')

            # fbPct from savant_team pitcher map (overrides null from slate)
            pitcher_savant_data = pitcher_map.get(pid, {})
            if pitcher_savant_data.get('fbPct') is not None:
                ps['fbPct'] = pitcher_savant_data['fbPct']

            updated += 1

    with open('data/slate.json', 'w') as f:
        json.dump(slate, f)

    tto_resolved = sum(1 for r in tto_results.values() if r.get('available'))
    fb_resolved  = sum(1 for g in games
                       for side in ['away','home']
                       if g.get(side,{}).get('pitcherSavant',{}).get('fbPct') is not None)

    elapsed = round(time.time() - start, 1)
    print(f'Done in {elapsed}s — {tto_resolved}/{len(starter_ids)} TTO resolved | {fb_resolved} fbPct populated')
    print(f'Updated {updated} pitcherSavant blocks in slate.json')

if __name__ == '__main__':
    main()
