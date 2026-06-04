"""
scripts/capture_closing_lines.py — v2.0
=========================================
Captures Kalshi closing prices for today's open bets at game start.
Now reads directly from data/kalshi_market_registry.json instead of
The Odds API, so every market type (ML, RL, Total, TT, F5, NRFI/YRFI)
gets an accurate Kalshi price at first pitch.

Two modes:
  1. Snapshot mode (called from fetch-slate workflow):
     Re-fetches live Kalshi prices for every registered market and appends
     a timestamped snapshot to registry[game].closing_snapshots[].
     This is the source of truth for CLV when bets are settled.

  2. Bet settlement mode (called from update-clv workflow):
     For each PENDING bet, finds the most recent closing_snapshot taken
     before or at game start and writes it to bets.json as closingLine.
"""

import json, os, sys
from datetime import datetime, timezone, timedelta
from urllib.request import urlopen, Request
from urllib.error import HTTPError

KALSHI_BASE  = 'https://api.elections.kalshi.com/trade-api/v2'
REGISTRY_PATH = 'data/kalshi_market_registry.json'
BETS_PATH     = 'bets.json'
MODE = sys.argv[1] if len(sys.argv) > 1 else 'snapshot'  # 'snapshot' or 'settle'

def get(url):
    try:
        req = Request(url, headers={'Accept':'application/json','User-Agent':'Mozilla/5.0'})
        with urlopen(req, timeout=15) as r:
            return json.loads(r.read()), None
    except HTTPError as e:
        return None, f"HTTP{e.code}"
    except Exception as e:
        return None, str(e)[:80]

def norm(v):
    if v is None: return None
    f = float(v)
    return round(f if f<=1.0 else f/100.0, 4)

def american(mid):
    if not mid or mid<=0 or mid>=1: return None
    return round(-(mid/(1-mid))*100) if mid>=0.5 else round(((1-mid)/mid)*100)

def price_block(m):
    bid  = norm(m.get('yes_bid_dollars') or m.get('yes_bid'))
    ask  = norm(m.get('yes_ask_dollars') or m.get('yes_ask'))
    last = norm(m.get('last_price_dollars') or m.get('last_price'))
    mid  = round(((bid or 0)+(ask or 0))/2, 4) if (bid or ask) else None
    return {
        'yes_bid':     bid,
        'yes_ask':     ask,
        'mid':         mid,
        'implied_pct': round(mid*100,2) if mid else None,
        'american':    american(mid),
        'last_price':  last,
    }

def american_to_prob(odds):
    try:
        o = float(odds)
        return 100/(o+100) if o >= 0 else abs(o)/(abs(o)+100)
    except:
        return None

# ── Load registry ─────────────────────────────────────────────────────────────
try:
    with open(REGISTRY_PATH) as f:
        reg_doc = json.load(f)
    registry = reg_doc.get('registry', {})
    print(f"Registry: {len(registry)} games loaded")
except FileNotFoundError:
    print("ERROR: kalshi_market_registry.json not found — run build_kalshi_registry.py first")
    sys.exit(0)

