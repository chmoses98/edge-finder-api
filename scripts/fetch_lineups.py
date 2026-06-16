"""
scripts/fetch_lineups.py — v2.0
Fetches confirmed starting lineups from MLB Stats API boxscore endpoint
and computes lineupWOBADelta + lineupAdj for each team in slate.json.

Changes from v1.0:
- Delta now computed vs team's own season xwOBA (not league average)
  Reason: league-average delta misstates the adjustment for above/below-average
  offenses. A .340 xwOBA team sitting their best hitters looks neutral vs league
  avg but is a meaningful downgrade vs their own baseline.
- lineupConfirmed flag added (True/False) — downstream logic gates TT bets on this
- lineupAdj field added: R/G adjustment ready to apply directly to offense_baseline
  Formula: lineupAdj = lineupWOBADelta * 4.5
  (wOBA delta * 4.5 converts wOBA gap to expected R/G change, per MODEL_CORE Section 1 Step 2)
- lineupBattersResolved added: count of batters with real xwOBA data (out of 9)
- Requires savant_team.json (fetched by fetch_savant_team.py) and teamstats.json
"""

import json
import time
import urllib.request

SEASON = '2026'
MIN_BATTERS_FOR_CONFIRMED = 6  # need at least 6/9 xwOBA values to apply adjustment
WOBA_TO_RPG_SCALAR = 4.5       # MODEL_CORE Section 1 Step 2: wOBA delta * 4.5 = R/G adj
LINEUP_ADJ_CAP = 0.25          # cap at ±0.25 R/G per MODEL_CORE

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
    """Load individual batter xwOBA from savant_team.json (keyed by player_id string)."""
    try:
        with open('data/savant_team.json') as f:
            savant = json.load(f)
        batters = savant.get('batters', {})
        print(f'Loaded {len(batters)} batter xwOBA values from savant_team.json')
        return batters
    except Exception as e:
        print(f'WARNING: Could not load savant_team.json: {e}')
        return {}

def load_team_woba():
    """Load team season xwOBA from savant_team.json (keyed by abbr)."""
    try:
        with open('data/savant_team.json') as f:
            savant = json.load(f)
        teams = savant.get('teams', {})
        # Build abbr -> xwoba map
        team_woba = {}
        for abbr, data in teams.items():
            xw = data.get('xwoba')
            if xw is not None:
                team_woba[abbr] = float(xw)
        print(f'Loaded season xwOBA for {len(team_woba)} teams')
        return team_woba
    except Exception as e:
        print(f'WARNING: Could not load team xwOBA from savant_team.json: {e}')
        return {}

POSITIONAL_WOBA = {
    'C': 0.305, '1B': 0.335, '2B': 0.315, '3B': 0.325,
    'SS': 0.310, 'LF': 0.330, 'RF': 0.330, 'CF': 0.315,
    'DH': 0.340, 'P': 0.145,
}
LEAGUE_AVG_WOBA = 0.318

def get_positional_fallback(player_data):
    """Return positional average wOBA when individual xwOBA unavailable."""
    pos = player_data.get('position', {}).get('abbreviation', '')
    return POSITIONAL_WOBA.get(pos, LEAGUE_AVG_WOBA)

