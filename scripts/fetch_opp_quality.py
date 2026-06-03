"""
scripts/fetch_opp_quality.py — v1.0

Computes rolling opponent starter quality for all 30 MLB teams.
Runs directly in GitHub Actions (no Vercel timeout constraint).

For each team:
  1. Fetch last 21 calendar days of completed games from MLB Stats API
  2. Identify opposing starter via probablePitcher (completed games keep this populated)
     Fallback: boxscore pitchers[] array for games where probablePitcher is null
  3. Look up starter xFIP from Savant leaderboard (data/savant_pitchers.json if exists,
     else fetch inline)
  4. Fallback: compute season FIP from MLB Stats API for pitchers not on Savant leaderboard
  5. Average → oppXFIPavg, apply (avg - 4.00) * 0.08 adj, cap ±0.2

Output: data/oppquality.json
"""

import json
import time
import urllib.request
import urllib.error
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

LEAGUE_AVG_XFIP     = 4.00
WINDOW_DAYS         = 21       # calendar days back — captures ~15 real games reliably
MIN_GAMES_FOR_SIGNAL = 5       # below this, adj is set null (low confidence)
FIP_CONST           = 3.10
SEASON              = '2026'
MAX_WORKERS         = 6        # parallel team fetches
BATCH_PAUSE         = 0.5      # seconds between batches (be polite to MLB API)

MLB_TEAM_ID_MAP = {
    'LAA':108,'ARI':109,'BAL':110,'BOS':111,'CHC':112,'CIN':113,'CLE':114,
    'COL':115,'DET':116,'HOU':117,'KC':118,'LAD':119,'WSH':120,'NYM':121,
    'ATH':133,'PIT':134,'SD':135,'SEA':136,'SF':137,'STL':138,'TB':139,
    'TEX':140,'TOR':141,'MIN':142,'PHI':143,'ATL':144,'CWS':145,'MIA':146,
    'NYY':147,'MIL':158,
}
MLB_ID_TO_ABBR = {v: k for k, v in MLB_TEAM_ID_MAP.items()}

COMPLETED_STATUSES = {'Final', 'Game Over', 'Completed Early'}

# ── HTTP helper ───────────────────────────────────────────────────────────────
def fetch_json(url, timeout=20, retries=2):
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={
                'User-Agent': 'Mozilla/5.0 (compatible; edge-finder/1.0)',
                'Accept': 'application/json',
            })
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except Exception as e:
            if attempt == retries:
                return None
            time.sleep(1.5 * (attempt + 1))
    return None

def fetch_csv(url, timeout=20):
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode('utf-8')
    except Exception:
        return None

def parse_csv(text):
    if not text:
        return []
    lines = text.strip().split('\n')
    if len(lines) < 2:
        return []
    def split_line(line):
        result, current, in_quotes = [], '', False
        for ch in line:
            if ch == '"': in_quotes = not in_quotes
            elif ch == ',' and not in_quotes:
                result.append(current.strip()); current = ''
            else:
                current += ch
        result.append(current.strip())
        return result
    headers = split_line(lines[0])
    rows = []
    for line in lines[1:]:
        if not line.strip():
            continue
        values = split_line(line)
        rows.append({headers[i]: values[i] if i < len(values) else '' for i in range(len(headers))})
    return rows

def pf(val):
    try:
        n = float(val)
        return None if (n != n) else n  # catch NaN
    except (TypeError, ValueError):
        return None

# ── Step 1: Fetch Savant pitcher leaderboard ──────────────────────────────────
def fetch_savant_pitcher_xfips():
    print('Fetching Savant pitcher leaderboard...')
    url = (f'https://baseballsavant.mlb.com/leaderboard/custom?year={SEASON}&type=pitcher'
           f'&filter=&min=1&selections=k_percent,bb_percent,xera,hard_hit_percent'
           f'&chart=false&x=xera&y=xera&r=no&chartType=beeswarm&csv=true')
    text = fetch_csv(url)
    rows = parse_csv(text)
    fip_map = {}
    for row in rows:
        pid  = row.get('player_id', '').strip()
        xera = pf(row.get('xera'))
        kpct = pf(row.get('k_percent'))
        bbpct = pf(row.get('bb_percent'))
        if pid:
            fip_map[pid] = {'xera': xera, 'kPct': kpct, 'bbPct': bbpct}
    print(f'  Savant leaderboard: {len(fip_map)} pitchers loaded')
    return fip_map

