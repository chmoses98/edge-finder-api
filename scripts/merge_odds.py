"""
scripts/merge_odds.py — v2.0
Reads Kalshi odds from data/kalshi_market_registry.json instead of
kalshi_raw.json / kalshi_search.json. Injects full Kalshi market structure
into each game in slate.json, covering all 8 market types.
"""
import json

# ── Load data sources ──────────────────────────────────────────────────────────
with open('data/odds.json') as f:
    odds = json.load(f)
try:
    with open('data/slate.json') as f:
        slate = json.load(f)
except Exception as e:
    print(f'ERROR: Could not parse data/slate.json: {e}')
    import sys; sys.exit(1)

# Load Kalshi market registry (primary source for all Kalshi odds)
try:
    with open('data/kalshi_market_registry.json') as f:
        reg_doc = json.load(f)
    registry = reg_doc.get('registry', {})
    print(f'Kalshi registry: {len(registry)} games')
except FileNotFoundError:
    registry = {}
    print('WARNING: kalshi_market_registry.json not found — Kalshi odds will be empty')

# Legacy fallback sources
try:
    with open('data/kalshi_raw.json') as f:
        kalshi_raw = json.load(f)
    legacy_ml_games = kalshi_raw.get('games', [])
except:
    legacy_ml_games = []

def normalize(name):
    return name.lower().replace(' ','').replace('.','').replace('-','')

FULL_TO_ABBR = {
    'detroit tigers':'DET','tampa bay rays':'TB','san diego padres':'SD',
    'philadelphia phillies':'PHI','baltimore orioles':'BAL','boston red sox':'BOS',
    'miami marlins':'MIA','washington nationals':'WSH','cleveland guardians':'CLE',
    'new york yankees':'NYY','kansas city royals':'KC','cincinnati reds':'CIN',
    'toronto blue jays':'TOR','atlanta braves':'ATL','chicago white sox':'CWS',
    'minnesota twins':'MIN','san francisco giants':'SF','milwaukee brewers':'MIL',
    'texas rangers':'TEX','st. louis cardinals':'STL','athletics':'ATH',
    'chicago cubs':'CHC','pittsburgh pirates':'PIT','houston astros':'HOU',
    'colorado rockies':'COL','los angeles angels':'LAA','los angeles dodgers':'LAD',
    'arizona diamondbacks':'AZ','new york mets':'NYM','seattle mariners':'SEA',
    'oakland athletics':'ATH',
}

def to_abbr(full):
    return FULL_TO_ABBR.get(full.lower(), full[:3].upper())

def vig_free(a_american, h_american):
    if a_american is None or h_american is None: return None, None
    def imp(o): return 100/(o+100) if o>0 else abs(o)/(abs(o)+100)
    ia, ih = imp(a_american), imp(h_american)
    tot = ia+ih
    return round(ia/tot*10000)/100, round(ih/tot*10000)/100

def find_registry_entry(away_full, home_full, away_abbr, home_abbr):
    """Find the registry entry for a game by trying multiple key combinations."""
    # Build candidate keys
    candidates = set()
    for a in [away_abbr, to_abbr(away_full)]:
        for h in [home_abbr, to_abbr(home_full)]:
            candidates.add(f"{a}{h}")
    for key in candidates:
        if key in registry:
            return registry[key]
    return None

odds_games = odds.get('games', [])
matched = 0
unmatched = []

