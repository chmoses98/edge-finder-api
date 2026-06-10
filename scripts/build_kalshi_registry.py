#!/usr/bin/env python3
"""
scripts/build_kalshi_registry.py
=================================
Builds data/kalshi_market_registry.json — the persistent source of truth
mapping every game to every Kalshi market ticker, organized for:
  1. Bet price lookup at session time (which ticker to read for each market)
  2. Closing line capture at game start (snapshot betTimeLine for CLV)

Runs as part of the fetch-slate workflow after market enumeration.

Registry schema per game entry:
{
  "PITHOU": {                          ← kalshiKey (away+home abbr)
    "event_ticker_prefix": "26JUN042010PITHOU",
    "game_time_et": "8:10 PM",
    "away": "PIT", "home": "HOU",
    "markets": {
      "moneyline": {
        "series": "KXMLBGAME",
        "away_ticker": "KXMLBGAME-26JUN042010PITHOU-PIT",
        "home_ticker": "KXMLBGAME-26JUN042010PITHOU-HOU",
        "prices": {
          "away": {"yes_bid":0.485,"yes_ask":0.495,"mid":0.490,"implied_pct":49.0,"american":104},
          "home": {"yes_bid":0.505,"yes_ask":0.515,"mid":0.510,"implied_pct":51.0,"american":-104}
        }
      },
      "spread": {
        "series": "KXMLBSPREAD",
        "lines": [
          {"ticker":"KXMLBSPREAD-...-PIT2","team":"PIT","runs":1.5,"implied_pct":36.5,...},
          ...
        ],
        "best_line": {...}   ← line closest to 50% implied (= traditional -1.5 equivalent)
      },
      "total": {
        "series": "KXMLBTOTAL",
        "lines": [
          {"ticker":"KXMLBTOTAL-...-8","total":8,"implied_pct":57.5,...},
          ...
        ],
        "best_line": {...}   ← line closest to 50%
      },
      "team_total_away": { "series":"KXMLBTEAMTOTAL", "team":"PIT", "lines":[...], "best_line":{...} },
      "team_total_home": { "series":"KXMLBTEAMTOTAL", "team":"HOU", "lines":[...], "best_line":{...} },
      "f5_moneyline":   { "series":"KXMLBF5", "away_ticker":"...-PIT","home_ticker":"...-HOU","tie_ticker":"...-TIE","prices":{...} },
      "f5_spread":      { "series":"KXMLBF5SPREAD", "lines":[...], "best_line":{...} },
      "f5_total":       { "series":"KXMLBF5TOTAL", "lines":[...], "best_line":{...} },
      "rfi":            { "series":"KXMLBRFI", "ticker":"KXMLBRFI-26JUN042010PITHOU",
                          "yes_is_yrfi":true,
                          "prices":{"yrfi":{"yes_bid":...,"implied_pct":...,"american":...},
                                    "nrfi":{"yes_bid":...,"implied_pct":...,"american":...}} }
    },
    "snapshot_ts": "2026-06-04T21:32:10Z",
    "closing_snapshots": []   ← populated by capture_closing_lines.py at game start
  }
}
"""

import json, sys, os
from datetime import datetime, timezone, timedelta
from urllib.request import urlopen, Request
from urllib.error import HTTPError

