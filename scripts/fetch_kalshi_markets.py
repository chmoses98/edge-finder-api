#!/usr/bin/env python3
"""
fetch_kalshi_markets.py — v1.0
=================================
Discovers all Kalshi MLB markets for today via the Events endpoint
with nested markets enabled. For each game (event), enumerates every
market, classifies it by type, and stores the result.

Also pulls live odds snapshots for all open markets and appends them
to data/kalshi_odds_history.json for historical tracking.

Called by GitHub Actions. Output:
  data/kalshi_market_index.json   — full market index (event+market metadata)
  data/kalshi_odds_history.json   — time-series odds snapshots (append-only)

Classification logic:
  moneyline        → title contains "wins"/"winner" and no inning/run qualifier
  spread           → title contains "wins by" or "1.5 runs"/"2.5 runs" (full game)
  total            → title contains "Total Runs" or "Over/Under" for full game
  team_total       → title contains team name + "scores" or "over X runs"
  f5_moneyline     → title contains "First 5 Innings" + winner
  f5_spread        → title contains "First 5 Innings" + "1.5"/"2.5 runs"
  nrfi             → title contains "No Run First Inning" or "NRFI"
  yrfi             → title contains "First Inning Run" scored (Yes side)
  unknown          → doesn't match any classifier
"""

import json
import sys
import os
from datetime import datetime, timezone, timedelta
from urllib.request import urlopen, Request
from urllib.error import HTTPError, URLError

