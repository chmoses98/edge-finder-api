#!/usr/bin/env python3
"""
CLV Update Script
Pulls closing lines from The Odds API historical endpoint for all settled bets
where clv is null. Calculates vig-free CLV and writes back to bets.json and BET_LOG.md.

Markets supported via Odds API: ML, F5 ML, Total, Game Total, Run Line, RL, Team Total, TT
Markets NOT supported (flagged): YRFI, NRFI, K Prop, Pitcher Prop, Batter Prop
"""

import json, os, re, sys, time
from datetime import datetime, timezone
from urllib.request import urlopen, Request
from urllib.error import HTTPError, URLError

ODDS_API_KEY = os.environ.get('ODDS_API_KEY', '')
BASE_URL = 'https://api.the-odds-api.com/v4'
SPORT = 'baseball_mlb'

# Markets that can be fetched from Odds API historical endpoint
SUPPORTED_MARKETS = {
    'ML':         ('h2h',      'ml'),
    'F5 ML':      ('h2h_h1',   'ml'),
    'Total':      ('totals',   'total'),
    'Game Total': ('totals',   'total'),
    'Run Line':   ('spreads',  'rl'),
    'RL':         ('spreads',  'rl'),
    'Team Total': ('team_totals', 'tt'),
    'TT':         ('team_totals', 'tt'),
}

UNSUPPORTED_MARKETS = {'YRFI', 'NRFI', 'K Prop', 'Pitcher Prop', 'Batter Prop'}


def american_to_implied(price):
    """Convert American odds to implied probability (raw, with vig)."""
    if price is None:
        return None
    if price >= 100:
        return 100 / (price + 100)
    else:
        return abs(price) / (abs(price) + 100)


def vig_free_prob(price_a, price_b):
    """Return vig-free implied probabilities for a two-outcome market."""
    imp_a = american_to_implied(price_a)
    imp_b = american_to_implied(price_b)
    if imp_a is None or imp_b is None:
        return None, None
    total = imp_a + imp_b
    return round(imp_a / total * 100, 1), round(imp_b / total * 100, 1)


def fetch_historical_odds(date_str, markets):
    """
    Fetch historical odds from The Odds API for a given date.
    date_str: YYYY-MM-DD
    markets: comma-separated market keys e.g. 'h2h,spreads,totals,h2h_h1'
    Returns list of game objects or [].
    """
    # Use end of day ET as commenceTimeTo (23:59:59 ET = next day 04:59:59 UTC)
    commence_to = f"{date_str}T05:00:00Z"  # ~midnight ET
    # commenceTimeFrom = start of date
    commence_from = f"{date_str}T00:00:00Z"

    url = (
        f"{BASE_URL}/historical/sports/{SPORT}/odds"
        f"?apiKey={ODDS_API_KEY}"
        f"&regions=us"
        f"&markets={markets}"
        f"&oddsFormat=american"
        f"&bookmakers=lowvig,draftkings,fanduel,betmgm"
        f"&commenceTimeFrom={commence_from}"
        f"&commenceTimeTo={commence_to}"
        f"&date={commence_to}"  # snapshot at close of day
    )
    try:
        req = Request(url, headers={'Accept': 'application/json'})
        with urlopen(req, timeout=15) as resp:
            remaining = resp.headers.get('x-requests-remaining', '?')
            data = json.loads(resp.read())
            print(f"  Fetched historical {markets} for {date_str} | remaining: {remaining}")
            return data.get('data', data) if isinstance(data, dict) else data
    except HTTPError as e:
        body = e.read().decode()
        print(f"  HTTP {e.code} fetching historical {markets} for {date_str}: {body[:200]}")
        return []
    except (URLError, Exception) as e:
        print(f"  Error fetching historical odds: {e}")
        return []


def parse_game_key(game_str):
    """
    Parse 'ATL @ BOS' or 'ATL@BOS' into (away_abbr, home_abbr).
    Also handles full names if needed.
    """
    game_str = game_str.strip()
    for sep in [' @ ', '@']:
        if sep in game_str:
            parts = game_str.split(sep)
            return parts[0].strip().upper(), parts[1].strip().upper()
    return None, None