KALSHI_BASE = 'https://api.elections.kalshi.com/trade-api/v2'
DATE = sys.argv[1] if len(sys.argv) > 1 else datetime.now(tz=timezone(timedelta(hours=-4))).strftime('%Y-%m-%d')
dt = datetime.strptime(DATE, '%Y-%m-%d')
MONTHS = ['JAN','FEB','MAR','APR','MAY','JUN','JUL','AUG','SEP','OCT','NOV','DEC']
KALSHI_DATE = str(dt.year)[2:] + MONTHS[dt.month-1] + str(dt.day).zfill(2)
SNAPSHOT_TS = datetime.now(tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

print(f"[build_kalshi_registry] DATE={DATE} KALSHI_DATE={KALSHI_DATE}")

# ── Confirmed series catalogue ────────────────────────────────────────────────
# This is the persistent source of truth about what series Kalshi offers.
# Update this dict when new series are discovered.
SERIES_CATALOGUE = {
    'KXMLBGAME':      'moneyline',
    'KXMLBSPREAD':    'spread',
    'KXMLBTOTAL':     'total',
    'KXMLBTEAMTOTAL': 'team_total',
    'KXMLBF5':        'f5_moneyline',
    'KXMLBF5SPREAD':  'f5_spread',
    'KXMLBF5TOTAL':   'f5_total',
    'KXMLBRFI':       'rfi',
}

SERIES_NOTES = {
    'KXMLBGAME':      'Full game winner. 2 markets/game. Ticker suffix: -{TEAM_ABBR}.',
    'KXMLBSPREAD':    'Win margin. Many markets/game. Suffix: -{TEAM}{RUNS} e.g. -PIT2 = PIT wins by >1.5.',
    'KXMLBTOTAL':     'Combined total runs over N. Integer lines. Suffix: -{N} e.g. -8 = over 8 runs.',
    'KXMLBTEAMTOTAL': 'Team scores over X.5. Many markets/team/game. Suffix: -{TEAM}{N} e.g. -PIT4 = PIT over 3.5.',
    'KXMLBF5':        'First 5 innings winner incl TIE. 3 markets/game. Suffix: -{TEAM} or -TIE.',
    'KXMLBF5SPREAD':  'First 5 innings win margin. Suffix: -{TEAM}{RUNS} e.g. -PIT2 = PIT wins F5 by >1.5.',
    'KXMLBF5TOTAL':   'First 5 innings combined total over N. Integer lines. Suffix: -{N}.',
    'KXMLBRFI':       'Run in First Inning. 1 market/game. NO suffix. YES=YRFI, NO=NRFI.',
}

def get(url):
    try:
        req = Request(url, headers={'Accept':'application/json','User-Agent':'Mozilla/5.0'})
        with urlopen(req, timeout=15) as r:
            return json.loads(r.read()), None
    except HTTPError as e:
        return None, f"HTTP{e.code}"
    except Exception as e:
        return None, str(e)[:80]

def pull_all_statuses(series):
    seen = {}
    for status in ['open','active','closed']:
        cursor = ''
        for _ in range(15):
            url = f"{KALSHI_BASE}/markets?series_ticker={series}&status={status}&limit=200"
            if cursor: url += f"&cursor={cursor}"
            data, err = get(url)
            if not data: break
            for m in (data.get('markets') or []):
                if KALSHI_DATE in (m.get('event_ticker','') or ''):
                    seen[m['ticker']] = m
            cursor = data.get('cursor','')
            if not cursor or not data.get('markets'): break
    return list(seen.values())

def norm(v):
    if v is None: return None
    f = float(v)
    return round(f if f <= 1.0 else f/100.0, 4)

def american(mid):
    if not mid or mid <= 0 or mid >= 1: return None
    return round(-(mid/(1-mid))*100) if mid >= 0.5 else round(((1-mid)/mid)*100)

def price_block(m):
    bid  = norm(m.get('yes_bid_dollars') or m.get('yes_bid'))
    ask  = norm(m.get('yes_ask_dollars') or m.get('yes_ask'))
    last = norm(m.get('last_price_dollars') or m.get('last_price'))
    mid  = round(((bid or 0)+(ask or 0))/2, 4) if (bid or ask) else None
    return {
        'yes_bid':    bid,
        'yes_ask':    ask,
        'mid':        mid,
        'implied_pct': round(mid*100,2) if mid else None,
        'american':   american(mid),
        'last_price': last,
        'status':     m.get('status',''),
    }

def best_line(lines_list, implied_key='implied_pct'):
    """Return line closest to 50% implied — that's the equivalent of the traditional market line."""
    if not lines_list: return None
    return min(lines_list, key=lambda x: abs((x.get(implied_key) or 0) - 50.0))

# ── Pull all markets ──────────────────────────────────────────────────────────
print("\nPulling all series...")
all_by_series = {}
for series, mtype in SERIES_CATALOGUE.items():
    mkts = pull_all_statuses(series)
    all_by_series[series] = mkts
    print(f"  {series}: {len(mkts)} markets today")

# ── Parse into game-keyed registry ───────────────────────────────────────────
# First pass: collect all event_tickers seen across all series
event_suffixes = set()
for series, mkts in all_by_series.items():
    for m in mkts:
        et = m.get('event_ticker','')
        # Strip series prefix to get the date+time+teams suffix
        suffix = et.replace(f"{series}-","",1)
        event_suffixes.add(suffix)

print(f"\nDistinct game suffixes: {sorted(event_suffixes)}")

# Parse suffix: {KALSHI_DATE}{HHMM}{AWAY}{HOME}
# e.g. 26JUN042010PITHOU → date=26JUN04, time=2010, away=PIT, home=HOU
# 2-letter MLB team abbreviations on Kalshi
TWO_LETTER_ABBRS = {'TB', 'AZ', 'SF', 'SD', 'KC', 'LA'}  # LA not used but included

def parse_suffix(suffix):
    """
    Returns (time_str, away_abbr, home_abbr) or None.
    Correctly handles 2-letter abbreviations (TB, AZ, SF, SD, KC) by
    checking against a known set before trying 3-letter splits.
    """
    if not suffix.startswith(KALSHI_DATE):
        return None
    rest = suffix[len(KALSHI_DATE):]   # e.g. "1340TBMIA"
    if len(rest) < 6: return None
    time_str = rest[:4]                # "1340"
    teams = rest[4:]                   # "TBMIA" or "PITHOU"

    # Try all valid splits: 2+rest, 3+rest
    # Prefer the split where BOTH parts are valid (known abbr or 2-3 alpha chars)
    candidates = []
    for a_len in [2, 3]:
        if len(teams) <= a_len:
            continue
        away = teams[:a_len]
        home = teams[a_len:]
        if not away.isalpha() or not home.isalpha():
            continue
        # Score: prefer split where away is a known 2-letter abbr
        score = 1 if away in TWO_LETTER_ABBRS else 0
        candidates.append((score, a_len, away, home))

    if not candidates:
        return None

    # Pick highest score; tie-break by trying 2 before 3 for 2-letter teams
    candidates.sort(key=lambda x: (-x[0], -x[1]))  # prefer 3-letter abbr on score tie
    _, _, away, home = candidates[0]
    return time_str, away, home

registry = {}

for suffix in sorted(event_suffixes):
    parsed = parse_suffix(suffix)
    if not parsed:
        print(f"  WARN: cannot parse suffix {suffix}")
        continue
    time_str, away, home = parsed
    kalshi_key = f"{away}{home}"

    # Human-readable game time
    hr = int(time_str[:2])
    mn = time_str[2:]
    ampm = 'PM' if hr >= 12 else 'AM'
    hr12 = hr-12 if hr>12 else (12 if hr==0 else hr)
    game_time = f"{hr12}:{mn} {ampm} ET"

    entry = {
        'kalshi_key':          kalshi_key,
        'date':                DATE,
        'kalshi_date':         KALSHI_DATE,
        'event_ticker_suffix': suffix,
        'game_time_et':        game_time,
        'time_str':            time_str,
        'away':                away,
        'home':                home,
        'snapshot_ts':         SNAPSHOT_TS,
        'markets':             {},
        'closing_snapshots':   [],
    }

    # ── Moneyline (KXMLBGAME) ─────────────────────────────────────────────
    ml_mkts = [m for m in all_by_series.get('KXMLBGAME',[])
               if m.get('event_ticker','').endswith(suffix)]
    if ml_mkts:
        away_m = next((m for m in ml_mkts if m['ticker'].endswith(f'-{away}')), None)
        home_m = next((m for m in ml_mkts if m['ticker'].endswith(f'-{home}')), None)
        entry['markets']['moneyline'] = {
            'series':       'KXMLBGAME',
            'away_ticker':  away_m['ticker'] if away_m else None,
            'home_ticker':  home_m['ticker'] if home_m else None,
            'prices': {
                'away': price_block(away_m) if away_m else None,
                'home': price_block(home_m) if home_m else None,
            }
        }

    # ── Spread (KXMLBSPREAD) ──────────────────────────────────────────────
    sp_mkts = [m for m in all_by_series.get('KXMLBSPREAD',[])
               if m.get('event_ticker','').endswith(suffix)]
    if sp_mkts:
        lines = []
        for m in sp_mkts:
            t = m['ticker']
            # Suffix is -{TEAM}{N}: extract team and run number
            # e.g. -PIT2 or -HOU3
            tail = t.split('-')[-1]   # "PIT2" or "HOU3" or "PIT11"
            # Find where digits start
            digit_start = next((i for i,c in enumerate(tail) if c.isdigit()), len(tail))
            team_part = tail[:digit_start]
            run_part  = tail[digit_start:]
            runs = float(run_part) - 0.5 if run_part.isdigit() else None
            pb = price_block(m)
            lines.append({
                'ticker':      t,
                'team':        team_part,
                'run_number':  int(run_part) if run_part.isdigit() else None,
                'win_by_over': runs,   # "team wins by over X runs"
                **pb,
            })
        lines.sort(key=lambda x: (x['team'], x.get('run_number',0)))
        entry['markets']['spread'] = {
            'series':    'KXMLBSPREAD',
            'lines':     lines,
            'best_line': best_line(lines),
            'note':      'Each market: YES = team wins by over N runs. run_number=2 means "wins by over 1.5"',
        }

    # ── Game Total (KXMLBTOTAL) ───────────────────────────────────────────
    tot_mkts = [m for m in all_by_series.get('KXMLBTOTAL',[])
                if m.get('event_ticker','').endswith(suffix)]
    if tot_mkts:
        lines = []
        for m in tot_mkts:
            t = m['ticker']
            n_str = t.split('-')[-1]
            n = int(n_str) if n_str.isdigit() else None
            pb = price_block(m)
            lines.append({'ticker': t, 'total': n, 'over_threshold': n, **pb})
        lines.sort(key=lambda x: x.get('total',0))
        entry['markets']['total'] = {
            'series':    'KXMLBTOTAL',
            'lines':     lines,
            'best_line': best_line(lines),
            'note':      'Each market: YES = total runs > N (strictly over integer N). No half-run lines.',
        }

    # ── Team Totals (KXMLBTEAMTOTAL) ─────────────────────────────────────
    tt_mkts = [m for m in all_by_series.get('KXMLBTEAMTOTAL',[])
               if m.get('event_ticker','').endswith(suffix)]
    if tt_mkts:
        for team_abbr, tt_key in [(away,'team_total_away'),(home,'team_total_home')]:
            team_lines = []
            for m in tt_mkts:
                t = m['ticker']
                tail = t.split('-')[-1]
                digit_start = next((i for i,c in enumerate(tail) if c.isdigit()), len(tail))
                team_part = tail[:digit_start]
                n_str     = tail[digit_start:]
                if team_part != team_abbr: continue
                n = int(n_str) if n_str.isdigit() else None
                pb = price_block(m)
                team_lines.append({'ticker': t, 'team': team_abbr, 'over_n': n, **pb})
            team_lines.sort(key=lambda x: x.get('over_n',0))
            if team_lines:
                entry['markets'][tt_key] = {
                    'series':    'KXMLBTEAMTOTAL',
                    'team':      team_abbr,
                    'lines':     team_lines,
                    'best_line': best_line(team_lines),
                    'note':      f'YES = {team_abbr} scores > N runs. over_n=4 means "scores over 3.5".',
                }

    # ── F5 Moneyline (KXMLBF5) ───────────────────────────────────────────
    f5_mkts = [m for m in all_by_series.get('KXMLBF5',[])
               if m.get('event_ticker','').endswith(suffix)]
    if f5_mkts:
        away_m = next((m for m in f5_mkts if m['ticker'].endswith(f'-{away}')), None)
        home_m = next((m for m in f5_mkts if m['ticker'].endswith(f'-{home}')), None)
        tie_m  = next((m for m in f5_mkts if m['ticker'].endswith('-TIE')), None)
        entry['markets']['f5_moneyline'] = {
            'series':      'KXMLBF5',
            'away_ticker': away_m['ticker'] if away_m else None,
            'home_ticker': home_m['ticker'] if home_m else None,
            'tie_ticker':  tie_m['ticker'] if tie_m else None,
            'prices': {
                'away': price_block(away_m) if away_m else None,
                'home': price_block(home_m) if home_m else None,
                'tie':  price_block(tie_m)  if tie_m else None,
            },
            'note': 'Three-way market. YES=away wins, YES=home wins, YES=tied after 5. Bet away or home YES side.',
        }

    # ── F5 Spread (KXMLBF5SPREAD) ────────────────────────────────────────
    f5sp_mkts = [m for m in all_by_series.get('KXMLBF5SPREAD',[])
                 if m.get('event_ticker','').endswith(suffix)]
    if f5sp_mkts:
        lines = []
        for m in f5sp_mkts:
            t = m['ticker']
            tail = t.split('-')[-1]
            digit_start = next((i for i,c in enumerate(tail) if c.isdigit()), len(tail))
            team_part = tail[:digit_start]
            n_str     = tail[digit_start:]
            runs = float(n_str) - 0.5 if n_str.isdigit() else None
            pb = price_block(m)
            lines.append({'ticker': t, 'team': team_part, 'run_number': int(n_str) if n_str.isdigit() else None,
                          'win_by_over': runs, **pb})
        lines.sort(key=lambda x: (x['team'], x.get('run_number',0)))
        entry['markets']['f5_spread'] = {
            'series':    'KXMLBF5SPREAD',
            'lines':     lines,
            'best_line': best_line(lines),
        }

    # ── F5 Total (KXMLBF5TOTAL) ──────────────────────────────────────────
    f5tot_mkts = [m for m in all_by_series.get('KXMLBF5TOTAL',[])
                  if m.get('event_ticker','').endswith(suffix)]
    if f5tot_mkts:
        lines = []
        for m in f5tot_mkts:
            t = m['ticker']
            n_str = t.split('-')[-1]
            n = int(n_str) if n_str.isdigit() else None
            pb = price_block(m)
            lines.append({'ticker': t, 'total': n, **pb})
        lines.sort(key=lambda x: x.get('total',0))
        entry['markets']['f5_total'] = {
            'series':    'KXMLBF5TOTAL',
            'lines':     lines,
            'best_line': best_line(lines),
            'note':      'YES = F5 combined runs > N.',
        }

    # ── RFI (KXMLBRFI) ───────────────────────────────────────────────────
    rfi_mkts = [m for m in all_by_series.get('KXMLBRFI',[])
                if m.get('event_ticker','').endswith(suffix)]
    if rfi_mkts:
        m = rfi_mkts[0]
        pb = price_block(m)
        # YES = run scored in 1st inning = YRFI
        # NO  = no run scored = NRFI
        # We store both sides derived from the single binary market
        mid_yes = pb.get('mid')
        mid_no  = round(1.0 - mid_yes, 4) if mid_yes else None
        entry['markets']['rfi'] = {
            'series':       'KXMLBRFI',
            'ticker':       m['ticker'],
            'yes_is_yrfi':  True,
            'prices': {
                'yrfi': {**pb, 'side': 'YES'},
                'nrfi': {
                    'yes_bid':     round(1.0-(pb.get('yes_ask') or 0), 4) if pb.get('yes_ask') else None,
                    'yes_ask':     round(1.0-(pb.get('yes_bid') or 0), 4) if pb.get('yes_bid') else None,
                    'mid':         mid_no,
                    'implied_pct': round(mid_no*100, 2) if mid_no else None,
                    'american':    american(mid_no),
                    'side':        'NO',
                },
            },
            'note': 'Single binary market per game. YES=run scores in 1st=YRFI. NO=no run=NRFI. Bet YES for YRFI, NO for NRFI.',
        }

    registry[kalshi_key] = entry
    mkt_types = list(entry['markets'].keys())
    print(f"  {kalshi_key} ({game_time}): {len(mkt_types)} market types — {mkt_types}")


# ── Backfill missing TT markets from kalshi_search.json ──────────────────────
# build_kalshi_registry.py hits the Kalshi API directly for KXMLBTEAMTOTAL.
# Due to pagination / rate-limiting some games' TT markets are missed.
# kalshi_search.json (from Vercel /api/kalshisearch) reliably contains ALL TT markets.
# Match by event_ticker_suffix (stored in registry during main build loop) to avoid
# re-running the abbreviation split logic.

def backfill_from_search(registry, kalshi_date):
    """
    Backfill missing or null-priced markets from kalshi_search.json.
    Covers: team_total (TT), moneyline (ML), f5_moneyline (F5).
    
    Uses event_ticker_suffix matching (stored in registry entries) to avoid
    re-running the parse_suffix abbreviation logic, which is buggy for 
    2-letter abbreviations (TB, SF, KC) in the old registry.
    
    For ML and F5, also repairs null prices caused by old bad parse_suffix
    (which stored wrong team abbrs like TBM/IA and therefore matched no tickers).
    """
    try:
        with open('data/kalshi_search.json') as f:
            search = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"WARN: could not load kalshi_search.json for backfill: {e}")
        return 0

    markets = search.get('markets', [])

    # Reverse lookup: event_ticker_suffix -> registry key (exact string match, no parsing)
    suffix_to_key = {e['event_ticker_suffix']: k for k, e in registry.items()}

    def price_from_market(m):
        """Extract normalized price block from a kalshi_search market record."""
        bid = m.get('yes_bid')
        ask = m.get('yes_ask')
        mid = m.get('mid') or (((bid or 0)+(ask or 0))/2 if (bid or ask) else None)
        am  = m.get('american_odds') or american(mid)
        return {
            'yes_bid':     bid,
            'yes_ask':     ask,
            'mid':         round(mid, 4) if mid else None,
            'implied_pct': round(mid*100, 2) if mid else None,
            'american':    am,
            'last_price':  m.get('last_price'),
            'status':      m.get('status', 'active'),
            '_source':     'kalshi_search_backfill',
        }

    backfilled_tt = backfilled_ml = backfilled_f5 = 0

    # ── TT backfill ───────────────────────────────────────────────────────────
    tt_mkts = [m for m in markets
               if m.get('market_type') == 'team_total'
               and kalshi_date in m.get('event_ticker', '')]

    suffix_to_tt = {}
    for m in tt_mkts:
        suffix = m['event_ticker'].replace('KXMLBTEAMTOTAL-', '', 1)
        suffix_to_tt.setdefault(suffix, []).append(m)

    for suffix, mkts_list in suffix_to_tt.items():
        reg_key = suffix_to_key.get(suffix)
        if not reg_key:
            continue
        entry = registry[reg_key]
        if 'team_total_away' in entry['markets'] and 'team_total_home' in entry['markets']:
            continue

        # Derive real team abbrs from ticker suffixes (source of truth from Kalshi)
        found_teams = set()
        for m in mkts_list:
            tail = m.get('market_ticker','').split('-')[-1]
            ds   = next((i for i,c in enumerate(tail) if c.isdigit()), len(tail))
            tp   = tail[:ds]
            if tp: found_teams.add(tp)

        away_team = entry['away']
        home_team  = entry['home']
        if away_team not in found_teams and home_team not in found_teams:
            sorted_t = sorted(found_teams)
            if len(sorted_t) == 2:
                away_team, home_team = sorted_t
            else:
                continue

        for team_abbr, tt_key in [(away_team,'team_total_away'),(home_team,'team_total_home')]:
            if tt_key in entry['markets']: continue
            team_lines = []
            for m in mkts_list:
                tail  = m.get('market_ticker','').split('-')[-1]
                ds    = next((i for i,c in enumerate(tail) if c.isdigit()), len(tail))
                tp    = tail[:ds]; n_str = tail[ds:]
                if tp != team_abbr: continue
                n = int(n_str) if n_str.isdigit() else None
                line = {'ticker': m.get('market_ticker'), 'team': team_abbr, 'over_n': n,
                        **price_from_market(m)}
                team_lines.append(line)
            if team_lines:
                team_lines.sort(key=lambda x: x.get('over_n') or 0)
                entry['markets'][tt_key] = {
                    'series': 'KXMLBTEAMTOTAL', 'team': team_abbr,
                    'lines': team_lines, 'best_line': best_line(team_lines),
                    'note': 'Backfilled from kalshi_search.json',
                    '_source': 'kalshi_search_backfill',
                }
                backfilled_tt += 1

    # ── ML backfill ───────────────────────────────────────────────────────────
    # Repairs null prices caused by old parse_suffix storing wrong abbrs (TBM/IA).
    ml_mkts = [m for m in markets
               if m.get('market_type') == 'moneyline'
               and kalshi_date in m.get('event_ticker', '')]

    suffix_to_ml = {}
    for m in ml_mkts:
        suffix = m['event_ticker'].replace('KXMLBGAME-', '', 1)
        suffix_to_ml.setdefault(suffix, []).append(m)

    for suffix, mkts_list in suffix_to_ml.items():
        reg_key = suffix_to_key.get(suffix)
        if not reg_key: continue
        entry = registry[reg_key]
        ml = entry['markets'].get('moneyline', {})
        prices = ml.get('prices', {})
        # Only backfill if prices are null (bad parse_suffix caused wrong ticker matching)
        if (prices.get('away') or {}).get('american') is not None:
            continue

        # Match each ticker to away/home using the suffix: -TB is away, -MIA is home
        # We know the registry key (TBMIA) and the team abbrs from the tickers themselves
        new_prices = {}
        new_tickers = {}
        for m in mkts_list:
            ticker = m.get('market_ticker','')
            team_part = ticker.split('-')[-1]  # TB, MIA, SF, CHC, KC, MIN
            pb = price_from_market(m)
            new_prices[team_part]  = pb
            new_tickers[team_part] = ticker

        # Determine which team_part is away and which is home
        # The registry key is e.g. TBMIA — but entry['away']/['home'] may be wrong
        # Use kalshi_search market ordering: in the registry the away_ticker ends with -AWAY_ABBR
        # We can cross-reference with what parse_suffix NOW correctly returns for this suffix
        team_parts = list(new_prices.keys())
        if len(team_parts) != 2:
            continue
        
        # Re-parse suffix with FIXED parse_suffix to get correct away/home order
        parsed = parse_suffix(suffix)
        if parsed:
            _, correct_away, correct_home = parsed
            away_pb = new_prices.get(correct_away)
            home_pb  = new_prices.get(correct_home)
            if away_pb and home_pb:
                ml['away_ticker'] = new_tickers.get(correct_away)
                ml['home_ticker'] = new_tickers.get(correct_home)
                ml.setdefault('prices', {})['away'] = away_pb
                ml['prices']['home']                 = home_pb
                ml['_backfilled'] = True
                # Also repair entry away/home if they were wrong
                entry['away'] = correct_away
                entry['home']  = correct_home
                backfilled_ml += 1
                print(f"  ML backfilled {reg_key}: {correct_away}={away_pb.get('american')} {correct_home}={home_pb.get('american')}")

    # ── F5 backfill ───────────────────────────────────────────────────────────
    f5_mkts = [m for m in markets
               if m.get('market_type') == 'f5_moneyline'
               and kalshi_date in m.get('event_ticker', '')]

    suffix_to_f5 = {}
    for m in f5_mkts:
        suffix = m['event_ticker'].replace('KXMLBF5-', '', 1)
        suffix_to_f5.setdefault(suffix, []).append(m)

    for suffix, mkts_list in suffix_to_f5.items():
        reg_key = suffix_to_key.get(suffix)
        if not reg_key: continue
        entry = registry[reg_key]
        # FIX: must get a reference to the existing entry OR create a new one and assign it back.
        # Previously: entry['markets'].get('f5_moneyline', {}) returned a disconnected empty dict
        # when the key was absent, so all writes were lost. Now we always write back explicitly.
        f5 = entry['markets'].get('f5_moneyline')
        if f5 is None:
            f5 = {}
            # Will assign into entry['markets'] only if we successfully build prices (below)
        prices = f5.get('prices', {})
        if (prices.get('away') or {}).get('american') is not None:
            continue  # already have prices

        # Match: -TB away, -MIA home, -TIE tie
        new_prices = {}; new_tickers = {}
        event_ticker_val = None
        for m in mkts_list:
            ticker = m.get('market_ticker','')
            team_part = ticker.split('-')[-1]  # TB, MIA, TIE
            new_prices[team_part]  = price_from_market(m)
            new_tickers[team_part] = ticker
            if event_ticker_val is None:
                event_ticker_val = m.get('event_ticker', '')

        parsed = parse_suffix(suffix)
        if not parsed: continue
        _, correct_away, correct_home = parsed

        away_pb = new_prices.get(correct_away)
        home_pb  = new_prices.get(correct_home)
        tie_pb   = new_prices.get('TIE')
        if away_pb and home_pb:
            f5['series']       = 'KXMLBF5'
            f5['eventTicker']  = event_ticker_val or f'KXMLBF5-{suffix}'
            f5['seriesTicker'] = 'KXMLBF5'
            f5['away_ticker']  = new_tickers.get(correct_away)
            f5['home_ticker']  = new_tickers.get(correct_home)
            f5['tie_ticker']   = new_tickers.get('TIE')
            f5.setdefault('prices', {})['away'] = away_pb
            f5['prices']['home']                = home_pb
            f5['prices']['tie']                 = tie_pb
            f5['_backfilled']  = True
            # FIX: write the (possibly new) f5 dict back into the registry entry
            entry['markets']['f5_moneyline'] = f5
            backfilled_f5 += 1
            print(f"  F5 backfilled {reg_key}: {correct_away}={away_pb.get('american')} {correct_home}={home_pb.get('american')}")

    total = backfilled_tt + backfilled_ml + backfilled_f5
    if total:
        print(f"  Backfill total: TT={backfilled_tt} ML={backfilled_ml} F5={backfilled_f5}")
    return total

