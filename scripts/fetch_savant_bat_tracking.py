#!/usr/bin/env python3
"""
scripts/fetch_savant_bat_tracking.py
=======================================
Hitter Projection Engine -- Phase 2 bat-tracking ingestion, I/O shell.

Calls the Vercel api/savantbattracking endpoint (see that file's
docstring for the column-name-verification caveat) and appends one
dated snapshot per resolved batter to data/bat_tracking_history.jsonl
via lib.research.bat_tracking_store -- never overwrites a prior date's
snapshot, so trend comparisons have real history.

Non-fatal by design: a fetch failure (including "Savant blocked this
request", which every other Savant fetcher in this repo already
documents as a live possibility) is reported and the script exits 0
with zero snapshots written, exactly like every other Savant-dependent
script in this repo already tolerates.
"""
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPTS_DIR)
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from lib.research.bat_tracking_store import record_snapshot, ingest_snapshots  # noqa: E402

VERCEL_BASE = 'https://edge-finder-api.vercel.app'


def fetch_json(url, timeout=55):
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception as e:
        print(f'  fetch error: {e}')
        return None


def main(as_of_date=None, year='2026', vercel_base=VERCEL_BASE):
    as_of_date = as_of_date or datetime.now(tz=timezone.utc).strftime('%Y-%m-%d')
    data = fetch_json(f'{vercel_base}/api/savantbattracking?year={year}')
    if not data or not data.get('ok'):
        print(f'  savant bat-tracking fetch failed: {data}')
        return {'status': 'FETCH_FAILED', 'asOfDate': as_of_date, 'snapshotsWritten': 0,
                'error': (data or {}).get('error')}

    fetched_at = data.get('fetchedAt') or datetime.now(tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    batters = data.get('batters') or {}
    rows = [record_snapshot(pid, as_of_date, fetched_at, fields) for pid, fields in batters.items()]
    written, skipped = ingest_snapshots(rows)

    resolved_field_count = data.get('resolvedFieldCount', 0)
    return {
        'status': 'OK',
        'asOfDate': as_of_date,
        'battersInResponse': len(batters),
        'snapshotsWritten': written,
        'snapshotsSkipped': skipped,
        'resolvedFieldCount': resolved_field_count,
        'columnNamesVerifiedLive': resolved_field_count > 0,
    }


if __name__ == '__main__':
    result = main()
    print(json.dumps(result, indent=2))
