import json

# Load all data sources
with open('data/odds.json') as f:
    odds = json.load(f)
with open('data/slate.json') as f:
    slate = json.load(f)

# Load Kalshi native ML data
try:
    with open('data/kalshi_raw.json') as f:
        kalshi_raw = json.load(f)
    kalshi_ml_games = kalshi_raw.get('games', [])
except:
    kalshi_ml_games = []

# Load Kalshi search data (F5/TT/NRFI)
try:
    with open('data/kalshi_search.json') as f:
        kalshi_search = json.load(f)
    kalshi_search_results = kalshi_search.get('results', kalshi_search.get('markets', []))
except:
    kalshi_search_results = []

print(f'Kalshi ML markets: {len(kalshi_ml_games)}')
print(f'Kalshi search results: {len(kalshi_search_results)}')

# Build Kalshi ML lookup by away+home abbr pair
# kalshi_raw games have: awayTeam (abbr), homeTeam (abbr), americanOdds, mid, impliedPct
# The "YES" contract = away team wins (that's what Kalshi shows by default for winner markets)
kalshi_ml_by_game = {}
for g in kalshi_ml_games:
    away = g.get('awayTeam', '')
    home = g.get('homeTeam', '')
    if not away or not home:
        continue
    key = f'{away}{home}'
    if key not in kalshi_ml_by_game:
        kalshi_ml_by_game[key] = {'away_markets': [], 'home_markets': []}
    ticker = g.get('ticker', '')
    american = g.get('americanOdds')
    implied = g.get('impliedPct')
    # Tickers end in -AWAY or -HOME abbr
    if ticker.endswith(f'-{away}'):
        kalshi_ml_by_game[key]['away_markets'].append({'american': american, 'implied': implied, 'ticker': ticker})
    elif ticker.endswith(f'-{home}'):
        kalshi_ml_by_game[key]['home_markets'].append({'american': american, 'implied': implied, 'ticker': ticker})

print(f'Kalshi ML game keys: {list(kalshi_ml_by_game.keys())[:8]}')

# Build Kalshi search lookup by game key
# Search results have ticker, title, seriesTicker, awayTeam, homeTeam
kalshi_extra_by_game = {}
for r in kalshi_search_results:
    away = r.get('awayTeam', '')
    home = r.get('homeTeam', '')
    if not away or not home:
        continue
    key = f'{away}{home}'
    if key not in kalshi_extra_by_game:
        kalshi_extra_by_game[key] = []
    kalshi_extra_by_game[key].append(r)

print(f'Kalshi search game keys: {list(kalshi_extra_by_game.keys())[:8]}')

def normalize(name):
    return name.lower().replace(' ', '').replace('.', '').replace('-', '')

FULL_TO_ABBR = {
    'detroit tigers': 'DET', 'tampa bay rays': 'TB', 'san diego padres': 'SD',
    'philadelphia phillies': 'PHI', 'baltimore orioles': 'BAL', 'boston red sox': 'BOS',
    'miami marlins': 'MIA', 'washington nationals': 'WSH', 'cleveland guardians': 'CLE',
    'new york yankees': 'NYY', 'kansas city royals': 'KC', 'cincinnati reds': 'CIN',
    'toronto blue jays': 'TOR', 'atlanta braves': 'ATL', 'chicago white sox': 'CWS',
    'minnesota twins': 'MIN', 'san francisco giants': 'SF', 'milwaukee brewers': 'MIL',
    'texas rangers': 'TEX', 'st. louis cardinals': 'STL', 'athletics': 'ATH',
    'chicago cubs': 'CHC', 'pittsburgh pirates': 'PIT', 'houston astros': 'HOU',
    'colorado rockies': 'COL', 'los angeles angels': 'LAA', 'los angeles dodgers': 'LAD',
    'arizona diamondbacks': 'AZ', 'new york mets': 'NYM', 'seattle mariners': 'SEA',
    'oakland athletics': 'ATH',
}

def to_abbr(full_name):
    return FULL_TO_ABBR.get(full_name.lower(), full_name[:3].upper())

def vig_free(away_american, home_american):
    if away_american is None or home_american is None:
        return None, None
    def to_imp(o):
        return 100/(o+100) if o > 0 else abs(o)/(abs(o)+100)
    ia, ih = to_imp(away_american), to_imp(home_american)
    tot = ia + ih
    return round(ia/tot*10000)/100, round(ih/tot*10000)/100

odds_games = odds.get('games', [])
matched = 0
unmatched = []

