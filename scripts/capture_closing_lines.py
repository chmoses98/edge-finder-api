"""
scripts/capture_closing_lines.py

Captures Kalshi closing prices for today's open bets BEFORE games start.
Runs as part of the fetch-slate workflow (~game time).
Writes closingLine and closingLineSource = 'kalshi_live' to open bets in bets.json.

This solves the problem that The Odds API historical endpoint may not have 
Kalshi historical snapshots — by capturing the price in real-time.
"""

import json
import os
import time
import urllib.request
from datetime import datetime, timezone

ODDS_API_KEY = os.environ.get('ODDS_API_KEY', '')
BASE_URL     = 'https://api.the-odds-api.com/v4'
SPORT        = 'baseball_mlb'

def api_get(url):
    try:
        req = urllib.request.Request(url, headers={'Accept': 'application/json'})
        with urllib.request.urlopen(req, timeout=20) as r:
            remaining = r.headers.get('x-requests-remaining', '?')
            return json.loads(r.read()), remaining
    except Exception as e:
        print(f'  API error: {e}')
        return None, None

def american_to_prob(o):
    try:
        o = float(o)
        return 100/(o+100) if o >= 0 else abs(o)/(abs(o)+100)
    except: return None

TEAM_TO_ABBR = {
    'Arizona Diamondbacks':'ARI','Atlanta Braves':'ATL','Baltimore Orioles':'BAL',
    'Boston Red Sox':'BOS','Chicago Cubs':'CHC','Chicago White Sox':'CWS',
    'Cincinnati Reds':'CIN','Cleveland Guardians':'CLE','Colorado Rockies':'COL',
    'Detroit Tigers':'DET','Houston Astros':'HOU','Kansas City Royals':'KC',
    'Los Angeles Angels':'LAA','Los Angeles Dodgers':'LAD','Miami Marlins':'MIA',
    'Milwaukee Brewers':'MIL','Minnesota Twins':'MIN','New York Mets':'NYM',
    'New York Yankees':'NYY','Oakland Athletics':'ATH','Athletics':'ATH',
    'Las Vegas Athletics':'ATH','Philadelphia Phillies':'PHI','Pittsburgh Pirates':'PIT',
    'San Diego Padres':'SD','San Francisco Giants':'SF','Seattle Mariners':'SEA',
    'St. Louis Cardinals':'STL','Tampa Bay Rays':'TB','Texas Rangers':'TEX',
    'Toronto Blue Jays':'TOR','Washington Nationals':'WSH',
    'ARI':'ARI','ATL':'ATL','BAL':'BAL','BOS':'BOS','CHC':'CHC','CWS':'CWS',
    'CIN':'CIN','CLE':'CLE','COL':'COL','DET':'DET','HOU':'HOU','KC':'KC',
    'LAA':'LAA','LAD':'LAD','MIA':'MIA','MIL':'MIL','MIN':'MIN','NYM':'NYM',
    'NYY':'NYY','ATH':'ATH','PHI':'PHI','PIT':'PIT','SD':'SD','SF':'SF',
    'SEA':'SEA','STL':'STL','TB':'TB','TEX':'TEX','TOR':'TOR','WSH':'WSH',
}

def to_abbr(name):
    return TEAM_TO_ABBR.get(name.strip(), name.strip()[:3].upper())

def parse_game(game_str):
    sep = ' @ ' if ' @ ' in game_str else '@'
    parts = game_str.split(sep, 1)
    if len(parts) != 2: return None, None
    return to_abbr(parts[0].strip()), to_abbr(parts[1].strip())

def main():
    if not ODDS_API_KEY:
        print('ODDS_API_KEY not set — skipping closing line capture')
        return

    today_et = (datetime.now(timezone.utc)).strftime('%Y-%m-%d')
    print(f'Capturing Kalshi closing lines for {today_et}...')

    with open('bets.json') as f:
        bets = json.load(f)

    # Find today's open bets that don't yet have a closing line
    open_bets = [
        b for b in bets
        if b.get('date') == today_et
        and b.get('status') not in ('WIN','LOSS','PUSH','VOID','SETTLED')
        and b.get('closingLine') is None
        and b.get('market') in ('ML','F5 ML','Run Line','Total','Team Total')
    ]
    print(f'Open bets needing closing lines: {len(open_bets)}')
    if not open_bets: return

    # Fetch current Kalshi odds (live prices = closing prices before game starts)
    url = (f'{BASE_URL}/sports/{SPORT}/odds'
           f'?apiKey={ODDS_API_KEY}&bookmakers=kalshi'
           f'&markets=h2h,spreads,totals,team_totals,h2h_1st_5_innings'
           f'&oddsFormat=american')
    data, remaining = api_get(url)
    if not data:
        print('Failed to fetch Kalshi live odds')
        return
    print(f'Fetched {len(data)} games from Kalshi live | credits_remaining={remaining}')

    # Build game lookup: (away_abbr, home_abbr) -> game
    game_lookup = {}
    for g in data:
        away = to_abbr(g.get('away_team',''))
        home = to_abbr(g.get('home_team',''))
        game_lookup[(away, home)] = g

    MARKET_TO_API = {
        'ML':         'h2h',
        'F5 ML':      'h2h_1st_5_innings',
        'Run Line':   'spreads',
        'Total':      'totals',
        'Team Total': 'team_totals',
    }

    updated = 0
    for b in open_bets:
        away, home = parse_game(b.get('game',''))
        if not away: continue
        game = game_lookup.get((away, home))
        if not game:
            print(f'  NO_MATCH: {b.get("game")}')
            continue

        mkt_key = MARKET_TO_API.get(b.get('market',''))
        if not mkt_key: continue

        kalshi_bk = next((bk for bk in game.get('bookmakers',[]) if bk['key']=='kalshi'), None)
        if not kalshi_bk: continue

        mkt = next((m for m in kalshi_bk.get('markets',[]) if m['key']==mkt_key), None)
        if not mkt: continue

        outcomes = mkt.get('outcomes', [])
        bet_str = (b.get('bet') or b.get('betTeam') or '').upper()
        bet_side = (b.get('betSide') or '').upper()

        closing_price = None
        if mkt_key == 'h2h' or mkt_key == 'h2h_1st_5_innings':
            is_away = 'AWAY' in bet_side or (away in bet_str)
            for o in outcomes:
                if (to_abbr(o['name']) == away) == is_away:
                    closing_price = o['price']
                    break
        elif mkt_key == 'totals':
            is_over = 'OVER' in bet_str
            for o in outcomes:
                if ('Over' in o['name']) == is_over:
                    closing_price = o['price']
                    break
        elif mkt_key == 'team_totals':
            is_away = 'AWAY' in bet_side or (away in bet_str)
            is_over = 'OVER' in bet_str
            for o in outcomes:
                desc = (o.get('description','') or o.get('name','')).upper()
                o_away = away in desc
                o_over = 'OVER' in (o.get('name','') or '').upper()
                if o_away == is_away and o_over == is_over:
                    closing_price = o['price']
                    break

        if closing_price is not None:
            b['closingLine']          = closing_price
            b['closingLineSource']    = 'kalshi_live'
            b['closingLineTimestamp'] = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
            updated += 1
            print(f'  ✓ {b.get("game")} {b.get("market")} | closing={closing_price} (Kalshi live)')
        else:
            print(f'  ? {b.get("game")} {b.get("market")} | no Kalshi outcome matched')

    with open('bets.json', 'w') as f:
        json.dump(bets, f, indent=2)
    print(f'\nCapture complete: {updated}/{len(open_bets)} bets updated with Kalshi closing lines')

if __name__ == '__main__':
    main()
