"""Debug all Savant/enrichment endpoints — non-fatal, prints response details."""
import urllib.request, json, sys

VERCEL = 'https://edge-finder-api.vercel.app'

def test_endpoint(name, url, timeout=45):
    print(f'\n=== {name} ===')
    print(f'URL: {url[:80]}')
    try:
        req = urllib.request.Request(url, headers={
            'Accept': 'application/json',
            'User-Agent': 'Mozilla/5.0',
            'Cache-Control': 'no-cache',
        })
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            print(f'Response: {len(raw)} bytes | Status: {r.status}')
            print(f'First 300 chars: {raw[:300].decode("utf-8", errors="replace")}')
            try:
                d = json.loads(raw)
                print(f'ok={d.get("ok")} | error={d.get("error")}')
                # Print key counts
                for key in ['teamCount', 'batterCount', 'pitcherCount', 'xstatsRows', 'fbRows']:
                    if key in d:
                        print(f'  {key}: {d[key]}')
                if d.get('teams'):
                    k = list(d['teams'].keys())[0]
                    print(f'  Sample team ({k}): {d["teams"][k]}')
                if d.get('pitchers'):
                    k = list(d['pitchers'].keys())[0]
                    print(f'  Sample pitcher ({k}): {d["pitchers"][k]}')
            except json.JSONDecodeError as e:
                print(f'JSON parse error: {e}')
    except Exception as e:
        print(f'Request error: {e}')

# Load slate to get pitcher IDs
try:
    with open('data/slate.json') as f:
        slate = json.load(f)
    pids = list({str(g.get(s,{}).get('pitcher',{}).get('id',''))
                 for g in slate.get('games',[]) for s in ['away','home']
                 if g.get(s,{}).get('pitcher',{}).get('id')})[:3]
    pid_str = ','.join(pids)
    print(f'Testing with pitcher IDs: {pid_str}')
except Exception as e:
    pid_str = '607200,605488'
    print(f'Using fallback pitcher IDs: {pid_str}')

test_endpoint('savant_batting', f'{VERCEL}/api/savant_batting?year=2026')
test_endpoint('savant_tto', f'{VERCEL}/api/savant_tto?playerIds={pid_str}&year=2026')
test_endpoint('savant_bullpen_hl', f'{VERCEL}/api/savant_bullpen_hl?season=2026')

print('\nDebug complete.')
