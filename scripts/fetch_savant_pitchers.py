"""
scripts/fetch_savant_pitchers.py — v5.0
Changes from v4:
  - UPGRADE 3: Fetches velocityRecent (avg FB velo last 3 starts) and
    velocitySeason (season avg FB velo) for each starter via MLB Stats API
    game log endpoint. Used by slate.js for velocity degradation adjustment.
  - velocityRecent/velocitySeason merged into pitcherSavant blocks.
  - All existing fbPct and TTO logic unchanged.
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

def fetch_velocity_data(pitcher_id):
    """
    Fetch avg fastball velocity from last 3 starts vs season avg.
    Uses MLB Stats API game log with pitchData hydration.
    Returns: { velocityRecent: float|None, velocitySeason: float|None }
    """
    url = (f'https://statsapi.mlb.com/api/v1/people/{pitcher_id}/stats'
           f'?stats=gameLog&group=pitching&season={SEASON}&gameType=R'
           f'&hydrate=pitchData&limit=10')
    data = fetch_json(url, timeout=20)
    if not data:
        return {'velocityRecent': None, 'velocitySeason': None}

    try:
        splits = data.get('stats', [{}])[0].get('splits', [])
        # Filter to starts only (gamesStarted > 0)
        starts = [s for s in splits if s.get('stat', {}).get('gamesStarted', 0) > 0]
        if not starts:
            return {'velocityRecent': None, 'velocitySeason': None}

        # Pull avg fastball speed from each start via pitchData if available
        # MLB Stats API game log doesn't always include pitch-by-pitch velocity
        # Use the 'pitchData' field or fallback to null
        velos = []
        for start in starts:
            pd = start.get('pitchData', {})
            # pitchData may have avgSpeed or pitches array
            avg_speed = pd.get('avgSpeed')
            if avg_speed:
                try:
                    velos.append(float(avg_speed))
                except (TypeError, ValueError):
                    pass

        if len(velos) < 3:
            # Fallback: try sport_hitting_tm style endpoint for velocity
            # MLB Stats API /people/{id}/stats?stats=pitchArsenal doesn't exist
            # but we can use the Savant approach via enrich endpoint
            return {'velocityRecent': None, 'velocitySeason': None}

        velos_sorted = sorted(range(len(velos)), key=lambda i: i)  # chronological
        season_avg = sum(velos) / len(velos)
        recent_avg = sum(velos[-3:]) / 3  # last 3 starts

        return {
            'velocityRecent': round(recent_avg, 1),
            'velocitySeason': round(season_avg, 1),
            'velocityStartsN': len(velos),
        }
    except Exception as e:
        return {'velocityRecent': None, 'velocitySeason': None}


def fetch_velocity_via_savant(pitcher_ids):
    """
    Fetch velocity data for a batch of pitchers via the Vercel enrich endpoint.
    type=velocity returns velocityRecent and velocitySeason for each pitcher.
    """
    if not pitcher_ids:
        return {}
    url = f'{VERCEL_BASE}/api/enrich?type=velocity&playerIds={",".join(pitcher_ids)}&year={SEASON}'
    data = fetch_json(url, timeout=45)
    if data and data.get('ok'):
        return data.get('pitchers', {})
    return {}


def main():
    start = time.time()

    with open('data/slate.json') as f:
        slate = json.load(f)

    # Load pitcher fbPct from savant_team.json
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

    # ── Fetch pitcher fbPct ──────────────────────────────────────────────────
    print('Fetching pitcher fbPct via /api/enrich?type=pitcherfbpct...')
    fbpct_map = {}
    BATCH = 15
    for i in range(0, len(starter_ids), BATCH):
        batch = starter_ids[i:i+BATCH]
        url = f'{VERCEL_BASE}/api/enrich?type=pitcherfbpct&playerIds={",".join(batch)}&year={SEASON}'
        data = fetch_json(url, timeout=45)
        if data and data.get('ok'):
            fbpct_map.update(data.get('pitchers', {}))
            print(f'  Batch {i//BATCH+1}: {data.get("resolved",0)}/{len(batch)} resolved')
        else:
            print(f'  Batch {i//BATCH+1}: failed')
        time.sleep(0.3)

    # ── UPGRADE 3: Fetch velocity data ──────────────────────────────────────
    print('Fetching velocity trends via /api/enrich?type=velocity...')
    velocity_map = {}
    for i in range(0, len(starter_ids), BATCH):
        batch = starter_ids[i:i+BATCH]
        vel_data = fetch_velocity_via_savant(batch)
        velocity_map.update(vel_data)
        if vel_data:
            resolved = sum(1 for v in vel_data.values() if v.get('velocityRecent') is not None)
            print(f'  Batch {i//BATCH+1}: {resolved}/{len(batch)} velocity resolved')
        else:
            print(f'  Batch {i//BATCH+1}: velocity endpoint not available yet (will be null)')
        time.sleep(0.3)

    # ── Fetch TTO splits ─────────────────────────────────────────────────────
    print('Fetching TTO splits via /api/enrich?type=tto...')
    tto_results = {}
    for i in range(0, len(starter_ids), 10):
        batch = starter_ids[i:i+10]
        url = f'{VERCEL_BASE}/api/enrich?type=tto&playerIds={",".join(batch)}&year={SEASON}'
        data = fetch_json(url, timeout=50)
        if data and data.get('ok'):
            tto_results.update(data.get('pitchers', {}))
        time.sleep(0.3)

    # ── Merge into slate.json ────────────────────────────────────────────────
    updated = 0
    vel_resolved = 0
    for game in games:
        for side in ['away', 'home']:
            pid = str(game.get(side, {}).get('pitcher', {}).get('id', ''))
            if not pid:
                continue
            ps = game.get(side, {}).get('pitcherSavant')
            if ps is None:
                continue

            # fbPct
            fb = fbpct_map.get(pid)
            if fb is None:
                fb = pitcher_map.get(pid, {}).get('fbPct')
            ps['fbPct'] = fb

            # UPGRADE 3: Velocity trend
            vel = velocity_map.get(pid, {})
            ps['velocityRecent'] = vel.get('velocityRecent')
            ps['velocitySeason'] = vel.get('velocitySeason')
            ps['velocityStartsN'] = vel.get('velocityStartsN')
            if ps['velocityRecent'] is not None:
                vel_resolved += 1
                drop = (ps['velocitySeason'] or 0) - (ps['velocityRecent'] or 0)
                if drop >= 1.0:
                    print(f'  ⚠ Velocity drop: {game.get(side,{}).get("pitcher",{}).get("name","?")} '
                          f'{ps["velocitySeason"]:.1f}→{ps["velocityRecent"]:.1f} mph ({drop:+.1f})')

            # TTO split
            tto = tto_results.get(pid, {})
            ps['ttoSplit']     = tto.get('ttoSplit')
            ps['ttoRisk']      = tto.get('ttoRisk', False)
            ps['ttoAvailable'] = tto.get('available', False)
            ps['tto1']         = tto.get('tto1')
            ps['tto3']         = tto.get('tto3')

            updated += 1

    # ── Sanitize pitcherSavant recentFIP ────────────────────────────────────
    # Root cause: the Vercel /api/pitchers endpoint computes FIP from raw game-log
    # components. For starters with < 3 starts, the FIP formula can produce
    # negative results (e.g. 0 HR, 0 BB, many K in a single outing -> negative FIP).
    # 
    # Fix: if startsSampled < 3, clear recentFIP entirely — there is not enough
    # data to override the season xFIP in the regression blend. build_market_ledger.py
    # already uses xFIP as fallback when recentFIP is null.
    # If startsSampled >= 3, floor recentFIP at 0.0 as a hard minimum.
    sanitized = 0
    cleared   = 0
    for game in games:
        for side in ['away', 'home']:
            ps = game.get(side, {}).get('pitcherSavant')
            if ps is None:
                continue
            rfip    = ps.get('recentFIP')
            samples = ps.get('startsSampled') or 0
            if rfip is None:
                continue
            if samples < 3:
                # Insufficient sample: clear recentFIP entirely.
                # The regression in build_market_ledger will use xFIP only.
                ps['recentFIP'] = None
                ps['recentFIPCleared'] = True
                ps['recentFIPClearedReason'] = f'startsSampled={samples} < 3 — xFIP used for regression'
                cleared += 1
            elif rfip < 0:
                # Negative value from small-sample FIP formula: floor to 0.0
                ps['recentFIP'] = 0.0
                ps['recentFIPSanitized'] = True
                ps['recentFIPOriginal'] = rfip
                sanitized += 1

    if cleared or sanitized:
        print(f'recentFIP sanitized: {cleared} cleared (startsSampled<3), {sanitized} floored to 0.0')

    with open('data/slate.json', 'w') as f:
        json.dump(slate, f)

    elapsed = round(time.time() - start, 1)
    print(f'Done in {elapsed}s — fbPct: {len(fbpct_map)}/{len(starter_ids)} | '
          f'velocity: {vel_resolved}/{updated} | TTO: {sum(1 for r in tto_results.values() if r.get("available"))}/{len(starter_ids)}')

if __name__ == '__main__':
    main()