# ── Step 2: Season FIP from MLB Stats API (fallback) ─────────────────────────
_fip_cache = {}

def fetch_pitcher_season_fip(pitcher_id):
    pid = str(pitcher_id)
    if pid in _fip_cache:
        return _fip_cache[pid]
    url = (f'https://statsapi.mlb.com/api/v1/people/{pid}/stats'
           f'?stats=season&group=pitching&season={SEASON}&gameType=R')
    data = fetch_json(url, timeout=10)
    if not data:
        _fip_cache[pid] = None
        return None
    try:
        s = data['stats'][0]['splits'][0]['stat']
        ip_raw = float(s.get('inningsPitched', 0))
        ip = int(ip_raw) + (ip_raw % 1) / 0.3 * 0.333
        if ip < 3:
            _fip_cache[pid] = None
            return None
        hr = int(s.get('homeRuns', 0))
        bb = int(s.get('baseOnBalls', 0))
        k  = int(s.get('strikeOuts', 0))
        fip = round((13 * hr + 3 * bb - 2 * k) / ip + FIP_CONST, 2)
        _fip_cache[pid] = fip
        return fip
    except (KeyError, IndexError, ZeroDivisionError, TypeError):
        _fip_cache[pid] = None
        return None

# ── Step 3: Fetch completed games for a team ──────────────────────────────────
def fetch_recent_games(team_id, window_days):
    today     = datetime.utcnow().date()
    end_date  = today - timedelta(days=1)
    start_date = today - timedelta(days=window_days)
    fmt = lambda d: d.strftime('%Y-%m-%d')

    url = (f'https://statsapi.mlb.com/api/v1/schedule?sportId=1&teamId={team_id}'
           f'&startDate={fmt(start_date)}&endDate={fmt(end_date)}'
           f'&gameType=R&hydrate=probablePitcher')
    data = fetch_json(url, timeout=15)
    if not data:
        return []

    games = []
    for dt in data.get('dates', []):
        for g in dt.get('games', []):
            status = g.get('status', {}).get('detailedState', '')
            if status not in COMPLETED_STATUSES:
                continue

            away_id   = g.get('teams', {}).get('away', {}).get('team', {}).get('id')
            home_id   = g.get('teams', {}).get('home', {}).get('team', {}).get('id')
            is_home   = (home_id == team_id)
            opp_side  = 'away' if is_home else 'home'
            opp_team  = g.get('teams', {}).get(opp_side, {})
            probable  = opp_team.get('probablePitcher')

            games.append({
                'gamePk':        g.get('gamePk'),
                'date':          dt.get('date'),
                'opp_side':      opp_side,
                'opp_starter_id':   str(probable['id'])   if probable else None,
                'opp_starter_name': probable.get('fullName') if probable else None,
            })

    # Return last 15 completed games
    return games[-15:]

# ── Step 4: Boxscore fallback for missing starters ────────────────────────────
def fetch_actual_starter(game_pk, opp_side):
    url  = f'https://statsapi.mlb.com/api/v1/game/{game_pk}/boxscore'
    data = fetch_json(url, timeout=12)
    if not data:
        return None
    try:
        pitchers = data['teams'][opp_side]['pitchers']
        if not pitchers:
            return None
        starter_id   = str(pitchers[0])
        players      = data['teams'][opp_side]['players']
        starter_data = players.get(f'ID{starter_id}', {})
        name         = starter_data.get('person', {}).get('fullName')
        return {'id': starter_id, 'name': name}
    except (KeyError, IndexError, TypeError):
        return None

