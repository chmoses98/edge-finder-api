#!/usr/bin/env python3
"""
build_final_index.py
Builds the definitive kalshi_market_index.json with all confirmed series.
All market tickers discovered and classified.
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
print(f"DATE={DATE} KALSHI_DATE={KALSHI_DATE} ts={SNAPSHOT_TS}")

def get(url):
    try:
        req = Request(url, headers={'Accept':'application/json','User-Agent':'Mozilla/5.0'})
        with urlopen(req, timeout=15) as r:
            return json.loads(r.read()), None
    except HTTPError as e:
        return None, f"HTTP{e.code}"
    except Exception as e:
        return None, str(e)[:80]

def pull_all(series_ticker):
    all_mkts = []
    cursor = ''
    for _ in range(15):
        url = f"{KALSHI_BASE}/markets?series_ticker={series_ticker}&status=open&limit=200"
        if cursor: url += f"&cursor={cursor}"
        data, err = get(url)
        if not data: break
        mkts = data.get('markets') or []
        all_mkts.extend(mkts)
        cursor = data.get('cursor','')
        if not cursor or not mkts: break
    return [m for m in all_mkts if KALSHI_DATE in (m.get('event_ticker','') or '')]

def norm_price(v):
    if v is None: return None
    f = float(v)
    return f if f <= 1.0 else f / 100.0

def american(mid):
    if not mid or mid <= 0 or mid >= 1: return None
    return round(-(mid/(1-mid))*100) if mid >= 0.5 else round(((1-mid)/mid)*100)

def make_record(m, market_type, series):
    bid = norm_price(m.get('yes_bid_dollars') or m.get('yes_bid'))
    ask = norm_price(m.get('yes_ask_dollars') or m.get('yes_ask'))
    last = norm_price(m.get('last_price_dollars') or m.get('last_price'))
    mid = ((bid or 0) + (ask or 0)) / 2 if (bid or ask) else None
    return {
        'event_ticker':  m.get('event_ticker', ''),
        'market_ticker': m['ticker'],
        'series':        series,
        'title':         m.get('title', ''),
        'subtitle':      m.get('subtitle', ''),
        'open_time':     m.get('open_time', ''),
        'close_time':    m.get('close_time', ''),
        'status':        m.get('status', 'open'),
        'market_type':   market_type,
        'snapshot_ts':   SNAPSHOT_TS,
        'yes_bid':       round(bid,4) if bid else None,
        'yes_ask':       round(ask,4) if ask else None,
        'mid':           round(mid,4) if mid else None,
        'implied_pct':   round(mid*100,2) if mid else None,
        'american_odds': american(mid) if mid else None,
        'last_price':    round(last,4) if last else None,
        'volume':        float(m.get('volume_fp') or m.get('volume') or 0),
    }

# ── Pull all confirmed series ─────────────────────────────────────────────────
CONFIRMED_SERIES = {
    'KXMLBGAME':     'moneyline',
    'KXMLBSPREAD':   'spread',
    'KXMLBTOTAL':    'total',
    'KXMLBTEAMTOTAL':'team_total',
    'KXMLBF5':       'f5_moneyline',
    'KXMLBF5SPREAD': 'f5_spread',
    'KXMLBF5TOTAL':  'f5_total',
    'KXMLBRFI':      'rfi',   # Run in First Inning — YES=YRFI, NO=NRFI
}

all_markets = []
by_series = {}
by_event = {}
by_type = {}

for series, mtype in CONFIRMED_SERIES.items():
    print(f"\nPulling {series} ({mtype})...")
    mkts = pull_all(series)
    print(f"  {len(mkts)} markets today")
    by_series[series] = []
    for m in mkts:
        rec = make_record(m, mtype, series)
        all_markets.append(rec)
        by_series[series].append(rec['market_ticker'])
        et = rec['event_ticker']
        if et not in by_event: by_event[et] = []
        by_event[et].append(rec)
        by_type[mtype] = by_type.get(mtype, 0) + 1

# ── Print comprehensive summary ───────────────────────────────────────────────
print(f"\n{'='*60}")
print(f"CONFIRMED KALSHI MLB MARKET INDEX — {DATE}")
print(f"{'='*60}")
print(f"\nTotal markets: {len(all_markets)}")
print(f"\nBy series:")
for s, tickers in sorted(by_series.items()):
    print(f"  {s:20s}: {len(tickers)} markets")
print(f"\nBy type:")
for t, cnt in sorted(by_type.items()):
    print(f"  {t:20s}: {cnt}")
print(f"\nBy event (game):")
for et in sorted(by_event.keys()):
    mkts = by_event[et]
    print(f"\n  {et} ({len(mkts)} total markets):")
    for mtype in ['moneyline','spread','total','team_total','f5_moneyline','f5_spread','f5_total','rfi']:
        type_mkts = [m for m in mkts if m['market_type'] == mtype]
        if type_mkts:
            print(f"    [{mtype:12s}] {len(type_mkts)} markets:")
            for m in sorted(type_mkts, key=lambda x: x['market_ticker']):
                print(f"      {m['market_ticker']:60s} implied={m['implied_pct']}%")

# ── Write final index ─────────────────────────────────────────────────────────
os.makedirs('data', exist_ok=True)
index = {
    'date': DATE,
    'kalshi_date': KALSHI_DATE,
    'fetched_at': SNAPSHOT_TS,
    'total_markets': len(all_markets),
    'confirmed_series': list(CONFIRMED_SERIES.keys()),
    'series_descriptions': {
        'KXMLBGAME':      'Full game moneyline (2 markets: away, home)',
        'KXMLBSPREAD':    'Win margin spreads (multiple lines per side: 1.5, 2.5, 3.5...)',
        'KXMLBTOTAL':     'Game total runs, integer lines (many per game: O4, O5, O6...)',
        'KXMLBTEAMTOTAL': 'Team total over X.5 runs (multiple lines per team)',
        'KXMLBF5':        'First 5 innings winner, includes TIE (3 markets per game)',
        'KXMLBF5SPREAD':  'First 5 innings win margin (multiple spreads per side)',
        'KXMLBF5TOTAL':   'First 5 innings total runs, integer lines',
        'KXMLBRFI':       'Run in First Inning — single binary market per game (YES=YRFI, NO=NRFI)',
    },
    'by_type': by_type,
    'by_series': {s: {'count': len(t)} for s, t in by_series.items()},
    'by_event': {
        et: [
            {k: v for k, v in m.items()}
            for m in sorted(mkts, key=lambda x: (x['series'], x['market_ticker']))
        ]
        for et, mkts in sorted(by_event.items())
    },
    'markets': sorted(all_markets, key=lambda x: (x['event_ticker'], x['series'], x['market_ticker'])),
}

with open('data/kalshi_market_index.json','w') as f:
    json.dump(index, f, indent=2)
print(f"\nWritten: data/kalshi_market_index.json")

# Also update history
try:
    with open('data/kalshi_odds_history.json') as f:
        history = json.load(f)
except:
    history = []

history = [h for h in history if h.get('snapshot_ts') != SNAPSHOT_TS]
cutoff = (datetime.now(tz=timezone.utc) - timedelta(days=90)).strftime('%Y-%m-%d')
new_snaps = [
    {k: m[k] for k in ['snapshot_ts','date','event_ticker','market_ticker',
                        'series','market_type','title','yes_bid','yes_ask',
                        'mid','implied_pct','last_price','volume']
     if k in m}
    for m in all_markets if m.get('mid') is not None
]
history = [h for h in history if h.get('date','') >= cutoff]
history.extend(new_snaps)
history.sort(key=lambda x: x.get('snapshot_ts',''))
with open('data/kalshi_odds_history.json','w') as f:
    json.dump(history, f, indent=2)
print(f"Updated: data/kalshi_odds_history.json ({len(new_snaps)} new snapshots, {len(history)} total)")
