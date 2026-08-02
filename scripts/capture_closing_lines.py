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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from capture_pregame_closing_lines import parse_scheduled_start_utc  # DST-aware ET->UTC
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))
from atomic_json import write_json_atomic

KALSHI_BASE  = 'https://api.elections.kalshi.com/trade-api/v2'
REGISTRY_PATH = 'data/kalshi_market_registry.json'
BETS_PATH     = 'bets.json'
MODE     = sys.argv[1] if len(sys.argv) > 1 else 'snapshot'  # 'snapshot' or 'settle'
DATE_ARG = sys.argv[2] if len(sys.argv) > 2 else os.environ.get('DATE', '')

# Ladder (multi-line) market types keyed by the bet's `market` field.
# 'Team Total' resolves to team_total_away/home based on betSide at match time.
LADDER_MARKET_TYPE = {
    'RUN LINE':  'spread',
    'RL':        'spread',
    'TOTAL':     'total',
    'TEAM TOTAL': 'team_total',
    'F5 SPREAD': 'f5_spread',
    'F5 RL':     'f5_spread',
    'F5 TOTAL':  'f5_total',
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
if DATE_ARG:
    DATE_ET = DATE_ARG
    print(f"Using provided date: {DATE_ET}")
else:
    DATE_ET = datetime.now(tz=timezone(timedelta(hours=-4))).strftime('%Y-%m-%d')
    print(f"Using computed ET date: {DATE_ET}")

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

    TEAM_TO_KEY = {}  # abbreviated team name → possible kalshi_key prefixes/suffixes
    SUFFIX_TO_KEY = {}  # event_ticker_suffix (date+time+teams) → kalshi_key
    for kalshi_key, entry in registry.items():
        TEAM_TO_KEY[entry.get('away','')] = kalshi_key
        TEAM_TO_KEY[entry.get('home','')] = kalshi_key
        suffix = entry.get('event_ticker_suffix')
        if suffix:
            SUFFIX_TO_KEY[suffix] = kalshi_key

    def parse_game_key(game_str):
        """'PIT @ HOU' → 'PITHOU'"""
        sep = ' @ ' if ' @ ' in game_str else '@'
        parts = game_str.split(sep, 1)
        if len(parts) != 2: return None
        return parts[0].strip() + parts[1].strip()

    def ticker_suffix(ticker_or_event_ticker):
        """'KXMLBTEAMTOTAL-26JUN091940ATLCWS-CWS4' -> '26JUN091940ATLCWS'."""
        if not ticker_or_event_ticker:
            return None
        parts = ticker_or_event_ticker.split('-')
        return parts[1] if len(parts) >= 2 else None

    def resolve_registry_entry(bet):
        """
        Resolve the (kalshi_key, entry) for a bet.

        Prefers exact ticker/event-ticker identity (event_ticker_suffix
        encodes date+time+teams) over team-name string parsing, so two
        games between the same two teams on the same date (a doubleheader)
        are never confused with each other — string parsing on `game`
        alone cannot distinguish them, but the ticker's embedded start
        time can.
        """
        for suffix_source in (bet.get('eventTicker'), bet.get('marketTicker'), bet.get('ticker')):
            suffix = ticker_suffix(suffix_source)
            if suffix and suffix in SUFFIX_TO_KEY:
                key = SUFFIX_TO_KEY[suffix]
                return key, registry[key]

        game_str = bet.get('game', '')
        game_key = parse_game_key(game_str)
        if game_key and game_key in registry:
            return game_key, registry[game_key]
        for abbr, kk in TEAM_TO_KEY.items():
            if abbr and abbr in game_str:
                return kk, registry.get(kk)
        return None, None

    def parse_iso(ts_str):
        if not ts_str:
            return None
        try:
            s = ts_str.replace('Z', '+00:00')
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except Exception:
            return None

    def select_closing_snapshot(entry, scheduled_start_utc):
        """
        Snapshot-selection rules (never violated):
          1. Prefer official_closing_snapshot (set by
             capture_pregame_closing_lines.py — always PRE_START by
             construction, closest to first pitch).
          2. Otherwise, the closest snapshot whose own timestamp is at or
             before scheduled first pitch.
          3. A snapshot timestamped after first pitch is NEVER used as a
             valid closing line, no matter how close.

        Returns (snapshot_or_None, source_label_or_None, status) where
        status is one of OK / LATE_ONLY / NO_SNAPSHOT.
        """
        official = entry.get('official_closing_snapshot')
        if official and official.get('prices'):
            return official, 'official_closing_snapshot', 'OK'

        snaps = entry.get('closing_snapshots', [])
        pre, late = [], []
        for s in snaps:
            ts = parse_iso(s.get('snapshot_ts'))
            if ts is None or scheduled_start_utc is None:
                continue
            (pre if ts <= scheduled_start_utc else late).append((ts, s))

        if pre:
            pre.sort(key=lambda x: x[0])
            return pre[-1][1], 'closest_pre_start_snapshot', 'OK'
        if late:
            return None, None, 'LATE_ONLY'
        return None, None, 'NO_SNAPSHOT'

    def normalize_side_abbr(bet_side, entry):
        bs = (bet_side or '').upper()
        if bs == 'AWAY':
            return (entry.get('away') or '').upper()
        if bs == 'HOME':
            return (entry.get('home') or '').upper()
        return bs

    def resolve_exact_price(bet, entry, snapshot):
        """
        Find the exact contract price the bet corresponds to within a
        single snapshot. Prefers the wager's exact Kalshi ticker; for
        ladder markets (spread/total/team total/F5 spread/F5 total) that
        lack a stored ticker, falls back to exact side + exact line —
        never the registry's generic best_line, which can silently compare
        the bet to a different contract at closing.
        """
        market = (bet.get('market') or '').strip().upper()
        ticker = bet.get('marketTicker') or bet.get('ticker')
        prices = snapshot.get('prices', {}) or {}
        reg_markets = entry.get('markets', {}) or {}

        # RFI/NRFI: side is unambiguous from the bet's own market field;
        # both share a single underlying Kalshi ticker (already inverted
        # into yrfi/nrfi sub-blocks at capture time).
        if market in ('NRFI', 'YRFI'):
            rfi = prices.get('rfi', {}) or {}
            return rfi.get('yrfi') if market == 'YRFI' else rfi.get('nrfi')

        # Fast path: flat by_ticker index (capture_pregame_closing_lines.py
        # snapshots always carry this; older snapshot-mode ones may not).
        by_ticker = prices.get('by_ticker')
        if ticker and by_ticker and ticker in by_ticker and by_ticker[ticker].get('mid') is not None:
            return by_ticker[ticker]

        if market in ('ML', 'F5 ML', 'F5'):
            mkt_type = 'f5_moneyline' if market in ('F5 ML', 'F5') else 'moneyline'
            reg_mkt = reg_markets.get(mkt_type, {})
            side = None
            if ticker:
                for tk_field, s in (('away_ticker','away'), ('home_ticker','home'), ('tie_ticker','tie')):
                    if reg_mkt.get(tk_field) == ticker:
                        side = s
                        break
            if side is None:
                abbr = normalize_side_abbr(bet.get('betSide'), entry)
                if abbr == (entry.get('away') or '').upper():
                    side = 'away'
                elif abbr == (entry.get('home') or '').upper():
                    side = 'home'
            if side:
                return (prices.get(mkt_type) or {}).get(side)
            return None

        mkt_type = LADDER_MARKET_TYPE.get(market)
        if mkt_type == 'team_total':
            abbr = normalize_side_abbr(bet.get('betSide'), entry)
            mkt_type = 'team_total_away' if abbr == (entry.get('away') or '').upper() else 'team_total_home'
        if mkt_type:
            lines = (prices.get(mkt_type) or {}).get('lines', [])
            if ticker:
                for line in lines:
                    if line.get('ticker') == ticker:
                        return line
            # Otherwise: exact side + exact line (never best_line).
            bet_line = bet.get('line')
            abbr = normalize_side_abbr(bet.get('betSide'), entry)
            for line in lines:
                if bet_line is None:
                    line_matches = False
                else:
                    line_matches = (
                        line.get('total') == bet_line
                        or line.get('over_n') == bet_line
                        or line.get('win_by_over') == abs(bet_line)
                    )
                side_matches = True
                if abbr and mkt_type in ('spread', 'f5_spread'):
                    side_matches = (line.get('team') or '').upper() == abbr
                if line_matches and side_matches:
                    return line
        return None

    updated = 0
    updated_late = 0
    for b in bets:
        if b.get('closingLine') is not None: continue
        if b.get('status') in ('WIN','LOSS','PUSH','VOID','SETTLED'): continue
        if b.get('date') != DATE_ET: continue

        game_str = b.get('game', '')
        market   = b.get('market', '')

        key, entry = resolve_registry_entry(b)
        if not entry:
            print(f"  NO_GAME_MATCH: {game_str}")
            continue

        scheduled_start_utc = parse_iso(b.get('scheduledStartTime'))
        if scheduled_start_utc is None:
            scheduled_start_utc = parse_scheduled_start_utc(entry.get('date'), entry.get('time_str'))

        snap, source, status = select_closing_snapshot(entry, scheduled_start_utc)

        if status == 'NO_SNAPSHOT':
            print(f"  NO_SNAPSHOT: {game_str} {market}")
            continue

        if status == 'LATE_ONLY':
            b['closingLine'] = None
            b['clvCaptureStatus'] = 'LATE_ONLY'
            b['closingLineUnavailableReason'] = (
                'Only post-first-pitch ("LATE") Kalshi snapshots exist for this '
                'game — no valid pre-start closing line was captured, so CLV '
                'cannot be computed. A late snapshot is never promoted to an '
                'official closing line.'
            )
            updated_late += 1
            print(f"  LATE_ONLY: {game_str} {market} — no pre-start snapshot available")
            continue

        price = resolve_exact_price(b, entry, snap)
        if price is None or price.get('mid') is None:
            print(f"  NO_CONTRACT_MATCH: {game_str} {market} ticker={b.get('marketTicker')}")
            continue

        entry_price = b.get('betTimeLine') or b.get('price')
        entry_prob = american_to_prob(entry_price)
        entry_pct = round(entry_prob * 100, 2) if entry_prob is not None else None

        closing_ask_pct = round(price['yes_ask'] * 100, 2) if price.get('yes_ask') is not None else None
        closing_mid_pct = round(price['mid'] * 100, 2) if price.get('mid') is not None else None

        b['closingLine']          = closing_ask_pct
        b['closingAskPct']        = closing_ask_pct
        b['closingMidPct']        = closing_mid_pct
        b['closingTicker']        = price.get('ticker') or b.get('marketTicker')
        b['closingLineSource']    = source
        b['closingLineTimestamp'] = snap.get('snapshot_ts', NOW_TS)
        b['clvCaptureStatus']     = 'OK'
        b['closingLineUnavailableReason'] = None
        # Positive CLV = the contract became MORE expensive after entry
        # (we bought before the market moved toward us).
        if entry_pct is not None and closing_ask_pct is not None:
            b['clvAskPct'] = round(closing_ask_pct - entry_pct, 2)
        if entry_pct is not None and closing_mid_pct is not None:
            b['clvMidPct'] = round(closing_mid_pct - entry_pct, 2)

        updated += 1
        print(f"  OK: {game_str} {market} entry={entry_pct} closingAsk={closing_ask_pct} closingMid={closing_mid_pct} src={source}")

    write_json_atomic(bets, BETS_PATH, indent=2)
    print(f"\n[DONE] Settle complete: {updated} bets updated with closing lines, {updated_late} marked LATE_ONLY")