# Team name → abbr mapping for Odds API full names
TEAM_ABBR = {
    'Arizona Diamondbacks': 'AZ', 'Atlanta Braves': 'ATL', 'Baltimore Orioles': 'BAL',
    'Boston Red Sox': 'BOS', 'Chicago Cubs': 'CHC', 'Chicago White Sox': 'CWS',
    'Cincinnati Reds': 'CIN', 'Cleveland Guardians': 'CLE', 'Colorado Rockies': 'COL',
    'Detroit Tigers': 'DET', 'Houston Astros': 'HOU', 'Kansas City Royals': 'KC',
    'Los Angeles Angels': 'LAA', 'Los Angeles Dodgers': 'LAD', 'Miami Marlins': 'MIA',
    'Milwaukee Brewers': 'MIL', 'Minnesota Twins': 'MIN', 'New York Mets': 'NYM',
    'New York Yankees': 'NYY', 'Oakland Athletics': 'OAK', 'Philadelphia Phillies': 'PHI',
    'Pittsburgh Pirates': 'PIT', 'San Diego Padres': 'SD', 'San Francisco Giants': 'SF',
    'Seattle Mariners': 'SEA', 'St. Louis Cardinals': 'STL', 'Tampa Bay Rays': 'TB',
    'Texas Rangers': 'TEX', 'Toronto Blue Jays': 'TOR', 'Washington Nationals': 'WSH',
    'Sacramento River Cats': 'SAC',  # fallback
}


def match_game(odds_games, away_abbr, home_abbr):
    """Find the Odds API game object matching the bet's away/home teams."""
    for g in odds_games:
        g_away = TEAM_ABBR.get(g.get('away_team', ''), g.get('away_team', '').upper())
        g_home = TEAM_ABBR.get(g.get('home_team', ''), g.get('home_team', '').upper())
        if g_away == away_abbr and g_home == home_abbr:
            return g
        # Fuzzy: partial match on abbr in full name
        if (away_abbr in g.get('away_team', '').upper() and
                home_abbr in g.get('home_team', '').upper()):
            return g
    return None


def extract_closing_ml(game, away_abbr):
    """Extract closing ML line for a side from Odds API game object."""
    # Priority: lowvig → draftkings → fanduel
    for bk_key in ['lowvig', 'draftkings', 'fanduel', 'betmgm']:
        bk = next((b for b in (game.get('bookmakers') or []) if b['key'] == bk_key), None)
        if not bk:
            continue
        h2h = next((m for m in bk.get('markets', []) if m['key'] in ('h2h', 'h2h_h1')), None)
        if not h2h:
            continue
        outcomes = h2h.get('outcomes', [])
        away_out = next((o for o in outcomes if TEAM_ABBR.get(o['name'], o['name'].upper()) == away_abbr), None)
        home_out = next((o for o in outcomes if TEAM_ABBR.get(o['name'], o['name'].upper()) != away_abbr), None)
        if away_out and home_out:
            return {
                'awayPrice': away_out['price'],
                'homePrice': home_out['price'],
                'book': bk_key,
                'market': h2h['key'],
            }
    return None


