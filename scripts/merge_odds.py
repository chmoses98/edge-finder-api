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

# RFI fallback: index KXMLBRFI markets from kalshi_search.json by game key
# Used when build_kalshi_registry.py fails to populate the 'rfi' block.
# kalshi_search.json 'markets' list contains full price data (yes_bid, yes_ask, mid,
# implied_pct, american_odds) for all KXMLBRFI markets fetched by the Vercel pipeline.
# Match key: event_ticker suffix after the date+time prefix = kalshiKey (e.g. 'MIAPIT')
_rfi_by_key = {}   # kalshiKey → market dict
try:
    with open('data/kalshi_search.json') as _f:
        _ks = json.load(_f)
    for _m in _ks.get('markets', []):
        if _m.get('market_type') != 'nrfi_yrfi':
            continue
        _et = _m.get('event_ticker', '')      # e.g. KXMLBRFI-26JUN121840MIAPIT
        _parts = _et.split('-')
        if len(_parts) < 2:
            continue
        # Suffix after series prefix: 26JUN121840MIAPIT
        _date_team = _parts[1] if len(_parts) == 2 else '-'.join(_parts[1:])
        # Team pair is the last 4-8 chars (strip 11-char date+time prefix: YYMONDDHHM is 11)
        _team_key = _date_team[11:] if len(_date_team) > 11 else ''
        if not _team_key:
            continue
        if _team_key in _rfi_by_key:
            # Ambiguous: two RFI markets matched the same key — do not use either
            _rfi_by_key[_team_key] = '__AMBIGUOUS__'
        else:
            _rfi_by_key[_team_key] = _m
    _rfi_loaded = len([v for v in _rfi_by_key.values() if v != '__AMBIGUOUS__'])
    print(f'RFI fallback index: {_rfi_loaded} markets indexed from kalshi_search.json')
except FileNotFoundError:
    print('WARNING: kalshi_search.json not found — RFI fallback unavailable')
except Exception as _e:
    print(f'WARNING: RFI fallback index failed: {_e}')

def _american_from_mid(mid):
    """Convert decimal probability mid to American odds integer."""
    if mid is None or mid <= 0 or mid >= 1:
        return None
    if mid >= 0.5:
        return round(-(mid / (1 - mid)) * 100)
    return round(((1 - mid) / mid) * 100)

def _build_rfi_from_ks_market(m):
    """
    Build the nrfi_yrfi dict that build_market_ledger expects,
    given a kalshi_search market entry (YES = YRFI side).
    Returns None if required price fields are absent.
    """
    yes_bid    = m.get('yes_bid')
    yes_ask    = m.get('yes_ask')
    yrfi_mid   = m.get('mid')
    yrfi_am    = m.get('american_odds')
    if yrfi_mid is None:
        return None
    # NO side = NRFI
    nrfi_mid     = round(1.0 - yrfi_mid, 4)
    nrfi_implied = round(nrfi_mid * 100, 2)
    yrfi_implied = m.get('implied_pct', round(yrfi_mid * 100, 2))
    nrfi_am      = _american_from_mid(nrfi_mid)
    # NO-side bid/ask = complement of YES ask/bid
    nrfi_bid = round(1.0 - yes_ask, 4) if yes_ask is not None else None
    nrfi_ask = round(1.0 - yes_bid, 4) if yes_bid is not None else None
    return {
        'ticker':        m.get('market_ticker') or m.get('event_ticker'),
        'yrfi_american': yrfi_am,
        'nrfi_american': nrfi_am,
        'yrfi_implied':  yrfi_implied,
        'nrfi_implied':  nrfi_implied,
        'yrfi_bid':      yes_bid,
        'yrfi_ask':      yes_ask,
        'nrfi_bid':      nrfi_bid,
        'nrfi_ask':      nrfi_ask,
        'source':        'kalshi_search_fallback',
        'note':          'Fallback: registry rfi block absent; prices from kalshi_search.json. YES=YRFI, NO=NRFI.',
    }

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


