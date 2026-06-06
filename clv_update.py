#!/usr/bin/env python3
"""
CLV Update Script — v6.3
Fixes in this version:
  - CLV formula corrected: was inverted (sign flip bug removed)
  - Kalshi is in us_ex region — historical query uses regions=us_ex (not bookmakers=kalshi)
  - Historical response correctly unwrapped from {data:[...]} wrapper
  - Market name normalization: all aliases map to canonical keys
  - Team name/abbr matching: handles full names, abbrs, and legacy formats
  - betSide inference: derives from betTeam, bet string, or betSide field
  - Size field: reads both 'size' and 'betSize', always writes both
  - Confidence casing: normalizes HIGH/MEDIUM/LOW/Paper etc. to Title case
  - Kalshi direct API is now the PRIMARY closing line source (all markets)
  - Fetches settled Kalshi markets, finds ticker by game+market type, pulls candlesticks
  - Covers ML, F5, NRFI, YRFI, TT — everything we bet on
  - Odds API (Pinnacle) is fallback for ML/RL/Total when Kalshi data unavailable
  - CLV runs independently of result settlement
  - Game string formats: 'AWAY @ HOME', 'AWAY@HOME', full team names all handled
  - P&L: always computed from price + size, never left null on settled bets
"""

import json, os, re, sys, time
from datetime import datetime, timezone, timedelta
from urllib.request import urlopen, Request
from urllib.error import HTTPError

ODDS_API_KEY = os.environ.get('ODDS_API_KEY', '')
BASE_URL     = 'https://api.the-odds-api.com/v4'
SPORT        = 'baseball_mlb'

# ── Canonical market names ────────────────────────────────────────────────────
# Every alias maps to one canonical name. This is the single source of truth.
# clv_update.py uses canonical names internally; bets.json stores whatever
# was logged but we normalize before matching.

MARKET_CANONICAL = {
    # ML variants
    'ML':           'ML',
    'MONEYLINE':    'ML',
    # F5 variants
    'F5':           'F5 ML',
    'F5 ML':        'F5 ML',
    'F5ML':         'F5 ML',
    # Run Line variants
    'RL':           'Run Line',
    'RUN LINE':     'Run Line',
    'RUN_LINE':     'Run Line',
    'RUNLINE':      'Run Line',
    'Run Line':     'Run Line',
    # Total variants
    'TOTAL':        'Total',
    'Total':        'Total',
    'GAME TOTAL':   'Total',
    'Game Total':   'Total',
    'GAMETOTAL':    'Total',
    # Team Total variants
    'TT':           'Team Total',
    'TEAM TOTAL':   'Team Total',
    'TEAMTOTAL':    'Team Total',
    'Team Total':   'Team Total',
    # First inning
    'NRFI':         'NRFI',
    'YRFI':         'YRFI',
    # Props — CLV not available but we handle gracefully
    'K PROP':       'K Prop',
    'K Prop':       'K Prop',
    'PITCHER PROP': 'Pitcher Prop',
    'Pitcher Prop': 'Pitcher Prop',
}

# Markets where we can pull closing lines automatically
CL_SUPPORTED = {'ML', 'F5 ML', 'F5 RL', 'Run Line', 'Total', 'Team Total', 'NRFI', 'YRFI'}
# Markets where CLV is unavailable from API (mark as such, don't leave null)
CL_UNAVAILABLE = {'K Prop', 'Pitcher Prop'}  # NRFI/YRFI moved to CL_SUPPORTED (Kalshi has them)

# Odds API market keys by canonical name
ODDS_API_MARKET_KEY = {
    'ML':        'h2h',
    'F5 ML':     'h2h_1st_5_innings',
    'F5 RL':     'spreads_1st_5_innings',
    'Run Line':  'spreads',
    'Total':     'totals',
    'Team Total':'team_totals',
    'NRFI':      'h2h_1st_1_innings',
    'YRFI':      'h2h_1st_1_innings',
}

# Markets requiring per-event endpoint (not available in bulk /odds)
# These are "additional markets" in Odds API terminology
ADDITIONAL_MARKETS = {'F5 ML', 'F5 RL', 'NRFI', 'YRFI'}
BULK_MARKETS       = {'ML', 'Run Line', 'Total', 'Team Total'}

# Sharp book preference order for closing line extraction
# Kalshi first — it's where we actually place bets, so CLV vs Kalshi is the primary signal.
# Pinnacle is the sharpest traditional book (fallback if Kalshi has no data for a market).
SHARP_ORDER = [
    'kalshi', 'pinnacle', 'lowvig', 'draftkings', 'fanduel', 'betmgm',
    'williamhill_us', 'fanatics', 'bovada', 'betonlineag',
    'betrivers', 'betus', 'mybookieag',
]

# ── Team name / abbreviation master map ──────────────────────────────────────
# Maps every known representation → canonical abbreviation used in bets.json
TEAM_TO_ABBR = {
    # Full names
    'Arizona Diamondbacks': 'ARI',  'Atlanta Braves': 'ATL',
    'Baltimore Orioles': 'BAL',     'Boston Red Sox': 'BOS',
    'Chicago Cubs': 'CHC',          'Chicago White Sox': 'CWS',
    'Cincinnati Reds': 'CIN',       'Cleveland Guardians': 'CLE',
    'Colorado Rockies': 'COL',      'Detroit Tigers': 'DET',
    'Houston Astros': 'HOU',        'Kansas City Royals': 'KC',
    'Los Angeles Angels': 'LAA',    'Los Angeles Dodgers': 'LAD',
    'Miami Marlins': 'MIA',         'Milwaukee Brewers': 'MIL',
    'Minnesota Twins': 'MIN',       'New York Mets': 'NYM',
    'New York Yankees': 'NYY',      'Oakland Athletics': 'ATH',
    'Athletics': 'ATH',             'Las Vegas Athletics': 'ATH',
    'Philadelphia Phillies': 'PHI', 'Pittsburgh Pirates': 'PIT',
    'San Diego Padres': 'SD',       'San Francisco Giants': 'SF',
    'Seattle Mariners': 'SEA',      'St. Louis Cardinals': 'STL',
    'Tampa Bay Rays': 'TB',         'Texas Rangers': 'TEX',
    'Toronto Blue Jays': 'TOR',     'Washington Nationals': 'WSH',
    # Common abbr variants (legacy bets.json values)
    'ARI': 'ARI',  'AZ': 'ARI',    # AZ used in some old bets
    'ATL': 'ATL',  'BAL': 'BAL',   'BOS': 'BOS',
    'CHC': 'CHC',  'CWS': 'CWS',   'CIN': 'CIN',
    'CLE': 'CLE',  'COL': 'COL',   'DET': 'DET',
    'HOU': 'HOU',  'KC':  'KC',    'LAA': 'LAA',
    'LAD': 'LAD',  'MIA': 'MIA',   'MIL': 'MIL',
    'MIN': 'MIN',  'NYM': 'NYM',   'NYY': 'NYY',
    'OAK': 'ATH',  'ATH': 'ATH',   'PHI': 'PHI',
    'PIT': 'PIT',  'SD':  'SD',    'SF':  'SF',
    'SEA': 'SEA',  'STL': 'STL',   'TB':  'TB',
    'TEX': 'TEX',  'TOR': 'TOR',   'WSH': 'WSH',
}