def extract_closing_total(game, bet_str):
    """Extract closing total line matching bet side (Over/Under) and number."""
    # Parse bet: 'Total O 8.5' or 'Over 9.0' or 'Under 7.5'
    side = 'over' if any(w in bet_str.upper() for w in ['OVER', ' O ']) else 'under'
    number_match = re.search(r'(\d+\.?\d*)', bet_str)
    bet_number = float(number_match.group(1)) if number_match else None

    for bk_key in ['lowvig', 'draftkings', 'fanduel', 'betmgm']:
        bk = next((b for b in (game.get('bookmakers') or []) if b['key'] == bk_key), None)
        if not bk:
            continue
        tot = next((m for m in bk.get('markets', []) if m['key'] == 'totals'), None)
        if not tot:
            continue
        outcomes = tot.get('outcomes', [])
        # Find the over and under at closing number
        over_out = next((o for o in outcomes if o['name'].lower() == 'over'), None)
        under_out = next((o for o in outcomes if o['name'].lower() == 'under'), None)
        if not over_out or not under_out:
            continue
        closing_number = over_out.get('point', None)
        if side == 'over':
            bet_price = over_out['price']
            opp_price = under_out['price']
        else:
            bet_price = under_out['price']
            opp_price = over_out['price']
        return {
            'betSide': side,
            'betPrice': bet_price,
            'oppPrice': opp_price,
            'closingNumber': closing_number,
            'betNumber': bet_number,
            'book': bk_key,
        }
    return None


def extract_closing_rl(game, bet_str, away_abbr):
    """Extract closing run line for the bet side."""
    # Determine which team and which side (+1.5 or -1.5)
    is_away = away_abbr in bet_str.upper()
    is_minus = '-1.5' in bet_str

    for bk_key in ['lowvig', 'draftkings', 'fanduel', 'betmgm']:
        bk = next((b for b in (game.get('bookmakers') or []) if b['key'] == bk_key), None)
        if not bk:
            continue
        spreads = next((m for m in bk.get('markets', []) if m['key'] == 'spreads'), None)
        if not spreads:
            continue
        outcomes = spreads.get('outcomes', [])
        for o in outcomes:
            o_abbr = TEAM_ABBR.get(o['name'], o['name'].upper())
            o_is_away = (o_abbr == away_abbr)
            o_point = o.get('point', 0)
            if o_is_away == is_away and ((o_point < 0) == is_minus):
                # Find the opposing outcome
                opp = next((x for x in outcomes if x is not o), None)
                return {
                    'betPrice': o['price'],
                    'oppPrice': opp['price'] if opp else None,
                    'point': o_point,
                    'book': bk_key,
                }
    return None


def calculate_clv(bet, closing):
    """
    Calculate CLV% = vig-free closing implied% for our side minus our price implied%.
    Positive = we beat the closing line.
    """
    if not closing:
        return None

    market = bet.get('market', '')
    bet_price = bet.get('price')
    if bet_price is None:
        return None

    if market in ('ML', 'F5 ML'):
        vf_away, vf_home = vig_free_prob(closing['awayPrice'], closing['homePrice'])
        if vf_away is None:
            return None
        away_abbr, _ = parse_game_key(bet.get('game', ''))
        bet_abbr = bet.get('bet', '').strip().upper().split()[0]
        is_away = (bet_abbr == away_abbr) or (away_abbr and away_abbr in bet.get('bet', '').upper())
        close_vf = vf_away if is_away else vf_home
        our_imp = american_to_implied(bet_price) * 100
        return round(close_vf - our_imp, 2)

    elif market in ('Total', 'Game Total'):
        if not closing:
            return None
        close_vf, opp_vf = vig_free_prob(closing['betPrice'], closing['oppPrice'])
        if close_vf is None:
            return None
        our_imp = american_to_implied(bet_price) * 100
        return round(close_vf - our_imp, 2)

    elif market in ('Run Line', 'RL'):
        if not closing:
            return None
        close_vf, opp_vf = vig_free_prob(closing['betPrice'], closing['oppPrice'])
        if close_vf is None:
            return None
        our_imp = american_to_implied(bet_price) * 100
        return round(close_vf - our_imp, 2)

    return None