bf_count = backfill_from_search(registry, KALSHI_DATE)
if bf_count > 0:
    print(f"  Backfill complete: {bf_count} entries added/repaired from kalshi_search.json")


# ── Load existing registry to preserve closing_snapshots ─────────────────────
REGISTRY_PATH = 'data/kalshi_market_registry.json'
try:
    with open(REGISTRY_PATH) as f:
        existing = json.load(f)
    existing_registry = existing.get('registry', {})
    # Preserve closing_snapshots from previous runs
    for key, entry in registry.items():
        if key in existing_registry:
            old_snaps = existing_registry[key].get('closing_snapshots', [])
            # Only keep snapshots from today
            entry['closing_snapshots'] = [
                s for s in old_snaps if s.get('date','') == DATE
            ]
except (FileNotFoundError, json.JSONDecodeError):
    pass

# ── Write registry ────────────────────────────────────────────────────────────
os.makedirs('data', exist_ok=True)
output = {
    'generated_at': SNAPSHOT_TS,
    'date':         DATE,
    'kalshi_date':  KALSHI_DATE,
    'series_catalogue': {
        s: {'market_type': mt, 'note': SERIES_NOTES[s]}
        for s, mt in SERIES_CATALOGUE.items()
    },
    'ticker_format_guide': {
        'event_ticker':     '{SERIES}-{YYMONDD}{HHMM}{AWAY}{HOME}',
        'market_ticker':    '{SERIES}-{YYMONDD}{HHMM}{AWAY}{HOME}-{SUFFIX}',
        'suffix_by_series': {
            'KXMLBGAME':      '{TEAM_ABBR}  e.g. -PIT or -HOU',
            'KXMLBSPREAD':    '{TEAM}{N}     e.g. -PIT2 = PIT wins by >1.5 runs',
            'KXMLBTOTAL':     '{N}           e.g. -8 = total runs over 8',
            'KXMLBTEAMTOTAL': '{TEAM}{N}     e.g. -PIT4 = PIT scores over 3.5 runs',
            'KXMLBF5':        '{TEAM} or TIE  e.g. -PIT, -HOU, -TIE',
            'KXMLBF5SPREAD':  '{TEAM}{N}     e.g. -PIT2 = PIT wins F5 by >1.5',
            'KXMLBF5TOTAL':   '{N}           e.g. -4 = F5 total over 4',
            'KXMLBRFI':       '(none)        single market per game, no suffix',
        }
    },
    'price_structure': {
        'yes_bid':    'Highest price a buyer will pay for YES contract (decimal 0–1)',
        'yes_ask':    'Lowest price a seller will accept for YES contract (decimal 0–1)',
        'mid':        '(yes_bid + yes_ask) / 2 — use this as the implied probability',
        'implied_pct':'mid × 100 — percentage probability',
        'american':   'Equivalent American odds derived from mid',
        'vig_free':   'Kalshi is a two-sided exchange — VF is computed per market pair',
    },
    'usage_for_session': {
        'bet_price':     'Read american from markets.{type}.prices.{side} at analysis time',
        'closing_line':  'snapshot captured at game start into closing_snapshots[] by capture_closing_lines.py',
        'clv_source':    'closing_snapshots[0].prices.{side}.american vs betTimeLine',
        'which_line':    'For spread/total/TT: use best_line (closest to 50% implied = traditional market line)',
    },
    'registry': registry,
}

