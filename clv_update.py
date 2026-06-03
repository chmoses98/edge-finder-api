#!/usr/bin/env python3
"""
CLV Update Script — v2
Pulls game scores AND closing lines from The Odds API.
Fully automated: determines WIN/LOSS/PUSH, calculates CLV, updates bets.json + BET_LOG.md.
No manual input required.
"""

import json, os, re, sys, time
from datetime import datetime, timezone, timedelta
from urllib.request import urlopen, Request
from urllib.error import HTTPError

ODDS_API_KEY = os.environ.get('ODDS_API_KEY', '')
BASE_URL     = 'https://api.the-odds-api.com/v4'
SPORT        = 'baseball_mlb'

SUPPORTED = {
    'ML':         ('h2h',         'ml'),
    'F5':         ('h2h_h1',      'f5'),
    'TOTAL':      ('totals',      'total'),
    'RUN LINE':   ('spreads',     'rl'),
    'TEAM TOTAL': ('team_totals', 'tt'),
}
UNSUPPORTED = {'YRFI', 'NRFI', 'K Prop', 'Pitcher Prop', 'Batter Prop'}
SHARP_ORDER = ['pinnacle', 'lowvig', 'draftkings', 'fanduel', 'betmgm',
               'williamhill_us', 'fanatics', 'bovada', 'betonlineag',
               'betrivers', 'betus', 'mybookieag']

TEAM_ABBR = {
    'Arizona Diamondbacks':'ARI',  'Atlanta Braves':'ATL',
    'Baltimore Orioles':'BAL',     'Boston Red Sox':'BOS',
    'Chicago Cubs':'CHC',          'Chicago White Sox':'CWS',
    'Cincinnati Reds':'CIN',       'Cleveland Guardians':'CLE',
    'Colorado Rockies':'COL',      'Detroit Tigers':'DET',
    'Houston Astros':'HOU',        'Kansas City Royals':'KC',
    'Los Angeles Angels':'LAA',    'Los Angeles Dodgers':'LAD',
    'Miami Marlins':'MIA',         'Milwaukee Brewers':'MIL',
    'Minnesota Twins':'MIN',       'New York Mets':'NYM',
    'New York Yankees':'NYY',      'Oakland Athletics':'OAK',
    'Athletics':'OAK',             'Las Vegas Athletics':'OAK',
    'Philadelphia Phillies':'PHI', 'Pittsburgh Pirates':'PIT',
    'San Diego Padres':'SD',       'San Francisco Giants':'SF',
    'Seattle Mariners':'SEA',      'St. Louis Cardinals':'STL',
    'Tampa Bay Rays':'TB',         'Texas Rangers':'TEX',
    'Toronto Blue Jays':'TOR',     'Washington Nationals':'WSH',
}

UNIT_SIZE = 100.0  # $ per unit — adjust as needed


def abbr(name):
    return TEAM_ABBR.get(name, (name or '').upper()[:3])


def to_imp(price):
    if price is None: return None
    return 100/(price+100) if price >= 0 else abs(price)/(abs(price)+100)


def parse_game(s):
    if not s: return None, None
    sep = ' @ ' if ' @ ' in s else '@'
    parts = s.split(sep, 1)
    if len(parts) != 2: return None, None
    return abbr(parts[0].strip()), abbr(parts[1].strip())


def get_bet_str(b):
    if b.get('bet'): return b['bet']
    team = b.get('betTeam') or ''
    side = b.get('betSide') or ''
    line = b.get('line')
    if line is not None:
        return f"{team} {side} {line}"
    return f"{team} {side}"


def get_result(b):
    return b.get('result') or b.get('status')


def calc_pl(price, size, result):
    """Calculate P/L in dollars given American odds, unit size, and result."""
    if result not in ('WIN', 'LOSS', 'PUSH') or size is None:
        return None
    dollars = float(size) * UNIT_SIZE
    if result == 'PUSH':
        return 0.0
    if result == 'LOSS':
        return -round(dollars, 2)
    # WIN
    if price >= 0:
        return round(dollars * price / 100, 2)
    else:
        return round(dollars * 100 / abs(price), 2)


# ── API helpers ───────────────────────────────────────────────────────────────

