#!/usr/bin/env python3
"""
CLV Update Script v2
Pulls closing lines from The Odds API historical endpoint.
Fixed: commenceTime range for MLB evening games + closingLine string bug.
"""

import json, os, re, sys, time
from datetime import datetime, timezone, timedelta
from urllib.request import urlopen, Request
from urllib.error import HTTPError, URLError

ODDS_API_KEY = os.environ.get('ODDS_API_KEY', '')
BASE_URL = 'https://api.the-odds-api.com/v4'
SPORT = 'baseball_mlb'

SUPPORTED_MARKETS = {
    'ML':         ('h2h',         'ml'),
    'F5 ML':      ('h2h_h1',      'ml'),
    'Total':      ('totals',      'total'),
    'Game Total': ('totals',      'total'),
    'Run Line':   ('spreads',     'rl'),
    'RL':         ('spreads',     'rl'),
    'Team Total': ('team_totals', 'tt'),
    'TT':         ('team_totals', 'tt'),
}

UNSUPPORTED_MARKETS = {'YRFI', 'NRFI', 'K Prop', 'Pitcher Prop', 'Batter Prop'}

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
}


def american_to_implied(price):
    if price is None: return None
    if price >= 100: return 100 / (price + 100)
    return abs(price) / (abs(price) + 100)


def vig_free_prob(price_a, price_b):
    imp_a = american_to_implied(price_a)
    imp_b = american_to_implied(price_b)
    if imp_a is None or imp_b is None: return None, None
    total = imp_a + imp_b
    return round(imp_a / total * 100, 1), round(imp_b / total * 100, 1)


def fetch_historical_odds(date_str, markets):
    """
    Fetch historical odds snapshot at 1am ET the following day (all games finished).
    MLB games run until ~midnight ET, so snapshot at 06:00 UTC next day = 1am ET.
    commenceTimeFrom/To covers the full calendar date in ET (noon UTC to noon UTC+1).
    """
    # Snapshot: 06:00 UTC next day = ~1am ET (all games finished, closing lines set)
    next_day = (datetime.strptime(date_str, '%Y-%m-%d') + timedelta(days=1)).strftime('%Y-%m-%d')
    snapshot = f"{next_day}T06:00:00Z"

    # Game start range: MLB games start ~18:00-01:00 ET = 22:00 UTC to 06:00 UTC next day
    commence_from = f"{date_str}T15:00:00Z"   # noon ET = covers early day games
    commence_to   = f"{next_day}T06:00:00Z"   # 1am ET next day = covers late games

    url = (
        f"{BASE_URL}/historical/sports/{SPORT}/odds"
        f"?apiKey={ODDS_API_KEY}"
        f"&regions=us"
        f"&markets={markets}"
        f"&oddsFormat=american"
        f"&commenceTimeFrom={commence_from}"
        f"&commenceTimeTo={commence_to}"
        f"&date={snapshot}"
    )
    try:
        req = Request(url, headers={'Accept': 'application/json'})
        with urlopen(req, timeout=15) as resp:
            remaining = resp.headers.get('x-requests-remaining', '?')
            raw = json.loads(resp.read())
            # Historical endpoint wraps in {data: [...]}
            data = raw.get('data', raw) if isinstance(raw, dict) else raw
            games = data if isinstance(data, list) else []
            print(f"  [{markets}] {date_str}: {len(games)} games | remaining: {remaining}")
            return games
    except HTTPError as e:
        body = e.read().decode()
        print(f"  HTTP {e.code} [{markets}] {date_str}: {body[:300]}")
        return []
    except Exception as e:
        print(f"  Error [{markets}] {date_str}: {e}")
        return []


def parse_game_key(game_str):
    game_str = game_str.strip()
    for sep in [' @ ', '@']:
        if sep in game_str:
            parts = game_str.split(sep, 1)
            return parts[0].strip().upper(), parts[1].strip().upper()
    return None, None


def match_game(odds_games, away_abbr, home_abbr):
    for g in odds_games:
        g_away = TEAM_ABBR.get(g.get('away_team', ''), g.get('away_team', '').upper()[:3])
        g_home = TEAM_ABBR.get(g.get('home_team', ''), g.get('home_team', '').upper()[:3])
        if g_away == away_abbr and g_home == home_abbr:
            return g
    return None


