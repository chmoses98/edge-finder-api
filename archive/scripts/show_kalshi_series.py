import json

try:
    with open('data/kalshi_markets.json') as f:
        d = json.load(f)
except:
    print('Could not read kalshi_markets.json')
    exit(0)

print(f'Kalshi today: {d.get("todayCount",0)} markets | Series found: {d.get("seriesFound",[])}')
for series, markets in d.get('bySeries', {}).items():
    print(f'\n=== {series} ({len(markets)} markets) ===')
    for m in markets[:4]:
        print(f'  {m.get("ticker","")[:50]}')
        print(f'  "{m.get("title","")}"')
        print(f'  bid={m.get("bid")} ask={m.get("ask")}')