def api_get(url):
    try:
        req = Request(url, headers={'Accept': 'application/json'})
        with urlopen(req, timeout=20) as resp:
            remaining = resp.headers.get('x-requests-remaining', '?')
            data = json.loads(resp.read())
            return data, remaining
    except HTTPError as e:
        body = e.read().decode()
        print(f"  HTTP {e.code}: {body[:300]}")
        return None, None
    except Exception as e:
        print(f"  Error: {e}")
        return None, None


def fetch_scores(date_str):
    """
    Pull completed game scores for a given date.
    Uses /scores endpoint with daysFrom parameter.
    Returns dict keyed by (away_abbr, home_abbr) -> {away_score, home_score, completed}
    """
    # daysFrom=1 gets yesterday, daysFrom=2 gets 2 days ago, etc.
    today = datetime.now(timezone.utc).date()
    game_date = datetime.strptime(date_str, '%Y-%m-%d').date()
    days_ago = (today - game_date).days
    days_from = max(1, days_ago + 1)  # API: 1=yesterday, 2=2 days ago, etc.

    url = (f"{BASE_URL}/sports/{SPORT}/scores"
           f"?apiKey={ODDS_API_KEY}&daysFrom={days_from}")

    print(f"Fetching scores (daysFrom={days_from})...")
    data, remaining = api_get(url)
    if not data:
        return {}

    scores = {}
    for g in data:
        # Filter to target date
        commence = g.get('commence_time', '')
        if date_str not in commence:
            continue
        if not g.get('completed'):
            continue

        away_a = abbr(g.get('away_team', ''))
        home_a = abbr(g.get('home_team', ''))
        sc = g.get('scores') or []

        away_score = home_score = None
        for s in sc:
            name_a = abbr(s.get('name', ''))
            try:
                val = int(s.get('score', 0))
            except (ValueError, TypeError):
                val = 0
            if name_a == away_a:
                away_score = val
            elif name_a == home_a:
                home_score = val

        if away_score is not None and home_score is not None:
            scores[(away_a, home_a)] = {
                'away_score': away_score,
                'home_score': home_score,
                'completed': True
            }
            print(f"  Score: {away_a} {away_score} @ {home_a} {home_score}")

    print(f"  {len(scores)} completed games found | credits_remaining={remaining}")
    return scores


def determine_result(b, scores, away_abbr, home_abbr):
    """
    Determine WIN/LOSS/PUSH for a bet given game scores.
    Returns (result, away_score, home_score) or (None, None, None) if not found.
    """
    sc = scores.get((away_abbr, home_abbr))
    if not sc:
        return None, None, None

    away_sc = sc['away_score']
    home_sc = sc['home_score']
    mkt     = b.get('market', '')
    side    = (b.get('betSide') or '').upper()
    line    = b.get('line')
    price   = b.get('price')

    if mkt == 'ML':
        if away_sc > home_sc:
            winner = 'AWAY'
        elif home_sc > away_sc:
            winner = 'HOME'
        else:
            return 'PUSH', away_sc, home_sc
        return ('WIN' if side == winner else 'LOSS'), away_sc, home_sc

    if mkt == 'RUN LINE':
        # line is from bettor's perspective: -1.5 means team must win by 2+
        # betSide is AWAY or HOME; line stored as +1.5 or -1.5 relative to that side
        if line is None:
            return None, away_sc, home_sc
        if side == 'HOME':
            margin = home_sc - away_sc + float(line)
        else:
            margin = away_sc - home_sc + float(line)
        if margin > 0:   return 'WIN',  away_sc, home_sc
        if margin < 0:   return 'LOSS', away_sc, home_sc
        return 'PUSH', away_sc, home_sc

    if mkt == 'TOTAL':
        total = away_sc + home_sc
        line_val = float(line) if line is not None else None
        if line_val is None:
            return None, away_sc, home_sc
        if 'OVER' in side:
            if total > line_val:  return 'WIN',  away_sc, home_sc
            if total < line_val:  return 'LOSS', away_sc, home_sc
            return 'PUSH', away_sc, home_sc
        else:  # UNDER
            if total < line_val:  return 'WIN',  away_sc, home_sc
            if total > line_val:  return 'LOSS', away_sc, home_sc
            return 'PUSH', away_sc, home_sc

    if mkt == 'TEAM TOTAL':
        if 'AWAY' in side:
            team_sc = away_sc
        else:
            team_sc = home_sc
        line_val = float(line) if line is not None else None
        if line_val is None:
            return None, away_sc, home_sc
        if 'OVER' in side:
            if team_sc > line_val:  return 'WIN',  away_sc, home_sc
            if team_sc < line_val:  return 'LOSS', away_sc, home_sc
            return 'PUSH', away_sc, home_sc
        else:
            if team_sc < line_val:  return 'WIN',  away_sc, home_sc
            if team_sc > line_val:  return 'LOSS', away_sc, home_sc
            return 'PUSH', away_sc, home_sc

    if mkt in ('YRFI', 'NRFI'):
        # Need play-by-play — not available from scores endpoint
        # Flag for manual resolution
        return None, away_sc, home_sc

    if mkt == 'F5':
        # F5 results not determinable from final score alone — flag for manual
        return None, away_sc, home_sc

    return None, away_sc, home_sc


