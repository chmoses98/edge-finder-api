"""
scripts/fetch_savant_pitchers.py — v5.2
Changes from v5.1:
  - Phase 5: split the per-game merge/sanitize logic (previously two
    in-place mutation loops over `games` inside main()) into pure
    functions with no I/O of their own:
      compute_pitcher_savant_enrichment() — pure per-side enrichment
      sanitize_recent_fip()               — pure per-side sanitization
      compute_game_pitcher_savant_fields() — pure per-game combination
      apply_savant_enrichment_immutable()  — pure per-slate transform
    fetch_json()/fetch_batch() (the network adapters) are unchanged.
    main() is now purely an orchestration adapter: fetch every batch
    first, then apply the whole enrichment+sanitization pass in one
    pure transform. CLI invocation, file paths, and output content are
    unchanged — see docs/IMMUTABLE_PIPELINE.md's
    fetch_savant_pitchers.py section for the full before/after contract
    and the golden-equivalence tests that prove it.

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
import os
import sys
import tempfile
import time
import traceback
import urllib.request
import urllib.error
from datetime import datetime

VERCEL_BASE = 'https://edge-finder-api.vercel.app'
SEASON      = '2026'


# ── Safe helpers ──────────────────────────────────────────────────────────────

def _write_slate_atomic(slate, path='data/slate.json'):
    """
    Write `slate` to `path` atomically: serialize to a temp file in the
    same directory, fsync it, then move it into place with os.replace().
    A plain `open(path, 'w')` + `json.dump()` writes incrementally, so a
    serialization failure partway through (verified empirically during
    the Phase 5 pre-refactor audit) leaves a truncated, invalid JSON file
    at `path` — this never happens with atomic replace. Output content
    and format are byte-for-byte unchanged; only the write mechanism is
    hardened. See fetch_lineups.py's identical helper for the fuller
    rationale, including why lib/pipeline_artifacts.write_stage_artifact()
    is not reused here (its meta/data envelope is not this file's format).
    """
    dest_dir = os.path.dirname(path) or '.'
    fd, tmp_path = tempfile.mkstemp(prefix='.slate.', suffix='.json.tmp', dir=dest_dir)
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(slate, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise


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


# ── HTTP fetch with retry/backoff (network adapter) ───────────────────────────

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


# ── Enrich API calls (network adapter) ────────────────────────────────────────

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


# ── Pure per-side transforms ───────────────────────────────────────────────────

def compute_pitcher_savant_enrichment(ps, pid, fbpct_map, velocity_map, tto_results, pitcher_map):
    """
    Pure function: given one side's existing pitcherSavant dict, its
    resolvable pitcher ID, and the three already-fetched per-pitcher-id
    maps plus the savant_team.json fallback map, return a NEW dict
    (shallow copy of `ps`) with fbPct/velocity*/tto* fields set exactly
    as the original merge loop did. Does not mutate `ps` or any of the
    map arguments. Caller is responsible for only calling this when
    `pid` is truthy and `ps` is a dict (mirrors the original's own
    guard conditions).
    """
    new_ps = dict(ps)

    # fbPct: enrich result is a number or null
    fb = fbpct_map.get(pid)
    if fb is None:
        fb = pitcher_map.get(pid, {}).get('fbPct') if pitcher_map else None
    new_ps['fbPct'] = fb

    # Velocity trend
    vel = velocity_map.get(pid, {}) or {}
    new_ps['velocityRecent']  = vel.get('velocityRecent')
    new_ps['velocitySeason']  = vel.get('velocitySeason')
    new_ps['velocityStartsN'] = vel.get('velocityStartsN')

    # TTO split
    tto = tto_results.get(pid, {}) or {}
    new_ps['ttoSplit']     = tto.get('ttoSplit')
    new_ps['ttoRisk']      = tto.get('ttoRisk', False)
    new_ps['ttoAvailable'] = tto.get('available', False)
    new_ps['tto1']         = tto.get('tto1')
    new_ps['tto3']         = tto.get('tto3')

    return new_ps


def sanitize_recent_fip(ps):
    """
    Pure function: given one side's pitcherSavant dict (or a non-dict
    value), return a NEW dict if sanitization would change anything, or
    None if no change is needed (not a dict, recentFIP absent, or
    recentFIP already valid) — mirrors the original sanitize loop's
    `continue` conditions exactly, including running independent of
    whether this side had a resolvable pitcher ID.
    """
    if not isinstance(ps, dict):
        return None
    rfip = ps.get('recentFIP')
    samples = ps.get('startsSampled') or 0
    if rfip is None:
        return None
    if samples < 3:
        new_ps = dict(ps)
        new_ps['recentFIP'] = None
        new_ps['recentFIPCleared'] = True
        new_ps['recentFIPClearedReason'] = (
            f'startsSampled={samples} < 3 — xFIP used for regression'
        )
        return new_ps
    elif rfip < 0:
        new_ps = dict(ps)
        new_ps['recentFIP'] = 0.0
        new_ps['recentFIPSanitized'] = True
        new_ps['recentFIPOriginal']  = rfip
        return new_ps
    return None


def compute_game_pitcher_savant_fields(game, fbpct_map, velocity_map, tto_results, pitcher_map):
    """
    Pure per-game transform: returns (new_game, side_reports).

    new_game is a NEW dict (shallow copy of `game`, with fresh away/home
    sub-dicts wherever a pitcherSavant block changed) with both sides'
    pitcherSavant blocks enriched and sanitized exactly as main()'s
    original two-pass mutation loop did — enrichment first (only when a
    pitcher ID resolves and an existing pitcherSavant dict is present),
    then sanitization (unconditionally, for any side with a dict
    pitcherSavant, whether or not enrichment ran). Does not mutate
    `game` or any of the map arguments.

    side_reports is {'away': {...}, 'home': {...}} with the bookkeeping
    main() needs to reproduce the exact original summary counts/prints
    (enriched, vel_resolved, velocity_drop_msg, recentFIP_cleared,
    recentFIP_sanitized) without main() having to re-derive them by
    re-inspecting the transformed data.
    """
    new_game = dict(game)
    side_reports = {}

    for side in ('away', 'home'):
        side_data = game.get(side)
        if not isinstance(side_data, dict):
            side_reports[side] = {
                'enriched': False, 'vel_resolved': False, 'velocity_drop_msg': None,
                'recentFIP_cleared': False, 'recentFIP_sanitized': False,
            }
            continue

        pid = safe_pitcher_id(game, side)
        ps = side_data.get('pitcherSavant')
        enriched = bool(pid) and isinstance(ps, dict)

        final_ps = ps
        if enriched:
            final_ps = compute_pitcher_savant_enrichment(ps, pid, fbpct_map, velocity_map, tto_results, pitcher_map)

        sanitized = sanitize_recent_fip(final_ps)
        if sanitized is not None:
            final_ps = sanitized

        velocity_drop_msg = None
        vel_resolved = enriched and isinstance(final_ps, dict) and final_ps.get('velocityRecent') is not None
        if vel_resolved:
            drop = (final_ps.get('velocitySeason') or 0) - (final_ps.get('velocityRecent') or 0)
            if drop >= 1.0:
                name = safe_pitcher_name(game, side)
                velocity_drop_msg = (
                    f'  ⚠ Velocity drop: {name} '
                    f'{final_ps["velocitySeason"]:.1f}→{final_ps["velocityRecent"]:.1f} mph ({drop:+.1f})'
                )

        new_side_data = dict(side_data)
        new_side_data['pitcherSavant'] = final_ps
        new_game[side] = new_side_data

        side_reports[side] = {
            'enriched': enriched,
            'vel_resolved': vel_resolved,
            'velocity_drop_msg': velocity_drop_msg,
            'recentFIP_cleared': isinstance(sanitized, dict) and sanitized.get('recentFIPCleared', False),
            'recentFIP_sanitized': isinstance(sanitized, dict) and sanitized.get('recentFIPSanitized', False),
        }

    return new_game, side_reports


def apply_savant_enrichment_immutable(slate, fbpct_map, velocity_map, tto_results, pitcher_map):
    """
    Pure transform: given the parsed slate and the three already-fetched
    per-pitcher-id maps plus the savant_team.json fallback map, return
    (new_slate, side_reports_by_game) — a NEW slate object with every
    game's pitcherSavant blocks enriched/sanitized, without mutating
    `slate` or any game inside it, without changing any other top-level
    slate field, the number of games, or game order.
    """
    new_games = []
    side_reports_by_game = []
    for game in slate.get('games', []):
        new_game, side_reports = compute_game_pitcher_savant_fields(
            game, fbpct_map, velocity_map, tto_results, pitcher_map
        )
        new_games.append(new_game)
        side_reports_by_game.append(side_reports)

    new_slate = dict(slate)
    new_slate['games'] = new_games
    return new_slate, side_reports_by_game


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

    # ── Pure transform pass: enrich + sanitize every game in one shot ─────────
    slate, side_reports_by_game = apply_savant_enrichment_immutable(
        slate, fbpct_map, velocity_map, tto_results, pitcher_map
    )
    games = slate.get('games', [])

    updated = 0
    vel_resolved = 0
    cleared = 0
    sanitized = 0
    for side_reports in side_reports_by_game:
        for side in ('away', 'home'):
            r = side_reports[side]
            if r['enriched']:
                updated += 1
            if r['vel_resolved']:
                vel_resolved += 1
            if r['velocity_drop_msg']:
                print(r['velocity_drop_msg'])
            if r['recentFIP_cleared']:
                cleared += 1
            if r['recentFIP_sanitized']:
                sanitized += 1

    if cleared or sanitized:
        print(f'recentFIP: {cleared} cleared (startsSampled<3), {sanitized} floored to 0.0')

    # ── Write slate.json ──────────────────────────────────────────────────────
    _write_slate_atomic(slate)

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