with open(REGISTRY_PATH, 'w') as f:
    json.dump(output, f, indent=2)

print(f"\n[DONE] Written {REGISTRY_PATH}")
print(f"  Games registered: {len(registry)}")
for key, entry in sorted(registry.items()):
    mkt_count = sum(len(v.get('lines',[])) if isinstance(v,dict) and 'lines' in v else 1
                    for v in entry['markets'].values())
    print(f"  {key}: {len(entry['markets'])} market types, {mkt_count} total tickers")

# ── Per-game DATA-HEALTH WARNING: F5 ticker present but prices null ─────────
# Emitted when a game has f5_moneyline in registry but away or home price is missing.
# This distinguishes "no F5 market exists" from "market exists but prices are missing".
for _key, _entry in sorted(registry.items()):
    _f5m = _entry.get('markets', {}).get('f5_moneyline', {})
    if _f5m:
        _away_am = (_f5m.get('prices', {}).get('away') or {}).get('american')
        _home_am = (_f5m.get('prices', {}).get('home') or {}).get('american')
        _away_tkr = _f5m.get('away_ticker')
        _home_tkr = _f5m.get('home_ticker')
        if (_away_tkr or _home_tkr) and (_away_am is None or _home_am is None):
            print(f"DATA-HEALTH WARNING: {_key} f5_moneyline has tickers "
                  f"(away={_away_tkr} home={_home_tkr}) but prices null "
                  f"(away_am={_away_am} home_am={_home_am}). "
                  f"F5 bet will be Missing Data. Source: {_f5m.get('_source','live_api')}")