for game in slate.get('games', []):
    away_abbr = game.get('away', {}).get('abbr', '')
    home_abbr = game.get('home', {}).get('abbr', '')
    away_full = game.get('away', {}).get('team', '')
    home_full = game.get('home', {}).get('team', '')

    # Match to Odds API game for Pinnacle/FD/DK data
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

    # Base odds from Odds API (Pinnacle, FD, DK, BetMGM)
    game['odds']                = best.get('books', {})
    game['pinnacleVF']          = best.get('pinnacleVF')
    game['pinnacleF5VF']        = best.get('pinnacleF5VF')
    game['oddsApiEventId']      = best.get('eventId')
    game['oddsApiCommenceTime'] = best.get('commenceTime')
    game.pop('pinVigFree', None)

    # ── Inject Kalshi data from registry ──────────────────────────────────────
    away_k = to_abbr(best['awayTeam'])
    home_k = to_abbr(best['homeTeam'])
    reg = find_registry_entry(best['awayTeam'], best['homeTeam'], away_k, home_k)

    kalshi_books = game['odds'].setdefault('kalshi', {})

    if reg:
        game['kalshiKey']     = reg['kalshi_key']
        game['kalshiGameTime'] = reg.get('game_time_et')
        mkts = reg.get('markets', {})

        # ── ML ────────────────────────────────────────────────────────────────
        ml = mkts.get('moneyline', {})
        if ml:
            away_p = (ml.get('prices') or {}).get('away') or {}
            home_p = (ml.get('prices') or {}).get('home') or {}
            a_am = away_p.get('american')
            h_am = home_p.get('american')
            kalshi_books['ml'] = {
                'away':         a_am,
                'home':         h_am,
                'away_ticker':  ml.get('away_ticker'),
                'home_ticker':  ml.get('home_ticker'),
                'source':       'kalshi_registry',
            }
            if a_am and h_am:
                vf_a, vf_h = vig_free(a_am, h_am)
                game['kalshiVF'] = {'away': vf_a, 'home': vf_h}

        # ── Run Line / Spread ────────────────────────────────────────────────
        sp = mkts.get('spread', {})
        if sp:
            bl = sp.get('best_line') or {}
            # Traditional RL: best_line is closest to 50%
            # The team with implied_pct > 50 is the "underdog" at +1.5 equivalent
            kalshi_books['rl'] = {
                'best_ticker':  bl.get('ticker'),
                'team':         bl.get('team'),
                'wins_by_over': bl.get('win_by_over'),
                'implied_pct':  bl.get('implied_pct'),
                'american':     bl.get('american'),
                'all_lines':    sp.get('lines', []),
                'source':       'kalshi_registry',
                'note':         'Spread is win-margin markets. best_line = line closest to 50% implied.',
            }

        # ── Game Total ────────────────────────────────────────────────────────
        tot = mkts.get('total', {})
        if tot:
            bl = tot.get('best_line') or {}
            kalshi_books['total'] = {
                'best_ticker':    bl.get('ticker'),
                'line':           bl.get('total'),
                'implied_pct':    bl.get('implied_pct'),
                'american':       bl.get('american'),
                'all_lines':      tot.get('lines', []),
                'source':         'kalshi_registry',
                'note':           'Integer total lines. best_line = line closest to 50%.',
            }

        # ── Team Totals ───────────────────────────────────────────────────────
        for tt_key, side_label in [('team_total_away','away'),('team_total_home','home')]:
            tt = mkts.get(tt_key, {})
            if tt:
                bl = tt.get('best_line') or {}
                kalshi_books.setdefault('team_totals', {})[side_label] = {
                    'team':        tt.get('team'),
                    'best_ticker': bl.get('ticker'),
                    'line':        bl.get('over_n'),          # N = over N-0.5 equivalent
                    'implied_pct': bl.get('implied_pct'),
                    'american':    bl.get('american'),
                    'all_lines':   tt.get('lines', []),
                    'source':      'kalshi_registry',
                }

        # ── F5 Moneyline ──────────────────────────────────────────────────────
        f5 = mkts.get('f5_moneyline', {})
        if f5:
            away_p  = (f5.get('prices') or {}).get('away') or {}
            home_p  = (f5.get('prices') or {}).get('home') or {}
            tie_p   = (f5.get('prices') or {}).get('tie')  or {}
            a_am    = away_p.get('american')
            h_am    = home_p.get('american')
            t_am    = tie_p.get('american')
            # Derive eventTicker from away_ticker if not stored directly on f5
            away_tkr = f5.get('away_ticker') or ''
            derived_event_ticker = '-'.join(away_tkr.split('-')[:-1]) if away_tkr else None
            kalshi_books['f5ml'] = {
                'away':         a_am,
                'home':         h_am,
                'tie':          t_am,
                'tie_american': t_am,           # legacy alias kept for backward compat
                'away_ticker':  f5.get('away_ticker'),
                'home_ticker':  f5.get('home_ticker'),
                'tie_ticker':   f5.get('tie_ticker'),
                'eventTicker':  f5.get('eventTicker') or derived_event_ticker,
                'seriesTicker': f5.get('seriesTicker', 'KXMLBF5'),
                'source':       'kalshi_registry',
                'status':       away_p.get('status') or 'active',
            }
            if a_am and h_am:
                vf_a, vf_h = vig_free(a_am, h_am)
                game['kalshiF5VF'] = {'away': vf_a, 'home': vf_h}

        # ── F5 Spread ────────────────────────────────────────────────────────
        f5sp = mkts.get('f5_spread', {})
        if f5sp:
            bl = f5sp.get('best_line') or {}
            kalshi_books['f5_spread'] = {
                'best_ticker': bl.get('ticker'),
                'team':        bl.get('team'),
                'wins_by_over':bl.get('win_by_over'),
                'implied_pct': bl.get('implied_pct'),
                'american':    bl.get('american'),
                'all_lines':   f5sp.get('lines', []),
                'source':      'kalshi_registry',
            }

        # ── F5 Total ─────────────────────────────────────────────────────────
        f5tot = mkts.get('f5_total', {})
        if f5tot:
            bl = f5tot.get('best_line') or {}
            kalshi_books['f5_total'] = {
                'best_ticker': bl.get('ticker'),
                'line':        bl.get('total'),
                'implied_pct': bl.get('implied_pct'),
                'american':    bl.get('american'),
                'all_lines':   f5tot.get('lines', []),
                'source':      'kalshi_registry',
            }

        # ── NRFI / YRFI ──────────────────────────────────────────────────────
        rfi = mkts.get('rfi', {})
        if rfi:
            rfi_prices = rfi.get('prices', {})
            kalshi_books['nrfi_yrfi'] = {
                'ticker':       rfi.get('ticker'),
                'yrfi_american': (rfi_prices.get('yrfi') or {}).get('american'),
                'nrfi_american': (rfi_prices.get('nrfi') or {}).get('american'),
                'yrfi_implied':  (rfi_prices.get('yrfi') or {}).get('implied_pct'),
                'nrfi_implied':  (rfi_prices.get('nrfi') or {}).get('implied_pct'),
                'source':        'kalshi_registry',
                'note':          'Single binary market. YES=YRFI, NO=NRFI.',
            }

    else:
        # No registry entry — fall back to legacy kalshi_raw for ML only
        game['kalshiKey'] = f"{away_k}{home_k}"
        game.setdefault('kalshiVF', None)

    matched += 1