for game in slate.get('games', []):
    away_abbr = game.get('away', {}).get('abbr', '')
    home_abbr = game.get('home', {}).get('abbr', '')
    away_full = game.get('away', {}).get('team', '')
    home_full = game.get('home', {}).get('team', '')

    # Match to Odds API game
    best = None
    for entry in odds_games:
        api_away = normalize(entry['awayTeam'])
        api_home = normalize(entry['homeTeam'])
        sa = normalize(away_full or away_abbr)
        sh = normalize(home_full or home_abbr)
        if (sa in api_away or api_away in sa or away_abbr.lower() in api_away) and \
           (sh in api_home or api_home in sh or home_abbr.lower() in api_home):
            best = entry
            break

    if not best:
        unmatched.append(f'{away_abbr}@{home_abbr}')
        continue

    # Base odds from Odds API
    game['odds']               = best.get('books', {})
    game['pinnacleVF']         = best.get('pinnacleVF')
    game['kalshiVF']           = best.get('kalshiVF')
    game['pinnacleF5VF']       = best.get('pinnacleF5VF')
    game['kalshiF5VF']         = best.get('kalshiF5VF')
    game['oddsApiEventId']     = best.get('eventId')
    game['oddsApiCommenceTime']= best.get('commenceTime')
    game.pop('kalshi', None)
    game.pop('pinVigFree', None)

    # Now inject Kalshi native data
    # Use the abbr from Odds API to form Kalshi key
    away_k = to_abbr(best['awayTeam'])
    home_k = to_abbr(best['homeTeam'])
    kalshi_key = f'{away_k}{home_k}'

    kalshi_books = game['odds'].setdefault('kalshi', {})

    # ML from Kalshi native
    kal_ml = kalshi_ml_by_game.get(kalshi_key, {})
    away_mkts = kal_ml.get('away_markets', [])
    home_mkts = kal_ml.get('home_markets', [])
    if away_mkts or home_mkts:
        # Best by volume isn't available here — just take first
        away_american = away_mkts[0]['american'] if away_mkts else None
        home_american = home_mkts[0]['american'] if home_mkts else None
        # Only inject if Odds API didn't already get it
        if not kalshi_books.get('ml', {}).get('away'):
            kalshi_books['ml'] = {'away': away_american, 'home': home_american, 'source': 'kalshi_native'}
        # Compute VF from native if needed
        if not game.get('kalshiVF') and away_american and home_american:
            vf_a, vf_h = vig_free(away_american, home_american)
            game['kalshiVF'] = {'away': vf_a, 'home': vf_h}

    # F5/TT/NRFI from Kalshi search
    extra = kalshi_extra_by_game.get(kalshi_key, [])
    for r in extra:
        title = (r.get('title') or '').lower()
        american = r.get('americanOdds') or r.get('american')
        implied = r.get('impliedPct')

        if 'first 5' in title or '5 innings' in title or 'f5' in title:
            if not kalshi_books.get('f5ml'):
                kalshi_books['f5ml'] = {'away': None, 'home': None, 'source': 'kalshi_search'}
            ticker = r.get('ticker', '')
            if ticker.endswith(f'-{away_k}'):
                kalshi_books['f5ml']['away'] = american
            elif ticker.endswith(f'-{home_k}'):
                kalshi_books['f5ml']['home'] = american

        elif 'nrfi' in title or 'yrfi' in title or 'first inning' in title or '1st inning' in title:
            if not kalshi_books.get('nrfi'):
                kalshi_books['nrfi'] = {'nrfi': None, 'yrfi': None, 'source': 'kalshi_search'}
            if 'no run' in title or 'nrfi' in title:
                kalshi_books['nrfi']['nrfi'] = american
            elif 'yrfi' in title or 'run scored' in title:
                kalshi_books['nrfi']['yrfi'] = american

        elif 'total' in title and ('over' in title or 'under' in title or '+' in title):
            import re
            line_m = re.search(r'(\d+\.?\d*)', title)
            line = float(line_m.group(1)) if line_m else None
            is_over = 'over' in title or '+' in title
            is_under = 'under' in title
            # Team total if team name mentioned
            if away_k.lower() in title or away_full.split()[-1].lower() in title:
                if not kalshi_books.get('teamTotals'):
                    kalshi_books['teamTotals'] = {'away': {}, 'home': {}}
                if is_over:
                    kalshi_books['teamTotals']['away']['over'] = american
                    kalshi_books['teamTotals']['away']['line'] = line
                elif is_under:
                    kalshi_books['teamTotals']['away']['under'] = american
            elif home_k.lower() in title or home_full.split()[-1].lower() in title:
                if not kalshi_books.get('teamTotals'):
                    kalshi_books['teamTotals'] = {'away': {}, 'home': {}}
                if is_over:
                    kalshi_books['teamTotals']['home']['over'] = american
                    kalshi_books['teamTotals']['home']['line'] = line
                elif is_under:
                    kalshi_books['teamTotals']['home']['under'] = american
            else:
                # Game total
                if not kalshi_books.get('total'):
                    kalshi_books['total'] = {}
                if is_over:
                    kalshi_books['total']['over'] = american
                    kalshi_books['total']['line'] = line
                elif is_under:
                    kalshi_books['total']['under'] = american

    # Recompute Kalshi F5 VF if we have it now
    f5 = kalshi_books.get('f5ml', {})
    if f5.get('away') and f5.get('home') and not game.get('kalshiF5VF'):
        vf_a, vf_h = vig_free(f5['away'], f5['home'])
        game['kalshiF5VF'] = {'away': vf_a, 'home': vf_h}

    game['kalshiKey'] = kalshi_key
    matched += 1

with open('data/slate.json', 'w') as f:
    json.dump(slate, f)

# Summary
ml=rl=tot=f5=tt=nrfi=0
for game in slate.get('games', []):
    kal = game.get('odds', {}).get('kalshi', {})
    if kal.get('ml', {}).get('away'): ml+=1
    if kal.get('rl', {}).get('away'): rl+=1
    if kal.get('total', {}).get('line'): tot+=1
    if kal.get('f5ml', {}).get('away'): f5+=1
    if kal.get('teamTotals', {}).get('away', {}).get('over'): tt+=1
    if kal.get('nrfi', {}).get('nrfi'): nrfi+=1

n = len(slate.get('games', []))
print(f'Merged: {matched}/{n} games (unmatched: {unmatched})')
print(f'Kalshi: ML={ml} RL={rl} Total={tot} F5={f5} TT={tt} NRFI={nrfi}')