# ── Config ──────────────────────────────────────────────────────────────────
KALSHI_BASE = 'https://api.elections.kalshi.com/trade-api/v2'
SERIES_TICKER = 'KXMLBGAME'
DATE = sys.argv[1] if len(sys.argv) > 1 else datetime.now(tz=timezone(timedelta(hours=-4))).strftime('%Y-%m-%d')
OUT_INDEX   = 'data/kalshi_market_index.json'
OUT_HISTORY = 'data/kalshi_odds_history.json'
SNAPSHOT_TS = datetime.now(tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

# ── Kalshi date format for ticker matching ───────────────────────────────────
dt = datetime.strptime(DATE, '%Y-%m-%d')
MONTHS = ['JAN','FEB','MAR','APR','MAY','JUN','JUL','AUG','SEP','OCT','NOV','DEC']
KALSHI_DATE = str(dt.year)[2:] + MONTHS[dt.month-1] + str(dt.day).zfill(2)

print(f"[fetch_kalshi_markets] DATE={DATE} | KALSHI_DATE={KALSHI_DATE} | ts={SNAPSHOT_TS}")


# ── HTTP helper ───────────────────────────────────────────────────────────────
def get(url, label=''):
    try:
        req = Request(url, headers={'Accept': 'application/json', 'User-Agent': 'Mozilla/5.0'})
        with urlopen(req, timeout=20) as r:
            return json.loads(r.read())
    except (HTTPError, URLError, json.JSONDecodeError) as e:
        print(f"  WARN [{label}]: {e}")
        return None


# ── Market classifier ─────────────────────────────────────────────────────────
def classify_market(ticker: str, title: str, subtitle: str) -> str:
    """Classify a Kalshi market by its ticker, title, and subtitle."""
    t = (title or '').lower()
    s = (subtitle or '').lower()
    k = (ticker or '').lower()
    combined = t + ' ' + s + ' ' + k

    # NRFI / YRFI — check first (most specific)
    if 'nrfi' in combined or 'no run first inning' in combined:
        return 'nrfi'
    if 'yrfi' in combined or 'first inning run' in combined:
        return 'yrfi'
    # Also catch: "score in the first inning"
    if 'score in the first' in combined or 'runs in the 1st' in combined:
        return 'yrfi'

    # First 5 innings markets
    if 'first 5' in combined or 'f5' in k or '1st 5' in combined:
        # F5 spread vs F5 ML
        if 'wins by' in combined or '1.5 runs' in combined or '2.5 runs' in combined or 'run line' in combined:
            return 'f5_spread'
        if 'wins' in combined or 'winner' in combined or 'moneyline' in combined:
            return 'f5_moneyline'
        return 'f5_moneyline'  # default F5

    # Spread (full game run line)
    if 'wins by' in combined or ('by over' in combined and 'run' in combined):
        return 'spread'
    if 'run line' in combined or '-1.5' in combined or '+1.5' in combined:
        return 'spread'

    # Team total
    if ('scores' in combined or 'over' in combined) and ('runs' in combined):
        # Distinguish team total from game total
        # Team total typically names a specific team: "Cubs score over X runs"
        # Game total just says "Total Runs Over X"
        if 'total' in combined and 'both' not in combined:
            # Could be either — check for team-specific language
            if any(x in combined for x in ['score over', 'scores over', 'score at least', 'scores at least']):
                return 'team_total'
        if 'score over' in combined or 'scores over' in combined:
            return 'team_total'
        if 'team total' in combined:
            return 'team_total'
    
    # Game total
    if 'total runs' in combined or 'combined' in combined or 'over/under' in combined:
        return 'total'
    if 'total' in combined and ('over' in combined or 'under' in combined) and 'inning' not in combined:
        return 'total'

    # Moneyline (full game winner)
    if 'wins' in combined or 'winner' in combined or 'moneyline' in combined:
        return 'moneyline'
    # Ticker-based ML detection (e.g. KXMLBGAME-26JUN04...)
    if any(suffix in k for suffix in ['ml', 'winner', 'win']):
        return 'moneyline'

    return 'unknown'


# ── Step 1: Pull all events for the series with nested markets ────────────────
print(f"\n[1] Fetching KXMLBGAME events with nested markets...")
all_events = []
cursor = ''
page = 0
while page < 10:
    url = f"{KALSHI_BASE}/events?series_ticker={SERIES_TICKER}&status=open&with_nested_markets=true&limit=100"
    if cursor:
        url += f"&cursor={cursor}"
    data = get(url, f"events page {page}")
    if not data:
        print(f"  Events endpoint returned nothing on page {page} — trying markets fallback")
        break
    events = data.get('events', [])
    all_events.extend(events)
    cursor = data.get('cursor', '')
    print(f"  Page {page}: {len(events)} events | cursor={'yes' if cursor else 'none'}")
    if not cursor or not events:
        break
    page += 1

print(f"  Total events found: {len(all_events)}")

# Filter to today's date
today_events = [e for e in all_events if KALSHI_DATE in (e.get('event_ticker') or '')]
print(f"  Today's events ({KALSHI_DATE}): {len(today_events)}")

# If nested markets came back with the events, extract them; otherwise fall back
# to the markets endpoint
market_index = []

if today_events:
    for event in today_events:
        event_ticker = event.get('event_ticker', '')
        event_title  = event.get('title', '')
        markets      = event.get('markets', [])
        
        # If no nested markets, try fetching them per-event
        if not markets:
            ev_url = f"{KALSHI_BASE}/events/{event_ticker}?with_nested_markets=true"
            ev_data = get(ev_url, f"event {event_ticker}")
            if ev_data:
                event_detail = ev_data.get('event', ev_data)
                markets = event_detail.get('markets', [])
        
        for mkt in markets:
            ticker       = mkt.get('ticker', '')
            title        = mkt.get('title', event_title)
            subtitle     = mkt.get('subtitle', '')
            open_time    = mkt.get('open_time', event.get('open_time', ''))
            close_time   = mkt.get('close_time', event.get('close_time', ''))
            market_type  = classify_market(ticker, title, subtitle)
            
            # Price snapshot
            yes_bid   = mkt.get('yes_bid') or mkt.get('yes_bid_dollars')
            yes_ask   = mkt.get('yes_ask') or mkt.get('yes_ask_dollars')
            last      = mkt.get('last_price') or mkt.get('last_price_dollars')
            
            # Normalize to dollars (Kalshi sometimes returns cents)
            def norm(v):
                if v is None: return None
                f = float(v)
                return f if f <= 1.0 else f / 100.0
            
            yes_bid_d = norm(yes_bid)
            yes_ask_d = norm(yes_ask)
            mid_d     = ((yes_bid_d or 0) + (yes_ask_d or 0)) / 2 if (yes_bid_d or yes_ask_d) else None
            
            record = {
                'event_ticker': event_ticker,
                'market_ticker': ticker,
                'title': title,
                'subtitle': subtitle,
                'open_time': open_time,
                'close_time': close_time,
                'market_type': market_type,
                'status': mkt.get('status', ''),
                'snapshot_ts': SNAPSHOT_TS,
                'yes_bid': yes_bid_d,
                'yes_ask': yes_ask_d,
                'mid': round(mid_d, 4) if mid_d else None,
                'implied_pct': round(mid_d * 100, 2) if mid_d else None,
                'last_price': norm(last),
                'volume': mkt.get('volume', mkt.get('volume_fp', 0)),
                'open_interest': mkt.get('open_interest', mkt.get('open_interest_fp', 0)),
            }
            market_index.append(record)

# ── Step 2: Fallback — pull from /markets endpoint if events had no nested data ──
if not market_index:
    print("\n[2] No nested markets from events endpoint — falling back to /markets with series_ticker")
    cursor = ''
    page = 0
    all_markets = []
    while page < 10:
        url = f"{KALSHI_BASE}/markets?series_ticker={SERIES_TICKER}&status=open&limit=200"
        if cursor:
            url += f"&cursor={cursor}"
        data = get(url, f"markets page {page}")
        if not data:
            break
        markets = data.get('markets', [])
        all_markets.extend(markets)
        cursor = data.get('cursor', '')
        print(f"  Page {page}: {len(markets)} markets")
        if not cursor or not markets:
            break
        page += 1
    
    today_markets = [m for m in all_markets if KALSHI_DATE in (m.get('event_ticker') or '')]
    print(f"  Today's markets: {len(today_markets)} of {len(all_markets)} total")
    
    for mkt in today_markets:
        ticker      = mkt.get('ticker', '')
        event_ticker = mkt.get('event_ticker', '')
        title       = mkt.get('title', '')
        subtitle    = mkt.get('subtitle', '')
        market_type = classify_market(ticker, title, subtitle)
        
        yes_bid_d = float(mkt.get('yes_bid_dollars', 0) or 0)
        yes_ask_d = float(mkt.get('yes_ask_dollars', 0) or 0)
        mid_d     = (yes_bid_d + yes_ask_d) / 2 if (yes_bid_d or yes_ask_d) else None
        
        record = {
            'event_ticker': event_ticker,
            'market_ticker': ticker,
            'title': title,
            'subtitle': subtitle,
            'open_time': mkt.get('open_time', ''),
            'close_time': mkt.get('close_time', ''),
            'market_type': market_type,
            'status': mkt.get('status', ''),
            'snapshot_ts': SNAPSHOT_TS,
            'yes_bid': yes_bid_d if yes_bid_d else None,
            'yes_ask': yes_ask_d if yes_ask_d else None,
            'mid': round(mid_d, 4) if mid_d else None,
            'implied_pct': round(mid_d * 100, 2) if mid_d else None,
            'last_price': float(mkt.get('last_price_dollars', 0) or 0) or None,
            'volume': float(mkt.get('volume_fp', 0) or 0),
            'open_interest': float(mkt.get('open_interest_fp', 0) or 0),
        }
        market_index.append(record)

# ── Step 3: Print summary ──────────────────────────────────────────────────────
by_type = {}
for r in market_index:
    mt = r['market_type']
    by_type[mt] = by_type.get(mt, 0) + 1

print(f"\n[3] Market index: {len(market_index)} markets discovered")
for mt, cnt in sorted(by_type.items(), key=lambda x: -x[1]):
    print(f"  {mt:20s}: {cnt}")

by_event = {}
for r in market_index:
    et = r['event_ticker']
    by_event[et] = by_event.get(et, 0) + 1
print(f"\n  Events with markets: {len(by_event)}")
for et, cnt in sorted(by_event.items()):
    print(f"    {et}: {cnt} markets")

# ── Step 3b: Broad, prefix-agnostic discovery retention (Model Performance
# Phase 2A) -- SERIES_TICKER above is a single hardcoded series
# ('KXMLBGAME'); a real Kalshi series this repository doesn't yet know the
# name of (e.g. the real F3/F7 series tickers; a user confirmed placing real
# wagers on both) would never be queried by Steps 1-2 above. This ADDITIVE
# pass fetches open markets with no series filter and retains anything whose
# series differs from SERIES_TICKER under a separate field, so nothing is
# silently dropped. Does not change market_index/by_type/by_event above --
# nothing in scripts/merge_odds.py or scripts/build_market_ledger.py reads
# this script's output at all (confirmed: only scripts/preview_kalshi.py and
# scripts/build_final_index.py do), so this is pure discovery visibility.
print(f"\n[3b] Broad discovery pass (no series filter)...")
discovered_unknown_series = []
_seen_unknown_tickers = set()
_page = 0
_cursor = ''
while _page < 10:
    _url = f"{KALSHI_BASE}/markets?status=open&limit=1000"
    if _cursor:
        _url += f"&cursor={_cursor}"
    _data = get(_url, f"broad discovery page {_page}")
    if not _data:
        break
    for _m in (_data.get('markets') or []):
        _et = _m.get('event_ticker', '') or ''
        if KALSHI_DATE not in _et:
            continue
        _series = _et.split('-')[0] if _et else ''
        if _series == SERIES_TICKER:
            continue  # already covered by Steps 1-2
        _t = _m.get('ticker')
        if _t and _t not in _seen_unknown_tickers:
            _seen_unknown_tickers.add(_t)
            discovered_unknown_series.append({
                'event_ticker': _et,
                'market_ticker': _t,
                'title': _m.get('title', ''),
                'subtitle': _m.get('subtitle', ''),
                'status': _m.get('status', ''),
            })
    _cursor = _data.get('cursor', '')
    if not _cursor or not _data.get('markets'):
        break
    _page += 1
print(f"  Discovered outside SERIES_TICKER allowlist: {len(discovered_unknown_series)}")

# ── Step 4: Write market index ────────────────────────────────────────────────
os.makedirs('data', exist_ok=True)
index_out = {
    'date': DATE,
    'kalshi_date': KALSHI_DATE,
    'fetched_at': SNAPSHOT_TS,
    'total_markets': len(market_index),
    'by_type': by_type,
    'by_event': by_event,
    'markets': market_index,
    'discoveredUnknownSeries': discovered_unknown_series,
    'discoveredUnknownSeriesCount': len(discovered_unknown_series),
}
with open(OUT_INDEX, 'w') as f:
    json.dump(index_out, f, indent=2)
print(f"\n[4] Written: {OUT_INDEX}")

# ── Step 5: Append odds snapshots to history file ────────────────────────────
# History file: flat array of snapshot records, each tagged with snapshot_ts
# This is append-only — we never overwrite historical data.
# New snapshots are appended each time this script runs.

try:
    with open(OUT_HISTORY) as f:
        history = json.load(f)
except (FileNotFoundError, json.JSONDecodeError):
    history = []

# Remove any existing snapshots from this exact timestamp (idempotent re-run)
history = [h for h in history if h.get('snapshot_ts') != SNAPSHOT_TS]

# Append new snapshots (only for markets with actual price data)
new_snapshots = []
for r in market_index:
    if r.get('mid') is not None:
        snap = {
            'snapshot_ts':    SNAPSHOT_TS,
            'date':           DATE,
            'event_ticker':   r['event_ticker'],
            'market_ticker':  r['market_ticker'],
            'market_type':    r['market_type'],
            'title':          r['title'],
            'yes_bid':        r['yes_bid'],
            'yes_ask':        r['yes_ask'],
            'mid':            r['mid'],
            'implied_pct':    r['implied_pct'],
            'last_price':     r['last_price'],
            'volume':         r['volume'],
        }
        new_snapshots.append(snap)

history.extend(new_snapshots)

# Keep history trimmed to last 90 days to avoid bloat
cutoff = (datetime.now(tz=timezone.utc) - timedelta(days=90)).strftime('%Y-%m-%d')
history = [h for h in history if h.get('date', '9999') >= cutoff]

with open(OUT_HISTORY, 'w') as f:
    json.dump(history, f, indent=2)
print(f"[5] Appended {len(new_snapshots)} snapshots → {OUT_HISTORY} (total: {len(history)} records)")

print("\n[DONE] fetch_kalshi_markets.py complete")