NOW_TS = datetime.now(tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
DATE_ET = datetime.now(tz=timezone(timedelta(hours=-4))).strftime('%Y-%m-%d')

# ── MODE 1: Snapshot — fetch live prices and save to registry ─────────────────
if MODE == 'snapshot':
    print(f"\n[SNAPSHOT MODE] Capturing live Kalshi prices for all registered markets")
    print(f"Timestamp: {NOW_TS}")

    updated_games = 0
    for kalshi_key, entry in registry.items():
        if entry.get('date') != DATE_ET:
            continue

        print(f"\n  {kalshi_key} ({entry.get('game_time_et')})...")
        snapshot = {
            'snapshot_ts': NOW_TS,
            'date':        DATE_ET,
            'prices':      {}
        }
        any_price = False

        for mkt_type, mkt_data in entry.get('markets', {}).items():
            snap_mkt = {}

            if mkt_type == 'moneyline':
                for side, ticker_key in [('away','away_ticker'),('home','home_ticker')]:
                    ticker = mkt_data.get(ticker_key)
                    if not ticker: continue
                    data, _ = get(f"{KALSHI_BASE}/markets/{ticker}")
                    if data and data.get('market'):
                        m = data['market']
                        snap_mkt[side] = price_block(m)
                        any_price = True

            elif mkt_type == 'rfi':
                ticker = mkt_data.get('ticker')
                if ticker:
                    data, _ = get(f"{KALSHI_BASE}/markets/{ticker}")
                    if data and data.get('market'):
                        m = data['market']
                        pb = price_block(m)
                        mid_yes = pb.get('mid')
                        mid_no  = round(1.0-mid_yes,4) if mid_yes else None
                        snap_mkt['yrfi'] = {**pb, 'side':'YES'}
                        snap_mkt['nrfi'] = {
                            'yes_bid':     round(1.0-(pb.get('yes_ask') or 0),4) if pb.get('yes_ask') else None,
                            'yes_ask':     round(1.0-(pb.get('yes_bid') or 0),4) if pb.get('yes_bid') else None,
                            'mid':         mid_no,
                            'implied_pct': round(mid_no*100,2) if mid_no else None,
                            'american':    american(mid_no),
                            'side':        'NO',
                        }
                        any_price = True

            elif mkt_type == 'f5_moneyline':
                for side, ticker_key in [('away','away_ticker'),('home','home_ticker'),('tie','tie_ticker')]:
                    ticker = mkt_data.get(ticker_key)
                    if not ticker: continue
                    data, _ = get(f"{KALSHI_BASE}/markets/{ticker}")
                    if data and data.get('market'):
                        snap_mkt[side] = price_block(data['market'])
                        any_price = True

            elif 'lines' in mkt_data:
                # Spread, Total, TT, F5 Spread, F5 Total — refresh each line ticker
                refreshed = []
                for line in (mkt_data.get('lines') or []):
                    ticker = line.get('ticker')
                    if not ticker:
                        refreshed.append(line)
                        continue
                    data, _ = get(f"{KALSHI_BASE}/markets/{ticker}")
                    if data and data.get('market'):
                        pb = price_block(data['market'])
                        refreshed.append({**line, **pb})
                        any_price = True
                    else:
                        refreshed.append(line)
                # Find best line (closest to 50%)
                bl = min(refreshed, key=lambda x: abs((x.get('implied_pct') or 0)-50.0)) if refreshed else None
                snap_mkt['lines']     = refreshed
                snap_mkt['best_line'] = bl

            if snap_mkt:
                snapshot['prices'][mkt_type] = snap_mkt

        if any_price:
            # Keep only snapshots from today, cap at 10 per game
            existing_snaps = [s for s in entry.get('closing_snapshots',[]) if s.get('date')==DATE_ET]
            existing_snaps.append(snapshot)
            entry['closing_snapshots'] = existing_snaps[-10:]
            updated_games += 1
            print(f"    Captured {len(snapshot['prices'])} market types")
        else:
            print(f"    No live prices found")

    # Save updated registry
    reg_doc['registry'] = registry
    reg_doc['last_snapshot_ts'] = NOW_TS
    with open(REGISTRY_PATH, 'w') as f:
        json.dump(reg_doc, f, indent=2)
    print(f"\n[DONE] Snapshot complete: {updated_games} games updated in {REGISTRY_PATH}")


# ── MODE 2: Settle — write closing lines to bets.json ────────────────────────
elif MODE == 'settle':
    print(f"\n[SETTLE MODE] Writing closing lines from registry to bets.json")

    with open(BETS_PATH) as f:
        bets = json.load(f)

    # Build lookup: kalshi_key → most recent closing snapshot
    snapshot_lookup = {}
    for kalshi_key, entry in registry.items():
        snaps = entry.get('closing_snapshots', [])
        if snaps:
            # Use the most recent snapshot — it represents closing prices
            snapshot_lookup[kalshi_key] = snaps[-1]

    TEAM_TO_KEY = {}  # abbreviated team name → possible kalshi_key prefixes/suffixes
    # Build reverse lookup from registry
    for kalshi_key, entry in registry.items():
        TEAM_TO_KEY[entry.get('away','')] = kalshi_key
        TEAM_TO_KEY[entry.get('home','')] = kalshi_key

    def parse_game_key(game_str):
        """'PIT @ HOU' → 'PITHOU'"""
        sep = ' @ ' if ' @ ' in game_str else '@'
        parts = game_str.split(sep, 1)
        if len(parts) != 2: return None
        return parts[0].strip() + parts[1].strip()

    MARKET_TO_REG_TYPE = {
        'ML':         'moneyline',
        'F5 ML':      'f5_moneyline',
        'Run Line':   'spread',
        'Total':      'total',
        'Team Total': None,  # handled by side
        'NRFI':       'rfi',
        'YRFI':       'rfi',
    }

    updated = 0
    for b in bets:
        if b.get('closingLine') is not None: continue
        if b.get('status') in ('WIN','LOSS','PUSH','VOID','SETTLED'): continue
        if b.get('date') != DATE_ET: continue

        game_str  = b.get('game','')
        market    = b.get('market','')
        bet_side  = (b.get('betSide') or '').upper()

        # Find kalshi_key
        game_key = parse_game_key(game_str)
        if not game_key or game_key not in snapshot_lookup:
            # Try reverse lookup
            for abbr, kk in TEAM_TO_KEY.items():
                if abbr in game_str:
                    game_key = kk
                    break

        snap = snapshot_lookup.get(game_key)
        if not snap:
            print(f"  NO_SNAPSHOT: {game_str}")
            continue

        prices = snap.get('prices', {})
        reg_type = MARKET_TO_REG_TYPE.get(market)

        closing_price = None
        closing_prob  = None

        if reg_type == 'moneyline':
            side_prices = prices.get('moneyline', {})
            side = 'away' if 'AWAY' in bet_side else 'home'
            p = side_prices.get(side, {})
            closing_price = p.get('american')
            closing_prob  = p.get('implied_pct')

        elif reg_type == 'f5_moneyline':
            side_prices = prices.get('f5_moneyline', {})
            side = 'away' if 'AWAY' in bet_side else 'home'
            p = side_prices.get(side, {})
            closing_price = p.get('american')
            closing_prob  = p.get('implied_pct')

        elif reg_type == 'rfi':
            rfi_prices = prices.get('rfi', {})
            rfi_side = 'yrfi' if market == 'YRFI' else 'nrfi'
            p = rfi_prices.get(rfi_side, {})
            closing_price = p.get('american')
            closing_prob  = p.get('implied_pct')

        elif reg_type == 'spread':
            sp_prices = prices.get('spread', {})
            bl = sp_prices.get('best_line', {})
            closing_price = bl.get('american')
            closing_prob  = bl.get('implied_pct')

        elif reg_type == 'total':
            tot_prices = prices.get('total', {})
            bl = tot_prices.get('best_line', {})
            closing_price = bl.get('american')
            closing_prob  = bl.get('implied_pct')

        elif market == 'Team Total':
            tt_side = 'team_total_away' if 'AWAY' in bet_side else 'team_total_home'
            tt_prices = prices.get(tt_side, {})
            bl = tt_prices.get('best_line', {})
            closing_price = bl.get('american')
            closing_prob  = bl.get('implied_pct')

        if closing_price is not None:
            b['closingLine']          = closing_price
            b['closingLinePct']       = closing_prob
            b['closingLineSource']    = 'kalshi_registry'
            b['closingLineTimestamp'] = snap.get('snapshot_ts', NOW_TS)
            updated += 1
            print(f"  ✓ {game_str} {market} {bet_side} → closing={closing_price} ({closing_prob}%)")
        else:
            print(f"  ? {game_str} {market} {bet_side} → no price in snapshot")

    with open(BETS_PATH, 'w') as f:
        json.dump(bets, f, indent=2)
    print(f"\n[DONE] Settle complete: {updated} bets updated with closing lines")