def compute_game_odds_fields(game, odds_games, registry, rfi_by_key):
    """
    Pure transform for a single slate game.

    Returns (new_game, matched, unmatched_label, log_lines):
      - new_game: a NEW dict — a shallow copy of `game` with odds/Kalshi
        fields populated exactly as the original mutating implementation
        computed them, and 'pinVigFree' removed if present.
      - matched: True if an Odds API entry was found for this game.
      - unmatched_label: 'AWAY@HOME' if no match was found, else None.
      - log_lines: RFI-fallback diagnostic lines to print, in the same
        order/content the original inline implementation printed them.

    Never mutates `game`, `odds_games`, `registry`, or `rfi_by_key`. Every
    nested object this function writes into the 'kalshi' book (ml, rl,
    total, team_totals, f5ml, f5_spread, f5_total, nrfi_yrfi) is a freshly
    built dict — none of it is a reference shared with `game`, the matched
    odds.json entry, or `registry`. See docs/IMMUTABLE_PIPELINE.md's
    shallow-copy boundary contract: nested values this function does not
    itself own/write are still shared by reference with `game`, but its
    own output fields never alias caller-owned mutable state. The
    pre-refactor implementation aliased game['odds'] directly to the
    matched odds.json entry's 'books' dict and mutated the 'kalshi'
    sub-block in place via setdefault() — in this codebase that never
    produced wrong output (find_registry_entry() is a pure function of
    the matched odds entry's own team names, so two slate games matching
    the same odds entry would deterministically recompute identical
    Kalshi data anyway), but it did mean merge_odds.py mutated the parsed
    data/odds.json structure as a side effect of populating slate.json —
    a violation of the "never mutate caller-owned dicts" contract this
    refactor establishes, even though it happened to be behaviorally inert.
    """
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
        return dict(game), False, f'{away_abbr}@{home_abbr}', []

    log_lines = []
    new_game = dict(game)

    # Base odds from Odds API (Pinnacle, FD, DK, BetMGM) — copy, don't alias
    new_odds = dict(best.get('books', {}))
    new_game['odds']                = new_odds
    new_game['pinnacleVF']          = best.get('pinnacleVF')
    new_game['pinnacleF5VF']        = best.get('pinnacleF5VF')
    new_game['oddsApiEventId']      = best.get('eventId')
    new_game['oddsApiCommenceTime'] = best.get('commenceTime')
    new_game.pop('pinVigFree', None)

    # ── Inject Kalshi data from registry ──────────────────────────────────────
    away_k = to_abbr(best['awayTeam'])
    home_k = to_abbr(best['homeTeam'])
    reg = find_registry_entry(best['awayTeam'], best['homeTeam'], away_k, home_k)

    # Copy (not alias) any pre-existing books.kalshi content — api/odds.js
    # may have already populated kalshi-native fields (ml/f5ml/nrfi/
    # teamTotals/total) before this script runs; those must be preserved,
    # not overwritten wholesale, and not mutated in place on the shared
    # odds.json-derived object.
    kalshi_books = dict(new_odds.get('kalshi', {}))
    new_odds['kalshi'] = kalshi_books

    if reg:
        new_game['kalshiKey']      = reg['kalshi_key']
        new_game['kalshiGameTime'] = reg.get('game_time_et')
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
                new_game['kalshiVF'] = {'away': vf_a, 'home': vf_h}

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
                tt_block = dict(kalshi_books.get('team_totals') or {})
                tt_block[side_label] = {
                    'team':        tt.get('team'),
                    'best_ticker': bl.get('ticker'),
                    'line':        bl.get('over_n'),          # N = over N-0.5 equivalent
                    'implied_pct': bl.get('implied_pct'),
                    'american':    bl.get('american'),
                    'all_lines':   tt.get('lines', []),
                    'source':      'kalshi_registry',
                }
                kalshi_books['team_totals'] = tt_block

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
                new_game['kalshiF5VF'] = {'away': vf_a, 'home': vf_h}

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
            # Primary: registry has RFI prices — use them
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
        elif 'nrfi_yrfi' not in kalshi_books:
            # Fallback: registry rfi block absent — try kalshi_search.json index
            # Strict exact-key match only. Ambiguous or missing = skip (Missing Data).
            _kkey = new_game.get('kalshiKey', '')
            _ks_rfi = rfi_by_key.get(_kkey)
            if _ks_rfi == '__AMBIGUOUS__':
                log_lines.append(f'  RFI fallback AMBIGUOUS for {_kkey} — skipping, will show Missing Data')
            elif _ks_rfi is not None:
                _rfi_dict = _build_rfi_from_ks_market(_ks_rfi)
                if _rfi_dict is not None:
                    kalshi_books['nrfi_yrfi'] = _rfi_dict
                    log_lines.append(
                        f'  RFI fallback OK: {_kkey} → ticker={_rfi_dict["ticker"]} '
                        f'YRFI={_rfi_dict["yrfi_american"]} NRFI={_rfi_dict["nrfi_american"]}'
                    )
                else:
                    log_lines.append(f'  RFI fallback MISSING PRICES for {_kkey} — skipping')
            # else: no match in kalshi_search.json — leave nrfi_yrfi absent (Missing Data)

    else:
        # No registry entry — fall back to legacy kalshi_raw for ML only
        new_game['kalshiKey'] = f"{away_k}{home_k}"
        if 'kalshiVF' not in new_game:
            new_game['kalshiVF'] = None

    return new_game, True, None, log_lines


def merge_odds_immutable(slate, odds_games, registry, rfi_by_key):
    """
    Pure transform: given the parsed slate, the odds.json games list, the
    Kalshi registry dict, and the RFI fallback index, return a NEW slate
    object — a shallow copy of `slate` with 'games' replaced by a new list
    of per-game results from compute_game_odds_fields() — without
    mutating `slate`, `odds_games`, `registry`, or `rfi_by_key`, and
    without changing any other top-level slate field, the number of
    games, or game order.

    Returns (new_slate, matched_count, unmatched_labels, log_lines) so the
    caller can reproduce the original script's stdout exactly, in the
    same per-game order it was originally interleaved in.
    """
    new_games = []
    matched = 0
    unmatched = []
    log_lines = []

    for game in slate.get('games', []):
        new_game, was_matched, unmatched_label, game_log_lines = compute_game_odds_fields(
            game, odds_games, registry, rfi_by_key
        )
        new_games.append(new_game)
        if was_matched:
            matched += 1
            log_lines.extend(game_log_lines)
        else:
            unmatched.append(unmatched_label)

    new_slate = dict(slate)
    new_slate['games'] = new_games
    return new_slate, matched, unmatched, log_lines


odds_games = odds.get('games', [])

slate, matched, unmatched, _log_lines = merge_odds_immutable(slate, odds_games, registry, _rfi_by_key)
for _line in _log_lines:
    print(_line)

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
