"""
scripts/fetch_lineups.py — v1.0
NEW SCRIPT: Fetches confirmed starting lineups from MLB Stats API
and computes lineupWOBADelta for each team's offense block in slate.json.

lineupWOBADelta = (confirmed_lineup_xwOBA - team_season_xwOBA) / team_season_xwOBA
This is injected into offenseTeamStats.lineupWOBADelta and picked up by
slate.js projectRuns() to scale the offense baseline up or down.

Runs in GitHub Actions after fetch_savant_pitchers.py, 2+ hours before first pitch.
"""

import json
import time
import urllib.request
from datetime import datetime

SEASON = '2026'
LEAGUE_AVG_WOBA = 0.318  # MLB season avg xwOBA
# Minimum PA for a batter to be included in lineup wOBA calc
MIN_PA = 10

MLB_TEAM_ID_MAP = {
    'LAA':108,'ARI':109,'BAL':110,'BOS':111,'CHC':112,'CIN':113,'CLE':114,
    'COL':115,'DET':116,'HOU':117,'KC':118,'LAD':119,'WSH':120,'NYM':121,
    'ATH':133,'PIT':134,'SD':135,'SEA':136,'SF':137,'STL':138,'TB':139,
    'TEX':140,'TOR':141,'MIN':142,'PHI':143,'ATL':144,'CWS':145,'MIA':146,
    'NYY':147,'MIL':158,
}
MLB_ID_TO_ABBR = {v: k for k, v in MLB_TEAM_ID_MAP.items()}

def fetch_json(url, timeout=20):
    try:
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0',
            'Accept': 'application/json',
        })
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception as e:
        print(f'  fetch error: {e} | {url[:80]}')
        return None

def load_batter_woba():
    """Load individual batter xwOBA from savant_team.json (already fetched)."""
    try:
        with open('data/savant_team.json') as f:
            savant = json.load(f)
        batters = savant.get('batters', {})
        print(f'Loaded {len(batters)} batter xwOBA values from savant_team.json')
        return batters
    except Exception as e:
        print(f'WARNING: Could not load savant_team.json: {e}')
        return {}

def fetch_lineup_for_game(game_pk, away_abbr, home_abbr, batter_woba_map):
    """
    Fetch confirmed lineups for a game via MLB Stats API boxscore.
    Returns { away: lineupWOBADelta, home: lineupWOBADelta }
    or None if lineups not yet posted.
    """
    url = f'https://statsapi.mlb.com/api/v1/game/{game_pk}/boxscore'
    data = fetch_json(url, timeout=15)
    if not data:
        return None

    result = {}
    for side, abbr in [('away', away_abbr), ('home', home_abbr)]:
        try:
            team_data = data.get('teams', {}).get(side, {})
            batters_order = team_data.get('battingOrder', [])
            players = team_data.get('players', {})

            if not batters_order:
                result[side] = None
                continue

            # Get top 9 batters from batting order
            lineup_woba_values = []
            missing = 0
            for player_id in batters_order[:9]:
                pid = str(player_id)
                woba = batter_woba_map.get(pid)
                if woba is not None:
                    lineup_woba_values.append(float(woba))
                else:
                    missing += 1

            if len(lineup_woba_values) < 6:
                # Not enough batter xwOBA data to compute meaningful delta
                result[side] = None
                continue

            lineup_avg_woba = sum(lineup_woba_values) / len(lineup_woba_values)
            # Delta: how much better/worse is today's lineup vs league average
            # Positive = better than average (boost offense), negative = worse
            delta = (lineup_avg_woba - LEAGUE_AVG_WOBA) / LEAGUE_AVG_WOBA
            result[side] = round(delta, 4)

            if abs(delta) > 0.03:
                direction = '↑' if delta > 0 else '↓'
                print(f'  {abbr} lineup: avg xwOBA={lineup_avg_woba:.3f} '
                      f'delta={delta:+.3f} {direction} '
                      f'({len(lineup_woba_values)}/9 batters resolved, {missing} missing)')

        except Exception as e:
            result[side] = None

    return result

def main():
    start = time.time()

    with open('data/slate.json') as f:
        slate = json.load(f)

    batter_woba = load_batter_woba()
    if not batter_woba:
        print('No batter wOBA data available — lineup adjustment will be null for all games')

    games = slate.get('games', [])
    print(f'Fetching lineups for {len(games)} games...')

    lineup_resolved = 0
    lineup_missing  = 0

    for game in games:
        game_pk   = game.get('gameId')
        away_abbr = game.get('away', {}).get('abbr', '')
        home_abbr = game.get('home', {}).get('abbr', '')

        if not game_pk:
            lineup_missing += 2
            continue

        deltas = fetch_lineup_for_game(game_pk, away_abbr, home_abbr, batter_woba)
        time.sleep(0.2)  # polite to MLB API

        if not deltas:
            game.setdefault('awayTeamStats', {})['lineupWOBADelta'] = None
            game.setdefault('homeTeamStats', {})['lineupWOBADelta'] = None
            lineup_missing += 2
            continue

        for side_key, side_name in [('awayTeamStats', 'away'), ('homeTeamStats', 'home')]:
            delta = deltas.get(side_name)
            game.setdefault(side_key, {})['lineupWOBADelta'] = delta
            if delta is not None:
                lineup_resolved += 1
            else:
                lineup_missing += 1

    with open('data/slate.json', 'w') as f:
        json.dump(slate, f)

    elapsed = round(time.time() - start, 1)
    print(f'\nDone in {elapsed}s — lineup wOBA delta: {lineup_resolved} resolved, {lineup_missing} null')
    print(f'Lineups not yet posted will be null (offense baseline used unchanged).')

if __name__ == '__main__':
    main()
