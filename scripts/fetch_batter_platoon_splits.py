#!/usr/bin/env python3
"""
scripts/fetch_batter_platoon_splits.py — v1.0
=================================================
Baseball Input Data / Platoon Context mission.

Fetches per-batter wOBA/K%/BB%/ISO splits vs LHP and vs RHP (via
`/api/enrich?type=batterplatoon`, MLB Stats API sitCodes=vl/vr -- see
that endpoint's own docstring in api/enrich.js) for every player in
every game's `confirmedLineup` (written by scripts/fetch_lineups.py,
which runs immediately before this script in .github/workflows/
fetch-slate.yml), and merges the result into each confirmed batter's
`platoonSplits` field.

Runs AFTER fetch_lineups.py (needs confirmedLineup to know which
player IDs to fetch) and BEFORE build_market_ledger.py (which reads
platoonSplits via lib.research.platoon_context).

Games with no confirmed lineup (confirmedLineup absent/empty) are
skipped entirely -- this script never fetches or guesses splits for an
unconfirmed lineup (Requirement 2: unconfirmed lineup never guesses
players/order). A per-batter fetch failure (network error, player not
found, insufficient PA) leaves that batter's platoonSplits at None,
exactly the same "honestly missing" state fetch_lineups.py already
seeds it with -- never fabricated.

IDEMPOTENT: safe to re-run any number of times.

EXIT CODES:
  0 — success (script ran; individual batter fetch failures are
      non-fatal and logged, matching fetch_savant_pitchers.py's own
      best-effort convention)
  1 — hard failure (slate missing / unreadable)
"""

import json
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib.atomic_json import write_json_atomic

VERCEL_BASE = 'https://edge-finder-api.vercel.app'
SEASON = '2026'
BATCH = 20
SLATE_PATH = 'data/slate.json'


def fetch_json(url, timeout=45):
    try:
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0',
            'Accept': 'application/json',
        })
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read()), None
    except Exception as e:
        return None, str(e)


def fetch_batch(player_ids, batch_num):
    if not player_ids:
        return {}
    ids_str = ','.join(player_ids)
    url = f'{VERCEL_BASE}/api/enrich?type=batterplatoon&playerIds={ids_str}&year={SEASON}'
    data, err = fetch_json(url)
    if err:
        print(f'  Batch {batch_num}: {err}')
        return {}
    if not data or not data.get('ok'):
        msg = data.get('error', 'ok=false') if data else 'null response'
        print(f'  Batch {batch_num}: API error — {msg}')
        return {}
    return data.get('batters', {})


def collect_confirmed_batter_ids(games):
    """Pure: every distinct playerId across every game's confirmedLineup."""
    ids = set()
    for g in games:
        for side_key in ('awayTeamStats', 'homeTeamStats'):
            for h in (g.get(side_key) or {}).get('confirmedLineup') or []:
                pid = h.get('playerId')
                if pid:
                    ids.add(str(pid))
    return sorted(ids)


def apply_platoon_splits_immutable(slate, splits_map):
    """
    Pure transform: returns a NEW slate object with every confirmedLineup
    entry's platoonSplits field set from splits_map (playerId -> {vsLHP,
    vsRHP}), without mutating `slate` or any game/list inside it. A
    playerId absent from splits_map (fetch failed or player wasn't in any
    confirmedLineup) keeps platoonSplits=None exactly as
    fetch_lineups.py seeded it -- never fabricated.
    """
    new_games = []
    updated = 0
    for g in slate.get('games', []):
        new_game = dict(g)
        for side_key in ('awayTeamStats', 'homeTeamStats'):
            ts = g.get(side_key)
            if not isinstance(ts, dict) or not ts.get('confirmedLineup'):
                continue
            new_lineup = []
            for h in ts['confirmedLineup']:
                pid = h.get('playerId')
                split = splits_map.get(str(pid)) if pid else None
                if split and (split.get('vsLHP') or split.get('vsRHP')):
                    new_h = dict(h)
                    new_h['platoonSplits'] = {
                        'vsLHP': split.get('vsLHP'),
                        'vsRHP': split.get('vsRHP'),
                    }
                    new_lineup.append(new_h)
                    updated += 1
                else:
                    new_lineup.append(h)
            new_ts = dict(ts)
            new_ts['confirmedLineup'] = new_lineup
            new_game[side_key] = new_ts
        new_games.append(new_game)

    new_slate = dict(slate)
    new_slate['games'] = new_games
    return new_slate, updated


def main():
    start = time.time()
    try:
        with open(SLATE_PATH) as f:
            slate = json.load(f)
    except FileNotFoundError:
        print(f'ERROR: {SLATE_PATH} not found', file=sys.stderr)
        return 1
    except json.JSONDecodeError as e:
        print(f'ERROR: {SLATE_PATH} is not valid JSON: {e}', file=sys.stderr)
        return 1

    games = slate.get('games', [])
    batter_ids = collect_confirmed_batter_ids(games)
    print(f'fetch_batter_platoon_splits v1.0 | games={len(games)} | confirmed batters={len(batter_ids)}')

    if not batter_ids:
        print('No confirmed-lineup batters found — nothing to fetch (unconfirmed lineups are never guessed).')
        return 0

    splits_map = {}
    for i in range(0, len(batter_ids), BATCH):
        batch = batter_ids[i:i + BATCH]
        result = fetch_batch(batch, i // BATCH + 1)
        if result:
            splits_map.update(result)
            resolved = sum(1 for v in result.values() if v.get('vsLHP') or v.get('vsRHP'))
            print(f'  Batch {i // BATCH + 1}: {resolved}/{len(batch)} resolved')
        time.sleep(0.3)

    slate, updated = apply_platoon_splits_immutable(slate, splits_map)
    write_json_atomic(slate, SLATE_PATH)

    elapsed = round(time.time() - start, 1)
    print(f'Done in {elapsed}s — {updated}/{len(batter_ids)} confirmed batters got a platoon split')
    return 0


if __name__ == '__main__':
    sys.exit(main())
