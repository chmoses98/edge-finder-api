#!/usr/bin/env python3
"""
scripts/fetch_statcast_pitch_log.py
======================================
Hitter Projection Engine -- Phase 2 raw per-pitch ingestion, I/O shell.

For each gamePk not already archived (lib.research.statcast_pitch_store.
has_game()), fetches that game's per-pitch log via the Vercel
api/savantpitches endpoint and ingests it into the raw archive. Already-
archived games are skipped without a network call -- this is what makes
a normal slate run NOT redownload a player's entire pitch history: only
today's (and any other not-yet-seen) gamePks are ever fetched.

gamePks come from data/slate.json's games (each carries `gameId`, which
is the same MLB gamePk every other part of this pipeline already uses)
by default, or can be passed explicitly for a backfill run.

Non-fatal by design (matches scripts/build_projection_board.py's own
posture): a single game's fetch failure is reported and skipped, never
raises, never blocks the rest of the run or the pipeline it's part of.
"""
import json
import os
import sys
import time
import urllib.request

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPTS_DIR)
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from lib.research.statcast_pitch_store import has_game, ingest_game_pitches  # noqa: E402

VERCEL_BASE = 'https://edge-finder-api.vercel.app'
DEFAULT_SLATE_PATH = os.path.join('data', 'slate.json')


def fetch_json(url, timeout=55):
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception as e:
        print(f'  fetch error: {e}')
        return None


def game_pks_from_slate(slate_path=DEFAULT_SLATE_PATH):
    try:
        with open(slate_path) as f:
            slate = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []
    return [g.get('gameId') for g in (slate.get('games') or []) if g.get('gameId')]


def fetch_and_ingest_game(game_pk, vercel_base=VERCEL_BASE):
    data = fetch_json(f'{vercel_base}/api/savantpitches?gamePk={game_pk}')
    if not data or not data.get('ok'):
        return {'gamePk': game_pk, 'status': 'FETCH_FAILED', 'error': (data or {}).get('error')}
    pitches = data.get('pitches') or []
    if not pitches:
        return {'gamePk': game_pk, 'status': 'NO_PITCHES'}
    summary = ingest_game_pitches(game_pk, pitches)
    summary['status'] = 'INGESTED'
    return summary


def main(game_pks=None, slate_path=None, force=False, sleep_between=0.0):
    game_pks = game_pks if game_pks is not None else game_pks_from_slate(slate_path or DEFAULT_SLATE_PATH)
    results = []
    for game_pk in game_pks:
        if not force and has_game(game_pk):
            results.append({'gamePk': game_pk, 'status': 'ALREADY_ARCHIVED'})
            continue
        results.append(fetch_and_ingest_game(game_pk))
        if sleep_between:
            time.sleep(sleep_between)

    summary = {
        'totalGames': len(game_pks),
        'alreadyArchived': sum(1 for r in results if r['status'] == 'ALREADY_ARCHIVED'),
        'ingested': sum(1 for r in results if r['status'] == 'INGESTED'),
        'fetchFailed': sum(1 for r in results if r['status'] == 'FETCH_FAILED'),
        'noPitches': sum(1 for r in results if r['status'] == 'NO_PITCHES'),
        'results': results,
    }
    return summary


if __name__ == '__main__':
    arg_pks = [int(x) for x in sys.argv[1:]] if len(sys.argv) > 1 else None
    result = main(game_pks=arg_pks)
    print(json.dumps(result, indent=2))