def get_sharp_book(game, market_key):
    """Return the sharpest available bookmaker object for a given market."""
    for bk_key in ['lowvig', 'draftkings', 'fanduel', 'betmgm']:
        bk = next((b for b in (game.get('bookmakers') or []) if b['key'] == bk_key), None)
        if not bk: continue
        mkt = next((m for m in bk.get('markets', []) if m['key'] == market_key), None)
        if mkt: return bk_key, mkt
    return None, None


def extract_closing_ml(game, away_abbr, market_key='h2h'):
    bk_key, mkt = get_sharp_book(game, market_key)
    if not mkt: return None
    outcomes = mkt.get('outcomes', [])
    away_out = next((o for o in outcomes if TEAM_ABBR.get(o['name'], o['name'].upper()) == away_abbr), None)
    home_out = next((o for o in outcomes if TEAM_ABBR.get(o['name'], o['name'].upper()) != away_abbr), None)
    if not away_out or not home_out: return None
    return {'awayPrice': away_out['price'], 'homePrice': home_out['price'], 'book': bk_key, 'marketKey': market_key}


def extract_closing_total(game, bet_str):
    bk_key, mkt = get_sharp_book(game, 'totals')
    if not mkt: return None
    side = 'over' if any(w in bet_str.upper() for w in ['OVER', ' O ']) else 'under'
    number_match = re.search(r'(\d+\.?\d*)', bet_str)
    bet_number = float(number_match.group(1)) if number_match else None
    outcomes = mkt.get('outcomes', [])
    over_out  = next((o for o in outcomes if o['name'].lower() == 'over'), None)
    under_out = next((o for o in outcomes if o['name'].lower() == 'under'), None)
    if not over_out or not under_out: return None
    closing_number = over_out.get('point')
    bet_out  = over_out if side == 'over' else under_out
    opp_out  = under_out if side == 'over' else over_out
    return {
        'betSide': side, 'betPrice': bet_out['price'], 'oppPrice': opp_out['price'],
        'closingNumber': closing_number, 'betNumber': bet_number, 'book': bk_key,
    }


def extract_closing_rl(game, bet_str, away_abbr):
    bk_key, mkt = get_sharp_book(game, 'spreads')
    if not mkt: return None
    is_away  = away_abbr in bet_str.upper()
    is_minus = '-1.5' in bet_str
    outcomes = mkt.get('outcomes', [])
    for o in outcomes:
        o_abbr   = TEAM_ABBR.get(o['name'], o['name'].upper())
        o_is_away = (o_abbr == away_abbr)
        o_point  = o.get('point', 0)
        if o_is_away == is_away and ((o_point < 0) == is_minus):
            opp = next((x for x in outcomes if x is not o), None)
            return {'betPrice': o['price'], 'oppPrice': opp['price'] if opp else None, 'point': o_point, 'book': bk_key}
    return None


def calculate_clv(bet, closing):
    if not closing: return None
    market    = bet.get('market', '')
    bet_price = bet.get('price')
    if bet_price is None: return None

    if market in ('ML', 'F5 ML'):
        vf_away, vf_home = vig_free_prob(closing['awayPrice'], closing['homePrice'])
        if vf_away is None: return None
        away_abbr, _ = parse_game_key(bet.get('game', ''))
        bet_text = bet.get('bet', '').upper()
        is_away  = away_abbr and (away_abbr in bet_text or bet_text.startswith(away_abbr))
        close_vf = vf_away if is_away else vf_home
        our_imp  = american_to_implied(bet_price) * 100
        return round(close_vf - our_imp, 2)

    elif market in ('Total', 'Game Total', 'Run Line', 'RL'):
        vf_bet, _ = vig_free_prob(closing['betPrice'], closing['oppPrice'])
        if vf_bet is None: return None
        our_imp = american_to_implied(bet_price) * 100
        return round(vf_bet - our_imp, 2)

    return None