def fetch_lineup_for_game(game_pk, away_abbr, home_abbr, batter_woba_map, team_woba_map):
    """
    Fetch confirmed lineups for a game via MLB Stats API boxscore.
    Returns dict with away/home lineup data, or None if lineups not posted.

    lineupWOBADelta = confirmed_lineup_avg_xwOBA - team_season_xwOBA
    lineupAdj = lineupWOBADelta * WOBA_TO_RPG_SCALAR (capped at ±0.25 R/G)
    lineupConfirmed = True if battingOrder is present in boxscore
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
                # Lineup not yet posted
                result[side] = {
                    # Legacy
                    'lineupConfirmed': False,
                    'lineupWOBADelta': None,
                    'lineupAdj': None,
                    # Phase 1B: Separated fields
                    'lineupPosted':              False,
                    'lineupStatus':              'missing',
                    'lineupConfirmedOfficial':   False,
                    'lineupSource':              'mlb_stats_api',
                    'lineupBattersExpected':     9,
                    'lineupBattersFound':        0,
                    'lineupBattersResolved':     0,
                    'lineupAdjAvailable':        False,
                    'lineupAdjApplied':          False,
                    'lineupDataQuality':         'none',
                    'lineupStatusReason':        'Batting order not yet posted by MLB Stats API',
                }
                continue

            # Collect xwOBA for each batter in the lineup
            lineup_wobas = []
            real_data_count = 0
            fallback_count = 0

            for player_id in batters_order[:9]:
                pid = str(player_id)
                player_data = players.get(f'ID{pid}', {})

                xwoba = batter_woba_map.get(pid)
                if xwoba is not None:
                    lineup_wobas.append(float(xwoba))
                    real_data_count += 1
                else:
                    # Use positional fallback rather than skipping
                    fallback = get_positional_fallback(player_data)
                    lineup_wobas.append(fallback)
                    fallback_count += 1

            if len(lineup_wobas) < 1:
                result[side] = {
                    'lineupConfirmed': False,
                    'lineupWOBADelta': None,
                    'lineupAdj': None,
                    'lineupBattersResolved': 0,
                }
                continue

            lineup_avg_woba = sum(lineup_wobas) / len(lineup_wobas)

            # Delta vs team's OWN season xwOBA (not league average)
            team_season_woba = team_woba_map.get(abbr)
            if team_season_woba is None:
                # Fall back to league average if team data missing
                team_season_woba = LEAGUE_AVG_WOBA
                print(f'  WARNING: No season xwOBA for {abbr} — using league avg {LEAGUE_AVG_WOBA}')

            raw_delta = lineup_avg_woba - team_season_woba
            # R/G adjustment: wOBA delta * 4.5 scalar, capped at ±0.25
            lineup_adj = max(-LINEUP_ADJ_CAP, min(LINEUP_ADJ_CAP, raw_delta * WOBA_TO_RPG_SCALAR))
            lineup_adj = round(lineup_adj, 3)
            raw_delta = round(raw_delta, 4)

            # Only mark confirmed if we have enough real data
            confirmed = real_data_count >= MIN_BATTERS_FOR_CONFIRMED

            # Phase 1B: Separated lineup fields
            # lineupPosted: battingOrder returned by API (independent of xwOBA resolution)
            lineup_posted = True  # we reached here, so battingOrder was present
            # lineupConfirmedOfficial: MLB Stats API returned battingOrder = official lineup
            # NOTE: MLB Stats API battingOrder presence IS official confirmation. This is
            # distinct from xwOBA data quality (whether we can compute lineup adjustments).
            lineup_confirmed_official = True  # battingOrder present = official

            adj_available = real_data_count >= MIN_BATTERS_FOR_CONFIRMED
            adj_applied   = adj_available  # we apply adj if available

            if adj_applied:
                data_quality = 'full' if real_data_count >= 8 else 'partial'
                status_reason = (
                    f'Official lineup confirmed, {real_data_count}/9 batters resolved for xwOBA adjustment'
                )
            else:
                data_quality = 'partial' if real_data_count > 0 else 'insufficient'
                status_reason = (
                    f'Official lineup confirmed but only {real_data_count}/9 batters resolved — '
                    f'lineup adjustment NOT applied (need {MIN_BATTERS_FOR_CONFIRMED}/9)'
                )

            result[side] = {
                # Legacy field (kept for backward compat with existing gates)
                'lineupConfirmed': confirmed,
                # Phase 1B: New separated fields
                'lineupPosted':              lineup_posted,
                'lineupStatus':              'confirmed',
                'lineupConfirmedOfficial':   lineup_confirmed_official,
                'lineupSource':              'mlb_stats_api',
                # NOTE: RotoWire/RotoGrinders sources not implemented — MLB Stats API
                # battingOrder is used as primary. Other sources would require paid API
                # access (RotoWire) or scraping (RotoGrinders), which is out of scope.
                # lineupSource='mlb_stats_api' when battingOrder present.
                'lineupBattersExpected':     9,
                'lineupBattersFound':        len(batters_order[:9]),
                'lineupBattersResolved':     real_data_count,
                'lineupAdjAvailable':        adj_available,
                'lineupAdjApplied':          adj_applied,
                'lineupDataQuality':         data_quality,
                'lineupStatusReason':        status_reason,
                # Legacy fields
                'lineupWOBADelta': raw_delta,
                'lineupAdj': lineup_adj if adj_applied else None,
                'lineupBattersFallback': fallback_count,
                'lineupAvgWOBA': round(lineup_avg_woba, 3),
                'teamSeasonWOBA': round(team_season_woba, 3),
            }

            if abs(raw_delta) > 0.005 or not confirmed:
                direction = '↑' if raw_delta > 0 else '↓'
                conf_str = 'CONFIRMED' if confirmed else f'PARTIAL ({real_data_count}/9 real)'
                print(f'  {abbr} lineup [{conf_str}]: avg_xwOBA={lineup_avg_woba:.3f} '
                      f'team_szn={team_season_woba:.3f} delta={raw_delta:+.4f} '
                      f'{direction} adj={lineup_adj:+.3f} R/G '
                      f'(real={real_data_count}, fallback={fallback_count})')

        except Exception as e:
            print(f'  Error processing {abbr} lineup: {e}')
            result[side] = {
                # Legacy
                'lineupConfirmed': False,
                'lineupWOBADelta': None,
                'lineupAdj': None,
                # Phase 1B: Separated fields
                'lineupPosted':              False,
                'lineupStatus':              'unknown',
                'lineupConfirmedOfficial':   False,
                'lineupSource':              'mlb_stats_api',
                'lineupBattersExpected':     9,
                'lineupBattersFound':        0,
                'lineupBattersResolved':     0,
                'lineupAdjAvailable':        False,
                'lineupAdjApplied':          False,
                'lineupDataQuality':         'none',
                'lineupStatusReason':        f'Error fetching lineup: {e}',
            }

    return result

def main():
    import time as t
    start = t.time()

    with open('data/slate.json') as f:
        slate = json.load(f)

    batter_woba = load_batter_woba()
    team_woba = load_team_woba()

    if not batter_woba:
        print('No batter wOBA data — lineup adjustments will be null for all games')

    games = slate.get('games', [])
    print(f'Fetching lineups for {len(games)} games...')

    confirmed_count = 0
    partial_count = 0
    missing_count = 0

    for game in games:
        game_pk   = game.get('gameId')
        away_abbr = game.get('away', {}).get('abbr', '')
        home_abbr = game.get('home', {}).get('abbr', '')

        if not game_pk:
            for side_key in ['awayTeamStats', 'homeTeamStats']:
                game.setdefault(side_key, {}).update({
                    'lineupConfirmed': False,
                    'lineupPosted': False,
                    'lineupStatus': 'missing',
                    'lineupConfirmedOfficial': False,
                    'lineupSource': 'mlb_stats_api',
                    'lineupBattersExpected': 9,
                    'lineupBattersFound': 0,
                    'lineupBattersResolved': 0,
                    'lineupAdjAvailable': False,
                    'lineupAdjApplied': False,
                    'lineupDataQuality': 'none',
                    'lineupStatusReason': 'No gameId available — cannot fetch lineup',
                    'lineupWOBADelta': None,
                    'lineupAdj': None,
                })
            missing_count += 2
            continue

        deltas = fetch_lineup_for_game(game_pk, away_abbr, home_abbr, batter_woba, team_woba)
        t.sleep(0.2)

        if not deltas:
            for side_key in ['awayTeamStats', 'homeTeamStats']:
                game.setdefault(side_key, {}).update({
                    'lineupConfirmed': False,
                    'lineupPosted': False,
                    'lineupStatus': 'missing',
                    'lineupConfirmedOfficial': False,
                    'lineupSource': 'mlb_stats_api',
                    'lineupBattersExpected': 9,
                    'lineupBattersFound': 0,
                    'lineupBattersResolved': 0,
                    'lineupAdjAvailable': False,
                    'lineupAdjApplied': False,
                    'lineupDataQuality': 'none',
                    'lineupStatusReason': 'MLB Stats API returned no data for this game',
                    'lineupWOBADelta': None,
                    'lineupAdj': None,
                })
            missing_count += 2
            continue

        for side_key, side_name in [('awayTeamStats', 'away'), ('homeTeamStats', 'home')]:
            d = deltas.get(side_name, {})
            game.setdefault(side_key, {}).update(d)

            if d.get('lineupConfirmed'):
                confirmed_count += 1
            elif d.get('lineupBattersResolved', 0) > 0:
                partial_count += 1
            else:
                missing_count += 1

    with open('data/slate.json', 'w') as f:
        json.dump(slate, f)

    elapsed = round(t.time() - start, 1)
    print(f'\nDone in {elapsed}s')
    print(f'  Confirmed (≥{MIN_BATTERS_FOR_CONFIRMED}/9 real xwOBA): {confirmed_count}')
    print(f'  Partial (<{MIN_BATTERS_FOR_CONFIRMED}/9 real xwOBA, adj not applied): {partial_count}')
    print(f'  Missing (lineup not posted): {missing_count}')
    print(f'  lineupAdj applied only when lineupConfirmed=True')

    # Phase 1B: Generate lineup audit artifact
    _generate_lineup_audit(slate, games)

def _generate_lineup_audit(slate, games):
    """
    Phase 1B: Write lineup audit files:
      data/lineup_audit_YYYY-MM-DD.json
      data/lineup_audit_YYYY-MM-DD.csv
    """
    import os, csv
    from datetime import datetime, timezone
    
    today = slate.get('date', datetime.now(tz=timezone.utc).strftime('%Y-%m-%d'))
    if not today:
        today = datetime.now(tz=timezone.utc).strftime('%Y-%m-%d')
    
    audit_rows = []
    for game in games:
        away = game.get('away', {})
        home  = game.get('home', {})
        away_name = away.get('team', away.get('abbr', '?'))
        home_name  = home.get('team',  home.get('abbr', '?'))
        game_label = f"{away.get('abbr','?')}@{home.get('abbr','?')}"
        
        for side_key, team_name in [('awayTeamStats', away_name), ('homeTeamStats', home_name)]:
            ts = game.get(side_key, {}) or {}
            row = {
                'date':                    today,
                'game':                    game_label,
                'team':                    team_name,
                'lineupStatus':            ts.get('lineupStatus', 'unknown'),
                'lineupConfirmedOfficial': ts.get('lineupConfirmedOfficial', False),
                'lineupSource':            ts.get('lineupSource', 'mlb_stats_api'),
                'lineupBattersExpected':   ts.get('lineupBattersExpected', 9),
                'lineupBattersFound':      ts.get('lineupBattersFound', 0),
                'lineupBattersResolved':   ts.get('lineupBattersResolved', 0),
                'lineupAdjAvailable':      ts.get('lineupAdjAvailable', False),
                'lineupAdjApplied':        ts.get('lineupAdjApplied', False),
                'lineupDataQuality':       ts.get('lineupDataQuality', 'none'),
                'lineupStatusReason':      ts.get('lineupStatusReason', ''),
                'reasonCodes':             '',
            }
            # Build reason codes
            rc = []
            if ts.get('lineupConfirmedOfficial'):
                rc.append('LINEUP_CONFIRMED_OFFICIAL')
            elif ts.get('lineupStatus') == 'projected':
                rc.append('LINEUP_PROJECTED_ONLY')
            elif ts.get('lineupStatus') == 'missing':
                rc.append('LINEUP_MISSING')
            if ts.get('lineupAdjApplied'):
                rc.append('LINEUP_ADJ_APPLIED')
            elif ts.get('lineupConfirmedOfficial') and not ts.get('lineupAdjAvailable'):
                rc.append('LINEUP_ADJ_UNAVAILABLE_BUT_OFFICIAL_CONFIRMED')
            elif not ts.get('lineupAdjAvailable'):
                rc.append('LINEUP_ADJ_UNAVAILABLE')
            row['reasonCodes'] = '|'.join(rc)
            audit_rows.append(row)
    
    os.makedirs('data', exist_ok=True)
    json_path = f'data/lineup_audit_{today}.json'
    csv_path  = f'data/lineup_audit_{today}.csv'
    
    with open(json_path, 'w') as f:
        import json
        json.dump({'date': today, 'generated_at': datetime.now(tz=timezone.utc).isoformat(),
                   'rows': audit_rows}, f, indent=2)
    
    if audit_rows:
        fieldnames = list(audit_rows[0].keys())
        with open(csv_path, 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(audit_rows)
    
    print(f'  Lineup audit written: {json_path} ({len(audit_rows)} rows)')

if __name__ == '__main__':
    main()