# ── Closing line helpers ───────────────────────────────────────────────────────

def fetch_historical(date_str, markets):
    next_day = (datetime.strptime(date_str, '%Y-%m-%d') + timedelta(days=1)).strftime('%Y-%m-%d')
    snapshot      = date_str + 'T23:00:00Z'
    commence_from = date_str + 'T00:00:00Z'
    commence_to   = next_day + 'T06:00:00Z'

    url = (f"{BASE_URL}/historical/sports/{SPORT}/odds"
           f"?apiKey={ODDS_API_KEY}&regions=us,eu&markets={markets}"
           f"&oddsFormat=american"
           f"&commenceTimeFrom={commence_from}"
           f"&commenceTimeTo={commence_to}"
           f"&date={snapshot}")

    print(f"  Fetching historical [{markets}]...")
    data, remaining = api_get(url)
    if data is None: return [], remaining
    games = data if isinstance(data, list) else data.get('data', [])
    print(f"    {len(games)} games | credits_remaining={remaining}")
    return games, remaining


def match_game(games, away, home):
    for g in games:
        if abbr(g.get('away_team')) == away and abbr(g.get('home_team')) == home:
            return g
    for g in games:
        if abbr(g.get('home_team')) == home:
            return g
    return None


def get_sharp(game, market_key):
    for bk_key in SHARP_ORDER:
        bk = next((b for b in (game.get('bookmakers') or []) if b['key'] == bk_key), None)
        if not bk: continue
        mkt = next((m for m in bk.get('markets', []) if m['key'] == market_key), None)
        if mkt: return bk_key, mkt
    return None, None


def extract_ml(game, away_abbr, market_key):
    bk_key, mkt = get_sharp(game, market_key)
    if not mkt: return None
    outs = mkt.get('outcomes', [])
    a = next((o for o in outs if abbr(o['name']) == away_abbr), None)
    h = next((o for o in outs if abbr(o['name']) != away_abbr), None)
    if not a or not h: return None
    return {'awayPrice': a['price'], 'homePrice': h['price'], 'book': bk_key}


def extract_total(game, bet_str):
    bk_key, mkt = get_sharp(game, 'totals')
    if not mkt: return None
    side = 'over' if re.search(r'over|OVER|\bO\b', str(bet_str)) else 'under'
    outs = mkt.get('outcomes', [])
    ov = next((o for o in outs if o['name'].lower() == 'over'), None)
    un = next((o for o in outs if o['name'].lower() == 'under'), None)
    if not ov or not un: return None
    bet_out = ov if side == 'over' else un
    return {'betSide': side, 'betPrice': bet_out['price'],
            'closingNumber': ov.get('point'), 'book': bk_key}


def extract_rl(game, away_abbr, bet_side):
    bk_key, mkt = get_sharp(game, 'spreads')
    if not mkt: return None
    is_away  = (bet_side or '').upper() == 'AWAY'
    is_minus = is_away  # away -1.5 = favorite RL; if away side with +1.5 that's is_minus=False
    # Actually: check the line field on the bet
    for o in (mkt.get('outcomes') or []):
        o_abbr    = abbr(o['name'])
        o_is_away = (o_abbr == away_abbr)
        if o_is_away == is_away:
            opp = next((x for x in mkt['outcomes'] if x is not o), None)
            return {'betPrice': o['price'], 'oppPrice': opp['price'] if opp else None,
                    'point': o.get('point'), 'book': bk_key}
    return None