# ── Step 5: Compute opp quality for one team ─────────────────────────────────
def compute_team_opp_quality(abbr, team_id, savant_fips):
    games = fetch_recent_games(team_id, WINDOW_DAYS)
    if not games:
        return abbr, {'oppXFIPavg': None, 'oppQualityAdj': None,
                      'gamesResolved': 0, 'gamesTotal': 0, 'confidence': 'low'}

    # Fill missing starters via boxscore (sequential — avoid hammering API)
    for game in games:
        if not game['opp_starter_id']:
            starter = fetch_actual_starter(game['gamePk'], game['opp_side'])
            if starter:
                game['opp_starter_id']   = starter['id']
                game['opp_starter_name'] = starter['name']

    # Resolve xFIP for each game
    xfip_values = []
    for game in games:
        pid = game.get('opp_starter_id')
        if not pid:
            continue
        # Primary: Savant xERA
        xfip = savant_fips.get(pid, {}).get('xera')
        # Fallback: MLB Stats API season FIP
        if xfip is None:
            xfip = fetch_pitcher_season_fip(pid)
        if xfip is not None:
            xfip_values.append(xfip)
            game['resolvedXFIP'] = xfip

    games_resolved = len(xfip_values)
    games_total    = len(games)

    if games_resolved < MIN_GAMES_FOR_SIGNAL:
        return abbr, {'oppXFIPavg': None, 'oppQualityAdj': None,
                      'gamesResolved': games_resolved, 'gamesTotal': games_total,
                      'confidence': 'low'}

    avg            = sum(xfip_values) / len(xfip_values)
    opp_xfip_avg   = round(avg, 2)
    raw_adj        = (opp_xfip_avg - LEAGUE_AVG_XFIP) * 0.08
    opp_quality_adj = round(max(-0.2, min(0.2, raw_adj)), 3)
    confidence     = 'full' if games_resolved >= 10 else 'partial'

    return abbr, {
        'oppXFIPavg':    opp_xfip_avg,
        'oppQualityAdj': opp_quality_adj,
        'gamesResolved': games_resolved,
        'gamesTotal':    games_total,
        'confidence':    confidence,
    }

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    start = time.time()
    savant_fips = fetch_savant_pitcher_xfips()

    print(f'Computing opp quality for {len(MLB_TEAM_ID_MAP)} teams ({MAX_WORKERS} parallel)...')
    results = {}

    # Process in batches of MAX_WORKERS
    abbrs = list(MLB_TEAM_ID_MAP.keys())
    for i in range(0, len(abbrs), MAX_WORKERS):
        batch = abbrs[i:i + MAX_WORKERS]
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
            futures = {ex.submit(compute_team_opp_quality, a, MLB_TEAM_ID_MAP[a], savant_fips): a for a in batch}
            for future in as_completed(futures):
                abbr, result = future.result()
                results[abbr] = result
                conf = result.get('confidence', 'low')
                resolved = result.get('gamesResolved', 0)
                adj = result.get('oppQualityAdj')
                adj_str = f'{adj:+.3f}' if adj is not None else 'n/a'
                print(f'  {abbr}: {resolved} games resolved ({conf}) → adj {adj_str}')
        if i + MAX_WORKERS < len(abbrs):
            time.sleep(BATCH_PAUSE)

    full_conf    = sum(1 for r in results.values() if r.get('confidence') == 'full')
    partial_conf = sum(1 for r in results.values() if r.get('confidence') == 'partial')
    low_conf     = sum(1 for r in results.values() if r.get('confidence') == 'low')

    output = {
        'ok':               True,
        'season':           SEASON,
        'fetchedAt':        datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
        'windowDays':       WINDOW_DAYS,
        'leagueAvgXFIP':    LEAGUE_AVG_XFIP,
        'summary': {
            'full':    full_conf,
            'partial': partial_conf,
            'low':     low_conf,
        },
        'teams': results,
    }

    with open('data/oppquality.json', 'w') as f:
        json.dump(output, f, indent=2)

    elapsed = round(time.time() - start, 1)
    print(f'\nDone in {elapsed}s — {full_conf} full, {partial_conf} partial, {low_conf} low confidence')
    print(f'Written to data/oppquality.json')

if __name__ == '__main__':
    main()