with open('data/slate.json', 'w') as f:
    json.dump(slate, f)

# Summary
ml=rl=tot=f5=tt=nrfi=0
for game in slate.get('games', []):
    kal = (game.get('odds') or {}).get('kalshi', {})
    if kal.get('ml', {}).get('away'): ml += 1
    if kal.get('rl', {}).get('best_ticker'): rl += 1
    if kal.get('total', {}).get('line'): tot += 1
    if kal.get('f5ml', {}).get('away'): f5 += 1
    if kal.get('team_totals', {}).get('away', {}).get('best_ticker'): tt += 1
    if kal.get('nrfi_yrfi', {}).get('ticker'): nrfi += 1

n = len(slate.get('games', []))
print(f'Merged: {matched}/{n} games (unmatched: {unmatched})')
print(f'Kalshi from registry: ML={ml} RL={rl} Total={tot} F5={f5} TT={tt} NRFI/YRFI={nrfi}')

# F5 moneyline visibility check — cross-pipeline signal
# If F5 prices were mapped into the registry but zero made it into slate,
# something went wrong in merge_odds itself.
_f5_in_registry = sum(
    1 for game in slate.get('games', [])
    if (game.get('odds') or {}).get('kalshi', {}).get('f5ml', {}).get('away') is not None
)
print(f'[F5-VISIBILITY] F5 moneyline prices in slate (odds.kalshi.f5ml.away set): {_f5_in_registry}/{n}')
if n > 0 and _f5_in_registry == 0:
    # Check whether registry had f5_moneyline at all — distinguish "not in registry" from "lost in merge"
    try:
        import json as _json
        with open('data/kalshi_market_registry.json') as _rf:
            _reg = _json.load(_rf)
        _reg_f5 = sum(
            1 for entry in _reg.get('registry', {}).values()
            if (entry.get('markets', {}).get('f5_moneyline', {}).get('prices', {}).get('away') or {}).get('american') is not None
        )
        if _reg_f5 > 0:
            print(f'[F5-VISIBILITY] WARNING: F5 moneyline discovery succeeded but mapping into the slate failed.')
            print(f'[F5-VISIBILITY] Registry had F5 prices for {_reg_f5} game(s) but none reached slate.json.')
            print(f'[F5-VISIBILITY] Inspect merge_odds.py find_registry_entry() and kalshi_books["f5ml"] block.')
        else:
            print(f'[F5-VISIBILITY] NOTE: Registry also has no F5 prices — issue is upstream in build_kalshi_registry.py.')
    except Exception as _e:
        print(f'[F5-VISIBILITY] NOTE: Could not read registry for cross-check: {_e}')
elif _f5_in_registry > 0:
    print(f'[F5-VISIBILITY] OK: F5 moneyline prices present in slate for {_f5_in_registry} game(s).')

# ── Post-merge Pinnacle VF check ──────────────────────────────────────────────
# Counts games missing pinnacleVF after merge. Missing is a warning only —
# never a blocking failure, never treated as zero or a usable value.
_pvf_missing = 0
_pvf_present = 0
for _g in slate.get('games', []):
    _pvf = _g.get('pinnacleVF') or {}
    if _pvf.get('away') is None:
        _pvf_missing += 1
    else:
        _pvf_present += 1

if _pvf_missing > 0:
    print(f'DATA-HEALTH WARNING: Pinnacle VF missing for {_pvf_missing} games; '
          f'Pinnacle-dependent checks disabled for those games.')
else:
    print(f'Pinnacle VF present for all {_pvf_present} games.')
