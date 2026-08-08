#!/usr/bin/env python3
"""
scripts/fetch_bullpen_usage.py -- v1.0

Fetches RECENT bullpen usage (previous-day usage, back-to-back
appearances, recent pitch counts, high-leverage save/hold workload, and
handedness mix) for all 30 MLB teams, and merges it into
data/bullpen.json under each team's own `recentUsage` block -- the same
file scripts/fetch_savant_bullpen_hl.py already merges its own
`hlXFIP`/`hlGrade` (season-aggregate, quality-only) fields into.

data/bullpen.json (fed by api/bullpen.js + this script) previously only
ever described SEASON-LONG bullpen quality -- nothing said whether
today's bullpen is actually rested and available. This script closes
that specific gap using data already fetchable from the MLB Stats API,
reusing the exact same schedule + boxscore endpoints/patterns
scripts/fetch_opp_quality.py already uses for starter identification
(fetch_recent_games/fetch_actual_starter) -- see
lib/edgelab/bullpen_usage.py for the pure parsing logic and the network
adapters this script calls.

Runs directly in GitHub Actions (same execution model as
fetch_opp_quality.py -- no Vercel timeout constraint), immediately after
"Fetch high-leverage bullpen xFIP splits" in fetch-slate.yml, so
data/bullpen.json has both fields before scripts/enrich_data.py merges
it into the slate.

Data/context only: writes ONLY to data/bullpen.json. Never touches
data/slate.json, bets.json, or any recommendation/settlement/ledger
file -- see tests/test_fetch_bullpen_usage.py.
"""
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.edgelab.bullpen_usage import (
    MLB_TEAM_ID_MAP,
    extract_completed_games_for_team,
    extract_relief_appearances,
    fetch_team_boxscore,
    fetch_team_recent_schedule,
    summarize_team_bullpen_usage,
)

WINDOW_DAYS = 3          # calendar days back -- enough buffer for a normal off-day
MAX_GAMES_CONSIDERED = 2  # only the last 2 completed games matter for back-to-back
MAX_WORKERS = 6           # same parallelism as scripts/fetch_opp_quality.py
BATCH_PAUSE = 0.5         # seconds between batches -- be polite to MLB API


def compute_team_recent_usage(abbr, team_id):
    today = datetime.utcnow().date()
    end_date = today - timedelta(days=1)
    start_date = today - timedelta(days=WINDOW_DAYS)
    fmt = lambda d: d.strftime('%Y-%m-%d')

    schedule = fetch_team_recent_schedule(team_id, fmt(start_date), fmt(end_date))
    completed = extract_completed_games_for_team(schedule, team_id)
    recent = completed[-MAX_GAMES_CONSIDERED:]

    games_with_appearances = []
    for g in recent:
        boxscore = fetch_team_boxscore(g['gamePk'])
        appearances = extract_relief_appearances(boxscore, g['side'])
        games_with_appearances.append({'date': g['date'], 'appearances': appearances})

    return abbr, summarize_team_bullpen_usage(games_with_appearances)


def main():
    start = time.time()
    print(f'Fetching recent bullpen usage for {len(MLB_TEAM_ID_MAP)} teams ({MAX_WORKERS} parallel)...')

    results = {}
    abbrs = list(MLB_TEAM_ID_MAP.keys())
    for i in range(0, len(abbrs), MAX_WORKERS):
        batch = abbrs[i:i + MAX_WORKERS]
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
            futures = {ex.submit(compute_team_recent_usage, a, MLB_TEAM_ID_MAP[a]): a for a in batch}
            for future in as_completed(futures):
                abbr, usage = future.result()
                results[abbr] = usage
                if usage['dataAvailable']:
                    print(f"  {abbr}: {usage['gamesConsidered']} game(s), "
                          f"{len(usage['recentPitchCounts'])} reliever(s) used, "
                          f"{len(usage['backToBackRelievers'])} back-to-back")
                else:
                    print(f"  {abbr}: unavailable ({usage['unavailableReason']})")
        if i + MAX_WORKERS < len(abbrs):
            time.sleep(BATCH_PAUSE)

    try:
        with open('data/bullpen.json') as f:
            bullpen = json.load(f)
    except Exception as e:
        print(f'Could not load bullpen.json: {e}')
        return

    bullpens = bullpen.setdefault('bullpens', {})
    merged = 0
    for abbr, usage in results.items():
        if abbr not in bullpens:
            continue
        bullpens[abbr]['recentUsage'] = usage
        merged += 1

    available = sum(1 for u in results.values() if u['dataAvailable'])
    bullpen['bullpenUsageFetchedAt'] = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
    bullpen['bullpenUsageAvailableCount'] = available

    with open('data/bullpen.json', 'w') as f:
        json.dump(bullpen, f)

    elapsed = round(time.time() - start, 1)
    print(f'\nDone in {elapsed}s -- {merged} team(s) merged, {available}/{len(results)} with usable recent-usage data')


if __name__ == '__main__':
    main()