# ── F5 moneyline visibility check ────────────────────────────────────────────
# Counts raw KXMLBF5 markets from the API pull, then checks how many registry
# entries actually have away+home F5 prices. Mismatch = parse_suffix or backfill bug.
_f5_discovered = len(all_by_series.get('KXMLBF5', []))
_f5_games_mapped = sum(
    1 for entry in registry.values()
    if (entry.get('markets', {}).get('f5_moneyline', {}).get('prices', {}).get('away') or {}).get('american') is not None
)
print(f"\n[F5-VISIBILITY] KXMLBF5 markets discovered (raw API): {_f5_discovered}")
print(f"[F5-VISIBILITY] Games with F5 moneyline prices in registry: {_f5_games_mapped}/{len(registry)}")
if _f5_discovered > 0 and _f5_games_mapped == 0:
    print("[F5-VISIBILITY] WARNING: F5 moneyline discovery succeeded but mapping into the registry failed.")
    print("[F5-VISIBILITY] Check parse_suffix() and backfill_from_search() — likely a suffix parse bug.")
elif _f5_discovered == 0:
    print("[F5-VISIBILITY] NOTE: No KXMLBF5 markets found via direct API pull (expected if Kalshi 403s).")
    print("[F5-VISIBILITY] Backfill from kalshi_search.json is the active F5 source.")
    _f5_backfill_mapped = sum(
        1 for entry in registry.values()
        if (entry.get('markets', {}).get('f5_moneyline', {}).get('prices', {}).get('away') or {}).get('american') is not None
    )
    print(f"[F5-VISIBILITY] F5 prices from backfill (kalshi_search.json): {_f5_backfill_mapped}/{len(registry)}")
    if _f5_backfill_mapped == 0:
        print("[F5-VISIBILITY] WARNING: F5 moneyline discovery succeeded but mapping into the registry failed.")
        print("[F5-VISIBILITY] Check parse_suffix() and backfill_from_search() — likely a suffix parse bug.")
    else:
        print(f"[F5-VISIBILITY] OK: F5 moneyline prices present in registry for {_f5_backfill_mapped} game(s).")
else:
    print(f"[F5-VISIBILITY] OK: F5 moneyline prices present in registry for {_f5_games_mapped} game(s).")


