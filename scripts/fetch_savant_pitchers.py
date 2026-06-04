"""
scripts/fetch_savant_pitchers.py — v4.0
Changes from v3:
  - Added pitcher fbPct fetch via /api/enrich?type=pitcherfbpct
    (uses MLB Stats API groundOuts/airOuts — reliable, no Savant dependency)
  - fbPct merged directly into pitcherSavant blocks in slate.json
  - TTO splits still fetched via /api/enrich?type=tto
"""

import json
import time
import urllib.request
from datetime import datetime

VERCEL_BASE = 'https://edge-finder-api.vercel.app'
SEASON      = '2026'

def fetch_json(url, timeout=45):
    try:
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0',
            'Accept': 'application/json',
            'Cache-Control': 'no-cache',
        })
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception as e:
        print(f'  fetch error {url[:70]}: {e}')
        return None

def main():
    start = time.time()

    with open('data/slate.json') as f:
        slate = json.load(f)

    # Load pitcher fbPct from savant_team.json (pitcher leaderboard data)
    try:
        with open('data/savant_team.json') as f:
            savant_team = json.load(f)
        pitcher_map = savant_team.get('pitchers', {})
    except Exception:
        pitcher_map = {}

    games = slate.get('games', [])

    # Collect starter IDs
    starter_ids = list({str(g.get(s, {}).get('pitcher', {}).get('id', ''))
                        for g in games for s in ['away', 'home']
                        if g.get(s, {}).get('pitcher', {}).get('id')})
    print(f'Starters: {len(starter_ids)} IDs')

    # ── Fetch pitcher fbPct from MLB Stats API via enrich endpoint ────────────
    print('Fetching pitcher fbPct via /api/enrich?type=pitcherfbpct...')
    fbpct_map = {}
    BATCH = 15
    for i in range(0, len(starter_ids), BATCH):
        batch = starter_ids[i:i+BATCH]
        url = f'{VERCEL_BASE}/api/enrich?type=pitcherfbpct&playerIds={",".join(batch)}&year={SEASON}'
        data = fetch_json(url, timeout=45)
        if data and data.get('ok'):
            fbpct_map.update(data.get('pitchers', {}))
            resolved = data.get('resolved', 0)
            print(f'  Batch {i//BATCH+1}: {resolved}/{len(batch)} resolved')
        else:
            print(f'  Batch {i//BATCH+1}: failed — {data}')
        time.sleep(0.3)

    # ── Fetch TTO splits ──────────────────────────────────────────────────────
    print('Fetching TTO splits via /api/enrich?type=tto...')
    tto_results = {}
    for i in range(0, len(starter_ids), 10):
        batch = starter_ids[i:i+10]
        url = f'{VERCEL_BASE}/api/enrich?type=tto&playerIds={",".join(batch)}&year={SEASON}'
        data = fetch_json(url, timeout=50)
        if data and data.get('ok'):
            tto_results.update(data.get('pitchers', {}))
        time.sleep(0.3)

    # ── Merge into slate.json ─────────────────────────────────────────────────
    updated = 0
    for game in games:
        for side in ['away', 'home']:
            pid = str(game.get(side, {}).get('pitcher', {}).get('id', ''))
            if not pid:
                continue
            ps = game.get(side, {}).get('pitcherSavant')
            if ps is None:
                continue

            # fbPct: from MLB Stats API (primary) or Savant leaderboard (fallback)
            fb = fbpct_map.get(pid)
            if fb is None:
                fb = pitcher_map.get(pid, {}).get('fbPct')  # Savant fallback (usually None)
            ps['fbPct'] = fb

            # TTO split
            tto = tto_results.get(pid, {})
            ps['ttoSplit']     = tto.get('ttoSplit')
            ps['ttoRisk']      = tto.get('ttoRisk', False)
            ps['ttoAvailable'] = tto.get('available', False)
            ps['tto1']         = tto.get('tto1')
            ps['tto3']         = tto.get('tto3')

            updated += 1

    with open('data/slate.json', 'w') as f:
        json.dump(slate, f)

    fb_resolved  = sum(1 for g in games for s in ['away','home']
                       if g.get(s,{}).get('pitcherSavant',{}).get('fbPct') is not None)
    tto_resolved = sum(1 for r in tto_results.values() if r.get('available'))
    elapsed = round(time.time() - start, 1)
    print(f'Done in {elapsed}s — fbPct: {fb_resolved}/{len(starter_ids)*2} | TTO: {tto_resolved}/{len(starter_ids)} resolved')

if __name__ == '__main__':
    main()