def build_closing_line_str(bet, closing):
    """Human-readable closing line string for BET_LOG."""
    if not closing:
        return '—'
    market = bet.get('market', '')
    if market in ('Total', 'Game Total'):
        num = closing.get('closingNumber', '?')
        side = closing.get('betSide', '').capitalize()
        price = closing.get('betPrice', '?')
        bet_num = closing.get('betNumber', '?')
        if bet_num and num and float(bet_num) != float(num):
            return f"{side} {bet_num}→{num} {price} [{closing.get('book','')}]"
        return f"{side} {num} {price} [{closing.get('book','')}]"
    elif market in ('ML', 'F5 ML'):
        away_abbr, _ = parse_game_key(bet.get('game', ''))
        bet_abbr = bet.get('bet', '').strip().upper().split()[0]
        is_away = (bet_abbr == away_abbr) or (away_abbr and away_abbr in bet.get('bet', '').upper())
        price = closing['awayPrice'] if is_away else closing['homePrice']
        return f"{price} [{closing.get('book','')}]"
    elif market in ('Run Line', 'RL'):
        return f"{closing.get('point','')} {closing.get('betPrice','')} [{closing.get('book','')}]"
    return '—'


def update_bet_log(bets):
    """Rebuild BET_LOG.md from bets list."""
    # Group by date
    from collections import defaultdict
    by_date = defaultdict(list)
    for b in bets:
        by_date[b['date']].append(b)

    # Overall stats
    total_w = sum(1 for b in bets if b.get('result') == 'WIN' and b.get('confidence') != 'Paper')
    total_l = sum(1 for b in bets if b.get('result') == 'LOSS' and b.get('confidence') != 'Paper')
    total_p = sum(1 for b in bets if b.get('result') == 'PUSH')
    total_pl = sum(b.get('pl', 0) or 0 for b in bets)
    pending = sum(1 for b in bets if b.get('status') not in ('SETTLED', 'WIN', 'LOSS', 'PUSH'))

    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    lines = [
        '# BET_LOG.md — Authoritative Bet Record',
        f'*Generated from bets.json — last updated: {today}*',
        '',
        f'## Overall Record: {total_w}W {total_l}L {total_p}P | Total P/L: ${total_pl:+.2f} | Pending: {pending}',
        '',
        '---',
        '',
    ]

    for date in sorted(by_date.keys(), reverse=True):
        day_bets = by_date[date]
        day_w = sum(1 for b in day_bets if b.get('result') == 'WIN' and b.get('confidence') != 'Paper')
        day_l = sum(1 for b in day_bets if b.get('result') == 'LOSS' and b.get('confidence') != 'Paper')
        day_pl = sum(b.get('pl', 0) or 0 for b in day_bets)
        lines.append(f'### {date} — {day_w}W {day_l}L | P/L: ${day_pl:+.2f}')
        lines.append('| ID | Market | Bet | Price | Edge% | Conf | Result | P/L | CLV% |')
        lines.append('|---|---|---|---|---|---|---|---|---|')
        for b in day_bets:
            bid    = b.get('id', '')
            market = b.get('market', '')
            bet    = b.get('bet', '')
            price  = b.get('price', '')
            edge   = f"{b.get('edgePct', '')}%" if b.get('edgePct') is not None else '—'
            conf   = b.get('confidence', '')
            result = b.get('result', b.get('status', ''))
            pl     = f"${b.get('pl', 0):+.2f}" if b.get('pl') is not None else '—'
            clv    = f"{b.get('clv'):+.1f}%" if b.get('clv') is not None else '—'
            lines.append(f'| {bid} | {market} | {bet} | {price} | {edge} | {conf} | {result} | {pl} | {clv} |')
        lines.append('')

    return '\n'.join(lines)


