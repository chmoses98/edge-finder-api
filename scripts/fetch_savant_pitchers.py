"""
scripts/fetch_savant_pitchers.py — v5.1
Changes from v5.0:
  - BUG FIX: pitcher=null (TBD starters) caused AttributeError crash.
    .get('pitcher', {}) returns None when pitcher key exists with null value.
    All pitcher access now uses safe_pitcher_get() helper.
  - Added structured error reporting: script fails clearly on unhandled errors
    instead of silently returning exit 0 with no data.
  - Added retry/backoff on transient API errors (5xx, connection timeout).
  - Added fallback: if all enrich batches fail, log clearly and continue.
    pitcherSavant blocks that already have xFIP/xERA from the Vercel slate
    are preserved — the script only enriches fbPct, velocity, TTO on top.
  - sys.exit(1) on uncaught main() exception with full traceback printed.
"""

import json
import sys
import time
import traceback
import urllib.request
import urllib.error
from datetime import datetime

VERCEL_BASE = 'https://edge-finder-api.vercel.app'
SEASON      = '2026'


# ── Safe helpers ──────────────────────────────────────────────────────────────

def safe_pitcher(game, side):
    """Return pitcher dict for game[side], or {} if null/missing. Never raises."""
    side_data = game.get(side)
    if not isinstance(side_data, dict):
        return {}
    pitcher = side_data.get('pitcher')
    if not isinstance(pitcher, dict):
        return {}
    return pitcher


def safe_pitcher_id(game, side):
    """Return pitcher ID as string, or '' if not available."""
    p = safe_pitcher(game, side)
    pid = p.get('id', '')
    return str(pid) if pid else ''


def safe_pitcher_name(game, side):
    """Return pitcher name string."""
    return safe_pitcher(game, side).get('name', '?')


# ── HTTP fetch with retry/backoff ─────────────────────────────────────────────

def fetch_json(url, timeout=45, retries=2, backoff=1.5):
    """
    Fetch JSON from url with retry/backoff on transient errors.
    Returns (data_dict, error_string) — never raises.
    Retries on: connection errors, 5xx, 429 (rate limit).
    Does NOT retry on: 4xx client errors (except 429).
    """
    last_err = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={
                'User-Agent': 'Mozilla/5.0',
                'Accept': 'application/json',
                'Cache-Control': 'no-cache',
            })
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read()), None

        except urllib.error.HTTPError as e:
            last_err = f'HTTP {e.code}'
            if e.code in (429, 500, 502, 503, 504):
                # Transient — retry with backoff
                if attempt < retries:
                    wait = backoff * (2 ** attempt)
                    print(f'    [{attempt+1}/{retries+1}] {last_err} — retrying in {wait:.1f}s')
                    time.sleep(wait)
                    continue
            # 4xx (not 429) — permanent failure, no retry
            return None, last_err

        except (TimeoutError, ConnectionError, OSError) as e:
            last_err = f'network: {e}'
            if attempt < retries:
                wait = backoff * (2 ** attempt)
                print(f'    [{attempt+1}/{retries+1}] {last_err} — retrying in {wait:.1f}s')
                time.sleep(wait)
                continue

        except Exception as e:
            last_err = f'{type(e).__name__}: {e}'
            return None, last_err

    return None, last_err


# ── Enrich API calls ──────────────────────────────────────────────────────────

def fetch_batch(endpoint_type, pitcher_ids, batch_num, timeout=50):
    """
    Fetch one batch from /api/enrich?type={endpoint_type}.
    Returns dict of {pitcher_id: data} or {} on failure.
    """
    if not pitcher_ids:
        return {}
    ids_str = ','.join(pitcher_ids)
    url = f'{VERCEL_BASE}/api/enrich?type={endpoint_type}&playerIds={ids_str}&year={SEASON}'
    data, err = fetch_json(url, timeout=timeout)
    if err:
        print(f'  Batch {batch_num} ({endpoint_type}): {err}')
        return {}
    if not data or not data.get('ok'):
        msg = data.get('error', 'ok=false') if data else 'null response'
        print(f'  Batch {batch_num} ({endpoint_type}): API error — {msg}')
        return {}
    return data.get('pitchers', {})


