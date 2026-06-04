"""Debug: test endpoints and write results to data/debug_endpoints.json"""
import urllib.request, json, sys, time

VERCEL = 'https://edge-finder-api.vercel.app'

def test_endpoint(name, url, timeout=45):
    result = {'name': name, 'url': url}
    try:
        req = urllib.request.Request(url, headers={
            'Accept': 'application/json',
            'User-Agent': 'Mozilla/5.0',
            'Cache-Control': 'no-cache',
        })
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            result['responseBytes'] = len(raw)
            result['httpStatus'] = r.status
            result['first300'] = raw[:300].decode('utf-8', errors='replace')
            try:
                d = json.loads(raw)
                result['ok'] = d.get('ok')
                result['error'] = d.get('error')
                result['teamCount'] = d.get('teamCount')
                result['batterCount'] = d.get('batterCount')
                result['pitcherCount'] = d.get('pitcherCount')
                result['xstatsRows'] = d.get('xstatsRows')
                result['fbRows'] = d.get('fbRows')
                result['xstatsHeaders'] = d.get('xstatsHeaders')
                result['fbHeaders'] = d.get('fbHeaders')
                teams = d.get('teams', {})
                if teams:
                    k = list(teams.keys())[0]
                    result['sampleTeam'] = {k: teams[k]}
                pitchers = d.get('pitchers', {})
                if pitchers:
                    k = list(pitchers.keys())[0]
                    result['samplePitcher'] = {k: pitchers[k]}
            except json.JSONDecodeError as e:
                result['jsonError'] = str(e)
    except Exception as e:
        result['requestError'] = str(e)
    return result

# Load pitcher IDs from slate
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
        test_endpoint('savant_batting', f'{VERCEL}/api/savant_batting?year=2026'),
        test_endpoint('savant_tto', f'{VERCEL}/api/savant_tto?playerIds={pid_str}&year=2026'),
        test_endpoint('savant_bullpen_hl', f'{VERCEL}/api/savant_bullpen_hl?season=2026'),
    ]
}

with open('data/debug_endpoints.json', 'w') as f:
    json.dump(results, f, indent=2)

print('Debug results written to data/debug_endpoints.json')
for ep in results['endpoints']:
    print(f"\n{ep['name']}: {ep.get('responseBytes','?')} bytes | ok={ep.get('ok')} | error={ep.get('error')}")
    if ep.get('first300'):
        print(f"  Preview: {ep['first300'][:150]}")
