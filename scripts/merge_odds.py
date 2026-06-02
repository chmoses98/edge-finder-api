import json

with open('data/odds.json') as f:
    odds = json.load(f)
with open('data/slate.json') as f:
    slate = json.load(f)

def normalize(name):
    return name.lower().replace(' ', '').replace('.', '').replace('-', '')

odds_games = odds.get('games', [])
matched = 0
unmatched = []

for game in slate.get('games', []):
    away_abbr = game.get('away', {}).get('abbr', '')
    home_abbr = game.get('home', {}).get('abbr', '')
    away_full = game.get('away', {}).get('team', '')
    home_full = game.get('home', {}).get('team', '')
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
    if best:
        game['odds']                 = best.get('books', {})
        game['pinnacleVF']           = best.get('pinnacleVF')
        game['kalshiVF']             = best.get('kalshiVF')
        game['pinnacleF5VF']         = best.get('pinnacleF5VF')
        game['kalshiF5VF']           = best.get('kalshiF5VF')
        game['oddsApiEventId']       = best.get('eventId')
        game['oddsApiCommenceTime']  = best.get('commenceTime')
        game.pop('kalshi', None)
        game.pop('pinVigFree', None)
        matched += 1
    else:
        unmatched.append(f'{away_abbr}@{home_abbr}')

with open('data/slate.json', 'w') as f:
    json.dump(slate, f)

print(f'Merged: {matched}/{len(slate.get("games",[]))} games matched')
if unmatched:
    print(f'Unmatched: {unmatched}')