def main():
    start = time.time()

    # ── Load slate ────────────────────────────────────────────────────────────
    try:
        with open('data/slate.json') as f:
            slate = json.load(f)
    except FileNotFoundError:
        print('ERROR: data/slate.json not found — was fetch_endpoint slate step successful?',
              file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f'ERROR: data/slate.json is not valid JSON: {e}', file=sys.stderr)
        sys.exit(1)

    # ── Load pitcher fbPct fallback ───────────────────────────────────────────
    try:
        with open('data/savant_team.json') as f:
            savant_team = json.load(f)
        pitcher_map = savant_team.get('pitchers', {})
    except Exception:
        pitcher_map = {}

    games = slate.get('games', [])
    if not games:
        print('No games in slate — nothing to enrich.')
        return

    # ── Collect starter IDs safely ────────────────────────────────────────────
    # Use safe_pitcher_id() to avoid AttributeError when pitcher=null
    starter_ids_set = set()
    tbd_count = 0
    for g in games:
        for side in ['away', 'home']:
            pid = safe_pitcher_id(g, side)
            if pid:
                starter_ids_set.add(pid)
            else:
                name = safe_pitcher(g, side).get('name', '')
                if not name:
                    tbd_count += 1
                    away = g.get('away', {}).get('abbr', '?')
                    home = g.get('home', {}).get('abbr', '?')
                    print(f'  TBD/null starter: {away}@{home}/{side} — skipping Savant enrichment')

    starter_ids = sorted(starter_ids_set)
    print(f'Starters: {len(starter_ids)} IDs | TBD starters: {tbd_count}')

    if not starter_ids:
        print('No confirmed starters with IDs — no Savant enrichment possible.')
        print('pitcherSavant blocks unchanged (already populated by Vercel slate API).')
        elapsed = round(time.time() - start, 1)
        print(f'Done in {elapsed}s — 0 starters enriched (all TBD)')
        return

    # ── Fetch pitcher fbPct ───────────────────────────────────────────────────
    print('Fetching pitcher fbPct via /api/enrich?type=pitcherfbpct...')
    fbpct_map = {}
    BATCH = 15
    any_fbpct_success = False
    for i in range(0, len(starter_ids), BATCH):
        batch = starter_ids[i:i+BATCH]
        result = fetch_batch('pitcherfbpct', batch, i//BATCH+1)
        if result:
            fbpct_map.update(result)
            resolved = sum(1 for v in result.values() if v is not None)
            print(f'  Batch {i//BATCH+1}: {resolved}/{len(batch)} resolved')
            any_fbpct_success = True
        time.sleep(0.3)

    if not any_fbpct_success and starter_ids:
        print('  WARNING: fbPct enrich failed for all batches — using savant_team.json fallback')

    # ── Fetch velocity trends ─────────────────────────────────────────────────
    print('Fetching velocity trends via /api/enrich?type=velocity...')
    velocity_map = {}
    for i in range(0, len(starter_ids), BATCH):
        batch = starter_ids[i:i+BATCH]
        result = fetch_batch('velocity', batch, i//BATCH+1, timeout=45)
        if result:
            velocity_map.update(result)
            resolved = sum(1 for v in result.values() if v.get('velocityRecent') is not None)
            print(f'  Batch {i//BATCH+1}: {resolved}/{len(batch)} velocity resolved')
        else:
            print(f'  Batch {i//BATCH+1}: velocity unavailable (will be null)')
        time.sleep(0.3)

    # ── Fetch TTO splits ──────────────────────────────────────────────────────
    print('Fetching TTO splits via /api/enrich?type=tto...')
    tto_results = {}
    for i in range(0, len(starter_ids), 10):
        batch = starter_ids[i:i+10]
        result = fetch_batch('tto', batch, i//10+1, timeout=55)
        if result:
            tto_results.update(result)
        time.sleep(0.3)

    # ── Merge into slate.json ─────────────────────────────────────────────────
    updated = 0
    vel_resolved = 0
    for game in games:
        for side in ['away', 'home']:
            pid = safe_pitcher_id(game, side)
            if not pid:
                continue

            # pitcherSavant may be None if pitcher not in Savant leaderboard
            side_data = game.get(side)
            if not isinstance(side_data, dict):
                continue
            ps = side_data.get('pitcherSavant')
            if not isinstance(ps, dict):
                continue

            # fbPct: enrich result is a number or null
            fb = fbpct_map.get(pid)
            if fb is None:
                fb = pitcher_map.get(pid, {}).get('fbPct') if pitcher_map else None
            ps['fbPct'] = fb

            # Velocity trend
            vel = velocity_map.get(pid, {}) or {}
            ps['velocityRecent']  = vel.get('velocityRecent')
            ps['velocitySeason']  = vel.get('velocitySeason')
            ps['velocityStartsN'] = vel.get('velocityStartsN')
            if ps['velocityRecent'] is not None:
                vel_resolved += 1
                drop = (ps['velocitySeason'] or 0) - (ps['velocityRecent'] or 0)
                if drop >= 1.0:
                    name = safe_pitcher_name(game, side)
                    print(f'  ⚠ Velocity drop: {name} '
                          f'{ps["velocitySeason"]:.1f}→{ps["velocityRecent"]:.1f} mph ({drop:+.1f})')

            # TTO split
            tto = tto_results.get(pid, {}) or {}
            ps['ttoSplit']     = tto.get('ttoSplit')
            ps['ttoRisk']      = tto.get('ttoRisk', False)
            ps['ttoAvailable'] = tto.get('available', False)
            ps['tto1']         = tto.get('tto1')
            ps['tto3']         = tto.get('tto3')

            updated += 1

    # ── Sanitize recentFIP ────────────────────────────────────────────────────
    sanitized = 0
    cleared   = 0
    for game in games:
        for side in ['away', 'home']:
            side_data = game.get(side)
            if not isinstance(side_data, dict):
                continue
            ps = side_data.get('pitcherSavant')
            if not isinstance(ps, dict):
                continue
            rfip    = ps.get('recentFIP')
            samples = ps.get('startsSampled') or 0
            if rfip is None:
                continue
            if samples < 3:
                ps['recentFIP'] = None
                ps['recentFIPCleared'] = True
                ps['recentFIPClearedReason'] = (
                    f'startsSampled={samples} < 3 — xFIP used for regression'
                )
                cleared += 1
            elif rfip < 0:
                ps['recentFIP'] = 0.0
                ps['recentFIPSanitized'] = True
                ps['recentFIPOriginal']  = rfip
                sanitized += 1

    if cleared or sanitized:
        print(f'recentFIP: {cleared} cleared (startsSampled<3), {sanitized} floored to 0.0')

    # ── Write slate.json ──────────────────────────────────────────────────────
    with open('data/slate.json', 'w') as f:
        json.dump(slate, f)

    elapsed = round(time.time() - start, 1)
    tto_resolved = sum(1 for r in tto_results.values() if r.get('available'))
    print(f'Done in {elapsed}s — '
          f'fbPct: {len(fbpct_map)}/{len(starter_ids)} | '
          f'velocity: {vel_resolved}/{updated} | '
          f'TTO: {tto_resolved}/{len(starter_ids)} | '
          f'enriched: {updated} starters')


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        print(f'\nUNHANDLED ERROR in fetch_savant_pitchers.py:', file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)
