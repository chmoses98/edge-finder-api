"""Debug enrich.js endpoint — writes results to data/debug_endpoints.json"""
import urllib.request, json, time

VERCEL = 'https://edge-finder-api.vercel.app'

def test(name, url, timeout=55):
    result = {'name': name, 'url': url}
    try:
        req = urllib.request.Request(url, headers={'Accept':'application/json','User-Agent':'Mozilla/5.0','Cache-Control':'no-cache'})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            result.update({'responseBytes':len(raw),'httpStatus':r.status,'first300':raw[:300].decode('utf-8',errors='replace')})
            try:
                d = json.loads(raw)
                for k in ['ok','error','teamCount','batterCount','pitcherCount','xstatsRows','fbRows','xstatsHeaders','fbHeaders']:
                    if k in d: result[k] = d[k]
                if d.get('teams'):
                    k = list(d['teams'].keys())[0]
                    result['sampleTeam'] = {k: d['teams'][k]}
                if d.get('pitchers'):
                    k = list(d['pitchers'].keys())[0]
                    result['samplePitcher'] = {k: d['pitchers'][k]}
            except json.JSONDecodeError as e:
                result['jsonError'] = str(e)
    except Exception as e:
        result['requestError'] = str(e)
    return result

try:
    with open('data/slate.json') as f:
        slate = json.load(f)
    pids = list({str(g.get(s,{}).get('pitcher',{}).get('id',''))
                 for g in slate.get('games',[]) for s in ['away','home']
                 if g.get(s,{}).get('pitcher',{}).get('id')})[:3]
    pid_str = ','.join(pids)
except Exception:
    pid_str = '607200,605488'

results = {
    'timestamp': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
    'pitcherIdsTested': pid_str,
    'endpoints': [
        test('enrich_batting',  f'{VERCEL}/api/enrich?type=batting&year=2026'),
        test('enrich_tto',      f'{VERCEL}/api/enrich?type=tto&playerIds={pid_str}&year=2026'),
        test('enrich_bullpen',  f'{VERCEL}/api/enrich?type=bullpen&season=2026'),
    ]
}

with open('data/debug_endpoints.json', 'w') as f:
    json.dump(results, f, indent=2)

print('Results written to data/debug_endpoints.json')
for ep in results['endpoints']:
    print(f"\n{ep['name']}: {ep.get('responseBytes','?')}B | ok={ep.get('ok')} | err={ep.get('requestError') or ep.get('error')}")
    if ep.get('teamCount') is not None: print(f"  teams={ep['teamCount']} batters={ep.get('batterCount')}")
    if ep.get('sampleTeam'): print(f"  sample: {ep['sampleTeam']}")
    if ep.get('samplePitcher'): print(f"  sample: {ep['samplePitcher']}")
    if ep.get('first300') and not ep.get('ok'): print(f"  first300: {ep['first300'][:150]}")