def build_closing_line_str(bet, closing):
    if not closing: return '—'
    market = bet.get('market', '')
    book   = closing.get('book', '')
    if market in ('Total', 'Game Total'):
        num      = closing.get('closingNumber', '?')
        side     = closing.get('betSide', '').capitalize()
        price    = closing.get('betPrice', '?')
        bet_num  = closing.get('betNumber')
        if bet_num is not None and num is not None and float(bet_num) != float(num):
            return f"{side} {bet_num}→{num} {price} [{book}]"
        return f"{side} {num} {price} [{book}]"
    elif market in ('ML', 'F5 ML'):
        away_abbr, _ = parse_game_key(bet.get('game', ''))
        bet_text  = bet.get('bet', '').upper()
        is_away   = away_abbr and (away_abbr in bet_text or bet_text.startswith(away_abbr))
        price     = closing['awayPrice'] if is_away else closing['homePrice']
        return f"{'+' if price > 0 else ''}{price} [{book}]"
    elif market in ('Run Line', 'RL'):
        pt    = closing.get('point', '')
        price = closing.get('betPrice', '')
        return f"{'+' if isinstance(pt,(int,float)) and pt > 0 else ''}{pt} {'+' if isinstance(price,(int,float)) and price > 0 else ''}{price} [{book}]"
    return '—'


def update_bet_log(bets):
    from collections import defaultdict
    by_date = defaultdict(list)
    for b in bets:
        by_date[b['date']].append(b)

    real_bets = [b for b in bets if b.get('confidence') != 'Paper']
    total_w  = sum(1 for b in real_bets if b.get('result') == 'WIN')
    total_l  = sum(1 for b in real_bets if b.get('result') == 'LOSS')
    total_p  = sum(1 for b in bets if b.get('result') == 'PUSH')
    total_pl = sum(b.get('pl', 0) or 0 for b in bets)
    pending  = sum(1 for b in bets if b.get('status') not in ('SETTLED', 'WIN', 'LOSS', 'PUSH'))
    today    = datetime.now(timezone.utc).strftime('%Y-%m-%d')

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
        day_real = [b for b in day_bets if b.get('confidence') != 'Paper']
        day_w    = sum(1 for b in day_real if b.get('result') == 'WIN')
        day_l    = sum(1 for b in day_real if b.get('result') == 'LOSS')
        day_pl   = sum(b.get('pl', 0) or 0 for b in day_bets)
        lines.append(f'### {date} — {day_w}W {day_l}L | P/L: ${day_pl:+.2f}')
        lines.append('| ID | Market | Bet | Price | Edge% | Conf | Result | P/L | Closing Line | CLV% |')
        lines.append('|---|---|---|---|---|---|---|---|---|---|')
        for b in day_bets:
            bid    = b.get('id', '')
            market = b.get('market', '')
            bet    = b.get('bet', '')
            price  = b.get('price', '')
            edge   = f"{b.get('edgePct', '')}%" if b.get('edgePct') is not None else '—'
            conf   = b.get('confidence', '')
            result = b.get('result', b.get('status', ''))
            pl     = f"${b.get('pl', 0):+.2f}" if b.get('pl') is not None else '—'
            cl     = b.get('closingLine') or '—'
            clv    = f"{b.get('clv'):+.1f}%" if b.get('clv') is not None else '—'
            lines.append(f'| {bid} | {market} | {bet} | {price} | {edge} | {conf} | {result} | {pl} | {cl} | {clv} |')
        lines.append('')

    return '\n'.join(lines)


