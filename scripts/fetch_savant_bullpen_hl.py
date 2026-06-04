"""
scripts/fetch_savant_bullpen_hl.py — v3.0
Changes from v1:
  - Now calls Vercel api/enrich?type=bullpen endpoint (MLB Stats API based)
    instead of hitting Savant statcast_search directly (blocked from GitHub Actions)
  - Merges hlXFIP results into data/bullpen.json
"""

import json
import time
import urllib.request
from datetime import datetime

VERCEL_BASE = 'https://edge-finder-api.vercel.app'

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

def bullpen_grade(xfip):
    if xfip is None: return None
    if xfip < 3.50: return 'ELITE'
    if xfip < 4.00: return 'ABOVE_AVERAGE'
    if xfip < 4.50: return 'AVERAGE'
    if xfip < 5.00: return 'BELOW_AVERAGE'
    return 'VULNERABLE'

def main():
    start = time.time()
    print('Fetching HL bullpen data from Vercel...')

    data = fetch_json(f'{VERCEL_BASE}/api/enrich?type=bullpen', timeout=55)
    if not data or not data.get('ok'):
        print(f'HL bullpen fetch failed: {data}')
        return

    hl_teams = data.get('teams', {})
    print(f'  {len(hl_teams)} teams with HL data')

    # Load and update bullpen.json
    try:
        with open('data/bullpen.json') as f:
            bullpen = json.load(f)
    except Exception as e:
        print(f'Could not load bullpen.json: {e}')
        return

    bullpens = bullpen.get('bullpens', {})
    merged = 0
    for abbr, bp in bullpens.items():
        hl = hl_teams.get(abbr, {})
        bp['hlXFIP']      = hl.get('hlXFIP')
        bp['hlGrade']     = hl.get('hlGrade')
        bp['hlAvailable'] = hl.get('hlAvailable', False)
        bp['hlSamplePA']  = hl.get('hlSamplePA')
        bp['hlMethod']    = hl.get('hlMethod')
        # Divergence: HL xFIP vs overall xFIP
        overall  = bp.get('xFIP')
        hl_xfip  = bp.get('hlXFIP')
        bp['hlDivergence'] = round(hl_xfip - overall, 2) if (hl_xfip and overall) else None
        merged += 1

    bullpen['hlDataAvailable'] = len(hl_teams) > 0
    bullpen['hlFetchedAt']     = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')

    with open('data/bullpen.json', 'w') as f:
        json.dump(bullpen, f)

    resolved = sum(1 for bp in bullpens.values() if bp.get('hlXFIP') is not None)
    elapsed  = round(time.time() - start, 1)
    print(f'Done in {elapsed}s — {resolved}/{merged} teams with hlXFIP populated')

if __name__ == '__main__':
    main()
