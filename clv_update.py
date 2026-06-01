#!/usr/bin/env python3
"""
CLV Update Script
Pulls closing lines from The Odds API historical endpoint.
Runs directly from GitHub Actions — no Vercel proxy needed.
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
    # 'F5 ML': h2h_h1 not supported on historical endpoint
    'Total':      ('totals',      'total'),
    'Game Total': ('totals',      'total'),
    'Run Line':   ('spreads',     'rl'),
    'RL':         ('spreads',     'rl'),
    'Team Total': ('team_totals', 'tt'),
    'TT':         ('team_totals', 'tt'),
}
UNSUPPORTED = {'YRFI', 'NRFI', 'K Prop', 'Pitcher Prop', 'Batter Prop', 'F5 ML'}  # F5 ML not on historical endpoint
SHARP_ORDER = ['lowvig', 'draftkings', 'fanduel', 'betmgm', 'williamhill_us', 'fanatics', 'bovada', 'betonlineag', 'betrivers', 'betus', 'mybookieag']

TEAM_ABBR = {
    'Arizona Diamondbacks':'AZ',   'Atlanta Braves':'ATL',
    'Baltimore Orioles':'BAL',     'Boston Red Sox':'BOS',
    'Chicago Cubs':'CHC',          'Chicago White Sox':'CWS',
    'Cincinnati Reds':'CIN',       'Cleveland Guardians':'CLE',
    'Colorado Rockies':'COL',      'Detroit Tigers':'DET',
    'Houston Astros':'HOU',        'Kansas City Royals':'KC',
    'Los Angeles Angels':'LAA',    'Los Angeles Dodgers':'LAD',
    'Miami Marlins':'MIA',         'Milwaukee Brewers':'MIL',
    'Minnesota Twins':'MIN',       'New York Mets':'NYM',
    'New York Yankees':'NYY',      'Oakland Athletics':'OAK',
    'Philadelphia Phillies':'PHI', 'Pittsburgh Pirates':'PIT',
    'San Diego Padres':'SD',       'San Francisco Giants':'SF',
    'Seattle Mariners':'SEA',      'St. Louis Cardinals':'STL',
    'Tampa Bay Rays':'TB',         'Texas Rangers':'TEX',
    'Toronto Blue Jays':'TOR',     'Washington Nationals':'WSH',
}


def abbr(name):
    return TEAM_ABBR.get(name, (name or '').upper())


def to_imp(price):
    if price is None: return None
    return 100/(price+100) if price >= 100 else abs(price)/(abs(price)+100)


def vig_free(pa, pb):
    ia, ib = to_imp(pa), to_imp(pb)
    if not ia or not ib: return None, None
    t = ia + ib
    return round(ia/t*100, 1), round(ib/t*100, 1)


def parse_game(s):
    if not s: return None, None
    sep = ' @ ' if ' @ ' in s else '@'
    parts = s.split(sep, 1)
    return (parts[0].strip().upper(), parts[1].strip().upper()) if len(parts)==2 else (None, None)


def fetch_historical(date_str, markets):
    """
    Snapshot at 06:00 UTC next day (~1am ET) — all MLB games finished.
    No commenceTime filters — let the snapshot capture everything on that date.
    """
    # snapshot = midnight UTC next day = 7pm ET on game day
    # commenceTimeFrom/To pins which games we get (the target date's games)
    from datetime import timedelta as _td
    next_day = (datetime.strptime(date_str, '%Y-%m-%d') + _td(days=1)).strftime('%Y-%m-%d')
    snapshot         = next_day + 'T00:00:00Z'   # midnight UTC = 7pm ET on game day
    commence_from    = date_str + 'T00:00:00Z'
    commence_to      = next_day + 'T06:00:00Z'   # covers late games ending past midnight ET

    url = (f"{BASE_URL}/historical/sports/{SPORT}/odds"
           f"?apiKey={ODDS_API_KEY}&regions=us&markets={markets}"
           f"&oddsFormat=american"
           f"&commenceTimeFrom={commence_from}"
           f"&commenceTimeTo={commence_to}"
           f"&date={snapshot}")

    try:
        req = Request(url, headers={'Accept': 'application/json'})
        with urlopen(req, timeout=20) as resp:
            remaining = resp.headers.get('x-requests-remaining', '?')
            raw = json.loads(resp.read())
            # Historical endpoint returns {data:[...], timestamp, ...}
            games = raw if isinstance(raw, list) else raw.get('data', [])
            print(f"  [{markets}] snapshot={snapshot}: {len(games)} games | remaining={remaining}")
            if games:
                sample = [(g.get('away_team','?'), g.get('home_team','?')) for g in games[:5]]
                print(f"  Games: {sample}")
            if not games and isinstance(raw, dict):
                print(f"  Raw keys: {list(raw.keys())} | Sample: {str(raw)[:200]}")
            return games, remaining
    except HTTPError as e:
        body = e.read().decode()
        print(f"  HTTP {e.code} [{markets}]: {body[:300]}")
        return [], None
    except Exception as e:
        print(f"  Error [{markets}]: {e}")
        return [], None


def match_game(games, away, home):
    for g in games:
        if abbr(g.get('away_team')) == away and abbr(g.get('home_team')) == home:
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
    side = 'over' if re.search(r'over|\bO\b', bet_str, re.I) else 'under'
    nm = re.search(r'(\d+\.?\d*)', bet_str)
    bet_num = float(nm.group(1)) if nm else None
    outs = mkt.get('outcomes', [])
    ov = next((o for o in outs if o['name'].lower() == 'over'), None)
    un = next((o for o in outs if o['name'].lower() == 'under'), None)
    if not ov or not un: return None
    bet_out = ov if side == 'over' else un
    opp_out = un if side == 'over' else ov
    return {'betSide': side, 'betPrice': bet_out['price'], 'oppPrice': opp_out['price'],
            'closingNumber': ov.get('point'), 'betNumber': bet_num, 'book': bk_key}


def extract_rl(game, bet_str, away_abbr):
    bk_key, mkt = get_sharp(game, 'spreads')
    if not mkt: return None
    is_away  = away_abbr and away_abbr in bet_str.upper()
    is_minus = '-1.5' in bet_str
    for o in (mkt.get('outcomes') or []):
        o_abbr   = abbr(o['name'])
        o_is_away = (o_abbr == away_abbr)
        o_point  = o.get('point', 0)
        if o_is_away == is_away and (o_point < 0) == is_minus:
            opp = next((x for x in mkt['outcomes'] if x is not o), None)
            return {'betPrice': o['price'], 'oppPrice': opp['price'] if opp else None,
                    'point': o_point, 'book': bk_key}
    return None


def calc_clv(bet, closing, market):
    if not closing: return None
    our_imp = to_imp(bet.get('price')) * 100
    if our_imp is None: return None

    if market in ('ML', 'F5 ML'):
        vf_a, vf_h = vig_free(closing['awayPrice'], closing['homePrice'])
        if vf_a is None: return None
        away, _ = parse_game(bet.get('game', ''))
        txt = (bet.get('bet') or '').upper()
        is_away = away and (txt.startswith(away) or away in txt)
        return round(((vf_a if is_away else vf_h) - our_imp), 2)

    if market in ('Total', 'Game Total', 'Run Line', 'RL'):
        vf, _ = vig_free(closing['betPrice'], closing['oppPrice'])
        if vf is None: return None
        return round(vf - our_imp, 2)

    return None


def cl_str(bet, closing, market):
    if not closing: return None
    bk = closing.get('book', '')
    fmt = lambda p: f"{'+' if p >= 0 else ''}{p}"
    if market in ('ML', 'F5 ML'):
        away, _ = parse_game(bet.get('game', ''))
        txt = (bet.get('bet') or '').upper()
        is_away = away and (txt.startswith(away) or away in txt)
        return f"{fmt(closing['awayPrice'] if is_away else closing['homePrice'])} [{bk}]"
    if market in ('Total', 'Game Total'):
        side = closing['betSide'].capitalize()
        cn, bn = closing.get('closingNumber'), closing.get('betNumber')
        num_str = f"{bn}→{cn}" if (bn is not None and cn is not None and bn != cn) else str(cn)
        return f"{side} {num_str} {fmt(closing['betPrice'])} [{bk}]"
    if market in ('Run Line', 'RL'):
        return f"{fmt(closing['point'])} {fmt(closing['betPrice'])} [{bk}]"
    return None


def rebuild_log(bets):
    from collections import defaultdict
    by_date = defaultdict(list)
    for b in bets:
        by_date[b['date']].append(b)

    real  = [b for b in bets if b.get('confidence') != 'Paper']
    tw    = sum(1 for b in real if b.get('result') == 'WIN')
    tl    = sum(1 for b in real if b.get('result') == 'LOSS')
    tp    = sum(1 for b in bets if b.get('result') == 'PUSH')
    tpl   = sum(b.get('pl', 0) or 0 for b in bets)
    pend  = sum(1 for b in bets if b.get('status') not in ('SETTLED','WIN','LOSS','PUSH'))
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')

    lines = [
        '# BET_LOG.md — Authoritative Bet Record',
        f'*Generated from bets.json — last updated: {today}*',
        '',
        f'## Overall Record: {tw}W {tl}L {tp}P | Total P/L: ${tpl:+.2f} | Pending: {pend}',
        '', '---', '',
    ]
    for date in sorted(by_date.keys(), reverse=True):
        db = by_date[date]
        dr = [b for b in db if b.get('confidence') != 'Paper']
        dw = sum(1 for b in dr if b.get('result') == 'WIN')
        dl = sum(1 for b in dr if b.get('result') == 'LOSS')
        dpl = sum(b.get('pl', 0) or 0 for b in db)
        lines.append(f'### {date} — {dw}W {dl}L | P/L: ${dpl:+.2f}')
        lines.append('| ID | Market | Bet | Price | Edge% | Conf | Result | P/L | Closing Line | CLV% |')
        lines.append('|---|---|---|---|---|---|---|---|---|---|')
        for b in db:
            edge = f"{b.get('edgePct','')}%" if b.get('edgePct') is not None else '—'
            pl   = f"${b.get('pl',0):+.2f}" if b.get('pl') is not None else '—'
            clv  = f"{b.get('clv'):+.1f}%" if b.get('clv') is not None else '—'
            lines.append(f"| {b.get('id','')} | {b.get('market','')} | {b.get('bet','')} "
                         f"| {b.get('price','')} | {edge} | {b.get('confidence','')} "
                         f"| {b.get('result', b.get('status',''))} | {pl} "
                         f"| {b.get('closingLine') or '—'} | {clv} |")
        lines.append('')
    return '\n'.join(lines)


def main():
    date = sys.argv[1] if len(sys.argv) > 1 else None
    if not date:
        # Default: yesterday ET
        et_now = datetime.now(timezone.utc) - timedelta(hours=5)
        date = (et_now - timedelta(days=1)).strftime('%Y-%m-%d')

    if not ODDS_API_KEY:
        print("ERROR: ODDS_API_KEY not set"); sys.exit(1)

    print(f"CLV update for {date}")

    with open('bets.json') as f:
        bets = json.load(f)

    # Mark unsupported
    for b in bets:
        if b.get('clv') is None and b.get('market') in UNSUPPORTED and b.get('result') in ('WIN','LOSS','PUSH'):
            b['closingLineSource'] = 'market_unavailable'

    targets = [
        b for b in bets
        if b.get('date') == date
        and b.get('clv') is None
        and b.get('result') in ('WIN', 'LOSS', 'PUSH')
        and b.get('market') in SUPPORTED
        and b.get('closingLineSource') not in ('expired_no_betTimeLine', 'market_unavailable')
    ]

    print(f"Bets to process: {len(targets)}")
    if not targets:
        print("Nothing to do.")
    else:
        needed   = set(SUPPORTED[b['market']][0] for b in targets)
        main_mkts = ','.join(m for m in needed if m not in ('h2h_h1', 'team_totals'))
        need_f5  = 'h2h_h1' in needed
        need_tt  = 'team_totals' in needed

        odds_games = []

        if main_mkts:
            games, _ = fetch_historical(date, main_mkts)
            odds_games = games

        def merge(base, extra):
            mp = {g['id']: g.get('bookmakers', []) for g in extra}
            for g in base:
                for bk in (g.get('bookmakers') or []):
                    xbk = next((b for b in mp.get(g['id'], []) if b['key'] == bk['key']), None)
                    if xbk: bk['markets'] = (bk.get('markets') or []) + (xbk.get('markets') or [])
            existing = {g['id'] for g in base}
            for g in extra:
                if g['id'] not in existing: base.append(g)

        if need_f5:
            time.sleep(0.5)
            f5_games, _ = fetch_historical(date, 'h2h_h1')
            merge(odds_games, f5_games)

        if need_tt:
            time.sleep(0.5)
            tt_games, _ = fetch_historical(date, 'team_totals')
            merge(odds_games, tt_games)

        print(f"Total games in pool: {len(odds_games)}")
        if odds_games:
            sample = [(abbr(g['away_team']), abbr(g['home_team'])) for g in odds_games[:5]]
            print(f"Sample: {sample}")

        updated = 0
        for b in targets:
            away, home = parse_game(b.get('game', ''))
            if not away:
                print(f"  SKIP {b['id']}: parse fail")
                continue

            game = match_game(odds_games, away, home)
            if not game:
                print(f"  NO_MATCH {b['id']}: {b.get('game')} ({away}@{home})")
                b['closingLineSource'] = 'no_game_match'
                continue

            mkt     = b['market']
            closing = None
            if mkt == 'ML':                             closing = extract_ml(game, away, 'h2h')
            elif mkt == 'F5 ML':                        closing = extract_ml(game, away, 'h2h_h1')
            elif mkt in ('Total', 'Game Total'):        closing = extract_total(game, b.get('bet', ''))
            elif mkt in ('Run Line', 'RL'):             closing = extract_rl(game, b.get('bet', ''), away)

            if not closing:
                bk_keys = [bk['key'] for bk in (game.get('bookmakers') or [])]
                mkt_keys = {bk['key']: [m['key'] for m in bk.get('markets',[])] for bk in (game.get('bookmakers') or [])}
                print(f"  NO_LINE {b['id']}: {mkt} | books={bk_keys} | markets={mkt_keys}")
                b['closingLineSource'] = 'line_not_found'
                continue

            clv = calc_clv(b, closing, mkt)
            b['closingLine']          = cl_str(b, closing, mkt)
            b['closingLineSource']    = closing['book']
            b['closingLineTimestamp'] = f"{date}T06:00:00Z"
            b['clv']                  = clv

            flag = '✓' if clv and clv > 0 else '✗'
            clv_s = f"{clv:+.2f}%" if clv is not None else "N/A"
            print(f"  {flag} {b['id']} | {mkt} | CL: {b['closingLine']} | CLV: {clv_s}")
            updated += 1

        print(f"\nUpdated: {updated}/{len(targets)}")

    with open('bets.json', 'w') as f:
        json.dump(bets, f, indent=2)
    print("bets.json written")

    with open('BET_LOG.md', 'w') as f:
        f.write(rebuild_log(bets))
    print("BET_LOG.md rebuilt")


if __name__ == '__main__':
    main()
