"""
preview_kalshi.py — v2.0
Shows a summary of discovered Kalshi markets from kalshi_market_index.json.
Called from fetch-slate.yml after the kalshi fetch steps.
"""
import json

# ── Show kalshi_raw.json (ML markets from original endpoint) ──────────────────
try:
    with open('data/kalshi_raw.json') as f:
        d = json.load(f)
    if 'error' in d:
        print(f'[kalshi_raw] error: {d["error"]}')
    else:
        print(f'[kalshi_raw] todayGames={d.get("todayGames",0)} | totalMarketsOpen={d.get("totalMarketsOpen",0)}')
        for g in (d.get('games', []) or [])[:5]:
            print(f'  {g.get("awayTeam")}@{g.get("homeTeam")} {g.get("gameTime")} | american={g.get("americanOdds")} ticker={g.get("ticker","")}')
except Exception as e:
    print(f'[kalshi_raw] not parseable: {e}')

# ── Show kalshi_market_index.json (full market enumeration) ──────────────────
try:
    with open('data/kalshi_market_index.json') as f:
        idx = json.load(f)
    total = idx.get('total_markets', 0)
    by_type = idx.get('by_type', {})
    by_event = idx.get('by_event', {})
    print(f'\n[kalshi_market_index] {total} markets | events={len(by_event)}')
    print('  By type:', ' | '.join(f'{k}:{v}' for k,v in sorted(by_type.items(), key=lambda x: -x[1])))
    print('  By event (games):')
    for et, cnt in sorted(by_event.items()):
        print(f'    {et}: {cnt} markets')
    
    # Show one full example per market type
    mkts = idx.get('markets', [])
    seen_types = set()
    print('\n  Examples per market type:')
    for m in mkts:
        mt = m.get('market_type','')
        if mt not in seen_types:
            seen_types.add(mt)
            print(f'    [{mt:15s}] {m.get("market_ticker","")[:40]} | "{m.get("title","")[:50]}"')
            print(f'               implied={m.get("implied_pct")}% | close={m.get("close_time","")[:16]}')
except FileNotFoundError:
    print('\n[kalshi_market_index] file not found — market enumeration may not have run yet')
except Exception as e:
    print(f'\n[kalshi_market_index] error: {e}')

# ── Show kalshi_search.json (legacy search results) ──────────────────────────
try:
    with open('data/kalshi_search.json') as f:
        s = json.load(f)
    results = s.get('results', s.get('markets', []))
    if results:
        print(f'\n[kalshi_search] {len(results)} legacy search results')
        for r in results[:3]:
            print(f'  {r.get("ticker","")[:40]} | {r.get("title","")[:50]}')
except Exception:
    pass