def main():
    if not ODDS_API_KEY:
        print("ERROR: ODDS_API_KEY not set")
        sys.exit(1)

    # Load bets.json
    with open('bets.json') as f:
        bets = json.load(f)

    # Find bets needing CLV: settled, clv is null, not unsupported market
    needs_clv = [
        b for b in bets
        if b.get('clv') is None
        and b.get('result') in ('WIN', 'LOSS', 'PUSH')
        and b.get('market') not in UNSUPPORTED_MARKETS
        and b.get('market') in SUPPORTED_MARKETS
        and b.get('closingLineSource') not in ('expired_no_betTimeLine',)
    ]

    # Mark unsupported markets
    for b in bets:
        if b.get('clv') is None and b.get('market') in UNSUPPORTED_MARKETS and b.get('result') in ('WIN', 'LOSS', 'PUSH'):
            b['closingLineSource'] = 'market_unavailable'

    print(f"Bets needing CLV: {len(needs_clv)}")
    if not needs_clv:
        print("Nothing to update.")
    else:
        # Group by date to minimize API calls
        from collections import defaultdict
        by_date = defaultdict(list)
        for b in needs_clv:
            by_date[b['date']].append(b)

        updated = 0
        for date, day_bets in sorted(by_date.items()):
            print(f"\nProcessing {date} ({len(day_bets)} bets)...")

            # Determine which market endpoints we need
            needed_markets = set()
            for b in day_bets:
                api_market = SUPPORTED_MARKETS.get(b['market'], (None,))[0]
                if api_market:
                    needed_markets.add(api_market)

            # Fetch all needed markets in two calls: main + h2h_h1
            main_mkts = ','.join(m for m in needed_markets if m != 'h2h_h1')
            f5_needed = 'h2h_h1' in needed_markets

            odds_games = []
            if main_mkts:
                time.sleep(0.5)
                odds_games = fetch_historical_odds(date, main_mkts)

            f5_games = []
            if f5_needed:
                time.sleep(0.5)
                f5_games = fetch_historical_odds(date, 'h2h_h1')

            # Merge F5 into odds_games by game id
            if f5_games:
                f5_map = {g['id']: g.get('bookmakers', []) for g in f5_games}
                for g in odds_games:
                    f5_bks = f5_map.get(g['id'], [])
                    for bk in g.get('bookmakers', []):
                        f5_bk = next((b for b in f5_bks if b['key'] == bk['key']), None)
                        if f5_bk:
                            bk['markets'] = bk.get('markets', []) + f5_bk.get('markets', [])
                # Add any F5-only games
                existing_ids = {g['id'] for g in odds_games}
                for g in f5_games:
                    if g['id'] not in existing_ids:
                        odds_games.append(g)

            for b in day_bets:
                away_abbr, home_abbr = parse_game_key(b.get('game', ''))
                if not away_abbr:
                    print(f"  Could not parse game: {b.get('game')}")
                    continue

                game = match_game(odds_games, away_abbr, home_abbr)
                if not game:
                    print(f"  No odds match: {b.get('game')} ({b['id']})")
                    b['closingLineSource'] = 'no_game_match'
                    continue

                market = b.get('market')
                closing = None

                if market in ('ML', 'F5 ML'):
                    closing = extract_closing_ml(game, away_abbr)
                elif market in ('Total', 'Game Total'):
                    closing = extract_closing_total(game, b.get('bet', ''))
                elif market in ('Run Line', 'RL'):
                    closing = extract_closing_rl(game, b.get('bet', ''), away_abbr)

                if not closing:
                    print(f"  No closing line: {b['id']} {market}")
                    b['closingLineSource'] = 'line_not_found'
                    continue

                clv = calculate_clv(b, closing)
                cl_str = build_closing_line_str(b, closing)

                b['closingLine'] = cl_str
                b['closingLineSource'] = closing.get('book', 'lowvig')
                b['closingLineTimestamp'] = f"{date}T23:59:00Z"
                b['clv'] = clv

                direction = '✓' if (clv is not None and clv > 0) else '✗'
                print(f"  {direction} {b['id']} | CL: {cl_str} | CLV: {clv:+.2f}%" if clv is not None else f"  ? {b['id']} | CL: {cl_str} | CLV: calc failed")
                updated += 1

        print(f"\nUpdated {updated} bets with CLV.")

    # Write bets.json
    with open('bets.json', 'w') as f:
        json.dump(bets, f, indent=2)
    print("bets.json written.")

    # Rebuild BET_LOG.md
    log_content = update_bet_log(bets)
    with open('BET_LOG.md', 'w') as f:
        f.write(log_content)
    print("BET_LOG.md rebuilt.")


if __name__ == '__main__':
    main()
