#!/usr/bin/env python3
"""
CLV Update Script — v6.4
Changes in this version:
  - Kalshi is the ONLY closing line source. Odds API removed entirely.
  - Pre-game close only: candlestick window capped before first pitch (22:00 UTC)
    to prevent in-game lines from corrupting CLV (fixes HOU ML -1900 issue)
  - betTimeLine_proxy fallback removed: clv=None when no Kalshi match found
    (was writing clv=0.0 which polluted all-time CLV stats)
  - price/betSide/betTeam backfilled from simplified bet format in Step 1
  - id auto-assigned in Step 0 for bets logged without id field
"""

import json, os, re, sys, time
from datetime import datetime, timezone, timedelta
from urllib.request import urlopen, Request
from urllib.error import HTTPError

# ── MLB Stats API ─────────────────────────────────────────────────────────────
MLB_STATS_API = "https://statsapi.mlb.com/api/v1"

# ── F5 settlement library ─────────────────────────────────────────────────────
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)
try:
    from lib.f5_settlement import (
        settle_f5_from_linescore_api,
        settle_f5_from_boxscore_fallback,
        extract_f5_score_from_linescore,
        F5_RESULT_WIN, F5_RESULT_LOSS, F5_RESULT_VOID, F5_RESULT_PENDING,
    )
    _F5_LIB_AVAILABLE = True
except ImportError:
    _F5_LIB_AVAILABLE = False
    print("WARNING: lib/f5_settlement not available — F5 settlement will be manual")

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

# ── Gap 4: MLB Stats API — linescore-based F5 settlement ────────────────────

def fetch_mlb_schedule_gamepks(date_str):
    """
    Fetch MLB Schedule for date_str to get gamePk values.
    Returns dict: (away_abbr, home_abbr) → gamePk (int)
    Uses: statsapi.mlb.com/api/v1/schedule?sportId=1&date=YYYY-MM-DD
    """
    url = f"{MLB_STATS_API}/schedule?sportId=1&date={date_str}&gameType=R"
    try:
        req = Request(url, headers={"User-Agent": "edge-finder-clv/1.0", "Accept": "application/json"})
        with urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
    except Exception as e:
        print(f"  [f5_settle] MLB schedule fetch failed: {e}")
        return {}

    gamepks = {}
    for date_entry in data.get("dates", []):
        for game in date_entry.get("games", []):
            gpk = game.get("gamePk")
            if not gpk:
                continue
            away_name = game.get("teams", {}).get("away", {}).get("team", {}).get("name", "")
            home_name = game.get("teams", {}).get("home", {}).get("team", {}).get("name", "")
            away_a = to_abbr(away_name)
            home_a = to_abbr(home_name)
            if away_a and home_a:
                gamepks[(away_a, home_a)] = gpk
    print(f"  [f5_settle] MLB schedule: {len(gamepks)} games with gamePk")
    return gamepks


def fetch_mlb_linescore(game_pk):
    """
    Fetch MLB linescore for a single gamePk.
    Returns linescore dict or None on failure.
    Primary source for F5 settlement (Gap 4).
    """
    url = f"{MLB_STATS_API}/game/{game_pk}/linescore"
    try:
        req = Request(url, headers={"User-Agent": "edge-finder-clv/1.0", "Accept": "application/json"})
        with urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        print(f"  [f5_settle] Linescore fetch failed for gamePk={game_pk}: {e}")
        return None