def normalize_market(raw):
    """Return canonical market name for any alias."""
    if not raw: return None
    return MARKET_CANONICAL.get(raw.strip(), MARKET_CANONICAL.get(raw.strip().upper()))

def normalize_confidence(raw):
    """Return Title-case confidence tier. Discard invalid tiers."""
    if not raw: return None
    r = raw.strip().title()   # HIGH→High, MEDIUM→Medium, etc.
    if r == 'Low': return 'Paper'  # LOW is not a real tier — treat as Paper
    if r in ('High', 'Medium', 'Paper'): return r
    return None

def to_abbr(name):
    """Convert any team name/abbr to canonical abbreviation."""
    if not name: return ''
    # Direct lookup
    a = TEAM_TO_ABBR.get(name.strip())
    if a: return a
    # Try uppercase
    a = TEAM_TO_ABBR.get(name.strip().upper())
    if a: return a
    # Try title case
    a = TEAM_TO_ABBR.get(name.strip().title())
    if a: return a
    # Fuzzy: match by first word of team name (e.g. 'Yankees' → NYY)
    first_word = name.strip().split()[0] if name.strip() else ''
    for full, abbr in TEAM_TO_ABBR.items():
        if first_word.lower() in full.lower() and len(first_word) > 3:
            return abbr
    # Last resort: first 3 chars uppercased
    return name.strip()[:3].upper()

def parse_game(game_str):
    """
    Parse 'AWAY @ HOME' or 'AWAY@HOME' or full team names.
    Returns (away_abbr, home_abbr) or (None, None).
    """
    if not game_str: return None, None
    sep = ' @ ' if ' @ ' in game_str else '@'
    parts = game_str.split(sep, 1)
    if len(parts) != 2: return None, None
    return to_abbr(parts[0].strip()), to_abbr(parts[1].strip())

def get_size(b):
    """Read bet size from either 'betSize' or 'size' field."""
    s = b.get('betSize') if b.get('betSize') is not None else b.get('size')
    try: return float(s) if s is not None else None
    except (TypeError, ValueError): return None

def get_betside(b, away_abbr):
    """
    Determine AWAY or HOME from betSide field, betTeam, or bet string.
    Returns 'AWAY', 'HOME', or None.
    """
    # Explicit betSide field (new bets)
    side = (b.get('betSide') or '').upper()
    if side in ('AWAY', 'HOME'): return side
    # betSide with direction like 'AWAY OVER' → AWAY
    if 'AWAY' in side: return 'AWAY'
    if 'HOME' in side: return 'HOME'
    # betTeam field (new bets)
    bet_team = b.get('betTeam') or ''
    if bet_team:
        ta = to_abbr(bet_team)
        if ta == away_abbr: return 'AWAY'
        if ta: return 'HOME'
    # bet string (old bets) — look for team abbr or name
    bet_str = b.get('bet') or ''
    if bet_str:
        # Check if away abbr appears in bet string
        if away_abbr and away_abbr in bet_str.upper():
            return 'AWAY'
        # Check full team names
        for part in bet_str.upper().split():
            ta = to_abbr(part)
            if ta == away_abbr: return 'AWAY'
    return None

def to_imp(price):
    """American odds → implied probability."""
    if price is None: return None
    try:
        p = float(price)
        return 100/(p+100) if p >= 0 else abs(p)/(abs(p)+100)
    except (TypeError, ValueError): return None

def calc_pl(price, size, result):
    """P&L in dollars given American odds, dollar size, and result."""
    if result not in ('WIN', 'LOSS', 'PUSH') or size is None or price is None:
        return None
    size = float(size)
    if result == 'PUSH': return 0.0
    if result == 'LOSS': return round(-size, 2)
    price = float(price)
    if price >= 0:
        return round(size * price / 100, 2)
    else:
        return round(size * 100 / abs(price), 2)

def get_result(b):
    return b.get('result') or b.get('status')

# ── API helpers ───────────────────────────────────────────────────────────────