def main():
    if not ODDS_API_KEY:
        print("ERROR: ODDS_API_KEY not set")
        sys.exit(1)

    with open('bets.json') as f:
        bets = json.load(f)

    # Mark unsupported markets
    for b in bets:
        if (b.get('clv') is None
                and b.get('market') in UNSUPPORTED_MARKETS
                and b.get('result') in ('WIN', 'LOSS', 'PUSH')):
            b['closingLineSource'] = 'market_unavailable'

    needs_clv = [
        b for b in bets
        if b.get('clv') is None
        and b.get('result') in ('WIN', 'LOSS', 'PUSH')
        and b.get('market') in SUPPORTED_MARKETS
        and b.get('closingLineSource') not in ('expired_no_betTimeLine', 'market_unavailable')
    ]

    print(f"Bets needing CLV: {len(needs_clv)}")

    if needs_clv:
        from collections import defaultdict
        by_date = defaultdict(list)
        for b in needs_clv:
            by_date[b['date']].append(b)

        updated = 0
        for date, day_bets in sorted(by_date.items()):
            print(f"\nProcessing {date} ({len(day_bets)} bets)...")

            needed = set(SUPPORTED_MARKETS[b['market']][0] for b in day_bets)
            main_mkts = ','.join(m for m in needed if m not in ('h2h_h1', 'team_totals'))
            need_f5  = 'h2h_h1' in needed
            need_tt  = 'team_totals' in needed

            odds_games = []
            if main_mkts:
                time.sleep(0.5)
                odds_games = fetch_historical_odds(date, main_mkts)

            if need_f5:
                time.sleep(0.5)
                f5_games = fetch_historical_odds(date, 'h2h_h1')
                f5_map = {g['id']: g.get('bookmakers', []) for g in f5_games}
                for g in odds_games:
                    for bk in g.get('bookmakers', []):
                        f5_bk = next((b for b in f5_map.get(g['id'], []) if b['key'] == bk['key']), None)
                        if f5_bk:
                            bk['markets'] = bk.get('markets', []) + f5_bk.get('markets', [])
                existing = {g['id'] for g in odds_games}
                for g in f5_games:
                    if g['id'] not in existing:
                        odds_games.append(g)

            if need_tt:
                time.sleep(0.5)
                tt_games = fetch_historical_odds(date, 'team_totals')
                tt_map = {g['id']: g.get('bookmakers', []) for g in tt_games}
                for g in odds_games:
                    for bk in g.get('bookmakers', []):
                        tt_bk = next((b for b in tt_map.get(g['id'], []) if b['key'] == bk['key']), None)
                        if tt_bk:
                            bk['markets'] = bk.get('markets', []) + tt_bk.get('markets', [])
                existing = {g['id'] for g in odds_games}
                for g in tt_games:
                    if g['id'] not in existing:
                        odds_games.append(g)

            for b in day_bets:
                away_abbr, home_abbr = parse_game_key(b.get('game', ''))
                if not away_abbr:
                    print(f"  Could not parse game: {b.get('game')} ({b['id']})")
                    continue

                game = match_game(odds_games, away_abbr, home_abbr)
                if not game:
                    print(f"  No match: {b.get('game')} ({b['id']}) | away={away_abbr} home={home_abbr}")
                    # Log available games for debugging
                    if odds_games:
                        avail = [(TEAM_ABBR.get(g['away_team'],g['away_team']), TEAM_ABBR.get(g['home_team'],g['home_team'])) for g in odds_games[:5]]
                        print(f"    Available: {avail}")
                    b['closingLineSource'] = 'no_game_match'
                    continue

                market  = b.get('market')
                closing = None
                if market in ('ML',):
                    closing = extract_closing_ml(game, away_abbr, 'h2h')
                elif market == 'F5 ML':
                    closing = extract_closing_ml(game, away_abbr, 'h2h_h1')
                elif market in ('Total', 'Game Total'):
                    closing = extract_closing_total(game, b.get('bet', ''))
                elif market in ('Run Line', 'RL'):
                    closing = extract_closing_rl(game, b.get('bet', ''), away_abbr)

                if not closing:
                    print(f"  No closing line: {b['id']} {market}")
                    b['closingLineSource'] = 'line_not_found'
                    continue

                clv    = calculate_clv(b, closing)
                cl_str = build_closing_line_str(b, closing)

                b['closingLine']          = cl_str
                b['closingLineSource']    = closing.get('book', 'lowvig')
                b['closingLineTimestamp'] = f"{date}T06:00:00Z"
                b['clv']                  = clv

                flag = '✓' if clv and clv > 0 else '✗'
                clv_str = f"{clv:+.2f}%" if clv is not None else "N/A"
                print(f"  {flag} {b['id']} | {market} | CL: {cl_str} | CLV: {clv_str}")
                updated += 1

        print(f"\nUpdated {updated} bets with CLV.")

    with open('bets.json', 'w') as f:
        json.dump(bets, f, indent=2)
    print("bets.json written.")

    log = update_bet_log(bets)
    with open('BET_LOG.md', 'w') as f:
        f.write(log)
    print("BET_LOG.md rebuilt.")


if __name__ == '__main__':
    main()