def extract_rl_by_line(game, away_abbr, bet_side, bet_line):
    """Match RL by side and line direction."""
    bk_key, mkt = get_sharp(game, 'spreads')
    if not mkt: return None
    is_away  = (bet_side or '').upper() == 'AWAY'
    is_minus = float(bet_line or 0) < 0
    for o in (mkt.get('outcomes') or []):
        o_abbr    = abbr(o['name'])
        o_is_away = (o_abbr == away_abbr)
        o_point   = o.get('point', 0)
        if o_is_away == is_away and (o_point < 0) == is_minus:
            opp = next((x for x in mkt['outcomes'] if x is not o), None)
            return {'betPrice': o['price'], 'oppPrice': opp['price'] if opp else None,
                    'point': o_point, 'book': bk_key}
    return None


def extract_tt(game, away_abbr, bet_side, bet_str):
    bk_key, mkt = get_sharp(game, 'team_totals')
    if not mkt: return None
    is_away = 'AWAY' in (bet_side or '').upper()
    is_over = 'OVER' in (bet_str or '').upper()
    for o in (mkt.get('outcomes') or []):
        o_desc = o.get('description', '').upper()
        o_name = o.get('name', '').upper()
        correct_team = (abbr(o_desc) == away_abbr) if is_away else (abbr(o_desc) != away_abbr)
        correct_side = ('OVER' in o_name) == is_over
        if correct_team and correct_side:
            return {'betPrice': o['price'], 'point': o.get('point'), 'book': bk_key}
    return None


def calc_clv(b, closing, market):
    if not closing: return None
    our_imp = to_imp(b.get('price'))
    if our_imp is None: return None
    bet_side = (b.get('betSide') or '').upper()

    if market in ('ML', 'F5'):
        is_away = bet_side == 'AWAY'
        close_price = closing['awayPrice'] if is_away else closing['homePrice']
        close_imp = to_imp(close_price)
        if close_imp is None: return None
        return round((our_imp - close_imp) * -100, 2)

    if market in ('RUN LINE', 'TOTAL', 'TEAM TOTAL'):
        close_imp = to_imp(closing['betPrice'])
        if close_imp is None: return None
        return round((our_imp - close_imp) * -100, 2)

    return None


def cl_str(b, closing, market):
    if not closing: return None
    bk = closing.get('book', '')
    fmt = lambda p: f"{'+' if p and p >= 0 else ''}{p}"
    bet_side = (b.get('betSide') or '').upper()

    if market in ('ML', 'F5'):
        is_away = bet_side == 'AWAY'
        return f"{fmt(closing['awayPrice'] if is_away else closing['homePrice'])} [{bk}]"
    if market == 'TOTAL':
        cn = closing.get('closingNumber')
        return f"{closing['betSide'].capitalize()} {cn} {fmt(closing['betPrice'])} [{bk}]"
    if market == 'RUN LINE':
        return f"{fmt(closing.get('point'))} {fmt(closing['betPrice'])} [{bk}]"
    if market == 'TEAM TOTAL':
        return f"O/U{closing.get('point')} {fmt(closing['betPrice'])} [{bk}]"
    return None


# ── Log builder ───────────────────────────────────────────────────────────────