def api_get(url):
    try:
        req = Request(url, headers={'Accept': 'application/json'})
        with urlopen(req, timeout=25) as resp:
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
    Pull completed game scores for date_str.
    Returns dict: (away_abbr, home_abbr) → {away_score, home_score, completed}
    """
    today = datetime.now(timezone.utc).date()
    game_date = datetime.strptime(date_str, '%Y-%m-%d').date()
    days_ago = (today - game_date).days
    days_from = max(1, days_ago + 1)

    url = (f"{BASE_URL}/sports/{SPORT}/scores"
           f"?apiKey={ODDS_API_KEY}&daysFrom={days_from}")

    print(f"  Fetching scores (daysFrom={days_from})...")
    data, remaining = api_get(url)
    if not data: return {}

    scores = {}
    for g in data:
        commence = g.get('commence_time', '')
        if date_str not in commence: continue
        if not g.get('completed'): continue

        away_a = to_abbr(g.get('away_team', ''))
        home_a = to_abbr(g.get('home_team', ''))
        sc = g.get('scores') or []

        away_score = home_score = None
        for s in sc:
            name_a = to_abbr(s.get('name', ''))
            try: val = int(s.get('score', 0))
            except (ValueError, TypeError): val = 0
            if name_a == away_a: away_score = val
            elif name_a == home_a: home_score = val

        if away_score is not None and home_score is not None:
            scores[(away_a, home_a)] = {
                'away_score': away_score,
                'home_score': home_score,
                'completed': True,
            }
            print(f"    {away_a} {away_score} @ {home_a} {home_score}")

    print(f"  {len(scores)} completed games | credits_remaining={remaining}")
    return scores

def determine_result(b, scores, away_abbr, home_abbr, canonical_mkt):
    """Determine WIN/LOSS/PUSH from scores. Returns (result, away_score, home_score)."""
    sc = scores.get((away_abbr, home_abbr))
    if not sc: return None, None, None

    away_sc = sc['away_score']
    home_sc = sc['home_score']
    bet_side = get_betside(b, away_abbr)
    line = b.get('line')

    if canonical_mkt == 'ML':
        if away_sc > home_sc: winner = 'AWAY'
        elif home_sc > away_sc: winner = 'HOME'
        else: return 'PUSH', away_sc, home_sc
        return ('WIN' if bet_side == winner else 'LOSS'), away_sc, home_sc

    if canonical_mkt == 'Run Line':
        if line is None: return None, away_sc, home_sc
        if bet_side == 'HOME':
            margin = home_sc - away_sc + float(line)
        else:
            margin = away_sc - home_sc + float(line)
        if margin > 0: return 'WIN', away_sc, home_sc
        if margin < 0: return 'LOSS', away_sc, home_sc
        return 'PUSH', away_sc, home_sc

    if canonical_mkt == 'Total':
        total = away_sc + home_sc
        line_val = float(line) if line is not None else None
        if line_val is None: return None, away_sc, home_sc
        side_upper = (bet_side or '').upper()
        # Infer OVER/UNDER from bet string if betSide doesn't carry it
        bet_str = (b.get('bet') or b.get('betTeam') or '').upper()
        is_over = 'OVER' in side_upper or 'OVER' in bet_str or bet_str.startswith('O ')
        is_under = 'UNDER' in side_upper or 'UNDER' in bet_str or bet_str.startswith('U ')
        if not is_over and not is_under:
            # Fall back: check bet string for 'U' prefix
            raw = b.get('bet') or ''
            is_under = raw.strip().upper().startswith('U') or 'UNDER' in raw.upper()
            is_over = not is_under
        if is_over:
            if total > line_val: return 'WIN', away_sc, home_sc
            if total < line_val: return 'LOSS', away_sc, home_sc
            return 'PUSH', away_sc, home_sc
        else:
            if total < line_val: return 'WIN', away_sc, home_sc
            if total > line_val: return 'LOSS', away_sc, home_sc
            return 'PUSH', away_sc, home_sc

    if canonical_mkt == 'Team Total':
        bet_str = (b.get('bet') or b.get('betTeam') or '').upper()
        is_away_side = 'AWAY' in (bet_side or '') or away_abbr in bet_str
        team_sc = away_sc if is_away_side else home_sc
        line_val = float(line) if line is not None else None
        if line_val is None:
            # Try to extract from bet string
            m = re.search(r'(\d+\.?\d*)\s*$', b.get('bet') or '')
            if m:
                try: line_val = float(m.group(1))
                except ValueError: pass
        if line_val is None: return None, away_sc, home_sc
        is_over = 'OVER' in bet_str or '+' in bet_str
        if is_over:
            if team_sc > line_val: return 'WIN', away_sc, home_sc
            if team_sc < line_val: return 'LOSS', away_sc, home_sc
            return 'PUSH', away_sc, home_sc
        else:
            if team_sc < line_val: return 'WIN', away_sc, home_sc
            if team_sc > line_val: return 'LOSS', away_sc, home_sc
            return 'PUSH', away_sc, home_sc

    # F5 ML — need inning-by-inning data; flag for manual
    if canonical_mkt == 'F5 ML':
        return None, away_sc, home_sc

    # NRFI/YRFI — flag for manual
    return None, away_sc, home_sc

# ── Historical odds fetch ─────────────────────────────────────────────────────


KALSHI_BASE = 'https://api.elections.kalshi.com/trade-api/v2'

# Map our canonical market names to Kalshi title keywords
KALSHI_MARKET_KEYWORDS = {
    'ML':         ['winner', 'game winner', 'moneyline'],
    'F5 ML':      ['first 5', '5 innings', 'f5', 'first five'],
    'NRFI':       ['nrfi', 'no run first inning', 'no runs first'],
    'YRFI':       ['yrfi', 'yes run first inning', 'run in first'],
    'Run Line':   ['run line', 'spread'],
    'Total':      ['total runs', 'over/under', 'game total'],
    'Team Total': ['team total', 'runs scored'],
}

# Team abbr to Kalshi short code (as seen in event_ticker suffixes)
# e.g. event_ticker: KXMLBGAME-24Jun0317:10NYYCLE → teams=NYYCLE
# NYY=NYY, CLE=CLE → teamsStr = NYYCLE (away first, then home)
# NOTE: Kalshi uses full 2-3 char MLB abbreviations in most cases
KALSHI_ABBR = {
    'ARI':'ARI', 'ATL':'ATL', 'BAL':'BAL', 'BOS':'BOS', 'CHC':'CHC',
    'CWS':'CWS', 'CIN':'CIN', 'CLE':'CLE', 'COL':'COL', 'DET':'DET',
    'HOU':'HOU', 'KC':'KC',   'LAA':'LAA', 'LAD':'LAD', 'MIA':'MIA',
    'MIL':'MIL', 'MIN':'MIN', 'NYM':'NYM', 'NYY':'NYY', 'ATH':'ATH',
    'PHI':'PHI', 'PIT':'PIT', 'SD':'SD',   'SF':'SF',   'SEA':'SEA',
    'STL':'STL', 'TB':'TB',   'TEX':'TEX', 'TOR':'TOR', 'WSH':'WSH',
}


def kalshi_api_get(url):
    """Call Kalshi API — no auth required for public market data."""
    try:
        req = urllib.request.Request(url, headers={
            'Accept': 'application/json',
            'User-Agent': 'Mozilla/5.0',
        })
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read())
    except Exception as e:
        print(f"    Kalshi API error: {e}")
        return None


def kalshi_price_to_implied(price_dollars):
    """Convert Kalshi yes_bid/yes_ask (dollars, 0.01-0.99) to implied probability."""
    try:
        p = float(price_dollars)
        return round(p, 4) if 0 < p < 1 else None
    except (TypeError, ValueError):
        return None


def implied_to_american(impl):
    """Convert implied probability to American odds."""
    if impl is None or impl <= 0 or impl >= 1: return None
    if impl >= 0.5:
        return round(-impl / (1 - impl) * 100)
    else:
        return round((1 - impl) / impl * 100)


def fetch_kalshi_settled_markets(date_str):
    """
    Fetch settled Kalshi markets for a given date using timestamp filters.
    Uses min_settled_ts/max_settled_ts for efficient date filtering.
    Markets within 3 months are available via /markets (live endpoint).
    Older markets require /historical/markets.
    """
    try:
        dt = datetime.strptime(date_str, '%Y-%m-%d')
        # Settlement window: game date midnight to next day midnight UTC
        # MLB games settle same day they're played (within hours of final out)
        min_ts = int(dt.replace(hour=0, minute=0, second=0, tzinfo=timezone.utc).timestamp())
        max_ts = int((dt + timedelta(days=2)).replace(
            hour=6, minute=0, second=0, tzinfo=timezone.utc).timestamp())
        # Also compute Kalshi date string for event_ticker matching (fallback)
        kalshi_date = dt.strftime('%y') + dt.strftime('%b') + dt.strftime('%d')
    except Exception as e:
        print(f"    Date parse error: {e}")
        return []

    print(f"    Fetching Kalshi markets for {date_str} (ts={min_ts}..{max_ts})...")

    all_markets = []

    # Fetch with timestamp filter — much more efficient than paginating all settled
    for attempt, base_url in enumerate([
        # Try live endpoint first (within 3-month window)
        f"{KALSHI_BASE}/markets?status=settled&min_settled_ts={min_ts}&max_settled_ts={max_ts}&limit=1000",
        # Fallback: historical endpoint for older dates
        f"{KALSHI_BASE}/historical/markets?min_settled_ts={min_ts}&max_settled_ts={max_ts}&limit=1000",
    ]):
        data = kalshi_api_get(base_url)
        if not data:
            continue
        markets = data.get('markets', [])
        if markets:
            all_markets = markets
            print(f"    Found {len(markets)} markets via {'live' if attempt==0 else 'historical'} endpoint")
            break
        time.sleep(0.3)

    if not all_markets:
        # Last resort: paginate without date filter, match by event_ticker date string
        print(f"    Falling back to pagination + event_ticker filter (kalshi_date={kalshi_date})")
        cursor = ''
        for page in range(8):
            url = f"{KALSHI_BASE}/markets?status=settled&limit=200"
            if cursor: url += f"&cursor={cursor}"
            data = kalshi_api_get(url)
            if not data: break
            markets = data.get('markets', [])
            day = [m for m in markets if kalshi_date in (m.get('event_ticker','') or '')]
            all_markets.extend(day)
            cursor = data.get('cursor', '')
            if not cursor or not markets: break
            time.sleep(0.2)
        print(f"    Pagination found {len(all_markets)} markets")

    print(f"    Titles sample: {[(m.get('ticker','')[:20], (m.get('title') or '')[:30]) for m in all_markets[:5]]}")
    return all_markets


def find_kalshi_ticker(markets, away_abbr, home_abbr, canonical_mkt, bet_side):
    """
    Find the Kalshi market ticker for a specific bet.
    Returns (ticker, is_yes_side) or (None, None)
    """
    keywords = KALSHI_MARKET_KEYWORDS.get(canonical_mkt, [])
    bet_str_upper = bet_side.upper() if bet_side else ''

    # Build team string patterns to match in event_ticker
    away_k = KALSHI_ABBR.get(away_abbr, away_abbr)
    home_k = KALSHI_ABBR.get(home_abbr, home_abbr)

    for m in markets:
        et    = (m.get('event_ticker') or '').upper()
        title = (m.get('title') or '').lower()
        tk    = m.get('ticker', '')

        # Must match the game (both teams in event_ticker)
        if away_k.upper() not in et and home_k.upper() not in et:
            continue

        # Must match market type by title keywords
        if not any(kw in title for kw in keywords):
            continue

        # Determine if our bet is the YES side
        # For ML: YES = away team wins (typically) — but Kalshi frames each side as separate market
        # Each market has one team — the YES side means that team wins
        tk_upper = tk.upper()
        if tk_upper.endswith(f'-{away_k.upper()}') or tk_upper.endswith(f'-{home_k.upper()}'):
            # This ticker is for a specific team
            is_away_ticker = tk_upper.endswith(f'-{away_k.upper()}')
            bet_is_away    = 'AWAY' in bet_str_upper or away_abbr in bet_str_upper

            # We want the ticker for the side we bet on
            if is_away_ticker == bet_is_away:
                return tk, True  # YES = our team wins

    # Fallback: return first matching market for this game/type
    for m in markets:
        et    = (m.get('event_ticker') or '').upper()
        title = (m.get('title') or '').lower()
        if (away_k.upper() in et or home_k.upper() in et) and any(kw in title for kw in keywords):
            return m.get('ticker'), True

    return None, None


def fetch_kalshi_closing_price(ticker, game_date_str, commence_hour_utc=None):
    """
    Fetch the Kalshi closing price for a market ticker.
    Uses GET /historical/markets/{ticker}/candlesticks with 1-minute intervals.
    Returns implied probability (0-1) as the closing yes_bid/yes_ask midpoint.
    """
    if not ticker:
        return None

    try:
        dt = datetime.strptime(game_date_str, '%Y-%m-%d')
        # Game typically starts 6pm-10pm ET = 22:00-02:00 UTC
        # Get candlesticks for the last hour before close
        # Use end_ts = day end (next day 06:00 UTC = all games finished)
        end_ts   = int((dt + timedelta(days=1)).replace(
            hour=4, minute=0, second=0, microsecond=0,
            tzinfo=timezone.utc).timestamp())
        start_ts = end_ts - 7200  # 2 hours before = window covering game start
    except Exception as e:
        print(f"    Timestamp error: {e}")
        return None

    # Try live endpoint first (within 3 months = all our bets are here)
    url = (f"{KALSHI_BASE}/markets/{ticker}/candlesticks"
           f"?start_ts={start_ts}&end_ts={end_ts}&period_interval=60")
    data = kalshi_api_get(url)

    # If not found in live, try historical endpoint
    if not data or not data.get('candlesticks'):
        url = (f"{KALSHI_BASE}/historical/markets/{ticker}/candlesticks"
               f"?start_ts={start_ts}&end_ts={end_ts}&period_interval=60")
        data = kalshi_api_get(url)

    if not data:
        return None

    candles = data.get('candlesticks', [])
    if not candles:
        return None

    # Use the last candle before market close (highest end_period_ts)
    last = max(candles, key=lambda c: c.get('end_period_ts', 0))

    # Midpoint of yes_bid and yes_ask as closing implied prob
    yes_bid = kalshi_price_to_implied(last.get('yes_bid', {}).get('close'))
    yes_ask = kalshi_price_to_implied(last.get('yes_ask', {}).get('close'))

    if yes_bid is not None and yes_ask is not None:
        return round((yes_bid + yes_ask) / 2, 4)
    return yes_bid or yes_ask


def fetch_historical(date_str, markets_csv):
    """
    Fetch historical odds from The Odds API at multiple snapshots.
    Uses bookmakers=kalshi to prioritize Kalshi closing lines.
    Falls back to all US/EU books if Kalshi has no data for a market.
    Response format: {timestamp, previous_timestamp, next_timestamp, data: [...games]}
    Returns list of game objects from best snapshot.
    """
    next_day = (datetime.strptime(date_str, '%Y-%m-%d') + timedelta(days=1)).strftime('%Y-%m-%d')
    commence_from = date_str + 'T00:00:00Z'
    commence_to   = next_day + 'T06:00:00Z'

    # Try snapshots closest to game time (most representative closing price)
    # Use T02:00:00Z (10pm ET next day) as primary — all games finished
    snapshots = [
        next_day  + 'T02:00:00Z',   # 10pm ET — best closing snapshot
        date_str  + 'T23:00:00Z',   # 7pm ET
        date_str  + 'T21:00:00Z',   # 5pm ET
        date_str  + 'T19:00:00Z',   # 3pm ET (day games)
    ]

    # First pass: try Kalshi-only (bookmakers=kalshi)
    # Kalshi is the market we bet on — its closing line is the gold standard CLV target
    kalshi_games = []
    for snapshot in snapshots[:2]:
        # Kalshi is in the us_ex region (US Exchanges)
        # Use both bookmakers=kalshi AND regions=us_ex for maximum compatibility
        url = (f"{BASE_URL}/historical/sports/{SPORT}/odds"
               f"?apiKey={ODDS_API_KEY}&regions=us_ex&bookmakers=kalshi"
               f"&markets={markets_csv}"
               f"&oddsFormat=american"
               f"&commenceTimeFrom={commence_from}"
               f"&commenceTimeTo={commence_to}"
               f"&date={snapshot}")

        print(f"  Historical [us_ex/Kalshi|{markets_csv}] @ {snapshot}...", end=' ')
        data, remaining = api_get(url)
        if data is None:
            print("FAILED")
            continue
        # Historical endpoint wraps response: {timestamp, data: [...games]}
        games = data.get('data', []) if isinstance(data, dict) else data
        print(f"{len(games)} games | credits={remaining}")
        if len(games) > len(kalshi_games):
            kalshi_games = games
        if len(kalshi_games) >= 10:
            break
        time.sleep(0.4)

    if kalshi_games:
        print(f"  Using Kalshi closing lines ({len(kalshi_games)} games)")
        return kalshi_games

    # Fallback: all US/EU books (Pinnacle etc) if Kalshi had no data
    print(f"  Kalshi had no data — falling back to all books")
    best_games = []
    for snapshot in snapshots:
        # Fallback: Pinnacle and other sharp books (us region)
        url = (f"{BASE_URL}/historical/sports/{SPORT}/odds"
               f"?apiKey={ODDS_API_KEY}&regions=us&markets={markets_csv}"
               f"&oddsFormat=american"
               f"&commenceTimeFrom={commence_from}"
               f"&commenceTimeTo={commence_to}"
               f"&date={snapshot}")

        print(f"  Historical [us/Pinnacle|{markets_csv}] @ {snapshot}...", end=' ')
        data, remaining = api_get(url)
        if data is None:
            print("FAILED")
            continue
        # Historical endpoint wraps response: {timestamp, data: [...games]}
        games = data.get('data', []) if isinstance(data, dict) else data
        print(f"{len(games)} games | credits={remaining}")
        if len(games) > len(best_games):
            best_games = games
        if len(best_games) >= 10:
            break
        time.sleep(0.4)

    return best_games

def fetch_historical_events(date_str):
    """
    Fetch event list for a date from historical API.
    Returns dict: (away_abbr, home_abbr) -> event_id
    Cost: 1 credit (events endpoint is cheap)
    """
    next_day = (datetime.strptime(date_str, '%Y-%m-%d') + timedelta(days=1)).strftime('%Y-%m-%d')
    snapshot = next_day + 'T02:00:00Z'  # 10pm ET — all games listed

    url = (f"{BASE_URL}/historical/sports/{SPORT}/events"
           f"?apiKey={ODDS_API_KEY}"
           f"&commenceTimeFrom={date_str}T00:00:00Z"
           f"&commenceTimeTo={next_day}T06:00:00Z"
           f"&date={snapshot}")

    print(f"  Fetching historical events for {date_str}...", end=' ')
    data, remaining = api_get(url)
    if not data:
        print("FAILED")
        return {}

    events = data.get('data', []) if isinstance(data, dict) else data
    print(f"{len(events)} events | credits={remaining}")

    result = {}
    for e in events:
        away = to_abbr(e.get('away_team', ''))
        home = to_abbr(e.get('home_team', ''))
        eid  = e.get('id', '')
        if away and home and eid:
            result[(away, home)] = eid
    return result


def fetch_historical_event_odds(event_id, date_str, markets_csv):
    """
    Fetch historical odds for a single event using the per-event endpoint.
    Required for additional markets: h2h_1st_5_innings, h2h_1st_1_innings etc.
    Cost: 10 credits per unique market returned.
    Uses us_ex (Kalshi) region first, falls back to us (Pinnacle).
    """
    next_day = (datetime.strptime(date_str, '%Y-%m-%d') + timedelta(days=1)).strftime('%Y-%m-%d')
    snapshot = next_day + 'T02:00:00Z'

    # Try Kalshi (us_ex) first
    for regions in ['us_ex', 'us']:
        url = (f"{BASE_URL}/historical/sports/{SPORT}/events/{event_id}/odds"
               f"?apiKey={ODDS_API_KEY}&regions={regions}&bookmakers=kalshi"
               f"&markets={markets_csv}&oddsFormat=american&date={snapshot}")
        if regions == 'us':
            # Fallback: remove bookmakers filter, use Pinnacle
            url = (f"{BASE_URL}/historical/sports/{SPORT}/events/{event_id}/odds"
                   f"?apiKey={ODDS_API_KEY}&regions=us"
                   f"&markets={markets_csv}&oddsFormat=american&date={snapshot}")

        data, remaining = api_get(url)
        if not data:
            continue

        game_data = data.get('data') if isinstance(data, dict) else data
        if game_data and game_data.get('bookmakers'):
            bks = [bk['key'] for bk in game_data.get('bookmakers', [])]
            print(f"    event {event_id[:8]}.. [{regions}]: bookmakers={bks} | credits={remaining}")
            return game_data
        time.sleep(0.3)

    return None


def merge_game_pools(*pools):
    """Merge multiple game pools by event ID, combining bookmakers."""
    merged = {}
    for pool in pools:
        for g in pool:
            gid = g.get('id') or g.get('eventId', '')
            if gid not in merged:
                merged[gid] = dict(g)
                merged[gid]['bookmakers'] = list(g.get('bookmakers') or [])
            else:
                existing_keys = {bk['key'] for bk in merged[gid]['bookmakers']}
                for bk in (g.get('bookmakers') or []):
                    if bk['key'] not in existing_keys:
                        merged[gid]['bookmakers'].append(bk)
                    else:
                        # Merge markets into existing bookmaker
                        existing_bk = next(b for b in merged[gid]['bookmakers'] if b['key'] == bk['key'])
                        existing_mkt_keys = {m['key'] for m in existing_bk.get('markets', [])}
                        for mkt in (bk.get('markets') or []):
                            if mkt['key'] not in existing_mkt_keys:
                                existing_bk.setdefault('markets', []).append(mkt)
    return list(merged.values())

def match_game(games, away_abbr, home_abbr):
    """Find game in pool matching away/home abbreviations."""
    for g in games:
        ga = to_abbr(g.get('away_team', ''))
        gh = to_abbr(g.get('home_team', ''))
        if ga == away_abbr and gh == home_abbr: return g
    # Relaxed: match home only
    for g in games:
        gh = to_abbr(g.get('home_team', ''))
        if gh == home_abbr: return g
    return None

def get_sharp_market(game, market_key):
    """Return (book_key, market_data) from sharpest available book."""
    for bk_key in SHARP_ORDER:
        bk = next((b for b in (game.get('bookmakers') or []) if b['key'] == bk_key), None)
        if not bk: continue
        mkt = next((m for m in bk.get('markets', []) if m['key'] == market_key), None)
        if mkt: return bk_key, mkt
    return None, None

# ── Closing line extraction ───────────────────────────────────────────────────

def extract_closing(b, game, canonical_mkt, away_abbr):
    """
    Extract closing line data for a bet from the game's historical odds.
    Returns dict with keys: betPrice, oppPrice, book, closingStr, impliedProb
    Returns None if not found.
    """
    api_key = ODDS_API_MARKET_KEY.get(canonical_mkt)
    if not api_key: return None

    bk_key, mkt = get_sharp_market(game, api_key)
    if not mkt: return None

    outcomes = mkt.get('outcomes') or []
    bet_side = get_betside(b, away_abbr)
    bet_str = (b.get('bet') or b.get('betTeam') or '').upper()

    if canonical_mkt == 'ML':
        is_away = bet_side == 'AWAY'
        for o in outcomes:
            o_is_away = to_abbr(o['name']) == away_abbr
            if o_is_away == is_away:
                return {
                    'betPrice': o['price'],
                    'book': bk_key,
                    'closingStr': f"{'+' if o['price'] >= 0 else ''}{o['price']} [{bk_key}]",
                }

    if canonical_mkt == 'F5 ML':
        is_away = bet_side == 'AWAY'
        for o in outcomes:
            o_is_away = to_abbr(o['name']) == away_abbr
            if o_is_away == is_away:
                return {
                    'betPrice': o['price'],
                    'book': bk_key,
                    'closingStr': f"F5 {'+' if o['price'] >= 0 else ''}{o['price']} [{bk_key}]",
                }

    if canonical_mkt == 'Run Line':
        bet_line = b.get('line')
        is_away = bet_side == 'AWAY'
        is_minus = float(bet_line or -1.5) < 0
        for o in outcomes:
            o_is_away = to_abbr(o['name']) == away_abbr
            o_point = o.get('point', 0)
            if o_is_away == is_away and (o_point < 0) == is_minus:
                p = o['price']
                return {
                    'betPrice': p,
                    'book': bk_key,
                    'closingStr': f"RL {o_point:+.1f} {'+' if p >= 0 else ''}{p} [{bk_key}]",
                }

    if canonical_mkt == 'Total':
        is_over = 'OVER' in bet_str or (b.get('bet') or '').upper().startswith('O')
        target_name = 'Over' if is_over else 'Under'
        for o in outcomes:
            if o['name'].lower() == target_name.lower():
                p = o['price']
                pt = o.get('point', '')
                return {
                    'betPrice': p,
                    'book': bk_key,
                    'closingStr': f"{target_name} {pt} {'+' if p >= 0 else ''}{p} [{bk_key}]",
                }

    if canonical_mkt == 'Team Total':
        is_away_side = 'AWAY' in (bet_side or '') or away_abbr in bet_str
        is_over = 'OVER' in bet_str
        for o in outcomes:
            desc = (o.get('description') or '').upper()
            name = (o.get('name') or '').upper()
            o_is_away = away_abbr in desc
            o_is_over = 'OVER' in name
            if o_is_away == is_away_side and o_is_over == is_over:
                p = o['price']
                pt = o.get('point', '')
                return {
                    'betPrice': p,
                    'book': bk_key,
                    'closingStr': f"TT {'OVER' if is_over else 'UNDER'} {pt} {'+' if p >= 0 else ''}{p} [{bk_key}]",
                }

    if canonical_mkt in ('NRFI', 'YRFI'):
        # h2h_1st_1_innings has two outcomes: each team's side
        # NRFI = under on runs in 1st inning — approximate via the lower-priced side
        # In practice, Kalshi lists this as a binary yes/no first-inning run market
        # The "No" side (NRFI) is typically the home team winning when neither scores
        # Best approach: take whichever outcome matches the bet string
        bet_str = (b.get('bet') or b.get('betTeam') or '').upper()
        for o in outcomes:
            o_name = o['name'].upper()
            if canonical_mkt == 'NRFI' and ('NO' in o_name or 'NRFI' in o_name or 'UNDER' in o_name):
                p = o['price']
                return {'betPrice': p, 'book': bk_key, 'closingStr': f"NRFI {'+' if p>=0 else ''}{p} [{bk_key}]"}
            if canonical_mkt == 'YRFI' and ('YES' in o_name or 'YRFI' in o_name or 'OVER' in o_name):
                p = o['price']
                return {'betPrice': p, 'book': bk_key, 'closingStr': f"YRFI {'+' if p>=0 else ''}{p} [{bk_key}]"}
        # Fallback: just take first outcome for NRFI, second for YRFI
        if outcomes:
            idx = 0 if canonical_mkt == 'NRFI' else min(1, len(outcomes)-1)
            p = outcomes[idx]['price']
            return {'betPrice': p, 'book': bk_key, 'closingStr': f"{canonical_mkt} {'+' if p>=0 else ''}{p} [{bk_key}]"}

    return None

def calc_clv(b, closing):
    """
    CLV% = (closingImpliedProb - betImpliedProb) * 100
    Positive CLV = closing implied prob is higher than our bet implied prob
                 = market moved toward our side = we got a better price than the close.
    Example: bet +150 (impl 40%), closes +100 (impl 50%) → CLV = +10% (GOOD)
    Example: bet -150 (impl 60%), closes -200 (impl 67%) → CLV = +7% (GOOD — shorter fav)
    Example: bet -300 (impl 75%), closes -150 (impl 60%) → CLV = -15% (BAD — line moved away)
    """
    our_imp   = to_imp(b.get('price'))
    close_imp = to_imp(closing.get('betPrice'))
    if our_imp is None or close_imp is None: return None
    clv = (close_imp - our_imp) * 100
    return round(clv, 2)

# ── BET_LOG.md rebuilder ──────────────────────────────────────────────────────

def rebuild_log(bets):
    from collections import defaultdict
    by_date = defaultdict(list)
    for b in bets:
        by_date[b['date']].append(b)

    real  = [b for b in bets if normalize_confidence(b.get('confidence','')) != 'Paper']
    tw    = sum(1 for b in real if get_result(b) == 'WIN')
    tl    = sum(1 for b in real if get_result(b) == 'LOSS')
    tp    = sum(1 for b in bets if get_result(b) == 'PUSH')
    tpl   = sum(float(b.get('pl') or 0) for b in bets)
    pend  = sum(1 for b in bets if get_result(b) not in ('WIN','LOSS','PUSH','VOID','NO_ACTION'))
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
        dr  = [b for b in db if normalize_confidence(b.get('confidence','')) != 'Paper']
        dw  = sum(1 for b in dr if get_result(b) == 'WIN')
        dl  = sum(1 for b in dr if get_result(b) == 'LOSS')
        dpl = sum(float(b.get('pl') or 0) for b in db)
        lines.append(f'### {date} — {dw}W {dl}L | P/L: ${dpl:+.2f}')
        lines.append('| ID | Mkt | Bet | Price | Edge% | Conf | Size | Result | P/L | CLV% |')
        lines.append('|---|---|---|---|---|---|---|---|---|---|')
        for b in db:
            edge  = f"{b.get('edgePct','')}%" if b.get('edgePct') is not None else '—'
            pl    = f"${float(b.get('pl') or 0):+.2f}" if b.get('pl') is not None else '—'
            clv   = f"{float(b.get('clv')):+.1f}%" if b.get('clv') is not None else '—'
            res   = get_result(b) or 'PENDING'
            bet_s = b.get('betTeam') or b.get('bet') or ''
            sz    = get_size(b) or ''
            lines.append(
                f"| {b.get('id','')} | {b.get('market','')} | {bet_s} "
                f"| {b.get('price','')} | {edge} | {b.get('confidence','')} "
                f"| {sz} | {res} | {pl} | {clv} |"
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

    print(f"\n=== CLV Update v3.0 for {date} ===\n")

    with open('bets.json') as f:
        bets = json.load(f)

    date_bets = [b for b in bets if b.get('date') == date]
    print(f"Bets for {date}: {len(date_bets)}")

    # ── Step 0: Ensure all bets have an 'id' field ─────────────────────────
    # Bets logged in simplified format may lack 'id'. Generate stable IDs.
    import re as _re
    existing_ids = {b.get('id') for b in bets if b.get('id')}
    max_seq = 0
    for bid in existing_ids:
        m = _re.search(r'(\d+)$', str(bid))
        if m:
            max_seq = max(max_seq, int(m.group(1)))
    for b in date_bets:
        if not b.get('id'):
            max_seq += 1
            b['id'] = f"{date}-{str(max_seq).zfill(3)}"
            # Also backfill into master bets list so it gets saved
    print(f"  IDs assigned to {sum(1 for b in date_bets if b.get('id'))} bets")

    # ── Step 1: Normalize fields on all date_bets ──────────────────────────
    print("\n--- Step 1: Field normalization ---")
    normalized = 0
    for b in date_bets:
        # Normalize market name
        raw_mkt = b.get('market', '')
        canon = normalize_market(raw_mkt)
        if canon and canon != raw_mkt:
            b['market'] = canon
            normalized += 1
        # Normalize confidence
        raw_conf = b.get('confidence', '')
        norm_conf = normalize_confidence(raw_conf)
        if norm_conf and norm_conf != raw_conf:
            b['confidence'] = norm_conf
            normalized += 1
        # Unify size fields: always write both, primary is 'size'
        s = get_size(b)
        if s is not None:
            b['size'] = s
            b['betSize'] = s
    print(f"  Normalized {normalized} field values")

    # ── Step 2: Pull scores and settle ────────────────────────────────────
    print("\n--- Step 2: Scores & Settlement ---")
    scores = fetch_scores(date)

    settled_this_run = 0
    f5_manual = []
    nrfi_yrfi_manual = []

    for b in date_bets:
        if get_result(b) in ('WIN', 'LOSS', 'PUSH', 'VOID', 'NO_ACTION'):
            continue

        canonical_mkt = normalize_market(b.get('market', ''))
        if not canonical_mkt:
            print(f"  ? {b['id']}: unrecognized market '{b.get('market')}'")
            continue

        away, home = parse_game(b.get('game', ''))
        if not away:
            print(f"  ? {b['id']}: cannot parse game '{b.get('game')}'")
            continue

        if canonical_mkt in ('F5 ML', 'F5 RL'):
            f5_manual.append(b['id'])
            continue  # Result needs manual settlement — but CLV is handled separately
        if canonical_mkt in ('NRFI', 'YRFI'):
            nrfi_yrfi_manual.append(b['id'])
            continue  # Result needs manual settlement — but CLV is handled separately

        result, away_sc, home_sc = determine_result(b, scores, away, home, canonical_mkt)
        if result is None:
            print(f"  ? {b['id']}: no score or result not determinable ({away}@{home} {canonical_mkt})")
            continue

        b['result'] = result
        b['status'] = 'SETTLED'
        b['awayScore'] = away_sc
        b['homeScore'] = home_sc
        # Always compute P&L from price + size
        b['pl'] = calc_pl(b.get('price'), get_size(b), result)
        settled_this_run += 1

        flag = '✓' if result == 'WIN' else ('↔' if result == 'PUSH' else '✗')
        pl_s = f"${b['pl']:+.2f}" if b['pl'] is not None else '—'
        print(f"  {flag} {b['id']} | {canonical_mkt} | {away_sc}-{home_sc} | {result} | {pl_s}")

    if f5_manual:
        print(f"\n  ⚠ F5 ML bets need manual settlement ({len(f5_manual)}):")
        for bid in f5_manual: print(f"    {bid}")
    if nrfi_yrfi_manual:
        print(f"\n  ⚠ NRFI/YRFI bets need manual settlement ({len(nrfi_yrfi_manual)}):")
        for bid in nrfi_yrfi_manual: print(f"    {bid}")
    print(f"\n  Auto-settled this run: {settled_this_run}")

    # ── Step 3: Pull closing lines and compute CLV ─────────────────────────
    print("\n--- Step 3: Closing Lines & CLV ---")

    # Mark unavailable markets (props only — all other markets are now supported)
    # Also clear stale market_unavailable flags on markets we now support (F5, NRFI, YRFI)
    for b in date_bets:
        mkt = normalize_market(b.get('market', ''))
        if mkt in CL_UNAVAILABLE and b.get('clv') is None:
            b['closingLineSource'] = 'market_unavailable'
            b['closingLine'] = None
            b['clv'] = None
        # Clear stale closing line data on supported markets so they get retried.
        # Covers: market_unavailable (old flag), FanDuel/oddsportal (wrong sources),
        # and any other non-authoritative source where clv is still None.
        elif mkt in CL_SUPPORTED and b.get('clv') is None:
            stale_sources = {'market_unavailable', 'FanDuel', 'fanduel', 'oddsportal',
                             'line_not_found', 'no_game_match', 'parse_error', 'not_applicable'}
            if b.get('closingLineSource') in stale_sources:
                b['closingLineSource'] = None
                b['closingLine'] = None

    # Which bets still need CLV?
    # CLV is independent of settlement — we can pull closing lines for any supported market
    # even if the bet hasn't been auto-settled yet (e.g. F5, NRFI/YRFI need manual settlement)
    clv_targets = [
        b for b in date_bets
        if b.get('clv') is None
        and normalize_market(b.get('market', '')) in CL_SUPPORTED
        and b.get('closingLineSource') not in ('expired_no_betTimeLine', 'market_unavailable', 'not_applicable')
        # Only skip CLV if the bet is still truly open (not yet happened)
        and b.get('status') not in ('OPEN',)
    ]
    print(f"CLV targets: {len(clv_targets)}")

    if clv_targets:
        # Split targets by endpoint type
        bulk_targets       = [b for b in clv_targets if normalize_market(b.get('market','')) in BULK_MARKETS]
        additional_targets = [b for b in clv_targets if normalize_market(b.get('market','')) in ADDITIONAL_MARKETS]

        print(f"  Bulk targets: {len(bulk_targets)} | Additional market targets: {len(additional_targets)}")

        # ── Bulk endpoint (ML, RL, Total, TT) ────────────────────────────────
        needed_api_keys = set()
        for b in bulk_targets:
            mkt = normalize_market(b.get('market', ''))
            ak = ODDS_API_MARKET_KEY.get(mkt)
            if ak: needed_api_keys.add(ak)

        all_games = []
        for api_key in sorted(needed_api_keys):
            time.sleep(0.4)
            games = fetch_historical(date, api_key)
            all_games = merge_game_pools(all_games, games) if all_games else games
        print(f"  Bulk games in pool: {len(all_games)}")

        # ── Per-event endpoint (F5, NRFI, YRFI) ──────────────────────────────
        event_game_cache = {}  # (away, home) -> game data with additional market odds
        if additional_targets:
            # Get event ID map once (cheap — 1 credit)
            event_ids = fetch_historical_events(date)
            print(f"  Event IDs found: {len(event_ids)}")

            # Determine which additional market keys are needed
            needed_add_keys = set()
            for b in additional_targets:
                mkt = normalize_market(b.get('market',''))
                ak  = ODDS_API_MARKET_KEY.get(mkt)
                if ak: needed_add_keys.add(ak)
            markets_csv = ','.join(sorted(needed_add_keys))

            # Fetch per-event odds for each unique game
            unique_games = set()
            for b in additional_targets:
                away, home = parse_game(b.get('game',''))
                if away and home: unique_games.add((away, home))

            for (away, home) in unique_games:
                event_id = event_ids.get((away, home))
                if not event_id:
                    print(f"  NO_EVENT_ID: {away}@{home}")
                    continue
                time.sleep(0.5)
                game_data = fetch_historical_event_odds(event_id, date, markets_csv)
                if game_data:
                    event_game_cache[(away, home)] = game_data

        # ── Clean up any diagnostic fields left in bets ────────────────────────
        for b in date_bets:
            b.pop('_kalshi_titles', None)
            b.pop('_kalshi_count', None)

        # ── Fetch Kalshi settled markets for this date (primary CLV source) ──
        print("\n  Fetching Kalshi settled markets (primary CLV source)...")
        kalshi_markets = fetch_kalshi_settled_markets(date)


        # ── Process all CLV targets ───────────────────────────────────────────
        clv_updated = 0
        for b in clv_targets:
            away, home = parse_game(b.get('game', ''))
            if not away:
                b['closingLineSource'] = 'parse_error'
                continue

            canonical_mkt = normalize_market(b.get('market', ''))
            is_additional  = canonical_mkt in ADDITIONAL_MARKETS
            bet_side       = (b.get('betSide') or b.get('betTeam') or b.get('bet') or '').upper()

            # ── Try Kalshi direct API first (most accurate — our actual market) ──
            kalshi_clv = None
            if kalshi_markets:
                ticker, is_yes = find_kalshi_ticker(kalshi_markets, away, home, canonical_mkt, bet_side)
                if ticker:
                    closing_impl = fetch_kalshi_closing_price(ticker, date)
                    if closing_impl is not None:
                        # Our bet implied prob (from American odds)
                        bet_impl = to_imp(b.get('price'))
                        if bet_impl is not None:
                            # CLV = (closing implied - bet implied) * 100
                            # For YES bet: higher closing price = market moved our way = positive CLV
                            kalshi_clv = round((closing_impl - bet_impl) * 100, 2)
                            close_american = implied_to_american(closing_impl)
                            close_str = f"{'+' if close_american>=0 else ''}{close_american} [kalshi]"
                            b['closingLine']          = close_str
                            b['closingLineSource']    = 'kalshi'
                            b['closingLineTimestamp'] = f"{date}T22:00:00Z"
                            b['clv']                  = kalshi_clv
                            if b.get('betTimeLine') is not None and kalshi_clv < -2.0:
                                b['clvNote'] = 'adverse-move'
                            flag  = '✓' if kalshi_clv > 0 else '✗'
                            print(f"  {flag} {b['id']} | {canonical_mkt} | Kalshi CL: {close_str} | CLV: {kalshi_clv:+.2f}%")
                            clv_updated += 1
                            continue
                        time.sleep(0.2)

            # ── Fall back to Odds API (Pinnacle) for bulk markets ─────────────
            if not is_additional:
                game = match_game(all_games, away, home)
                if game:
                    closing = extract_closing(b, game, canonical_mkt, away)
                    if closing:
                        clv = calc_clv(b, closing)
                        b['closingLine']          = closing['closingStr']
                        b['closingLineSource']    = closing['book']
                        b['closingLineTimestamp'] = f"{date}T23:00:00Z"
                        b['clv']                  = clv
                        if b.get('betTimeLine') is not None and clv is not None and clv < -2.0:
                            b['clvNote'] = 'adverse-move'
                        flag  = '✓' if clv and clv > 0 else '✗'
                        clv_s = f"{clv:+.2f}%" if clv is not None else "N/A"
                        print(f"  {flag} {b['id']} | {canonical_mkt} | Pinnacle CL: {closing['closingStr']} | CLV: {clv_s}")
                        clv_updated += 1
                        continue

            # ── For additional markets (F5/NRFI/YRFI), try per-event Odds API ─
            if is_additional:
                game = event_game_cache.get((away, home))
                if game:
                    closing = extract_closing(b, game, canonical_mkt, away)
                    if closing:
                        clv = calc_clv(b, closing)
                        b['closingLine']          = closing['closingStr']
                        b['closingLineSource']    = closing['book']
                        b['closingLineTimestamp'] = f"{date}T23:00:00Z"
                        b['clv']                  = clv
                        flag  = '✓' if clv and clv > 0 else '✗'
                        clv_s = f"{clv:+.2f}%" if clv is not None else "N/A"
                        print(f"  {flag} {b['id']} | {canonical_mkt} | OddsAPI CL: {closing['closingStr']} | CLV: {clv_s}")
                        clv_updated += 1
                        continue

            # ── Final fallback: betTimeLine proxy ─────────────────────────────
            if b.get('betTimeLine') is not None:
                b['closingLine']       = b['betTimeLine']
                b['closingLineSource'] = 'betTimeLine_proxy'
                b['clv']               = 0.0
            else:
                b['closingLineSource'] = 'unavailable'

        print(f"\n  CLV updated: {clv_updated}/{len(clv_targets)}")

    # ── Step 4: Summary ───────────────────────────────────────────────────
    print("\n--- Summary ---")
    settled = [b for b in date_bets if get_result(b) in ('WIN','LOSS','PUSH')]
    wins   = sum(1 for b in settled if get_result(b) == 'WIN')
    losses = sum(1 for b in settled if get_result(b) == 'LOSS')
    pushes = sum(1 for b in settled if get_result(b) == 'PUSH')
    total_pl = sum(float(b.get('pl') or 0) for b in settled)
    clv_vals = [float(b['clv']) for b in settled if b.get('clv') is not None]
    avg_clv  = sum(clv_vals)/len(clv_vals) if clv_vals else None

    print(f"  Record:  {wins}W {losses}L {pushes}P")
    print(f"  P/L:     ${total_pl:+.2f}")
    if avg_clv is not None:
        status = 'GOOD' if avg_clv > 1.0 else ('WARNING' if avg_clv > -1.0 else 'BAD')
        print(f"  Avg CLV: {avg_clv:+.2f}% [{status}]")

    pending = [b for b in date_bets if get_result(b) not in ('WIN','LOSS','PUSH','VOID','NO_ACTION')]
    if pending:
        print(f"  Needs manual settlement ({len(pending)}):")
        for b in pending:
            print(f"    {b['id']} | {b.get('market')} | {b.get('bet') or b.get('betTeam')}")

    # ── Write outputs ─────────────────────────────────────────────────────
    with open('bets.json', 'w') as f:
        json.dump(bets, f, indent=2)
    print("\nbets.json written")

    with open('BET_LOG.md', 'w') as f:
        f.write(rebuild_log(bets))
    print("BET_LOG.md rebuilt")


if __name__ == '__main__':
    main()