def settle_f5_bet_from_linescore(b, game_pk, bet_side):
    """
    Settle a single F5 ML bet using the MLB linescore API (primary source).

    Returns dict with:
        result: 'WIN' | 'LOSS' | 'PUSH' | 'VOID' | None
        awayF5: int | None
        homeF5: int | None
        f5SettlementSource: 'LINESCORE' | 'BOXSCORE_FALLBACK' | 'RBI_RECONSTRUCTION_FALLBACK'
        notes: str
    """
    if not _F5_LIB_AVAILABLE:
        return {"result": None, "f5SettlementSource": None, "notes": "lib/f5_settlement unavailable"}

    linescore = fetch_mlb_linescore(game_pk)

    if linescore:
        try:
            settlement = settle_f5_from_linescore_api(linescore, bet_side=bet_side)
            return {
                "result": settlement["result"],
                "awayF5": settlement["awayF5"],
                "homeF5": settlement["homeF5"],
                "isTie": settlement.get("isTie", False),
                "f5SettlementSource": "LINESCORE",
                "notes": settlement.get("notes", ""),
            }
        except Exception as e:
            # Linescore available but insufficient innings (game incomplete or data issue)
            print(f"  [f5_settle] Linescore parse failed for gamePk={game_pk}: {e}")
            return {
                "result": None,
                "awayF5": None,
                "homeF5": None,
                "f5SettlementSource": "LINESCORE",
                "notes": f"Linescore insufficient: {e} — game may be incomplete",
            }
    else:
        # Linescore unavailable — flag clearly as fallback
        return {
            "result": None,
            "awayF5": None,
            "homeF5": None,
            "f5SettlementSource": "BOXSCORE_FALLBACK",
            "notes": "Linescore API unavailable — manual settlement required; "
                     "do NOT use RBI reconstruction without flagging RBI_RECONSTRUCTION_FALLBACK",
        }


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
        req = Request(url, headers={
            'Accept': 'application/json',
            'User-Agent': 'Mozilla/5.0',
        })
        with urlopen(req, timeout=20) as r:
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
    Fetch the Kalshi PRE-GAME closing price for a market ticker.
    Uses the last candlestick BEFORE first pitch to avoid in-game lines.
    MLB games typically start 22:00-02:00 UTC. We cap at 22:00 UTC (6pm ET)
    on the game date — capturing the last pre-game price across all time zones.
    Returns implied probability (0-1) as the closing yes_bid/yes_ask midpoint.
    """
    if not ticker:
        return None

    try:
        dt = datetime.strptime(game_date_str, '%Y-%m-%d')
        # Extract game time from ticker: {SERIES}-{YYMONDD}{HHMM}{AWAY}{HOME}
        # e.g. KXMLBRFI-26JUN051910TBMIA → HHMM = 1910 (ET) = 23:10 UTC
        # Parse the HHMM from the ticker string
        import re as _re
        hhmm_match = _re.search(r'(\d{4})(?=[A-Z]{2,3})', ticker.split('-')[1] if '-' in ticker else '')
        if hhmm_match:
            hhmm = hhmm_match.group(1)
            hh_et, mm = int(hhmm[:2]), int(hhmm[2:])
            # Convert ET → UTC (+4 for EDT)
            hh_utc = (hh_et + 4) % 24
            # End window = game start UTC time (last pre-game candle)
            # Handle date rollover for games starting in ET evening (e.g. 2210 ET = 0210 UTC next day)
            if hh_utc < hh_et:  # crossed midnight UTC
                cap_dt = (dt + timedelta(days=1)).replace(hour=hh_utc, minute=mm,
                          second=0, microsecond=0, tzinfo=timezone.utc)
            else:
                cap_dt = dt.replace(hour=hh_utc, minute=mm, second=0,
                                    microsecond=0, tzinfo=timezone.utc)
            end_ts   = int(cap_dt.timestamp())
            start_ts = end_ts - 7200   # 2-hour pre-game window
        else:
            # Fallback: use 22:00 UTC on game date as cap
            cap_hour = commence_hour_utc if commence_hour_utc else 22
            end_ts   = int(dt.replace(hour=cap_hour, minute=0, second=0,
                                      microsecond=0, tzinfo=timezone.utc).timestamp())
            start_ts = end_ts - 7200
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

    # ── Classify bets: real vs paper ──────────────────────────────────────
    # A bet is paper if ANY of these signals fire (most-to-least reliable):
    #   1. betType == 'PAPER'  (explicit canonical flag)
    #   2. type == 'paper'     (legacy lowercase flag)
    #   3. confidence == 'Paper' (confidence tier used before betType existed)
    #   4. conf == 'PAPER' or status == 'PAPER' (older simplified-format fields)
    # Real money bets must have betType=='REAL' OR type=='real'.
    # Ambiguous bets (betType=None, type=None, conf!=Paper) are treated as real
    # for W/L record purposes but their P/L is tracked separately as ambiguous.
    def _is_paper_bet(b):
        if b.get('betType') == 'PAPER' or b.get('type') == 'paper':
            return True
        conf = normalize_confidence(b.get('confidence', '') or b.get('conf', '') or '')
        if conf == 'Paper':
            return True
        if str(b.get('status', '')).upper() == 'PAPER':
            return True
        return False

    def _is_real_bet(b):
        return b.get('betType') == 'REAL' or b.get('type') == 'real'

    real_bets  = [b for b in bets if _is_real_bet(b)]
    paper_bets = [b for b in bets if _is_paper_bet(b)]
    # Bets with neither real nor paper signal — legacy bets logged before betType existed
    legacy_bets = [b for b in bets if not _is_real_bet(b) and not _is_paper_bet(b)]

    # Real-money record = explicit real + legacy (pre-betType era)
    record_bets = real_bets + legacy_bets
    tw    = sum(1 for b in record_bets if get_result(b) == 'WIN')
    tl    = sum(1 for b in record_bets if get_result(b) == 'LOSS')
    tp    = sum(1 for b in record_bets if get_result(b) == 'PUSH')
    tpl   = sum(float(b.get('pl') or 0) for b in record_bets)   # real P/L only
    paper_pl_total = sum(float(b.get('pl') or 0) for b in paper_bets)
    pend  = sum(1 for b in bets if get_result(b) not in ('WIN','LOSS','PUSH','VOID','NO_ACTION'))
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')

    # ── Paper stats by market ─────────────────────────────────────────────
    from collections import defaultdict as _dd
    paper_by_mkt = _dd(lambda: {'w':0,'l':0,'pl':0.0,'clv':[]})
    for b in paper_bets:
        mkt = normalize_market(b.get('market','')) or b.get('market','Unknown')
        r   = get_result(b)
        if r == 'WIN':   paper_by_mkt[mkt]['w'] += 1
        elif r == 'LOSS': paper_by_mkt[mkt]['l'] += 1
        paper_by_mkt[mkt]['pl'] += float(b.get('pl') or 0)
        if b.get('clv') is not None:
            paper_by_mkt[mkt]['clv'].append(float(b['clv']))

    paper_clv_vals = [float(b['clv']) for b in paper_bets if b.get('clv') is not None]
    paper_clv_avg  = round(sum(paper_clv_vals)/len(paper_clv_vals), 2) if paper_clv_vals else None
    paper_clv_pos  = sum(1 for v in paper_clv_vals if v > 0)
    paper_clv_neg  = sum(1 for v in paper_clv_vals if v < 0)
    paper_clv_flat = sum(1 for v in paper_clv_vals if v == 0)
    paper_wins = sum(1 for b in paper_bets if get_result(b) == 'WIN')
    paper_losses = sum(1 for b in paper_bets if get_result(b) == 'LOSS')
    paper_stake = sum(float(b.get('stake') or b.get('size') or b.get('betSize') or 0) for b in paper_bets)
    paper_roi   = round(paper_pl_total / paper_stake * 100, 1) if paper_stake > 0 else None

    lines = [
        '# BET_LOG.md — Authoritative Bet Record',
        f'*Generated from bets.json — last updated: {today}*',
        '',
        f'## Real-Money Record: {tw}W {tl}L {tp}P | Real P/L: ${tpl:+.2f} | Pending: {pend}',
        '',
        '> **Note:** Paper bets are excluded from Real-Money Record and P/L above.',
        '> Paper P/L is tracked separately in the Paper Performance section below.',
        '', '---', '',
    ]

    for date in sorted(by_date.keys(), reverse=True):
        db  = by_date[date]
        # Real-money bets for this date (excluding paper)
        dr  = [b for b in db if not _is_paper_bet(b)]
        dp  = [b for b in db if _is_paper_bet(b)]
        dw  = sum(1 for b in dr if get_result(b) == 'WIN')
        dl  = sum(1 for b in dr if get_result(b) == 'LOSS')
        dpl = sum(float(b.get('pl') or 0) for b in dr)     # real P/L only
        dppl = sum(float(b.get('pl') or 0) for b in dp)    # paper P/L separate
        paper_note = f' | Paper P/L: ${dppl:+.2f}' if dp else ''
        lines.append(f'### {date} — {dw}W {dl}L | Real P/L: ${dpl:+.2f}{paper_note}')
        lines.append('| ID | Mkt | Bet | Price | Edge% | Conf | Size | Result | P/L | CLV% |')
        lines.append('|---|---|---|---|---|---|---|---|---|---|')
        # Real-money bets first
        for b in dr:
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
        # Paper bets in a sub-section (if any)
        if dp:
            lines.append(f'> **Paper bets — {date}** ({len(dp)} bets, paper P/L: ${dppl:+.2f} — excluded from real-money record)')
            lines.append('| ID | Mkt | Bet | Price | Edge% | Conf | Size | Result | P/L | CLV% | Note |')
            lines.append('|---|---|---|---|---|---|---|---|---|---|---|')
            for b in dp:
                edge  = f"{b.get('edgePct','')}%" if b.get('edgePct') is not None else '—'
                pl    = f"${float(b.get('pl') or 0):+.2f}" if b.get('pl') is not None else '—'
                clv   = f"{float(b.get('clv')):+.1f}%" if b.get('clv') is not None else '—'
                res   = get_result(b) or 'PENDING'
                bet_s = b.get('betTeam') or b.get('bet') or b.get('side') or ''
                sz    = get_size(b) or ''
                note  = b.get('note') or b.get('notes') or '—'
                if isinstance(note, str) and len(note) > 60:
                    note = note[:57] + '...'
                lines.append(
                    f"| {b.get('id','')} | {b.get('market','')} | {bet_s} "
                    f"| {b.get('price','')} | {edge} | {b.get('confidence','Paper')} "
                    f"| {sz} | {res} | {pl} | {clv} | {note} |"
                )
        lines.append('')

    # ── Paper Performance Summary ─────────────────────────────────────────
    lines += [
        '---', '',
        '## Paper Performance Summary',
        f'*Paper bets track model edges that cannot be placed as real money yet.*',
        f'*These are NEVER included in the Real-Money Record above.*',
        '',
        f'**Overall Paper Record:** {paper_wins}W {paper_losses}L | '
        f'Paper P/L: ${paper_pl_total:+.2f} | '
        f'Paper Stake: ${paper_stake:.2f} | '
        f'Paper ROI: {paper_roi:+.1f}%' if paper_roi is not None else
        f'**Overall Paper Record:** {paper_wins}W {paper_losses}L | Paper P/L: ${paper_pl_total:+.2f}',
        '',
        f'**Paper CLV:** avg {paper_clv_avg:+.2f}% | +CLV: {paper_clv_pos} | -CLV: {paper_clv_neg} | flat: {paper_clv_flat} | n={len(paper_clv_vals)}'
        if paper_clv_avg is not None else
        f'**Paper CLV:** no valid CLV data yet ({len(paper_bets)} bets pending)',
        '',
        '### Paper Performance by Market',
        '| Market | W | L | WR% | P/L | ROI% | Avg CLV | N | Recommendation |',
        '|---|---|---|---|---|---|---|---|---|',
    ]
    for mkt in sorted(paper_by_mkt.keys()):
        s = paper_by_mkt[mkt]
        w, l = s['w'], s['l']
        n_settled = w + l
        wr  = round(w / n_settled * 100, 1) if n_settled > 0 else None
        pl  = round(s['pl'], 2)
        clv_list = s['clv']
        avg_clv = round(sum(clv_list)/len(clv_list), 2) if clv_list else None
        # Stake approximation: assume $1 paper size
        n_total = len([b for b in paper_bets if (normalize_market(b.get('market','')) or b.get('market','')) == mkt])
        stake_est = n_total * 1.0
        roi = round(pl / stake_est * 100, 1) if stake_est > 0 else None
        # Promotion recommendation
        if n_settled < 10:
            rec = 'INSUFFICIENT SAMPLE'
        elif avg_clv is not None and avg_clv >= 1.0 and wr is not None and wr >= 52:
            rec = '✅ PROMOTE CANDIDATE'
        elif avg_clv is not None and avg_clv < -1.0:
            rec = '❌ REJECT — negative CLV'
        elif wr is not None and wr < 42 and n_settled >= 15:
            rec = '⚠️ LOSING — monitor'
        else:
            rec = '🔄 KEEP PAPER'
        wr_s   = f'{wr:.1f}%' if wr is not None else '—'
        roi_s  = f'{roi:+.1f}%' if roi is not None else '—'
        clv_s  = f'{avg_clv:+.2f}%' if avg_clv is not None else '—'
        lines.append(f'| {mkt} | {w} | {l} | {wr_s} | ${pl:+.2f} | {roi_s} | {clv_s} | {n_settled} | {rec} |')
    lines += [
        '',
        '> **Promotion rules are informational only.** No automatic changes to real-money rules.',
        '> A market is a "Promote Candidate" when: WR ≥52% AND avg CLV ≥+1.0% AND N ≥10 settled bets.',
        '> Even a promote candidate requires manual review and explicit rule update before real-money placement.',
        '',
    ]
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
        # Backfill 'price' from 'odds' for simplified bet format
        # All downstream CLV/P&L math reads b.get('price')
        if b.get('price') is None and b.get('odds') is not None:
            b['price'] = b['odds']
            normalized += 1
        # Backfill 'betTimeLine' from 'odds' if still missing
        if b.get('betTimeLine') is None and b.get('odds') is not None:
            b['betTimeLine'] = b['odds']

        # Backfill 'betSide' / 'betTeam' from simplified 'side' field
        # 'side' in simplified format is e.g. "SEA", "TOR", "NRFI", "YRFI", "PIT Over 4"
        if not b.get('betSide') and not b.get('betTeam') and b.get('side'):
            side_val = b['side']
            game_parts = (b.get('game') or '').split(' @ ')
            away_abbr = game_parts[0].strip() if len(game_parts) == 2 else ''
            home_abbr = game_parts[1].strip() if len(game_parts) == 2 else ''
            side_upper = side_val.upper()
            if side_upper in ('NRFI', 'YRFI'):
                b['betSide'] = side_val
            elif away_abbr and side_upper == away_abbr.upper():
                b['betSide'] = 'AWAY'
                b['betTeam'] = away_abbr
            elif home_abbr and side_upper == home_abbr.upper():
                b['betSide'] = 'HOME'
                b['betTeam'] = home_abbr
            else:
                # TT / RL side strings like "PIT Over 4", "NYM -1.5"
                b['bet'] = side_val
            normalized += 1

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

    # ── Gap 4: Prefetch MLB gamePk map for F5 linescore settlement ────────────
    # Fetched once before the loop to avoid repeated API calls.
    # Maps (away_abbr, home_abbr) → gamePk for linescore lookups.
    f5_gamepks = {}
    has_f5_bets = any(
        normalize_market(b.get('market', '')) in ('F5 ML', 'F5 RL')
        for b in date_bets
        if get_result(b) not in ('WIN', 'LOSS', 'PUSH', 'VOID', 'NO_ACTION')
    )
    if has_f5_bets:
        print("  [f5_settle] Fetching MLB gamePk map for F5 linescore settlement...")
        f5_gamepks = fetch_mlb_schedule_gamepks(date)

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

        # ── Gap 4: F5 ML settlement via MLB linescore API ─────────────────────
        if canonical_mkt in ('F5 ML', 'F5 RL'):
            game_pk = f5_gamepks.get((away, home))
            if not game_pk:
                print(f"  ? {b['id']}: F5 ML — no gamePk found for {away}@{home}, flagging manual")
                f5_manual.append(b['id'])
                b['f5SettlementSource'] = 'BOXSCORE_FALLBACK'
                b['f5SettlementNote'] = f"No gamePk found for {away}@{home}"
                continue

            bet_side = get_betside(b, away)
            f5_result = settle_f5_bet_from_linescore(b, game_pk, bet_side or 'away')

            if f5_result.get("result") in ('WIN', 'LOSS', 'PUSH'):
                b['result'] = f5_result['result']
                b['status'] = 'SETTLED'
                b['awayScore'] = f5_result.get('awayF5')
                b['homeScore'] = f5_result.get('homeF5')
                b['f5SettlementSource'] = f5_result.get('f5SettlementSource', 'LINESCORE')
                b['f5IsTie'] = f5_result.get('isTie', False)
                b['f5SettlementNote'] = f5_result.get('notes', '')
                b['pl'] = calc_pl(b.get('price'), get_size(b), b['result'])
                settled_this_run += 1
                flag = '✓' if b['result'] == 'WIN' else ('↔' if b['result'] == 'PUSH' else '✗')
                pl_s = f"${b['pl']:+.2f}" if b['pl'] is not None else '—'
                tie_note = ' [TIE→LOSS]' if f5_result.get('isTie') else ''
                print(f"  {flag} {b['id']} | F5 ML | "
                      f"F5: away={f5_result.get('awayF5')} home={f5_result.get('homeF5')}{tie_note} | "
                      f"{b['result']} | {pl_s} | src={b['f5SettlementSource']}")
            else:
                # Linescore incomplete (game not finished or API error)
                f5_manual.append(b['id'])
                b['f5SettlementSource'] = f5_result.get('f5SettlementSource', 'LINESCORE')
                b['f5SettlementNote'] = f5_result.get('notes', 'Linescore incomplete')
                print(f"  ? {b['id']}: F5 ML — {f5_result.get('notes', 'incomplete')} — flagged manual")
            continue

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
        print(f"\n  ⚠ F5 ML bets needing manual settlement ({len(f5_manual)}):")
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

        # ── Fetch Kalshi settled markets for this date (only CLV source) ──────
        # We no longer call The Odds API. Kalshi is where we bet — CLV vs Kalshi
        # is the only meaningful signal. Odds API is removed entirely.
        for b in date_bets:
            b.pop('_kalshi_titles', None)
            b.pop('_kalshi_count', None)

        print("\n  Fetching Kalshi settled markets (sole CLV source)...")
        # ── Build direct Kalshi ticker map from slate data ───────────────────
        # Ticker format: {SERIES}-{YYMONDD}{HHMM}{AWAY}{HOME}[-{SUFFIX}]
        # YYMONDD = e.g. 26JUN05; HHMM = ET game time e.g. 1910; SUFFIX per series.
        # We build tickers directly rather than searching settled markets,
        # because the settled market search often returns 0 results or misses series.
        print("\n  Building Kalshi ticker map from slate...")
        kalshi_ticker_map = {}  # (away, home) -> {market_type -> ticker(s)}
        try:
            slate_path = 'data/slate.json'
            with open(slate_path) as sf:
                slate_data = json.load(sf)
            slate_games = slate_data.get('games', [])

            # Parse ET game times from UTC startTime
            for g in slate_games:
                away_abbr = g.get('away', {}).get('abbr', '')
                home_abbr = g.get('home', {}).get('abbr', '')
                start_utc = g.get('startTime', '')
                if not (away_abbr and home_abbr and start_utc): continue
                try:
                    from datetime import datetime as _dt
                    dt_utc = _dt.fromisoformat(start_utc.replace('Z', '+00:00'))
                    hour_et = (dt_utc.hour - 4) % 24
                    time_str = f"{hour_et:02d}{dt_utc.minute:02d}"
                    # Build Kalshi date string: YYMONDD
                    # Use the game date (may be next UTC day for late ET games)
                    game_date_et = date  # assume ET date = the date param
                    dt_game = _dt.strptime(game_date_et, '%Y-%m-%d')
                    kal_date = dt_game.strftime('%y') + dt_game.strftime('%b').upper()[:3] + dt_game.strftime('%d').lstrip('0')
                    prefix = f"{kal_date}{time_str}{away_abbr}{home_abbr}"
                    kalshi_ticker_map[(away_abbr, home_abbr)] = {
                        'prefix':      prefix,
                        'ml_away':     f"KXMLBGAME-{prefix}-{away_abbr}",
                        'ml_home':     f"KXMLBGAME-{prefix}-{home_abbr}",
                        'rl_away':     f"KXMLBSPREAD-{prefix}-{away_abbr}2",
                        'rl_home':     f"KXMLBSPREAD-{prefix}-{home_abbr}2",
                        'rfi':         f"KXMLBRFI-{prefix}",
                        'f5_away':     f"KXMLBF5-{prefix}-{away_abbr}",
                        'f5_home':     f"KXMLBF5-{prefix}-{home_abbr}",
                        'f5_tie':      f"KXMLBF5-{prefix}-TIE",
                        'tt_away':     f"KXMLBTEAMTOTAL-{prefix}-{away_abbr}",
                        'tt_home':     f"KXMLBTEAMTOTAL-{prefix}-{home_abbr}",
                    }
                except Exception as te:
                    print(f"    Ticker build error for {away_abbr}@{home_abbr}: {te}")
            print(f"  Ticker map built for {len(kalshi_ticker_map)} games")
        except Exception as e:
            print(f"  Ticker map build failed: {e}")

        # ── Process all CLV targets ───────────────────────────────────────────
        # Note: Kalshi candlestick API (historical price data) only works for KXMLBGAME (ML).
        # KXMLBRFI, KXMLBF5, KXMLBTEAMTOTAL tickers return 404 on candlestick endpoint.
        # These markets are logged as 'kalshi_no_history' so they're not endlessly retried.
        KALSHI_CANDLESTICK_SUPPORTED = {'ML', 'Run Line', 'Total'}  # KXMLBGAME, KXMLBSPREAD, KXMLBTOTAL

        clv_updated = 0
        for b in clv_targets:
            away, home = parse_game(b.get('game', ''))
            if not away:
                b['closingLineSource'] = 'parse_error'
                continue

            canonical_mkt = normalize_market(b.get('market', ''))

            # Skip markets where Kalshi doesn't provide historical candlestick data
            if canonical_mkt not in KALSHI_CANDLESTICK_SUPPORTED:
                # Check if v2 (fetch_kalshi_clv_v2.py) should handle this market
                # Do NOT mark as unsupported — v2 handles NRFI, YRFI, F5 ML, TT
                # Only mark as unsupported if there is no stored CLV already
                if b.get('clv') is not None:
                    # CLV already set by v2 — do not overwrite
                    print(f"  — {b['id']} | {canonical_mkt} | CLV already set by v2 — skipping legacy")
                    continue
                # For markets v2 handles, defer rather than mark unsupported
                if canonical_mkt in ('NRFI', 'YRFI', 'F5 ML', 'Team Total'):
                    print(f"  — {b['id']} | {canonical_mkt} | deferred to fetch_kalshi_clv_v2.py")
                    continue
                b['closingLineSource'] = 'kalshi_no_history'
                b['closingLine']       = None
                b['clv']               = None
                print(f"  — {b['id']} | {canonical_mkt} | Kalshi candlestick API not available for this series")
                continue

            bet_side       = (b.get('betSide') or b.get('betTeam') or b.get('bet') or b.get('side') or '').upper()
            tickers        = kalshi_ticker_map.get((away, home)) or kalshi_ticker_map.get((away.upper(), home.upper()))

            # Priority 1: Use stored marketTicker from bets.json (never reconstruct if present)
            ticker = b.get('marketTicker') or b.get('ticker')
            is_nrfi = False  # NRFI = NO side (closing_impl must be inverted)

            if not ticker and tickers:
                # Fall back to reconstructed ticker from slate
                if canonical_mkt == 'ML':
                    # Determine away or home
                    if away.upper() in bet_side or b.get('betSide','').upper() == 'AWAY':
                        ticker = tickers['ml_away']
                    else:
                        ticker = tickers['ml_home']
                elif canonical_mkt == 'Run Line':
                    if away.upper() in bet_side or b.get('betSide','').upper() == 'AWAY':
                        ticker = tickers['rl_away']
                    else:
                        ticker = tickers['rl_home']
                elif canonical_mkt in ('NRFI', 'YRFI'):
                    ticker = tickers['rfi']
                elif canonical_mkt == 'F5 ML':
                    if away.upper() in bet_side or b.get('betSide','').upper() == 'AWAY':
                        ticker = tickers['f5_away']
                    else:
                        ticker = tickers['f5_home']
                elif canonical_mkt == 'Team Total':
                    # bet_side like "PIT Over 4" or "LAA Over 3"
                    if away.upper() in bet_side:
                        ticker = tickers['tt_away'] + str(int(float(bet_side.split()[-1])))
                    else:
                        ticker = tickers['tt_home'] + str(int(float(bet_side.split()[-1])))

            if canonical_mkt in ('NRFI', 'YRFI'):
                is_nrfi = (canonical_mkt == 'NRFI')  # NRFI = bet on NO side

            if not ticker:
                b['closingLineSource'] = 'FAIL_NO_TICKER'
                b['closingLine']       = None
                b['clv']              = None
                print(f"  ✗ {b['id']} | {canonical_mkt} | FAIL_NO_TICKER: no stored or reconstructed ticker for {away}@{home}")
                continue

            closing_impl = fetch_kalshi_closing_price(ticker, date)
            if closing_impl is None:
                b['closingLineSource'] = 'no_kalshi_data'
                b['closingLine']       = None
                b['clv']               = None
                print(f"  ? {b['id']} | {canonical_mkt} | ticker {ticker} → no data")
                continue

            # For NRFI: we bet NO (market YES = YRFI), so our implied = 1 - closing_impl
            bet_impl = to_imp(b.get('price'))
            if bet_impl is None:
                b['closingLineSource'] = 'no_price'
                continue

            if is_nrfi:
                # We bet NO on the RFI market. Our probability = 1 - yes_prob.
                our_closing_impl = 1.0 - closing_impl
            else:
                our_closing_impl = closing_impl

            kalshi_clv = round((our_closing_impl - bet_impl) * 100, 2)
            close_american = implied_to_american(our_closing_impl)
            close_str = f"{'+' if close_american and close_american>=0 else ''}{close_american} [kalshi]"
            b['closingLine']          = close_str
            b['closingLineSource']    = 'kalshi'
            b['closingLineTimestamp'] = f"{date}T22:00:00Z"
            b['clv']                  = kalshi_clv
            if kalshi_clv is not None and kalshi_clv < -2.0:
                b['clvNote'] = 'adverse-move'
            flag = '✓' if kalshi_clv > 0 else '✗'
            print(f"  {flag} {b['id']} | {canonical_mkt} | Kalshi CL: {close_str} | CLV: {kalshi_clv:+.2f}%")
            clv_updated += 1
            time.sleep(0.15)

    # ── Step 4: Summary ───────────────────────────────────────────────────
    print("\n--- Summary ---")

    def _date_is_paper(b):
        """Paper detection consistent with rebuild_log."""
        if b.get('betType') == 'PAPER' or b.get('type') == 'paper':
            return True
        conf = normalize_confidence(b.get('confidence', '') or b.get('conf', '') or '')
        if conf == 'Paper':
            return True
        if str(b.get('status', '')).upper() == 'PAPER':
            return True
        return False

    real_date   = [b for b in date_bets if not _date_is_paper(b)]
    paper_date  = [b for b in date_bets if _date_is_paper(b)]

    settled_real  = [b for b in real_date  if get_result(b) in ('WIN','LOSS','PUSH')]
    settled_paper = [b for b in paper_date if get_result(b) in ('WIN','LOSS','PUSH')]

    wins   = sum(1 for b in settled_real if get_result(b) == 'WIN')
    losses = sum(1 for b in settled_real if get_result(b) == 'LOSS')
    pushes = sum(1 for b in settled_real if get_result(b) == 'PUSH')
    total_pl = sum(float(b.get('pl') or 0) for b in settled_real)
    clv_vals = [float(b['clv']) for b in settled_real if b.get('clv') is not None]
    avg_clv  = sum(clv_vals)/len(clv_vals) if clv_vals else None

    print(f"  [REAL] Record: {wins}W {losses}L {pushes}P")
    print(f"  [REAL] P/L:    ${total_pl:+.2f}")
    if avg_clv is not None:
        status = 'GOOD' if avg_clv > 1.0 else ('WARNING' if avg_clv > -1.0 else 'BAD')
        print(f"  [REAL] Avg CLV: {avg_clv:+.2f}% [{status}]")

    # Paper summary
    if paper_date:
        pw     = sum(1 for b in settled_paper if get_result(b) == 'WIN')
        pl_p   = sum(1 for b in settled_paper if get_result(b) == 'LOSS')
        ppl    = sum(float(b.get('pl') or 0) for b in settled_paper)
        pclv_v = [float(b['clv']) for b in settled_paper if b.get('clv') is not None]
        pavg   = round(sum(pclv_v)/len(pclv_v), 2) if pclv_v else None
        print(f"\n  [PAPER] Record: {pw}W {pl_p}L  (NOT counted in real record)")
        print(f"  [PAPER] P/L:    ${ppl:+.2f}  (NOT counted in real P/L)")
        if pavg is not None:
            print(f"  [PAPER] Avg CLV: {pavg:+.2f}%")
        pending_paper = [b for b in paper_date if get_result(b) not in ('WIN','LOSS','PUSH','VOID','NO_ACTION')]
        if pending_paper:
            print(f"  [PAPER] Unsettled ({len(pending_paper)}): {[b.get('id') for b in pending_paper]}")

    pending = [b for b in date_bets if get_result(b) not in ('WIN','LOSS','PUSH','VOID','NO_ACTION')]
    real_pending = [b for b in pending if not _date_is_paper(b)]
    if real_pending:
        print(f"\n  [REAL] Needs manual settlement ({len(real_pending)}):")
        for b in real_pending:
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