def rebuild_log(bets):
    from collections import defaultdict
    by_date = defaultdict(list)
    for b in bets:
        by_date[b['date']].append(b)

    real  = [b for b in bets if b.get('confidence') not in ('Paper', 'PAPER')]
    tw    = sum(1 for b in real if get_result(b) == 'WIN')
    tl    = sum(1 for b in real if get_result(b) == 'LOSS')
    tp    = sum(1 for b in bets if get_result(b) == 'PUSH')
    tpl   = sum(b.get('pl', 0) or 0 for b in bets)
    pend  = sum(1 for b in bets if get_result(b) not in ('WIN', 'LOSS', 'PUSH'))
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')

    lines = [
        '# BET_LOG.md — Authoritative Bet Record',
        f'*Generated from bets.json — last updated: {today}*',
        '',
        f'## Overall Record: {tw}W {tl}L {tp}P | Total P/L: ${tpl:+.2f} | Pending: {pend}',
        '', '---', '',
    ]
    for date in sorted(by_date.keys(), reverse=True):
        db  = by_date[date]
        dr  = [b for b in db if b.get('confidence') not in ('Paper', 'PAPER')]
        dw  = sum(1 for b in dr if get_result(b) == 'WIN')
        dl  = sum(1 for b in dr if get_result(b) == 'LOSS')
        dpl = sum(b.get('pl', 0) or 0 for b in db)
        lines.append(f'### {date} — {dw}W {dl}L | P/L: ${dpl:+.2f}')
        lines.append('| ID | Market | Bet | Price | Edge% | Conf | Size | Result | P/L | Closing | CLV% |')
        lines.append('|---|---|---|---|---|---|---|---|---|---|---|')
        for b in db:
            edge  = f"{b.get('edgePct','')}%" if b.get('edgePct') is not None else '—'
            pl    = f"${b.get('pl',0):+.2f}" if b.get('pl') is not None else '—'
            clv   = f"{b.get('clv'):+.1f}%" if b.get('clv') is not None else '—'
            res   = get_result(b) or 'PENDING'
            bet_s = b.get('betTeam') or b.get('bet') or ''
            lines.append(
                f"| {b.get('id','')} | {b.get('market','')} | {bet_s} "
                f"| {b.get('price','')} | {edge} | {b.get('confidence','')} "
                f"| {b.get('betSize','')}u | {res} | {pl} "
                f"| {b.get('closingLine') or '—'} | {clv} |"
            )
        lines.append('')
    return '\n'.join(lines)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    date = sys.argv[1] if len(sys.argv) > 1 else None
    if not date:
        et_now = datetime.now(timezone.utc) - timedelta(hours=5)
        date = (et_now - timedelta(days=1)).strftime('%Y-%m-%d')

    if not ODDS_API_KEY:
        print("ERROR: ODDS_API_KEY not set"); sys.exit(1)

    print(f"\n=== CLV Update for {date} ===\n")

    with open('bets.json') as f:
        bets = json.load(f)

    date_bets = [b for b in bets if b.get('date') == date]
    print(f"Bets for {date}: {len(date_bets)}")

    # ── Step 1: Pull scores and settle unsettled bets ──────────────────────
    print("\n--- Step 1: Scores & Settlement ---")
    scores = fetch_scores(date)

    settled_count = 0
    f5_manual = []
    nrfi_manual = []

    for b in date_bets:
        if get_result(b) in ('WIN', 'LOSS', 'PUSH'):
            continue  # already settled

        away, home = parse_game(b.get('game', ''))
        if not away:
            continue

        mkt = b.get('market', '')

        if mkt in ('YRFI', 'NRFI'):
            nrfi_manual.append(b['id'])
            continue

        if mkt == 'F5':
            f5_manual.append(b['id'])
            continue

        result, away_sc, home_sc = determine_result(b, scores, away, home)

        if result is None:
            print(f"  ? {b['id']}: no score found ({away}@{home})")
            continue

        b['result'] = result
        b['awayScore'] = away_sc
        b['homeScore'] = home_sc
        b['pl'] = calc_pl(b.get('price'), b.get('betSize'), result)
        settled_count += 1

        flag = '✓' if result == 'WIN' else ('↔' if result == 'PUSH' else '✗')
        pl_s = f"${b['pl']:+.2f}" if b['pl'] is not None else '—'
        print(f"  {flag} {b['id']} | {mkt} | {away_sc}-{home_sc} | {result} | {pl_s}")

    if f5_manual:
        print(f"\n  ⚠ F5 bets require manual settlement ({len(f5_manual)}):")
        for bid in f5_manual:
            print(f"    {bid}")

    if nrfi_manual:
        print(f"\n  ⚠ NRFI/YRFI bets require manual settlement ({len(nrfi_manual)}):")
        for bid in nrfi_manual:
            print(f"    {bid}")

    print(f"\n  Auto-settled: {settled_count} bets")

    # ── Step 2: Pull closing lines + CLV ──────────────────────────────────
    print("\n--- Step 2: Closing Lines & CLV ---")

    # Mark unsupported
    for b in date_bets:
        if b.get('clv') is None and b.get('market') in UNSUPPORTED:
            b['closingLineSource'] = 'market_unavailable'

    targets = [
        b for b in date_bets
        if b.get('clv') is None
        and get_result(b) in ('WIN', 'LOSS', 'PUSH')
        and b.get('market') in SUPPORTED
        and b.get('closingLineSource') not in ('expired', 'market_unavailable')
    ]

    print(f"CLV targets: {len(targets)}")

    if targets:
        needed    = set(SUPPORTED[b['market']][0] for b in targets)
        main_mkts = ','.join(m for m in needed if m not in ('h2h_h1', 'team_totals'))
        need_f5   = 'h2h_h1' in needed
        need_tt   = 'team_totals' in needed

        odds_games = []

        if main_mkts:
            games, _ = fetch_historical(date, main_mkts)
            odds_games = games

        def merge(base, extra):
            mp = {g['id']: g for g in extra}
            existing_ids = {g['id'] for g in base}
            for g in base:
                ex = mp.get(g['id'])
                if ex:
                    for bk in (ex.get('bookmakers') or []):
                        match = next((b for b in (g.get('bookmakers') or []) if b['key'] == bk['key']), None)
                        if match:
                            match['markets'] = (match.get('markets') or []) + (bk.get('markets') or [])
                        else:
                            g.setdefault('bookmakers', []).append(bk)
            for g in extra:
                if g['id'] not in existing_ids:
                    base.append(g)

        if need_f5:
            time.sleep(0.5)
            f5g, _ = fetch_historical(date, 'h2h_h1')
            merge(odds_games, f5g)

        if need_tt:
            time.sleep(0.5)
            ttg, _ = fetch_historical(date, 'team_totals')
            merge(odds_games, ttg)

        print(f"  Games in pool: {len(odds_games)}")

        clv_updated = 0
        for b in targets:
            away, home = parse_game(b.get('game', ''))
            if not away: continue

            game = match_game(odds_games, away, home)
            if not game:
                print(f"  NO_MATCH {b['id']}: {away}@{home}")
                b['closingLineSource'] = 'no_game_match'
                continue

            mkt     = b['market']
            closing = None
            bet_s   = get_bet_str(b)
            bet_side = (b.get('betSide') or '').upper()
            bet_line = b.get('line')

            if mkt == 'ML':
                closing = extract_ml(game, away, 'h2h')
            elif mkt == 'F5':
                closing = extract_ml(game, away, 'h2h_h1')
            elif mkt == 'TOTAL':
                closing = extract_total(game, bet_s)
            elif mkt == 'RUN LINE':
                closing = extract_rl_by_line(game, away, bet_side, bet_line)
            elif mkt == 'TEAM TOTAL':
                closing = extract_tt(game, away, bet_side, bet_s)

            if not closing:
                print(f"  NO_LINE {b['id']}: {mkt}")
                b['closingLineSource'] = 'line_not_found'
                continue

            clv = calc_clv(b, closing, mkt)
            b['closingLine']          = cl_str(b, closing, mkt)
            b['closingLineSource']    = closing['book']
            b['closingLineTimestamp'] = f"{date}T23:00:00Z"
            b['clv']                  = clv

            flag  = '✓' if clv and clv > 0 else '✗'
            clv_s = f"{clv:+.2f}%" if clv is not None else "N/A"
            res   = get_result(b)
            print(f"  {flag} {b['id']} | {mkt} | {res} | CL: {b['closingLine']} | CLV: {clv_s}")
            clv_updated += 1

        print(f"\n  CLV updated: {clv_updated}/{len(targets)}")

    # ── Step 3: Summary ───────────────────────────────────────────────────
    print("\n--- Summary ---")
    settled = [b for b in date_bets if get_result(b) in ('WIN','LOSS','PUSH')]
    wins    = sum(1 for b in settled if get_result(b) == 'WIN')
    losses  = sum(1 for b in settled if get_result(b) == 'LOSS')
    pushes  = sum(1 for b in settled if get_result(b) == 'PUSH')
    total_pl = sum(b.get('pl', 0) or 0 for b in settled)
    clv_vals = [b['clv'] for b in settled if b.get('clv') is not None]
    avg_clv  = sum(clv_vals) / len(clv_vals) if clv_vals else None

    print(f"  Record:  {wins}W {losses}L {pushes}P")
    print(f"  P/L:     ${total_pl:+.2f}")
    if avg_clv is not None:
        status = 'GOOD' if avg_clv > 1.0 else ('WARNING' if avg_clv > -1.0 else 'BAD')
        print(f"  Avg CLV: {avg_clv:+.2f}% [{status}]")
    pending = [b for b in date_bets if get_result(b) not in ('WIN','LOSS','PUSH')]
    if pending:
        print(f"  Pending: {len(pending)} bets (F5/NRFI/YRFI need manual settlement)")

    # ── Write outputs ─────────────────────────────────────────────────────
    with open('bets.json', 'w') as f:
        json.dump(bets, f, indent=2)
    print("\nbets.json written")

    with open('BET_LOG.md', 'w') as f:
        f.write(rebuild_log(bets))
    print("BET_LOG.md rebuilt")


if __name__ == '__main__':
    main()
