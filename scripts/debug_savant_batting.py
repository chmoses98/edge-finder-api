"""Debug: test savant_batting endpoint - does NOT fail the workflow."""
import urllib.request, json, sys

url = 'https://edge-finder-api.vercel.app/api/savant_batting?year=2026'
print(f'Calling: {url}')
try:
    req = urllib.request.Request(url, headers={
        'Accept': 'application/json',
        'User-Agent': 'Mozilla/5.0',
        'Cache-Control': 'no-cache',
    })
    with urllib.request.urlopen(req, timeout=45) as r:
        raw = r.read()
        print(f'Response size: {len(raw)} bytes')
        print(f'First 500 chars: {raw[:500].decode("utf-8", errors="replace")}')
        d = json.loads(raw)
    print('ok:', d.get('ok'))
    print('teamCount:', d.get('teamCount'))
    print('batterCount:', d.get('batterCount'))
    print('xstatsRows:', d.get('xstatsRows'))
    print('fbRows:', d.get('fbRows'))
    print('xstatsHeaders:', d.get('xstatsHeaders'))
    print('fbHeaders:', d.get('fbHeaders'))
    print('error:', d.get('error'))
    teams = d.get('teams', {})
    if teams:
        k = list(teams.keys())[0]
        print(f'Sample ({k}): xwoba={teams[k].get("xwoba")} fbPct={teams[k].get("fbPct")}')
    else:
        print('NO TEAMS — team column not found in Savant CSV')
except Exception as e:
    print(f'ERROR: {e}')
    # Do NOT sys.exit(1) — let rest of workflow continue
print('Debug complete.')
